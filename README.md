# Algorithmic Trading App

A rule-based algorithmic trading application that enforces **universe & data**, **entry/exit**, **position sizing**, **portfolio & drawdown**, **execution**, and **compliance** rules before any trade.

The structure is inspired by [QuantConnect Lean](https://github.com/QuantConnect/Lean): **Algorithm** (with `Initialize` / `OnEndOfDay`), **Engine** (backtest/live), **Data** (CSV or Alpaca), and a **CLI** (`lean backtest`, `lean live`).

### Source of truth (docs vs config)

**Committed `config/default.yaml` and `src/*.py` are authoritative.** This README is written to match them; if you fork and change YAML, update the README (or treat any mismatch as a documentation bug). When reading logs, infer behavior from **`TrendFollowingStrategy`** (`src/strategy.py`) and **`run_alpaca_loop.py`**, not from generic “200-day trend” language unless your config actually uses those periods.

## Priority order (implementation order)

1. **Open-order lock** — Before placing a new order for a symbol, skip if that symbol already has an open (pending) order. *(run_alpaca_loop + broker.get_open_orders)*
2. **Long-only enforcement** — Reject short signals; map long → buy for execution. No sell-short. *(trading_engine)*
3. **10-minute entry loop** — Exits every 5 min; new entry checks every 10 min. *(config: entry_check_interval_minutes)*
4. **Trailing exit for winners** — After partial take-profit, trail the remainder by configurable % (e.g. 3%). *(strategy: use_trailing_stop, trailing_stop_pct)*
5. **Turtle hybrid logic** — Add only after 1–4 are solid and stable.

## Rules Implemented

### 1) Universe & Data
- **Traded set** = `universe.symbols` \ `universe.paused_symbols` in **`config/default.yaml`**. As committed, **`universe.symbols`** is: **SPY, QQQ, IWM, XLF, XLK, XLE, GLD, AAPL, MSFT, NVDA, AMZN, META, GOOGL, AMD, TSLA, BABA, JD, PDD** (19 tickers). **`universe.paused_symbols`** is `[]` unless you add exclusions.
- **Regime filter** (`universe.regime_min_pct_above_50d_ma`, default `0.30`): On each entry pass, the loop counts how many of those symbols have **close &gt; 50-day MA** on daily bars. If the fraction is **below** the threshold, the market is treated as **bearish** → **no** normal long entries; the bear-ETF path may still run.
- **Inverse / bear ETFs** (`universe.bear_etfs`): Separate from `universe.symbols`. As committed: **`symbols: [SQQQ, SPXS]`** (no TZA), **`max_positions: 1`**, **`max_exposure_pct_equity: 20`**, **`breakdown.reference_symbol: QQQ`** with **`ma_period: 50`**. In a bearish regime, if QQQ is below that MA, the loop may open **at most one** inverse ETF long, with total inverse notional capped at **20% of equity** (see `run_alpaca_loop.py`).
- **Market sessions**, **market quality** (spread, volume/ATR, volatility spike), and **trade filters** (macro blackout, earnings blackout, vol DNT, high-vol sizing): see `default.yaml` sections `market_sessions`, `market_quality`, `trade_filters`.

### Live vs paper (Alpaca loop + news)
- **`broker.paper`** in YAML defaults to **`true`**. CLI **`--live`** sets paper to **`false`** and **`--paper`** forces paper.
- **`scripts/run_alpaca_loop.py`**: With **`--live`**, **`news_sentiment.enabled` is forced to `false`** in memory (NewsAPI/FinBERT off for real-money runs). On **paper**, news follows YAML (`news_sentiment.enabled`, default **`false`**).
- **`lean live`** delegates to `run_alpaca_loop.py` with **`--paper`** (default) or **`--live`**.

### News + FinBERT (optional)
- **Off by default** (`news_sentiment.enabled: false`). When enabled **and not** using `--live`, the loop can use NewsAPI + FinBERT (see `src/news_sentiment/`, `requirements-news.txt`).

### 2) Strategy & exits (mechanical — matches `strategy` in `default.yaml`)

Implemented by **`TrendFollowingStrategy`** in **`src/strategy.py`**.

**Entry (as committed)**  
- **`strategy.type`**: `trend_following`  
- **`strategy.player_focus`**: **`retail`** → moving averages are taken from **`strategy.retail`**: **`ma_fast: 10`**, **`ma_slow: 50`** (these override the neutral defaults in code; they also match **`strategy.trend_following.ma_fast` / `ma_slow`** in the file).  
- **`strategy.trend_following.entry_mode`**: **`momentum`** → long only if **close &gt; slow MA** and **close &gt; fast MA**, plus **ATR% ≤ `max_atr_pct_for_entry`** (4.0 in default yaml), spread/ATR kill-switch at entry, and optional **candlestick** / **institutional volume** filters if enabled.  
- If **`entry_mode`** were **`pullback`**, the logic would require price **near** the fast MA within **`pullback_tolerance_pct`** instead of strictly above both.

**Exits (as committed, `strategy.exits`)**  
- **Stop-loss** `stop_loss_pct` (1.5%).  
- **Partial take-profit** at **`partial_take_profit_pct`** (3.0%) for **`partial_exit_ratio`** of the position (0.5).  
- **Trailing stop** on the remainder **`trailing_stop_pct`** (3.0%) when **`use_trailing_stop`** is true.  
- **Time exit**: `strategy.exits.time_bars_exit` is **25** in YAML, but with **`player_focus: retail`** the code uses **`strategy.retail.time_bars_exit` (10)** for the strategy object.  
- **Alpaca live loop caveat**: `run_alpaca_loop` passes **`bars_held`** from **`position_tracker.bars_held`**, which is **calendar days since entry**, not “number of daily bars”. Treat **`time_bars_exit`** as **days held** in live trading unless you change the tracker.  
- **Kill-switch** on wide spread or high **ATR%**; **cooldowns** and optional **re-entry rules** after stop or profit (see YAML `cooldown_*`, `require_*`).

### 3) Live loop (`scripts/run_alpaca_loop.py` + `broker` section)
- **`exit_check_interval_minutes`**: 5 — sleep between iterations; each pass refreshes account/positions, runs **exit** logic on tracked positions (quotes, daily bars for ATR, optional news-based exit if enabled).
- **`entry_check_interval_minutes`**: 10 — when elapsed, runs **regime** (% of universe above 50D MA), optional **market_regime** scorer (size multiplier), **bear-ETF** breakdown path, then **long entries** per symbol with a **cheap prefilter** (already in position / open order / tracked, then **close &gt; fast &amp; slow MA**, spread, cash) before **`run_entry_gates`**. Optional **news-driven** entry uses **`entry_override`** only when news is enabled and not `--live`.

### 4) Position sizing (`position_sizing` in `default.yaml`)
- As committed: **`risk_per_trade_pct` 0.5**, **`max_open_risk_pct` 3.0**, **`max_exposure_per_symbol_pct` / `max_exposure_per_sector_pct` 40**, optional **high-vol reduction** (e.g. half size when ATR% &gt; threshold).

### 5) Portfolio & drawdown (`portfolio_risk`)
- As committed: **daily loss limit** −2%, **max drawdown** −10% with **safe mode**, **max_trades_per_day** 15, **max_trades_per_symbol_per_day** 3 (tune in YAML).

### 6) Execution (`execution` + Alpaca)
- **Limit vs market**, spread gate, slippage tracking / strategy block — see `default.yaml` **`execution`**.

### 7) Compliance
- **PDT**: Pattern Day Trader rules — $25,000 minimum equity and day-trade limit when below (current framework; may change per FINRA).
- **Best execution**: Note in config; app enforces limits only; broker retains best execution duty.

## Default: nothing runs automatically

The app **does not run or schedule by default**. No loop runs at market open unless you start it yourself or set up a schedule (see `SCHEDULE.md`). Run scripts only when you want to trade.

## Project Layout (Lean-style)

```
algo/
├── lean                    # CLI: lean backtest | lean live (QuantConnect Lean–inspired)
├── config/
│   └── default.yaml       # All parameters (universe, strategy, risk, execution, broker, compliance)
├── src/
│   ├── algorithm/         # Algorithm layer (QCAlgorithm, Context, Slice)
│   │   ├── base.py        # QCAlgorithm: Initialize(), OnEndOfDay()
│   │   ├── context.py     # AlgorithmContext, Portfolio, Slice, Bar
│   │   └── trend_following.py  # Default TrendFollowingAlgorithm
│   ├── engine/            # Engine: runs algorithm over data
│   │   └── backtest_engine.py  # EngineBacktest (algorithm-driven backtest)
│   ├── backtest/          # Data loaders + metrics (CSV, Alpaca)
│   │   ├── data.py        # load_csv_data(), load_alpaca_data()
│   │   ├── engine.py      # BacktestEngine (strategy-driven, optional)
│   │   └── metrics.py    # compute_metrics()
│   ├── config_loader.py
│   ├── universe.py
│   ├── strategy.py        # Trend-following entry/exit logic
│   ├── position_sizing.py
│   ├── portfolio_risk.py
│   ├── execution.py
│   ├── compliance.py
│   ├── trading_engine.py  # Full gates for live trading
│   ├── news_sentiment/    # NewsAPI + FinBERT + rule helpers
│   └── brokers/
│       └── alpaca_client.py
├── scripts/
│   ├── run_backtest.py    # Backtest (CSV/Alpaca) — or use: lean backtest
│   ├── download_backtest_data.py
│   ├── run_alpaca_loop.py # Live loop — or use: lean live
│   ├── run_alpaca.py
│   └── ...
├── requirements.txt
└── README.md
```

## Setup

```bash
cd algo
python -m venv .venv
source .venv/bin/activate   # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Broker: Alpaca

The app is configured for **Alpaca** as the broker. In `config/default.yaml`:

- `broker.firm: alpaca`
- `broker.paper: true` — paper trading (default). Set to `false` for **live** (real money).
- Override without editing config: **CLI** `--live` or `--paper` (e.g. `python scripts/run_alpaca.py --live`), or **env** `APCA_PAPER=false` / `ALPACA_LIVE=true` for live.

Set environment variables (never commit keys):

- **Paper:** `APCA_API_KEY_ID`, `APCA_API_SECRET_KEY` (from Alpaca dashboard → Paper Trading → API Keys)
- **Live:** `ALPACA_LIVE_API_KEY_ID`, `ALPACA_LIVE_API_SECRET_KEY` (from Alpaca dashboard → Live → API Keys). Alpaca uses **separate** key pairs for paper vs live; using paper keys with `--live` causes 401 Unauthorized.

Paper base URL is used automatically when `paper: true`.

### Lean CLI (backtest & live)

From the project root:

```bash
# Backtest (CSV or Alpaca)
python lean backtest --data-dir data/backtest --start 2023-01-01 --end 2024-12-31
python lean backtest --alpaca --start 2023-01-01 --end 2024-12-31

# Live / paper loop
python lean live              # paper (default)
python lean live --live       # live account
python lean live -v           # verbose
```

Or use the scripts directly:

```bash
python scripts/run_alpaca.py
```

This uses your Alpaca account (paper or live) for equity and positions, fetches daily bars and latest quote for the first universe symbol, runs all entry gates, and submits the order to Alpaca if allowed.

**Nothing trading on live?** Run the loop with **`--verbose`** to see why each symbol is skipped:  
`python scripts/run_alpaca_loop.py --live --verbose`  
Common reasons: market closed (only 9:30–16:00 ET); "no entry signal" (trend strategy + gates); or spread / risk / PDT. **News sentiment is disabled automatically on `--live`.** One-shot: `python scripts/run_alpaca.py --live`.

## Run Example (no broker)

```bash
python scripts/run_example.py
```

This runs the full entry gate sequence for a sample symbol (SPY) with synthetic OHLCV and prints whether the trade is allowed and the order/sizing details.

## Configuration

Edit `config/default.yaml` to:

- Set **`universe.symbols`**, **`paused_symbols`**, **`bear_etfs`**, and liquidity filters — then **update this README** if you change the default set materially.
- Adjust **market_sessions**, **market_quality**, **`strategy`** (including **`player_focus`**, **`entry_mode`**, MA periods, exits), **`position_sizing`**, **`portfolio_risk`**, **`execution`**, **`compliance`**.
- Set **position_sizing** (risk per trade, max open risk, symbol/sector caps).
- Set **portfolio_risk** (daily loss limit, max drawdown, safe mode, trade frequency).
- Set **execution** (limit vs market, spread gate, slippage limits).
- Set **compliance** (PDT minimum equity, margin account flag).

## Algorithm API (Lean-style)

Subclass `QCAlgorithm` and override `Initialize(context)` and `OnEndOfDay(context, slice)`:

```python
from src.algorithm import QCAlgorithm, AlgorithmContext, Slice

class MyAlgorithm(QCAlgorithm):
    def initialize(self, context: AlgorithmContext) -> None:
        pass  # Universe from config

    def on_end_of_day(self, context: AlgorithmContext, slice: Slice) -> None:
        for symbol in slice.symbols():
            bar = slice.get(symbol)
            # ... use bar.open, bar.high, bar.low, bar.close
            # context.market_order(symbol, quantity)
```

Run with the engine: `EngineBacktest(config).run(MyAlgorithm(config), data, ...)`. The default `TrendFollowingAlgorithm` wraps the trend-following strategy and position sizer.

## Extending the App

- **Strategies**: Add `MeanReversionStrategy` or `BreakoutStrategy` in `strategy.py` and select via `strategy.type` in config.
- **Data**: Replace sample data with your data provider (e.g. Alpaca, Polygon) and feed OHLCV + spread/volume/ATR into `TradingEngine.run_entry_gates`.
- **Broker**: Use `order_request` from `TradeDecision` to send limit/market orders via your broker API; record fills in `ExecutionManager.record_fill` for slippage and strategy blocking.

## Disclaimer

This app is for educational and research use. Trading involves risk. PDT and other rules may change. Always ensure compliance with your broker and applicable regulations.
