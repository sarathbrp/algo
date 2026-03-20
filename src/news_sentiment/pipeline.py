"""Orchestrate NewsAPI → FinBERT → cached sentiment score per symbol."""
from __future__ import annotations

import logging
import time
from typing import Any

from .finbert_sentiment import score_texts
from .newsapi_client import fetch_headlines, newsapi_key_from_config

log = logging.getLogger(__name__)


class NewsSentimentPipeline:
    def __init__(self, config: dict[str, Any]):
        self.config = config
        ns = config.get("news_sentiment") or {}
        self.enabled = bool(ns.get("enabled", False))
        self.lookback_hours = int(ns.get("headline_lookback_hours", 24))
        self.max_headlines = int(ns.get("max_headlines", 15))
        self.cache_ttl_sec = float(ns.get("cache_ttl_seconds", 900))
        self.model_id = str(ns.get("finbert_model", "ProsusAI/finbert"))
        self._cache: dict[str, tuple[float, float]] = {}  # symbol -> (ts, score)

    def sentiment_for_symbol(self, symbol: str) -> float:
        """FinBERT aggregate score in ~[-1, 1]; 0 if disabled / no API / error."""
        if not self.enabled:
            return 0.0
        key = symbol.upper()
        now = time.time()
        if key in self._cache:
            ts, sc = self._cache[key]
            if now - ts < self.cache_ttl_sec:
                return sc

        api_key = newsapi_key_from_config(self.config)
        if not api_key:
            log.debug("News sentiment skipped: no NEWSAPI_KEY")
            self._cache[key] = (now, 0.0)
            return 0.0

        headlines = fetch_headlines(
            key,
            api_key,
            lookback_hours=self.lookback_hours,
            page_size=self.max_headlines,
        )
        if not headlines:
            self._cache[key] = (now, 0.0)
            return 0.0

        try:
            score = score_texts(headlines, model_id=self.model_id)
        except Exception as e:
            log.warning("FinBERT scoring failed for %s: %s", key, e)
            score = 0.0
        self._cache[key] = (now, score)
        return score
