"""Rules engine — indicator computation, rule evaluation, validation, and backtesting."""
from __future__ import annotations

from src.rules_engine.indicators import compute_indicator
from src.rules_engine.evaluator import evaluate_rule, EvaluationResult
from src.rules_engine.validator import validate_rule, detect_conflicts, ValidationIssue
from src.rules_engine.backtest import run_backtest, BacktestResult, BacktestConfig

__all__ = [
    "compute_indicator",
    "evaluate_rule",
    "EvaluationResult",
    "validate_rule",
    "detect_conflicts",
    "ValidationIssue",
    "run_backtest",
    "BacktestResult",
    "BacktestConfig",
]
