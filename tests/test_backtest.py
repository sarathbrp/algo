"""Tests for src/rules_engine/backtest.py — bar-by-bar backtest simulation."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.rules_engine.backtest import run_backtest, BacktestConfig, BacktestResult, SimulatedTrade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(closes: list[float], *, spread: float = 0.5) -> pd.DataFrame:
    """Build a synthetic OHLCV DataFrame from a list of close prices.

    High/Low are set symmetrically around close, open equals previous close
    (or first close), and volume is constant.
    """
    n = len(closes)
    closes_arr = np.array(closes, dtype=float)
    opens = np.roll(closes_arr, 1)
    opens[0] = closes_arr[0]
    highs = closes_arr + spread
    lows = closes_arr - spread
    volumes = np.full(n, 1_000_000, dtype=float)
    dates = pd.date_range("2025-01-01", periods=n, freq="D")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes_arr, "volume": volumes},
        index=dates,
    )


def _entry_tree(comparator="is_above", sma_period=10, extra_actions=None):
    """Price is_above/is_below SMA entry rule tree."""
    actions = [{"action": "enter_long", "params": {}}]
    if extra_actions:
        actions.extend(extra_actions)
    return {
        "groups": [{"logic": "AND", "conditions": [
            {
                "indicator": "price",
                "params": {"field": "close"},
                "comparator": comparator,
                "value": {"indicator": "sma", "params": {"period": sma_period}},
            },
        ]}],
        "actions": actions,
    }


def _exit_tree(comparator="is_below", sma_period=10):
    """Price is_below/is_above SMA exit rule tree."""
    return {
        "groups": [{"logic": "AND", "conditions": [
            {
                "indicator": "price",
                "params": {"field": "close"},
                "comparator": comparator,
                "value": {"indicator": "sma", "params": {"period": sma_period}},
            },
        ]}],
        "actions": [{"action": "exit_position", "params": {}}],
    }


# ---------------------------------------------------------------------------
# 1. Uptrend scenario — at least one winning trade
# ---------------------------------------------------------------------------

class TestUptrendScenario:
    def test_uptrend_produces_winning_trade(self):
        """Prices go linearly from 100 to 200 over 100 bars.
        Price should be above SMA(10) for most of the trend, producing
        at least one winning trade.
        """
        closes = [100 + i for i in range(100)]
        df = _make_ohlcv(closes)
        entry = _entry_tree(comparator="is_above", sma_period=10)
        exit_ = _exit_tree(comparator="is_below", sma_period=10)

        result = run_backtest(df, entry, exit_)

        assert isinstance(result, BacktestResult)
        assert result.total_trades >= 1
        assert result.winning_trades >= 1
        assert result.total_pnl > 0


# ---------------------------------------------------------------------------
# 2. Stop loss
# ---------------------------------------------------------------------------

class TestStopLoss:
    def test_stop_loss_fires_on_price_drop(self):
        """Entry at a high price, then price drops sharply.
        Stop loss at 2% should close the trade with negative pnl.
        """
        # Build prices: rise above SMA to trigger entry, then crash
        # Rise then sudden crash — stop loss should trigger before slow exit rule
        prices = [100.0] * 20  # flat warmup
        prices += [100 + i * 2 for i in range(1, 11)]  # rise to 120
        prices += [120, 115, 105, 95, 85, 75]  # sharp crash

        df = _make_ohlcv(prices)
        entry = _entry_tree(
            comparator="is_above",
            sma_period=10,
            extra_actions=[{"action": "set_stop_loss", "params": {"pct": 2.0}}],
        )
        # Use a very slow exit SMA so stop loss fires first
        exit_ = _exit_tree(comparator="is_below", sma_period=30)

        result = run_backtest(df, entry, exit_)

        # Find a trade that was stopped out
        stop_trades = [t for t in result.trades if t.exit_reason == "stop_loss"]
        assert len(stop_trades) >= 1, f"Expected a stop_loss trade, got reasons: {[t.exit_reason for t in result.trades]}"

        for t in stop_trades:
            assert t.pnl is not None and t.pnl < 0, "Stop-loss trade should have negative PnL"


# ---------------------------------------------------------------------------
# 3. Take profit
# ---------------------------------------------------------------------------

class TestTakeProfit:
    def test_take_profit_fires_on_price_rise(self):
        """Entry fires, price keeps rising. Take profit at 5% should close."""
        # Warmup flat, then steady rise
        prices = [100.0] * 15
        prices += [100 + i * 1.5 for i in range(1, 30)]  # rise to ~143.5

        df = _make_ohlcv(prices)
        entry = _entry_tree(
            comparator="is_above",
            sma_period=10,
            extra_actions=[{"action": "set_take_profit", "params": {"pct": 5.0}}],
        )
        exit_ = _exit_tree(comparator="is_below", sma_period=10)

        result = run_backtest(df, entry, exit_)

        tp_trades = [t for t in result.trades if t.exit_reason == "take_profit"]
        assert len(tp_trades) >= 1, f"Expected a take_profit trade, got reasons: {[t.exit_reason for t in result.trades]}"

        for t in tp_trades:
            assert t.pnl is not None and t.pnl > 0, "Take-profit trade should have positive PnL"


# ---------------------------------------------------------------------------
# 4. No signals — flat market, condition never met
# ---------------------------------------------------------------------------

class TestNoSignals:
    def test_flat_prices_no_trades(self):
        """Completely flat prices — SMA equals price, is_above never fires."""
        closes = [100.0] * 50
        df = _make_ohlcv(closes)
        entry = _entry_tree(comparator="is_above", sma_period=10)
        exit_ = _exit_tree(comparator="is_below", sma_period=10)

        result = run_backtest(df, entry, exit_)

        assert result.total_trades == 0
        assert result.trades == []
        assert result.winning_trades == 0
        assert result.losing_trades == 0
        assert result.win_rate is None
        assert result.total_pnl == 0.0
        assert result.avg_pnl_per_trade is None


# ---------------------------------------------------------------------------
# 5. End of data — open position force-closed
# ---------------------------------------------------------------------------

class TestEndOfData:
    def test_open_position_closed_at_end(self):
        """Entry fires but exit condition never fires before data ends.
        Last trade should have exit_reason='end_of_data'.
        """
        # Rise to trigger entry, then stay high (exit never fires)
        prices = [100.0] * 15
        prices += [100 + i for i in range(1, 20)]  # steady rise, no pullback

        df = _make_ohlcv(prices)
        entry = _entry_tree(comparator="is_above", sma_period=10)
        # Exit requires price below SMA — won't happen in a steady uptrend
        exit_ = _exit_tree(comparator="is_below", sma_period=10)

        result = run_backtest(df, entry, exit_)

        assert result.total_trades >= 1
        last_trade = result.trades[-1]
        assert last_trade.exit_reason == "end_of_data"
        assert last_trade.exit_bar == len(df) - 1


# ---------------------------------------------------------------------------
# 6. Equity curve starts at initial_capital
# ---------------------------------------------------------------------------

class TestEquityCurve:
    def test_equity_curve_starts_at_initial_capital(self):
        closes = [100.0] * 50
        df = _make_ohlcv(closes)
        cfg = BacktestConfig(initial_capital=50_000.0)
        entry = _entry_tree(comparator="is_above", sma_period=10)
        exit_ = _exit_tree(comparator="is_below", sma_period=10)

        result = run_backtest(df, entry, exit_, config=cfg)

        assert len(result.equity_curve) == len(df)
        assert result.equity_curve[0] == 50_000.0

    def test_equity_curve_adjusts_with_trades(self):
        """After a trade with positive P&L, final equity should exceed initial."""
        # Rise then drop — forces entry then exit with profit
        closes = [100.0] * 15
        closes += [100 + i * 2 for i in range(1, 20)]  # rise to 138
        closes += [138 - i * 3 for i in range(1, 15)]  # drop back to ~96
        df = _make_ohlcv(closes)
        cfg = BacktestConfig(initial_capital=100_000.0, position_size_pct=10.0)
        entry = _entry_tree(comparator="is_above", sma_period=10)
        exit_ = _exit_tree(comparator="is_below", sma_period=10)

        result = run_backtest(df, entry, exit_, config=cfg)

        # At least one closed trade with positive P&L should raise equity
        if result.total_trades > 0 and result.total_pnl > 0:
            assert result.equity_curve[-1] > cfg.initial_capital


# ---------------------------------------------------------------------------
# 7. Multiple trades in oscillating market
# ---------------------------------------------------------------------------

class TestMultipleTrades:
    def test_oscillating_market_multiple_trades(self):
        """Price oscillates above and below SMA, generating multiple entries/exits."""
        # Build a sine-like pattern around 100
        n = 200
        base = 100.0
        amplitude = 15.0
        closes = [base + amplitude * np.sin(2 * np.pi * i / 40) for i in range(n)]
        df = _make_ohlcv(closes)

        entry = _entry_tree(comparator="is_above", sma_period=10)
        exit_ = _exit_tree(comparator="is_below", sma_period=10)

        result = run_backtest(df, entry, exit_)

        assert result.total_trades >= 2, (
            f"Oscillating market should produce multiple trades, got {result.total_trades}"
        )
        assert result.entry_signals >= 2
        assert result.bars_evaluated > 0


# ---------------------------------------------------------------------------
# BacktestResult fields sanity
# ---------------------------------------------------------------------------

class TestBacktestResultFields:
    def test_result_field_consistency(self):
        """total_trades == winning + losing (+ break-even), win_rate in [0,1]."""
        closes = [100 + i for i in range(100)]
        df = _make_ohlcv(closes)
        result = run_backtest(df, _entry_tree(), _exit_tree())

        assert result.total_trades == len(result.trades)
        assert result.winning_trades + result.losing_trades <= result.total_trades
        if result.win_rate is not None:
            assert 0.0 <= result.win_rate <= 1.0

    def test_max_drawdown_non_negative(self):
        closes = [100 + i for i in range(100)]
        df = _make_ohlcv(closes)
        result = run_backtest(df, _entry_tree(), _exit_tree())

        assert result.max_drawdown_pct >= 0.0

    def test_simulated_trade_fields(self):
        """Each completed trade should have all fields populated."""
        closes = [100 + i for i in range(50)]
        closes += [150 - i for i in range(50)]  # rise then fall to force exit
        df = _make_ohlcv(closes)
        result = run_backtest(df, _entry_tree(), _exit_tree())

        for trade in result.trades:
            assert isinstance(trade, SimulatedTrade)
            assert trade.entry_bar is not None
            assert trade.entry_price > 0
            assert trade.exit_bar is not None
            assert trade.exit_price is not None and trade.exit_price > 0
            assert trade.pnl is not None
            assert trade.pnl_pct is not None
            assert trade.exit_reason is not None
            assert trade.bars_held is not None and trade.bars_held >= 0
            assert trade.side in ("long", "short")


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

class TestBacktestConfig:
    def test_default_config_values(self):
        cfg = BacktestConfig()
        assert cfg.initial_capital == 100_000.0
        assert cfg.position_size_pct == 10.0
        assert cfg.max_positions == 1
        assert cfg.commission_per_trade == 0.0

    def test_custom_config(self):
        cfg = BacktestConfig(
            initial_capital=50_000.0,
            position_size_pct=5.0,
            max_positions=3,
            commission_per_trade=1.50,
        )
        assert cfg.initial_capital == 50_000.0
        assert cfg.commission_per_trade == 1.50
