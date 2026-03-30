"""Tests for account-centric repositories introduced in phase 1."""
from __future__ import annotations

import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("TIDB_DSN", "")

from src.db.models import Base, User
from src.db.repos import account_repo


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
def users(session):
    session.add_all([
        User(id="alice", email="alice@test.com"),
        User(id="bob", email="bob@test.com"),
    ])
    session.commit()


def test_create_and_get_broker_account(session, users):
    account_repo.create_broker_account(
        session,
        user_id="alice",
        provider_account_id="acct-alice",
        credentials_ref="secret://alpaca/alice",
    )
    session.commit()

    account = account_repo.get_broker_account_for_user(session, "alice")
    assert account is not None
    assert account.provider == "alpaca"
    assert account.provider_account_id == "acct-alice"


def test_list_active_broker_accounts_filters_inactive(session, users):
    account_repo.create_broker_account(session, user_id="alice", provider_account_id="acct-a")
    account_repo.create_broker_account(
        session,
        user_id="bob",
        provider_account_id="acct-b",
        is_active=False,
    )
    session.commit()

    accounts = account_repo.list_active_broker_accounts(session)
    assert [acct.user_id for acct in accounts] == ["alice"]


def test_upsert_user_settings_creates_and_updates(session, users):
    settings = account_repo.upsert_user_settings(
        session,
        user_id="alice",
        theme="amber",
        timezone="UTC",
    )
    session.commit()
    assert settings.id is not None

    settings = account_repo.upsert_user_settings(
        session,
        user_id="alice",
        theme="night",
        timezone="America/Chicago",
        notifications_enabled=False,
    )
    session.commit()

    fetched = account_repo.get_user_settings(session, "alice")
    assert fetched is not None
    assert fetched.theme == "night"
    assert fetched.timezone == "America/Chicago"
    assert fetched.notifications_enabled is False


def test_upsert_account_settings_creates_and_updates(session, users):
    account = account_repo.create_broker_account(session, user_id="alice", provider_account_id="acct-a")
    session.commit()

    settings = account_repo.upsert_account_settings(
        session,
        broker_account_id=account.id,
        bot_state="running",
        strategy_slug="trend_following",
        risk_profile="balanced",
        max_positions=4,
    )
    session.commit()
    assert settings.id is not None

    settings = account_repo.upsert_account_settings(
        session,
        broker_account_id=account.id,
        trading_enabled=False,
        bot_state="paused",
        strategy_slug="breakout",
        risk_profile="aggressive",
        max_positions=8,
    )
    session.commit()

    fetched = account_repo.get_account_settings(session, account.id)
    assert fetched is not None
    assert fetched.trading_enabled is False
    assert fetched.bot_state == "paused"
    assert fetched.strategy_slug == "breakout"
    assert fetched.risk_profile == "aggressive"
    assert fetched.max_positions == 8
