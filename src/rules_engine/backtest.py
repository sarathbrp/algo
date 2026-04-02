"""Bar-by-bar backtest simulation engine.

Walks through a DataFrame, evaluates entry/exit rule trees at each bar,
simulates trades, and computes performance metrics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from src.rules_engine.evaluator import evaluate_rule, compute_warmup_period


@dataclass
class SimulatedTrade:
    entry_bar: int
    entry_price: float
    entry_date: str
    exit_bar: int | None = None
    exit_price: float | None = None
    exit_date: str | None = None
    side: str = "long"
    pnl: float | None = None
    pnl_pct: float | None = None
    exit_reason: str | None = None
    bars_held: int | None = None


@dataclass
class BacktestResult:
    trades: list[SimulatedTrade]
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float | None
    total_pnl: float
    avg_pnl_per_trade: float | None
    max_drawdown_pct: float
    equity_curve: list[float]
    bars_evaluated: int
    entry_signals: int
    exit_signals: int


@dataclass
class BacktestConfig:
    initial_capital: float = 100_000.0
    position_size_pct: float = 10.0
    max_positions: int = 1
    commission_per_trade: float = 0.0


def _extract_risk_params(actions: list[dict]) -> dict[str, float]:
    """Extract stop_loss, take_profit, trailing_stop percentages from actions."""
    params: dict[str, float] = {}
    for act in actions:
        action_id = act.get("action", "")
        pct = act.get("params", {}).get("pct")
        if pct is None:
            continue
        if action_id == "set_stop_loss":
            params["stop_loss_pct"] = float(pct)
        elif action_id == "set_take_profit":
            params["take_profit_pct"] = float(pct)
        elif action_id == "set_trailing_stop":
            params["trailing_stop_pct"] = float(pct)
    return params


def _determine_side(actions: list[dict]) -> str:
    """Determine trade side from entry actions."""
    for act in actions:
        if act.get("action") == "enter_short":
            return "short"
    return "long"


def _bar_date(df: pd.DataFrame, idx: int) -> str:
    """Get ISO date string for a bar index."""
    try:
        return str(df.index[idx])
    except (IndexError, TypeError):
        return ""


def run_backtest(
    df: pd.DataFrame,
    entry_rule_tree: dict,
    exit_rule_tree: dict,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Run a bar-by-bar backtest simulation.

    Parameters
    ----------
    df : OHLCV DataFrame (must have open, high, low, close, volume columns)
    entry_rule_tree : JSON rule tree for entry conditions
    exit_rule_tree : JSON rule tree for exit conditions
    config : backtest parameters (capital, sizing, etc.)
    """
    if config is None:
        config = BacktestConfig()

    n_bars = len(df)
    warmup = max(
        compute_warmup_period(entry_rule_tree),
        compute_warmup_period(exit_rule_tree),
    )

    # State
    trades: list[SimulatedTrade] = []
    in_position = False
    entry_price = 0.0
    entry_bar = 0
    entry_side = "long"
    trail_high = 0.0
    risk_params: dict[str, float] = {}
    position_shares = 0.0

    # Equity tracking
    equity = config.initial_capital
    equity_curve: list[float] = []
    peak_equity = equity

    entry_signals = 0
    exit_signals = 0

    start_bar = min(warmup, n_bars)

    for i in range(n_bars):
        bar_close = float(df["close"].iloc[i])
        bar_high = float(df["high"].iloc[i])
        bar_low = float(df["low"].iloc[i])

        if i < start_bar:
            equity_curve.append(equity)
            continue

        if in_position:
            # Check risk management first (stop loss, take profit, trailing stop)
            exit_reason = _check_risk_exit(
                entry_price, bar_close, bar_high, bar_low,
                entry_side, trail_high, risk_params,
            )

            # Update trailing high
            if entry_side == "long":
                trail_high = max(trail_high, bar_high)
            else:
                trail_high = min(trail_high, bar_low) if trail_high > 0 else bar_low

            # Check exit rule
            if exit_reason is None:
                exit_result = evaluate_rule(df, exit_rule_tree, bar_idx=i)
                if exit_result.fired:
                    exit_reason = "exit_rule"
                    exit_signals += 1

            if exit_reason is not None:
                # Close position
                exit_price = _get_exit_price(
                    entry_price, bar_close, bar_high, bar_low,
                    entry_side, exit_reason, risk_params,
                )
                pnl = _compute_pnl(entry_price, exit_price, position_shares, entry_side)
                pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_side == "long" \
                    else ((entry_price - exit_price) / entry_price * 100)

                trades.append(SimulatedTrade(
                    entry_bar=entry_bar,
                    entry_price=entry_price,
                    entry_date=_bar_date(df, entry_bar),
                    exit_bar=i,
                    exit_price=exit_price,
                    exit_date=_bar_date(df, i),
                    side=entry_side,
                    pnl=pnl - config.commission_per_trade * 2,
                    pnl_pct=pnl_pct,
                    exit_reason=exit_reason,
                    bars_held=i - entry_bar,
                ))

                equity += pnl - config.commission_per_trade * 2
                in_position = False

        if not in_position and i < n_bars - 1:  # Don't enter on last bar
            entry_result = evaluate_rule(df, entry_rule_tree, bar_idx=i)
            if entry_result.fired:
                entry_signals += 1
                entry_price = bar_close
                entry_bar = i
                entry_side = _determine_side(entry_result.actions)
                trail_high = bar_high if entry_side == "long" else bar_low
                risk_params = _extract_risk_params(entry_result.actions)
                position_size = equity * (config.position_size_pct / 100)
                position_shares = position_size / entry_price if entry_price > 0 else 0
                in_position = True

        equity_curve.append(equity)
        if equity > peak_equity:
            peak_equity = equity

    # Close any open position at end of data
    if in_position:
        exit_price = float(df["close"].iloc[-1])
        pnl = _compute_pnl(entry_price, exit_price, position_shares, entry_side)
        pnl_pct = ((exit_price - entry_price) / entry_price * 100) if entry_side == "long" \
            else ((entry_price - exit_price) / entry_price * 100)
        trades.append(SimulatedTrade(
            entry_bar=entry_bar,
            entry_price=entry_price,
            entry_date=_bar_date(df, entry_bar),
            exit_bar=n_bars - 1,
            exit_price=exit_price,
            exit_date=_bar_date(df, n_bars - 1),
            side=entry_side,
            pnl=pnl,
            pnl_pct=pnl_pct,
            exit_reason="end_of_data",
            bars_held=n_bars - 1 - entry_bar,
        ))
        equity += pnl

    # Pad equity curve to match bars
    while len(equity_curve) < n_bars:
        equity_curve.append(equity)

    # Compute metrics
    winning = [t for t in trades if (t.pnl or 0) > 0]
    losing = [t for t in trades if (t.pnl or 0) < 0]
    total_pnl = sum(t.pnl or 0 for t in trades)
    max_dd = _max_drawdown(equity_curve)

    return BacktestResult(
        trades=trades,
        total_trades=len(trades),
        winning_trades=len(winning),
        losing_trades=len(losing),
        win_rate=len(winning) / len(trades) if trades else None,
        total_pnl=total_pnl,
        avg_pnl_per_trade=total_pnl / len(trades) if trades else None,
        max_drawdown_pct=max_dd,
        equity_curve=equity_curve,
        bars_evaluated=n_bars - start_bar,
        entry_signals=entry_signals,
        exit_signals=exit_signals,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_risk_exit(
    entry_price: float, close: float, high: float, low: float,
    side: str, trail_high: float, params: dict[str, float],
) -> str | None:
    """Check stop-loss, take-profit, trailing-stop. Returns exit reason or None."""
    if side == "long":
        ret_pct = (close - entry_price) / entry_price * 100
        # Stop loss
        sl = params.get("stop_loss_pct")
        if sl is not None and ret_pct <= -sl:
            return "stop_loss"
        # Take profit
        tp = params.get("take_profit_pct")
        if tp is not None and ret_pct >= tp:
            return "take_profit"
        # Trailing stop
        ts = params.get("trailing_stop_pct")
        if ts is not None and trail_high > 0:
            trail_ret = (close - trail_high) / trail_high * 100
            if trail_ret <= -ts:
                return "trailing_stop"
    else:  # short
        ret_pct = (entry_price - close) / entry_price * 100
        sl = params.get("stop_loss_pct")
        if sl is not None and ret_pct <= -sl:
            return "stop_loss"
        tp = params.get("take_profit_pct")
        if tp is not None and ret_pct >= tp:
            return "take_profit"
        ts = params.get("trailing_stop_pct")
        if ts is not None and trail_high > 0:
            trail_ret = (trail_high - close) / trail_high * 100
            if trail_ret <= -ts:
                return "trailing_stop"
    return None


def _get_exit_price(
    entry_price: float, close: float, high: float, low: float,
    side: str, reason: str, params: dict[str, float],
) -> float:
    """Determine exit price based on reason. Uses close as default."""
    if reason == "stop_loss":
        sl = params.get("stop_loss_pct", 0)
        if side == "long":
            return entry_price * (1 - sl / 100)
        return entry_price * (1 + sl / 100)
    if reason == "take_profit":
        tp = params.get("take_profit_pct", 0)
        if side == "long":
            return entry_price * (1 + tp / 100)
        return entry_price * (1 - tp / 100)
    return close


def _compute_pnl(entry: float, exit_price: float, shares: float, side: str) -> float:
    if side == "long":
        return (exit_price - entry) * shares
    return (entry - exit_price) * shares


def _max_drawdown(equity_curve: list[float]) -> float:
    """Compute maximum drawdown percentage from an equity curve."""
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd
