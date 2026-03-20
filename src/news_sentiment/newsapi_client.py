"""Fetch recent headlines from NewsAPI (https://newsapi.org/)."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

log = logging.getLogger(__name__)

NEWSAPI_EVERYTHING = "https://newsapi.org/v2/everything"


def fetch_headlines(
    symbol: str,
    api_key: str,
    *,
    lookback_hours: int = 24,
    page_size: int = 15,
    language: str = "en",
    timeout_sec: float = 15.0,
) -> list[str]:
    """
    Query NewsAPI everything endpoint for articles mentioning the ticker/symbol.
    Returns list of headline strings (title + optional description snippet).
    """
    if not api_key:
        return []
    # NewsAPI free tier: query often uses company name; symbol in quotes helps
    q = f'"{symbol}" OR {symbol} stock'
    to_dt = datetime.now(timezone.utc)
    from_dt = to_dt - timedelta(hours=max(1, lookback_hours))
    params: dict[str, Any] = {
        "q": q,
        "language": language,
        "sortBy": "publishedAt",
        "pageSize": min(100, max(1, page_size)),
        "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": to_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "apiKey": api_key,
    }
    try:
        r = requests.get(NEWSAPI_EVERYTHING, params=params, timeout=timeout_sec)
        r.raise_for_status()
        data = r.json()
        if data.get("status") != "ok":
            log.warning("NewsAPI non-ok: %s", data.get("message", data))
            return []
        out: list[str] = []
        for art in data.get("articles") or []:
            title = (art.get("title") or "").strip()
            desc = (art.get("description") or "").strip()
            if title:
                out.append(f"{title}. {desc}" if desc else title)
        return out
    except Exception as e:
        log.warning("NewsAPI fetch failed for %s: %s", symbol, e)
        return []


def newsapi_key_from_config(config: dict) -> str:
    ns = config.get("news_sentiment") or {}
    env_name = ns.get("newsapi_key_env") or "NEWSAPI_KEY"
    return (os.environ.get(env_name) or "").strip()
