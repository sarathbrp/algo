"""Per-user data endpoints — portfolio, positions, trades, gate-log, regime."""
from __future__ import annotations

import logging
import os
from typing import Any, Literal

_redis_url = os.environ.get("REDIS_URL")


def _signal_worker_reload(user_id: str) -> None:
    """Set a Redis key telling the worker to reload this user's credentials."""
    if not _redis_url:
        return
    try:
        import redis
        r = redis.from_url(_redis_url, decode_responses=True)
        r.setex(f"algosphere:reload:{user_id}", 300, "1")
    except Exception:
        pass

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, Field

from src.api.deps import CurrentUser, DbSession
from src.db.models import UserRole
from src.db.repos import (
    account_repo, daily_summary_repo, gate_log_repo, order_log_repo,
    portfolio_repo, position_snapshot_repo, regime_repo, rule_repo,
    trade_repo, user_repo, worker_repo,
)
from src.worker.control import (
    BOT_STATE_PAUSED,
    BOT_STATE_RUNNING,
    BOT_STATE_STOPPED,
    describe_bot_state,
    normalize_bot_state,
)

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users/{user_id}", tags=["users"])


def _check_access(current_user, user_id: str) -> None:
    """Users can only access their own data unless they are admin."""
    if current_user.role != UserRole.admin and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


# ---------------------------------------------------------------------------
# Pydantic response models
# ---------------------------------------------------------------------------

class SnapshotOut(BaseModel):
    equity: float | None
    cash: float | None
    buying_power: float | None
    daily_pnl: float | None
    daily_pnl_pct: float | None
    mode: str | None = None
    captured_at: str

    model_config = {"from_attributes": True}


class PortfolioOut(BaseModel):
    latest: SnapshotOut | None
    history: list[SnapshotOut]


class PositionOut(BaseModel):
    symbol: str
    side: str
    qty: float
    avg_entry_price: float | None
    last_buy_price: float | None
    current_price: float | None
    unrealized_pnl: float | None
    stop_pct: float | None
    partial_taken: bool
    pending_sell: bool = Field(False, description="True if there's an open sell order queued for this position")

    model_config = {"from_attributes": True}


class TradeOut(BaseModel):
    id: int
    symbol: str
    side: str
    qty: float
    entry_price: float | None
    exit_price: float | None
    pnl: float | None
    pnl_pct: float | None
    exit_reason: str | None
    mode: str | None = None
    entered_at: str | None
    exited_at: str | None

    model_config = {"from_attributes": True}


class GateLogOut(BaseModel):
    id: int
    gate: str
    symbol: str | None
    passed: bool
    reason: str | None
    logged_at: str

    model_config = {"from_attributes": True}


class RegimeOut(BaseModel):
    label: str
    spy_score: float | None
    qqq_score: float | None
    vix: float | None
    logged_at: str

    model_config = {"from_attributes": True}


class AccountSettingsOut(BaseModel):
    broker_account_id: int
    paper: bool
    trading_enabled: bool
    bot_state: str
    bot_state_description: str
    strategy_slug: str
    risk_profile: str
    max_positions: int | None
    has_credentials: bool
    worker_status: str | None
    worker_current_user_id: str | None
    worker_last_heartbeat: str | None
    worker_last_reconciled_at: str | None


class BotControlUpdate(BaseModel):
    bot_state: Literal["running", "paused", "stopped"]


class QuoteOut(BaseModel):
    symbol: str
    bid: float
    ask: float
    mid: float
    spread_pct: float
    change_pct: float | None = Field(None, description="% change from previous close")
    prev_close: float | None = Field(None, description="Previous day's closing price")
    timestamp: str
    source: str
    stale: bool


class QuotesOut(BaseModel):
    feed_status: str | None
    feed_timestamp: str | None
    quotes: list[QuoteOut]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _snap_to_out(snap) -> SnapshotOut:
    return SnapshotOut(
        equity=float(snap.equity) if snap.equity is not None else None,
        cash=float(snap.cash) if snap.cash is not None else None,
        buying_power=float(snap.buying_power) if snap.buying_power is not None else None,
        daily_pnl=float(snap.daily_pnl) if snap.daily_pnl is not None else None,
        daily_pnl_pct=float(snap.daily_pnl_pct) if snap.daily_pnl_pct is not None else None,
        mode=getattr(snap, "mode", None),
        captured_at=snap.captured_at.isoformat(),
    )


def _pos_to_out(pos) -> PositionOut:
    return PositionOut(
        symbol=pos.symbol,
        side=pos.side.value,
        qty=float(pos.qty),
        avg_entry_price=float(pos.avg_entry_price) if pos.avg_entry_price is not None else None,
        last_buy_price=float(pos.last_buy_price) if getattr(pos, "last_buy_price", None) is not None else None,
        current_price=float(pos.current_price) if pos.current_price is not None else None,
        unrealized_pnl=float(pos.unrealized_pnl) if pos.unrealized_pnl is not None else None,
        stop_pct=float(pos.stop_pct) if pos.stop_pct is not None else None,
        partial_taken=pos.partial_taken,
    )


def _trade_to_out(t) -> TradeOut:
    return TradeOut(
        id=t.id,
        symbol=t.symbol,
        side=t.side.value,
        qty=float(t.qty),
        entry_price=float(t.entry_price) if t.entry_price is not None else None,
        exit_price=float(t.exit_price) if t.exit_price is not None else None,
        pnl=float(t.pnl) if t.pnl is not None else None,
        pnl_pct=float(t.pnl_pct) if t.pnl_pct is not None else None,
        exit_reason=t.exit_reason,
        mode=getattr(t, "mode", None),
        entered_at=t.entered_at.isoformat() if t.entered_at else None,
        exited_at=t.exited_at.isoformat() if t.exited_at else None,
    )


def _gate_to_out(g) -> GateLogOut:
    return GateLogOut(
        id=g.id,
        gate=g.gate,
        symbol=g.symbol,
        passed=g.passed,
        reason=g.reason,
        logged_at=g.logged_at.isoformat(),
    )


def _regime_to_out(r) -> RegimeOut:
    return RegimeOut(
        label=r.label.value,
        spy_score=float(r.spy_score) if r.spy_score is not None else None,
        qqq_score=float(r.qqq_score) if r.qqq_score is not None else None,
        vix=float(r.vix) if r.vix is not None else None,
        logged_at=r.logged_at.isoformat(),
    )


def _quote_to_out(quote) -> QuoteOut:
    return QuoteOut(
        symbol=quote.symbol,
        bid=float(quote.bid),
        ask=float(quote.ask),
        mid=float(quote.mid),
        spread_pct=float(quote.spread_pct),
        timestamp=quote.timestamp.isoformat(),
        source=quote.source,
        stale=bool(quote.is_stale()),
    )


def _get_quote_cache(request: Request):
    return getattr(request.app.state, "quote_cache", None)


def _get_live_broker(session, user_id: str) -> Any | None:
    """Build a temporary AlpacaBroker from stored user credentials. Returns None on failure."""
    from src.api.broker_utils import get_live_broker
    return get_live_broker(session, user_id)


def _account_settings_to_out(session, broker_account, settings) -> AccountSettingsOut:
    bot_state = normalize_bot_state(getattr(settings, "bot_state", None), trading_enabled=settings.trading_enabled)
    worker = worker_repo.get_worker_status(session, "alpaca_loop")
    user = user_repo.get_by_id(session, broker_account.user_id)
    has_credentials = bool(
        user is not None
        and getattr(user, "alpaca_key_env", None)
        and getattr(user, "alpaca_secret_env", None)
    )
    return AccountSettingsOut(
        broker_account_id=broker_account.id,
        paper=bool(broker_account.paper),
        trading_enabled=bool(settings.trading_enabled),
        bot_state=bot_state,
        bot_state_description=describe_bot_state(bot_state),
        strategy_slug=settings.strategy_slug,
        risk_profile=settings.risk_profile,
        has_credentials=has_credentials,
        max_positions=settings.max_positions,
        worker_status=worker.status if worker is not None else None,
        worker_current_user_id=worker.current_user_id if worker is not None else None,
        worker_last_heartbeat=worker.last_heartbeat.isoformat() if worker is not None and worker.last_heartbeat else None,
        worker_last_reconciled_at=worker.last_reconciled_at.isoformat() if worker is not None and worker.last_reconciled_at else None,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/portfolio", response_model=PortfolioOut)
def get_portfolio(
    user_id: str,
    session: DbSession,
    current_user: CurrentUser,
    history_limit: int = Query(default=100, ge=1, le=500),
) -> PortfolioOut:
    _check_access(current_user, user_id)
    history = portfolio_repo.get_equity_history(session, user_id, limit=history_limit)

    # Try live broker data for the latest snapshot
    live_snap = None
    broker = _get_live_broker(session, user_id)
    if broker:
        try:
            snap = broker.get_account_snapshot()
            equity = snap.get("equity", 0.0)
            last_eq = snap.get("last_equity")
            daily_pnl = (equity - last_eq) if last_eq else None
            daily_pnl_pct = (daily_pnl / last_eq * 100) if last_eq and daily_pnl is not None else None
            from datetime import datetime, timezone
            live_snap = SnapshotOut(
                equity=equity,
                cash=snap.get("cash"),
                buying_power=broker.get_buying_power(),
                daily_pnl=daily_pnl,
                daily_pnl_pct=daily_pnl_pct,
                captured_at=datetime.now(timezone.utc).isoformat(),
            )
        except Exception:
            pass

    if live_snap is None:
        db_latest = portfolio_repo.get_latest_snapshot(session, user_id)
        live_snap = _snap_to_out(db_latest) if db_latest else None

    return PortfolioOut(
        latest=live_snap,
        history=[_snap_to_out(s) for s in history],
    )


@router.get("/positions", response_model=list[PositionOut])
def get_positions(user_id: str, session: DbSession, current_user: CurrentUser) -> list[PositionOut]:
    _check_access(current_user, user_id)

    # Try live broker positions for real-time prices
    broker = _get_live_broker(session, user_id)
    if broker:
        try:
            live_positions = broker.get_positions()
            db_positions = {p.symbol.upper(): p for p in portfolio_repo.get_positions(session, user_id)}
            out: list[PositionOut] = []
            for bp in live_positions:
                symbol = str(bp.get("symbol", "")).upper()
                qty_raw = int(float(bp.get("qty") or 0))
                qty = abs(qty_raw)
                if qty <= 0:
                    continue
                side_raw = str(bp.get("side") or "long").strip().lower()
                side = side_raw if side_raw in ("long", "short") else ("short" if qty_raw < 0 else "long")
                market_value = float(bp.get("market_value") or 0)
                cost_basis = float(bp.get("cost_basis") or 0)
                current_price = abs(market_value / qty) if qty else None
                avg_entry = abs(cost_basis / qty_raw) if qty_raw else None
                unrealized = float(bp.get("unrealized_pl") or 0)
                db_pos = db_positions.get(symbol)
                _qa = bp.get("qty_available")
                qty_available = int(float(_qa)) if _qa is not None else qty
                out.append(PositionOut(
                    symbol=symbol,
                    side=side,
                    qty=float(qty),
                    avg_entry_price=avg_entry,
                    last_buy_price=float(db_pos.last_buy_price) if db_pos and db_pos.last_buy_price else avg_entry,
                    current_price=current_price,
                    unrealized_pnl=unrealized,
                    stop_pct=float(db_pos.stop_pct) if db_pos and db_pos.stop_pct else 1.5,
                    partial_taken=bool(db_pos.partial_taken) if db_pos else False,
                    pending_sell=qty_available == 0 and qty > 0,
                ))
            return out
        except Exception as exc:
            _logger.debug("Live positions failed for %s: %s", user_id, exc)

    return [_pos_to_out(p) for p in portfolio_repo.get_positions(session, user_id)]


@router.get("/trades", response_model=list[TradeOut])
def get_trades(
    user_id: str,
    session: DbSession,
    current_user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[TradeOut]:
    _check_access(current_user, user_id)
    return [_trade_to_out(t) for t in trade_repo.get_trades(session, user_id, limit=limit)]


@router.get("/gate-log", response_model=list[GateLogOut])
def get_gate_log(
    user_id: str,
    session: DbSession,
    current_user: CurrentUser,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[GateLogOut]:
    _check_access(current_user, user_id)
    # Filter out entry skips (passed=False, gate="entry") — users see actions, not skips
    all_logs = gate_log_repo.get_recent(session, user_id, limit=limit * 3)
    filtered = [g for g in all_logs if not (g.gate == "entry" and not g.passed)]
    return [_gate_to_out(g) for g in filtered[:limit]]


@router.get("/regime", response_model=RegimeOut | None)
def get_regime(user_id: str, session: DbSession, current_user: CurrentUser) -> RegimeOut | None:
    _check_access(current_user, user_id)
    latest = regime_repo.get_latest(session, user_id)
    return _regime_to_out(latest) if latest else None


@router.get("/account-settings", response_model=AccountSettingsOut | None)
def get_account_settings(user_id: str, session: DbSession, current_user: CurrentUser) -> AccountSettingsOut | None:
    _check_access(current_user, user_id)
    broker_account = account_repo.get_broker_account_for_user(session, user_id)
    if broker_account is None:
        return None
    settings = account_repo.get_account_settings(session, broker_account.id)
    if settings is None:
        return None
    return _account_settings_to_out(session, broker_account, settings)


@router.patch("/bot-control", response_model=AccountSettingsOut)
def update_bot_control(
    user_id: str,
    body: BotControlUpdate,
    session: DbSession,
    current_user: CurrentUser,
) -> AccountSettingsOut:
    _check_access(current_user, user_id)
    broker_account = account_repo.get_broker_account_for_user(session, user_id)
    if broker_account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Broker account not found")

    bot_state = normalize_bot_state(body.bot_state)
    trading_enabled = bot_state == BOT_STATE_RUNNING
    existing_settings = account_repo.get_account_settings(session, broker_account.id)
    settings = account_repo.upsert_account_settings(
        session,
        broker_account_id=broker_account.id,
        trading_enabled=trading_enabled,
        bot_state=bot_state,
        strategy_slug=existing_settings.strategy_slug if existing_settings is not None else "trend_following",
        risk_profile=existing_settings.risk_profile if existing_settings is not None else "balanced",
        max_positions=existing_settings.max_positions if existing_settings is not None else None,
    )
    return _account_settings_to_out(session, broker_account, settings)


@router.get("/quotes", response_model=QuotesOut)
def get_quotes(
    user_id: str,
    request: Request,
    session: DbSession,
    current_user: CurrentUser,
    symbols: list[str] = Query(default=[]),
) -> QuotesOut:
    _check_access(current_user, user_id)
    cache = _get_quote_cache(request)

    normalized_symbols = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    if not normalized_symbols:
        normalized_symbols = [p.symbol.upper() for p in portfolio_repo.get_positions(session, user_id)]

    feed_status = None
    feed_ts = None
    quotes = []

    # Try Redis cache first
    if cache is not None:
        feed_status, feed_ts = cache.get_feed_status()
        for symbol in normalized_symbols:
            quote = cache.get_quote(symbol)
            if quote is not None:
                quotes.append(_quote_to_out(quote))

    # For symbols not in cache, try live broker
    cached_symbols = {q.symbol for q in quotes}
    missing = [s for s in normalized_symbols if s not in cached_symbols]
    if missing:
        broker = _get_live_broker(session, user_id)
        if broker:
            from datetime import datetime, timezone
            for symbol in missing:
                try:
                    q = broker.get_latest_quote(symbol)
                    if q is not None:
                        quotes.append(QuoteOut(
                            symbol=symbol,
                            bid=float(q.bid),
                            ask=float(q.ask),
                            mid=float(q.mid),
                            spread_pct=float(q.spread_pct),
                            timestamp=(q.timestamp or datetime.now(timezone.utc)).isoformat(),
                            source="alpaca",
                            stale=False,
                        ))
                except Exception:
                    pass

    # Fetch prev close for % change on all symbols + fallback for missing quotes
    _prev_close_cache: dict[str, float] = {}
    broker_for_bars = _get_live_broker(session, user_id) if not missing else broker
    if broker_for_bars:
        from datetime import datetime, timezone
        for symbol in normalized_symbols:
            try:
                df = broker_for_bars.get_bars(symbol, timeframe="1Day", limit=3)
                if len(df) >= 2:
                    _prev_close_cache[symbol] = float(df["close"].iloc[-2])
                    # Fallback: if no quote yet, use last bar close
                    if symbol not in {q.symbol for q in quotes}:
                        close = float(df["close"].iloc[-1])
                        prev = float(df["close"].iloc[-2])
                        change = ((close - prev) / prev * 100) if prev > 0 else None
                        quotes.append(QuoteOut(
                            symbol=symbol, bid=close, ask=close, mid=close,
                            spread_pct=0.0, change_pct=change, prev_close=prev,
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            source="bar_close", stale=True,
                        ))
            except Exception:
                pass

    # Compute change_pct for quotes that don't have it yet
    for q in quotes:
        if q.change_pct is None and q.symbol in _prev_close_cache:
            prev = _prev_close_cache[q.symbol]
            if prev > 0:
                q.change_pct = (q.mid - prev) / prev * 100
                q.prev_close = prev

    return QuotesOut(
        feed_status=feed_status if feed_status else ("OK" if quotes else None),
        feed_timestamp=feed_ts.isoformat() if feed_ts is not None else None,
        quotes=quotes,
    )


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

class WatchlistOut(BaseModel):
    symbols: list[str]


class WatchlistUpdate(BaseModel):
    symbols: list[str]


@router.get("/watchlist", response_model=WatchlistOut)
def get_watchlist(
    user_id: str,
    session: DbSession,
    current_user: CurrentUser,
) -> WatchlistOut:
    _check_access(current_user, user_id)
    settings = account_repo.get_user_settings(session, user_id)
    if settings is None or not settings.dashboard_layout:
        return WatchlistOut(symbols=[])
    import json
    try:
        layout = json.loads(settings.dashboard_layout)
        return WatchlistOut(symbols=layout.get("watchlist", []))
    except (json.JSONDecodeError, AttributeError):
        return WatchlistOut(symbols=[])


@router.put("/watchlist", response_model=WatchlistOut)
def update_watchlist(
    user_id: str,
    body: WatchlistUpdate,
    session: DbSession,
    current_user: CurrentUser,
) -> WatchlistOut:
    _check_access(current_user, user_id)
    import json
    symbols = [s.strip().upper() for s in body.symbols if s.strip()]

    settings = account_repo.get_user_settings(session, user_id)
    layout = {}
    if settings and settings.dashboard_layout:
        try:
            layout = json.loads(settings.dashboard_layout)
        except (json.JSONDecodeError, AttributeError):
            layout = {}
    layout["watchlist"] = symbols

    account_repo.upsert_user_settings(
        session,
        user_id=user_id,
        theme=settings.theme if settings else "system",
        dashboard_layout=json.dumps(layout),
        timezone=settings.timezone if settings else "America/New_York",
        notifications_enabled=settings.notifications_enabled if settings else True,
    )
    return WatchlistOut(symbols=symbols)


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

class OnboardRequest(BaseModel):
    alpaca_key: str
    alpaca_secret: str
    paper: bool = True
    risk_profile: str = "balanced"


class OnboardResponse(BaseModel):
    ok: bool = True


@router.put("/onboard", response_model=OnboardResponse)
def onboard(
    user_id: str,
    body: OnboardRequest,
    session: DbSession,
    current_user: CurrentUser,
) -> OnboardResponse:
    _check_access(current_user, user_id)
    user = user_repo.get_by_id(session, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    user.alpaca_key_env = body.alpaca_key
    user.alpaca_secret_env = body.alpaca_secret
    user.paper = body.paper
    user.risk_profile = body.risk_profile
    broker_account = account_repo.get_broker_account_for_user(session, user_id)
    if broker_account is None:
        broker_account = account_repo.create_broker_account(
            session,
            user_id=user_id,
            provider="alpaca",
            paper=body.paper,
            credentials_ref=f"user:{user_id}:alpaca",
            is_active=True,
        )
        session.flush()
    else:
        broker_account.paper = body.paper
        broker_account.credentials_ref = f"user:{user_id}:alpaca"
        broker_account.is_active = True

    account_repo.upsert_account_settings(
        session,
        broker_account_id=broker_account.id,
        trading_enabled=True,
        bot_state=BOT_STATE_RUNNING,
        strategy_slug="trend_following",
        risk_profile=body.risk_profile,
    )
    _signal_worker_reload(user_id)
    return OnboardResponse()


# ---------------------------------------------------------------------------
# Trading history endpoints
# ---------------------------------------------------------------------------

class DailySummaryOut(BaseModel):
    date: str
    mode: str
    open_equity: float | None
    close_equity: float | None
    daily_pnl: float | None
    daily_pnl_pct: float | None
    daily_return_pct: float | None
    win_count: int
    loss_count: int
    total_trades_today: int
    max_drawdown_pct: float | None
    positions_opened: int
    positions_closed: int


class OrderLogOut(BaseModel):
    id: int
    symbol: str
    side: str
    qty: float
    price: float | None
    source: str | None
    mode: str | None
    created_at: str


class PositionSnapshotOut(BaseModel):
    symbol: str
    side: str
    qty: float
    avg_entry_price: float | None
    current_price: float | None
    unrealized_pnl: float | None
    mode: str | None
    captured_at: str


@router.get("/daily-summaries", response_model=list[DailySummaryOut])
def get_daily_summaries(
    user_id: str,
    session: DbSession,
    current_user: CurrentUser,
    mode: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=365),
) -> list[DailySummaryOut]:
    _check_access(current_user, user_id)
    summaries = daily_summary_repo.get_daily_summaries(session, user_id, mode=mode, limit=limit)
    return [
        DailySummaryOut(
            date=s.date.isoformat() if hasattr(s.date, 'isoformat') else str(s.date),
            mode=s.mode,
            open_equity=float(s.open_equity) if s.open_equity is not None else None,
            close_equity=float(s.close_equity) if s.close_equity is not None else None,
            daily_pnl=float(s.daily_pnl) if s.daily_pnl is not None else None,
            daily_pnl_pct=float(s.daily_pnl_pct) if s.daily_pnl_pct is not None else None,
            daily_return_pct=float(s.daily_return_pct) if s.daily_return_pct is not None else None,
            win_count=s.win_count,
            loss_count=s.loss_count,
            total_trades_today=s.total_trades_today,
            max_drawdown_pct=float(s.max_drawdown_pct) if s.max_drawdown_pct is not None else None,
            positions_opened=s.positions_opened,
            positions_closed=s.positions_closed,
        )
        for s in summaries
    ]


@router.get("/order-log", response_model=list[OrderLogOut])
def get_order_log(
    user_id: str,
    session: DbSession,
    current_user: CurrentUser,
    mode: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[OrderLogOut]:
    _check_access(current_user, user_id)
    orders = order_log_repo.get_orders(session, user_id, mode=mode, limit=limit)
    return [
        OrderLogOut(
            id=o.id,
            symbol=o.symbol,
            side=o.side,
            qty=float(o.qty),
            price=float(o.price) if o.price is not None else None,
            source=o.source,
            mode=o.mode,
            created_at=o.created_at.isoformat(),
        )
        for o in orders
    ]


@router.get("/position-history", response_model=list[PositionSnapshotOut])
def get_position_history(
    user_id: str,
    session: DbSession,
    current_user: CurrentUser,
    symbol: str | None = Query(default=None),
    mode: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[PositionSnapshotOut]:
    _check_access(current_user, user_id)
    snaps = position_snapshot_repo.get_position_history(session, user_id, symbol=symbol, mode=mode, limit=limit)
    return [
        PositionSnapshotOut(
            symbol=s.symbol,
            side=s.side.value,
            qty=float(s.qty),
            avg_entry_price=float(s.avg_entry_price) if s.avg_entry_price is not None else None,
            current_price=float(s.current_price) if s.current_price is not None else None,
            unrealized_pnl=float(s.unrealized_pnl) if s.unrealized_pnl is not None else None,
            mode=s.mode,
            captured_at=s.captured_at.isoformat(),
        )
        for s in snaps
    ]


# ---------------------------------------------------------------------------
# Position actions — sell / close
# ---------------------------------------------------------------------------

class SellPositionRequest(BaseModel):
    qty: int | None = Field(None, description="Number of shares to sell. If omitted, sells entire position.")
    order_type: str = Field("market", description="'market' (sells immediately) or 'limit' (sells only at your price or better)")
    limit_price: float | None = Field(None, description="Required for limit orders. The minimum price you'll accept.")
    time_in_force: str = Field("day", description="'day' (expires end of day) or 'gtc' (stays open until filled or canceled)")


class SellPositionResponse(BaseModel):
    success: bool
    symbol: str
    order_type: str
    qty_sold: str | None = None
    limit_price: float | None = None
    message: str


@router.post(
    "/positions/{symbol}/close",
    response_model=SellPositionResponse,
    summary="Sell / close a position",
    description="""Close an open position. Supports two order types:

- **market** (default) — sells immediately at the best available price. If the market is closed, the order queues and executes at market open.
- **limit** — sells only at your specified price or higher. Good for after-hours when you don't want to accept a gap-down opening price.

If qty is omitted, sells the entire position.""",
    responses={
        422: {"description": "Broker not configured or order failed"},
    },
)
def close_position(
    user_id: str,
    symbol: str,
    body: SellPositionRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> SellPositionResponse:
    _check_access(current_user, user_id)
    broker = _get_live_broker(db, user_id)
    if broker is None:
        raise HTTPException(status_code=422, detail="Broker not configured")

    sym = symbol.upper()

    # Cancel any existing open orders for this symbol first
    # (e.g. bot's stop-loss or trailing stop holding the shares)
    try:
        open_orders = broker.get_open_orders()
        for order in open_orders:
            if order.get("symbol", "").upper() == sym:
                order_id = order.get("id")
                if order_id:
                    try:
                        broker._trading.cancel_order_by_id(order_id)
                        _logger.info("[%s] Cancelled open order %s for %s before manual sell", user_id, order_id, sym)
                    except Exception:
                        pass  # order may have already filled/cancelled
    except Exception as exc:
        _logger.debug("[%s] Could not check/cancel open orders for %s: %s", user_id, sym, exc)

    # Get position info before selling (for trade record)
    entry_price = None
    pos_qty = None
    pos_side = "long"
    try:
        positions = broker.get_positions()
        pos = next((p for p in positions if p["symbol"].upper() == sym), None)
        if pos:
            entry_price = float(pos.get("avg_entry_price", 0) or pos.get("cost_basis", 0))
            pos_qty = int(float(pos.get("qty", 0)))
            pos_side = pos.get("side", "long")
    except Exception:
        pass

    try:
        from datetime import datetime, timezone
        sell_qty = body.qty or pos_qty
        fill_price = None
        order_id = None

        if body.order_type == "limit":
            if body.limit_price is None or body.limit_price <= 0:
                raise HTTPException(status_code=422, detail="Limit price is required for limit orders")
            qty = body.qty
            if qty is None:
                if pos_qty is None:
                    raise HTTPException(status_code=404, detail=f"No open position for {sym}")
                qty = pos_qty

            from alpaca.trading.requests import LimitOrderRequest
            from alpaca.trading.enums import OrderSide, TimeInForce
            tif = TimeInForce.GTC if body.time_in_force.lower() == "gtc" else TimeInForce.DAY
            req = LimitOrderRequest(
                symbol=sym, qty=qty, side=OrderSide.SELL,
                time_in_force=tif, limit_price=float(body.limit_price),
            )
            result = broker._trading.submit_order(order_data=req)
            order_id = str(getattr(result, "id", ""))
            fill_price = body.limit_price
            sell_qty = qty
            order_type_label = "limit"
            msg = f"Limit sell: {qty} shares of {sym} at ${body.limit_price:.2f} ({body.time_in_force.upper()})"
        else:
            result = broker.close_position(sym, qty=body.qty)
            order_id = str(getattr(result, "id", ""))
            fill_price = float(getattr(result, "filled_avg_price", 0) or 0) or None
            order_type_label = "market"
            status = getattr(result, "status", None)
            is_filled = str(status).lower() in ("filled", "partially_filled") if status else bool(fill_price)
            if is_filled:
                msg = f"Sold {sell_qty or 'all'} shares of {sym} at ${fill_price:.2f}" if fill_price else f"Sold {sell_qty or 'all'} shares of {sym}"
            else:
                msg = f"Sell order queued for {sell_qty or 'all'} shares of {sym} — will execute at market open (9:30 AM ET)"

        _logger.info("[%s] %s sell %s (qty=%s, fill=%s)", user_id, order_type_label, sym, sell_qty, fill_price)

        # Record in order_log
        try:
            order_log_repo.record_order(
                db, user_id=user_id, symbol=sym, side="sell",
                qty=float(sell_qty or 0), price=fill_price,
                order_type=order_type_label, source="manual",
                mode="paper" if getattr(broker, 'paper', True) else "live",
                broker_order_id=order_id,
            )
        except Exception as exc:
            _logger.debug("[%s] Failed to record order log: %s", user_id, exc)

        # Record as completed trade if we have entry info
        # For queued orders (market closed), use current_price as estimate
        if entry_price and entry_price > 0 and sell_qty:
            exit_px = fill_price
            if not exit_px or exit_px <= 0:
                # Order queued (market closed) — use current price as estimate
                try:
                    quote = broker.get_latest_quote(sym)
                    exit_px = float(getattr(quote, 'mid', 0) or 0) if quote else 0
                except Exception:
                    exit_px = 0
                # Still no price — use entry as placeholder
                if not exit_px or exit_px <= 0:
                    try:
                        for p_info in (broker.get_positions() or []):
                            if p_info.get("symbol", "").upper() == sym:
                                exit_px = float(p_info.get("current_price", 0) or 0)
                                break
                    except Exception:
                        pass

            exit_reason = "manual" if (fill_price and fill_price > 0) else "manual_pending"
            if exit_px and exit_px > 0:
                try:
                    pnl = (exit_px - entry_price) * sell_qty if pos_side == "long" \
                        else (entry_price - exit_px) * sell_qty
                    pnl_pct = ((exit_px - entry_price) / entry_price * 100) if pos_side == "long" \
                        else ((entry_price - exit_px) / entry_price * 100)
                    trade_repo.record_trade(
                        db, user_id=user_id, symbol=sym, side=pos_side,
                        qty=float(sell_qty), entry_price=entry_price, exit_price=exit_px,
                        pnl=pnl, pnl_pct=pnl_pct, exit_reason=exit_reason,
                        exited_at=datetime.now(timezone.utc),
                        mode="paper" if getattr(broker, 'paper', True) else "live",
                    )
                except Exception as exc:
                    _logger.debug("[%s] Failed to record trade: %s", user_id, exc)

        # Deactivate rules for this symbol after manual sell
        try:
            for _rule in rule_repo.get_rules(db, user_id, symbol=sym, active_only=True):
                rule_repo.update_rule(db, _rule, is_active=False)
            _logger.info("[%s] Deactivated rules for %s after manual sell", user_id, sym)
        except Exception:
            pass

        db.commit()

        return SellPositionResponse(
            success=True, symbol=sym, order_type=order_type_label,
            qty_sold=str(sell_qty or "all"), limit_price=body.limit_price,
            message=msg,
        )
    except HTTPException:
        raise
    except Exception as exc:
        _logger.error("[%s] Failed to sell %s: %s", user_id, symbol, exc)
        raise HTTPException(status_code=422, detail=f"Failed to sell: {exc}")
