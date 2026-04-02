"""MCP Tools — actions AI assistants can take on AlgoSphere.

Each tool takes parameters, performs an action, and returns a text result.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from src.api.broker_utils import get_live_broker
from src.db.repos import (
    account_repo, gate_log_repo, order_log_repo, rule_repo, trade_repo,
)
from src.rules_engine.evaluator import evaluate_rule
from src.rules_engine.validator import validate_rule, detect_conflicts
from src.rules_engine.backtest import run_backtest, BacktestConfig
from src.rules_engine.strategy_templates import get_strategy_templates, get_template_by_id


def create_rule(
    session: Session, user_id: str, *,
    symbol: str, name: str, rule_type: str,
    conditions: list[dict], actions: list[dict],
    logic: str = "AND", expires_at: str | None = None,
) -> str:
    """Create a trading rule."""
    # Validate symbol
    broker = get_live_broker(session, user_id)
    if broker:
        asset = broker.validate_symbol(symbol)
        if asset is None:
            return f"Error: '{symbol.upper()}' is not a recognized ticker symbol."
        if not asset.get("tradable", False):
            return f"Error: '{symbol.upper()}' is not currently tradable."

    rule_tree = {
        "groups": [{"logic": logic, "conditions": conditions}],
        "actions": actions,
    }

    # Validate rule
    issues = validate_rule(rule_tree, rule_type)
    errors = [i for i in issues if i.severity == "error"]
    if errors:
        return "Rule has errors:\n" + "\n".join(f"  - {e.message}" for e in errors)

    rule = rule_repo.create_rule(
        session,
        user_id=user_id,
        symbol=symbol.upper(),
        name=name,
        rule_type=rule_type,
        rule_tree=rule_tree,
        expires_at=expires_at,
    )
    session.commit()
    session.refresh(rule)
    return f"Created rule #{rule.id}: [{rule_type.upper()}] {symbol.upper()} — {name}"


def apply_strategy(
    session: Session, user_id: str, *,
    template_id: str, symbol: str, qty: int = 1,
) -> str:
    """Apply a pre-built strategy template to a ticker."""
    template = get_template_by_id(template_id)
    if not template:
        available = ", ".join(t["id"] for t in get_strategy_templates())
        return f"Template '{template_id}' not found. Available: {available}"

    broker = get_live_broker(session, user_id)
    if broker:
        asset = broker.validate_symbol(symbol)
        if asset is None:
            return f"Error: '{symbol.upper()}' is not a recognized ticker."

    sym = symbol.upper()
    entry_tree = template["entry_rule"]
    for a in entry_tree.get("actions", []):
        if a["action"] in ("enter_long", "enter_short", "limit_entry_at"):
            a["params"]["qty"] = qty

    entry = rule_repo.create_rule(
        session, user_id=user_id, symbol=sym,
        name=f"{sym} — {template['name']} (Entry)",
        description=template["description"],
        rule_type="entry", rule_tree=entry_tree,
    )
    exit_rule = rule_repo.create_rule(
        session, user_id=user_id, symbol=sym,
        name=f"{sym} — {template['name']} (Exit)",
        description=template["description"],
        rule_type="exit", rule_tree=template["exit_rule"],
    )
    session.commit()

    lines = [
        f"Applied '{template['name']}' to {sym}",
        f"  Entry rule #{entry.id}: {entry.name}",
        f"  Exit rule  #{exit_rule.id}: {exit_rule.name}",
        "",
        "Entry conditions:",
    ]
    for e in template.get("explanation", {}).get("entry", []):
        lines.append(f"  → {e}")
    lines.append("\nExit conditions:")
    for e in template.get("explanation", {}).get("exit", []):
        lines.append(f"  → {e}")
    return "\n".join(lines)


def evaluate_rule_tool(
    session: Session, user_id: str, *,
    rule_id: int | None = None,
    rule_tree: dict | None = None,
    symbol: str | None = None,
) -> str:
    """Evaluate a rule against current market data."""
    broker = get_live_broker(session, user_id)
    if not broker:
        return "Error: No broker connected."

    if rule_id:
        rule = rule_repo.get_rule_by_id(session, rule_id)
        if not rule or rule.user_id != user_id:
            return f"Error: Rule #{rule_id} not found."
        tree = json.loads(rule.rule_tree)
        sym = symbol or rule.symbol
    elif rule_tree:
        tree = rule_tree
        sym = symbol
    else:
        return "Error: Provide rule_id or rule_tree."

    if not sym:
        return "Error: Symbol is required."

    try:
        df = broker.get_bars(sym, timeframe="1Day", limit=220)
        if df.empty:
            return f"Error: No data for {sym}."
    except Exception as e:
        return f"Error fetching data for {sym}: {e}"

    result = evaluate_rule(df, tree, bar_idx=-1)
    lines = [f"Evaluation: {sym} — {'FIRED ★' if result.fired else 'NOT FIRED'}"]
    for g in result.groups:
        for c in g.conditions:
            status = "✓" if c.passed else "✗"
            lines.append(f"  {status} {c.detail}")
    if result.indicator_values:
        lines.append("\nIndicator values:")
        for k, v in result.indicator_values.items():
            lines.append(f"  {k} = {v:.4f}")
    return "\n".join(lines)


def backtest_tool(
    session: Session, user_id: str, *,
    entry_rule_id: int | None = None,
    exit_rule_id: int | None = None,
    entry_rule_tree: dict | None = None,
    exit_rule_tree: dict | None = None,
    symbol: str,
    start_date: str | None = None,
    end_date: str | None = None,
    initial_capital: float = 100000,
    position_size_pct: float = 10,
) -> str:
    """Run a historical backtest."""
    broker = get_live_broker(session, user_id)
    if not broker:
        return "Error: No broker connected."

    # Resolve rules
    if entry_rule_id:
        r = rule_repo.get_rule_by_id(session, entry_rule_id)
        if not r:
            return f"Error: Entry rule #{entry_rule_id} not found."
        entry_tree = json.loads(r.rule_tree)
    elif entry_rule_tree:
        entry_tree = entry_rule_tree
    else:
        return "Error: Provide entry_rule_id or entry_rule_tree."

    if exit_rule_id:
        r = rule_repo.get_rule_by_id(session, exit_rule_id)
        if not r:
            return f"Error: Exit rule #{exit_rule_id} not found."
        exit_tree = json.loads(r.rule_tree)
    elif exit_rule_tree:
        exit_tree = exit_rule_tree
    else:
        return "Error: Provide exit_rule_id or exit_rule_tree."

    end_dt = datetime.fromisoformat(end_date) if end_date else datetime.now(timezone.utc)
    start_dt = datetime.fromisoformat(start_date) if start_date else end_dt - timedelta(days=365)

    try:
        df = broker.get_bars(symbol, timeframe="1Day", start=start_dt, end=end_dt, limit=10000)
        if df.empty:
            return f"Error: No data for {symbol}."
    except Exception as e:
        return f"Error: {e}"

    config = BacktestConfig(initial_capital=initial_capital, position_size_pct=position_size_pct)
    result = run_backtest(df, entry_tree, exit_tree, config)

    lines = [
        f"Backtest: {symbol} ({len(df)} bars)",
        f"  Total trades:    {result.total_trades}",
        f"  Winning:         {result.winning_trades}",
        f"  Losing:          {result.losing_trades}",
        f"  Win rate:        {result.win_rate * 100:.0f}%" if result.win_rate else "  Win rate:        N/A",
        f"  Total P&L:       ${result.total_pnl:+,.2f}",
        f"  Avg P&L/trade:   ${result.avg_pnl_per_trade:+,.2f}" if result.avg_pnl_per_trade else "  Avg P&L/trade:   N/A",
        f"  Max drawdown:    {result.max_drawdown_pct:.2f}%",
        f"  Entry signals:   {result.entry_signals}",
    ]
    if result.trades:
        lines.append("\nTrades:")
        for t in result.trades[:10]:
            lines.append(
                f"  {t.entry_date[:10]} → {(t.exit_date or '?')[:10]}  "
                f"${t.entry_price:.2f} → ${t.exit_price or 0:.2f}  "
                f"P&L ${t.pnl or 0:+.2f}  {t.exit_reason or '?'}"
            )
        if len(result.trades) > 10:
            lines.append(f"  ... and {len(result.trades) - 10} more trades")
    return "\n".join(lines)


def sell_position(
    session: Session, user_id: str, *,
    symbol: str,
    order_type: str = "market",
    limit_price: float | None = None,
    qty: int | None = None,
) -> str:
    """Close a position."""
    broker = get_live_broker(session, user_id)
    if not broker:
        return "Error: No broker connected."

    sym = symbol.upper()

    # Cancel existing orders for this symbol
    try:
        for order in broker.get_open_orders():
            if order.get("symbol", "").upper() == sym:
                try:
                    broker._trading.cancel_order_by_id(order["id"])
                except Exception:
                    pass
    except Exception:
        pass

    # Get position info
    entry_price = None
    pos_qty = None
    try:
        for p in broker.get_positions():
            if p["symbol"].upper() == sym:
                entry_price = float(p.get("avg_entry_price", 0))
                pos_qty = int(float(p.get("qty", 0)))
                break
    except Exception:
        pass

    if pos_qty is None or pos_qty <= 0:
        return f"No open position for {sym}."

    sell_qty = qty or pos_qty

    try:
        if order_type == "limit" and limit_price:
            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            req = LimitOrderRequest(
                symbol=sym, qty=sell_qty, side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC, limit_price=float(limit_price),
            )
            broker._trading.submit_order(order_data=req)
            msg = f"Limit sell: {sell_qty} shares of {sym} at ${limit_price:.2f}"
        else:
            broker.close_position(sym, qty=sell_qty if qty else None)
            msg = f"Market sell: {sell_qty} shares of {sym}"

        # Record
        order_log_repo.record_order(
            session, user_id=user_id, symbol=sym, side="sell",
            qty=float(sell_qty), price=limit_price,
            order_type=order_type, source="mcp",
            mode="paper" if broker.paper else "live",
        )
        session.commit()
        return msg
    except Exception as e:
        return f"Error selling {sym}: {e}"


def place_order(
    session: Session, user_id: str, *,
    symbol: str, side: str = "buy", qty: int = 1,
    entry_price: float | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
) -> str:
    """Place a bracket order."""
    broker = get_live_broker(session, user_id)
    if not broker:
        return "Error: No broker connected."

    sym = symbol.upper()
    if not entry_price:
        try:
            q = broker.get_latest_quote(sym)
            entry_price = q.mid if q else None
        except Exception:
            pass
    if not entry_price:
        return f"Error: Could not determine price for {sym}."

    tp_price = round(entry_price * (1 + take_profit_pct / 100), 2) if take_profit_pct else None
    sl_price = round(entry_price * (1 - stop_loss_pct / 100), 2) if stop_loss_pct else None

    try:
        if tp_price or sl_price:
            broker.submit_bracket_order(
                symbol=sym, side=side, qty=qty,
                limit_price=entry_price,
                take_profit_price=tp_price, stop_loss_price=sl_price,
                time_in_force="gtc",
            )
            order_type = "bracket"
        else:
            from src.execution import OrderRequest, OrderType
            broker.submit_order(OrderRequest(
                symbol=sym, side=side, quantity=qty,
                order_type=OrderType.LIMIT, limit_price=entry_price,
            ))
            order_type = "limit"

        order_log_repo.record_order(
            session, user_id=user_id, symbol=sym, side=side,
            qty=float(qty), price=entry_price,
            order_type=order_type, source="mcp",
            mode="paper" if broker.paper else "live",
        )
        session.commit()

        parts = [f"Order placed: {side} {qty} {sym} @ ${entry_price:.2f}"]
        if tp_price:
            parts.append(f"TP @ ${tp_price:.2f}")
        if sl_price:
            parts.append(f"SL @ ${sl_price:.2f}")
        return " | ".join(parts)
    except Exception as e:
        return f"Error: {e}"


def toggle_rule(session: Session, user_id: str, *, rule_id: int, active: bool) -> str:
    """Enable or disable a rule."""
    rule = rule_repo.get_rule_by_id(session, rule_id)
    if not rule or rule.user_id != user_id:
        return f"Error: Rule #{rule_id} not found."
    rule_repo.update_rule(session, rule, is_active=active)
    session.commit()
    state = "enabled" if active else "disabled"
    return f"Rule #{rule_id} ({rule.symbol} — {rule.name}) {state}."


def delete_rule(session: Session, user_id: str, *, rule_id: int) -> str:
    """Delete a rule."""
    rule = rule_repo.get_rule_by_id(session, rule_id)
    if not rule or rule.user_id != user_id:
        return f"Error: Rule #{rule_id} not found."
    name = f"{rule.symbol} — {rule.name}"
    rule_repo.delete_rule(session, rule)
    session.commit()
    return f"Deleted rule #{rule_id} ({name})."


def set_bot_state(session: Session, user_id: str, *, state: str) -> str:
    """Set bot state: running, paused, or stopped."""
    if state not in ("running", "paused", "stopped"):
        return f"Error: state must be 'running', 'paused', or 'stopped'."
    broker_account = account_repo.get_broker_account_for_user(session, user_id)
    if not broker_account:
        return "Error: No broker account configured."
    settings = account_repo.get_account_settings(session, broker_account.id)
    if not settings:
        return "Error: No account settings."
    settings.bot_state = state
    settings.trading_enabled = state != "stopped"
    session.commit()
    return f"Bot state set to '{state}'."


def scan_watchlist(session: Session, user_id: str) -> str:
    """Scan all watchlist symbols for technical setups."""
    from src.db.models import UserSettings
    from sqlalchemy import select
    from src.rules_engine.indicators import compute_indicator

    settings = session.scalars(
        select(UserSettings).where(UserSettings.user_id == user_id)
    ).first()
    symbols = []
    if settings and settings.dashboard_layout:
        try:
            symbols = json.loads(settings.dashboard_layout).get("watchlist", [])
        except Exception:
            pass

    if not symbols:
        return "Watchlist is empty."

    broker = get_live_broker(session, user_id)
    if not broker:
        return "No broker connected."

    lines = ["Watchlist Scan\n"]
    for sym in symbols:
        try:
            df = broker.get_bars(sym, timeframe="1Day", limit=60)
            if df.empty or len(df) < 20:
                lines.append(f"  {sym:6s}  insufficient data")
                continue
            close = float(df["close"].iloc[-1])
            sma20 = float(compute_indicator(df, "sma", {"period": 20}).iloc[-1])
            sma50 = float(compute_indicator(df, "sma", {"period": 50}).iloc[-1]) if len(df) >= 50 else None
            rsi = float(compute_indicator(df, "rsi", {"period": 14}).iloc[-1])
            atr_pct = float(compute_indicator(df, "atr_pct", {"period": 14}).iloc[-1])
            gap = float(compute_indicator(df, "gap_pct").iloc[-1])

            above_sma20 = "↑" if close > sma20 else "↓"
            above_sma50 = ("↑" if close > sma50 else "↓") if sma50 else "?"
            rsi_label = "OVERSOLD" if rsi < 30 else "OVERBOUGHT" if rsi > 70 else ""

            lines.append(
                f"  {sym:6s}  ${close:>8.2f}  SMA20{above_sma20} SMA50{above_sma50}  "
                f"RSI {rsi:>5.1f} {rsi_label:10s}  ATR% {atr_pct:.1f}  Gap {gap:+.1f}%"
            )
        except Exception as e:
            lines.append(f"  {sym:6s}  error: {e}")

    return "\n".join(lines)
