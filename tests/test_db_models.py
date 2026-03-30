"""Tests for SAR-168: TiDB schema + Alembic migrations.

Uses SQLite in-memory for CI — no TiDB cluster required.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

# Ensure SQLite fallback for tests
os.environ.setdefault("TIDB_DSN", "")

from src.db.models import (
    AccountSettings,
    Base,
    BrokerAccount,
    GateLog,
    PortfolioSnapshot,
    Position,
    RegimeLabel,
    RegimeLog,
    Trade,
    TradeSide,
    User,
    UserSettings,
    UserRole,
    WorkerStatus,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture()
def session(engine):
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    with Session() as s:
        yield s


@pytest.fixture()
def user(session) -> User:
    u = User(
        id="trader1",
        email="trader1@algo.com",
        role=UserRole.trader,
        paper=True,
    )
    session.add(u)
    session.commit()
    return u


# ---------------------------------------------------------------------------
# Schema / table existence
# ---------------------------------------------------------------------------

def test_all_tables_created(engine):
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    expected = {
        "users",
        "broker_accounts",
        "user_settings",
        "account_settings",
        "portfolio_snapshots",
        "positions",
        "trades",
        "regime_log",
        "gate_log",
        "worker_status",
    }
    assert expected <= tables


def test_users_columns(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("users")}
    assert {"id", "email", "hashed_password", "role", "paper", "created_at"} <= cols


def test_broker_accounts_columns(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("broker_accounts")}
    assert {
        "id", "user_id", "provider", "provider_account_id", "paper",
        "credentials_ref", "is_active", "created_at", "updated_at",
    } <= cols


def test_user_settings_columns(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("user_settings")}
    assert {
        "id", "user_id", "theme", "dashboard_layout", "timezone",
        "notifications_enabled", "created_at", "updated_at",
    } <= cols


def test_account_settings_columns(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("account_settings")}
    assert {
        "id", "broker_account_id", "trading_enabled", "bot_state", "strategy_slug",
        "risk_profile", "max_positions", "created_at", "updated_at",
    } <= cols


def test_worker_status_columns(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("worker_status")}
    assert {
        "id", "worker_name", "status", "current_user_id", "last_error",
        "last_heartbeat", "last_reconciled_at", "updated_at",
    } <= cols


def test_trades_vector_column(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("trades")}
    assert "setup_vector" in cols


def test_regime_log_vector_column(engine):
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("regime_log")}
    assert "state_vector" in cols


def test_indexes_created(engine):
    insp = inspect(engine)
    trade_indexes = {i["name"] for i in insp.get_indexes("trades")}
    assert "idx_trade_user" in trade_indexes
    assert "idx_trade_user_time" in trade_indexes

    gate_indexes = {i["name"] for i in insp.get_indexes("gate_log")}
    assert "idx_gate_user_time" in gate_indexes
    assert "idx_gate_user_gate" in gate_indexes

    broker_indexes = {i["name"] for i in insp.get_indexes("broker_accounts")}
    assert "idx_broker_account_active" in broker_indexes

    worker_indexes = {i["name"] for i in insp.get_indexes("worker_status")}
    assert "idx_worker_status_updated" in worker_indexes


# ---------------------------------------------------------------------------
# User model
# ---------------------------------------------------------------------------

def test_create_user(session):
    u = User(id="u1", email="u1@test.com", role=UserRole.admin, paper=False)
    session.add(u)
    session.commit()

    fetched = session.get(User, "u1")
    assert fetched is not None
    assert fetched.email == "u1@test.com"
    assert fetched.role == UserRole.admin
    assert fetched.paper is False


def test_user_default_role_is_trader(session):
    u = User(id="u2", email="u2@test.com")
    session.add(u)
    session.commit()
    assert session.get(User, "u2").role == UserRole.trader


def test_user_email_unique_constraint(session):
    session.add(User(id="u3", email="same@test.com"))
    session.commit()
    session.add(User(id="u4", email="same@test.com"))
    with pytest.raises(Exception):
        session.commit()


def test_user_has_single_broker_account(session):
    session.add(User(id="u5", email="u5@test.com"))
    session.commit()
    session.add(BrokerAccount(user_id="u5", provider_account_id="acct-1"))
    session.commit()
    session.add(BrokerAccount(user_id="u5", provider_account_id="acct-2"))
    with pytest.raises(Exception):
        session.commit()


def test_create_user_settings(session, user):
    settings = UserSettings(user_id=user.id, theme="amber", timezone="UTC")
    session.add(settings)
    session.commit()

    fetched = session.get(UserSettings, settings.id)
    assert fetched.theme == "amber"
    assert fetched.timezone == "UTC"


def test_create_broker_account_and_settings(session, user):
    account = BrokerAccount(
        user_id=user.id,
        provider="alpaca",
        provider_account_id="acct-123",
        paper=True,
        credentials_ref="secret://alpaca/trader1",
    )
    session.add(account)
    session.commit()

    acct_settings = AccountSettings(
        broker_account_id=account.id,
        trading_enabled=True,
        bot_state="running",
        strategy_slug="trend_following",
        risk_profile="balanced",
        max_positions=5,
    )
    session.add(acct_settings)
    session.commit()

    fetched_account = session.get(BrokerAccount, account.id)
    fetched_settings = session.get(AccountSettings, acct_settings.id)
    assert fetched_account.provider_account_id == "acct-123"
    assert fetched_settings.bot_state == "running"
    assert fetched_settings.max_positions == 5


def test_create_worker_status(session):
    status = WorkerStatus(worker_name="alpaca_loop", status="running")
    session.add(status)
    session.commit()

    fetched = session.get(WorkerStatus, status.id)
    assert fetched.worker_name == "alpaca_loop"
    assert fetched.status == "running"


# ---------------------------------------------------------------------------
# PortfolioSnapshot
# ---------------------------------------------------------------------------

def test_create_portfolio_snapshot(session, user):
    snap = PortfolioSnapshot(
        user_id=user.id,
        equity=100_000.00,
        cash=50_000.00,
        daily_pnl=1_234.56,
    )
    session.add(snap)
    session.commit()

    fetched = session.get(PortfolioSnapshot, snap.id)
    assert fetched is not None
    assert float(fetched.equity) == pytest.approx(100_000.00)
    assert float(fetched.daily_pnl) == pytest.approx(1_234.56)


def test_portfolio_snapshot_fk_cascade(session, user):
    snap = PortfolioSnapshot(user_id=user.id, equity=1000)
    session.add(snap)
    session.commit()
    snap_id = snap.id

    session.delete(user)
    session.commit()
    assert session.get(PortfolioSnapshot, snap_id) is None


# ---------------------------------------------------------------------------
# Position
# ---------------------------------------------------------------------------

def test_create_position(session, user):
    pos = Position(
        user_id=user.id,
        symbol="AAPL",
        side=TradeSide.long,
        qty=10.0,
        avg_entry_price=175.50,
        last_buy_price=175.50,
    )
    session.add(pos)
    session.commit()

    fetched = session.get(Position, pos.id)
    assert fetched.symbol == "AAPL"
    assert fetched.side == TradeSide.long
    assert float(fetched.qty) == pytest.approx(10.0)
    assert float(fetched.last_buy_price) == pytest.approx(175.5)


def test_position_multi_user_isolation(session):
    u1 = User(id="mu1", email="mu1@test.com")
    u2 = User(id="mu2", email="mu2@test.com")
    session.add_all([u1, u2])
    session.commit()

    session.add(Position(user_id="mu1", symbol="TSLA", side=TradeSide.long, qty=5))
    session.add(Position(user_id="mu2", symbol="NVDA", side=TradeSide.long, qty=3))
    session.commit()

    from sqlalchemy import select
    u1_positions = session.scalars(
        select(Position).where(Position.user_id == "mu1")
    ).all()
    assert len(u1_positions) == 1
    assert u1_positions[0].symbol == "TSLA"


def test_position_unique_per_user_symbol(session):
    session.add(User(id="mu3", email="mu3@test.com"))
    session.commit()
    session.add(Position(user_id="mu3", symbol="TSLA", side=TradeSide.long, qty=5))
    session.commit()
    session.add(Position(user_id="mu3", symbol="TSLA", side=TradeSide.long, qty=8))
    with pytest.raises(Exception):
        session.commit()


# ---------------------------------------------------------------------------
# Trade (including vector column)
# ---------------------------------------------------------------------------

def test_create_trade_without_vector(session, user):
    trade = Trade(
        user_id=user.id,
        symbol="MSFT",
        side=TradeSide.long,
        qty=20.0,
        entry_price=400.00,
        exit_price=415.00,
        pnl=300.00,
        exit_reason="trail_stop",
    )
    session.add(trade)
    session.commit()

    fetched = session.get(Trade, trade.id)
    assert fetched.exit_reason == "trail_stop"
    assert float(fetched.pnl) == pytest.approx(300.00)
    assert fetched.setup_vector is None


def test_trade_with_setup_vector(session, user):
    vector = [0.1] * 1536
    trade = Trade(
        user_id=user.id,
        symbol="GOOG",
        side=TradeSide.long,
        qty=5.0,
        setup_vector=vector,
    )
    session.add(trade)
    session.commit()

    fetched = session.get(Trade, trade.id)
    assert fetched.setup_vector is not None
    assert len(fetched.setup_vector) == 1536
    assert fetched.setup_vector[0] == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# RegimeLog
# ---------------------------------------------------------------------------

def test_create_regime_log(session, user):
    regime = RegimeLog(
        user_id=user.id,
        label=RegimeLabel.bullish,
        spy_score=0.72,
        qqq_score=0.68,
        vix=14.2,
    )
    session.add(regime)
    session.commit()

    fetched = session.get(RegimeLog, regime.id)
    assert fetched.label == RegimeLabel.bullish
    assert float(fetched.spy_score) == pytest.approx(0.72)


def test_regime_log_state_vector(session, user):
    vector = [0.5] * 256
    regime = RegimeLog(
        user_id=user.id,
        label=RegimeLabel.neutral,
        state_vector=vector,
    )
    session.add(regime)
    session.commit()

    fetched = session.get(RegimeLog, regime.id)
    assert len(fetched.state_vector) == 256


# ---------------------------------------------------------------------------
# GateLog
# ---------------------------------------------------------------------------

def test_create_gate_log(session, user):
    entry = GateLog(
        user_id=user.id,
        gate="regime_filter",
        symbol="AAPL",
        passed=True,
        reason="bullish regime",
    )
    session.add(entry)
    session.commit()

    fetched = session.get(GateLog, entry.id)
    assert fetched.gate == "regime_filter"
    assert fetched.passed is True
    assert fetched.reason == "bullish regime"


def test_gate_log_failed_gate(session, user):
    entry = GateLog(
        user_id=user.id,
        gate="vix_filter",
        symbol=None,
        passed=False,
        reason="VIX > 30",
    )
    session.add(entry)
    session.commit()

    fetched = session.get(GateLog, entry.id)
    assert fetched.passed is False
    assert fetched.symbol is None


def test_gate_log_multi_entries(session, user):
    from sqlalchemy import select
    gates = ["regime_filter", "vix_filter", "pdt_filter"]
    for gate in gates:
        session.add(GateLog(user_id=user.id, gate=gate, passed=True))
    session.commit()

    count = session.scalar(
        select(GateLog).where(GateLog.user_id == user.id).with_only_columns(
            __import__("sqlalchemy").func.count()
        )
    )
    assert count == 3
