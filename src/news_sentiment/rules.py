"""Rule engine: positive + volume spike → buy; negative + weak trend → sell."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


def volume_spike_ratio(df: pd.DataFrame, lookback: int = 20) -> float | None:
    """Last bar volume / average of prior `lookback` days. None if not computable."""
    if df is None or df.empty or "volume" not in df.columns:
        return None
    n = len(df)
    if n < lookback + 1:
        return None
    vol = df["volume"].astype(float)
    last = float(vol.iloc[-1])
    prev = vol.iloc[-lookback - 1 : -1]
    avg = float(prev.mean())
    if avg <= 0:
        return None
    return last / avg


def weak_trend_vs_ma(df: pd.DataFrame, ma_period: int) -> bool:
    """True if close is below MA(ma_period) (weak / distribution)."""
    if df is None or df.empty or "close" not in df.columns:
        return False
    if len(df) < ma_period:
        return False
    close = float(df["close"].iloc[-1])
    ma = float(df["close"].rolling(ma_period).mean().iloc[-1])
    return close < ma


@dataclass
class NewsRuleEngine:
    """Configurable thresholds from config['news_sentiment']."""

    positive_score_threshold: float = 0.12
    negative_score_threshold: float = -0.12
    volume_spike_min: float = 1.5
    weak_trend_ma_period: int = 20

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "NewsRuleEngine":
        ns = config.get("news_sentiment") or {}
        return cls(
            positive_score_threshold=float(ns.get("positive_score_threshold", 0.12)),
            negative_score_threshold=float(ns.get("negative_score_threshold", -0.12)),
            volume_spike_min=float(ns.get("volume_spike_min", 1.5)),
            weak_trend_ma_period=int(ns.get("weak_trend_ma_period", 20)),
        )

    def should_buy(self, sentiment_score: float, vol_ratio: float | None) -> bool:
        if vol_ratio is None:
            return False
        return sentiment_score >= self.positive_score_threshold and vol_ratio >= self.volume_spike_min

    def should_sell(self, sentiment_score: float, df: pd.DataFrame) -> bool:
        if sentiment_score > self.negative_score_threshold:
            return False
        return weak_trend_vs_ma(df, self.weak_trend_ma_period)
