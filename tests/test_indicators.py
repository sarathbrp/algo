"""Tests for src/rules_engine/indicators.py — pure computation, no credentials."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.rules_engine.indicators import compute_indicator, sma, ema, rsi, atr, atr_pct, vwap, price, volume, volume_avg_ratio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(prices: list[float] | np.ndarray, volumes: list[float] | np.ndarray | None = None) -> pd.DataFrame:
    """Build an OHLCV DataFrame from close prices."""
    prices = np.asarray(prices, dtype=float)
    n = len(prices)
    if volumes is None:
        volumes = np.full(n, 1000.0)
    else:
        volumes = np.asarray(volumes, dtype=float)
    return pd.DataFrame({
        "open": prices,
        "high": prices * 1.01,
        "low": prices * 0.99,
        "close": prices,
        "volume": volumes,
    })


# ---------------------------------------------------------------------------
# SMA
# ---------------------------------------------------------------------------

class TestSMA:
    def test_sma_known_values(self):
        df = _make_df([10, 20, 30, 40, 50])
        result = compute_indicator(df, "sma", {"period": 3})
        # Last value = mean(30, 40, 50) = 40
        assert result.iloc[-1] == pytest.approx(40.0)
        # Second-to-last = mean(20, 30, 40) = 30
        assert result.iloc[-2] == pytest.approx(30.0)

    def test_sma_first_bars_nan(self):
        df = _make_df([10, 20, 30, 40, 50])
        result = compute_indicator(df, "sma", {"period": 3})
        assert pd.isna(result.iloc[0])
        assert pd.isna(result.iloc[1])
        assert not pd.isna(result.iloc[2])

    def test_sma_custom_field(self):
        df = _make_df([100, 200, 300])
        result = sma(df, period=2, field="high")
        # high = close * 1.01
        expected = (200 * 1.01 + 300 * 1.01) / 2
        assert result.iloc[-1] == pytest.approx(expected)


# ---------------------------------------------------------------------------
# EMA
# ---------------------------------------------------------------------------

class TestEMA:
    def test_ema_first_value_equals_first_data_point(self):
        df = _make_df([50, 60, 70, 80, 90])
        result = compute_indicator(df, "ema", {"period": 3})
        # With adjust=False the first EMA value equals the first close
        assert result.iloc[0] == pytest.approx(50.0)

    def test_ema_trends_toward_recent(self):
        """EMA on an uptrend should be > SMA (closer to recent values)."""
        df = _make_df(list(range(1, 51)))
        ema_vals = compute_indicator(df, "ema", {"period": 10})
        sma_vals = compute_indicator(df, "sma", {"period": 10})
        # On a monotonic uptrend, EMA > SMA for all non-NaN bars
        valid = sma_vals.dropna().index
        assert (ema_vals.loc[valid] >= sma_vals.loc[valid]).all()

    def test_ema_converges_on_constant(self):
        """EMA on a constant series should equal that constant."""
        df = _make_df([42.0] * 20)
        result = ema(df, period=10)
        assert result.iloc[-1] == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# RSI
# ---------------------------------------------------------------------------

class TestRSI:
    def test_rsi_bounded(self):
        np.random.seed(42)
        prices = np.cumsum(np.random.randn(100)) + 100
        df = _make_df(prices)
        result = compute_indicator(df, "rsi", {"period": 14})
        valid = result.dropna()
        assert (valid >= 0).all()
        assert (valid <= 100).all()

    def test_rsi_monotonic_up(self):
        prices = list(range(50, 100))
        df = _make_df(prices)
        result = rsi(df, period=14)
        # After warmup, RSI should be 100 for strictly increasing
        assert result.iloc[-1] == pytest.approx(100.0)

    def test_rsi_monotonic_down(self):
        prices = list(range(100, 50, -1))
        df = _make_df(prices)
        result = rsi(df, period=14)
        # After warmup, RSI should be ~0 for strictly decreasing
        assert result.iloc[-1] == pytest.approx(0.0, abs=0.5)

    def test_rsi_warmup_nan(self):
        df = _make_df(list(range(30)))
        result = rsi(df, period=14)
        assert result.iloc[:14].isna().all()
        assert result.iloc[14:].notna().all()


# ---------------------------------------------------------------------------
# ATR
# ---------------------------------------------------------------------------

class TestATR:
    def test_atr_known_values(self):
        """ATR on a constant-spread DataFrame with no gaps."""
        prices = np.array([100.0] * 10)
        df = _make_df(prices)
        # high = 101, low = 99 → true range = 2 each bar (no gap)
        result = atr(df, period=3)
        # After warmup, ATR = rolling 3-bar mean of true range = 2.0
        assert result.iloc[-1] == pytest.approx(2.0)

    def test_atr_first_bars_nan(self):
        df = _make_df([100.0] * 10)
        result = atr(df, period=5)
        assert result.iloc[:4].isna().all()


# ---------------------------------------------------------------------------
# ATR Pct
# ---------------------------------------------------------------------------

class TestATRPct:
    def test_atr_pct(self):
        prices = np.array([100.0] * 10)
        df = _make_df(prices)
        result = atr_pct(df, period=3)
        # ATR = 2.0, close = 100 → pct = 2.0%
        assert result.iloc[-1] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# VWAP
# ---------------------------------------------------------------------------

class TestVWAP:
    def test_vwap_uniform_volume_equals_typical_price_sma(self):
        """With uniform volume, VWAP is a cumulative average of typical price."""
        prices = np.array([10.0, 20.0, 30.0, 40.0, 50.0])
        df = _make_df(prices, volumes=[1000] * 5)
        result = vwap(df)
        # Typical price = (high+low+close)/3 = (c*1.01 + c*0.99 + c)/3 = c
        # With uniform volume, VWAP at bar i = cumulative mean of typical prices
        cum_mean = pd.Series(prices).expanding().mean()
        for i in range(len(prices)):
            assert result.iloc[i] == pytest.approx(cum_mean.iloc[i], rel=1e-6)

    def test_vwap_single_bar(self):
        df = _make_df([100.0])
        result = vwap(df)
        typical = (100 * 1.01 + 100 * 0.99 + 100) / 3
        assert result.iloc[0] == pytest.approx(typical)


# ---------------------------------------------------------------------------
# Price and Volume
# ---------------------------------------------------------------------------

class TestPriceAndVolume:
    def test_price_default_close(self):
        df = _make_df([42.0, 43.0])
        result = price(df)
        assert list(result) == [42.0, 43.0]

    def test_price_field_high(self):
        df = _make_df([100.0])
        result = compute_indicator(df, "price", {"field": "high"})
        assert result.iloc[0] == pytest.approx(101.0)

    def test_volume_returns_raw(self):
        df = _make_df([10, 20], volumes=[500, 700])
        result = volume(df)
        assert list(result) == [500.0, 700.0]


# ---------------------------------------------------------------------------
# Volume Avg Ratio
# ---------------------------------------------------------------------------

class TestVolumeAvgRatio:
    def test_ratio_above_one_for_spike(self):
        """Last bar has 5x volume → ratio > 1."""
        vols = [1000] * 20 + [5000]
        df = _make_df([100.0] * 21, volumes=vols)
        result = volume_avg_ratio(df, period=20)
        assert result.iloc[-1] > 1.0
        # Rolling window includes the spike bar itself:
        # mean of 19*1000 + 5000 over 20 = 24000/20 = 1200, ratio = 5000/1200
        assert result.iloc[-1] == pytest.approx(5000.0 / 1200.0, rel=1e-6)

    def test_ratio_one_for_constant_volume(self):
        df = _make_df([100.0] * 25, volumes=[1000] * 25)
        result = volume_avg_ratio(df, period=20)
        assert result.iloc[-1] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Dispatch / error handling
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_unknown_indicator_raises(self):
        df = _make_df([100.0])
        with pytest.raises(ValueError, match="Unknown indicator"):
            compute_indicator(df, "nonexistent_indicator", {})

    def test_short_dataframe_returns_all_nan(self):
        """Fewer bars than period → all NaN for SMA."""
        df = _make_df([10, 20])
        result = compute_indicator(df, "sma", {"period": 5})
        assert result.isna().all()
