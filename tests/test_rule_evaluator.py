"""Tests for src/rules_engine/evaluator.py — rule tree evaluation, no credentials."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.rules_engine.evaluator import (
    EvaluationResult,
    evaluate_rule,
    compute_warmup_period,
    _cache_key,
    _get_indicator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(prices: list[float] | np.ndarray, volumes: list[float] | np.ndarray | None = None) -> pd.DataFrame:
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


def _simple_rule(groups, actions=None):
    """Shorthand to build a rule tree dict."""
    return {
        "groups": groups,
        "actions": actions or [{"action": "enter_long", "params": {}}],
    }


# ---------------------------------------------------------------------------
# crosses_above: EMA(10) crosses above EMA(30)
# ---------------------------------------------------------------------------

class TestCrossesAbove:
    def test_ema_cross_fires_at_known_bar(self):
        """Build prices where EMA(10) crosses above EMA(30) at a known bar."""
        # Start high so EMA(30) remembers high values, drop to create
        # EMA(10) < EMA(30), then ramp up so EMA(10) crosses above EMA(30).
        prices = [100.0] * 40 + [60.0] * 20 + [60 + i * 5 for i in range(1, 41)]
        df = _make_df(prices)

        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "ema",
                "params": {"period": 10},
                "comparator": "crosses_above",
                "value": {"indicator": "ema", "params": {"period": 30}},
            }],
        }])

        # Find the cross bar
        from src.rules_engine.indicators import compute_indicator
        ema10 = compute_indicator(df, "ema", {"period": 10})
        ema30 = compute_indicator(df, "ema", {"period": 30})

        cross_bar = None
        for i in range(1, len(df)):
            if ema10.iloc[i - 1] <= ema30.iloc[i - 1] and ema10.iloc[i] > ema30.iloc[i]:
                cross_bar = i
                break

        assert cross_bar is not None, "Test data should produce a cross"

        result = evaluate_rule(df, rule, bar_idx=cross_bar)
        assert result.fired is True
        assert result.bar_index == cross_bar

        # One bar before cross should not fire
        result_before = evaluate_rule(df, rule, bar_idx=cross_bar - 1)
        assert result_before.fired is False

    def test_crosses_above_at_bar_zero_no_fire(self):
        """bar_idx=0 has no previous bar → crosses_above cannot fire."""
        df = _make_df([100, 200, 300])
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "price",
                "params": {},
                "comparator": "crosses_above",
                "value": 150,
            }],
        }])
        result = evaluate_rule(df, rule, bar_idx=0)
        assert result.fired is False


# ---------------------------------------------------------------------------
# is_above / is_below
# ---------------------------------------------------------------------------

class TestIsAboveBelow:
    def test_is_above_fires(self):
        df = _make_df([150.0] * 5)
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "price",
                "params": {},
                "comparator": "is_above",
                "value": 100,
            }],
        }])
        result = evaluate_rule(df, rule)
        assert result.fired is True

    def test_is_above_does_not_fire(self):
        df = _make_df([80.0] * 5)
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "price",
                "params": {},
                "comparator": "is_above",
                "value": 100,
            }],
        }])
        result = evaluate_rule(df, rule)
        assert result.fired is False

    def test_is_below_fires(self):
        """RSI=25 is below 30 → fires."""
        # Build monotonic-down prices so RSI is very low
        prices = list(range(200, 150, -1))
        df = _make_df(prices)
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "rsi",
                "params": {"period": 14},
                "comparator": "is_below",
                "value": 30,
            }],
        }])
        result = evaluate_rule(df, rule)
        assert result.fired is True


# ---------------------------------------------------------------------------
# between
# ---------------------------------------------------------------------------

class TestBetween:
    def test_between_fires(self):
        """RSI between 30 and 70 for a mixed-movement series."""
        np.random.seed(99)
        prices = 100 + np.cumsum(np.random.randn(60) * 0.5)
        df = _make_df(prices)

        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "rsi",
                "params": {"period": 14},
                "comparator": "between",
                "value": [30, 70],
            }],
        }])
        result = evaluate_rule(df, rule)
        # With random walk around 100, RSI should be mid-range
        assert result.fired is True

    def test_between_does_not_fire(self):
        """RSI for monotonic up is 100 — not between 30 and 70."""
        prices = list(range(50, 100))
        df = _make_df(prices)
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "rsi",
                "params": {"period": 14},
                "comparator": "between",
                "value": [30, 70],
            }],
        }])
        result = evaluate_rule(df, rule)
        assert result.fired is False


# ---------------------------------------------------------------------------
# AND / OR group logic
# ---------------------------------------------------------------------------

class TestGroupLogic:
    def _both_true_rule(self):
        """Two conditions that are both true: price > 50 AND price > 60."""
        return _simple_rule([{
            "logic": "AND",
            "conditions": [
                {"indicator": "price", "params": {}, "comparator": "is_above", "value": 50},
                {"indicator": "price", "params": {}, "comparator": "is_above", "value": 60},
            ],
        }])

    def _one_true_one_false_rule(self, logic="AND"):
        """price > 50 (true) AND/OR price > 200 (false)."""
        return _simple_rule([{
            "logic": logic,
            "conditions": [
                {"indicator": "price", "params": {}, "comparator": "is_above", "value": 50},
                {"indicator": "price", "params": {}, "comparator": "is_above", "value": 200},
            ],
        }])

    def test_and_both_true(self):
        df = _make_df([100.0] * 5)
        result = evaluate_rule(df, self._both_true_rule())
        assert result.fired is True

    def test_and_one_false(self):
        df = _make_df([100.0] * 5)
        result = evaluate_rule(df, self._one_true_one_false_rule("AND"))
        assert result.fired is False

    def test_or_one_true(self):
        df = _make_df([100.0] * 5)
        result = evaluate_rule(df, self._one_true_one_false_rule("OR"))
        assert result.fired is True

    def test_or_both_false(self):
        df = _make_df([10.0] * 5)
        rule = _simple_rule([{
            "logic": "OR",
            "conditions": [
                {"indicator": "price", "params": {}, "comparator": "is_above", "value": 50},
                {"indicator": "price", "params": {}, "comparator": "is_above", "value": 200},
            ],
        }])
        result = evaluate_rule(df, rule)
        assert result.fired is False


# ---------------------------------------------------------------------------
# NaN / warmup
# ---------------------------------------------------------------------------

class TestWarmup:
    def test_nan_warmup_does_not_fire(self):
        """Evaluate at a bar still in warmup period → condition fails."""
        df = _make_df(list(range(5)))  # only 5 bars, SMA(10) is all NaN
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "sma",
                "params": {"period": 10},
                "comparator": "is_above",
                "value": 0,
            }],
        }])
        result = evaluate_rule(df, rule)
        assert result.fired is False


# ---------------------------------------------------------------------------
# Value as number vs indicator ref
# ---------------------------------------------------------------------------

class TestValueResolution:
    def test_value_as_number(self):
        df = _make_df([150.0] * 5)
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "price",
                "params": {},
                "comparator": "is_above",
                "value": 100,
            }],
        }])
        result = evaluate_rule(df, rule)
        assert result.fired is True

    def test_value_as_indicator_ref(self):
        """EMA(5) is_above EMA(20) on uptrend → fires."""
        prices = list(range(1, 51))
        df = _make_df(prices)
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "ema",
                "params": {"period": 5},
                "comparator": "is_above",
                "value": {"indicator": "ema", "params": {"period": 20}},
            }],
        }])
        result = evaluate_rule(df, rule)
        assert result.fired is True


# ---------------------------------------------------------------------------
# compute_warmup_period
# ---------------------------------------------------------------------------

class TestWarmupPeriod:
    def test_returns_max_period_plus_one(self):
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [
                {
                    "indicator": "ema",
                    "params": {"period": 20},
                    "comparator": "crosses_above",
                    "value": {"indicator": "ema", "params": {"period": 50}},
                },
            ],
        }])
        assert compute_warmup_period(rule) == 51  # max(20, 50) + 1

    def test_minimum_warmup(self):
        """Empty rule tree still returns at least 3 (minimum 2 + 1)."""
        assert compute_warmup_period({"groups": []}) == 3

    def test_multiple_conditions(self):
        rule = _simple_rule([
            {
                "logic": "AND",
                "conditions": [
                    {"indicator": "sma", "params": {"period": 10}, "comparator": "is_above", "value": 0},
                    {"indicator": "rsi", "params": {"period": 14}, "comparator": "is_below", "value": 70},
                ],
            },
            {
                "logic": "AND",
                "conditions": [
                    {"indicator": "ema", "params": {"period": 200}, "comparator": "is_above", "value": 0},
                ],
            },
        ])
        # max period = 200, +1 = 201
        assert compute_warmup_period(rule) == 201


# ---------------------------------------------------------------------------
# Indicator caching
# ---------------------------------------------------------------------------

class TestIndicatorCaching:
    def test_same_indicator_computed_once(self):
        """Two conditions referencing EMA(20) should use the cache."""
        df = _make_df(list(range(1, 51)))
        cache: dict[str, pd.Series] = {}

        from src.rules_engine.indicators import compute_indicator

        # Manually invoke _get_indicator twice
        s1 = _get_indicator(df, "ema", {"period": 20}, cache)
        s2 = _get_indicator(df, "ema", {"period": 20}, cache)

        # Same object (cached)
        assert s1 is s2
        # Only one key in cache
        assert len(cache) == 1

    def test_different_params_separate_cache_entries(self):
        df = _make_df(list(range(1, 51)))
        cache: dict[str, pd.Series] = {}

        s1 = _get_indicator(df, "ema", {"period": 10}, cache)
        s2 = _get_indicator(df, "ema", {"period": 20}, cache)

        assert s1 is not s2
        assert len(cache) == 2


# ---------------------------------------------------------------------------
# EvaluationResult structure
# ---------------------------------------------------------------------------

class TestEvaluationResult:
    def test_actions_returned_on_fire(self):
        df = _make_df([200.0] * 5)
        actions = [{"action": "enter_long", "params": {"size": 100}}]
        rule = _simple_rule(
            [{
                "logic": "AND",
                "conditions": [{"indicator": "price", "params": {}, "comparator": "is_above", "value": 100}],
            }],
            actions=actions,
        )
        result = evaluate_rule(df, rule)
        assert result.fired is True
        assert result.actions == actions

    def test_actions_empty_when_not_fired(self):
        df = _make_df([50.0] * 5)
        actions = [{"action": "enter_long", "params": {}}]
        rule = _simple_rule(
            [{
                "logic": "AND",
                "conditions": [{"indicator": "price", "params": {}, "comparator": "is_above", "value": 100}],
            }],
            actions=actions,
        )
        result = evaluate_rule(df, rule)
        assert result.fired is False
        assert result.actions == []

    def test_indicator_values_populated(self):
        df = _make_df([100.0] * 25)
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{
                "indicator": "sma",
                "params": {"period": 10},
                "comparator": "is_above",
                "value": 50,
            }],
        }])
        result = evaluate_rule(df, rule)
        assert len(result.indicator_values) > 0

    def test_bar_index_negative_resolved(self):
        df = _make_df([100.0] * 10)
        rule = _simple_rule([{
            "logic": "AND",
            "conditions": [{"indicator": "price", "params": {}, "comparator": "is_above", "value": 50}],
        }])
        result = evaluate_rule(df, rule, bar_idx=-1)
        assert result.bar_index == 9  # len(10) + (-1) = 9
