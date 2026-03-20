"""
Trading engine: orchestrates universe, strategy, sizing, portfolio risk, execution, compliance.

Runs the full gate sequence before any trade and applies all rules.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from .config_loader import load_config
from .universe import MarketCalendar, UniverseFilter, MarketQualityGate, MarketQualityResult
from .strategy import TrendFollowingStrategy, EntrySignal, ExitSignal
from .position_sizing import PositionSizer, PositionSizingResult
from .portfolio_risk import PortfolioRiskManager, PortfolioRiskState
from .execution import ExecutionManager, ExecutionState
from .compliance import ComplianceManager, PDTState
from .trade_filters import MacroEventBlackout, EarningsBlackout, VolatilityDoNotTrade


@dataclass
class TradingEngineState:
    portfolio_risk: PortfolioRiskState = field(default_factory=PortfolioRiskState)
    execution: ExecutionState = field(default_factory=ExecutionState)
    pdt: PDTState = field(default_factory=lambda: PDTState(equity=0.0, day_trades_count_rolling=0, day_trade_dates=[]))
    # After stop loss: block re-entry for cooldown; optional: require new breakout above stopped price
    last_stop_loss_at: dict[str, datetime] = field(default_factory=dict)
    last_stopped_ref_price: dict[str, float] = field(default_factory=dict)
    # After profit booking: cooldown and require price > previous exit price
    last_profit_exit_at: dict[str, datetime] = field(default_factory=dict)
    last_profit_exit_price: dict[str, float] = field(default_factory=dict)


@dataclass
class TradeDecision:
    allowed: bool
    reason: str
    order_request: Any = None
    entry_signal: EntrySignal | None = None
    position_sizing: PositionSizingResult | None = None


class TradingEngine:
    """
    Single place to run all gates before sending a order.
    """

    def __init__(self, config: dict[str, Any] | None = None, config_path: str | Path | None = None):
        self.config = config or load_config(config_path)
        self.calendar = MarketCalendar(self.config)
        self.universe = UniverseFilter(self.config)
        self.market_quality = MarketQualityGate(self.config)
        self.strategy = TrendFollowingStrategy(self.config)
        self.sizer = PositionSizer(self.config)
        self.portfolio_risk = PortfolioRiskManager(self.config)
        self.execution = ExecutionManager(self.config)
        self.compliance = ComplianceManager(self.config)
        self.macro_blackout = MacroEventBlackout(self.config)
        self.earnings_blackout = EarningsBlackout(self.config)
        self.volatility_dnt = VolatilityDoNotTrade(self.config)
        self.state = TradingEngineState()

    def update_equity(self, equity: float, dt: datetime | None = None) -> None:
        dt = dt or datetime.utcnow()
        self.state.portfolio_risk.peak_equity = max(
            self.state.portfolio_risk.peak_equity,
            equity,
        )
        self.portfolio_risk.update_equity(self.state.portfolio_risk, dt, equity)
        self.compliance.update_equity(self.state.pdt, equity)

    def record_stop_loss(self, symbol: str, dt: datetime, entry_price: float | None = None) -> None:
        """Record a stop-loss exit so re-entry is subject to cooldown and optional new-breakout rule."""
        self.state.last_stop_loss_at[symbol] = dt
        if entry_price is not None:
            self.state.last_stopped_ref_price[symbol] = entry_price

    def record_profit_exit(self, symbol: str, dt: datetime, exit_price: float) -> None:
        """Record a profit-booking exit (take-profit, partial, trailing) so re-entry has cooldown and price > exit."""
        self.state.last_profit_exit_at[symbol] = dt
        self.state.last_profit_exit_price[symbol] = exit_price

    def is_trading_allowed(self, dt: datetime) -> bool:
        return self.calendar.is_trading_allowed(dt)

    def run_entry_gates(
        self,
        symbol: str,
        dt: datetime,
        account_equity: float,
        current_positions: dict[str, Any],
        sector_exposure_pct: dict[str, float],
        # Market data for quality & strategy
        spread_pct: float,
        volume_atr_ratio: float | None = None,
        atr_pct: float | None = None,
        ohlcv_df: Any = None,
        symbol_sector: dict[str, str] | None = None,
        log_strategy_context: bool = False,
        regime_size_multiplier: float | None = None,
        entry_override: EntrySignal | None = None,
    ) -> TradeDecision:
        """
        Run full gate sequence for an entry. Returns TradeDecision with allowed=False
        and reason if any gate fails.
        """
        today = dt.date() if isinstance(dt, datetime) else date.today()

        if not self.calendar.is_trading_allowed(dt):
            return TradeDecision(allowed=False, reason="market closed or session not tradeable")

        macro = self.macro_blackout.check(dt)
        if not macro.allowed:
            return TradeDecision(allowed=False, reason=macro.reason)

        if not self.universe.is_eligible(symbol):
            return TradeDecision(allowed=False, reason=f"symbol {symbol} not in universe or liquidity filter")

        earnings = self.earnings_blackout.check(symbol, dt)
        if not earnings.allowed:
            return TradeDecision(allowed=False, reason=earnings.reason)

        mq = self.market_quality.check(
            symbol=symbol,
            spread_pct=spread_pct,
            volume_atr_ratio=volume_atr_ratio,
            current_atr_pct=atr_pct,
        )
        if not mq.ok:
            return TradeDecision(allowed=False, reason=f"market_quality: {mq.reason}")

        allowed, reason = self.execution.can_trade_spread(spread_pct)
        if not allowed:
            return TradeDecision(allowed=False, reason=reason)

        vol_dnt = self.volatility_dnt.check(atr_pct=atr_pct, spread_pct=spread_pct, symbol=symbol)
        if not vol_dnt.allowed:
            return TradeDecision(allowed=False, reason=vol_dnt.reason)

        if self.execution.should_block_strategy(self.state.execution):
            return TradeDecision(
                allowed=False,
                reason="strategy blocked: avg slippage exceeded threshold",
            )

        can_trade, reason = self.portfolio_risk.can_trade(
            self.state.portfolio_risk,
            account_equity,
            symbol,
            today,
        )
        if not can_trade:
            return TradeDecision(allowed=False, reason=reason)

        can_dt, reason = self.compliance.can_day_trade(self.state.pdt, today)
        if not can_dt:
            return TradeDecision(allowed=False, reason=reason)

        # Cooldown after stop loss: avoid immediate re-entry
        if symbol in self.state.last_stop_loss_at:
            elapsed_min = (dt - self.state.last_stop_loss_at[symbol]).total_seconds() / 60.0
            if elapsed_min < self.strategy.cooldown_after_stop_minutes:
                return TradeDecision(
                    allowed=False,
                    reason=f"cooldown after stop loss ({elapsed_min:.0f} min < {self.strategy.cooldown_after_stop_minutes} min)",
                )

        # Optional: require new breakout above stopped price before re-entry
        if (
            self.strategy.require_new_breakout_after_stop
            and symbol in self.state.last_stopped_ref_price
            and ohlcv_df is not None
            and not ohlcv_df.empty
        ):
            ref_price = self.state.last_stopped_ref_price[symbol]
            current_close = float(ohlcv_df["close"].iloc[-1])
            if current_close <= ref_price:
                return TradeDecision(
                    allowed=False,
                    reason=f"re-entry requires new breakout above stopped price ({current_close:.2f} <= {ref_price:.2f})",
                )

        # Cooldown after profit booking
        if symbol in self.state.last_profit_exit_at:
            elapsed_min = (dt - self.state.last_profit_exit_at[symbol]).total_seconds() / 60.0
            if elapsed_min < self.strategy.cooldown_after_profit_minutes:
                return TradeDecision(
                    allowed=False,
                    reason=f"cooldown after profit booking ({elapsed_min:.0f} min < {self.strategy.cooldown_after_profit_minutes} min)",
                )

        # After profit: require price > previous exit price before re-entry
        if (
            self.strategy.require_price_above_exit_after_profit
            and symbol in self.state.last_profit_exit_price
            and ohlcv_df is not None
            and not ohlcv_df.empty
        ):
            exit_price = self.state.last_profit_exit_price[symbol]
            current_close = float(ohlcv_df["close"].iloc[-1])
            if current_close <= exit_price:
                return TradeDecision(
                    allowed=False,
                    reason=f"re-entry after profit requires price > exit price ({current_close:.2f} <= {exit_price:.2f})",
                )

        # Log strategy inputs so "no entry signal" is debuggable
        if log_strategy_context and ohlcv_df is not None and not ohlcv_df.empty:
            try:
                close = ohlcv_df["close"]
                n = len(close)
                last_close = float(close.iloc[-1]) if n else None
                ma_f = float(close.rolling(self.strategy.ma_fast).mean().iloc[-1]) if n >= self.strategy.ma_fast else None
                ma_s = float(close.rolling(self.strategy.ma_slow).mean().iloc[-1]) if n >= self.strategy.ma_slow else None
                src = "news_override" if entry_override else "trend"
                log.info(
                    "strategy input %s: close=%.2f ma_fast(%d)=%s ma_slow(%d)=%s atr_pct=%s source=%s",
                    symbol,
                    last_close or 0.0,
                    self.strategy.ma_fast,
                    f"{ma_f:.2f}" if ma_f is not None else "n/a",
                    self.strategy.ma_slow,
                    f"{ma_s:.2f}" if ma_s is not None else "n/a",
                    f"{atr_pct:.2f}%" if atr_pct is not None else "n/a",
                    src,
                )
            except Exception as e:
                log.debug("strategy context log failed: %s", e)

        if entry_override is not None:
            entry = entry_override
        else:
            entry = self.strategy.generate_entry(symbol, ohlcv_df, spread_pct, atr_pct)
            if entry is None:
                return TradeDecision(allowed=False, reason="no entry signal")

        # Long-only: skip short signals (bearish with no position = skip, not sell-short)
        if entry.side and entry.side.lower() not in ("long", "buy"):
            return TradeDecision(allowed=False, reason="long-only: skipping non-long signal")

        # Position sizing
        current_with_stops = [
            (p.get("notional", 0), p.get("stop_pct", 0))
            for p in current_positions.values()
        ]
        open_risk_pct = self.sizer.total_open_risk_pct(account_equity, current_with_stops)
        sizing = self.sizer.size_position(
            account_equity=account_equity,
            price=ohlcv_df["close"].iloc[-1] if ohlcv_df is not None and not ohlcv_df.empty else 0.0,
            stop_distance_pct=entry.stop_pct,
            symbol=symbol,
            current_positions=current_positions,
            sector_exposure_pct=sector_exposure_pct,
            symbol_sector=symbol_sector,
            atr_pct=atr_pct,
            regime_size_multiplier=regime_size_multiplier,
        )
        if sizing.reject_reason:
            return TradeDecision(
                allowed=False,
                reason=sizing.reject_reason,
                entry_signal=entry,
            )
        if self.sizer.would_exceed_max_open_risk(open_risk_pct, entry.stop_pct, sizing.risk_pct):
            return TradeDecision(
                allowed=False,
                reason=f"would exceed max open risk ({self.sizer.max_open_risk_pct}%)",
                entry_signal=entry,
                position_sizing=sizing,
            )

        # Build order (limit preferred). Map long -> buy for execution/broker.
        mid = ohlcv_df["close"].iloc[-1] if ohlcv_df is not None and not ohlcv_df.empty else 0.0
        order_side = "buy" if (entry.side or "long").lower() in ("long", "buy") else "sell"
        order = self.execution.build_order(
            symbol=symbol,
            side=order_side,
            quantity=sizing.shares,
            mid_price=mid,
            spread_pct=spread_pct,
        )
        if order is None:
            return TradeDecision(
                allowed=False,
                reason="execution: order build failed (spread?)",
                entry_signal=entry,
                position_sizing=sizing,
            )

        # Clear stop-loss and profit-exit cooldown state for this symbol now that we allow entry
        self.state.last_stop_loss_at.pop(symbol, None)
        self.state.last_stopped_ref_price.pop(symbol, None)
        self.state.last_profit_exit_at.pop(symbol, None)
        self.state.last_profit_exit_price.pop(symbol, None)

        return TradeDecision(
            allowed=True,
            reason="ok",
            order_request=order,
            entry_signal=entry,
            position_sizing=sizing,
        )

    def check_exit(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        bars_held: int,
        spread_pct: float | None = None,
        atr_pct: float | None = None,
        *,
        partial_taken: bool = False,
        trail_high: float | None = None,
        current_qty: int = 0,
    ) -> ExitSignal | None:
        """atr_pct must be ATR% = (ATR/close)*100, not a ratio or multiple."""
        return self.strategy.check_exit(
            symbol,
            entry_price,
            current_price,
            bars_held,
            spread_pct,
            atr_pct,
            partial_taken=partial_taken,
            trail_high=trail_high,
            current_qty=current_qty,
        )
