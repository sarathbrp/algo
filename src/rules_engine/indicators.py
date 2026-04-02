"""Pure indicator functions: DataFrame in → Series out.

Every function takes a pandas DataFrame with columns [open, high, low, close, volume]
and returns a pd.Series aligned with the DataFrame index. Warmup bars are NaN.
"""
from __future__ import annotations

import pandas as pd

from src.strategy import _atr as _strategy_atr


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_INDICATOR_FNS: dict[str, callable] = {}


def _register(name: str):
    def decorator(fn):
        _INDICATOR_FNS[name] = fn
        return fn
    return decorator


def compute_indicator(df: pd.DataFrame, indicator: str, params: dict | None = None) -> pd.Series:
    """Compute an indicator by name. Raises ValueError for unknown indicators."""
    if indicator not in _INDICATOR_FNS:
        raise ValueError(f"Unknown indicator: {indicator!r}. Available: {sorted(_INDICATOR_FNS)}")
    return _INDICATOR_FNS[indicator](df, **(params or {}))


# ---------------------------------------------------------------------------
# Indicator implementations
# ---------------------------------------------------------------------------

@_register("sma")
def sma(df: pd.DataFrame, period: int = 20, field: str = "close") -> pd.Series:
    """Simple Moving Average."""
    return df[field].rolling(period).mean()


@_register("ema")
def ema(df: pd.DataFrame, period: int = 20, field: str = "close") -> pd.Series:
    """Exponential Moving Average."""
    return df[field].ewm(span=period, adjust=False).mean()


@_register("rsi")
def rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Relative Strength Index (Wilder's smoothing)."""
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))
    # First `period` bars don't have enough data
    result.iloc[:period] = float("nan")
    return result


@_register("atr")
def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — delegates to existing strategy implementation."""
    return _strategy_atr(df["high"], df["low"], df["close"], period)


@_register("atr_pct")
def atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """ATR as a percentage of close price."""
    return (atr(df, period) / df["close"]) * 100


@_register("vwap")
def vwap(df: pd.DataFrame) -> pd.Series:
    """Cumulative Volume-Weighted Average Price."""
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_tp_vol = (typical_price * df["volume"]).cumsum()
    cum_vol = df["volume"].cumsum()
    return cum_tp_vol / cum_vol


@_register("price")
def price(df: pd.DataFrame, field: str = "close") -> pd.Series:
    """Raw price field (close, open, high, or low)."""
    return df[field]


@_register("volume")
def volume(df: pd.DataFrame) -> pd.Series:
    """Raw volume."""
    return df["volume"]


@_register("volume_avg_ratio")
def volume_avg_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Current volume divided by N-period average volume."""
    avg = df["volume"].rolling(period).mean()
    return df["volume"] / avg


@_register("gap_pct")
def gap_pct(df: pd.DataFrame) -> pd.Series:
    """Today's open vs yesterday's close, as a percentage. Positive = gap up, negative = gap down."""
    prev_close = df["close"].shift(1)
    return ((df["open"] - prev_close) / prev_close) * 100


@_register("daily_change_pct")
def daily_change_pct(df: pd.DataFrame) -> pd.Series:
    """Day-over-day close change as percentage."""
    return df["close"].pct_change() * 100
