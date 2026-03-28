"""Tests for SAR-173: FastAPI REST API."""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("TIDB_DSN", "")
os.environ.setdefault("JWT_SECRET", "test-secret-for-api")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.deps import get_db
from src.api.main import app
from src.auth import hash_password
from src.db.models import Base, User, UserRole
from src.db.repos import portfolio_repo, trade_repo, gate_log_repo, regime_repo, user_repo


# ---------------------------------------------------------------------------
# Test DB setup
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_engine():
    # StaticPool ensures all sessions share the same in-memory SQLite connection
    eng = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(scope="module")
def TestSession(test_engine):
    return sessionmaker(bind=test_engine, autoflush=False, autocommit=False)


@pytest.fixture(scope="module")
def client(test_engine, TestSession):
    def override_get_db():
        session = TestSession()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def seeded(TestSession):
    """Seed DB with trader + admin users and some data."""
    with TestSession() as session:
        trader = user_repo.create(
            session,
            user_id="api_trader",
            email="trader@api.example.com",
            hashed_password=hash_password("trader-pass"),
            role=UserRole.trader,
            paper=True,
        )
        admin = user_repo.create(
            session,
            user_id="api_admin",
            email="admin@api.example.com",
            hashed_password=hash_password("admin-pass"),
            role=UserRole.admin,
            paper=False,
        )
        portfolio_repo.snapshot_portfolio(session, user_id="api_trader", equity=100_000.0, cash=50_000.0, daily_pnl=500.0)
        portfolio_repo.upsert_position(session, user_id="api_trader", symbol="AAPL", qty=10.0, avg_entry_price=175.0)
        trade_repo.record_trade(session, user_id="api_trader", symbol="MSFT", qty=5.0, pnl=200.0, exit_reason="target")
        gate_log_repo.record_gate(session, user_id="api_trader", gate="regime_filter", passed=True, reason="bullish")
        regime_repo.record_regime(session, user_id="api_trader", label="bullish", spy_score=0.72, vix=14.2)
        session.commit()
    return {"trader_id": "api_trader", "admin_id": "api_admin"}


@pytest.fixture(scope="module")
def trader_token(client, seeded):
    resp = client.post("/auth/login", json={"email": "trader@api.example.com", "password": "trader-pass"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


@pytest.fixture(scope="module")
def admin_token(client, seeded):
    resp = client.post("/auth/login", json={"email": "admin@api.example.com", "password": "admin-pass"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------

def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Auth — /auth/login
# ---------------------------------------------------------------------------

def test_login_success(client, seeded):
    resp = client.post("/auth/login", json={"email": "trader@api.example.com", "password": "trader-pass"})
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_login_wrong_password(client, seeded):
    resp = client.post("/auth/login", json={"email": "trader@api.example.com", "password": "wrong"})
    assert resp.status_code == 401


def test_login_unknown_email(client, seeded):
    resp = client.post("/auth/login", json={"email": "ghost@example.com", "password": "x"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Auth — /auth/me
# ---------------------------------------------------------------------------

def test_me_returns_profile(client, seeded, trader_token):
    resp = client.get("/auth/me", headers={"Authorization": f"Bearer {trader_token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "api_trader"
    assert data["role"] == "trader"


def test_me_no_token(client):
    resp = client.get("/auth/me")
    assert resp.status_code in (401, 403)


def test_me_invalid_token(client):
    resp = client.get("/auth/me", headers={"Authorization": "Bearer not.a.token"})
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# /api/users/{id}/portfolio
# ---------------------------------------------------------------------------

def test_get_portfolio(client, seeded, trader_token):
    resp = client.get(
        "/api/users/api_trader/portfolio",
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["latest"]["equity"] == pytest.approx(100_000.0)
    assert isinstance(data["history"], list)


def test_get_portfolio_forbidden_other_user(client, seeded, trader_token):
    resp = client.get(
        "/api/users/api_admin/portfolio",
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 403


def test_get_portfolio_admin_can_access_any(client, seeded, admin_token):
    resp = client.get(
        "/api/users/api_trader/portfolio",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# /api/users/{id}/positions
# ---------------------------------------------------------------------------

def test_get_positions(client, seeded, trader_token):
    resp = client.get(
        "/api/users/api_trader/positions",
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 200
    positions = resp.json()
    assert len(positions) >= 1
    assert positions[0]["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# /api/users/{id}/trades
# ---------------------------------------------------------------------------

def test_get_trades(client, seeded, trader_token):
    resp = client.get(
        "/api/users/api_trader/trades",
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 200
    trades = resp.json()
    assert len(trades) >= 1
    assert trades[0]["symbol"] == "MSFT"


def test_get_trades_limit_param(client, seeded, trader_token):
    resp = client.get(
        "/api/users/api_trader/trades?limit=1",
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 200
    assert len(resp.json()) <= 1


# ---------------------------------------------------------------------------
# /api/users/{id}/gate-log
# ---------------------------------------------------------------------------

def test_get_gate_log(client, seeded, trader_token):
    resp = client.get(
        "/api/users/api_trader/gate-log",
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 200
    entries = resp.json()
    assert any(e["gate"] == "regime_filter" for e in entries)


# ---------------------------------------------------------------------------
# /api/users/{id}/regime
# ---------------------------------------------------------------------------

def test_get_regime(client, seeded, trader_token):
    resp = client.get(
        "/api/users/api_trader/regime",
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["label"] == "bullish"
    assert data["spy_score"] == pytest.approx(0.72)


# ---------------------------------------------------------------------------
# /api/admin/users
# ---------------------------------------------------------------------------

def test_admin_list_users(client, seeded, admin_token):
    resp = client.get(
        "/api/admin/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert resp.status_code == 200
    users = resp.json()
    ids = [u["id"] for u in users]
    assert "api_trader" in ids
    assert "api_admin" in ids


def test_admin_users_forbidden_for_trader(client, seeded, trader_token):
    resp = client.get(
        "/api/admin/users",
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 403


def test_admin_users_no_auth(client):
    resp = client.get("/api/admin/users")
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# /auth/register
# ---------------------------------------------------------------------------

def test_register_success(client):
    resp = client.post("/auth/register", json={
        "email": "newuser@register-test.example.com",
        "password": "validpass123",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


def test_register_returns_valid_token(client):
    resp = client.post("/auth/register", json={
        "email": "tokencheck@register-test.example.com",
        "password": "validpass123",
    })
    token = resp.json()["access_token"]
    # Use the token to hit /auth/me
    me_resp = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me_resp.status_code == 200
    assert me_resp.json()["email"] == "tokencheck@register-test.example.com"
    assert me_resp.json()["role"] == "trader"


def test_register_duplicate_email(client):
    client.post("/auth/register", json={
        "email": "dupe@register-test.example.com",
        "password": "validpass123",
    })
    resp = client.post("/auth/register", json={
        "email": "dupe@register-test.example.com",
        "password": "differentpass123",
    })
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


def test_register_short_password(client):
    resp = client.post("/auth/register", json={
        "email": "short@register-test.example.com",
        "password": "abc",
    })
    assert resp.status_code == 422


def test_register_invalid_email(client):
    resp = client.post("/auth/register", json={
        "email": "not-an-email",
        "password": "validpass123",
    })
    assert resp.status_code == 422


def test_register_missing_fields(client):
    resp = client.post("/auth/register", json={"email": "only@email.com"})
    assert resp.status_code == 422


def test_register_then_login(client):
    """Full flow: register then login with same credentials."""
    email = "flow@register-test.example.com"
    password = "mypassword123"
    reg_resp = client.post("/auth/register", json={"email": email, "password": password})
    assert reg_resp.status_code == 201

    login_resp = client.post("/auth/login", json={"email": email, "password": password})
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()


# ---------------------------------------------------------------------------
# /api/users/{id}/onboard
# ---------------------------------------------------------------------------

def test_onboard_success(client):
    # Register a new user
    reg = client.post("/auth/register", json={
        "email": "onboard@onboard-test.example.com",
        "password": "validpass123",
    })
    token = reg.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    user_id = me.json()["id"]

    resp = client.put(
        f"/api/users/{user_id}/onboard",
        json={
            "alpaca_key": "PKTEST123",
            "alpaca_secret": "secretkey456",
            "paper": True,
            "risk_profile": "conservative",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_onboard_updates_user_profile(client):
    # Register and onboard
    reg = client.post("/auth/register", json={
        "email": "onboard2@onboard-test.example.com",
        "password": "validpass123",
    })
    token = reg.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    user_id = me.json()["id"]

    client.put(
        f"/api/users/{user_id}/onboard",
        json={
            "alpaca_key": "PKLIVE789",
            "alpaca_secret": "livesecret",
            "paper": False,
            "risk_profile": "aggressive",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    # Verify profile updated
    me2 = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me2.json()["paper"] is False


def test_onboard_forbidden_other_user(client, seeded, trader_token):
    resp = client.put(
        "/api/users/api_admin/onboard",
        json={
            "alpaca_key": "PK123",
            "alpaca_secret": "secret",
            "paper": True,
            "risk_profile": "balanced",
        },
        headers={"Authorization": f"Bearer {trader_token}"},
    )
    assert resp.status_code == 403


def test_onboard_no_auth(client, seeded):
    resp = client.put(
        "/api/users/api_trader/onboard",
        json={
            "alpaca_key": "PK123",
            "alpaca_secret": "secret",
            "paper": True,
            "risk_profile": "balanced",
        },
    )
    assert resp.status_code in (401, 403)


def test_onboard_missing_fields(client):
    reg = client.post("/auth/register", json={
        "email": "onboard3@onboard-test.example.com",
        "password": "validpass123",
    })
    token = reg.json()["access_token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    user_id = me.json()["id"]

    resp = client.put(
        f"/api/users/{user_id}/onboard",
        json={"alpaca_key": "PK123"},  # missing alpaca_secret
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422
