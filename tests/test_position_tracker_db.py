"""DB-backed tests for position_tracker."""
from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.db.models import Base, User
from src import position_tracker as pt


@pytest.fixture()
def db_session_factory(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    import src.db.connection as dbc

    monkeypatch.setenv("POSITION_TRACKER_BACKEND", "db")
    monkeypatch.setattr(dbc, "engine", engine)
    monkeypatch.setattr(dbc, "_SessionLocal", Session)

    with Session() as session:
        session.add(User(id="alice", email="alice-tracker@test.com"))
        session.add(User(id="bob", email="bob-tracker@test.com"))
        session.commit()

    yield Session

    Base.metadata.drop_all(engine)
    engine.dispose()


def test_db_backend_add_and_load(db_session_factory):
    pt.add("AAPL", 10, 150.0, 1.5, user_id="alice")
    data = pt.load("alice")
    assert data["AAPL"]["qty"] == 10
    assert data["AAPL"]["entry_price"] == 150.0
    assert data["AAPL"]["last_buy_price"] == 150.0


def test_db_backend_users_isolated(db_session_factory):
    pt.add("AAPL", 10, 150.0, 1.5, user_id="alice")
    pt.add("AAPL", 5, 200.0, 2.0, user_id="bob")
    assert pt.load("alice")["AAPL"]["qty"] == 10
    assert pt.load("bob")["AAPL"]["qty"] == 5


def test_db_backend_merge_add_shares_updates_weighted_average(db_session_factory):
    pt.add("AAPL", 10, 100.0, 1.5, user_id="alice")
    pt.merge_add_shares("AAPL", 10, 120.0, user_id="alice")
    data = pt.load("alice")
    assert data["AAPL"]["qty"] == 20
    assert data["AAPL"]["entry_price"] == pytest.approx(110.0)
    assert data["AAPL"]["last_buy_price"] == pytest.approx(120.0)


def test_db_backend_update_and_remove(db_session_factory):
    pt.add("AAPL", 10, 150.0, 1.5, user_id="alice")
    pt.update("AAPL", qty=4, partial_taken=True, trail_high=155.0, user_id="alice")
    data = pt.load("alice")
    assert data["AAPL"]["qty"] == 4
    assert data["AAPL"]["partial_taken"] is True
    assert data["AAPL"]["trail_high"] == 155.0

    pt.remove("AAPL", user_id="alice")
    assert pt.load("alice") == {}


def test_db_backend_clear_all(db_session_factory):
    pt.add("AAPL", 10, 150.0, 1.5, user_id="alice")
    pt.add("MSFT", 5, 200.0, 2.0, user_id="alice")
    pt.clear_all(user_id="alice")
    assert pt.load("alice") == {}


def test_db_backend_save_round_trip(db_session_factory):
    pt.save(
        {
            "NVDA": {
                "qty": 3,
                "entry_price": 900.0,
                "entry_time": "2026-03-28T12:00:00+00:00",
                "stop_pct": 1.2,
                "partial_taken": False,
                "trail_high": None,
                "side": "long",
                "last_buy_price": 900.0,
            }
        },
        user_id="alice",
    )
    data = pt.load("alice")
    assert data["NVDA"]["entry_price"] == 900.0
    assert data["NVDA"]["entry_time"].startswith("2026-03-28T12:00:00")


def test_get_tracked_entry_info_uses_user_id_from_tracker_filename(db_session_factory):
    pt.add("AAPL", 10, 150.0, 1.5, user_id="alice")
    row = pt.get_tracked_entry_info(Path("/tmp/positions_alice.json"), "AAPL")
    assert row["qty"] == 10


def test_explicit_base_path_keeps_file_backend(tmp_path, monkeypatch):
    monkeypatch.delenv("POSITION_TRACKER_BACKEND", raising=False)
    monkeypatch.delenv("TIDB_DSN", raising=False)

    tracker_path = tmp_path / "positions_alice.json"
    pt.add("AAPL", 2, 123.0, 1.5, base_path=tracker_path)

    data = pt.load(base_path=tracker_path)
    assert data["AAPL"]["qty"] == 2
