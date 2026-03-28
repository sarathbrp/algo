#!/usr/bin/env python3
"""
Run the trading engine in a loop until market close (no user interaction).

Checks for entry signals every N minutes during regular session; stops when
market closes or daily loss limit / safe mode is hit.

Multi-user support: when ``config/users.yaml`` exists, iterates over all
configured users each cycle.  Each user has their own broker, engine,
tracker, and risk state.  Errors in one user never crash others.

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
from src.loop_helpers import (
    UserLoopContext,
    init_user_contexts,
    log_startup_summary,
)


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


def _log_entry_skip(
    dt: datetime,
    symbol: str,
    reason: str,
    *,
    verbose: bool,
    force: bool = False,
) -> None:
    """Print `SYMBOL skip — reason`. If force, always print; else print when verbose or symbol is SQQQ."""
    sym_u = str(symbol).upper()
    if force or verbose or sym_u == "SQQQ":
        print(dt.strftime("%H:%M ET"), f"{sym_u} skip — {reason}")


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
    user_manager = UserManager(config, users_path=users_path)

    if user_manager.multi_user and (args.live or args.paper):
        print("WARNING: --live/--paper flags are ignored in multi-user mode "
              "(each user has their own paper flag in users.yaml)")

    user_contexts = init_user_contexts(
        user_manager,
        project_root=PROJECT_ROOT,
        user_filter=args.user,
    )
    if not user_contexts:
        print("No user contexts loaded. Exiting.")
        sys.exit(1)

    log_startup_summary(user_contexts)

    # In single-user fallback, apply --live/--paper to the default user's config
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
    exit_interval_min = int(broker_cfg.get("exit_check_interval_minutes") or broker_cfg.get("check_interval_minutes", 5))
    entry_interval_min = int(broker_cfg.get("entry_check_interval_minutes", 10))
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
                break
            print(dt.strftime("%Y-%m-%d %H:%M ET"), "Outside regular hours. Sleeping until next check.")
            time.sleep(exit_interval_sec)
            continue

        # ---- Per-user trading pass ----
        all_users_stopped = True
        for _uctx in user_contexts:
          try:
            _uid = _uctx.user_id
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
        symbols = config.get("universe", {}).get("symbols", ["SPY"])
        paused = {p.upper() for p in config.get("universe", {}).get("paused_symbols", [])}
        symbols = [s for s in symbols if s.upper() not in paused]

        # Heartbeat: so you see the loop is running even when no trades
        print(dt.strftime("%H:%M ET"), "— equity $%.0f, checking %d symbols..." % (account_equity, len(symbols)))
        sys.stdout.flush()

        # ----- Exit rules for each tracked position (long only; cover any legacy shorts) -----
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
            try:
                quote = broker.get_latest_quote(symbol)
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
                        if exit_signal.reason == ExitReason.STOP_LOSS:
                            engine.record_stop_loss(symbol, dt, entry_price=entry_price)
                        elif exit_signal.reason in (ExitReason.TAKE_PROFIT, ExitReason.TRAILING_STOP):
                            engine.record_profit_exit(symbol, dt, quote.mid)
                        remove_tracked(symbol, user_id=_uid, data_dir=_data_dir)
            except Exception as e:
                print(dt.strftime("%H:%M ET"), symbol, "exit check skip —", type(e).__name__, str(e)[:60])
                continue

        # Entry check every entry_interval_min (e.g. 10 min)
        now_sec = time.time()
        do_entry_check = last_entry_check_time is None or (now_sec - last_entry_check_time) >= entry_interval_sec
        if do_entry_check:
            last_entry_check_time = now_sec

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

                # SQQQ controlled scaling (QQQ vs 50D MA only; step index + scale_count)
                scaling_cfg = bear_etfs_cfg.get("controlled_scaling") or {}
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
                        continue

          except Exception as _user_exc:
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
        print("\nStopped by user.")
