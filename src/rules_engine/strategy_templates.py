"""Pre-built strategy templates for the rules engine.

These translate the existing TrendFollowingStrategy into rule trees
that users can select per ticker without building conditions from scratch.
"""
from __future__ import annotations

from typing import Any


def get_strategy_templates() -> list[dict[str, Any]]:
    """Return all available strategy templates with full metadata."""
    return [
        {
            "id": "core_trend_following",
            "name": "Core Trend Following",
            "description": (
                "The original AlgoSphere strategy. Buys when a stock is in a clear "
                "uptrend (price above its 200-day average) with strong short-term "
                "momentum (price above 20-day average). Filters out high-volatility "
                "setups to avoid choppy markets. Exits with a 1% stop loss, takes "
                "partial profit at 2%, then trails the rest with a 1% trailing stop. "
                "Also exits after 10 bars if nothing happens."
            ),
            "entry_rule": {
                "groups": [
                    {
                        "logic": "AND",
                        "conditions": [
                            {
                                "indicator": "price",
                                "params": {"field": "close"},
                                "comparator": "is_above",
                                "value": {"indicator": "sma", "params": {"period": 200}},
                            },
                            {
                                "indicator": "price",
                                "params": {"field": "close"},
                                "comparator": "is_above",
                                "value": {"indicator": "sma", "params": {"period": 20}},
                            },
                            {
                                "indicator": "atr_pct",
                                "params": {"period": 14},
                                "comparator": "is_below",
                                "value": 2.0,
                            },
                        ],
                    }
                ],
                "actions": [
                    {"action": "enter_long", "params": {"qty": 1}},
                    {"action": "set_stop_loss", "params": {"pct": 1.0}},
                    {"action": "set_take_profit", "params": {"pct": 2.0}},
                    {"action": "set_trailing_stop", "params": {"pct": 1.0}},
                ],
            },
            "exit_rule": {
                "groups": [
                    {
                        "logic": "OR",
                        "conditions": [
                            {
                                "indicator": "price",
                                "params": {"field": "close"},
                                "comparator": "is_below",
                                "value": {"indicator": "sma", "params": {"period": 20}},
                            },
                            {
                                "indicator": "atr_pct",
                                "params": {"period": 14},
                                "comparator": "is_above",
                                "value": 3.0,
                            },
                        ],
                    }
                ],
                "actions": [
                    {"action": "exit_position", "params": {}},
                ],
            },
            "explanation": {
                "entry": [
                    "Price must be above the 200-day moving average — confirms the stock is in a long-term uptrend",
                    "Price must also be above the 20-day moving average — confirms short-term momentum is bullish",
                    "ATR% must be below 2% — avoids entering when price is swinging wildly (choppy/volatile market)",
                    "If all three are true: buy shares, set a 1% stop loss, take profit at 2%, and trail the rest with a 1% trailing stop",
                ],
                "exit": [
                    "Exit if price drops below the 20-day average — momentum has turned against you",
                    "OR exit if ATR% exceeds 3% — volatility spike, protect your capital",
                ],
            },
        },
        {
            "id": "pullback_entry",
            "name": "Buy the Dip",
            "description": (
                "Waits for a stock in an uptrend to pull back to its 20-day average, "
                "then buys near that support level. Good for getting better entry prices "
                "in trending stocks instead of chasing. Uses RSI to confirm the dip "
                "isn't a reversal."
            ),
            "entry_rule": {
                "groups": [
                    {
                        "logic": "AND",
                        "conditions": [
                            {
                                "indicator": "price",
                                "params": {"field": "close"},
                                "comparator": "is_above",
                                "value": {"indicator": "sma", "params": {"period": 200}},
                            },
                            {
                                "indicator": "rsi",
                                "params": {"period": 14},
                                "comparator": "is_below",
                                "value": 40,
                            },
                            {
                                "indicator": "price",
                                "params": {"field": "close"},
                                "comparator": "is_above",
                                "value": {"indicator": "sma", "params": {"period": 50}},
                            },
                        ],
                    }
                ],
                "actions": [
                    {"action": "enter_long", "params": {"qty": 1}},
                    {"action": "set_stop_loss", "params": {"pct": 3.0}},
                    {"action": "set_take_profit", "params": {"pct": 5.0}},
                ],
            },
            "exit_rule": {
                "groups": [
                    {
                        "logic": "OR",
                        "conditions": [
                            {
                                "indicator": "rsi",
                                "params": {"period": 14},
                                "comparator": "is_above",
                                "value": 70,
                            },
                            {
                                "indicator": "price",
                                "params": {"field": "close"},
                                "comparator": "is_below",
                                "value": {"indicator": "sma", "params": {"period": 50}},
                            },
                        ],
                    }
                ],
                "actions": [
                    {"action": "exit_position", "params": {}},
                ],
            },
            "explanation": {
                "entry": [
                    "Price must be above 200-day MA — stock is in a long-term uptrend",
                    "RSI must be below 40 — stock has pulled back and may be oversold (the dip)",
                    "Price must still be above 50-day MA — the dip isn't too deep",
                    "If all true: buy with 3% stop loss and 5% profit target",
                ],
                "exit": [
                    "Exit when RSI rises above 70 — stock is overbought, time to take profits",
                    "OR exit if price falls below 50-day MA — the trend may be broken",
                ],
            },
        },
        {
            "id": "momentum_breakout",
            "name": "Momentum Breakout",
            "description": (
                "Catches stocks breaking out with strong momentum. Enters when the "
                "fast EMA crosses above the slow EMA with volume confirmation. "
                "Uses a trailing stop to ride the trend as long as it lasts."
            ),
            "entry_rule": {
                "groups": [
                    {
                        "logic": "AND",
                        "conditions": [
                            {
                                "indicator": "ema",
                                "params": {"period": 10},
                                "comparator": "crosses_above",
                                "value": {"indicator": "ema", "params": {"period": 30}},
                            },
                            {
                                "indicator": "volume_avg_ratio",
                                "params": {"period": 20},
                                "comparator": "is_above",
                                "value": 1.5,
                            },
                        ],
                    }
                ],
                "actions": [
                    {"action": "enter_long", "params": {"qty": 1}},
                    {"action": "set_stop_loss", "params": {"pct": 2.0}},
                    {"action": "set_trailing_stop", "params": {"pct": 1.5}},
                ],
            },
            "exit_rule": {
                "groups": [
                    {
                        "logic": "AND",
                        "conditions": [
                            {
                                "indicator": "ema",
                                "params": {"period": 10},
                                "comparator": "crosses_below",
                                "value": {"indicator": "ema", "params": {"period": 30}},
                            },
                        ],
                    }
                ],
                "actions": [
                    {"action": "exit_position", "params": {}},
                ],
            },
            "explanation": {
                "entry": [
                    "EMA(10) crosses above EMA(30) — short-term momentum has turned bullish",
                    "Volume must be 1.5x above its 20-day average — confirms real buying interest, not a fake move",
                    "If both true: buy with 2% stop loss and 1.5% trailing stop to ride the trend",
                ],
                "exit": [
                    "Exit when EMA(10) crosses back below EMA(30) — momentum has faded",
                ],
            },
        },
    ]


def get_template_by_id(template_id: str) -> dict[str, Any] | None:
    """Look up a strategy template by ID."""
    for t in get_strategy_templates():
        if t["id"] == template_id:
            return t
    return None
