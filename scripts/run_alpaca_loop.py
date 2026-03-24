#!/usr/bin/env python3
"""
Run the trading engine in a loop until market close (no user interaction).

Checks for entry signals every N minutes during regular session; stops when
market closes or daily loss limit / safe mode is hit.
CLI: --live or --paper to override config.
"""
import argparse
import logging
import sys
import time
from pathlib import Path
from datetime import datetime
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
    remove as remove_tracked,
    update as update_tracked,
    bars_held,
    minutes_held as holding_minutes,
)
from src.strategy import ExitReason, EntrySignal
from src.market_regime import MarketRegimeScorer
from src.news_sentiment import NewsSentimentPipeline, NewsRuleEngine, volume_spike_ratio


def main() -> None:
    parser = argparse.ArgumentParser(description="Run trading loop until market close")
    parser.add_argument("--live", action="store_true", help="Use live account (real money)")
    parser.add_argument("--paper", action="store_true", help="Use paper account (default)")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print why each symbol is skipped (no trade)")
    args = parser.parse_args()
    if args.live and args.paper:
        parser.error("Use only one of --live or --paper")
    verbose = getattr(args, "verbose", False)
    if verbose:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    config_path = PROJECT_ROOT / "config" / "default.yaml"
    config = load_config(config_path)
    if args.live:
        config.setdefault("broker", {})["paper"] = False
        # News + FinBERT disabled for live trading (bandwidth / latency / signal risk)
        config.setdefault("news_sentiment", {})["enabled"] = False
    elif args.paper:
        config.setdefault("broker", {})["paper"] = True

    broker_cfg = config.get("broker", {})
    if broker_cfg.get("firm") != "alpaca":
        print("Config broker.firm is not 'alpaca'. Exiting.")
        sys.exit(1)

    broker = AlpacaBroker(config)
    mode = "PAPER" if broker.paper else "LIVE (real money)"
    print("Broker mode:", mode)
    engine = TradingEngine(config=config)
    calendar = MarketCalendar(config)
    regime_scorer = MarketRegimeScorer(config)
    et = pytz.timezone("America/New_York")
    exit_interval_min = int(broker_cfg.get("exit_check_interval_minutes") or broker_cfg.get("check_interval_minutes", 5))
    entry_interval_min = int(broker_cfg.get("entry_check_interval_minutes", 10))
    exit_interval_sec = exit_interval_min * 60
    entry_interval_sec = entry_interval_min * 60
    tracker_path = PROJECT_ROOT / "data" / "positions_tracked.json"
    last_entry_check_time = None  # entry check every entry_interval_min (e.g. 10 min)
    mq_cfg = config.get("market_quality", {})
    stale_quote_max_age = float(mq_cfg.get("stale_quote_max_age_seconds", 60))
    regime_pct_above_50d_ma = float(config.get("universe", {}).get("regime_min_pct_above_50d_ma", 0.30))
    ns_cfg = config.get("news_sentiment") or {}
    news_enabled = bool(ns_cfg.get("enabled", False))
    news_pipeline = NewsSentimentPipeline(config) if news_enabled else None
    news_rules = NewsRuleEngine.from_config(config) if news_enabled else None
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

        print(dt.strftime("%H:%M ET"), "— in session, fetching account...")
        sys.stdout.flush()
        account_equity = broker.get_equity()
        engine.update_equity(account_equity)
        engine.state.pdt.equity = account_equity

        # Stop if portfolio risk says no more trading today
        can_trade, reason = engine.portfolio_risk.can_trade(
            engine.state.portfolio_risk, account_equity, "SPY", dt.date()
        )
        if not can_trade:
            print(dt.strftime("%Y-%m-%d %H:%M ET"), reason, "- Stopping for today.")
            break

        positions = broker.get_positions()
        tracked = load_tracked(tracker_path)
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
                add_tracked(tracker_path, sym, qty, entry, 1.5, side=side)
        tracked = load_tracked(tracker_path)
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
                remove_tracked(tracker_path, symbol)
                continue
            if not any(p["symbol"] == symbol for p in positions):
                remove_tracked(tracker_path, symbol)
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
                                    remove_tracked(tracker_path, symbol)
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
                        remove_tracked(tracker_path, symbol)
                    continue
                if partial_taken:
                    new_high = max(trail_high_f or entry_price, quote.mid)
                    update_tracked(tracker_path, symbol, trail_high=new_high)
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
                            remove_tracked(tracker_path, symbol)
                        else:
                            update_tracked(tracker_path, symbol, qty=remaining, partial_taken=True, trail_high=quote.mid)
                    else:
                        sell_order = engine.execution.build_order(symbol, "sell", qty, quote.mid, quote.spread_pct)
                        if sell_order:
                            broker.submit_order(sell_order)
                            print(dt.strftime("%H:%M ET"), symbol, "SELL", qty, "shares —", exit_signal.reason.value)
                        if exit_signal.reason == ExitReason.STOP_LOSS:
                            engine.record_stop_loss(symbol, dt, entry_price=entry_price)
                        elif exit_signal.reason in (ExitReason.TAKE_PROFIT, ExitReason.TRAILING_STOP):
                            engine.record_profit_exit(symbol, dt, quote.mid)
                        remove_tracked(tracker_path, symbol)
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
            if verbose:
                print(dt.strftime("%H:%M ET"), "Entry check: equity $%.0f, positions %d" % (account_equity, len(positions)))
            open_orders = broker.get_open_orders()
            open_order_symbols = {o.get("symbol", "").upper() for o in (open_orders or []) if o.get("symbol")}
            available_cash = broker.get_buying_power()
            ma_fast_period = engine.strategy.ma_fast
            ma_slow_period = engine.strategy.ma_slow

            # Bear ETFs: long only when bearish + breakdown (e.g. QQQ below 50D MA)
            bear_etfs_cfg = config.get("universe", {}).get("bear_etfs", {})
            bear_etf_all_raw = list(bear_etfs_cfg.get("symbols") or [])
            bear_etf_universe_set = {str(s).upper() for s in bear_etf_all_raw}
            bear_etf_symbols = list(bear_etf_all_raw)
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

            if bearish_regime and breakdown_detected and bear_etf_symbols:
                # Inverse entries (often SQQQ-only when breakdown ref is QQQ); exposure counts all bear_etfs.symbols
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
                    if current_bear_etf >= max_bear_etf_positions:
                        break
                    if symbol in current_positions or symbol.upper() in open_order_symbols or symbol.upper() in tracked:
                        continue
                    try:
                        df = broker.get_bars(symbol, timeframe="1Day", limit=60)
                        if df.empty or len(df) < 20:
                            continue
                        close = float(df["close"].iloc[-1])
                        quote = broker.get_latest_quote(symbol)
                        if quote and getattr(quote, "is_stale", None) and quote.is_stale(stale_quote_max_age):
                            spread_pct = 0.15
                        else:
                            spread_pct = quote.spread_pct if quote else 0.15
                        spread_cap = engine.market_quality._max_spread_for_symbol(symbol)
                        if spread_pct is not None and spread_pct > spread_cap:
                            continue
                        if close * 1 > available_cash:
                            continue
                        sizing = engine.sizer.size_position(
                            account_equity,
                            close,
                            bear_etf_stop_pct,
                            symbol,
                            current_positions,
                            sector_exposure_pct,
                            symbol_sector=None,
                        )
                        if not sizing or sizing.shares <= 0:
                            continue
                        if current_bear_etf_notional + sizing.notional > max_bear_etf_notional:
                            if verbose:
                                print("  %s: skip — inverse ETF exposure cap (%.0f%% equity)" % (symbol, max_bear_etf_pct))
                            continue
                        buy_order = engine.execution.build_order(symbol, "buy", sizing.shares, quote.mid if quote else close, spread_pct or 0.15)
                        if not buy_order:
                            continue
                        broker.submit_order(buy_order)
                        current_bear_etf_notional += sizing.notional
                        add_tracked(tracker_path, symbol, sizing.shares, close, bear_etf_stop_pct, side="long")
                        current_bear_etf += 1
                        current_positions[symbol] = {"notional": sizing.notional, "stop_pct": bear_etf_stop_pct}
                        print(dt.strftime("%H:%M ET"), symbol, "BUY (bear ETF, breakdown)", sizing.shares, "shares")
                    except Exception as e:
                        if verbose:
                            print("  %s: bear ETF skip —" % symbol, type(e).__name__, str(e)[:60])
                        continue

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
                        if verbose:
                            print("  %s: skip — already_have_position" % symbol)
                        continue
                    if symbol.upper() in open_order_symbols:
                        if verbose:
                            print("  %s: skip — open_order_exists" % symbol)
                        continue
                    if symbol.upper() in tracked:
                        if verbose:
                            print("  %s: skip — in_tracked_state" % symbol)
                        continue
                    try:
                        df = broker.get_bars(symbol, timeframe="1Day", limit=220)
                        if df.empty or len(df) < max(200, ma_slow_period):
                            if verbose:
                                print("  %s: skip — not_enough_bars (got %d)" % (symbol, len(df) if not df.empty else 0))
                            continue
                        close = float(df["close"].iloc[-1])
                        # Trend prefilter: above MAs OR (news sentiment + volume spike) when enabled
                        trend_long_ok = True
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
                            if verbose:
                                print("  %s: skip — below_MA and not (positive_news+vol_spike)" % symbol)
                            continue

                        quote = broker.get_latest_quote(symbol)
                        if quote and getattr(quote, "is_stale", None) and quote.is_stale(stale_quote_max_age):
                            spread_pct = 0.15
                        else:
                            spread_pct = quote.spread_pct if quote else 0.15
                        spread_cap = engine.market_quality._max_spread_for_symbol(symbol)
                        if spread_pct is not None and spread_pct > spread_cap:
                            if verbose:
                                print("  %s: skip — spread_too_high (%.2f%% > %.2f%%)" % (symbol, spread_pct, spread_cap))
                            continue
                        est_buying_power_required = close * 1  # min 1 share
                        if est_buying_power_required > available_cash:
                            if verbose:
                                print("  %s: skip — insufficient_buying_power (est $%.0f > $%.0f)" % (symbol, est_buying_power_required, available_cash))
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
                                print(dt.strftime("%H:%M ET"), symbol, "skip — insufficient buying power (need $%.0f, have $%.0f)" % (notional, buying_power))
                                continue
                            order = broker.submit_order(decision.order_request)
                            qty_bought = decision.position_sizing.shares if decision.position_sizing else 0
                            entry_price = float(df["close"].iloc[-1]) if not df.empty else quote.mid
                            stop_pct = decision.entry_signal.stop_pct if decision.entry_signal else 1.5
                            add_tracked(tracker_path, symbol, qty_bought, entry_price, stop_pct)
                            src = (decision.entry_signal.metadata or {}).get("source") if decision.entry_signal else None
                            if src == "news_sentiment":
                                print(dt.strftime("%H:%M ET"), symbol, "BUY", qty_bought, "shares (news+vol spike)", getattr(order, "id", ""))
                            else:
                                print(dt.strftime("%H:%M ET"), symbol, "BUY", qty_bought, "shares", getattr(order, "id", ""))
                            current_positions[symbol] = {"notional": notional, "stop_pct": stop_pct}
                        else:
                            if verbose and decision is not None:
                                print("  %s: %s" % (symbol, decision.reason or "no entry signal"))
                    except Exception as e:
                        print(dt.strftime("%H:%M ET"), symbol, "skip —", type(e).__name__, str(e)[:80])
                        continue

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
