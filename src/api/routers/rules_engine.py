"""Rules engine evaluation, backtest, and validation endpoints.

Provides APIs to test, validate, and simulate trading rules against
historical and live market data before activating them for real trading.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from src.api.deps import CurrentUser, DbSession
from src.api.broker_utils import get_live_broker
from src.db.models import UserRole
from src.db.repos import rule_repo
from src.rules_engine.evaluator import evaluate_rule, EvaluationResult
from src.rules_engine.validator import validate_rule, detect_conflicts, ValidationIssue
from src.rules_engine.backtest import run_backtest, BacktestConfig

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users/{user_id}/rules", tags=["rules-engine"])


def _check_access(current_user, user_id: str) -> None:
    if current_user.role != UserRole.admin and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


def _load_rule_tree(db, user_id: str, rule_id: int) -> dict:
    rule = rule_repo.get_rule_by_id(db, rule_id)
    if rule is None or rule.user_id != user_id:
        raise HTTPException(status_code=404, detail="Rule not found")
    return json.loads(rule.rule_tree)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class EvaluateRequest(BaseModel):
    """Request to evaluate a saved rule against live market data."""
    symbol: str = Field(..., description="Stock ticker symbol (e.g. AAPL, MSFT, SPY)", examples=["AAPL"])
    timeframe: str = Field("1Day", description="Bar timeframe: 1Min, 5Min, 15Min, 1Hour, 1Day, 1Week", examples=["1Day"])


class ConditionResultOut(BaseModel):
    """Result of evaluating a single condition within a rule."""
    indicator_name: str = Field(..., description="Indicator identifier with params, e.g. 'ema:[('period', 20)]'")
    indicator_value: float | None = Field(None, description="Computed indicator value at the evaluated bar (None if warmup)")
    comparator: str = Field(..., description="Comparison operator used: is_above, is_below, crosses_above, crosses_below, between")
    target_value: float | None = Field(None, description="The comparison target value (number or computed indicator)")
    passed: bool = Field(..., description="Whether this condition evaluated to True")
    detail: str = Field(..., description="Human-readable explanation of the evaluation result")


class GroupResultOut(BaseModel):
    """Result of evaluating a condition group (AND/OR logic)."""
    logic: str = Field(..., description="Logic operator: AND (all conditions must pass) or OR (any condition passes)")
    conditions: list[ConditionResultOut] = Field(..., description="Individual condition results within this group")
    passed: bool = Field(..., description="Whether the group as a whole evaluated to True")


class EvaluateResponse(BaseModel):
    """Full evaluation result — shows whether a rule would fire and why."""
    fired: bool = Field(..., description="Whether the rule would trigger (all groups passed)")
    groups: list[GroupResultOut] = Field(..., description="Per-group evaluation results with condition-level detail")
    actions: list[dict[str, Any]] = Field(..., description="Actions that would execute if fired (empty if not fired). E.g. enter_long, set_stop_loss")
    indicator_values: dict[str, float] = Field(..., description="Snapshot of all computed indicator values at the evaluated bar")
    symbol: str = Field(..., description="The symbol that was evaluated")
    bar_count: int = Field(..., description="Number of historical bars used for evaluation")
    evaluated_at: str = Field(..., description="ISO 8601 timestamp of when the evaluation was performed")


class EvaluateInlineRequest(BaseModel):
    """Request to evaluate an unsaved rule tree against live market data. Useful for testing rules before saving."""
    rule_tree: dict[str, Any] = Field(..., description="The rule tree JSON with 'groups' and 'actions'", examples=[{
        "groups": [{"logic": "AND", "conditions": [
            {"indicator": "ema", "params": {"period": 20}, "comparator": "crosses_above",
             "value": {"indicator": "ema", "params": {"period": 50}}}
        ]}],
        "actions": [{"action": "enter_long", "params": {}}]
    }])
    symbol: str = Field(..., description="Stock ticker symbol", examples=["AAPL"])
    timeframe: str = Field("1Day", description="Bar timeframe", examples=["1Day"])


class ValidateRequest(BaseModel):
    """Request to statically validate a rule tree for logical errors. No broker credentials needed."""
    rule_tree: dict[str, Any] = Field(..., description="The rule tree JSON to validate")
    rule_type: str = Field(..., description="Rule type: 'entry' or 'exit'", examples=["entry"])


class ValidationIssueOut(BaseModel):
    """A single validation issue found in a rule."""
    severity: str = Field(..., description="Issue severity: 'error' (rule is broken) or 'warning' (potential problem)")
    field: str = Field(..., description="JSONPath-like reference to the problematic field, e.g. 'groups[0].conditions[1]'")
    message: str = Field(..., description="Human-readable description of the issue")


class ValidateResponse(BaseModel):
    """Validation result — shows whether a rule is valid and lists any issues."""
    valid: bool = Field(..., description="True if no errors found (warnings are OK). False if any errors exist.")
    issues: list[ValidationIssueOut] = Field(..., description="List of validation issues (errors and warnings)")


class ConflictCheckRequest(BaseModel):
    """Request to check for conflicts between rules. Defaults to all active rules if rule_ids is omitted."""
    rule_ids: list[int] | None = Field(None, description="Specific rule IDs to check. If null, checks all active rules for this user.")


class BacktestRequest(BaseModel):
    """Request to run a historical backtest simulation. Provide either rule IDs (saved rules) or inline rule trees.

    The backtest fetches historical bars in a single API call, then walks bar-by-bar
    evaluating entry and exit rules to simulate trades.
    """
    entry_rule_id: int | None = Field(None, description="ID of a saved entry rule to use. Mutually exclusive with entry_rule_tree.")
    entry_rule_tree: dict[str, Any] | None = Field(None, description="Inline entry rule tree (use instead of entry_rule_id for unsaved rules)", examples=[{
        "groups": [{"logic": "AND", "conditions": [
            {"indicator": "price", "params": {"field": "close"}, "comparator": "is_above",
             "value": {"indicator": "sma", "params": {"period": 20}}}
        ]}],
        "actions": [{"action": "enter_long", "params": {}}, {"action": "set_stop_loss", "params": {"pct": 2.0}}]
    }])
    exit_rule_id: int | None = Field(None, description="ID of a saved exit rule to use. Mutually exclusive with exit_rule_tree.")
    exit_rule_tree: dict[str, Any] | None = Field(None, description="Inline exit rule tree (use instead of exit_rule_id for unsaved rules)", examples=[{
        "groups": [{"logic": "AND", "conditions": [
            {"indicator": "price", "params": {"field": "close"}, "comparator": "is_below",
             "value": {"indicator": "sma", "params": {"period": 20}}}
        ]}],
        "actions": [{"action": "exit_position", "params": {}}]
    }])
    symbol: str = Field(..., description="Stock ticker symbol to backtest", examples=["AAPL"])
    timeframe: str = Field("1Day", description="Bar timeframe: 1Min, 5Min, 15Min, 1Hour, 1Day", examples=["1Day"])
    start_date: str | None = Field(None, description="Backtest start date (ISO 8601). Defaults to 1 year ago.", examples=["2025-01-01"])
    end_date: str | None = Field(None, description="Backtest end date (ISO 8601). Defaults to today.", examples=["2026-01-01"])
    initial_capital: float = Field(100_000.0, description="Starting capital in USD", ge=100)
    position_size_pct: float = Field(10.0, description="Percentage of capital to allocate per trade", ge=0.1, le=100)


class SimulatedTradeOut(BaseModel):
    """A single simulated trade from the backtest."""
    entry_bar: int = Field(..., description="Bar index where the trade was entered")
    entry_price: float = Field(..., description="Entry price (close of entry bar)")
    entry_date: str = Field(..., description="Date/time of entry")
    exit_bar: int | None = Field(None, description="Bar index where the trade was exited")
    exit_price: float | None = Field(None, description="Exit price")
    exit_date: str | None = Field(None, description="Date/time of exit")
    side: str = Field(..., description="Trade direction: 'long' or 'short'")
    pnl: float | None = Field(None, description="Profit/loss in USD")
    pnl_pct: float | None = Field(None, description="Profit/loss as percentage of entry price")
    exit_reason: str | None = Field(None, description="Why the trade was closed: exit_rule, stop_loss, take_profit, trailing_stop, end_of_data")
    bars_held: int | None = Field(None, description="Number of bars the position was held")


class BacktestResponse(BaseModel):
    """Complete backtest simulation results with trades, metrics, and equity curve."""
    trades: list[SimulatedTradeOut] = Field(..., description="List of all simulated trades with entry/exit details and P&L")
    total_trades: int = Field(..., description="Total number of trades executed")
    winning_trades: int = Field(..., description="Number of trades with positive P&L")
    losing_trades: int = Field(..., description="Number of trades with negative P&L")
    win_rate: float | None = Field(None, description="Winning trades / total trades (null if no trades)", ge=0, le=1)
    total_pnl: float = Field(..., description="Sum of all trade P&L in USD")
    avg_pnl_per_trade: float | None = Field(None, description="Average P&L per trade in USD (null if no trades)")
    max_drawdown_pct: float = Field(..., description="Maximum peak-to-trough equity decline as percentage", ge=0)
    equity_curve: list[float] = Field(..., description="Equity value at each bar (for charting). Starts at initial_capital.")
    bars_evaluated: int = Field(..., description="Number of bars actually evaluated (after warmup period)")
    entry_signals: int = Field(..., description="Number of bars where the entry rule fired")
    exit_signals: int = Field(..., description="Number of bars where the exit rule fired")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _eval_to_response(result: EvaluationResult, symbol: str, bar_count: int) -> EvaluateResponse:
    groups = []
    for g in result.groups:
        conditions = [
            ConditionResultOut(
                indicator_name=c.indicator_name,
                indicator_value=c.indicator_value,
                comparator=c.comparator,
                target_value=c.target_value,
                passed=c.passed,
                detail=c.detail,
            )
            for c in g.conditions
        ]
        groups.append(GroupResultOut(logic=g.logic, conditions=conditions, passed=g.passed))

    return EvaluateResponse(
        fired=result.fired,
        groups=groups,
        actions=result.actions,
        indicator_values=result.indicator_values,
        symbol=symbol,
        bar_count=bar_count,
        evaluated_at=datetime.utcnow().isoformat() + "Z",
    )


def _get_bars(db, user_id: str, symbol: str, timeframe: str, limit: int = 300,
              start: datetime | None = None, end: datetime | None = None):
    broker = get_live_broker(db, user_id)
    if broker is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Broker credentials not configured. Please connect your broker in Settings.",
        )
    df = broker.get_bars(symbol, timeframe=timeframe, start=start, end=end, limit=limit)
    if df.empty:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No bar data returned for {symbol}. Check the symbol and date range.",
        )
    return df


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Pipeline summary for dashboard
# ---------------------------------------------------------------------------

class RuleSignalOut(BaseModel):
    rule_id: int = Field(..., description="Rule ID")
    rule_name: str = Field(..., description="Rule name")
    rule_type: str = Field(..., description="'entry' or 'exit'")
    fired: bool = Field(..., description="Whether this rule would fire right now")
    conditions_met: int = Field(..., description="Number of conditions that passed")
    conditions_total: int = Field(..., description="Total conditions in the rule")
    actions_summary: str = Field(..., description="Short summary of the actions")
    indicator_snapshot: dict[str, float] = Field(default_factory=dict, description="Key indicator values")


class SymbolPipelineOut(BaseModel):
    symbol: str = Field(..., description="Ticker symbol")
    entry_rules: list[RuleSignalOut] = Field(default_factory=list)
    exit_rules: list[RuleSignalOut] = Field(default_factory=list)
    status: str = Field(..., description="Overall status: 'ready_to_buy', 'watching', 'in_position', 'ready_to_sell', 'no_signal'")
    status_detail: str = Field(..., description="Human-readable status line")


class PipelineSummaryOut(BaseModel):
    total_active_rules: int = Field(..., description="Total active rules across all tickers")
    symbols_monitored: int = Field(..., description="Number of unique tickers with active rules")
    pipeline: list[SymbolPipelineOut] = Field(..., description="Per-symbol breakdown")
    updated_at: str = Field(..., description="Timestamp of this evaluation")


def _summarize_actions(actions: list[dict]) -> str:
    parts = []
    for a in actions:
        aid = a.get("action", "")
        params = a.get("params", {})
        if aid == "enter_long":
            qty = params.get("qty", "")
            parts.append(f"Buy{f' {qty} shares' if qty else ''}")
        elif aid == "enter_short":
            parts.append("Short sell")
        elif aid == "limit_entry_at":
            parts.append(f"Buy at ${params.get('price', '?')}")
        elif aid == "exit_position":
            parts.append("Sell / Close")
        elif aid == "exit_at_price":
            parts.append(f"Sell at ${params.get('price', '?')}")
        elif aid == "set_stop_loss":
            parts.append(f"SL {params.get('pct', '?')}%")
        elif aid == "set_take_profit":
            parts.append(f"TP {params.get('pct', '?')}%")
        elif aid == "set_trailing_stop":
            parts.append(f"Trail {params.get('pct', '?')}%")
    return ", ".join(parts) if parts else "—"


@router.get(
    "/pipeline",
    response_model=PipelineSummaryOut,
    summary="Get rules pipeline summary for dashboard",
    description="""Returns a real-time summary of all active rules grouped by ticker.

For each symbol shows:
- Which rules are active (entry and exit)
- Whether each rule would fire right now (based on latest market data)
- How many conditions are met vs total
- Key indicator values (price, SMA, RSI, etc.)
- Overall status: watching, ready to buy, in position, ready to sell

**Rate limit**: One Alpaca `get_bars` call per monitored symbol.
This endpoint is designed to be polled by the dashboard (every 15-30s).""",
)
def get_pipeline_summary(
    user_id: str,
    db: DbSession,
    current_user: CurrentUser,
) -> PipelineSummaryOut:
    _check_access(current_user, user_id)

    all_rules = rule_repo.get_rules(db, user_id, active_only=True)
    if not all_rules:
        return PipelineSummaryOut(
            total_active_rules=0, symbols_monitored=0, pipeline=[],
            updated_at=datetime.utcnow().isoformat() + "Z",
        )

    # Group rules by symbol
    by_symbol: dict[str, list] = {}
    for r in all_rules:
        by_symbol.setdefault(r.symbol, []).append(r)

    broker = get_live_broker(db, user_id)

    # Get user's current positions to determine status
    positions_set: set[str] = set()
    if broker:
        try:
            positions = broker.get_positions()
            positions_set = {p["symbol"].upper() for p in positions}
        except Exception:
            pass

    pipeline: list[SymbolPipelineOut] = []

    for symbol, rules in sorted(by_symbol.items()):
        entry_signals: list[RuleSignalOut] = []
        exit_signals: list[RuleSignalOut] = []

        # Try to fetch bars for evaluation (skip if no broker)
        df = None
        if broker:
            try:
                df = broker.get_bars(symbol, timeframe="1Day", limit=220)
                if df.empty:
                    df = None
            except Exception:
                df = None

        for r in rules:
            tree = json.loads(r.rule_tree)
            actions_summary = _summarize_actions(tree.get("actions", []))

            if df is not None and len(df) > 1:
                result = evaluate_rule(df, tree, bar_idx=-1)
                total_conds = sum(len(g.conditions) for g in result.groups)
                met_conds = sum(1 for g in result.groups for c in g.conditions if c.passed)
                signal = RuleSignalOut(
                    rule_id=r.id, rule_name=r.name, rule_type=r.rule_type,
                    fired=result.fired, conditions_met=met_conds, conditions_total=total_conds,
                    actions_summary=actions_summary, indicator_snapshot=result.indicator_values,
                )
            else:
                signal = RuleSignalOut(
                    rule_id=r.id, rule_name=r.name, rule_type=r.rule_type,
                    fired=False, conditions_met=0, conditions_total=0,
                    actions_summary=actions_summary, indicator_snapshot={},
                )

            if r.rule_type == "entry":
                entry_signals.append(signal)
            else:
                exit_signals.append(signal)

        # Determine overall status
        in_position = symbol.upper() in positions_set
        any_entry_fired = any(s.fired for s in entry_signals)
        any_exit_fired = any(s.fired for s in exit_signals)

        if in_position and any_exit_fired:
            status = "ready_to_sell"
            detail = f"Exit signal active — conditions met to close {symbol}"
        elif in_position:
            detail_parts = []
            for s in exit_signals:
                detail_parts.append(f"{s.conditions_met}/{s.conditions_total} exit conditions met")
            status = "in_position"
            detail = f"Holding {symbol} — " + (", ".join(detail_parts) if detail_parts else "monitoring for exit")
        elif any_entry_fired:
            status = "ready_to_buy"
            detail = f"Entry signal active — conditions met to buy {symbol}"
        elif entry_signals:
            detail_parts = []
            for s in entry_signals:
                detail_parts.append(f"{s.conditions_met}/{s.conditions_total} conditions met")
            status = "watching"
            detail = f"Monitoring {symbol} — " + ", ".join(detail_parts)
        else:
            status = "no_signal"
            detail = f"Exit rules only for {symbol}"

        pipeline.append(SymbolPipelineOut(
            symbol=symbol, entry_rules=entry_signals, exit_rules=exit_signals,
            status=status, status_detail=detail,
        ))

    return PipelineSummaryOut(
        total_active_rules=len(all_rules),
        symbols_monitored=len(by_symbol),
        pipeline=pipeline,
        updated_at=datetime.utcnow().isoformat() + "Z",
    )


@router.post(
    "/validate",
    response_model=ValidateResponse,
    summary="Validate a rule (static analysis)",
    description="""Performs static validation on a rule tree without needing broker credentials or market data.

Checks for:
- **Missing actions**: entry rules must have enter_long/enter_short, exit rules must have exit_position or stop/take-profit
- **Action/type mismatch**: entry rules with exit actions (or vice versa)
- **Empty conditions**: groups with no conditions
- **Period limits**: indicator periods > 500 are errors, > 200 are warnings
- **Contradictory conditions**: e.g. `is_above 70 AND is_below 30` in the same AND group
- **Invalid 'between' bounds**: reversed or non-array values

Returns `valid: true` if no errors found (warnings don't block validity).""",
)
def validate_endpoint(
    user_id: str,
    body: ValidateRequest,
    current_user: CurrentUser,
) -> ValidateResponse:
    _check_access(current_user, user_id)
    issues = validate_rule(body.rule_tree, body.rule_type)
    return ValidateResponse(
        valid=len([i for i in issues if i.severity == "error"]) == 0,
        issues=[ValidationIssueOut(severity=i.severity, field=i.field, message=i.message) for i in issues],
    )


@router.post(
    "/check-conflicts",
    response_model=ValidateResponse,
    summary="Detect conflicts between rules",
    description="""Checks for conflicts between multiple trading rules. By default checks all active rules for the user.

Detects:
- **Entry vs Exit overlap**: entry and exit rules on the same indicator with overlapping thresholds that could trigger simultaneously
- **Duplicate entry rules**: multiple entry rules checking the same indicator with the same comparator, risking duplicate positions

Only active rules are checked. Pass specific `rule_ids` to check a subset.""",
)
def check_conflicts_endpoint(
    user_id: str,
    body: ConflictCheckRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> ValidateResponse:
    _check_access(current_user, user_id)

    if body.rule_ids:
        rules_raw = []
        for rid in body.rule_ids:
            rule = rule_repo.get_rule_by_id(db, rid)
            if rule and rule.user_id == user_id:
                rules_raw.append(rule)
    else:
        rules_raw = rule_repo.get_rules(db, user_id, active_only=True)

    rules_dicts = [
        {
            "name": r.name,
            "rule_type": r.rule_type,
            "rule_tree": json.loads(r.rule_tree),
            "is_active": r.is_active,
        }
        for r in rules_raw
    ]

    issues = detect_conflicts(rules_dicts)
    return ValidateResponse(
        valid=len([i for i in issues if i.severity == "error"]) == 0,
        issues=[ValidationIssueOut(severity=i.severity, field=i.field, message=i.message) for i in issues],
    )


@router.post(
    "/{rule_id}/evaluate",
    response_model=EvaluateResponse,
    summary="Evaluate a saved rule against live data",
    description="""Tests whether a saved rule would fire RIGHT NOW for a given symbol.

Fetches the latest 300 bars from Alpaca via the user's broker credentials,
computes all indicators referenced in the rule, evaluates every condition,
and returns a detailed breakdown showing:
- Whether the rule fired (all conditions met)
- Per-condition pass/fail with computed indicator values
- The actions that would execute
- A snapshot of all indicator values at the current bar

**Requires**: broker credentials configured in Settings.
**Rate limit impact**: 1 Alpaca API call per request.""",
    responses={
        404: {"description": "Rule not found or does not belong to this user"},
        422: {"description": "Broker credentials not configured or no data returned for symbol"},
    },
)
def evaluate_saved_rule(
    user_id: str,
    rule_id: int,
    body: EvaluateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> EvaluateResponse:
    _check_access(current_user, user_id)
    rule_tree = _load_rule_tree(db, user_id, rule_id)
    df = _get_bars(db, user_id, body.symbol, body.timeframe)
    result = evaluate_rule(df, rule_tree, bar_idx=-1)
    return _eval_to_response(result, body.symbol, len(df))


@router.post(
    "/evaluate-inline",
    response_model=EvaluateResponse,
    summary="Evaluate an unsaved rule against live data",
    description="""Tests an unsaved rule tree against current market data — useful for previewing
rules in the builder before saving.

Same behavior as the saved-rule evaluate endpoint, but accepts a raw rule tree JSON
instead of a rule ID. Use this to power real-time feedback in the rule builder UI.

**Requires**: broker credentials configured in Settings.
**Rate limit impact**: 1 Alpaca API call per request.""",
    responses={
        422: {"description": "Broker credentials not configured or no data returned for symbol"},
    },
)
def evaluate_inline_rule(
    user_id: str,
    body: EvaluateInlineRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> EvaluateResponse:
    _check_access(current_user, user_id)
    df = _get_bars(db, user_id, body.symbol, body.timeframe)
    result = evaluate_rule(df, body.rule_tree, bar_idx=-1)
    return _eval_to_response(result, body.symbol, len(df))


@router.post(
    "/backtest",
    response_model=BacktestResponse,
    summary="Run a historical backtest simulation",
    description="""Simulates trading with entry and exit rules against historical bar data.

**How it works:**
1. Fetches historical bars from Alpaca (single API call, up to 10,000 bars)
2. Computes a warmup period from the maximum indicator period in both rules
3. Walks bar-by-bar from warmup to end:
   - If not in a position: evaluates entry rules. If fired, enters at the bar's close price.
   - If in a position: checks stop-loss/take-profit/trailing-stop from entry actions first,
     then evaluates exit rules. If any trigger, exits the trade.
4. Any position still open at the end of data is closed at the last bar's price.

**Supports:**
- Long and short positions (based on entry action: enter_long vs enter_short)
- Stop-loss, take-profit, and trailing stop (from entry rule actions)
- Configurable initial capital and position sizing

**Returns:**
- Full trade list with entry/exit prices, P&L, exit reasons
- Win rate, total P&L, average P&L per trade
- Maximum drawdown percentage
- Equity curve array (for charting)
- Entry/exit signal counts

**Rate limit impact**: 1 Alpaca API call per backtest.
**Performance**: ~250 bars/year for daily data. A 5-year backtest is ~1250 bars, evaluated in < 100ms.

Provide rules either by ID (saved rules) or as inline rule trees.""",
    responses={
        400: {"description": "Missing entry_rule_id/entry_rule_tree or exit_rule_id/exit_rule_tree"},
        404: {"description": "Referenced rule not found"},
        422: {"description": "Broker credentials not configured or no data returned"},
    },
)
def backtest_endpoint(
    user_id: str,
    body: BacktestRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> BacktestResponse:
    _check_access(current_user, user_id)

    # Resolve entry rule
    if body.entry_rule_tree:
        entry_tree = body.entry_rule_tree
    elif body.entry_rule_id:
        entry_tree = _load_rule_tree(db, user_id, body.entry_rule_id)
    else:
        raise HTTPException(status_code=400, detail="Provide entry_rule_id or entry_rule_tree")

    # Resolve exit rule
    if body.exit_rule_tree:
        exit_tree = body.exit_rule_tree
    elif body.exit_rule_id:
        exit_tree = _load_rule_tree(db, user_id, body.exit_rule_id)
    else:
        raise HTTPException(status_code=400, detail="Provide exit_rule_id or exit_rule_tree")

    # Parse dates
    end_dt = datetime.fromisoformat(body.end_date) if body.end_date else datetime.utcnow()
    start_dt = datetime.fromisoformat(body.start_date) if body.start_date else end_dt - timedelta(days=365)

    df = _get_bars(db, user_id, body.symbol, body.timeframe, limit=10000, start=start_dt, end=end_dt)

    config = BacktestConfig(
        initial_capital=body.initial_capital,
        position_size_pct=body.position_size_pct,
    )

    result = run_backtest(df, entry_tree, exit_tree, config)

    return BacktestResponse(
        trades=[SimulatedTradeOut(
            entry_bar=t.entry_bar, entry_price=t.entry_price, entry_date=t.entry_date,
            exit_bar=t.exit_bar, exit_price=t.exit_price, exit_date=t.exit_date,
            side=t.side, pnl=t.pnl, pnl_pct=t.pnl_pct,
            exit_reason=t.exit_reason, bars_held=t.bars_held,
        ) for t in result.trades],
        total_trades=result.total_trades,
        winning_trades=result.winning_trades,
        losing_trades=result.losing_trades,
        win_rate=result.win_rate,
        total_pnl=result.total_pnl,
        avg_pnl_per_trade=result.avg_pnl_per_trade,
        max_drawdown_pct=result.max_drawdown_pct,
        equity_curve=result.equity_curve,
        bars_evaluated=result.bars_evaluated,
        entry_signals=result.entry_signals,
        exit_signals=result.exit_signals,
    )


# ---------------------------------------------------------------------------
# Order placement
# ---------------------------------------------------------------------------

class PlaceOrderRequest(BaseModel):
    """Place a real order (bracket or simple) based on a rule's actions."""
    symbol: str = Field(..., description="Stock ticker symbol", examples=["AAPL"])
    qty: int = Field(..., description="Number of shares to trade", ge=1, examples=[10])
    side: str = Field("buy", description="Order side: 'buy' or 'sell'", examples=["buy"])
    time_in_force: str = Field("gtc", description="Time in force: 'day' (expires EOD) or 'gtc' (good til canceled)", examples=["gtc"])


class PlaceOrderInlineRequest(BaseModel):
    """Place a bracket order from inline parameters — no saved rule needed.

    Perfect for the use case: "I see support at $150, I want to buy there
    with a 2% stop loss and exit at $155 or +3%."
    """
    symbol: str = Field(..., description="Stock ticker symbol", examples=["AAPL"])
    qty: int = Field(..., description="Number of shares", ge=1, examples=[10])
    side: str = Field("buy", description="'buy' for long, 'sell' for short", examples=["buy"])
    entry_price: float = Field(..., description="Limit entry price (e.g. support level from chart)", examples=[150.00])
    take_profit_price: float | None = Field(None, description="Take profit at this exact price. Mutually exclusive with take_profit_pct.", examples=[155.00])
    take_profit_pct: float | None = Field(None, description="Take profit at this % gain from entry. Mutually exclusive with take_profit_price.", examples=[3.0])
    stop_loss_price: float | None = Field(None, description="Stop loss at this exact price. Mutually exclusive with stop_loss_pct.", examples=[147.00])
    stop_loss_pct: float | None = Field(None, description="Stop loss at this % loss from entry.", examples=[2.0])
    time_in_force: str = Field("gtc", description="'day' or 'gtc'", examples=["gtc"])


class OrderResultOut(BaseModel):
    """Result of placing an order via Alpaca."""
    success: bool = Field(..., description="Whether the order was accepted by the broker")
    order_id: str | None = Field(None, description="Alpaca order ID (for tracking)")
    order_type: str = Field(..., description="Order type placed: 'bracket', 'limit', or 'market'")
    symbol: str = Field(..., description="Symbol ordered")
    side: str = Field(..., description="Order side: buy/sell")
    qty: int = Field(..., description="Shares ordered")
    entry_price: float | None = Field(None, description="Limit entry price (if bracket/limit)")
    take_profit_price: float | None = Field(None, description="Take profit target price")
    stop_loss_price: float | None = Field(None, description="Stop loss trigger price")
    message: str = Field(..., description="Human-readable status message")


def _extract_order_params(actions: list[dict], entry_price: float | None, side: str) -> dict:
    """Extract order parameters from rule actions."""
    params: dict[str, Any] = {}

    for act in actions:
        action_id = act.get("action", "")
        act_params = act.get("params", {})

        if action_id == "limit_entry_at":
            params["entry_price"] = float(act_params.get("price", 0))
        elif action_id == "exit_at_price":
            params["take_profit_price"] = float(act_params.get("price", 0))
        elif action_id == "set_take_profit":
            pct = float(act_params.get("pct", 0))
            if entry_price and pct > 0:
                if side == "buy":
                    params["take_profit_price"] = round(entry_price * (1 + pct / 100), 2)
                else:
                    params["take_profit_price"] = round(entry_price * (1 - pct / 100), 2)
        elif action_id == "set_stop_loss":
            pct = float(act_params.get("pct", 0))
            if entry_price and pct > 0:
                if side == "buy":
                    params["stop_loss_price"] = round(entry_price * (1 - pct / 100), 2)
                else:
                    params["stop_loss_price"] = round(entry_price * (1 + pct / 100), 2)

    return params


@router.post(
    "/{rule_id}/place-order",
    response_model=OrderResultOut,
    summary="Place a live order from a saved rule",
    description="""Places a real order with your broker based on a saved rule's actions.

**Supports bracket orders**: If the rule has `limit_entry_at` + `set_stop_loss` + `set_take_profit`
(or `exit_at_price`), this places a single atomic bracket order with Alpaca — entry, stop-loss,
and take-profit all in one call.

**Example workflow**:
1. User sees support at $150 on TradingView
2. Creates a rule: limit entry at $150, stop loss 2%, take profit at $155
3. Calls this endpoint → Alpaca bracket order placed
4. If filled at $150: stop-loss at $147, take-profit at $155 — all managed by Alpaca

**Order types placed**:
- `bracket` — entry + TP + SL (when both exit params present)
- `limit` — limit entry only (when no exit params)

**Requires**: broker credentials configured in Settings.""",
    responses={
        404: {"description": "Rule not found"},
        422: {"description": "Broker not configured or invalid order parameters"},
    },
)
def place_order_from_rule(
    user_id: str,
    rule_id: int,
    body: PlaceOrderRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> OrderResultOut:
    _check_access(current_user, user_id)
    rule_tree = _load_rule_tree(db, user_id, rule_id)

    broker = get_live_broker(db, user_id)
    if broker is None:
        raise HTTPException(status_code=422, detail="Broker credentials not configured.")

    actions = rule_tree.get("actions", [])

    # Determine entry price from actions
    entry_price = None
    for act in actions:
        if act.get("action") == "limit_entry_at":
            entry_price = float(act.get("params", {}).get("price", 0))
            break

    if entry_price is None or entry_price <= 0:
        raise HTTPException(status_code=422, detail="Rule must have a 'Limit Entry at Price' action with a valid price.")

    order_params = _extract_order_params(actions, entry_price, body.side)
    tp = order_params.get("take_profit_price")
    sl = order_params.get("stop_loss_price")

    try:
        if tp or sl:
            result = broker.submit_bracket_order(
                symbol=body.symbol, side=body.side, qty=body.qty,
                limit_price=entry_price,
                take_profit_price=tp, stop_loss_price=sl,
                time_in_force=body.time_in_force,
            )
            order_type = "bracket"
        else:
            from src.execution import OrderRequest, OrderType
            order = OrderRequest(
                symbol=body.symbol, side=body.side, quantity=body.qty,
                order_type=OrderType.LIMIT, limit_price=entry_price,
            )
            result = broker.submit_order(order)
            order_type = "limit"

        order_id = str(getattr(result, "id", None) or "")
        _logger.info("[%s] Placed %s order for %s %s x%d @ $%.2f (TP=%s, SL=%s)",
                     user_id, order_type, body.side, body.symbol, body.qty, entry_price, tp, sl)

        return OrderResultOut(
            success=True, order_id=order_id, order_type=order_type,
            symbol=body.symbol, side=body.side, qty=body.qty,
            entry_price=entry_price, take_profit_price=tp, stop_loss_price=sl,
            message=f"Bracket order placed: {body.side} {body.qty} {body.symbol} @ ${entry_price:.2f}"
                    + (f", TP @ ${tp:.2f}" if tp else "")
                    + (f", SL @ ${sl:.2f}" if sl else ""),
        )
    except Exception as exc:
        _logger.error("[%s] Order placement failed: %s", user_id, exc)
        raise HTTPException(status_code=422, detail=f"Order failed: {exc}")


@router.post(
    "/place-order",
    response_model=OrderResultOut,
    summary="Place a bracket order directly (no saved rule)",
    description="""Place a bracket order with explicit parameters — no saved rule needed.

**Use case**: You see a support level at $150 on TradingView. You want to:
- Buy 10 shares at $150 (limit order)
- Take profit at $155 (or +3%)
- Stop loss at $147 (or -2%)

This places it as one atomic Alpaca bracket order.

You can mix percentage and absolute targets:
- `take_profit_pct: 3.0` → auto-calculates $154.50 from $150 entry
- `take_profit_price: 155.0` → uses the exact price
- Same for stop_loss_pct vs stop_loss_price

If both `_pct` and `_price` are given for the same leg, the explicit price wins.""",
    responses={
        422: {"description": "Broker not configured or invalid parameters"},
    },
)
def place_order_inline(
    user_id: str,
    body: PlaceOrderInlineRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> OrderResultOut:
    _check_access(current_user, user_id)

    broker = get_live_broker(db, user_id)
    if broker is None:
        raise HTTPException(status_code=422, detail="Broker credentials not configured.")

    entry_price = body.entry_price

    # Resolve take profit
    tp = body.take_profit_price
    if tp is None and body.take_profit_pct is not None:
        if body.side == "buy":
            tp = round(entry_price * (1 + body.take_profit_pct / 100), 2)
        else:
            tp = round(entry_price * (1 - body.take_profit_pct / 100), 2)

    # Resolve stop loss
    sl = body.stop_loss_price
    if sl is None and body.stop_loss_pct is not None:
        if body.side == "buy":
            sl = round(entry_price * (1 - body.stop_loss_pct / 100), 2)
        else:
            sl = round(entry_price * (1 + body.stop_loss_pct / 100), 2)

    try:
        if tp or sl:
            result = broker.submit_bracket_order(
                symbol=body.symbol, side=body.side, qty=body.qty,
                limit_price=entry_price,
                take_profit_price=tp, stop_loss_price=sl,
                time_in_force=body.time_in_force,
            )
            order_type = "bracket"
        else:
            from src.execution import OrderRequest, OrderType
            order = OrderRequest(
                symbol=body.symbol, side=body.side, quantity=body.qty,
                order_type=OrderType.LIMIT, limit_price=entry_price,
            )
            result = broker.submit_order(order)
            order_type = "limit"

        order_id = str(getattr(result, "id", None) or "")
        _logger.info("[%s] Placed inline %s order: %s %s x%d @ $%.2f (TP=%s, SL=%s)",
                     user_id, order_type, body.side, body.symbol, body.qty, entry_price, tp, sl)

        return OrderResultOut(
            success=True, order_id=order_id, order_type=order_type,
            symbol=body.symbol, side=body.side, qty=body.qty,
            entry_price=entry_price, take_profit_price=tp, stop_loss_price=sl,
            message=f"Order placed: {body.side} {body.qty} {body.symbol} @ ${entry_price:.2f}"
                    + (f", TP @ ${tp:.2f}" if tp else "")
                    + (f", SL @ ${sl:.2f}" if sl else ""),
        )
    except Exception as exc:
        _logger.error("[%s] Inline order failed: %s", user_id, exc)
        raise HTTPException(status_code=422, detail=f"Order failed: {exc}")
