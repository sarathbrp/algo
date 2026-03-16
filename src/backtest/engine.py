"""
Backtest engine: run strategy over historical daily bars with position sizing and exit rules.

Simulates day-by-day: mark-to-market, check exits (stop, time, partial, trailing), then check entries
with cooldown/breakout rules. Tracks trades and equity curve.
"""
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from ..config_loader import load_config
from ..strategy import TrendFollowingStrategy, ExitReason, _atr
from ..position_sizing import PositionSizer


@dataclass
class BacktestTrade:
    symbol: str
    side: str
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    qty: int
    pnl: float
    pnl_pct: float
    exit_reason: str
    bars_held: int


@dataclass
class BacktestResult:
    equity_curve: list[tuple[date, float]]
    trades: list[BacktestTrade]
    initial_equity: float
    final_equity: float
    config: dict[str, Any]


@dataclass
class _Position:
    symbol: str
    entry_price: float
    qty: int
    entry_date: date
    entry_date_idx: int  # index in trading_dates for bars_held
    stop_pct: float
    partial_taken: bool
    trail_high: float


class BacktestEngine:
    """
    Run backtest over pre-loaded OHLCV data (dict[symbol -> DataFrame] with datetime index).
    Uses strategy + sizer; applies cooldown/breakout rules after stop or profit exit.
    """

    def __init__(self, config: dict[str, Any] | None = None, config_path: str | None = None):
        self.config = config or load_config(config_path)
        self.strategy = TrendFollowingStrategy(self.config)
        self.sizer = PositionSizer(self.config)
        # Cooldown state (mirrors TradingEngine)
        self.last_stop_loss_at: dict[str, datetime] = {}
        self.last_stopped_ref_price: dict[str, float] = {}
        self.last_profit_exit_at: dict[str, datetime] = {}
        self.last_profit_exit_price: dict[str, float] = {}

    def run(
        self,
        data: dict[str, pd.DataFrame],
        initial_equity: float = 100_000.0,
        spread_pct: float = 0.15,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> BacktestResult:
        """
        Run backtest. data: symbol -> DataFrame with datetime index, columns open, high, low, close, volume.
        Returns BacktestResult with equity_curve, trades, and final equity.
        """
        if not data:
            return BacktestResult(
                equity_curve=[],
                trades=[],
                initial_equity=initial_equity,
                final_equity=initial_equity,
                config=self.config,
            )

        # Build sorted list of trading dates (union of all symbols, within available range)
        all_dates: set[date] = set()
        for df in data.values():
            for d in df.index:
                all_dates.add(pd.Timestamp(d).date())
        trading_dates = sorted(all_dates)
        if start_date:
            trading_dates = [d for d in trading_dates if d >= start_date]
        if end_date:
            trading_dates = [d for d in trading_dates if d <= end_date]
        if not trading_dates:
            return BacktestResult(
                equity_curve=[],
                trades=[],
                initial_equity=initial_equity,
                final_equity=initial_equity,
                config=self.config,
            )

        date_to_idx = {d: i for i, d in enumerate(trading_dates)}
        symbols = list(data.keys())
        cash = initial_equity
        positions: dict[str, _Position] = {}
        trades: list[BacktestTrade] = []
        equity_curve: list[tuple[date, float]] = []

        for today in trading_dates:
            dt = datetime.combine(today, datetime.min.time())

            # Mark-to-market: equity = cash + sum(position value at today's close)
            equity = cash
            for sym, pos in list(positions.items()):
                if sym not in data or today not in data[sym].index:
                    continue
                close = float(data[sym].loc[today, "close"])
                equity += pos.qty * close

            equity_curve.append((today, equity))

            # ---- Exits (check each open position) ----
            for sym in list(positions.keys()):
                if sym not in data or today not in data[sym].index:
                    continue
                pos = positions[sym]
                close = float(data[sym].loc[today, "close"])
                bars_held = date_to_idx.get(today, 0) - pos.entry_date_idx
                atr_pct = None
                if len(data[sym]) >= 14 and today in data[sym].index:
                    row = data[sym].loc[:today].tail(14)
                    if len(row) >= 14:
                        atr = _atr(row["high"], row["low"], row["close"], 14)
                        atr_pct = float((atr.iloc[-1] / close) * 100) if atr.iloc[-1] else None

                exit_signal = self.strategy.check_exit(
                    sym,
                    pos.entry_price,
                    close,
                    bars_held,
                    spread_pct=spread_pct,
                    atr_pct=atr_pct,
                    partial_taken=pos.partial_taken,
                    trail_high=pos.trail_high,
                    current_qty=pos.qty,
                )

                if exit_signal is None:
                    # Update trail_high for next bar
                    if pos.partial_taken:
                        pos.trail_high = max(pos.trail_high, close)
                    continue

                if exit_signal.reason == ExitReason.PARTIAL_TAKE_PROFIT:
                    qty_to_sell = exit_signal.metadata.get("qty_to_sell", max(1, pos.qty // 2))
                    qty_to_sell = min(qty_to_sell, pos.qty)
                    cash += qty_to_sell * close
                    pos.qty -= qty_to_sell
                    pos.partial_taken = True
                    pos.trail_high = close
                    if pos.qty <= 0:
                        pnl = (close - pos.entry_price) * qty_to_sell
                        pnl_pct = (close - pos.entry_price) / pos.entry_price * 100
                        trades.append(
                            BacktestTrade(
                                symbol=sym,
                                side="long",
                                entry_date=pos.entry_date,
                                exit_date=today,
                                entry_price=pos.entry_price,
                                exit_price=close,
                                qty=qty_to_sell,
                                pnl=pnl,
                                pnl_pct=pnl_pct,
                                exit_reason=exit_signal.reason.value,
                                bars_held=bars_held,
                            )
                        )
                        self.last_profit_exit_at[sym] = dt
                        self.last_profit_exit_price[sym] = close
                        del positions[sym]
                    continue

                # Full exit
                cash += pos.qty * close
                pnl = (close - pos.entry_price) * pos.qty
                pnl_pct = (close - pos.entry_price) / pos.entry_price * 100
                trades.append(
                    BacktestTrade(
                        symbol=sym,
                        side="long",
                        entry_date=pos.entry_date,
                        exit_date=today,
                        entry_price=pos.entry_price,
                        exit_price=close,
                        qty=pos.qty,
                        pnl=pnl,
                        pnl_pct=pnl_pct,
                        exit_reason=exit_signal.reason.value,
                        bars_held=bars_held,
                    )
                )
                if exit_signal.reason == ExitReason.STOP_LOSS:
                    self.last_stop_loss_at[sym] = dt
                    self.last_stopped_ref_price[sym] = pos.entry_price
                elif exit_signal.reason in (ExitReason.TAKE_PROFIT, ExitReason.TRAILING_STOP):
                    self.last_profit_exit_at[sym] = dt
                    self.last_profit_exit_price[sym] = close
                del positions[sym]

            # Recompute equity after exits for entry sizing
            equity = cash
            for sym, pos in positions.items():
                if sym in data and today in data[sym].index:
                    equity += pos.qty * float(data[sym].loc[today, "close"])

            # ---- Entries (symbols not in position, with enough history) ----
            current_positions_for_sizer = {
                s: {"notional": p.qty * float(data[s].loc[today, "close"]) if today in data[s].index else 0, "stop_pct": p.stop_pct}
                for s, p in positions.items()
                if s in data and today in data[s].index
            }

            for sym in symbols:
                if sym in positions or sym not in data:
                    continue
                df_sym = data[sym]
                if today not in df_sym.index:
                    continue
                df_to_date = df_sym.loc[:today]
                if len(df_to_date) < self.strategy.ma_slow:
                    continue

                # Cooldown after stop
                if sym in self.last_stop_loss_at:
                    elapsed_min = (dt - self.last_stop_loss_at[sym]).total_seconds() / 60.0
                    if elapsed_min < self.strategy.cooldown_after_stop_minutes:
                        continue
                if self.strategy.require_new_breakout_after_stop and sym in self.last_stopped_ref_price:
                    close_now = float(df_to_date["close"].iloc[-1])
                    if close_now <= self.last_stopped_ref_price[sym]:
                        continue

                # Cooldown after profit
                if sym in self.last_profit_exit_at:
                    elapsed_min = (dt - self.last_profit_exit_at[sym]).total_seconds() / 60.0
                    if elapsed_min < self.strategy.cooldown_after_profit_minutes:
                        continue
                if self.strategy.require_price_above_exit_after_profit and sym in self.last_profit_exit_price:
                    close_now = float(df_to_date["close"].iloc[-1])
                    if close_now <= self.last_profit_exit_price[sym]:
                        continue

                atr_pct = None
                if len(df_to_date) >= self.strategy.atr_period:
                    atr_ser = self.strategy.atr_pct(df_to_date)
                    if len(atr_ser) and not pd.isna(atr_ser.iloc[-1]):
                        atr_pct = float(atr_ser.iloc[-1])

                entry_signal = self.strategy.generate_entry(sym, df_to_date, spread_pct, atr_pct)
                if entry_signal is None:
                    continue

                price = float(df_to_date["close"].iloc[-1])
                sector_exposure_pct = {}
                sizing = self.sizer.size_position(
                    account_equity=equity,
                    price=price,
                    stop_distance_pct=entry_signal.stop_pct,
                    symbol=sym,
                    current_positions=current_positions_for_sizer,
                    sector_exposure_pct=sector_exposure_pct,
                    symbol_sector=None,
                    atr_pct=atr_pct,
                    regime_size_multiplier=None,
                )
                if sizing.reject_reason or sizing.shares <= 0:
                    continue

                current_with_stops = [
                    (p.get("notional", 0), p.get("stop_pct", 0))
                    for p in current_positions_for_sizer.values()
                ]
                open_risk_pct = self.sizer.total_open_risk_pct(equity, current_with_stops)
                if self.sizer.would_exceed_max_open_risk(open_risk_pct, entry_signal.stop_pct, sizing.risk_pct):
                    continue

                # Open position
                notional = sizing.shares * price
                cash -= notional
                idx = date_to_idx[today]
                positions[sym] = _Position(
                    symbol=sym,
                    entry_price=price,
                    qty=sizing.shares,
                    entry_date=today,
                    entry_date_idx=idx,
                    stop_pct=entry_signal.stop_pct,
                    partial_taken=False,
                    trail_high=price,
                )
                current_positions_for_sizer[sym] = {"notional": notional, "stop_pct": entry_signal.stop_pct}

                # Clear cooldown state for this symbol (we entered)
                self.last_stop_loss_at.pop(sym, None)
                self.last_stopped_ref_price.pop(sym, None)
                self.last_profit_exit_at.pop(sym, None)
                self.last_profit_exit_price.pop(sym, None)

        # Final equity
        last_date = trading_dates[-1]
        final_equity = cash
        for sym, pos in positions.items():
            if sym in data and last_date in data[sym].index:
                final_equity += pos.qty * float(data[sym].loc[last_date, "close"])

        return BacktestResult(
            equity_curve=equity_curve,
            trades=trades,
            initial_equity=initial_equity,
            final_equity=final_equity,
            config=self.config,
        )
