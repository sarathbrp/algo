"""SQLAlchemy ORM models for AlgoSphere.

All tables are designed for TiDB (MySQL-compatible).  Vector columns use
``TiDBVector`` which falls back to TEXT on SQLite for CI compatibility.

Tables
------
- users              — trader accounts (auth + config)
- portfolio_snapshots — point-in-time equity/cash snapshots per user
- positions          — current open positions per user
- trades             — completed trade history (vector-ready for agents)
- regime_log         — market regime state over time (vector-ready)
- gate_log           — 12-gate pipeline decisions per loop
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.db.vector_type import TiDBVector


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class UserRole(str, enum.Enum):
    admin = "admin"
    trader = "trader"


class TradeSide(str, enum.Enum):
    long = "long"
    short = "short"


class RegimeLabel(str, enum.Enum):
    bullish = "bullish"
    neutral = "neutral"
    bearish = "bearish"


class TradingMode(str, enum.Enum):
    paper = "paper"
    live = "live"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    """A trader account in AlgoSphere."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.trader
    )
    paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    alpaca_key_env: Mapped[str | None] = mapped_column(String(128), nullable=True)
    alpaca_secret_env: Mapped[str | None] = mapped_column(String(128), nullable=True)
    risk_profile: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    snapshots: Mapped[list[PortfolioSnapshot]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    positions: Mapped[list[Position]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    trades: Mapped[list[Trade]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    regime_logs: Mapped[list[RegimeLog]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    gate_logs: Mapped[list[GateLog]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    broker_accounts: Mapped[list[BrokerAccount]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    user_settings: Mapped[UserSettings | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    daily_summaries: Mapped[list[DailySummary]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    order_logs: Mapped[list[OrderLog]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    position_snapshots: Mapped[list[PositionSnapshot]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    trading_rules: Mapped[list[TradingRule]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User id={self.id!r} role={self.role.value}>"


class PortfolioSnapshot(Base):
    """Point-in-time equity/cash snapshot for a user."""

    __tablename__ = "portfolio_snapshots"
    __table_args__ = (
        Index("idx_snapshot_user_time", "user_id", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    equity: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    cash: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    buying_power: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    daily_pnl: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    daily_pnl_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="snapshots")

    def __repr__(self) -> str:
        return f"<PortfolioSnapshot user={self.user_id!r} equity={self.equity}>"


class BrokerAccount(Base):
    """Linked external broker account for a user."""

    __tablename__ = "broker_accounts"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_broker_account_user"),
        UniqueConstraint("provider", "provider_account_id", name="uq_broker_account_provider_ref"),
        Index("idx_broker_account_active", "is_active"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="alpaca")
    provider_account_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    paper: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    credentials_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="broker_accounts")
    settings: Mapped[AccountSettings | None] = relationship(
        back_populates="broker_account", cascade="all, delete-orphan", uselist=False
    )

    def __repr__(self) -> str:
        return f"<BrokerAccount user={self.user_id!r} provider={self.provider!r}>"


class UserSettings(Base):
    """Per-user dashboard and notification preferences."""

    __tablename__ = "user_settings"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_settings_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    theme: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    dashboard_layout: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/New_York")
    notifications_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="user_settings")

    def __repr__(self) -> str:
        return f"<UserSettings user={self.user_id!r} theme={self.theme!r}>"


class AccountSettings(Base):
    """Trading and risk settings applied to a broker account."""

    __tablename__ = "account_settings"
    __table_args__ = (
        UniqueConstraint("broker_account_id", name="uq_account_settings_account"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    broker_account_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("broker_accounts.id", ondelete="CASCADE"), nullable=False
    )
    trading_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    bot_state: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    strategy_slug: Mapped[str] = mapped_column(String(64), nullable=False, default="trend_following")
    risk_profile: Mapped[str] = mapped_column(String(32), nullable=False, default="balanced")
    max_positions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    broker_account: Mapped[BrokerAccount] = relationship(back_populates="settings")

    def __repr__(self) -> str:
        return f"<AccountSettings account_id={self.broker_account_id!r} strategy={self.strategy_slug!r}>"


class WorkerStatus(Base):
    """Health and reconciliation status for a long-running worker process."""

    __tablename__ = "worker_status"
    __table_args__ = (
        UniqueConstraint("worker_name", name="uq_worker_status_name"),
        Index("idx_worker_status_updated", "updated_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    worker_name: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="starting")
    current_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_heartbeat: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    last_reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self) -> str:
        return f"<WorkerStatus worker_name={self.worker_name!r} status={self.status!r}>"


class Position(Base):
    """Current open position for a user."""

    __tablename__ = "positions"
    __table_args__ = (
        Index("idx_position_user", "user_id"),
        UniqueConstraint("user_id", "symbol", name="uq_position_user_symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide), nullable=False, default=TradeSide.long)
    qty: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    avg_entry_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    last_buy_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    current_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    stop_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    partial_taken: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trail_high: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="positions")

    def __repr__(self) -> str:
        return f"<Position user={self.user_id!r} symbol={self.symbol!r} qty={self.qty}>"


class Trade(Base):
    """Completed trade record.  ``setup_vector`` reserved for agent similarity search."""

    __tablename__ = "trades"
    __table_args__ = (
        Index("idx_trade_user", "user_id"),
        Index("idx_trade_user_time", "user_id", "exited_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide), nullable=False, default=TradeSide.long)
    qty: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    entry_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    exit_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    pnl: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    pnl_pct: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    entered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Reserved for agent embedding of market conditions at trade entry
    setup_vector: Mapped[list[float] | None] = mapped_column(TiDBVector(1536), nullable=True)

    user: Mapped[User] = relationship(back_populates="trades")

    def __repr__(self) -> str:
        return f"<Trade user={self.user_id!r} symbol={self.symbol!r} pnl={self.pnl}>"


class RegimeLog(Base):
    """Market regime state snapshot.  ``state_vector`` reserved for agent retrieval."""

    __tablename__ = "regime_log"
    __table_args__ = (
        Index("idx_regime_user_time", "user_id", "logged_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    label: Mapped[RegimeLabel] = mapped_column(Enum(RegimeLabel), nullable=False)
    spy_score: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    qqq_score: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    vix: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    # Reserved for agent similarity search ("find regimes like now")
    state_vector: Mapped[list[float] | None] = mapped_column(TiDBVector(256), nullable=True)

    user: Mapped[User] = relationship(back_populates="regime_logs")

    def __repr__(self) -> str:
        return f"<RegimeLog user={self.user_id!r} label={self.label.value}>"


class GateLog(Base):
    """12-gate pipeline decision log entry."""

    __tablename__ = "gate_log"
    __table_args__ = (
        Index("idx_gate_user_time", "user_id", "logged_at"),
        Index("idx_gate_user_gate", "user_id", "gate"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    gate: Mapped[str] = mapped_column(String(64), nullable=False)
    symbol: Mapped[str | None] = mapped_column(String(16), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="gate_logs")

    def __repr__(self) -> str:
        return f"<GateLog user={self.user_id!r} gate={self.gate!r} passed={self.passed}>"


class DailySummary(Base):
    """End-of-day trading summary for a user."""

    __tablename__ = "daily_summaries"
    __table_args__ = (
        Index("idx_daily_summary_user_date", "user_id", "date"),
        UniqueConstraint("user_id", "date", "mode", name="uq_daily_summary_user_date_mode"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    open_equity: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    close_equity: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    daily_pnl: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    daily_pnl_pct: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    daily_return_pct: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    win_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    loss_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_trades_today: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Numeric(10, 6), nullable=True)
    positions_opened: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    positions_closed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="daily_summaries")

    def __repr__(self) -> str:
        return f"<DailySummary user={self.user_id!r} date={self.date} mode={self.mode!r}>"


class OrderLog(Base):
    """Log of all order events (buy/sell) for audit trail."""

    __tablename__ = "order_log"
    __table_args__ = (
        Index("idx_order_log_user_time", "user_id", "created_at"),
        Index("idx_order_log_user_symbol", "user_id", "symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    order_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    broker_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="order_logs")

    def __repr__(self) -> str:
        return f"<OrderLog user={self.user_id!r} {self.side} {self.symbol} qty={self.qty}>"


class TradingRule(Base):
    """User-defined trading rule for the rules engine."""

    __tablename__ = "trading_rules"
    __table_args__ = (
        Index("idx_rule_user", "user_id"),
        Index("idx_rule_user_active", "user_id", "is_active"),
        Index("idx_rule_user_symbol", "user_id", "symbol"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    rule_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "entry" or "exit"
    rule_tree: Mapped[str] = mapped_column(Text, nullable=False)  # JSON rule tree
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="trading_rules")

    def __repr__(self) -> str:
        return f"<TradingRule user={self.user_id!r} name={self.name!r} type={self.rule_type!r}>"


class PositionSnapshot(Base):
    """Historical snapshot of a position at a point in time."""

    __tablename__ = "position_snapshots"
    __table_args__ = (
        Index("idx_pos_snapshot_user_time", "user_id", "captured_at"),
        Index("idx_pos_snapshot_symbol", "user_id", "symbol", "captured_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(16), nullable=False)
    side: Mapped[TradeSide] = mapped_column(Enum(TradeSide), nullable=False)
    qty: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    avg_entry_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    current_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    unrealized_pnl: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    mode: Mapped[str | None] = mapped_column(String(8), nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    user: Mapped[User] = relationship(back_populates="position_snapshots")

    def __repr__(self) -> str:
        return f"<PositionSnapshot user={self.user_id!r} {self.symbol} qty={self.qty}>"
