"""Tests for loop_helpers — multi-user loop context, init, error isolation."""

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.loop_helpers import (
    UserLoopContext,
    init_user_contexts,
    log_startup_summary,
    parse_cli_args,
    run_user_pass,
)
from src.user_manager import UserContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_user_manager():
    """Return a mock UserManager with two users."""
    mgr = MagicMock()
    mgr.multi_user = True

    alice_ctx = UserContext(
        user_id="alice",
        api_key="k1",
        api_secret="s1",
        paper=True,
        config={"broker": {"firm": "alpaca"}},
    )
    bob_ctx = UserContext(
        user_id="bob",
        api_key="k2",
        api_secret="s2",
        paper=False,
        config={"broker": {"firm": "alpaca"}},
    )

    mgr.list_users.return_value = [alice_ctx, bob_ctx]
    mgr.get_user.side_effect = lambda uid: {"alice": alice_ctx, "bob": bob_ctx}[uid]
    mgr.get_broker.side_effect = lambda uid: MagicMock(name=f"broker_{uid}")

    return mgr


@pytest.fixture()
def single_user_manager():
    """Return a mock UserManager with single default user."""
    mgr = MagicMock()
    mgr.multi_user = False

    default_ctx = UserContext(
        user_id="default",
        api_key="k",
        api_secret="s",
        paper=True,
        config={"broker": {"firm": "alpaca"}},
    )

    mgr.list_users.return_value = [default_ctx]
    mgr.get_user.side_effect = lambda uid: default_ctx if uid == "default" else (_ for _ in ()).throw(KeyError(uid))
    mgr.get_broker.return_value = MagicMock(name="broker_default")

    return mgr


# ---------------------------------------------------------------------------
# UserLoopContext
# ---------------------------------------------------------------------------

class TestUserLoopContext:

    def test_fields(self):
        ctx = UserLoopContext(
            user_id="alice",
            user_ctx=MagicMock(),
            broker=MagicMock(),
            engine=MagicMock(),
            config={"broker": {}},
            paper=True,
            data_dir=Path("/tmp/data"),
        )
        assert ctx.user_id == "alice"
        assert ctx.paper is True
        assert ctx.data_dir == Path("/tmp/data")

    def test_data_dir_defaults_none(self):
        ctx = UserLoopContext(
            user_id="bob",
            user_ctx=MagicMock(),
            broker=MagicMock(),
            engine=MagicMock(),
            config={},
            paper=False,
        )
        assert ctx.data_dir is None


# ---------------------------------------------------------------------------
# init_user_contexts
# ---------------------------------------------------------------------------

class TestInitUserContexts:

    @patch("src.loop_helpers.TradingEngine")
    def test_creates_contexts_for_all_users(self, MockEngine, mock_user_manager):
        MockEngine.return_value = MagicMock(name="engine")
        contexts = init_user_contexts(mock_user_manager, project_root=Path("/tmp/proj"))
        assert len(contexts) == 2
        assert contexts[0].user_id == "alice"
        assert contexts[1].user_id == "bob"
        assert contexts[0].paper is True
        assert contexts[1].paper is False
        assert contexts[0].data_dir == Path("/tmp/proj/data")

    @patch("src.loop_helpers.TradingEngine")
    def test_user_filter(self, MockEngine, mock_user_manager):
        MockEngine.return_value = MagicMock(name="engine")
        contexts = init_user_contexts(
            mock_user_manager,
            project_root=Path("/tmp/proj"),
            user_filter="alice",
        )
        assert len(contexts) == 1
        assert contexts[0].user_id == "alice"

    @patch("src.loop_helpers.TradingEngine")
    def test_user_filter_unknown_raises(self, MockEngine, mock_user_manager):
        mock_user_manager.get_user.side_effect = KeyError("Unknown user_id 'charlie'")
        with pytest.raises(KeyError, match="charlie"):
            init_user_contexts(
                mock_user_manager,
                project_root=Path("/tmp/proj"),
                user_filter="charlie",
            )

    @patch("src.loop_helpers.TradingEngine")
    def test_single_user_fallback(self, MockEngine, single_user_manager):
        MockEngine.return_value = MagicMock(name="engine")
        contexts = init_user_contexts(single_user_manager, project_root=Path("/tmp/proj"))
        assert len(contexts) == 1
        assert contexts[0].user_id == "default"

    @patch("src.loop_helpers.TradingEngine")
    def test_broker_failure_skips_user(self, MockEngine, mock_user_manager, caplog):
        MockEngine.return_value = MagicMock(name="engine")
        # Alice's broker raises, Bob's works
        mock_user_manager.get_broker.side_effect = [
            RuntimeError("auth failed"),
            MagicMock(name="broker_bob"),
        ]
        with caplog.at_level(logging.ERROR):
            contexts = init_user_contexts(mock_user_manager, project_root=Path("/tmp/proj"))
        assert len(contexts) == 1
        assert contexts[0].user_id == "bob"

    @patch("src.loop_helpers.TradingEngine")
    def test_engine_failure_skips_user(self, MockEngine, mock_user_manager, caplog):
        call_count = [0]
        def side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ValueError("bad config")
            return MagicMock(name="engine")

        MockEngine.side_effect = side_effect
        with caplog.at_level(logging.ERROR):
            contexts = init_user_contexts(mock_user_manager, project_root=Path("/tmp/proj"))
        assert len(contexts) == 1
        assert contexts[0].user_id == "bob"


# ---------------------------------------------------------------------------
# log_startup_summary
# ---------------------------------------------------------------------------

class TestLogStartupSummary:

    def test_logs_users(self, caplog):
        ctx1 = UserLoopContext(
            user_id="alice", user_ctx=MagicMock(), broker=MagicMock(),
            engine=MagicMock(), config={}, paper=True,
        )
        ctx2 = UserLoopContext(
            user_id="bob", user_ctx=MagicMock(), broker=MagicMock(),
            engine=MagicMock(), config={}, paper=False,
        )
        with caplog.at_level(logging.INFO):
            log_startup_summary([ctx1, ctx2])
        assert "2 user(s)" in caplog.text
        assert "alice" in caplog.text
        assert "PAPER" in caplog.text
        assert "bob" in caplog.text
        assert "LIVE" in caplog.text

    def test_logs_warning_empty(self, caplog):
        with caplog.at_level(logging.WARNING):
            log_startup_summary([])
        assert "No user contexts" in caplog.text


# ---------------------------------------------------------------------------
# run_user_pass — error isolation
# ---------------------------------------------------------------------------

class TestRunUserPass:

    def _ctx(self, uid="alice"):
        return UserLoopContext(
            user_id=uid, user_ctx=MagicMock(), broker=MagicMock(),
            engine=MagicMock(), config={}, paper=True,
        )

    def test_success_returns_true(self):
        ctx = self._ctx()
        callback = MagicMock()
        result = run_user_pass(ctx, callback)
        assert result is True
        callback.assert_called_once_with(ctx)

    def test_passes_kwargs(self):
        ctx = self._ctx()
        callback = MagicMock()
        run_user_pass(ctx, callback, dt="now", verbose=True)
        callback.assert_called_once_with(ctx, dt="now", verbose=True)

    def test_exception_returns_false(self, caplog):
        ctx = self._ctx()
        callback = MagicMock(side_effect=RuntimeError("broker down"))
        with caplog.at_level(logging.ERROR):
            result = run_user_pass(ctx, callback)
        assert result is False
        assert "alice" in caplog.text
        assert "Error during trading pass" in caplog.text

    def test_exception_does_not_propagate(self):
        ctx = self._ctx()
        callback = MagicMock(side_effect=Exception("fatal"))
        # Should NOT raise
        result = run_user_pass(ctx, callback)
        assert result is False

    def test_multiple_users_one_fails(self):
        """Simulate iterating over users: one fails, other succeeds."""
        ctxs = [self._ctx("alice"), self._ctx("bob")]
        results = []
        for ctx in ctxs:
            if ctx.user_id == "alice":
                cb = MagicMock(side_effect=RuntimeError("alice broker down"))
            else:
                cb = MagicMock()
            results.append(run_user_pass(ctx, cb))
        assert results == [False, True]


# ---------------------------------------------------------------------------
# parse_cli_args
# ---------------------------------------------------------------------------

class TestParseCLIArgs:

    def test_defaults(self):
        args = parse_cli_args([])
        assert args.live is False
        assert args.paper is False
        assert args.user is None
        assert args.verbose is False

    def test_live_flag(self):
        args = parse_cli_args(["--live"])
        assert args.live is True

    def test_paper_flag(self):
        args = parse_cli_args(["--paper"])
        assert args.paper is True

    def test_user_flag(self):
        args = parse_cli_args(["--user", "alice"])
        assert args.user == "alice"

    def test_verbose_short(self):
        args = parse_cli_args(["-v"])
        assert args.verbose is True

    def test_combined_flags(self):
        args = parse_cli_args(["--paper", "--user", "bob", "-v"])
        assert args.paper is True
        assert args.user == "bob"
        assert args.verbose is True
