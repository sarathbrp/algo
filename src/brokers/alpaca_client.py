"""
Alpaca broker integration: account, bars, quotes (spread), order submission.

Uses alpaca-py (TradingClient + StockHistoricalDataClient).

Credentials can be supplied in two ways (checked in order):
  1. Explicit ``api_key`` / ``secret`` / ``paper`` arguments (multi-user mode).
  2. Environment variables: ``APCA_API_KEY_ID`` / ``APCA_API_SECRET_KEY`` (paper only) or
     ``ALPACA_LIVE_API_KEY_ID`` / ``ALPACA_LIVE_API_SECRET_KEY`` (live only — paper keys are not used as fallback).

Retries on connection errors (RemoteDisconnected, ConnectionError) so the loop
doesn't crash.
"""
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable, TypeVar

import pandas as pd

T = TypeVar("T")

try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        GetOrdersRequest,
        GetPortfolioHistoryRequest,
        LimitOrderRequest,
        MarketOrderRequest,
    )
    from alpaca.trading.enums import OrderSide, TimeInForce
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest, StockLatestQuoteRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.data.enums import DataFeed
    ALPACA_AVAILABLE = True
except ImportError:
    ALPACA_AVAILABLE = False
    DataFeed = None

ALPACA_OPTIONS_CHAIN = False
OptionHistoricalDataClient = None
OptionChainRequest = None
OptionsFeed = None
if ALPACA_AVAILABLE:
    try:
        from alpaca.data.historical.option import OptionHistoricalDataClient
        from alpaca.data.requests import OptionChainRequest
        from alpaca.data.enums import OptionsFeed

        ALPACA_OPTIONS_CHAIN = True
    except ImportError:
        pass

from ..execution import OrderRequest, OrderType
from ..options_selector import OptionContractCandidate, parse_occ_equity_option_symbol


@dataclass
class QuoteInfo:
    bid: float
    ask: float
    mid: float
    spread_pct: float
    timestamp: datetime | None = None  # quote time (UTC); None = unknown

    def is_stale(self, max_age_seconds: float) -> bool:
        """True if quote is older than max_age_seconds (use for spread gate)."""
        if self.timestamp is None:
            return False  # unknown age: treat as fresh
        from datetime import timezone
        now = datetime.now(timezone.utc)
        ts = self.timestamp
        if getattr(ts, "tzinfo", None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (now - ts).total_seconds() > max_age_seconds


class AlpacaBroker:
    """Alpaca broker: account, historical bars, latest quote, order submission."""

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        api_key: str | None = None,
        secret: str | None = None,
        paper: bool | None = None,
    ):
        if not ALPACA_AVAILABLE:
            raise RuntimeError("alpaca-py is required for Alpaca broker. Install: pip install alpaca-py")
        self.config = config or {}
        broker_cfg = self.config.get("broker", {})

        # --- Resolve paper vs live -----------------------------------------
        if paper is not None:
            # Explicit argument takes priority (multi-user mode).
            self.paper = paper
        else:
            # Legacy: config → env overrides
            paper_cfg = broker_cfg.get("paper", True)
            apca_paper = _env("APCA_PAPER")
            alpaca_live = _env("ALPACA_LIVE")
            if apca_paper is not None:
                self.paper = str(apca_paper).strip().lower() in ("true", "1", "yes")
            elif alpaca_live is not None:
                self.paper = not (str(alpaca_live).strip().lower() in ("true", "1", "yes"))
            else:
                self.paper = paper_cfg

        # --- Resolve credentials -------------------------------------------
        if api_key is not None and secret is not None:
            # Explicit credentials (multi-user mode) — use directly.
            resolved_key = api_key
            resolved_secret = secret
        else:
            # Legacy: read from environment / config.
            if self.paper:
                resolved_key = _env("APCA_API_KEY_ID") or broker_cfg.get("api_key")
                resolved_secret = _env("APCA_API_SECRET_KEY") or broker_cfg.get("secret_key")
            else:
                # Live API rejects paper keys — do not fall back to APCA_* (avoids opaque 401/unauthorized).
                resolved_key = _env("ALPACA_LIVE_API_KEY_ID") or broker_cfg.get("api_key")
                resolved_secret = _env("ALPACA_LIVE_API_SECRET_KEY") or broker_cfg.get("secret_key")
        if not resolved_key or not resolved_secret:
            if self.paper:
                raise ValueError(
                    "Alpaca paper credentials required. Set APCA_API_KEY_ID and APCA_API_SECRET_KEY "
                    "(Alpaca dashboard → Paper Trading → API Keys)."
                )
            raise ValueError(
                "Alpaca LIVE credentials required. Set ALPACA_LIVE_API_KEY_ID and "
                "ALPACA_LIVE_API_SECRET_KEY (Alpaca dashboard → Live → API Keys). "
                "Paper keys (APCA_*) are not accepted on the live trading API."
            )

        self._trading = TradingClient(resolved_key, resolved_secret, paper=self.paper)
        self._data = StockHistoricalDataClient(resolved_key, resolved_secret)
        # IEX is free; SIP requires paid subscription ("subscription does not permit querying recent SIP data")
        feed_name = (broker_cfg.get("data_feed") or "iex").strip().upper()
        self._feed_enum = getattr(DataFeed, feed_name, DataFeed.IEX) if ALPACA_AVAILABLE else None
        self._retry_times = int(broker_cfg.get("api_retry_times", 3))
        self._retry_delay_sec = float(broker_cfg.get("api_retry_delay_sec", 3.0))
        self._option_data: Any = None
        self._options_feed: Any = None
        if ALPACA_OPTIONS_CHAIN and OptionHistoricalDataClient is not None and OptionsFeed is not None:
            self._option_data = OptionHistoricalDataClient(resolved_key, resolved_secret)
            opt_feed_name = (broker_cfg.get("options_feed") or "indicative").strip().lower()
            self._options_feed = getattr(OptionsFeed, opt_feed_name.upper(), OptionsFeed.INDICATIVE)

    def _with_retry(self, fn: Callable[[], T]) -> T:
        """Retry on connection errors (e.g. Remote end closed connection without response)."""
        last: BaseException | None = None
        for attempt in range(self._retry_times):
            try:
                return fn()
            except Exception as e:
                last = e
                name = type(e).__name__
                if "RemoteDisconnected" in name or "ConnectionError" in name or "Connection aborted" in str(e) or "ProtocolError" in name:
                    if attempt < self._retry_times - 1:
                        time.sleep(self._retry_delay_sec)
                        continue
                raise
        if last:
            raise last
        raise RuntimeError("retry failed")

    def get_equity(self) -> float:
        def _get() -> float:
            acc = self._trading.get_account()
            return float(acc.equity or 0)
        return self._with_retry(_get)

    def get_buying_power(self) -> float:
        """Cash available to open new positions (avoids Alpaca 403 insufficient buying power)."""
        def _get() -> float:
            acc = self._trading.get_account()
            return float(getattr(acc, "buying_power", 0) or getattr(acc, "cash", 0) or 0)
        return self._with_retry(_get)

    def get_account_snapshot(self) -> dict[str, Any]:
        """
        Current equity, last_equity (prior regular session close), cash.
        Session P&L (mark-to-market since prior close) ≈ equity - last_equity when last_equity is set.
        """
        def _get() -> dict[str, Any]:
            acc = self._trading.get_account()

            def fnum(x: Any) -> float | None:
                if x is None or x == "":
                    return None
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None

            return {
                "equity": fnum(getattr(acc, "equity", None)) or 0.0,
                "last_equity": fnum(getattr(acc, "last_equity", None)),
                "cash": fnum(getattr(acc, "cash", None)),
            }

        return self._with_retry(_get)

    def get_portfolio_daily_pnl_for_date(self, d: date) -> dict[str, Any] | None:
        """
        Alpaca portfolio history (1D bars): profit_loss / profit_loss_pct / equity for calendar date d in US/Eastern.
        Returns None if the API has no bar for that day (e.g. weekend) or on error.
        """
        try:
            import pytz
            from alpaca.trading.requests import GetPortfolioHistoryRequest

            et = pytz.timezone("America/New_York")
        except Exception:
            return None

        def _fetch() -> dict[str, Any] | None:
            req = GetPortfolioHistoryRequest(period="6M", timeframe="1D", date_end=d)
            ph = self._trading.get_portfolio_history(req)
            ts_list = list(ph.timestamp or [])
            pl_list = list(ph.profit_loss or [])
            plp_list = list(ph.profit_loss_pct or [])
            eq_list = list(ph.equity or [])
            if not ts_list:
                return None
            target_idx: int | None = None
            for i, ts in enumerate(ts_list):
                dt_et = datetime.fromtimestamp(int(ts), tz=pytz.UTC).astimezone(et)
                if dt_et.date() == d:
                    target_idx = i
            if target_idx is None:
                return None
            out: dict[str, Any] = {
                "profit_loss": float(pl_list[target_idx]) if target_idx < len(pl_list) else 0.0,
                "equity": float(eq_list[target_idx]) if target_idx < len(eq_list) else 0.0,
            }
            if target_idx < len(plp_list) and plp_list[target_idx] is not None:
                out["profit_loss_pct"] = float(plp_list[target_idx])
            else:
                out["profit_loss_pct"] = None
            return out

        try:
            return self._with_retry(_fetch)
        except Exception:
            return None

    def get_positions(self) -> list[dict[str, Any]]:
        def _get() -> list[dict[str, Any]]:
            positions = self._trading.get_all_positions()
            out = []
            for p in positions:
                out.append({
                    "symbol": p.symbol,
                    "qty": int(float(p.qty)),
                    "side": str(p.side),
                    "market_value": float(p.market_value or 0),
                    "cost_basis": float(p.cost_basis or 0),
                    "unrealized_pl": float(p.unrealized_pl or 0),
                })
            return out
        return self._with_retry(_get)

    def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Day",
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 300,
    ) -> pd.DataFrame:
        """Return OHLCV DataFrame with columns open, high, low, close, volume."""
        if end is None:
            end = datetime.utcnow()
        if start is None:
            if timeframe == "1Day":
                start = end - timedelta(days=400)
            else:
                start = end - timedelta(days=5)
        tf = TimeFrame.Day if timeframe == "1Day" else TimeFrame(1, "Min")
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=tf,
            start=start,
            end=end,
            feed=self._feed_enum,
        )
        bars = self._with_retry(lambda: self._data.get_stock_bars(req))
        if bars is None or getattr(bars, "df", None) is None:
            return pd.DataFrame()
        df = bars.df
        # BarSet.df can be multi-index (symbol, column) or (timestamp, column)
        if isinstance(df.columns, pd.MultiIndex):
            if symbol in df.columns.get_level_values(0):
                df = df[symbol].copy()
            else:
                return pd.DataFrame()
        need = {"open", "high", "low", "close", "volume"}
        renames = {
            "open_price": "open", "high_price": "high", "low_price": "low",
            "close_price": "close",
        }
        df = df.rename(columns=renames)
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        if len(cols) < 5:
            return pd.DataFrame()
        df = df[cols].astype(float)
        return df.tail(limit)

    def get_latest_quote(self, symbol: str) -> QuoteInfo | None:
        req = StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._feed_enum)
        quotes = self._with_retry(lambda: self._data.get_stock_latest_quote(req))
        if not quotes or symbol not in quotes:
            return None
        q = quotes[symbol]
        bid = float(q.bid_price or 0)
        ask = float(q.ask_price or 0)
        if bid <= 0 or ask <= 0:
            return None
        mid = (bid + ask) / 2.0
        spread_pct = (ask - bid) / mid * 100.0
        ts = None
        if hasattr(q, "timestamp") and q.timestamp is not None:
            ts = q.timestamp if isinstance(q.timestamp, datetime) else datetime.fromisoformat(str(q.timestamp).replace("Z", "+00:00"))
        return QuoteInfo(bid=bid, ask=ask, mid=mid, spread_pct=spread_pct, timestamp=ts)

    def get_option_chain_candidates(
        self,
        underlying: str,
        *,
        expiration_date_gte: date,
        expiration_date_lte: date,
    ) -> list[OptionContractCandidate]:
        """
        Option chain snapshots from Alpaca Market Data, mapped for `select_option_contract`.

        Requires options market data access (see broker.options_feed: indicative vs opra).
        Open interest is not in the snapshot model; set to 0 (use min_open_interest: 0 or rely on volume).
        """
        if self._option_data is None or OptionChainRequest is None:
            return []
        und = str(underlying or "").strip().upper()
        if not und:
            return []

        def _fetch() -> dict[str, Any]:
            req = OptionChainRequest(
                underlying_symbol=und,
                feed=self._options_feed,
                expiration_date_gte=expiration_date_gte,
                expiration_date_lte=expiration_date_lte,
            )
            return self._option_data.get_option_chain(req)

        try:
            chain = self._with_retry(_fetch)
        except Exception as e:
            print(
                datetime.now().strftime("%H:%M"),
                "option chain",
                und,
                type(e).__name__,
                str(e)[:120],
                flush=True,
            )
            return []

        if not chain:
            return []

        out: list[OptionContractCandidate] = []
        for sym, snap in chain.items():
            parsed = parse_occ_equity_option_symbol(sym)
            if parsed is None:
                continue
            root, exp, right, strike = parsed
            if root != und:
                continue
            lq = getattr(snap, "latest_quote", None)
            if lq is None:
                continue
            bid = float(getattr(lq, "bid_price", 0) or 0)
            ask = float(getattr(lq, "ask_price", 0) or 0)
            if bid <= 0 or ask <= 0:
                continue
            lt = getattr(snap, "latest_trade", None)
            vol = int(float(getattr(lt, "size", 0) or 0)) if lt is not None else 0
            out.append(
                OptionContractCandidate(
                    symbol=str(sym).strip().upper(),
                    strike=strike,
                    expiration=exp,
                    right=right,
                    open_interest=0,
                    volume=vol,
                    bid=bid,
                    ask=ask,
                )
            )
        return out

    def submit_order(self, order: OrderRequest) -> Any:
        """Submit order to Alpaca. Returns Alpaca order object."""
        side = OrderSide.BUY if order.side.lower() == "buy" else OrderSide.SELL
        tif = TimeInForce.DAY
        if order.order_type == OrderType.LIMIT and order.limit_price is not None:
            limit_price = float(order.limit_price)
            req = LimitOrderRequest(
                symbol=order.symbol,
                qty=order.quantity,
                side=side,
                time_in_force=tif,
                limit_price=limit_price,
            )
        else:
            req = MarketOrderRequest(
                symbol=order.symbol,
                qty=order.quantity,
                side=side,
                time_in_force=tif,
            )
        return self._trading.submit_order(order_data=req)

    def get_order(self, order_id: str) -> Any:
        return self._trading.get_order_by_id(order_id)

    def close_all_positions(self, cancel_orders: bool = True) -> list[Any]:
        """Liquidate all open positions. If cancel_orders is True, cancel open orders first.
        Returns list of close-position responses. Only use on paper accounts for reset."""
        def _close() -> list[Any]:
            return self._trading.close_all_positions(cancel_orders=cancel_orders) or []
        return self._with_retry(_close)

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Return all open (pending) orders. Used to avoid placing a second order for the same symbol."""
        req = GetOrdersRequest(status="open", limit=500)
        orders = self._with_retry(lambda: self._trading.get_orders(req))
        out = []
        for o in orders or []:
            out.append({
                "id": str(getattr(o, "id", "")),
                "symbol": getattr(o, "symbol", ""),
                "side": str(getattr(o, "side", "")),
                "qty": int(float(getattr(o, "qty", 0) or 0)),
            })
        return out

    def get_orders_for_date(self, trade_date: "datetime | date") -> list[dict[str, Any]]:
        """Return orders (filled or closed) that were submitted on the given date (ET)."""
        from datetime import date as date_type
        if hasattr(trade_date, "date"):
            d = trade_date.date()
        else:
            d = trade_date
        # Alpaca expects UTC; use ET day boundaries
        try:
            import pytz
            et = pytz.timezone("America/New_York")
            after = et.localize(datetime(d.year, d.month, d.day, 0, 0, 0))
            until = et.localize(datetime(d.year, d.month, d.day, 23, 59, 59)) + timedelta(seconds=1)
            after_utc = after.astimezone(pytz.UTC)
            until_utc = until.astimezone(pytz.UTC)
        except Exception:
            after_utc = datetime(d.year, d.month, d.day, 0, 0, 0)
            until_utc = datetime(d.year, d.month, d.day, 23, 59, 59)
        req = GetOrdersRequest(status="closed", after=after_utc, until=until_utc, limit=500)
        orders = self._trading.get_orders(req)
        out = []
        for o in orders or []:
            filled = getattr(o, "filled_avg_price", None) or getattr(o, "filled_average_price", None)
            out.append({
                "id": str(getattr(o, "id", "")),
                "symbol": getattr(o, "symbol", ""),
                "side": str(getattr(o, "side", "")),
                "qty": int(float(getattr(o, "filled_qty", 0) or getattr(o, "qty", 0) or 0)),
                "filled_avg_price": float(filled) if filled is not None else None,
                "submitted_at": getattr(o, "submitted_at", None),
                "filled_at": getattr(o, "filled_at", None),
            })
        return out


def _env(key: str) -> str | None:
    import os
    return os.environ.get(key)
