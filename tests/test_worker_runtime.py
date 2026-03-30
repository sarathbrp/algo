"""Tests for worker health and reconciliation helpers."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, User
from src.db.repos import portfolio_repo, worker_repo
from src.worker import health as worker_health
from src.worker.control import (
    BOT_STATE_PAUSED,
    BOT_STATE_RUNNING,
    BOT_STATE_STOPPED,
    can_manage_open_positions,
    can_open_new_trades,
    describe_bot_state,
    normalize_bot_state,
)
from src.worker.reconcile import reconcile_positions


@pytest.fixture()
def db_runtime(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    import src.db.connection as dbc

    monkeypatch.setattr(dbc, "engine", engine)
    monkeypatch.setattr(dbc, "_SessionLocal", Session)

    with Session() as session:
        session.add_all([
            User(id="alice", email="alice-worker@test.com"),
            User(id="bob", email="bob-worker@test.com"),
        ])
        session.commit()

    yield Session

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_worker_repo_upsert_status(db_runtime):
    with db_runtime() as session:
        worker_repo.upsert_worker_status(session, worker_name="alpaca_loop", status="starting")
        session.commit()
        worker_repo.upsert_worker_status(session, worker_name="alpaca_loop", status="running", current_user_id="alice")
        session.commit()

        row = worker_repo.get_worker_status(session, "alpaca_loop")
        assert row is not None
        assert row.status == "running"
        assert row.current_user_id == "alice"


def test_worker_health_helpers_persist(db_runtime):
    worker_health.mark_starting("alpaca_loop")
    worker_health.mark_running("alpaca_loop", current_user_id="alice")
    worker_health.mark_error("alpaca_loop", "boom", current_user_id="alice")
    worker_health.mark_stopped("alpaca_loop")

    with db_runtime() as session:
        row = worker_repo.get_worker_status(session, "alpaca_loop")
        assert row is not None
        assert row.status == "stopped"


def test_bot_control_state_helpers():
    assert normalize_bot_state("running") == BOT_STATE_RUNNING
    assert normalize_bot_state("paused") == BOT_STATE_PAUSED
    assert normalize_bot_state("stopped") == BOT_STATE_STOPPED
    assert normalize_bot_state(None, trading_enabled=False) == BOT_STATE_PAUSED
    assert can_open_new_trades(BOT_STATE_RUNNING) is True
    assert can_open_new_trades(BOT_STATE_PAUSED) is False
    assert can_manage_open_positions(BOT_STATE_PAUSED) is True
    assert can_manage_open_positions(BOT_STATE_STOPPED) is False
    assert "open positions" in describe_bot_state(BOT_STATE_PAUSED).lower()


def test_reconcile_positions_creates_updates_and_removes(db_runtime):
    with db_runtime() as session:
        portfolio_repo.upsert_position(
            session,
            user_id="alice",
            symbol="AAPL",
            qty=1.0,
            avg_entry_price=100.0,
            last_buy_price=100.0,
            stop_pct=1.5,
            entered_at=datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc),
        )
        portfolio_repo.upsert_position(
            session,
            user_id="alice",
            symbol="OLD",
            qty=2.0,
            avg_entry_price=50.0,
            last_buy_price=50.0,
            stop_pct=2.0,
            entered_at=datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc),
        )
        session.commit()

    result = reconcile_positions(
        "alice",
        [
            {
                "symbol": "AAPL",
                "qty": 3,
                "side": "long",
                "market_value": 330.0,
                "cost_basis": 300.0,
                "unrealized_pl": 30.0,
            },
            {
                "symbol": "MSFT",
                "qty": 2,
                "side": "long",
                "market_value": 420.0,
                "cost_basis": 400.0,
                "unrealized_pl": 20.0,
            },
        ],
    )

    assert result.created == 1
    assert result.updated == 1
    assert result.removed == 1
    assert result.total_broker_positions == 2

    with db_runtime() as session:
        positions = {p.symbol: p for p in portfolio_repo.get_positions(session, "alice")}
        assert set(positions) == {"AAPL", "MSFT"}
        assert float(positions["AAPL"].qty) == pytest.approx(3.0)
        assert float(positions["AAPL"].avg_entry_price) == pytest.approx(100.0)
        assert float(positions["AAPL"].current_price) == pytest.approx(110.0)
        assert float(positions["MSFT"].current_price) == pytest.approx(210.0)


def test_reconcile_positions_handles_short_side_and_preserves_stop(db_runtime):
    with db_runtime() as session:
        portfolio_repo.upsert_position(
            session,
            user_id="bob",
            symbol="TSLA",
            side="short",
            qty=1.0,
            avg_entry_price=250.0,
            last_buy_price=250.0,
            stop_pct=4.0,
            entered_at=datetime(2026, 3, 28, 12, 0, tzinfo=timezone.utc),
        )
        session.commit()

    reconcile_positions(
        "bob",
        [
            {
                "symbol": "TSLA",
                "qty": -2,
                "side": "weird-side",
                "market_value": -480.0,
                "cost_basis": -500.0,
                "unrealized_pl": 20.0,
            }
        ],
    )

    with db_runtime() as session:
        pos = portfolio_repo.get_positions(session, "bob")[0]
        assert pos.side.value == "short"
        assert float(pos.qty) == pytest.approx(2.0)
        assert float(pos.stop_pct) == pytest.approx(4.0)
