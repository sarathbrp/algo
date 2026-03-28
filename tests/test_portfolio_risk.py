"""Tests for portfolio_risk — single-user and multi-user wrappers."""

from datetime import date, datetime, timezone

import pytest

from src.portfolio_risk import (
    MultiUserPortfolioRiskManager,
    PortfolioRiskManager,
    PortfolioRiskState,
)


# ---------------------------------------------------------------------------
# PortfolioRiskState defaults
# ---------------------------------------------------------------------------

class TestPortfolioRiskState:

    def test_defaults(self):
        s = PortfolioRiskState()
        assert s.equity_curve == []
        assert s.peak_equity == 0.0
        assert s.daily_pnl_pct == 0.0
        assert s.daily_trade_count == 0
        assert s.daily_trades_per_symbol == {}
        assert s.last_trade_date is None
        assert s.safe_mode is False
        assert s.trading_stopped_for_day is False


# ---------------------------------------------------------------------------
# PortfolioRiskManager — core logic
# ---------------------------------------------------------------------------

class TestUpdateEquity:

    def test_appends_to_curve(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState()
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mgr.update_equity(state, dt, 10_000.0)
        assert len(state.equity_curve) == 1
        assert state.equity_curve[0] == (dt, 10_000.0)

    def test_updates_peak(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState()
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mgr.update_equity(state, dt, 10_000.0)
        assert state.peak_equity == 10_000.0
        mgr.update_equity(state, dt, 9_000.0)
        assert state.peak_equity == 10_000.0  # peak not lowered
        mgr.update_equity(state, dt, 11_000.0)
        assert state.peak_equity == 11_000.0


class TestCurrentDrawdownPct:

    def test_zero_peak(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState()
        assert mgr.current_drawdown_pct(state, 5_000.0) == 0.0

    def test_no_drawdown(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState(peak_equity=10_000.0)
        assert mgr.current_drawdown_pct(state, 10_000.0) == 0.0

    def test_ten_percent_drawdown(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState(peak_equity=10_000.0)
        dd = mgr.current_drawdown_pct(state, 9_000.0)
        assert dd == pytest.approx(-10.0)


class TestCheckDailyReset:

    def test_resets_on_new_day(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState(
            daily_pnl_pct=-1.5,
            daily_trade_count=5,
            daily_trades_per_symbol={"AAPL": 2},
            trading_stopped_for_day=True,
            last_trade_date=date(2026, 1, 1),
        )
        mgr.check_daily_reset(state, date(2026, 1, 2))
        assert state.daily_pnl_pct == 0.0
        assert state.daily_trade_count == 0
        assert state.daily_trades_per_symbol == {}
        assert state.trading_stopped_for_day is False
        assert state.last_trade_date == date(2026, 1, 2)

    def test_no_reset_same_day(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState(
            daily_pnl_pct=-1.0,
            daily_trade_count=3,
            last_trade_date=date(2026, 1, 1),
        )
        mgr.check_daily_reset(state, date(2026, 1, 1))
        assert state.daily_pnl_pct == -1.0
        assert state.daily_trade_count == 3


class TestCanTrade:

    def _mgr(self, **overrides):
        cfg = {"portfolio_risk": overrides}
        return PortfolioRiskManager(cfg)

    def test_allowed(self):
        mgr = self._mgr()
        state = PortfolioRiskState(peak_equity=10_000.0, last_trade_date=date(2026, 1, 1))
        ok, reason = mgr.can_trade(state, 10_000.0, "AAPL", today=date(2026, 1, 1))
        assert ok is True
        assert reason == "ok"

    def test_safe_mode_blocks(self):
        mgr = self._mgr(recovery_criteria_pct=-8.0)
        state = PortfolioRiskState(peak_equity=10_000.0, safe_mode=True)
        ok, reason = mgr.can_trade(state, 8_500.0, "AAPL", today=date(2026, 1, 1))
        assert ok is False
        assert "safe_mode" in reason

    def test_safe_mode_allows_when_recovered(self):
        mgr = self._mgr(recovery_criteria_pct=-8.0)
        state = PortfolioRiskState(peak_equity=10_000.0, safe_mode=True,
                                   last_trade_date=date(2026, 1, 1))
        # -5% drawdown is above -8% threshold → allowed
        ok, reason = mgr.can_trade(state, 9_500.0, "AAPL", today=date(2026, 1, 1))
        assert ok is True

    def test_daily_loss_limit_stops_trading(self):
        mgr = self._mgr(daily_loss_limit_pct=-2.0)
        state = PortfolioRiskState(
            peak_equity=10_000.0,
            daily_pnl_pct=-2.5,
            last_trade_date=date(2026, 1, 1),
        )
        ok, reason = mgr.can_trade(state, 10_000.0, "AAPL", today=date(2026, 1, 1))
        assert ok is False
        assert "daily loss limit" in reason
        assert state.trading_stopped_for_day is True

    def test_trading_stopped_for_day_blocks(self):
        mgr = self._mgr()
        state = PortfolioRiskState(
            peak_equity=10_000.0,
            trading_stopped_for_day=True,
            last_trade_date=date(2026, 1, 1),
        )
        ok, reason = mgr.can_trade(state, 10_000.0, "AAPL", today=date(2026, 1, 1))
        assert ok is False
        assert "trading stopped" in reason

    def test_max_drawdown_triggers_safe_mode(self):
        mgr = self._mgr(max_drawdown_pct=-10.0)
        state = PortfolioRiskState(peak_equity=10_000.0, last_trade_date=date(2026, 1, 1))
        ok, reason = mgr.can_trade(state, 8_900.0, "AAPL", today=date(2026, 1, 1))
        assert ok is False
        assert "max drawdown" in reason
        assert state.safe_mode is True

    def test_max_trades_per_day(self):
        mgr = self._mgr(max_trades_per_day=2)
        state = PortfolioRiskState(
            peak_equity=10_000.0,
            daily_trade_count=2,
            last_trade_date=date(2026, 1, 1),
        )
        ok, reason = mgr.can_trade(state, 10_000.0, "AAPL", today=date(2026, 1, 1))
        assert ok is False
        assert "max trades per day" in reason

    def test_max_trades_per_symbol_per_day(self):
        mgr = self._mgr(max_trades_per_symbol_per_day=1)
        state = PortfolioRiskState(
            peak_equity=10_000.0,
            daily_trades_per_symbol={"AAPL": 1},
            last_trade_date=date(2026, 1, 1),
        )
        ok, reason = mgr.can_trade(state, 10_000.0, "AAPL", today=date(2026, 1, 1))
        assert ok is False
        assert "max trades per symbol" in reason

    def test_today_defaults_to_now(self):
        mgr = self._mgr()
        state = PortfolioRiskState(peak_equity=10_000.0)
        ok, _ = mgr.can_trade(state, 10_000.0, "AAPL")
        assert ok is True
        assert state.last_trade_date == date.today()

    def test_safe_mode_not_triggered_when_disabled(self):
        mgr = self._mgr(max_drawdown_pct=-10.0, safe_mode_after_max_dd=False)
        state = PortfolioRiskState(peak_equity=10_000.0, last_trade_date=date(2026, 1, 1))
        ok, _ = mgr.can_trade(state, 8_900.0, "AAPL", today=date(2026, 1, 1))
        assert ok is True
        assert state.safe_mode is False


class TestRecordTrade:

    def test_increments_counts(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState()
        mgr.record_trade(state, "AAPL", -0.5)
        assert state.daily_trade_count == 1
        assert state.daily_trades_per_symbol == {"AAPL": 1}
        assert state.daily_pnl_pct == pytest.approx(-0.5)

    def test_accumulates(self):
        mgr = PortfolioRiskManager({})
        state = PortfolioRiskState()
        mgr.record_trade(state, "AAPL", -0.5)
        mgr.record_trade(state, "AAPL", 0.3)
        mgr.record_trade(state, "TSLA", 1.0)
        assert state.daily_trade_count == 3
        assert state.daily_trades_per_symbol == {"AAPL": 2, "TSLA": 1}
        assert state.daily_pnl_pct == pytest.approx(0.8)


# ---------------------------------------------------------------------------
# MultiUserPortfolioRiskManager
# ---------------------------------------------------------------------------

class TestMultiUserPortfolioRiskManager:

    @staticmethod
    def _configs():
        return {
            "alice": {"portfolio_risk": {"daily_loss_limit_pct": -1.0, "max_trades_per_day": 5}},
            "bob": {"portfolio_risk": {"daily_loss_limit_pct": -3.0, "max_trades_per_day": 20}},
        }

    def test_register_and_get_state(self):
        mu = MultiUserPortfolioRiskManager(self._configs())
        assert isinstance(mu.get_state("alice"), PortfolioRiskState)
        assert isinstance(mu.get_state("bob"), PortfolioRiskState)

    def test_unregistered_user_raises(self):
        mu = MultiUserPortfolioRiskManager()
        with pytest.raises(KeyError, match="charlie"):
            mu.get_state("charlie")

    def test_register_idempotent(self):
        mu = MultiUserPortfolioRiskManager()
        mu.register_user("alice", {})
        state1 = mu.get_state("alice")
        mu.register_user("alice", {"portfolio_risk": {"daily_loss_limit_pct": -99}})
        state2 = mu.get_state("alice")
        assert state1 is state2  # not replaced

    def test_users_isolated(self):
        mu = MultiUserPortfolioRiskManager(self._configs())
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        mu.update_equity("alice", dt, 10_000.0)
        mu.update_equity("bob", dt, 50_000.0)
        assert mu.get_state("alice").peak_equity == 10_000.0
        assert mu.get_state("bob").peak_equity == 50_000.0

    def test_can_trade_per_user_config(self):
        mu = MultiUserPortfolioRiskManager(self._configs())
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        today = date(2026, 1, 1)
        mu.update_equity("alice", dt, 10_000.0)
        mu.update_equity("bob", dt, 10_000.0)

        # Alice: daily_loss_limit=-1%, set pnl to -1.5%
        mu.get_state("alice").daily_pnl_pct = -1.5
        mu.get_state("alice").last_trade_date = today
        ok_a, _ = mu.can_trade("alice", 10_000.0, "AAPL", today)
        assert ok_a is False

        # Bob: daily_loss_limit=-3%, same pnl → still allowed
        mu.get_state("bob").daily_pnl_pct = -1.5
        mu.get_state("bob").last_trade_date = today
        ok_b, _ = mu.can_trade("bob", 10_000.0, "AAPL", today)
        assert ok_b is True

    def test_record_trade(self):
        mu = MultiUserPortfolioRiskManager(self._configs())
        mu.record_trade("alice", "AAPL", -0.5)
        assert mu.get_state("alice").daily_trade_count == 1
        assert mu.get_state("bob").daily_trade_count == 0

    def test_check_daily_reset(self):
        mu = MultiUserPortfolioRiskManager(self._configs())
        mu.get_state("alice").daily_trade_count = 5
        mu.get_state("alice").last_trade_date = date(2026, 1, 1)
        mu.check_daily_reset("alice", date(2026, 1, 2))
        assert mu.get_state("alice").daily_trade_count == 0

    def test_update_equity_unknown_user(self):
        mu = MultiUserPortfolioRiskManager()
        with pytest.raises(KeyError, match="unknown"):
            mu.update_equity("unknown", datetime.now(timezone.utc), 1000.0)

    def test_can_trade_unknown_user(self):
        mu = MultiUserPortfolioRiskManager()
        with pytest.raises(KeyError):
            mu.can_trade("unknown", 1000.0, "AAPL")

    def test_record_trade_unknown_user(self):
        mu = MultiUserPortfolioRiskManager()
        with pytest.raises(KeyError):
            mu.record_trade("unknown", "AAPL", 0.1)

    def test_check_daily_reset_unknown_user(self):
        mu = MultiUserPortfolioRiskManager()
        with pytest.raises(KeyError):
            mu.check_daily_reset("unknown", date.today())
