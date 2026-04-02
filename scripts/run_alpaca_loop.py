#!/usr/bin/env python3
"""
Run the trading engine in a loop until market close (no user interaction).

Checks for entry signals every N minutes during regular session; stops when
market closes or daily loss limit / safe mode is hit.

The production runtime loads active users from the database. YAML and
single-user environment-variable modes remain as compatibility fallbacks for
local development and migration.

CLI: --live or --paper to override config (single-user only).
     --user <id> to run only one user (multi-user mode).
"""
import argparse
import logging
import sys
import time
from pathlib import Path
from datetime import date, datetime, timedelta
import pytz

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.config_loader import load_config
from src.trading_engine import TradingEngine
from src.brokers.alpaca_client import AlpacaBroker
from src.strategy import _atr
from src.universe import MarketCalendar, SessionType
from src.position_tracker import (
    load as load_tracked,
    add as add_tracked,
    merge_add_shares as merge_add_tracked,
    remove as remove_tracked,
    update as update_tracked,
    bars_held,
    minutes_held as holding_minutes,
    minutes_since_iso,
    get_tracked_entry_info,
)
from src.strategy import ExitReason, EntrySignal
from src.market_regime import MarketRegimeScorer
from src.news_sentiment import NewsSentimentPipeline, NewsRuleEngine, volume_spike_ratio
from src.entry_router import (
    EntryRouteSignal,
    log_options_stock_path_if_ineligible,
    route_to_options_executor,
    route_to_stock_executor,
    should_use_options,
)
from src.user_manager import UserManager
from src.db import get_session
from src.db.repos import account_repo, rule_repo, gate_log_repo, trade_repo, order_log_repo
from src.loop_lock import LoopLockError, UserLoopLock, acquire_user_loop_locks
from src.loop_helpers import (
    UserLoopContext,
    init_user_contexts,
    log_startup_summary,
)
from src.worker.control import (
    BOT_STATE_PAUSED,
    BOT_STATE_RUNNING,
    BOT_STATE_STOPPED,
    can_manage_open_positions,
    can_open_new_trades,
    normalize_bot_state,
)
from src.worker.health import mark_error, mark_running, mark_starting, mark_stopped
from src.worker.reconcile import reconcile_positions
from src.db.repos import (
    daily_summary_repo,
    gate_log_repo,
    order_log_repo,
    portfolio_repo,
    position_snapshot_repo,
    regime_repo,
    trade_repo,
)

# Optional Redis quote cache — pushes live quotes for the /quotes API endpoint
_quote_cache = None
_redis_url = __import__("os").environ.get("REDIS_URL")
if _redis_url:
    try:
        import redis as _redis_mod
        from src.market_data.quote_cache import RedisQuoteCache
        from src.market_data.streamer import QuoteUpdateProcessor
        _quote_cache = QuoteUpdateProcessor(RedisQuoteCache(_redis_mod.from_url(_redis_url, decode_responses=False)))
    except Exception:
        pass


def _push_quote(symbol: str, quote) -> None:
    """Best-effort push of a broker quote into Redis for the dashboard."""
    if _quote_cache is None or quote is None:
        return
    try:
        _quote_cache.process(symbol, bid=quote.bid, ask=quote.ask, timestamp=getattr(quote, "timestamp", None))
    except Exception:
        pass


def _option_chain_expiry_bounds(config: dict, as_of: date) -> tuple[date, date]:
    cs = (config.get("options") or {}).get("contract_selection") or {}
    dte_min = int(cs.get("expiry_min_days", 14))
    dte_max = int(cs.get("expiry_max_days", 35))
    return as_of + timedelta(days=dte_min), as_of + timedelta(days=dte_max)


def _option_chain_for_underlying(
    broker: AlpacaBroker,
    config: dict,
    underlying: str,
    log_dt: datetime,
) -> list:
    """Alpaca option chain mapped to selector candidates (empty list on error / no client)."""
    et = pytz.timezone("America/New_York")
    as_of = log_dt.astimezone(et).date()
    lo, hi = _option_chain_expiry_bounds(config, as_of)
    fn = getattr(broker, "get_option_chain_candidates", None)
    if fn is None:
        return []
    return fn(underlying, expiration_date_gte=lo, expiration_date_lte=hi)


_current_loop_uid: str | None = None  # set by the per-user loop body


def _log_entry_skip(
    dt: datetime,
    symbol: str,
    reason: str,
    *,
    verbose: bool,
    force: bool = False,
) -> None:
    """Log skip to DB only. No stdout noise — users see actions, not skips."""
    sym_u = str(symbol).upper()
    if _current_loop_uid:
        _persist_gate_log(_current_loop_uid, sym_u, reason, passed=False)


def _persist_portfolio_snapshot(user_id: str, broker, mode: str | None = None) -> None:
    """Snapshot account equity/cash/P&L into the DB for the dashboard."""
    try:
        snap = broker.get_account_snapshot()
        equity = snap.get("equity", 0.0)
        cash = snap.get("cash")
        last_eq = snap.get("last_equity")
        daily_pnl = (equity - last_eq) if last_eq else None
        daily_pnl_pct = (daily_pnl / last_eq * 100) if last_eq and daily_pnl is not None else None
        buying_power = broker.get_buying_power()
        with get_session() as session:
            portfolio_repo.snapshot_portfolio(
                session,
                user_id=user_id,
                equity=equity,
                cash=cash,
                buying_power=buying_power,
                daily_pnl=daily_pnl,
                daily_pnl_pct=daily_pnl_pct,
                mode=mode,
            )
    except Exception as e:
        print("  snapshot error:", type(e).__name__, str(e)[:80])


def _persist_regime(user_id: str, regime_result, bearish_regime: bool, regime_bars: dict | None = None, regime_scorer=None) -> None:
    """Write regime state to DB for the dashboard radar.

    spy_score / qqq_score: (close - MA) / MA — positive means above MA, negative below.
    vix: raw VIX level (the radar divides by 40 to normalize).
    """
    try:
        label = "bearish" if bearish_regime else (regime_result.condition if regime_result else "neutral")
        if label == "defensive":
            label = "bearish"

        spy_score = 0.0
        qqq_score = 0.0
        vix_val = 0.0

        if regime_bars:
            spy_sym = getattr(regime_scorer, "symbol_spy", "SPY") if regime_scorer else "SPY"
            qqq_sym = getattr(regime_scorer, "symbol_qqq", "QQQ") if regime_scorer else "QQQ"
            vix_sym = getattr(regime_scorer, "symbol_vix", "VIX") if regime_scorer else "VIX"
            ma_period = getattr(regime_scorer, "ma_period_trend", 50) if regime_scorer else 50

            def _pct_vs_ma(sym: str, period: int = 50) -> float:
                df = regime_bars.get(sym)
                if df is None or df.empty or len(df) < period:
                    return 0.0
                close = float(df["close"].iloc[-1])
                ma = float(df["close"].rolling(period).mean().iloc[-1])
                return (close - ma) / ma if ma else 0.0

            def _close(sym: str) -> float:
                df = regime_bars.get(sym)
                if df is None or df.empty:
                    return 0.0
                return float(df["close"].iloc[-1])

            spy_score = _pct_vs_ma(spy_sym, ma_period)
            qqq_score = _pct_vs_ma(qqq_sym, ma_period)
            vix_val = _close(vix_sym)

        with get_session() as session:
            regime_repo.record_regime(
                session,
                user_id=user_id,
                label=label,
                spy_score=spy_score,
                qqq_score=qqq_score,
                vix=vix_val,
            )
    except Exception as e:
        print("  regime persist error:", type(e).__name__, str(e)[:80])


def _persist_gate_log(user_id: str, symbol: str, reason: str, passed: bool = False) -> None:
    """Write a gate skip/pass entry to DB for the dashboard gate log panel."""
    try:
        with get_session() as session:
            gate_log_repo.record_gate(
                session,
                user_id=user_id,
                gate="entry",
                symbol=symbol,
                passed=passed,
                reason=reason,
            )
    except Exception:
        pass  # best-effort; don't break the loop


def _log_worker_event(user_id: str, gate: str, reason: str, symbol: str | None = None, passed: bool = True) -> None:
    """Write a general worker activity log entry."""
    try:
        with get_session() as session:
            gate_log_repo.record_gate(
                session,
                user_id=user_id,
                gate=gate,
                symbol=symbol,
                passed=passed,
                reason=reason,
            )
    except Exception:
        pass


def _persist_trade(
    user_id: str, symbol: str, side: str, qty: int,
    entry_price: float, exit_price: float,
    exit_reason: str, entered_at=None, exited_at=None, mode: str | None = None,
) -> None:
    """Record a closed trade to DB for the dashboard trades panel."""
    try:
        pnl = (exit_price - entry_price) * qty if side == "long" else (entry_price - exit_price) * qty
        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_price and side == "long" else (
            ((entry_price - exit_price) / entry_price * 100) if entry_price else None
        )
        with get_session() as session:
            trade_repo.record_trade(
                session,
                user_id=user_id,
                symbol=symbol,
                side=side,
                qty=float(qty),
                entry_price=entry_price,
                exit_price=exit_price,
                pnl=pnl,
                pnl_pct=pnl_pct,
                exit_reason=exit_reason,
                entered_at=entered_at,
                exited_at=exited_at,
                mode=mode,
            )
    except Exception as e:
        print("  trade persist error:", type(e).__name__, str(e)[:80])


def _persist_order_log(
    user_id: str, symbol: str, side: str, qty, price: float | None,
    source: str, mode: str | None = None, broker_order_id: str | None = None,
) -> None:
    """Record a buy/sell order event for audit trail."""
    try:
        with get_session() as session:
            order_log_repo.record_order(
                session,
                user_id=user_id,
                symbol=symbol,
                side=side,
                qty=float(qty),
                price=price,
                order_type="limit",
                source=source,
                mode=mode,
                broker_order_id=broker_order_id,
            )
    except Exception:
        pass


def _persist_position_snapshots(user_id: str, broker_positions: list, mode: str | None = None) -> None:
    """Snapshot all open positions for history audit trail."""
    try:
        with get_session() as session:
            position_snapshot_repo.snapshot_positions(
                session, user_id=user_id, positions=broker_positions, mode=mode,
            )
    except Exception:
        pass


def _persist_daily_summary(user_id: str, broker, mode: str, trade_date=None) -> None:
    """Compute and store end-of-day trading summary."""
    try:
        from datetime import date as date_type
        today = trade_date or date_type.today()

        with get_session() as session:
            snapshots = portfolio_repo.get_snapshots_for_date(session, user_id, today, mode=mode)
            trades_today = trade_repo.get_trades_for_date(session, user_id, today, mode=mode)

            open_equity = float(snapshots[0].equity) if snapshots and snapshots[0].equity else None
            close_equity = float(snapshots[-1].equity) if snapshots and snapshots[-1].equity else None

            # Max drawdown from intraday equity peaks
            max_dd_pct = None
            if snapshots:
                peak = float(snapshots[0].equity) if snapshots[0].equity else 0
                max_dd = 0.0
                for s in snapshots:
                    eq = float(s.equity) if s.equity else 0
                    if eq > peak:
                        peak = eq
                    dd = (peak - eq) / peak * 100 if peak > 0 else 0
                    if dd > max_dd:
                        max_dd = dd
                max_dd_pct = max_dd

            daily_pnl = (close_equity - open_equity) if close_equity and open_equity else None
            daily_pnl_pct = (daily_pnl / open_equity * 100) if daily_pnl and open_equity else None

            # Try Alpaca last_equity for daily return
            daily_return_pct = daily_pnl_pct
            try:
                snap = broker.get_account_snapshot()
                last_eq = snap.get("last_equity")
                if last_eq and close_equity:
                    daily_return_pct = ((close_equity - float(last_eq)) / float(last_eq)) * 100
            except Exception:
                pass

            win_count = sum(1 for t in trades_today if t.pnl and float(t.pnl) > 0)
            loss_count = sum(1 for t in trades_today if t.pnl and float(t.pnl) <= 0)

            # Count orders for positions opened/closed
            buy_count = 0
            sell_count = 0
            try:
                orders_today = order_log_repo.get_orders_for_date(session, user_id, today, mode=mode)
                buy_count = sum(1 for o in orders_today if o.side == "buy")
                sell_count = sum(1 for o in orders_today if o.side == "sell")
            except Exception:
                pass

            daily_summary_repo.record_daily_summary(
                session,
                user_id=user_id,
                date=today,
                mode=mode,
                open_equity=open_equity,
                close_equity=close_equity,
                daily_pnl=daily_pnl,
                daily_pnl_pct=daily_pnl_pct,
                daily_return_pct=daily_return_pct,
                win_count=win_count,
                loss_count=loss_count,
                total_trades_today=len(trades_today),
                max_drawdown_pct=max_dd_pct,
                positions_opened=buy_count,
                positions_closed=sell_count,
            )
    except Exception as e:
        print("  daily summary error:", type(e).__name__, str(e)[:80])


def _load_bot_state(user_id: str) -> str:
    with get_session() as session:
        broker_account = account_repo.get_broker_account_for_user(session, user_id)
        if broker_account is None:
            return BOT_STATE_RUNNING
        settings = account_repo.get_account_settings(session, broker_account.id)
        if settings is None:
            return BOT_STATE_RUNNING
        return normalize_bot_state(
            getattr(settings, "bot_state", None),
            trading_enabled=bool(settings.trading_enabled),
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trading loop until market close")
    parser.add_argument("--live", action="store_true", help="Use live account (real money)")
    parser.add_argument("--paper", action="store_true", help="Use paper account (default)")
    parser.add_argument(
        "--user",
        type=str,
        default=None,
        help="Run only this user_id (multi-user mode)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Print skip reasons for all symbols in trend scan; inverse (SQQQ/SPXS) skips always print without -v",
    )
    args = parser.parse_args()
    worker_name = "alpaca_loop"
    mark_starting(worker_name)
    if args.live and args.paper:
        parser.error("Use only one of --live or --paper")
    verbose = getattr(args, "verbose", False)
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    config_path = PROJECT_ROOT / "config" / "default.yaml"
    config = load_config(config_path)

    # ---------------------------------------------------------------------------
    # Multi-user setup via UserManager
    # ---------------------------------------------------------------------------
    users_path = PROJECT_ROOT / "config" / "users.yaml"
    user_manager = UserManager(config, users_path=users_path, runtime_source="db")

    if user_manager.multi_user and (args.live or args.paper):
        print("WARNING: --live/--paper flags are ignored in multi-user mode "
              "(each user has their own runtime paper/live setting)")

    user_contexts = init_user_contexts(
        user_manager,
        project_root=PROJECT_ROOT,
        user_filter=args.user,
    )
    if not user_contexts:
        print("No user contexts loaded — no onboarded accounts yet. Waiting for users to connect their broker in Settings.")
        sys.exit(0)

    log_startup_summary(user_contexts)

    # In compatibility single-user fallback, apply --live/--paper to the default user's config
    if not user_manager.multi_user:
        uctx = user_contexts[0]
        if args.live:
            uctx.config.setdefault("broker", {})["paper"] = False
            uctx.config.setdefault("news_sentiment", {})["enabled"] = False
        elif args.paper:
            uctx.config.setdefault("broker", {})["paper"] = True

    # Use the first user's config for shared settings (calendar, intervals)
    # These are system-wide, not per-user
    first_config = user_contexts[0].config
    broker_cfg = first_config.get("broker", {})
    if broker_cfg.get("firm") != "alpaca":
        print("Config broker.firm is not 'alpaca'. Exiting.")
        sys.exit(1)

    # For backward compat: single-user uses first context's broker/engine directly
    broker = user_contexts[0].broker
    engine = user_contexts[0].engine
    mode = "PAPER" if user_contexts[0].paper else "LIVE (real money)"
    print("AlgoSphere — broker mode:", mode, flush=True)
    calendar = MarketCalendar(first_config)
    regime_scorer = MarketRegimeScorer(first_config)
    et = pytz.timezone("America/New_York")
    is_live = not user_contexts[0].paper
    if is_live:
        exit_interval_min = int(broker_cfg.get("live_exit_check_interval_minutes", 1))
        entry_interval_min = int(broker_cfg.get("live_entry_check_interval_minutes", 2))
    else:
        exit_interval_min = int(broker_cfg.get("exit_check_interval_minutes") or broker_cfg.get("check_interval_minutes", 2))
        entry_interval_min = int(broker_cfg.get("entry_check_interval_minutes", 5))
    exit_interval_sec = exit_interval_min * 60
    entry_interval_sec = entry_interval_min * 60
    # NOTE: In multi-user mode, tracker uses user_id-scoped files.
    # The legacy tracker_path is kept for backward compat (single-user).
    tracker_path = PROJECT_ROOT / "data" / "positions_tracked.json"
    last_entry_check_time = None  # entry check every entry_interval_min (e.g. 10 min)
    mq_cfg = first_config.get("market_quality", {})
    stale_quote_max_age = float(mq_cfg.get("stale_quote_max_age_seconds", 60))
    regime_pct_above_50d_ma = float(first_config.get("universe", {}).get("regime_min_pct_above_50d_ma", 0.30))
    ns_cfg = first_config.get("news_sentiment") or {}
    news_enabled = bool(ns_cfg.get("enabled", False))
    news_pipeline = NewsSentimentPipeline(first_config) if news_enabled else None
    news_rules = NewsRuleEngine.from_config(first_config) if news_enabled else None
    news_vol_lookback = int(ns_cfg.get("volume_lookback_days", 20))

    print("Running until market close. Exits every %d min, entries every %d min. Ctrl+C to stop." % (exit_interval_min, entry_interval_min))
    if args.live:
        print("News sentiment: OFF (live trading; forced in loop).")
    elif news_enabled:
        print("News sentiment: ON (NewsAPI + FinBERT). Set %s in env." % (ns_cfg.get("newsapi_key_env") or "NEWSAPI_KEY"))
    print("-" * 50)

    while True:
        dt = datetime.now(et)
        if not calendar.is_trading_allowed(dt):
            session = calendar.get_session_at(dt)
            if session == SessionType.CLOSED:
                print(dt.strftime("%Y-%m-%d %H:%M ET"), "Market closed. Stopping.")
                for _uctx_eod in user_contexts:
                    _eod_mode = "paper" if _uctx_eod.paper else "live"
                    _persist_daily_summary(_uctx_eod.user_id, _uctx_eod.broker, _eod_mode, trade_date=dt.date())
                    print(dt.strftime("%H:%M ET"), "[%s] daily summary saved (%s)" % (_uctx_eod.user_id, _eod_mode))
                break
            print(dt.strftime("%Y-%m-%d %H:%M ET"), "Outside regular hours. Sleeping until next check.")
            time.sleep(exit_interval_sec)
            continue

        # ---- Check for credential reload signals from Settings page ----
        if _redis_url:
            try:
                import redis as _redis_reload_mod
                _rr = _redis_reload_mod.from_url(_redis_url, decode_responses=True)
                for _reload_key in _rr.scan_iter("algosphere:reload:*"):
                    _reload_uid = _reload_key.split(":")[-1]
                    user_manager.reload_user(_reload_uid)
                    for _i, _lctx in enumerate(user_contexts):
                        if _lctx.user_id == _reload_uid:
                            _new_uctx = user_manager.get_user(_reload_uid)
                            _new_broker = user_manager.get_broker(_reload_uid)
                            user_contexts[_i] = UserLoopContext(
                                user_id=_reload_uid,
                                user_ctx=_new_uctx,
                                broker=_new_broker,
                                engine=_lctx.engine,
                                config=_new_uctx.config,
                                paper=_new_uctx.paper,
                                data_dir=_lctx.data_dir,
                            )
                            break
                    _rr.delete(_reload_key)
                    print(f"[{_reload_uid}] Credentials reloaded — settings update applied")
            except Exception:
                pass

        # ---- Per-user trading pass ----
        all_users_stopped = True
        for _uctx in user_contexts:
          try:
            global _current_loop_uid
            _uid = _uctx.user_id
            _current_loop_uid = _uid
            _mode = "paper" if _uctx.paper else "live"
            broker = _uctx.broker
            engine = _uctx.engine
            config = _uctx.config

            print(dt.strftime("%H:%M ET"), "[%s] — in session, fetching account..." % _uid)
            sys.stdout.flush()
            account_equity = broker.get_equity()
            engine.update_equity(account_equity)
            engine.state.pdt.equity = account_equity

            # Stop if portfolio risk says no more trading today
            can_trade, reason = engine.portfolio_risk.can_trade(
                engine.state.portfolio_risk, account_equity, "SPY", dt.date()
            )
            if not can_trade:
                print(dt.strftime("%Y-%m-%d %H:%M ET"), "[%s]" % _uid, reason, "- Stopped for today.")
                continue
            all_users_stopped = False

            _data_dir = _uctx.data_dir
            positions = broker.get_positions()
            reconcile_positions(_uid, positions)
            _persist_portfolio_snapshot(_uid, broker, mode=_mode)
            _persist_position_snapshots(_uid, positions, mode=_mode)
            mark_running(
                worker_name,
                current_user_id=_uid,
                last_reconciled_at=dt.astimezone(pytz.UTC),
            )
            bot_state = _load_bot_state(_uid)
            can_open_orders = can_open_new_trades(bot_state)
            can_manage_positions = can_manage_open_positions(bot_state)
            tracked = load_tracked(_uid, data_dir=_data_dir)
            # Sync tracker with broker: add any position broker has that we don't track (e.g. after restart)
            for p in positions:
                sym = p["symbol"]
                if sym not in tracked:
                    cost = float(p.get("cost_basis") or 0)
                    qty_raw = int(float(p.get("qty") or 0))
                    qty = abs(qty_raw)
                    side = (p.get("side") or "long").strip().lower()
                    if side != "long" and side != "short":
                        side = "short" if qty_raw < 0 else "long"
                    # For shorts Alpaca may report cost_basis as proceeds; entry = price per share (positive)
                    entry = abs(cost / qty_raw) if qty_raw else 0
                    add_tracked(sym, qty, entry, 1.5, side=side, user_id=_uid, data_dir=_data_dir)
            tracked = load_tracked(_uid, data_dir=_data_dir)
            current_positions = {p["symbol"]: {"notional": p["market_value"], "stop_pct": tracked.get(p["symbol"], {}).get("stop_pct", 1.5)} for p in positions}
            sector_exposure_pct = {}
            # --- Symbols come from active rules only ---
            _rule_symbols = []
            try:
                from src.db.connection import _SessionLocal
                _rule_session = _SessionLocal()
                _rule_symbols = rule_repo.get_active_symbols(_rule_session, _uid)
                _rule_session.close()
            except Exception:
                pass
            if _rule_symbols:
                symbols = _rule_symbols
            else:
                # No active rules — nothing to trade
                symbols = []

            # Heartbeat: so you see the loop is running even when no trades
            print(dt.strftime("%H:%M ET"), "— equity $%.0f, checking %d symbols..." % (account_equity, len(symbols)))
            sys.stdout.flush()
            _log_worker_event(_uid, "heartbeat", "equity $%.0f, %d positions, checking %d symbols" % (account_equity, len(positions), len(symbols)))

            # ----- RULES ENGINE EXIT CHECK -----
            if can_manage_positions:
                try:
                    from src.rules_engine.evaluator import evaluate_rule
                    import json as _json
                    _ex_session = _SessionLocal()
                    for _ex_sym in list(tracked.keys()):
                        _ex_pos = tracked[_ex_sym]
                        _ex_qty = int(_ex_pos.get("qty", 0))
                        if _ex_qty <= 0:
                            continue
                        _exit_rules = rule_repo.get_rules(_ex_session, _uid, symbol=_ex_sym, rule_type="exit", active_only=True)
                        if not _exit_rules:
                            continue
                        try:
                            _ex_df = broker.get_bars(_ex_sym, timeframe="1Day", limit=60)
                            if _ex_df.empty or len(_ex_df) < 5:
                                continue
                        except Exception:
                            continue
                        for _rule in _exit_rules:
                            _tree = _json.loads(_rule.rule_tree)
                            _result = evaluate_rule(_ex_df, _tree, bar_idx=-1)
                            if _result.fired:
                                try:
                                    broker.close_position(_ex_sym)
                                    _close_price = float(_ex_df["close"].iloc[-1])
                                    _entry_price = float(_ex_pos.get("entry_price", 0))
                                    _pnl = (_close_price - _entry_price) * _ex_qty if _entry_price else 0
                                    _pnl_pct = ((_close_price - _entry_price) / _entry_price * 100) if _entry_price else 0
                                    trade_repo.record_trade(
                                        _ex_session, user_id=_uid, symbol=_ex_sym, side="long",
                                        qty=float(_ex_qty), entry_price=_entry_price, exit_price=_close_price,
                                        pnl=_pnl, pnl_pct=_pnl_pct, exit_reason=f"rule:{_rule.name}",
                                        mode="paper" if broker.paper else "live",
                                    )
                                    order_log_repo.record_order(
                                        _ex_session, user_id=_uid, symbol=_ex_sym, side="sell",
                                        qty=float(_ex_qty), price=_close_price,
                                        order_type="market", source=f"rule:{_rule.name}",
                                        mode="paper" if broker.paper else "live",
                                    )
                                    gate_log_repo.record_gate(
                                        _ex_session, user_id=_uid, gate="rule_exit",
                                        symbol=_ex_sym, passed=True,
                                        reason=f"Rule '{_rule.name}' fired → sold {_ex_qty} shares @ ${_close_price:.2f} (P&L: ${_pnl:.2f})",
                                    )
                                    remove_tracked(_ex_sym, user_id=_uid, data_dir=_data_dir)
                                    print(dt.strftime("%H:%M ET"), f"— RULE EXIT: sold {_ex_qty} {_ex_sym} @ ${_close_price:.2f} (P&L ${_pnl:.2f}, rule: {_rule.name})")
                                    # Auto-deactivate exit rule + matching entry rules for this symbol
                                    rule_repo.update_rule(_ex_session, _rule, is_active=False)
                                    for _sibling in rule_repo.get_rules(_ex_session, _uid, symbol=_ex_sym, active_only=True):
                                        rule_repo.update_rule(_ex_session, _sibling, is_active=False)
                                    print(dt.strftime("%H:%M ET"), f"— Rules for {_ex_sym} deactivated (position closed)")
                                except Exception as _ce:
                                    print(dt.strftime("%H:%M ET"), f"— RULE EXIT FAILED for {_ex_sym}: {_ce}")
                                break  # first matching exit rule wins
                    _ex_session.commit()
                    _ex_session.close()
                except Exception as _ex_exc:
                    print(dt.strftime("%H:%M ET"), f"— Rules engine exit error: {_ex_exc}")

            # ----- Legacy exit rules for each tracked position -----
            if can_manage_positions:
                for symbol in list(tracked.keys()):
                    pos = tracked[symbol]
                    qty = int(pos.get("qty", 0))
                    side = (pos.get("side") or "long").strip().lower()
                    if qty <= 0:
                        remove_tracked(symbol, user_id=_uid, data_dir=_data_dir)
                        continue
                    if not any(p["symbol"] == symbol for p in positions):
                        remove_tracked(symbol, user_id=_uid, data_dir=_data_dir)
                        continue
                    quote = broker.get_latest_quote(symbol)
                    _push_quote(symbol, quote)
                    if not quote:
                        continue
                    entry_price = float(pos.get("entry_price", 0))
                    if entry_price <= 0:
                        continue
                    entry_time_iso = pos.get("entry_time", "")
                    bars = bars_held(entry_time_iso, dt)
                    hold_mins = holding_minutes(entry_time_iso, dt) if entry_time_iso else None
                    partial_taken = bool(pos.get("partial_taken", False))
                    trail_high_val = pos.get("trail_high")
                    trail_high_f = float(trail_high_val) if trail_high_val is not None else None
                    atr_pct_exit = None
                    if symbol in symbols:
                        try:
                            df_ex = broker.get_bars(symbol, timeframe="1Day", limit=20)
                            if not df_ex.empty and len(df_ex) >= 14:
                                atr = _atr(df_ex["high"], df_ex["low"], df_ex["close"], 14)
                                atr_pct_exit = (atr.iloc[-1] / df_ex["close"].iloc[-1]) * 100
                        except Exception:
                            pass
                    # News rule: negative sentiment + weak trend → full exit (long only)
                    if side != "short" and news_enabled and news_pipeline and news_rules:
                        try:
                            df_news = broker.get_bars(symbol, timeframe="1Day", limit=max(60, news_rules.weak_trend_ma_period + 5))
                            if not df_news.empty and len(df_news) >= news_rules.weak_trend_ma_period:
                                sent = news_pipeline.sentiment_for_symbol(symbol)
                                if news_rules.should_sell(sent, df_news):
                                    mh = float(getattr(engine.strategy, "min_hold_minutes", 0) or 0)
                                    skip_news_exit = mh > 0 and hold_mins is not None and hold_mins < mh
                                    if skip_news_exit:
                                        if verbose:
                                            print(
                                                dt.strftime("%H:%M ET"),
                                                symbol,
                                                "news exit skipped — min_hold",
                                                f"({hold_mins:.0f}m < {mh:.0f}m)",
                                            )
                                    else:
                                        sell_order = engine.execution.build_order(symbol, "sell", qty, quote.mid, quote.spread_pct)
                                        if sell_order:
                                            broker.submit_order(sell_order)
                                            print(
                                                dt.strftime("%H:%M ET"),
                                                symbol,
                                                "SELL",
                                                qty,
                                                "shares —",
                                                ExitReason.NEWS_SENTIMENT.value,
                                                "(sent=%.2f)" % sent,
                                            )
                                            _persist_trade(_uid, symbol, side, qty, entry_price, quote.mid, "news-sentiment", exited_at=dt, mode=_mode)
                                            _persist_order_log(_uid, symbol, "sell", qty, quote.mid, "news-sentiment", mode=_mode)
                                            _log_worker_event(_uid, "sell", "SELL %d shares — news sentiment (sent=%.2f)" % (qty, sent), symbol=symbol)
                                        remove_tracked(symbol, user_id=_uid, data_dir=_data_dir)
                                        continue
                        except Exception as e:
                            if verbose:
                                print(dt.strftime("%H:%M ET"), symbol, "news exit skip —", type(e).__name__, str(e)[:50])
                    # Legacy short: cover only (no new shorts opened)
                    if side == "short":
                        exit_signal = engine.strategy.check_exit_short(
                            symbol,
                            entry_price,
                            quote.mid,
                            bars,
                            1.5,
                            2.0,
                            10,
                            quote.spread_pct,
                            atr_pct_exit,
                            minutes_held=hold_mins,
                        )
                        if exit_signal:
                            cover_order = engine.execution.build_order(symbol, "buy", qty, quote.mid, quote.spread_pct)
                            if cover_order:
                                broker.submit_order(cover_order)
                                print(dt.strftime("%H:%M ET"), symbol, "COVER", qty, "shares (legacy short) —", exit_signal.reason.value)
                                _persist_trade(_uid, symbol, "short", qty, entry_price, quote.mid, exit_signal.reason.value, exited_at=dt, mode=_mode)
                                _persist_order_log(_uid, symbol, "buy", qty, quote.mid, "cover-short", mode=_mode)
                                _log_worker_event(_uid, "sell", "COVER %d shares — %s" % (qty, exit_signal.reason.value), symbol=symbol)
                            remove_tracked(symbol, user_id=_uid, data_dir=_data_dir)
                        continue
                    if partial_taken:
                        new_high = max(trail_high_f or entry_price, quote.mid)
                        update_tracked(symbol, user_id=_uid, data_dir=_data_dir, trail_high=new_high)
                        trail_high_f = new_high
                    exit_signal = engine.check_exit(
                        symbol,
                        entry_price,
                        quote.mid,
                        bars,
                        quote.spread_pct,
                        atr_pct_exit,
                        partial_taken=partial_taken,
                        trail_high=trail_high_f,
                        current_qty=qty,
                        minutes_held=hold_mins,
                    )
                    if exit_signal:
                        if exit_signal.reason == ExitReason.PARTIAL_TAKE_PROFIT:
                            qty_to_sell = exit_signal.metadata.get("qty_to_sell", max(1, qty // 2))
                            sell_order = engine.execution.build_order(symbol, "sell", qty_to_sell, quote.mid, quote.spread_pct)
                            if sell_order:
                                broker.submit_order(sell_order)
                                print(dt.strftime("%H:%M ET"), symbol, "SELL", qty_to_sell, "shares (partial @ 2%) —", exit_signal.reason.value)
                                _persist_trade(_uid, symbol, side, qty_to_sell, entry_price, quote.mid, exit_signal.reason.value, exited_at=dt, mode=_mode)
                                _persist_order_log(_uid, symbol, "sell", qty_to_sell, quote.mid, exit_signal.reason.value, mode=_mode)
                                _log_worker_event(_uid, "sell", "SELL %d shares (partial) — %s" % (qty_to_sell, exit_signal.reason.value), symbol=symbol)
                            remaining = qty - qty_to_sell
                            if remaining <= 0:
                                engine.record_profit_exit(symbol, dt, quote.mid)
                                remove_tracked(symbol, user_id=_uid, data_dir=_data_dir)
                            else:
                                update_tracked(symbol, user_id=_uid, data_dir=_data_dir, qty=remaining, partial_taken=True, trail_high=quote.mid)
                        else:
                            sell_order = engine.execution.build_order(symbol, "sell", qty, quote.mid, quote.spread_pct)
                            if sell_order:
                                broker.submit_order(sell_order)
                                print(dt.strftime("%H:%M ET"), symbol, "SELL", qty, "shares —", exit_signal.reason.value)
                                _persist_trade(_uid, symbol, side, qty, entry_price, quote.mid, exit_signal.reason.value, exited_at=dt, mode=_mode)
                                _persist_order_log(_uid, symbol, "sell", qty, quote.mid, exit_signal.reason.value, mode=_mode)
                                _log_worker_event(_uid, "sell", "SELL %d shares — %s" % (qty, exit_signal.reason.value), symbol=symbol)
                            if exit_signal.reason == ExitReason.STOP_LOSS:
                                engine.record_stop_loss(symbol, dt, entry_price=entry_price)
                            elif exit_signal.reason in (ExitReason.TAKE_PROFIT, ExitReason.TRAILING_STOP):
                                engine.record_profit_exit(symbol, dt, quote.mid)
                            remove_tracked(symbol, user_id=_uid, data_dir=_data_dir)
            elif bot_state == BOT_STATE_STOPPED:
                print(dt.strftime("%H:%M ET"), f"[{_uid}] bot stopped — sync only; automated entries and exits are disabled")
            # Entry check every entry_interval_min (e.g. 10 min)
            now_sec = time.time()
            do_entry_check = last_entry_check_time is None or (now_sec - last_entry_check_time) >= entry_interval_sec
            if do_entry_check:
                last_entry_check_time = now_sec

            if not can_open_orders:
                if bot_state == BOT_STATE_PAUSED and do_entry_check:
                    print(dt.strftime("%H:%M ET"), f"[{_uid}] bot paused — open positions stay managed, but no new entries will be placed")
                continue

            bearish_regime = False
            if do_entry_check:
                # Regime filter: if < 30% above 50D MA = bearish → bear-ETF path; else long entries
                above_50d = 0
                total_with_bars = 0
                try:
                    for sym in symbols:
                        b = broker.get_bars(sym, timeframe="1Day", limit=55)
                        if b.empty or len(b) < 50:
                            continue
                        total_with_bars += 1
                        close = float(b["close"].iloc[-1])
                        ma50 = float(b["close"].rolling(50).mean().iloc[-1])
                        if close > ma50:
                            above_50d += 1
                    if total_with_bars > 0:
                        pct_above = above_50d / total_with_bars
                        if pct_above < regime_pct_above_50d_ma:
                            bearish_regime = True
                            print(dt.strftime("%H:%M ET"), "— bearish regime: %.0f%% above 50D MA — long entries skipped (bear ETFs if breakdown)" % (pct_above * 100))
                except Exception as e:
                    if verbose:
                        print(dt.strftime("%H:%M ET"), "— regime filter skip:", type(e).__name__, str(e)[:50])

            if do_entry_check:
                boost_inverse_etf_priority = bool(bearish_regime)
                # Market regime: fetch SPY/QQQ/VIX/HYG/TLT bars for position size multiplier
                regime_multiplier = None
                if regime_scorer.enabled:
                    try:
                        regime_bars = {}
                        for sym in regime_scorer.required_symbols():
                            b = broker.get_bars(sym, timeframe="1Day", limit=60)
                            if not b.empty and len(b) >= regime_scorer.ma_period_trend:
                                regime_bars[sym] = b
                        if regime_bars:
                            regime_result = regime_scorer.compute(regime_bars)
                            regime_multiplier = regime_result.size_multiplier
                            print(dt.strftime("%H:%M ET"), "— regime score %d (%s), size mult %.2f" % (regime_result.score, regime_result.condition, regime_multiplier))
                            _persist_regime(_uid, regime_result, bearish_regime, regime_bars=regime_bars, regime_scorer=regime_scorer)
                            _log_worker_event(_uid, "regime", "regime score %d (%s), size mult %.2f" % (regime_result.score, regime_result.condition, regime_multiplier))
                    except Exception as e:
                        if verbose:
                            print(dt.strftime("%H:%M ET"), "— regime skip:", type(e).__name__, str(e)[:50])
                bear_inv_regime_mult = regime_multiplier
                if boost_inverse_etf_priority:
                    bear_inv_regime_mult = max(regime_multiplier, 1.0) if regime_multiplier is not None else 1.0
                if verbose:
                    print(dt.strftime("%H:%M ET"), "Entry check: equity $%.0f, positions %d" % (account_equity, len(positions)))
                open_orders = broker.get_open_orders()
                open_order_symbols = {o.get("symbol", "").upper() for o in (open_orders or []) if o.get("symbol")}
                available_cash = broker.get_buying_power()
                opts_enabled = bool((config.get("options") or {}).get("enabled"))
                if opts_enabled:
                    ou = (config.get("options") or {}).get("allowed_underlyings") or []
                    print(
                        dt.strftime("%H:%M ET"),
                        "— options: enabled | chain via Alpaca options API (broker.options_feed); allowed:",
                        ", ".join(str(x).upper() for x in ou) or "(none)",
                        flush=True,
                    )
                ma_fast_period = engine.strategy.ma_fast
                ma_slow_period = engine.strategy.ma_slow

                # Bear ETFs: long only when bearish + breakdown (e.g. QQQ below 50D MA)
                bear_etfs_cfg = config.get("universe", {}).get("bear_etfs", {})
                bear_etf_all_raw = list(bear_etfs_cfg.get("symbols") or [])
                bear_etf_universe_set = {str(s).upper() for s in bear_etf_all_raw}
                bear_etf_symbols = list(bear_etf_all_raw)
                if not bearish_regime and "SQQQ" in bear_etf_universe_set:
                    _log_entry_skip(
                        dt,
                        "SQQQ",
                        "not bearish regime (breadth filter)",
                        verbose=verbose,
                        force=True,
                    )
                breakdown_cfg = bear_etfs_cfg.get("breakdown", {}) or {}
                ref_symbol = breakdown_cfg.get("reference_symbol") or "QQQ"
                breakdown_ma_period = int(breakdown_cfg.get("ma_period") or 50)
                prefer_sqqq_qqq = bool(bear_etfs_cfg.get("prefer_sqqq_when_breakdown_reference_is_qqq", True))
                if prefer_sqqq_qqq and str(ref_symbol).upper() == "QQQ":
                    sqqq_only = [s for s in bear_etf_symbols if str(s).upper() == "SQQQ"]
                    if sqqq_only:
                        bear_etf_symbols = sqqq_only
                    elif verbose:
                        print(dt.strftime("%H:%M ET"), "— bear ETFs: QQQ breakdown but SQQQ not in bear_etfs.symbols; using full list")
                breakdown_detected = False
                ref_close: float | None = None
                ref_ma: float | None = None
                # Legacy bear ETF path disabled — rules engine handles all entries
                bear_etf_symbols = []
                if bearish_regime and bear_etf_symbols and ref_symbol:
                    try:
                        ref_bars = broker.get_bars(ref_symbol, timeframe="1Day", limit=breakdown_ma_period + 10)
                        if not ref_bars.empty and len(ref_bars) >= breakdown_ma_period:
                            ref_close = float(ref_bars["close"].iloc[-1])
                            ref_ma = float(ref_bars["close"].rolling(breakdown_ma_period).mean().iloc[-1])
                            if ref_close < ref_ma:
                                breakdown_detected = True
                                if verbose:
                                    print(dt.strftime("%H:%M ET"), "— breakdown: %s below %dD MA (%.2f < %.2f)" % (ref_symbol, breakdown_ma_period, ref_close, ref_ma))
                    except Exception as e:
                        if verbose:
                            print(dt.strftime("%H:%M ET"), "— breakdown check skip:", type(e).__name__, str(e)[:40])

                # SQQQ entry signal: QQQ close < QQQ 50D MA (always QQQ/50 for SQQQ; independent of breakdown.reference_symbol)
                qqq_below_ma50 = False
                qqq_price: float | None = None
                qqq_ma50: float | None = None
                if bearish_regime:
                    try:
                        if (
                            str(ref_symbol).upper() == "QQQ"
                            and breakdown_ma_period == 50
                            and ref_close is not None
                            and ref_ma is not None
                        ):
                            qqq_price, qqq_ma50 = ref_close, ref_ma
                        else:
                            qb = broker.get_bars("QQQ", timeframe="1Day", limit=60)
                            if not qb.empty and len(qb) >= 50:
                                qqq_price = float(qb["close"].iloc[-1])
                                qqq_ma50 = float(qb["close"].rolling(50).mean().iloc[-1])
                        if qqq_price is not None and qqq_ma50 is not None:
                            qqq_below_ma50 = qqq_price < qqq_ma50
                            if qqq_below_ma50 and verbose:
                                print(
                                    dt.strftime("%H:%M ET"),
                                    "— SQQQ gate: QQQ %.2f < 50D MA %.2f" % (qqq_price, qqq_ma50),
                                )
                    except Exception as e:
                        if verbose:
                            print(dt.strftime("%H:%M ET"), "— QQQ MA50 check skip:", type(e).__name__, str(e)[:40])

                if bearish_regime and bear_etf_symbols:
                    # Inverse entries: SQQQ when QQQ < MA50; other bear symbols use configured breakdown ref/MA
                    max_bear_etf_positions = int(bear_etfs_cfg.get("max_positions") or 2)
                    bear_etf_stop_pct = float(bear_etfs_cfg.get("stop_pct") or 2.0)
                    max_bear_etf_pct = float(bear_etfs_cfg.get("max_exposure_pct_equity") or 10)
                    max_bear_etf_notional = account_equity * (max_bear_etf_pct / 100.0)
                    current_bear_etf_notional = sum(
                        abs(float(p.get("market_value") or 0))
                        for p in positions
                        if str(p.get("symbol") or "").upper() in bear_etf_universe_set
                    )
                    tracked_upper = {str(k).upper() for k in tracked}
                    pos_bear_syms = {str(p.get("symbol") or "").upper() for p in positions}
                    current_bear_etf = len(
                        {s for s in bear_etf_universe_set if s in pos_bear_syms or s in tracked_upper}
                    )
                    for symbol in bear_etf_symbols:
                        sym_u = str(symbol).upper()
                        if current_bear_etf >= max_bear_etf_positions:
                            _log_entry_skip(
                                dt,
                                sym_u,
                                "max inverse ETF positions (%d)" % max_bear_etf_positions,
                                verbose=verbose,
                                force=True,
                            )
                            break
                        if sym_u == "SQQQ":
                            if not qqq_below_ma50:
                                if qqq_price is None or qqq_ma50 is None:
                                    _log_entry_skip(
                                        dt,
                                        sym_u,
                                        "QQQ vs 50D MA unavailable (insufficient data or API)",
                                        verbose=verbose,
                                        force=True,
                                    )
                                else:
                                    _log_entry_skip(
                                        dt,
                                        sym_u,
                                        "QQQ not below 50D MA (QQQ %.2f >= MA %.2f)" % (qqq_price, qqq_ma50),
                                        verbose=verbose,
                                        force=True,
                                    )
                                continue
                        elif not breakdown_detected:
                            _log_entry_skip(
                                dt,
                                sym_u,
                                "no breakdown (%s not below %dD MA)" % (ref_symbol, breakdown_ma_period),
                                verbose=verbose,
                                force=True,
                            )
                            continue
                        reasons_block = []
                        if symbol in current_positions:
                            reasons_block.append("already in positions")
                        if symbol.upper() in open_order_symbols:
                            reasons_block.append("open buy/sell order")
                        if symbol.upper() in tracked:
                            reasons_block.append("in tracked state")
                        if reasons_block:
                            _log_entry_skip(
                                dt,
                                sym_u,
                                "; ".join(reasons_block),
                                verbose=verbose,
                                force=True,
                            )
                            continue
                        try:
                            df = broker.get_bars(symbol, timeframe="1Day", limit=60)
                            if df.empty or len(df) < 20:
                                _log_entry_skip(
                                    dt,
                                    sym_u,
                                    "not enough daily bars (need 20, got %d)" % (0 if df.empty else len(df)),
                                    verbose=verbose,
                                    force=True,
                                )
                                continue
                            close = float(df["close"].iloc[-1])
                            quote = broker.get_latest_quote(symbol)
                            _push_quote(symbol, quote)
                            if quote and getattr(quote, "is_stale", None) and quote.is_stale(stale_quote_max_age):
                                spread_pct = 0.15
                            else:
                                spread_pct = quote.spread_pct if quote else 0.15
                            spread_cap = engine.market_quality._max_spread_for_symbol(symbol)
                            if spread_pct is not None and spread_pct > spread_cap:
                                _log_entry_skip(
                                    dt,
                                    sym_u,
                                    "spread %.3f%% > cap %.3f%%" % (spread_pct, spread_cap),
                                    verbose=verbose,
                                    force=True,
                                )
                                continue
                            if close * 1 > available_cash:
                                _log_entry_skip(
                                    dt,
                                    sym_u,
                                    "insufficient buying power (1 share ~$%.2f > $%.2f available)" % (close, available_cash),
                                    verbose=verbose,
                                    force=True,
                                )
                                continue
                            sizing = engine.sizer.size_position(
                                account_equity,
                                close,
                                bear_etf_stop_pct,
                                symbol,
                                current_positions,
                                sector_exposure_pct,
                                symbol_sector=None,
                                regime_size_multiplier=bear_inv_regime_mult,
                            )
                            if not sizing or sizing.shares <= 0:
                                rr = getattr(sizing, "reject_reason", None) if sizing else None
                                _log_entry_skip(
                                    dt,
                                    sym_u,
                                    "position size rejected (%s)" % (rr or "zero shares"),
                                    verbose=verbose,
                                    force=True,
                                )
                                continue
                            if current_bear_etf_notional + sizing.notional > max_bear_etf_notional:
                                _log_entry_skip(
                                    dt,
                                    sym_u,
                                    "inverse ETF exposure cap (%.0f%% equity ≈ $%.0f max, current $%.0f + new $%.0f)"
                                    % (
                                        max_bear_etf_pct,
                                        max_bear_etf_notional,
                                        current_bear_etf_notional,
                                        sizing.notional,
                                    ),
                                    verbose=verbose,
                                    force=True,
                                )
                                continue
                            buy_order = engine.execution.build_order(symbol, "buy", sizing.shares, quote.mid if quote else close, spread_pct or 0.15)
                            if not buy_order:
                                _log_entry_skip(dt, sym_u, "execution could not build order", verbose=verbose, force=True)
                                continue
                            opts_cfg = config.get("options") or {}
                            und = "QQQ" if sym_u == "SQQQ" else ("SPY" if sym_u == "SPXS" else sym_u)
                            signal_bear = EntryRouteSignal(
                                underlying=und,
                                direction="bearish",
                                source="bear_etf",
                                stock_symbol=sym_u,
                            )
                            u_spot = None
                            uq_und = broker.get_latest_quote(und)
                            if uq_und is not None and getattr(uq_und, "mid", None):
                                try:
                                    u_spot = float(uq_und.mid)
                                except (TypeError, ValueError):
                                    u_spot = None
                            options_handled = False
                            if opts_cfg.get("enabled") and should_use_options(config, signal_bear):
                                chain_bear = _option_chain_for_underlying(broker, config, und, dt)
                                options_handled = route_to_options_executor(
                                    config,
                                    signal_bear,
                                    log_dt=dt,
                                    verbose=verbose,
                                    account_equity=account_equity,
                                    positions=positions,
                                    broker=broker,
                                    execution_manager=engine.execution,
                                    chain_candidates=chain_bear,
                                    underlying_spot=u_spot,
                                )
                            elif opts_cfg.get("enabled"):
                                log_options_stock_path_if_ineligible(config, signal_bear, dt)
                            if not options_handled:

                                def _bear_stock_execute() -> None:
                                    nonlocal current_bear_etf_notional, current_bear_etf, current_positions
                                    broker.submit_order(buy_order)
                                    current_bear_etf_notional += sizing.notional
                                    add_tracked(symbol, sizing.shares, close, bear_etf_stop_pct, side="long", user_id=_uid, data_dir=_data_dir)
                                    current_bear_etf += 1
                                    current_positions[symbol] = {"notional": sizing.notional, "stop_pct": bear_etf_stop_pct}
                                    label_local = "QQQ < MA50" if sym_u == "SQQQ" else "breakdown"
                                    print(
                                        dt.strftime("%H:%M ET"),
                                        symbol,
                                        "BUY (bear ETF, %s)" % label_local,
                                        sizing.shares,
                                        "shares",
                                    )
                                    _persist_order_log(_uid, symbol, "buy", sizing.shares, close, "bear_etf", mode=_mode)
                                    _log_worker_event(_uid, "buy", "BUY %d shares (bear ETF, %s)" % (sizing.shares, label_local), symbol=symbol)

                                route_to_stock_executor(signal_bear, _bear_stock_execute)
                        except Exception as e:
                            _log_entry_skip(
                                dt,
                                sym_u,
                                "%s: %s" % (type(e).__name__, str(e)[:80]),
                                verbose=verbose,
                                force=True,
                            )
                            continue

                    # SQQQ controlled scaling — DISABLED (rules engine handles all entries)
                    scaling_cfg = {}
                    sqqq_sym = str(scaling_cfg.get("symbol") or "SQQQ").upper()

                    if bool(scaling_cfg.get("enabled", False)) and sqqq_sym in bear_etf_universe_set:
                        ref_sym_cfg = str(scaling_cfg.get("reference_symbol") or "QQQ").upper()
                        ref_ma_cfg = int(scaling_cfg.get("ma_period") or 50)
                        cooldown_minutes = int(scaling_cfg.get("cooldown_minutes") or 10)
                        require_price_above_last_entry = bool(
                            scaling_cfg.get("require_price_above_last_entry", True)
                        )
                        steps = list(scaling_cfg.get("steps") or [])

                        if (
                            qqq_price is None
                            or qqq_ma50 is None
                            or qqq_ma50 <= 0
                            or ref_sym_cfg != "QQQ"
                            or ref_ma_cfg != 50
                        ):
                            _log_entry_skip(
                                dt,
                                sqqq_sym,
                                "scaling skipped — QQQ/50D reference unavailable",
                                verbose=verbose,
                                force=True,
                            )
                        else:
                            dist_pct = (qqq_ma50 - qqq_price) / qqq_ma50 * 100.0
                            active_step_idx = -1
                            for i, step in enumerate(steps):
                                if not isinstance(step, dict):
                                    continue
                                trig = float(step.get("reference_ma_distance_pct_min") or 0.0)
                                if dist_pct >= trig:
                                    active_step_idx = i

                            tracked_row = get_tracked_entry_info(
                                _data_dir / f"positions_{_uid}.json", sqqq_sym
                            )
                            scale_count = int(
                                (tracked_row.get("scale_count") or 1)
                                if sqqq_sym in current_positions
                                else 0
                            )
                            last_entry_price = tracked_row.get("last_entry_price")
                            last_scale_ts = tracked_row.get("last_scale_ts")

                            mins_since_last = minutes_since_iso(
                                str(last_scale_ts) if last_scale_ts else None, dt
                            )
                            cooldown_ok = mins_since_last is None or mins_since_last >= cooldown_minutes

                            if sqqq_sym not in current_positions:
                                _log_entry_skip(
                                    dt,
                                    sqqq_sym,
                                    "scaling skipped — no existing SQQQ position",
                                    verbose=verbose,
                                    force=True,
                                )
                            elif active_step_idx < 0:
                                _log_entry_skip(
                                    dt,
                                    sqqq_sym,
                                    "scaling skipped — QQQ only %.2f%% below MA50" % dist_pct,
                                    verbose=verbose,
                                    force=True,
                                )
                            elif scale_count >= len(steps):
                                _log_entry_skip(
                                    dt,
                                    sqqq_sym,
                                    "scaling skipped — already at max scale steps (%d)" % len(steps),
                                    verbose=verbose,
                                    force=True,
                                )
                            elif scale_count > active_step_idx:
                                _log_entry_skip(
                                    dt,
                                    sqqq_sym,
                                    "scaling skipped — current scale_count %d already matches trend step %d"
                                    % (scale_count, active_step_idx + 1),
                                    verbose=verbose,
                                    force=True,
                                )
                            elif sqqq_sym in open_order_symbols:
                                _log_entry_skip(
                                    dt,
                                    sqqq_sym,
                                    "scaling skipped — open order pending",
                                    verbose=verbose,
                                    force=True,
                                )
                            elif current_bear_etf_notional >= max_bear_etf_notional - 1e-6:
                                _log_entry_skip(
                                    dt,
                                    sqqq_sym,
                                    "scaling skipped — inverse exposure at cap (~$%.0f)"
                                    % max_bear_etf_notional,
                                    verbose=verbose,
                                    force=True,
                                )
                            elif not cooldown_ok:
                                _log_entry_skip(
                                    dt,
                                    sqqq_sym,
                                    "scaling skipped — cooldown %.1f/%d min"
                                    % (mins_since_last or 0.0, cooldown_minutes),
                                    verbose=verbose,
                                    force=True,
                                )
                            else:
                                try:
                                    df_s = broker.get_bars(sqqq_sym, timeframe="1Day", limit=60)
                                    if df_s.empty or len(df_s) < 20:
                                        _log_entry_skip(
                                            dt,
                                            sqqq_sym,
                                            "scaling skipped — not enough daily bars",
                                            verbose=verbose,
                                            force=True,
                                        )
                                    else:
                                        close_s = float(df_s["close"].iloc[-1])

                                        if (
                                            require_price_above_last_entry
                                            and last_entry_price is not None
                                            and close_s <= float(last_entry_price)
                                        ):
                                            _log_entry_skip(
                                                dt,
                                                sqqq_sym,
                                                "scaling skipped — SQQQ %.2f <= last entry %.2f"
                                                % (close_s, float(last_entry_price)),
                                                verbose=verbose,
                                                force=True,
                                            )
                                        else:
                                            quote_s = broker.get_latest_quote(sqqq_sym)
                                            _push_quote(sqqq_sym, quote_s)
                                            if quote_s and getattr(quote_s, "is_stale", None) and quote_s.is_stale(stale_quote_max_age):
                                                spread_pct_s = 0.15
                                            else:
                                                spread_pct_s = quote_s.spread_pct if quote_s else 0.15

                                            spread_cap_s = engine.market_quality._max_spread_for_symbol(sqqq_sym)
                                            if spread_pct_s is not None and spread_pct_s > spread_cap_s:
                                                _log_entry_skip(
                                                    dt,
                                                    sqqq_sym,
                                                    "scaling skipped — spread %.3f%% > cap %.3f%%"
                                                    % (spread_pct_s, spread_cap_s),
                                                    verbose=verbose,
                                                    force=True,
                                                )
                                            else:
                                                sizing_s = engine.sizer.size_position(
                                                    account_equity,
                                                    close_s,
                                                    bear_etf_stop_pct,
                                                    sqqq_sym,
                                                    current_positions,
                                                    sector_exposure_pct,
                                                    symbol_sector=None,
                                                    regime_size_multiplier=bear_inv_regime_mult,
                                                )

                                                if not sizing_s or sizing_s.shares <= 0:
                                                    rr = getattr(sizing_s, "reject_reason", None) if sizing_s else None
                                                    _log_entry_skip(
                                                        dt,
                                                        sqqq_sym,
                                                        "scaling skipped — sizing rejected (%s)" % (rr or "zero shares"),
                                                        verbose=verbose,
                                                        force=True,
                                                    )
                                                else:
                                                    step_cfg = steps[scale_count]
                                                    size_mult = float(step_cfg.get("size_multiplier") or 0.0)
                                                    add_sh = max(1, int(sizing_s.shares * size_mult))
                                                    add_notional = add_sh * close_s
                                                    room = max_bear_etf_notional - current_bear_etf_notional

                                                    if add_notional > room and close_s > 0:
                                                        add_sh = max(0, int(room / close_s))
                                                        add_notional = add_sh * close_s

                                                    buying_power_s = broker.get_buying_power()

                                                    if add_sh <= 0:
                                                        _log_entry_skip(
                                                            dt,
                                                            sqqq_sym,
                                                            "scaling skipped — clipped to 0 shares (room $%.0f)" % room,
                                                            verbose=verbose,
                                                            force=True,
                                                        )
                                                    elif add_notional > buying_power_s:
                                                        _log_entry_skip(
                                                            dt,
                                                            sqqq_sym,
                                                            "scaling skipped — buying power (need $%.0f, have $%.0f)"
                                                            % (add_notional, buying_power_s),
                                                            verbose=verbose,
                                                            force=True,
                                                        )
                                                    else:
                                                        buy_order_s = engine.execution.build_order(
                                                            sqqq_sym,
                                                            "buy",
                                                            add_sh,
                                                            quote_s.mid if quote_s else close_s,
                                                            spread_pct_s or 0.15,
                                                        )
                                                        if not buy_order_s:
                                                            _log_entry_skip(
                                                                dt,
                                                                sqqq_sym,
                                                                "scaling skipped — could not build order",
                                                                verbose=verbose,
                                                                force=True,
                                                            )
                                                        else:
                                                            broker.submit_order(buy_order_s)
                                                            current_bear_etf_notional += add_notional

                                                            prev = current_positions.get(sqqq_sym, {})
                                                            merge_add_tracked(
                                                                sqqq_sym,
                                                                add_sh,
                                                                close_s,
                                                                bear_etf_stop_pct,
                                                                user_id=_uid,
                                                                data_dir=_data_dir,
                                                                extras={
                                                                    "scale_count": scale_count + 1,
                                                                    "last_entry_price": close_s,
                                                                    "last_scale_ts": dt.isoformat(),
                                                                },
                                                            )
                                                            current_positions[sqqq_sym] = {
                                                                "notional": float(prev.get("notional", 0)) + add_notional,
                                                                "stop_pct": bear_etf_stop_pct,
                                                            }

                                                            print(
                                                                dt.strftime("%H:%M ET"),
                                                                sqqq_sym,
                                                                "BUY (scale step %d/%d, QQQ %.2f%% below MA50)"
                                                                % (scale_count + 1, len(steps), dist_pct),
                                                                add_sh,
                                                                "shares",
                                                            )
                                                            _persist_order_log(_uid, sqqq_sym, "buy", add_sh, close_s, "scale", mode=_mode)
                                                            _log_worker_event(_uid, "buy", "BUY %d shares (scale %d/%d, QQQ %.1f%% below MA50)" % (add_sh, scale_count + 1, len(steps), dist_pct), symbol=sqqq_sym)
                                except Exception as e:
                                    _log_entry_skip(
                                        dt,
                                        sqqq_sym,
                                        "scaling skipped — %s: %s" % (type(e).__name__, str(e)[:60]),
                                        verbose=verbose,
                                        force=True,
                                    )


                universe_cfg = config.get("universe", {})
                bearish_allow_longs = bool(universe_cfg.get("bearish_allow_trend_long_entries", False))
                bearish_max_norm = universe_cfg.get("bearish_max_normal_long_positions")
                bearish_max_norm = int(bearish_max_norm) if bearish_max_norm is not None and str(bearish_max_norm).strip() != "" else None
                bear_etf_set = bear_etf_universe_set

                def _normal_long_position_count() -> int:
                    n = 0
                    for p in positions:
                        sym = p.get("symbol", "")
                        if not sym or str(sym).upper() in bear_etf_set:
                            continue
                        if int(float(p.get("qty") or 0)) > 0:
                            n += 1
                    return n

                run_trend_long_entries = (not bearish_regime) or bearish_allow_longs
                if run_trend_long_entries and bearish_regime and bearish_max_norm is not None:
                    n_norm = _normal_long_position_count()
                    if n_norm >= bearish_max_norm:
                        run_trend_long_entries = False
                        if verbose:
                            print(
                                dt.strftime("%H:%M ET"),
                                "— bearish: skip trend long entries (%d normal longs >= cap %d)" % (n_norm, bearish_max_norm),
                            )

                # ===== RULES ENGINE ENTRY EVALUATION =====
                if symbols:
                    try:
                        from src.rules_engine.evaluator import evaluate_rule
                        import json as _json
                        _re_session = _SessionLocal()
                        for _re_sym in symbols:
                            if _re_sym in current_positions or _re_sym.upper() in open_order_symbols or _re_sym.upper() in tracked:
                                continue
                            _entry_rules = rule_repo.get_rules(_re_session, _uid, symbol=_re_sym, rule_type="entry", active_only=True)
                            if not _entry_rules:
                                continue
                            try:
                                _re_df = broker.get_bars(_re_sym, timeframe="1Day", limit=220)
                                if _re_df.empty or len(_re_df) < 5:
                                    continue
                            except Exception:
                                continue
                            for _rule in _entry_rules:
                                _tree = _json.loads(_rule.rule_tree)
                                _result = evaluate_rule(_re_df, _tree, bar_idx=-1)
                                if _result.fired:
                                    # Extract qty and price from actions
                                    _qty = 1
                                    _limit_price = None
                                    _stop_pct = None
                                    _tp_pct = None
                                    for _act in _result.actions:
                                        _aid = _act.get("action", "")
                                        _ap = _act.get("params", {})
                                        if _aid in ("enter_long", "enter_short", "limit_entry_at"):
                                            _qty = int(_ap.get("qty", 1) or 1)
                                        if _aid == "limit_entry_at":
                                            _limit_price = float(_ap.get("price", 0) or 0)
                                        if _aid == "set_stop_loss":
                                            _stop_pct = float(_ap.get("pct", 0) or 0)
                                        if _aid == "set_take_profit":
                                            _tp_pct = float(_ap.get("pct", 0) or 0)
                                    _close = float(_re_df["close"].iloc[-1])
                                    _order_cost = (_limit_price or _close) * _qty
                                    if _order_cost > available_cash:
                                        print(dt.strftime("%H:%M ET"), f"— RULE FIRED for {_re_sym} but insufficient buying power (${_order_cost:.0f} > ${available_cash:.0f})")
                                        gate_log_repo.record_gate(
                                            _re_session, user_id=_uid, gate="rule_entry",
                                            symbol=_re_sym, passed=False,
                                            reason=f"Rule '{_rule.name}' fired but insufficient buying power (${_order_cost:.0f} > ${available_cash:.0f})",
                                        )
                                        continue
                                    # Place the order
                                    try:
                                        _side = "sell" if any(a.get("action") == "enter_short" for a in _result.actions) else "buy"
                                        _tp_price = round((_limit_price or _close) * (1 + _tp_pct / 100), 2) if _tp_pct else None
                                        _sl_price = round((_limit_price or _close) * (1 - _stop_pct / 100), 2) if _stop_pct else None
                                        if _limit_price and _limit_price > 0:
                                            if _tp_price or _sl_price:
                                                broker.submit_bracket_order(
                                                    symbol=_re_sym, side=_side, qty=_qty,
                                                    limit_price=_limit_price,
                                                    take_profit_price=_tp_price,
                                                    stop_loss_price=_sl_price,
                                                    time_in_force="gtc",
                                                )
                                            else:
                                                from src.execution import OrderRequest, OrderType
                                                broker.submit_order(OrderRequest(
                                                    symbol=_re_sym, side=_side, quantity=_qty,
                                                    order_type=OrderType.LIMIT, limit_price=_limit_price,
                                                ))
                                        else:
                                            from src.execution import OrderRequest, OrderType
                                            broker.submit_order(OrderRequest(
                                                symbol=_re_sym, side=_side, quantity=_qty,
                                                order_type=OrderType.MARKET,
                                            ))
                                        _entry_price = _limit_price or _close
                                        add_tracked(_re_sym, _qty, _entry_price, _stop_pct or 1.5, side="long", user_id=_uid, data_dir=_data_dir)
                                        order_log_repo.record_order(
                                            _re_session, user_id=_uid, symbol=_re_sym, side=_side,
                                            qty=float(_qty), price=_entry_price,
                                            order_type="bracket" if (_tp_price or _sl_price) else ("limit" if _limit_price else "market"),
                                            source=f"rule:{_rule.name}", mode="paper" if broker.paper else "live",
                                        )
                                        gate_log_repo.record_gate(
                                            _re_session, user_id=_uid, gate="rule_entry",
                                            symbol=_re_sym, passed=True,
                                            reason=f"Rule '{_rule.name}' fired → {_side} {_qty} shares @ ${_entry_price:.2f}",
                                        )
                                        print(dt.strftime("%H:%M ET"), f"— RULE ENTRY: {_side} {_qty} {_re_sym} @ ${_entry_price:.2f} (rule: {_rule.name})")
                                        # Auto-deactivate entry rule after order placed (one-shot)
                                        rule_repo.update_rule(_re_session, _rule, is_active=False)
                                        print(dt.strftime("%H:%M ET"), f"— Rule #{_rule.id} deactivated (entry filled)")
                                        available_cash -= _order_cost
                                    except Exception as _oe:
                                        print(dt.strftime("%H:%M ET"), f"— RULE ORDER FAILED for {_re_sym}: {_oe}")
                                        gate_log_repo.record_gate(
                                            _re_session, user_id=_uid, gate="rule_entry",
                                            symbol=_re_sym, passed=False,
                                            reason=f"Rule '{_rule.name}' fired but order failed: {_oe}",
                                        )
                                    break  # first matching rule wins per symbol
                        _re_session.commit()
                        _re_session.close()
                    except Exception as _re_exc:
                        print(dt.strftime("%H:%M ET"), f"— Rules engine error: {_re_exc}")

                # ===== LEGACY ENTRY (disabled — rules engine handles entries) =====
                run_trend_long_entries = False

                if run_trend_long_entries:
                    for symbol in symbols:
                        if symbol in current_positions:
                            _log_entry_skip(
                                dt,
                                symbol,
                                "already in positions",
                                verbose=verbose,
                                force=False,
                            )
                            continue
                        if symbol.upper() in open_order_symbols:
                            _log_entry_skip(
                                dt,
                                symbol,
                                "open order pending",
                                verbose=verbose,
                                force=False,
                            )
                            continue
                        if symbol.upper() in tracked:
                            _log_entry_skip(
                                dt,
                                symbol,
                                "in tracked state (pending exit/sync)",
                                verbose=verbose,
                                force=False,
                            )
                            continue
                        try:
                            df = broker.get_bars(symbol, timeframe="1Day", limit=220)
                            min_hist = engine.strategy.min_history_bars_for_entry(symbol)
                            need = min_hist if str(symbol).upper() == "SQQQ" else max(200, ma_slow_period)
                            if df.empty or len(df) < need:
                                _log_entry_skip(
                                    dt,
                                    symbol,
                                    "not enough bars (got %d, need %d)" % (len(df) if not df.empty else 0, need),
                                    verbose=verbose,
                                    force=False,
                                )
                                continue
                            close = float(df["close"].iloc[-1])
                            # Trend prefilter: above MAs OR (news sentiment + volume spike) when enabled
                            trend_long_ok = True
                            skip_ma_check = str(symbol).upper() == "SQQQ"
                            skip_pullback_check = str(symbol).upper() == "SQQQ"
                            if not (skip_ma_check and skip_pullback_check):
                                if len(df) >= ma_fast_period:
                                    ma_fast = float(df["close"].rolling(ma_fast_period).mean().iloc[-1])
                                    if close <= ma_fast:
                                        trend_long_ok = False
                                if len(df) >= ma_slow_period:
                                    ma_slow = float(df["close"].rolling(ma_slow_period).mean().iloc[-1])
                                    if close <= ma_slow:
                                        trend_long_ok = False

                            sentiment_score = 0.0
                            vol_ratio = None
                            news_buy = False
                            if news_enabled and news_pipeline and news_rules:
                                sentiment_score = news_pipeline.sentiment_for_symbol(symbol)
                                vol_ratio = volume_spike_ratio(df, news_vol_lookback)
                                news_buy = news_rules.should_buy(sentiment_score, vol_ratio)

                            if not trend_long_ok and not news_buy:
                                _log_entry_skip(
                                    dt,
                                    symbol,
                                    "below MAs (trend prefilter) and no news+volume buy override",
                                    verbose=verbose,
                                    force=False,
                                )
                                continue

                            quote = broker.get_latest_quote(symbol)
                            _push_quote(symbol, quote)
                            if quote and getattr(quote, "is_stale", None) and quote.is_stale(stale_quote_max_age):
                                spread_pct = 0.15
                            else:
                                spread_pct = quote.spread_pct if quote else 0.15
                            spread_cap = engine.market_quality._max_spread_for_symbol(symbol)
                            if spread_pct is not None and spread_pct > spread_cap:
                                _log_entry_skip(
                                    dt,
                                    symbol,
                                    "spread %.3f%% > cap %.3f%%" % (spread_pct, spread_cap),
                                    verbose=verbose,
                                    force=False,
                                )
                                continue
                            est_buying_power_required = close * 1  # min 1 share
                            if est_buying_power_required > available_cash:
                                _log_entry_skip(
                                    dt,
                                    symbol,
                                    "insufficient buying power (1 share ~$%.2f > $%.2f)" % (est_buying_power_required, available_cash),
                                    verbose=verbose,
                                    force=False,
                                )
                                continue
                            atr = _atr(df["high"], df["low"], df["close"], 14)
                            atr_pct = (atr.iloc[-1] / df["close"].iloc[-1]) * 100 if len(atr) else None

                            min_vol_atr = engine.market_quality.min_volume_atr_ratio
                            vol_atr_g = max(min_vol_atr, float(vol_ratio)) if vol_ratio is not None else 1.5

                            decision = None
                            if trend_long_ok:
                                decision = engine.run_entry_gates(
                                    symbol=symbol,
                                    dt=dt,
                                    account_equity=account_equity,
                                    current_positions=current_positions,
                                    sector_exposure_pct=sector_exposure_pct,
                                    spread_pct=spread_pct,
                                    volume_atr_ratio=vol_atr_g,
                                    atr_pct=atr_pct,
                                    ohlcv_df=df,
                                    symbol_sector=None,
                                    log_strategy_context=verbose,
                                    regime_size_multiplier=regime_multiplier,
                                )
                            if (decision is None or not decision.allowed) and news_buy:
                                entry_override = EntrySignal(
                                    symbol=symbol,
                                    side="long",
                                    strength=float(sentiment_score),
                                    stop_pct=engine.strategy.stop_loss_pct,
                                    take_profit_pct=engine.strategy.take_profit_pct,
                                    time_bars_exit=engine.strategy.time_bars_exit,
                                    metadata={
                                        "source": "news_sentiment",
                                        "news_sentiment": sentiment_score,
                                        "volume_ratio": vol_ratio,
                                    },
                                )
                                decision = engine.run_entry_gates(
                                    symbol=symbol,
                                    dt=dt,
                                    account_equity=account_equity,
                                    current_positions=current_positions,
                                    sector_exposure_pct=sector_exposure_pct,
                                    spread_pct=spread_pct,
                                    volume_atr_ratio=vol_atr_g,
                                    atr_pct=atr_pct,
                                    ohlcv_df=df,
                                    symbol_sector=None,
                                    log_strategy_context=verbose,
                                    regime_size_multiplier=regime_multiplier,
                                    entry_override=entry_override,
                                )
                            if decision is not None and decision.allowed and decision.order_request:
                                notional = (decision.position_sizing.notional if decision.position_sizing else 0) or 0
                                buying_power = broker.get_buying_power()
                                if notional > buying_power:
                                    _log_entry_skip(
                                        dt,
                                        symbol,
                                        "insufficient buying power for sized order (need $%.0f, have $%.0f)"
                                        % (notional, buying_power),
                                        verbose=verbose,
                                        force=False,
                                    )
                                    continue
                                opts_cfg = config.get("options") or {}
                                src_meta = (decision.entry_signal.metadata or {}).get("source") if decision.entry_signal else None
                                route_src = "news_override" if src_meta == "news_sentiment" else "trend_long"
                                signal_trend = EntryRouteSignal(
                                    underlying=str(symbol).upper(),
                                    direction="bullish",
                                    source=route_src,
                                    stock_symbol=str(symbol).upper(),
                                )
                                trend_spot = None
                                uq_sym = broker.get_latest_quote(str(symbol).upper())
                                if uq_sym is not None and getattr(uq_sym, "mid", None):
                                    try:
                                        trend_spot = float(uq_sym.mid)
                                    except (TypeError, ValueError):
                                        trend_spot = None
                                options_handled = False
                                if opts_cfg.get("enabled") and should_use_options(config, signal_trend):
                                    sym_u = str(symbol).upper()
                                    chain_trend = _option_chain_for_underlying(broker, config, sym_u, dt)
                                    options_handled = route_to_options_executor(
                                        config,
                                        signal_trend,
                                        log_dt=dt,
                                        verbose=verbose,
                                        account_equity=account_equity,
                                        positions=positions,
                                        broker=broker,
                                        execution_manager=engine.execution,
                                        chain_candidates=chain_trend,
                                        underlying_spot=trend_spot,
                                    )
                                elif opts_cfg.get("enabled"):
                                    log_options_stock_path_if_ineligible(config, signal_trend, dt)
                                if not options_handled:

                                    def _trend_stock_execute() -> None:
                                        nonlocal current_positions
                                        order_t = broker.submit_order(decision.order_request)
                                        qty_bought = decision.position_sizing.shares if decision.position_sizing else 0
                                        entry_price = float(df["close"].iloc[-1]) if not df.empty else quote.mid
                                        stop_pct = decision.entry_signal.stop_pct if decision.entry_signal else 1.5
                                        add_tracked(symbol, qty_bought, entry_price, stop_pct, user_id=_uid, data_dir=_data_dir)
                                        src = (decision.entry_signal.metadata or {}).get("source") if decision.entry_signal else None
                                        buy_label = "news+vol spike" if src == "news_sentiment" else "trend"
                                        if src == "news_sentiment":
                                            print(
                                                dt.strftime("%H:%M ET"),
                                                symbol,
                                                "BUY",
                                                qty_bought,
                                                "shares (news+vol spike)",
                                                getattr(order_t, "id", ""),
                                            )
                                        else:
                                            print(
                                                dt.strftime("%H:%M ET"),
                                                symbol,
                                                "BUY",
                                                qty_bought,
                                                "shares",
                                                getattr(order_t, "id", ""),
                                            )
                                        _persist_order_log(_uid, symbol, "buy", qty_bought, entry_price, buy_label, mode=_mode, broker_order_id=str(getattr(order_t, "id", "")))
                                        _log_worker_event(_uid, "buy", "BUY %d shares (%s)" % (qty_bought, buy_label), symbol=symbol)
                                        current_positions[symbol] = {"notional": notional, "stop_pct": stop_pct}

                                    route_to_stock_executor(signal_trend, _trend_stock_execute)
                            else:
                                if decision is not None:
                                    _log_entry_skip(
                                        dt,
                                        symbol,
                                        decision.reason or "no entry signal",
                                        verbose=verbose,
                                        force=False,
                                    )
                        except Exception as e:
                            _log_entry_skip(
                                dt,
                                symbol,
                                "%s: %s" % (type(e).__name__, str(e)[:80]),
                                verbose=verbose,
                                force=False,
                            )
          except Exception as _user_exc:
            mark_error(worker_name, str(_user_exc), current_user_id=_uid)
            print(dt.strftime("%H:%M ET"), "[%s] ERROR: %s: %s — skipping to next user" % (
                _uid, type(_user_exc).__name__, str(_user_exc)[:120]))
            continue
        # end for _uctx in user_contexts

        if all_users_stopped:
            print(dt.strftime("%Y-%m-%d %H:%M ET"), "All users stopped for today.")
            break

        elapsed = int(now_sec - last_entry_check_time) // 60 if last_entry_check_time else 0
        next_entry_min = max(0, entry_interval_min - elapsed)
        if do_entry_check:
            print(dt.strftime("%H:%M ET"), "— exits every %d min, next entry check in %d min" % (exit_interval_min, entry_interval_min))
        else:
            print(dt.strftime("%H:%M ET"), "— next exit in %d min, entry check in %d min" % (exit_interval_min, next_entry_min))
        sys.stdout.flush()
        time.sleep(exit_interval_sec)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        mark_stopped("alpaca_loop")
        print("\nStopped by user.")
