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


@app.get("/healthz", tags=["meta"])
def healthz() -> dict:
    return {"status": "ok"}
