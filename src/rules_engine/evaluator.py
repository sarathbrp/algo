"""Rule tree evaluator — walks JSON rule trees against DataFrames.

The evaluator computes indicators, resolves condition values, and determines
whether a rule fires at a given bar index. Indicator results are cached so
the same indicator+params combo is computed only once per evaluation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.rules_engine.indicators import compute_indicator


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class ConditionResult:
    indicator_name: str
    indicator_value: float | None
    comparator: str
    target_value: float | None
    passed: bool
    detail: str


@dataclass
class GroupResult:
    logic: str
    conditions: list[ConditionResult]
    passed: bool


@dataclass
class EvaluationResult:
    fired: bool
    groups: list[GroupResult]
    actions: list[dict]
    bar_index: int
    indicator_values: dict[str, float]


# ---------------------------------------------------------------------------
# Indicator cache
# ---------------------------------------------------------------------------

def _cache_key(indicator: str, params: dict) -> str:
    sorted_items = sorted((k, v) for k, v in params.items())
    return f"{indicator}:{sorted_items}"


def _get_indicator(
    df: pd.DataFrame,
    indicator: str,
    params: dict,
    cache: dict[str, pd.Series],
) -> pd.Series:
    key = _cache_key(indicator, params)
    if key not in cache:
        cache[key] = compute_indicator(df, indicator, params)
    return cache[key]


def _safe_float(series: pd.Series, idx: int) -> float | None:
    """Extract a float from a series, returning None for NaN."""
    if idx < 0:
        idx = len(series) + idx
    if idx < 0 or idx >= len(series):
        return None
    val = series.iloc[idx]
    if pd.isna(val):
        return None
    return float(val)


# ---------------------------------------------------------------------------
# Value resolution
# ---------------------------------------------------------------------------

def _resolve_value(
    df: pd.DataFrame,
    value_spec: Any,
    bar_idx: int,
    cache: dict[str, pd.Series],
) -> tuple[float | None, float | None]:
    """Resolve a condition's value field to (current, previous) floats.

    value_spec can be:
    - a number → (number, number)
    - an indicator ref dict → compute and extract at bar_idx and bar_idx-1
    """
    if isinstance(value_spec, (int, float)):
        return (float(value_spec), float(value_spec))
    if isinstance(value_spec, dict) and "indicator" in value_spec:
        series = _get_indicator(df, value_spec["indicator"], value_spec.get("params", {}), cache)
        cur = _safe_float(series, bar_idx)
        prev = _safe_float(series, bar_idx - 1) if bar_idx > 0 else None
        return (cur, prev)
    return (None, None)


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def evaluate_condition(
    df: pd.DataFrame,
    condition: dict,
    bar_idx: int,
    cache: dict[str, pd.Series],
) -> ConditionResult:
    """Evaluate a single condition at bar_idx."""
    indicator = condition["indicator"]
    params = condition.get("params", {})
    comparator = condition["comparator"]
    value_spec = condition["value"]

    # Compute left side (the indicator)
    left_series = _get_indicator(df, indicator, params, cache)
    left_cur = _safe_float(left_series, bar_idx)
    left_prev = _safe_float(left_series, bar_idx - 1) if bar_idx > 0 else None

    # Handle 'between' specially
    if comparator == "between":
        if isinstance(value_spec, list) and len(value_spec) == 2:
            low, high = float(value_spec[0]), float(value_spec[1])
            passed = left_cur is not None and low <= left_cur <= high
            return ConditionResult(
                indicator_name=_cache_key(indicator, params),
                indicator_value=left_cur,
                comparator=comparator,
                target_value=None,
                passed=passed,
                detail=f"{left_cur} {'is' if passed else 'is not'} between {low} and {high}",
            )
        return ConditionResult(
            indicator_name=_cache_key(indicator, params),
            indicator_value=left_cur,
            comparator=comparator,
            target_value=None,
            passed=False,
            detail="Invalid 'between' value — expected [low, high]",
        )

    # Resolve right side
    right_cur, right_prev = _resolve_value(df, value_spec, bar_idx, cache)

    # Check for None values (warmup / missing data)
    if left_cur is None or right_cur is None:
        return ConditionResult(
            indicator_name=_cache_key(indicator, params),
            indicator_value=left_cur,
            comparator=comparator,
            target_value=right_cur,
            passed=False,
            detail="Insufficient data (indicator still in warmup period)",
        )

    passed = False
    detail = ""

    if comparator == "is_above":
        passed = left_cur > right_cur
        detail = f"{left_cur:.4f} {'>' if passed else '<='} {right_cur:.4f}"

    elif comparator == "is_below":
        passed = left_cur < right_cur
        detail = f"{left_cur:.4f} {'<' if passed else '>='} {right_cur:.4f}"

    elif comparator == "crosses_above":
        if left_prev is None or right_prev is None:
            detail = "Insufficient history for cross detection (need at least 2 bars)"
        else:
            passed = left_prev <= right_prev and left_cur > right_cur
            detail = f"prev: {left_prev:.4f} vs {right_prev:.4f}, now: {left_cur:.4f} vs {right_cur:.4f} — {'crossed above' if passed else 'no cross'}"

    elif comparator == "crosses_below":
        if left_prev is None or right_prev is None:
            detail = "Insufficient history for cross detection (need at least 2 bars)"
        else:
            passed = left_prev >= right_prev and left_cur < right_cur
            detail = f"prev: {left_prev:.4f} vs {right_prev:.4f}, now: {left_cur:.4f} vs {right_cur:.4f} — {'crossed below' if passed else 'no cross'}"

    else:
        detail = f"Unknown comparator: {comparator}"

    return ConditionResult(
        indicator_name=_cache_key(indicator, params),
        indicator_value=left_cur,
        comparator=comparator,
        target_value=right_cur,
        passed=passed,
        detail=detail,
    )


# ---------------------------------------------------------------------------
# Group and rule evaluation
# ---------------------------------------------------------------------------

def evaluate_group(
    df: pd.DataFrame,
    group: dict,
    bar_idx: int,
    cache: dict[str, pd.Series],
) -> GroupResult:
    """Evaluate all conditions in a group with AND/OR logic."""
    logic = group.get("logic", "AND").upper()
    conditions = group.get("conditions", [])
    results = [evaluate_condition(df, c, bar_idx, cache) for c in conditions]

    if not results:
        return GroupResult(logic=logic, conditions=results, passed=False)

    if logic == "OR":
        passed = any(r.passed for r in results)
    else:  # AND
        passed = all(r.passed for r in results)

    return GroupResult(logic=logic, conditions=results, passed=passed)


def evaluate_rule(
    df: pd.DataFrame,
    rule_tree: dict,
    bar_idx: int = -1,
) -> EvaluationResult:
    """Evaluate a full rule tree at a given bar index.

    Parameters
    ----------
    df : DataFrame with OHLCV columns
    rule_tree : the JSON rule tree (groups + actions)
    bar_idx : bar to evaluate at (-1 = last bar)

    Returns
    -------
    EvaluationResult with fired status, per-group results, actions, and indicator snapshot.
    """
    if bar_idx < 0:
        bar_idx = len(df) + bar_idx

    cache: dict[str, pd.Series] = {}
    groups = rule_tree.get("groups", [])
    actions = rule_tree.get("actions", [])

    group_results = [evaluate_group(df, g, bar_idx, cache) for g in groups]

    # All groups must pass (implicit AND at the top level)
    fired = bool(group_results) and all(g.passed for g in group_results)

    # Build indicator snapshot
    indicator_values: dict[str, float] = {}
    for key, series in cache.items():
        val = _safe_float(series, bar_idx)
        if val is not None:
            indicator_values[key] = val

    return EvaluationResult(
        fired=fired,
        groups=group_results,
        actions=actions if fired else [],
        bar_index=bar_idx,
        indicator_values=indicator_values,
    )


# ---------------------------------------------------------------------------
# Warmup period calculation
# ---------------------------------------------------------------------------

def compute_warmup_period(rule_tree: dict) -> int:
    """Walk the rule tree and find the maximum indicator period needed."""
    max_period = 2  # minimum

    def _scan_indicator(spec: dict) -> None:
        nonlocal max_period
        period = spec.get("params", {}).get("period", 0)
        if period > max_period:
            max_period = period

    for group in rule_tree.get("groups", []):
        for cond in group.get("conditions", []):
            _scan_indicator(cond)
            # Also check value if it's an indicator ref
            val = cond.get("value")
            if isinstance(val, dict) and "indicator" in val:
                _scan_indicator(val)

    # Add 1 for cross detection (needs previous bar)
    return max_period + 1
