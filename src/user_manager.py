"""Multi-user account management for the trading engine.

Runtime loading defaults to DB-backed users + broker accounts. YAML and
single-user environment-variable loading remain as explicit compatibility
fallbacks for local development and migration.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.config_loader import deep_merge, load_config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UserContext:
    """Immutable context for a single trading user."""

    user_id: str
    api_key: str
    api_secret: str
    paper: bool
    config: dict[str, Any] = field(repr=False)  # full merged config


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_REQUIRED_USER_FIELDS = {"id", "alpaca_key_env", "alpaca_secret_env", "paper"}


def _validate_user_entry(entry: dict, index: int) -> None:
    """Raise ``ValueError`` if a user entry is missing required fields."""
    missing = _REQUIRED_USER_FIELDS - set(entry.keys())
    if missing:
        raise ValueError(
            f"users[{index}] is missing required field(s): {', '.join(sorted(missing))}"
        )
    if not isinstance(entry["id"], str) or not entry["id"].strip():
        raise ValueError(f"users[{index}].id must be a non-empty string")
    if not isinstance(entry["paper"], bool):
        raise ValueError(
            f"users[{index}].paper must be a boolean (true/false), "
            f"got {type(entry['paper']).__name__}"
        )


def _resolve_env(var_name: str, user_id: str, field_label: str) -> str:
    """Resolve an environment variable or raise with a helpful message."""
    value = os.environ.get(var_name)
    if not value:
        raise EnvironmentError(
            f"Environment variable '{var_name}' (for user '{user_id}' "
            f"{field_label}) is not set or is empty."
        )
    return value


def _resolve_secret_value(raw_or_env: str | None, user_id: str, field_label: str) -> str:
    """Resolve a raw credential or an env-var reference.

    If *raw_or_env* matches an existing environment variable, that value is used.
    Otherwise the raw value itself is returned. Empty values raise.
    """
    value = (raw_or_env or "").strip()
    if not value:
        raise EnvironmentError(
            f"Credential for user '{user_id}' {field_label} is not set or is empty."
        )
    return os.environ.get(value, value)


# ---------------------------------------------------------------------------
# UserManager
# ---------------------------------------------------------------------------

class UserManager:
    """Load and manage multi-user trading contexts.

    Parameters
    ----------
    base_config : dict
        The base configuration loaded from ``config/default.yaml``.
    users_path : str | Path | None
        Path to ``users.yaml``.  When *None*, the manager resolves the
        default location relative to the project root
        (``config/users.yaml``).
    """

    def __init__(
        self,
        base_config: dict[str, Any],
        users_path: str | Path | None = None,
        runtime_source: str = "db",
    ) -> None:
        self._base_config = base_config
        self._users_path = self._resolve_users_path(users_path)
        self._users: dict[str, UserContext] = {}
        self._brokers: dict[str, Any] = {}
        self._multi_user: bool = False
        self._runtime_source = (runtime_source or "auto").strip().lower()
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def multi_user(self) -> bool:
        """True when running in multi-user mode."""
        return self._multi_user

    def list_users(self) -> list[UserContext]:
        """Return all loaded users (order preserved from YAML)."""
        return list(self._users.values())

    def get_user(self, user_id: str) -> UserContext:
        """Return the ``UserContext`` for *user_id* or raise ``KeyError``."""
        try:
            return self._users[user_id]
        except KeyError:
            available = ", ".join(self._users) or "(none)"
            raise KeyError(
                f"Unknown user_id '{user_id}'. Available: {available}"
            ) from None

    def reload_user(self, user_id: str) -> None:
        """Re-read a user's credentials from DB and clear their cached broker.

        Called by the worker when it detects a Redis reload signal after the
        user updates their Alpaca keys in Settings.
        """
        from src.db import get_session
        from src.db.repos import account_repo, user_repo

        try:
            with get_session() as session:
                account = account_repo.get_broker_account_for_user(session, user_id)
                if account is None:
                    logger.warning("[%s] reload_user: no broker account found", user_id)
                    return
                user = user_repo.get_by_id(session, user_id)
                if user is None:
                    logger.warning("[%s] reload_user: user not found", user_id)
                    return
                self._users[user_id] = self._build_user_context_from_db(user, account)
                self._brokers.pop(user_id, None)
            logger.info("[%s] Credentials reloaded from DB", user_id)
        except Exception:
            logger.exception("[%s] Failed to reload user credentials", user_id)

    def get_broker(self, user_id: str) -> Any:
        """Return a cached ``AlpacaBroker`` for *user_id*.

        The broker is lazily created on first call and reused for all
        subsequent requests.  Each user gets their own isolated broker
        instance with their own credentials.
        """
        if user_id in self._brokers:
            return self._brokers[user_id]

        from src.brokers.alpaca_client import AlpacaBroker

        user = self.get_user(user_id)
        broker = AlpacaBroker(
            config=user.config,
            api_key=user.api_key,
            secret=user.api_secret,
            paper=user.paper,
        )
        self._brokers[user_id] = broker
        logger.info(
            "[%s] Broker initialised (paper=%s)", user_id, user.paper
        )
        return broker

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_users_path(users_path: str | Path | None) -> Path:
        if users_path is not None:
            return Path(users_path)
        return Path(__file__).resolve().parent.parent / "config" / "users.yaml"

    def _load(self) -> None:
        if self._runtime_source in ("auto", "db") and self._load_from_db():
            return
        if self._runtime_source == "db":
            logger.info(
                "No active broker accounts in DB yet — "
                "users can connect via Settings. Falling back to config."
            )

        if not self._users_path.exists():
            logger.info(
                "No users.yaml found at %s — running in single-user mode.",
                self._users_path,
            )
            self._load_single_user_fallback()
            return

        raw = self._read_users_yaml()
        entries = raw.get("users")
        if not entries:
            logger.warning(
                "users.yaml found but 'users' list is empty — "
                "falling back to single-user mode."
            )
            self._load_single_user_fallback()
            return

        self._multi_user = True
        seen_ids: set[str] = set()
        for idx, entry in enumerate(entries):
            _validate_user_entry(entry, idx)
            uid = entry["id"]
            if uid in seen_ids:
                raise ValueError(f"Duplicate user id '{uid}' in users.yaml")
            seen_ids.add(uid)
            self._users[uid] = self._build_user_context(entry)

        logger.info(
            "Loaded %d user(s) from %s: %s",
            len(self._users),
            self._users_path,
            ", ".join(
                f"{u.user_id} ({'paper' if u.paper else 'LIVE'})"
                for u in self._users.values()
            ),
        )

    def _load_from_db(self) -> bool:
        """Load active runtime users from DB. Returns True if any were loaded."""
        from src.db import get_session
        from src.db.repos import account_repo, user_repo

        try:
            with get_session() as session:
                accounts = account_repo.list_active_broker_accounts(session, provider="alpaca")
                if not accounts:
                    return False

                for account in accounts:
                    user = user_repo.get_by_id(session, account.user_id)
                    if user is None:
                        logger.warning(
                            "Skipping broker account %s because user %s does not exist",
                            account.id,
                            account.user_id,
                        )
                        continue
                    self._users[user.id] = self._build_user_context_from_db(user, account)
        except EnvironmentError:
            raise
        except Exception:
            logger.exception("Failed to load runtime users from DB")
            return False

        self._multi_user = len(self._users) > 1
        if self._users:
            logger.info(
                "Loaded %d active user(s) from DB: %s",
                len(self._users),
                ", ".join(
                    f"{u.user_id} ({'paper' if u.paper else 'LIVE'})"
                    for u in self._users.values()
                ),
            )
            return True
        return False

    def _read_users_yaml(self) -> dict:
        with open(self._users_path) as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            raise ValueError(
                f"users.yaml must be a YAML mapping, got {type(data).__name__}"
            )
        return data

    def _build_user_context(self, entry: dict) -> UserContext:
        uid = entry["id"]
        api_key = _resolve_env(entry["alpaca_key_env"], uid, "API key")
        api_secret = _resolve_env(entry["alpaca_secret_env"], uid, "API secret")
        paper = entry["paper"]

        overrides = entry.get("overrides") or {}
        merged_config = deep_merge(self._base_config, overrides)

        return UserContext(
            user_id=uid,
            api_key=api_key,
            api_secret=api_secret,
            paper=paper,
            config=merged_config,
        )

    def _build_user_context_from_db(self, user: Any, account: Any) -> UserContext:
        merged_config = deep_merge(
            self._base_config,
            {"broker": {"paper": bool(account.paper)}},
        )
        if getattr(user, "risk_profile", None):
            merged_config.setdefault("account_runtime", {})["risk_profile"] = user.risk_profile

        return UserContext(
            user_id=user.id,
            api_key=_resolve_secret_value(getattr(user, "alpaca_key_env", None), user.id, "API key"),
            api_secret=_resolve_secret_value(getattr(user, "alpaca_secret_env", None), user.id, "API secret"),
            paper=bool(account.paper),
            config=merged_config,
        )

    def _load_single_user_fallback(self) -> None:
        """Create a single 'default' user from standard env vars.

        In single-user mode we try to resolve credentials from the
        environment.  If the env vars are not set we still create the
        context with empty strings — the broker will raise its own error
        at connection time, preserving the existing behaviour.
        """
        self._multi_user = False

        paper = self._base_config.get("broker", {}).get("paper", True)

        if paper:
            api_key = os.environ.get("APCA_API_KEY_ID", "")
            api_secret = os.environ.get("APCA_API_SECRET_KEY", "")
        else:
            api_key = os.environ.get("ALPACA_LIVE_API_KEY_ID", "")
            api_secret = os.environ.get("ALPACA_LIVE_API_SECRET_KEY", "")

        self._users["default"] = UserContext(
            user_id="default",
            api_key=api_key,
            api_secret=api_secret,
            paper=paper,
            config=self._base_config,
        )
        logger.info(
            "Single-user mode: user_id='default', paper=%s", paper
        )


def load_users(
    *,
    config_path: str | Path | None = None,
    users_path: str | Path | None = None,
    runtime_source: str = "db",
) -> list[UserContext]:
    """Convenience helper for scripts that need resolved runtime users."""
    base_config = load_config(config_path)
    return UserManager(
        base_config,
        users_path=users_path,
        runtime_source=runtime_source,
    ).list_users()
