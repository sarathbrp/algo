"""Tests for UserManager — multi-user loading, validation, and fallback."""

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config_loader import load_config
from src.db.models import Base, BrokerAccount, User
from src.user_manager import UserContext, UserManager, load_users


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def base_config():
    """Load the real default.yaml as the base config."""
    return load_config()


@pytest.fixture()
def users_yaml(tmp_path):
    """Helper that writes a users.yaml to a temp dir and returns the path."""
    def _write(content: str) -> Path:
        p = tmp_path / "users.yaml"
        p.write_text(textwrap.dedent(content))
        return p
    return _write


@pytest.fixture()
def db_runtime(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    import src.db.connection as dbc

    monkeypatch.setattr(dbc, "engine", engine)
    monkeypatch.setattr(dbc, "_SessionLocal", Session)

    yield Session

    Base.metadata.drop_all(engine)
    engine.dispose()


# ---------------------------------------------------------------------------
# Multi-user mode
# ---------------------------------------------------------------------------

class TestMultiUserLoading:

    def test_loads_two_users(self, base_config, users_yaml, monkeypatch):
        monkeypatch.setenv("U1_KEY", "key1")
        monkeypatch.setenv("U1_SECRET", "secret1")
        monkeypatch.setenv("U2_KEY", "key2")
        monkeypatch.setenv("U2_SECRET", "secret2")

        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: U1_KEY
                alpaca_secret_env: U1_SECRET
                paper: true
              - id: bob
                alpaca_key_env: U2_KEY
                alpaca_secret_env: U2_SECRET
                paper: false
        """)

        mgr = UserManager(base_config, users_path=path)

        assert mgr.multi_user is True
        assert len(mgr.list_users()) == 2
        alice = mgr.get_user("alice")
        assert alice.api_key == "key1"
        assert alice.paper is True
        bob = mgr.get_user("bob")
        assert bob.api_key == "key2"
        assert bob.paper is False

    def test_overrides_merged(self, base_config, users_yaml, monkeypatch):
        monkeypatch.setenv("U1_KEY", "key1")
        monkeypatch.setenv("U1_SECRET", "secret1")

        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: U1_KEY
                alpaca_secret_env: U1_SECRET
                paper: true
                overrides:
                  position_sizing:
                    max_position_dollar_cap: 999
        """)

        mgr = UserManager(base_config, users_path=path)
        alice = mgr.get_user("alice")

        # Overridden value
        assert alice.config["position_sizing"]["max_position_dollar_cap"] == 999
        # Non-overridden value preserved from base
        assert alice.config["position_sizing"]["risk_per_trade_pct"] == base_config["position_sizing"]["risk_per_trade_pct"]

    def test_overrides_do_not_mutate_base(self, base_config, users_yaml, monkeypatch):
        monkeypatch.setenv("U1_KEY", "k")
        monkeypatch.setenv("U1_SECRET", "s")
        original_cap = base_config["position_sizing"]["max_position_dollar_cap"]

        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: U1_KEY
                alpaca_secret_env: U1_SECRET
                paper: true
                overrides:
                  position_sizing:
                    max_position_dollar_cap: 1
        """)

        UserManager(base_config, users_path=path)
        assert base_config["position_sizing"]["max_position_dollar_cap"] == original_cap

    def test_user_order_preserved(self, base_config, users_yaml, monkeypatch):
        for i in range(1, 4):
            monkeypatch.setenv(f"K{i}", f"key{i}")
            monkeypatch.setenv(f"S{i}", f"sec{i}")

        path = users_yaml("""\
            users:
              - id: charlie
                alpaca_key_env: K1
                alpaca_secret_env: S1
                paper: true
              - id: alice
                alpaca_key_env: K2
                alpaca_secret_env: S2
                paper: true
              - id: bob
                alpaca_key_env: K3
                alpaca_secret_env: S3
                paper: true
        """)

        mgr = UserManager(base_config, users_path=path)
        ids = [u.user_id for u in mgr.list_users()]
        assert ids == ["charlie", "alice", "bob"]


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestValidation:

    def test_missing_required_field(self, base_config, users_yaml):
        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: X
                paper: true
        """)
        with pytest.raises(ValueError, match="missing required field.*alpaca_secret_env"):
            UserManager(base_config, users_path=path)

    def test_duplicate_user_id(self, base_config, users_yaml, monkeypatch):
        monkeypatch.setenv("K", "k")
        monkeypatch.setenv("S", "s")

        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: K
                alpaca_secret_env: S
                paper: true
              - id: alice
                alpaca_key_env: K
                alpaca_secret_env: S
                paper: false
        """)
        with pytest.raises(ValueError, match="Duplicate user id 'alice'"):
            UserManager(base_config, users_path=path)

    def test_empty_user_id(self, base_config, users_yaml):
        path = users_yaml("""\
            users:
              - id: ""
                alpaca_key_env: X
                alpaca_secret_env: Y
                paper: true
        """)
        with pytest.raises(ValueError, match="non-empty string"):
            UserManager(base_config, users_path=path)

    def test_paper_not_bool(self, base_config, users_yaml):
        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: X
                alpaca_secret_env: Y
                paper: "yes"
        """)
        with pytest.raises(ValueError, match="must be a boolean"):
            UserManager(base_config, users_path=path)

    def test_missing_env_var(self, base_config, users_yaml, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: NONEXISTENT_VAR
                alpaca_secret_env: ALSO_MISSING
                paper: true
        """)
        with pytest.raises(EnvironmentError, match="NONEXISTENT_VAR"):
            UserManager(base_config, users_path=path)


# ---------------------------------------------------------------------------
# Single-user fallback
# ---------------------------------------------------------------------------

class TestSingleUserFallback:

    def test_no_users_yaml(self, base_config, tmp_path):
        """When users.yaml doesn't exist, fall back to single default user."""
        nonexistent = tmp_path / "users.yaml"
        mgr = UserManager(base_config, users_path=nonexistent)

        assert mgr.multi_user is False
        users = mgr.list_users()
        assert len(users) == 1
        assert users[0].user_id == "default"
        assert users[0].paper == base_config.get("broker", {}).get("paper", True)

    def test_empty_users_list(self, base_config, users_yaml):
        """Empty users list in YAML also falls back to single-user."""
        path = users_yaml("""\
            users: []
        """)
        mgr = UserManager(base_config, users_path=path)
        assert mgr.multi_user is False
        assert len(mgr.list_users()) == 1
        assert mgr.list_users()[0].user_id == "default"

    def test_fallback_config_is_base(self, base_config, tmp_path):
        """In single-user mode, config should be the unmodified base config."""
        mgr = UserManager(base_config, users_path=tmp_path / "nope.yaml")
        default = mgr.get_user("default")
        assert default.config is base_config

    def test_get_unknown_user_raises(self, base_config, tmp_path):
        mgr = UserManager(base_config, users_path=tmp_path / "nope.yaml")
        with pytest.raises(KeyError, match="Unknown user_id 'bob'"):
            mgr.get_user("bob")


class TestDbRuntimeLoading:

    def test_loads_active_users_from_db(self, base_config, db_runtime):
        with db_runtime() as session:
            session.add_all([
                User(
                    id="alice",
                    email="alice@test.com",
                    alpaca_key_env="raw-alice-key",
                    alpaca_secret_env="raw-alice-secret",
                ),
                User(
                    id="bob",
                    email="bob@test.com",
                    alpaca_key_env="raw-bob-key",
                    alpaca_secret_env="raw-bob-secret",
                ),
            ])
            session.commit()
            session.add_all([
                BrokerAccount(user_id="alice", provider="alpaca", provider_account_id="acct-a", paper=True, is_active=True),
                BrokerAccount(user_id="bob", provider="alpaca", provider_account_id="acct-b", paper=False, is_active=True),
            ])
            session.commit()

        mgr = UserManager(base_config, runtime_source="db")
        users = mgr.list_users()
        assert [u.user_id for u in users] == ["alice", "bob"]
        assert mgr.multi_user is True
        assert mgr.get_user("alice").api_key == "raw-alice-key"
        assert mgr.get_user("bob").paper is False

    def test_default_runtime_prefers_db_accounts(self, base_config, db_runtime):
        with db_runtime() as session:
            session.add(
                User(
                    id="alice",
                    email="alice@test.com",
                    alpaca_key_env="raw-alice-key",
                    alpaca_secret_env="raw-alice-secret",
                )
            )
            session.commit()
            session.add(
                BrokerAccount(
                    user_id="alice",
                    provider="alpaca",
                    provider_account_id="acct-a",
                    paper=True,
                    is_active=True,
                )
            )
            session.commit()

        mgr = UserManager(base_config)
        assert [u.user_id for u in mgr.list_users()] == ["alice"]

    def test_db_runtime_resolves_env_references(self, base_config, db_runtime, monkeypatch):
        monkeypatch.setenv("ALICE_KEY_ENV", "resolved-key")
        monkeypatch.setenv("ALICE_SECRET_ENV", "resolved-secret")
        with db_runtime() as session:
            session.add(
                User(
                    id="alice",
                    email="alice@test.com",
                    alpaca_key_env="ALICE_KEY_ENV",
                    alpaca_secret_env="ALICE_SECRET_ENV",
                )
            )
            session.commit()
            session.add(BrokerAccount(user_id="alice", provider="alpaca", provider_account_id="acct-a", paper=True, is_active=True))
            session.commit()

        mgr = UserManager(base_config, runtime_source="db")
        alice = mgr.get_user("alice")
        assert alice.api_key == "resolved-key"
        assert alice.api_secret == "resolved-secret"

    def test_auto_runtime_falls_back_to_yaml_when_db_empty(self, base_config, users_yaml, db_runtime, monkeypatch):
        monkeypatch.setenv("U1_KEY", "key1")
        monkeypatch.setenv("U1_SECRET", "secret1")
        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: U1_KEY
                alpaca_secret_env: U1_SECRET
                paper: true
        """)

        mgr = UserManager(base_config, users_path=path, runtime_source="auto")
        assert mgr.list_users()[0].user_id == "alice"

    def test_db_runtime_requires_credentials(self, base_config, db_runtime):
        with db_runtime() as session:
            session.add(User(id="alice", email="alice@test.com"))
            session.commit()
            session.add(BrokerAccount(user_id="alice", provider="alpaca", provider_account_id="acct-a", paper=True, is_active=True))
            session.commit()

        with pytest.raises(EnvironmentError, match="API key"):
            UserManager(base_config, runtime_source="db")


def test_load_users_helper_uses_yaml_runtime(base_config, users_yaml, monkeypatch):
    monkeypatch.setenv("U1_KEY", "key1")
    monkeypatch.setenv("U1_SECRET", "secret1")
    path = users_yaml("""\
        users:
          - id: alice
            alpaca_key_env: U1_KEY
            alpaca_secret_env: U1_SECRET
            paper: true
    """)

    users = load_users(users_path=path, runtime_source="yaml")
    assert len(users) == 1
    assert users[0].user_id == "alice"


# ---------------------------------------------------------------------------
# Broker caching (get_broker)
# ---------------------------------------------------------------------------

class TestGetBroker:

    def _patch_broker(self):
        """Context manager to mock AlpacaBroker where get_broker imports it."""
        return patch.dict(
            "sys.modules",
            {"src.brokers.alpaca_client": MagicMock()},
        )

    def test_get_broker_creates_and_caches(self, base_config, users_yaml, monkeypatch):
        monkeypatch.setenv("K1", "key1")
        monkeypatch.setenv("S1", "secret1")

        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: K1
                alpaca_secret_env: S1
                paper: true
        """)

        mgr = UserManager(base_config, users_path=path)

        mock_cls = MagicMock()
        mock_instance = MagicMock(name="broker_alice")
        mock_cls.return_value = mock_instance

        # Inject a fake broker directly into the cache to test caching
        mgr._brokers["alice"] = mock_instance
        broker1 = mgr.get_broker("alice")
        broker2 = mgr.get_broker("alice")

        assert broker1 is broker2
        assert broker1 is mock_instance

    def test_get_broker_passes_user_credentials(self, base_config, users_yaml, monkeypatch):
        monkeypatch.setenv("K1", "alice_key")
        monkeypatch.setenv("S1", "alice_secret")

        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: K1
                alpaca_secret_env: S1
                paper: true
        """)

        mgr = UserManager(base_config, users_path=path)
        mock_cls = MagicMock(name="AlpacaBroker")

        # Patch the import that happens inside get_broker
        import src.brokers.alpaca_client as broker_mod
        original_cls = getattr(broker_mod, "AlpacaBroker", None)
        broker_mod.AlpacaBroker = mock_cls
        try:
            mgr.get_broker("alice")
            mock_cls.assert_called_once_with(
                config=mgr.get_user("alice").config,
                api_key="alice_key",
                secret="alice_secret",
                paper=True,
            )
        finally:
            if original_cls is not None:
                broker_mod.AlpacaBroker = original_cls

    def test_get_broker_separate_instances_per_user(self, base_config, users_yaml, monkeypatch):
        monkeypatch.setenv("K1", "k1")
        monkeypatch.setenv("S1", "s1")
        monkeypatch.setenv("K2", "k2")
        monkeypatch.setenv("S2", "s2")

        path = users_yaml("""\
            users:
              - id: alice
                alpaca_key_env: K1
                alpaca_secret_env: S1
                paper: true
              - id: bob
                alpaca_key_env: K2
                alpaca_secret_env: S2
                paper: false
        """)

        mgr = UserManager(base_config, users_path=path)

        import src.brokers.alpaca_client as broker_mod
        mock_cls = MagicMock(name="AlpacaBroker")
        mock_cls.side_effect = [MagicMock(name="broker_alice"), MagicMock(name="broker_bob")]
        original_cls = getattr(broker_mod, "AlpacaBroker", None)
        broker_mod.AlpacaBroker = mock_cls
        try:
            broker_alice = mgr.get_broker("alice")
            broker_bob = mgr.get_broker("bob")
            assert broker_alice is not broker_bob
            assert mock_cls.call_count == 2
        finally:
            if original_cls is not None:
                broker_mod.AlpacaBroker = original_cls

    def test_get_broker_unknown_user_raises(self, base_config, tmp_path):
        mgr = UserManager(base_config, users_path=tmp_path / "nope.yaml")
        with pytest.raises(KeyError, match="Unknown user_id 'bob'"):
            mgr.get_broker("bob")
