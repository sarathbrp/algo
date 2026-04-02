"""MCP Resources — read-only data for AI assistants.

Each resource returns formatted text that Claude can read naturally.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from src.api.broker_utils import get_live_broker
from src.db.repos import (
    account_repo, gate_log_repo, portfolio_repo, regime_repo,
    rule_repo, trade_repo, user_repo, worker_repo,
)
from src.rules_engine.evaluator import evaluate_rule


def get_portfolio(session, user_id: str) -> str:
    """Current portfolio state — equity, cash, buying power, day P&L."""
    broker = get_live_broker(session, user_id)
    if broker:
        try:
            snap = broker.get_account_snapshot()
            return (
                f"Portfolio Summary\n"
                f"  Equity:       ${float(snap.get('equity', 0)):,.2f}\n"
                f"  Cash:         ${float(snap.get('cash', 0)):,.2f}\n"
                f"  Buying Power: ${float(snap.get('buying_power', 0)):,.2f}\n"
                f"  Day P&L:      ${float(snap.get('daily_pnl', 0)):,.2f}\n"
                f"  Day P&L %:    {float(snap.get('daily_pnl_pct', 0)):.2f}%\n"
            )
        except Exception as e:
            pass

    latest = portfolio_repo.get_latest_snapshot(session, user_id)
    if latest:
        return (
            f"Portfolio Summary (from DB snapshot)\n"
            f"  Equity:       ${float(latest.equity or 0):,.2f}\n"
            f"  Cash:         ${float(latest.cash or 0):,.2f}\n"
            f"  Buying Power: ${float(latest.buying_power or 0):,.2f}\n"
            f"  Day P&L:      ${float(latest.daily_pnl or 0):,.2f}\n"
        )
    return "No portfolio data available. Connect your broker in Settings."


def get_positions(session, user_id: str) -> str:
    """Open positions with live prices."""
    broker = get_live_broker(session, user_id)
    if not broker:
        return "No broker connected. Connect in Settings."

    try:
        positions = broker.get_positions()
    except Exception:
        positions = []

    if not positions:
        return "No open positions."

    lines = ["Open Positions\n"]
    total_pnl = 0.0
    for p in positions:
        qty = int(float(p.get("qty", 0)))
        entry = float(p.get("avg_entry_price", 0) or p.get("cost_basis", 0) / max(qty, 1))
        current = float(p.get("current_price", 0) or (float(p.get("market_value", 0)) / max(qty, 1)))
        pnl = float(p.get("unrealized_pl", 0))
        total_pnl += pnl
        ret_pct = ((current - entry) / entry * 100) if entry > 0 else 0
        lines.append(
            f"  {p['symbol']:6s}  {qty:>4d} shares @ ${entry:>8.2f}  "
            f"now ${current:>8.2f}  P&L ${pnl:>+8.2f} ({ret_pct:+.2f}%)"
        )
    lines.append(f"\n  Total Unrealized P&L: ${total_pnl:+,.2f}")
    return "\n".join(lines)


def get_watchlist(session, user_id: str) -> str:
    """Watchlist with live quotes."""
    from src.db.models import UserSettings
    from sqlalchemy import select

    settings = session.scalars(
        select(UserSettings).where(UserSettings.user_id == user_id)
    ).first()
    symbols = []
    if settings and settings.dashboard_layout:
        try:
            layout = json.loads(settings.dashboard_layout)
            symbols = layout.get("watchlist", [])
        except Exception:
            pass

    if not symbols:
        return "Watchlist is empty. Add tickers from the dashboard."

    broker = get_live_broker(session, user_id)
    lines = [f"Watchlist ({len(symbols)} tickers)\n"]
    for sym in symbols:
        quote_str = "--"
        if broker:
            try:
                q = broker.get_latest_quote(sym)
                if q:
                    quote_str = f"${q.mid:.2f}  spread {q.spread_pct:.2f}%"
            except Exception:
                pass
        lines.append(f"  {sym:6s}  {quote_str}")
    return "\n".join(lines)


def get_rules(session, user_id: str) -> str:
    """All active rules with conditions."""
    rules = rule_repo.get_rules(session, user_id, active_only=True)
    if not rules:
        return "No active rules. Create rules from the Rules tab or use apply_strategy."

    lines = [f"Active Rules ({len(rules)} total)\n"]
    for r in rules:
        tree = json.loads(r.rule_tree)
        expires = f"  expires {r.expires_at.isoformat()}" if r.expires_at else ""
        lines.append(f"  #{r.id} [{r.rule_type.upper()}] {r.symbol} — {r.name}{expires}")

        for g in tree.get("groups", []):
            conds = g.get("conditions", [])
            logic = g.get("logic", "AND")
            for i, c in enumerate(conds):
                prefix = "    IF " if i == 0 else f"    {logic} "
                val = c.get("value")
                if isinstance(val, dict):
                    val_str = f"{val.get('indicator', '?')}({val.get('params', {})})"
                else:
                    val_str = str(val)
                lines.append(f"{prefix}{c['indicator']}({c.get('params', {})}) {c['comparator']} {val_str}")

        for a in tree.get("actions", []):
            params = a.get("params", {})
            param_str = ", ".join(f"{k}={v}" for k, v in params.items()) if params else ""
            lines.append(f"    THEN {a['action']}({param_str})")
        lines.append("")
    return "\n".join(lines)


def get_pipeline(session, user_id: str) -> str:
    """Rules pipeline — current signal status per ticker."""
    rules = rule_repo.get_rules(session, user_id, active_only=True)
    if not rules:
        return "No active rules — pipeline is empty."

    broker = get_live_broker(session, user_id)
    by_symbol: dict[str, list] = {}
    for r in rules:
        by_symbol.setdefault(r.symbol, []).append(r)

    # Get positions
    positions_set: set[str] = set()
    if broker:
        try:
            positions_set = {p["symbol"].upper() for p in broker.get_positions()}
        except Exception:
            pass

    lines = [f"Rules Pipeline ({len(by_symbol)} tickers)\n"]

    for symbol, sym_rules in sorted(by_symbol.items()):
        in_pos = symbol in positions_set
        entry_fired = False
        exit_fired = False

        for r in sym_rules:
            tree = json.loads(r.rule_tree)
            status_str = ""
            if broker:
                try:
                    df = broker.get_bars(symbol, timeframe="1Day", limit=220)
                    if not df.empty and len(df) > 1:
                        result = evaluate_rule(df, tree, bar_idx=-1)
                        total = sum(len(g.conditions) for g in result.groups)
                        met = sum(1 for g in result.groups for c in g.conditions if c.passed)
                        status_str = f" → {met}/{total} conditions met"
                        if result.fired:
                            status_str += " ★ FIRED"
                            if r.rule_type == "entry":
                                entry_fired = True
                            else:
                                exit_fired = True
                except Exception:
                    status_str = " → (data unavailable)"

            lines.append(f"  {symbol} [{r.rule_type.upper()}] {r.name}{status_str}")

        # Status summary
        if in_pos and exit_fired:
            lines.append(f"  → STATUS: READY TO SELL")
        elif in_pos:
            lines.append(f"  → STATUS: HOLDING")
        elif entry_fired:
            lines.append(f"  → STATUS: READY TO BUY")
        else:
            lines.append(f"  → STATUS: WATCHING")
        lines.append("")

    return "\n".join(lines)


def get_trades(session, user_id: str) -> str:
    """Recent completed trades."""
    trades = trade_repo.get_trades(session, user_id, limit=20)
    if not trades:
        return "No completed trades yet."

    lines = ["Recent Trades (last 20)\n"]
    total_pnl = 0.0
    wins = 0
    losses = 0
    for t in trades:
        pnl = float(t.pnl or 0)
        total_pnl += pnl
        if pnl > 0:
            wins += 1
        elif pnl < 0:
            losses += 1
        exit_dt = t.exited_at.strftime("%Y-%m-%d %H:%M") if t.exited_at else "?"
        lines.append(
            f"  {exit_dt}  {t.symbol:6s} {t.side.value:5s} {float(t.qty):>5.0f} shares  "
            f"${float(t.entry_price or 0):>8.2f} → ${float(t.exit_price or 0):>8.2f}  "
            f"P&L ${pnl:>+8.2f}  {t.exit_reason or '?'}"
        )
    total = wins + losses
    wr = f"{wins / total * 100:.0f}%" if total > 0 else "N/A"
    lines.append(f"\n  Total P&L: ${total_pnl:+,.2f}  |  {wins}W {losses}L  |  Win Rate: {wr}")
    return "\n".join(lines)


def get_worker_status(session, user_id: str) -> str:
    """Bot state and worker health."""
    broker_account = account_repo.get_broker_account_for_user(session, user_id)
    if not broker_account:
        return "No broker account configured."

    settings = account_repo.get_account_settings(session, broker_account.id)
    worker = worker_repo.get_worker_status(session, "trading_loop")

    bot_state = getattr(settings, "bot_state", "unknown") if settings else "unknown"
    worker_status = getattr(worker, "status", "unknown") if worker else "unknown"
    heartbeat = worker.last_heartbeat.strftime("%H:%M:%S ET") if worker and worker.last_heartbeat else "never"

    regime = regime_repo.get_latest(session, user_id)
    regime_str = f"{regime.label.value} (SPY {float(regime.spy_score or 0):.2f}, VIX {float(regime.vix or 0):.1f})" if regime else "unknown"

    return (
        f"Worker Status\n"
        f"  Bot State:    {bot_state}\n"
        f"  Worker:       {worker_status}\n"
        f"  Heartbeat:    {heartbeat}\n"
        f"  Regime:       {regime_str}\n"
        f"  Paper Mode:   {broker_account.paper}\n"
    )
