"""AlgoSphere FastAPI application."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.routers import admin, auth, users
from src.db import Base, engine

# Create tables on startup for SQLite only (dev convenience).
# For TiDB/MySQL, Alembic manages the schema — see docker/entrypoint-api.sh.
if str(engine.url).startswith("sqlite"):
    Base.metadata.create_all(engine)

app = FastAPI(
    title="AlgoSphere API",
    version="0.1.0",
    description="Multi-user algorithmic trading dashboard API",
)

_allowed_origins = os.environ.get(
    "CORS_ORIGINS",
    "http://localhost:5173,http://localhost:3000",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _allowed_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(admin.router)

# ---------------------------------------------------------------------------
# Redis quote cache (optional — degrades gracefully if REDIS_URL is unset)
# ---------------------------------------------------------------------------
_redis_url = os.environ.get("REDIS_URL")
if _redis_url:
    try:
        import redis
        from src.market_data.quote_cache import RedisQuoteCache

        _redis_client = redis.from_url(_redis_url, decode_responses=False)
        app.state.quote_cache = RedisQuoteCache(_redis_client)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Redis quote cache unavailable: %s", exc)


@app.get("/healthz", tags=["meta"])
def healthz() -> dict:
    return {"status": "ok"}
