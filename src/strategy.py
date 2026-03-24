"""
Entry/exit rules: mechanical strategy with defined exits before entries.

Default: Trend-following (price above 200D MA, pullback to 20D MA, volatility filter).
Exits: stop-loss (hard), take-profit (optional), time-based, kill-switch.
"""
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd
import numpy as np

from .candlestick import detect_any as candlestick_detect_any


class StrategyType(Enum):
    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"
    BREAKOUT = "breakout"


class ExitReason(Enum):
    STOP_LOSS = "stop_loss"
    TAKE_PROFIT = "take_profit"
    PARTIAL_TAKE_PROFIT = "partial_take_profit"
    TRAILING_STOP = "trailing_stop"
    TIME_BARS = "time_bars"
    KILL_SWITCH = "kill_switch"
    SIGNAL_EXIT = "signal_exit"
    NEWS_SENTIMENT = "news_sentiment"  # negative sentiment + weak trend (rule engine)


@dataclass
class EntrySignal:
    symbol: str
    side: str  # "long" | "short"
    strength: float
    stop_pct: float
    take_profit_pct: float | None
    time_bars_exit: int
    metadata: dict[str, Any]


@dataclass
class ExitSignal:
    symbol: str
    reason: ExitReason
    metadata: dict[str, Any]


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


class PlayerFocus:
    """Strategy style: institutional (follow big volume), retail (faster/short horizon), neutral."""
    NEUTRAL = "neutral"
    INSTITUTIONAL = "institutional"
    RETAIL = "retail"


class TrendFollowingStrategy:
    """
    Trend-following entries from config strategy.trend_following + strategy.retail (when player_focus=retail).
    Default yaml: retail + entry_mode=momentum → close > slow MA and > fast MA (e.g. 50/10), ATR% cap, optional candlestick filter.
    entry_mode=pullback → close > slow MA and near fast MA within tolerance.
    Exits: stop, partial + trailing, time, kill-switch (see strategy.exits); retail uses retail.time_bars_exit for time exit.
    Live loop passes "bars_held" as calendar days since entry — align time_bars_exit with that semantics for Alpaca loop.
    """

    def __init__(self, config: dict[str, Any]):
        strat = config.get("strategy", {})
        tf = strat.get("trend_following", {})
        exits = strat.get("exits", {})

        self.player_focus = (strat.get("player_focus") or PlayerFocus.NEUTRAL).strip().lower()
        inst = strat.get("institutional", {})
        self.institutional_min_volume_ratio = float(inst.get("min_volume_ratio_vs_avg", 1.2))
        retail = strat.get("retail", {})
        retail_ma_fast = int(retail.get("ma_fast", 10))
        retail_ma_slow = int(retail.get("ma_slow", 50))
        retail_time_bars = int(retail.get("time_bars_exit", 10))

        self.ma_fast = int(tf.get("ma_fast", 20))
        self.ma_slow = int(tf.get("ma_slow", 200))
        if self.player_focus == PlayerFocus.RETAIL:
            self.ma_fast = retail_ma_fast
            self.ma_slow = retail_ma_slow
        self.entry_mode = (tf.get("entry_mode") or "momentum").strip().lower()
        self.pullback_touch_ma_fast = bool(tf.get("pullback_touch_ma_fast", True))
        self.pullback_tolerance_pct = float(tf.get("pullback_tolerance_pct", 0.5))  # default 0.5%
        self.atr_period = int(tf.get("volatility_filter_atr_period", 14))
        self.max_atr_pct_for_entry = float(tf.get("max_atr_pct_for_entry", 2.0))

        self.stop_loss_pct = float(exits.get("stop_loss_pct", 1.0))
        self.cooldown_after_stop_minutes = float(exits.get("cooldown_after_stop_minutes", 30))
        self.require_new_breakout_after_stop = bool(exits.get("require_new_breakout_after_stop", False))
        self.cooldown_after_profit_minutes = float(exits.get("cooldown_after_profit_minutes", 10))
        self.require_price_above_exit_after_profit = bool(exits.get("require_price_above_exit_after_profit", True))
        tp = exits.get("take_profit_pct")
        self.take_profit_pct = float(tp) if tp is not None and tp != "" else None
        self.partial_take_profit_pct = float(exits.get("partial_take_profit_pct", 2.0))
        self.partial_exit_ratio = float(exits.get("partial_exit_ratio", 0.5))
        self.use_trailing_stop = bool(exits.get("use_trailing_stop", True))
        self.trailing_stop_pct = float(exits.get("trailing_stop_pct", 1.0))
        self.time_bars_exit = int(exits.get("time_bars_exit", 10))
        if self.player_focus == PlayerFocus.RETAIL:
            self.time_bars_exit = retail_time_bars
        ks = exits.get("kill_switch", {})
        self.kill_switch_max_spread_pct = float(ks.get("max_spread_pct", 0.25))
        # Kill-switch uses ATR% = (ATR/close)*100; config in percent (e.g. 3.0 = 3%)
        self.kill_switch_max_atr_pct = float(
            ks.get("max_atr_pct") or ks.get("max_atr_multiple", 3.0)
        )
        # 0 = disabled. Blocks partial, trail, time, kill-switch (not stop-loss). Live: wall-clock minutes.
        self.min_hold_minutes = float(exits.get("min_hold_minutes", 0) or 0)

        cf = strat.get("candlestick_filter", {})
        self.candlestick_enabled = bool(cf.get("enabled", False))
        self.candlestick_patterns = list(cf.get("patterns", []) or [])

    def _effective_minutes_held(self, minutes_held: float | None, bars_held: int) -> float:
        """Live: use wall-clock minutes when provided. Daily backtest: approximate bars × 1440."""
        if minutes_held is not None:
            return float(minutes_held)
        if self.min_hold_minutes <= 0:
            return float("inf")
        return float(bars_held) * 1440.0

    def _within_min_hold(self, minutes_held: float | None, bars_held: int) -> bool:
        if self.min_hold_minutes <= 0:
            return False
        return self._effective_minutes_held(minutes_held, bars_held) < self.min_hold_minutes

    def atr_pct(self, df: pd.DataFrame) -> pd.Series:
        if df.empty or len(df) < self.atr_period:
            return pd.Series(dtype=float)
        atr = _atr(df["high"], df["low"], df["close"], self.atr_period)
        return (atr / df["close"]) * 100

    def generate_entry(
        self,
        symbol: str,
        df: pd.DataFrame,
        spread_pct: float | None = None,
        atr_pct_now: float | None = None,
    ) -> EntrySignal | None:
        """Generate entry only when trend + pullback + volatility filter pass.
        atr_pct_now must be ATR% = (ATR/close)*100."""
        if df is None or len(df) < self.ma_slow:
            return None

        close = df["close"]
        atr_pct = self.atr_pct(df)
        if atr_pct.iloc[-1] > self.max_atr_pct_for_entry:
            return None

        ma_fast = close.rolling(self.ma_fast).mean()
        ma_slow = close.rolling(self.ma_slow).mean()

        price = close.iloc[-1]
        ma_f = ma_fast.iloc[-1]
        ma_s = ma_slow.iloc[-1]

        # Uptrend: price above slow MA
        if price <= ma_s or ma_s <= 0:
            return None
        # Entry mode: momentum = just above both MAs; pullback = price must be near fast MA
        if self.entry_mode == "momentum":
            if price <= ma_f or ma_f <= 0:
                return None
        else:
            tol = self.pullback_tolerance_pct / 100.0
            if self.pullback_touch_ma_fast and (ma_f <= 0 or abs(price - ma_f) / ma_f > tol):
                return None

        # Kill-switch: don't enter if spread/volatility already bad (atr_pct_now is ATR%)
        if spread_pct is not None and spread_pct > self.kill_switch_max_spread_pct:
            return None
        if atr_pct_now is not None and atr_pct_now > self.kill_switch_max_atr_pct:
            return None

        # Institutional: only enter when volume is elevated (proxy for institutional activity)
        if self.player_focus == PlayerFocus.INSTITUTIONAL and "volume" in df.columns and len(df) >= 20:
            vol = df["volume"]
            avg_vol = vol.rolling(20).mean().iloc[-1]
            if avg_vol and avg_vol > 0:
                volume_ratio = vol.iloc[-1] / avg_vol
                if volume_ratio < self.institutional_min_volume_ratio:
                    return None

        # Candlestick filter: only enter when one of the configured patterns appears on the last bar(s)
        if self.candlestick_enabled and not candlestick_detect_any(df, self.candlestick_patterns):
            return None

        return EntrySignal(
            symbol=symbol,
            side="long",
            strength=1.0,
            stop_pct=self.stop_loss_pct,
            take_profit_pct=self.partial_take_profit_pct or self.take_profit_pct,
            time_bars_exit=self.time_bars_exit,
            metadata={"ma_fast": ma_f, "ma_slow": ma_s, "atr_pct": atr_pct.iloc[-1]},
        )

    def check_exit(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        bars_held: int,
        spread_pct: float | None = None,
        atr_pct: float | None = None,
        *,
        partial_taken: bool = False,
        trail_high: float | None = None,
        current_qty: int = 0,
        minutes_held: float | None = None,
    ) -> ExitSignal | None:
        """Check for stop, partial at 2%, trailing stop on remainder, time, or kill-switch.
        atr_pct must be ATR% = (ATR/close)*100.
        min_hold_minutes: stop-loss always allowed; other exits deferred until hold elapsed (live: wall clock)."""
        ret_pct = (current_price - entry_price) / entry_price * 100

        if ret_pct <= -self.stop_loss_pct:
            return ExitSignal(symbol=symbol, reason=ExitReason.STOP_LOSS, metadata={"ret_pct": ret_pct})
        if self._within_min_hold(minutes_held, bars_held):
            return None

        if bars_held >= self.time_bars_exit:
            return ExitSignal(symbol=symbol, reason=ExitReason.TIME_BARS, metadata={"bars_held": bars_held})
        if spread_pct is not None and spread_pct > self.kill_switch_max_spread_pct:
            return ExitSignal(symbol=symbol, reason=ExitReason.KILL_SWITCH, metadata={"spread_pct": spread_pct})
        if atr_pct is not None and atr_pct > self.kill_switch_max_atr_pct:
            return ExitSignal(symbol=symbol, reason=ExitReason.KILL_SWITCH, metadata={"atr_pct": atr_pct})

        if not partial_taken and ret_pct >= self.partial_take_profit_pct and current_qty > 0:
            qty_to_sell = max(1, int(current_qty * self.partial_exit_ratio))
            if qty_to_sell < current_qty:
                return ExitSignal(
                    symbol=symbol,
                    reason=ExitReason.PARTIAL_TAKE_PROFIT,
                    metadata={"ret_pct": ret_pct, "qty_to_sell": qty_to_sell},
                )
        if self.use_trailing_stop and partial_taken and current_qty > 0 and trail_high is not None:
            high = max(trail_high, current_price)
            threshold = high * (1 - self.trailing_stop_pct / 100.0)
            if current_price <= threshold:
                return ExitSignal(
                    symbol=symbol,
                    reason=ExitReason.TRAILING_STOP,
                    metadata={"ret_pct": ret_pct, "trail_high": high},
                )
        return None

    def check_exit_short(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        bars_held: int,
        stop_pct: float,
        take_profit_pct: float,
        time_bars_exit: int,
        spread_pct: float | None = None,
        atr_pct: float | None = None,
        *,
        minutes_held: float | None = None,
    ) -> ExitSignal | None:
        """Exit rules for short: stop when price rises, take profit when price falls. atr_pct for kill-switch."""
        ret_pct = (entry_price - current_price) / entry_price * 100  # profit when price falls
        if ret_pct <= -stop_pct:
            return ExitSignal(symbol=symbol, reason=ExitReason.STOP_LOSS, metadata={"ret_pct": ret_pct})
        if self._within_min_hold(minutes_held, bars_held):
            return None
        if bars_held >= time_bars_exit:
            return ExitSignal(symbol=symbol, reason=ExitReason.TIME_BARS, metadata={"bars_held": bars_held})
        if spread_pct is not None and spread_pct > self.kill_switch_max_spread_pct:
            return ExitSignal(symbol=symbol, reason=ExitReason.KILL_SWITCH, metadata={"spread_pct": spread_pct})
        if atr_pct is not None and atr_pct > self.kill_switch_max_atr_pct:
            return ExitSignal(symbol=symbol, reason=ExitReason.KILL_SWITCH, metadata={"atr_pct": atr_pct})
        if ret_pct >= take_profit_pct:
            return ExitSignal(symbol=symbol, reason=ExitReason.TAKE_PROFIT, metadata={"ret_pct": ret_pct})
        return None
