"""Rules engine CRUD endpoints."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator

from src.api.deps import CurrentUser, DbSession
from src.api.broker_utils import get_live_broker
from src.db.models import UserRole
from src.db.repos import rule_repo

_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/users/{user_id}/rules", tags=["rules"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _check_access(current_user, user_id: str) -> None:
    if current_user.role != UserRole.admin and current_user.id != user_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

VALID_RULE_TYPES = {"entry", "exit"}

VALID_INDICATORS = {
    "price", "sma", "ema", "rsi", "atr", "atr_pct", "vwap",
    "volume", "volume_avg_ratio", "gap_pct", "daily_change_pct",
}

VALID_COMPARATORS = {
    "crosses_above", "crosses_below",
    "is_above", "is_below",
    "between",
}

VALID_ACTIONS = {
    "enter_long", "enter_short", "exit_position",
    "set_stop_loss", "set_take_profit", "set_trailing_stop",
    "limit_entry_at", "exit_at_price",
}


class RuleCondition(BaseModel):
    indicator: str
    params: dict[str, Any] = {}
    comparator: str
    value: Any  # number, indicator ref, or [low, high] for between

    @field_validator("indicator")
    @classmethod
    def validate_indicator(cls, v: str) -> str:
        if v not in VALID_INDICATORS:
            raise ValueError(f"Unknown indicator: {v}. Valid: {sorted(VALID_INDICATORS)}")
        return v

    @field_validator("comparator")
    @classmethod
    def validate_comparator(cls, v: str) -> str:
        if v not in VALID_COMPARATORS:
            raise ValueError(f"Unknown comparator: {v}. Valid: {sorted(VALID_COMPARATORS)}")
        return v


class RuleAction(BaseModel):
    action: str
    params: dict[str, Any] = {}

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in VALID_ACTIONS:
            raise ValueError(f"Unknown action: {v}. Valid: {sorted(VALID_ACTIONS)}")
        return v


class RuleGroup(BaseModel):
    logic: str = "AND"  # "AND" or "OR"
    conditions: list[RuleCondition]


class RuleTree(BaseModel):
    groups: list[RuleGroup]
    actions: list[RuleAction]


class RuleCreateRequest(BaseModel):
    symbol: str = Field(..., description="Ticker symbol this rule applies to — validated against Alpaca")
    name: str
    description: str | None = None
    rule_type: str
    rule_tree: RuleTree
    is_active: bool = True
    expires_at: str | None = Field(None, description="ISO 8601 expiration date. Rule auto-deactivates after this time. Null = never expires.")
    priority: int = 0

    @field_validator("rule_type")
    @classmethod
    def validate_rule_type(cls, v: str) -> str:
        if v not in VALID_RULE_TYPES:
            raise ValueError(f"rule_type must be one of {sorted(VALID_RULE_TYPES)}")
        return v


_UNSET = object()

class RuleUpdateRequest(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    symbol: str | None = None
    name: str | None = None
    description: str | None = Field(default=None)
    rule_type: str | None = None
    rule_tree: RuleTree | None = None
    is_active: bool | None = None
    expires_at: str | None = Field(default=None, description="ISO 8601 expiration. Null = never expires. Set to '' to clear expiration.")
    priority: int | None = None

    @field_validator("rule_type")
    @classmethod
    def validate_rule_type(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_RULE_TYPES:
            raise ValueError(f"rule_type must be one of {sorted(VALID_RULE_TYPES)}")
        return v


class RuleOut(BaseModel):
    id: int
    symbol: str
    name: str
    description: str | None
    rule_type: str
    rule_tree: dict[str, Any]
    is_active: bool
    expires_at: str | None
    priority: int
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


def _rule_to_out(rule) -> RuleOut:
    return RuleOut(
        id=rule.id,
        symbol=rule.symbol,
        name=rule.name,
        description=rule.description,
        rule_type=rule.rule_type,
        rule_tree=json.loads(rule.rule_tree),
        is_active=rule.is_active,
        expires_at=rule.expires_at.isoformat() if rule.expires_at else None,
        priority=rule.priority,
        created_at=rule.created_at.isoformat() if rule.created_at else "",
        updated_at=rule.updated_at.isoformat() if rule.updated_at else "",
    )


# ---------------------------------------------------------------------------
# Indicator / action metadata for the frontend toolbox
# ---------------------------------------------------------------------------

class IndicatorMeta(BaseModel):
    id: str
    label: str
    description: str
    params: list[dict[str, Any]]
    category: str


class ActionMeta(BaseModel):
    id: str
    label: str
    description: str
    params: list[dict[str, Any]]
    for_rule_type: str  # "entry", "exit", or "both"


INDICATOR_CATALOG: list[dict[str, Any]] = [
    {
        "id": "price",
        "label": "Price",
        "description": "Current stock price (close, open, high, or low)",
        "params": [{"name": "field", "type": "select", "options": ["close", "open", "high", "low"], "default": "close"}],
        "category": "price",
    },
    {
        "id": "sma",
        "label": "SMA",
        "description": "Simple Moving Average — average price over N periods",
        "params": [{"name": "period", "type": "number", "default": 20, "min": 2, "max": 500}],
        "category": "trend",
    },
    {
        "id": "ema",
        "label": "EMA",
        "description": "Exponential Moving Average — recent prices weighted more heavily",
        "params": [{"name": "period", "type": "number", "default": 20, "min": 2, "max": 500}],
        "category": "trend",
    },
    {
        "id": "rsi",
        "label": "RSI",
        "description": "Relative Strength Index — momentum oscillator (0-100). Below 30 = oversold, above 70 = overbought",
        "params": [{"name": "period", "type": "number", "default": 14, "min": 2, "max": 100}],
        "category": "momentum",
    },
    {
        "id": "atr",
        "label": "ATR",
        "description": "Average True Range — measures price volatility in dollars",
        "params": [{"name": "period", "type": "number", "default": 14, "min": 2, "max": 100}],
        "category": "volatility",
    },
    {
        "id": "atr_pct",
        "label": "ATR %",
        "description": "ATR as a percentage of price — normalized volatility measure",
        "params": [{"name": "period", "type": "number", "default": 14, "min": 2, "max": 100}],
        "category": "volatility",
    },
    {
        "id": "vwap",
        "label": "VWAP",
        "description": "Volume-Weighted Average Price — the fair price based on volume",
        "params": [],
        "category": "price",
    },
    {
        "id": "volume",
        "label": "Volume",
        "description": "Current trading volume",
        "params": [],
        "category": "volume",
    },
    {
        "id": "volume_avg_ratio",
        "label": "Volume vs Avg",
        "description": "Current volume divided by N-period average volume. 1.5 means 50% above average",
        "params": [{"name": "period", "type": "number", "default": 20, "min": 2, "max": 100}],
        "category": "volume",
    },
    {
        "id": "gap_pct",
        "label": "Gap %",
        "description": "How much the stock gapped at open vs yesterday's close. -5 means it opened 5% lower. Use this to avoid buying into a crash.",
        "params": [],
        "category": "price",
    },
    {
        "id": "daily_change_pct",
        "label": "Daily Change %",
        "description": "Today's price change from yesterday's close as a percentage",
        "params": [],
        "category": "price",
    },
]

COMPARATOR_CATALOG: list[dict[str, Any]] = [
    {"id": "crosses_above", "label": "crosses above", "description": "Triggers when value crosses from below to above the target"},
    {"id": "crosses_below", "label": "crosses below", "description": "Triggers when value crosses from above to below the target"},
    {"id": "is_above", "label": "is above", "description": "True when value is currently above the target"},
    {"id": "is_below", "label": "is below", "description": "True when value is currently below the target"},
    {"id": "between", "label": "is between", "description": "True when value is between two bounds"},
]

ACTION_CATALOG: list[dict[str, Any]] = [
    {
        "id": "enter_long",
        "label": "Buy",
        "description": "Buy shares when your conditions are met",
        "params": [{"name": "qty", "type": "number", "default": 1, "min": 1, "max": 100000, "label": "Shares"}],
        "for_rule_type": "entry",
    },
    {
        "id": "enter_short",
        "label": "Short Sell",
        "description": "Bet against the stock — profit when the price goes down",
        "params": [{"name": "qty", "type": "number", "default": 1, "min": 1, "max": 100000, "label": "Shares"}],
        "for_rule_type": "entry",
    },
    {
        "id": "exit_position",
        "label": "Sell / Close",
        "description": "Close your position and take whatever profit or loss you have",
        "params": [],
        "for_rule_type": "exit",
    },
    {
        "id": "set_stop_loss",
        "label": "Stop Loss %",
        "description": "Auto-sell if the price drops by this much — protects you from big losses",
        "params": [{"name": "pct", "type": "number", "default": 2.0, "min": 0.1, "max": 50}],
        "for_rule_type": "both",
    },
    {
        "id": "set_take_profit",
        "label": "Take Profit %",
        "description": "Auto-sell when your profit hits this percentage — locks in your gains",
        "params": [{"name": "pct", "type": "number", "default": 5.0, "min": 0.1, "max": 100}],
        "for_rule_type": "both",
    },
    {
        "id": "set_trailing_stop",
        "label": "Trailing Stop %",
        "description": "A smart stop loss that follows the price up — if the stock rises to $110 then drops 2%, it sells at $107.80",
        "params": [{"name": "pct", "type": "number", "default": 1.5, "min": 0.1, "max": 50}],
        "for_rule_type": "both",
    },
    {
        "id": "limit_entry_at",
        "label": "Buy at Price",
        "description": "Set your entry price — the order waits until the stock reaches this price (e.g. a support level you spotted on the chart)",
        "params": [
            {"name": "price", "type": "number", "default": 0, "min": 0.01, "max": 999999},
            {"name": "qty", "type": "number", "default": 1, "min": 1, "max": 100000, "label": "Shares"},
        ],
        "for_rule_type": "entry",
    },
    {
        "id": "exit_at_price",
        "label": "Sell at Price",
        "description": "Set your exit price — automatically sells when the stock reaches this target (e.g. a resistance level or your profit target)",
        "params": [{"name": "price", "type": "number", "default": 0, "min": 0.01, "max": 999999}],
        "for_rule_type": "exit",
    },
]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get(
    "/strategy-templates",
    summary="List pre-built strategy templates",
    description="""Returns all available strategy templates that users can apply to any ticker with one click.

Each template includes:
- Entry and exit rule trees (ready to save)
- Plain-English explanation of what the strategy does and why
- Recommended stop-loss, take-profit, and other risk parameters

Templates available:
- **Core Trend Following** — the original AlgoSphere strategy (SMA 200/20 + ATR filter)
- **Buy the Dip** — pullback entries on trending stocks (RSI + SMA 50)
- **Momentum Breakout** — EMA crossover with volume confirmation""",
)
def get_strategy_templates_endpoint(user_id: str, current_user: CurrentUser) -> list[dict[str, Any]]:
    _check_access(current_user, user_id)
    from src.rules_engine.strategy_templates import get_strategy_templates
    return get_strategy_templates()


class ApplyTemplateRequest(BaseModel):
    template_id: str = Field(..., description="Strategy template ID (e.g. 'core_trend_following')")
    symbol: str = Field(..., description="Ticker symbol to apply this strategy to")
    qty: int = Field(1, description="Number of shares per trade", ge=1)


@router.post(
    "/apply-template",
    response_model=dict[str, Any],
    summary="Apply a strategy template to a ticker",
    description="""Creates both entry and exit rules for a ticker based on a pre-built strategy template.

This is the fastest way to start trading a ticker — pick a strategy, pick a ticker, and go.
The template creates two rules (entry + exit) with the strategy's conditions and actions pre-filled.
You can always edit the rules afterwards to customize them.""",
    responses={404: {"description": "Template not found"}, 422: {"description": "Invalid ticker"}},
)
def apply_strategy_template(
    user_id: str,
    body: ApplyTemplateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> dict[str, Any]:
    _check_access(current_user, user_id)
    from src.rules_engine.strategy_templates import get_template_by_id

    template = get_template_by_id(body.template_id)
    if template is None:
        raise HTTPException(status_code=404, detail=f"Strategy template '{body.template_id}' not found")

    # Validate symbol
    broker = get_live_broker(db, user_id)
    if broker is not None:
        asset = broker.validate_symbol(body.symbol)
        if asset is None:
            raise HTTPException(status_code=422, detail=f"'{body.symbol.upper()}' is not a recognized ticker")
        if not asset.get("tradable", False):
            raise HTTPException(status_code=422, detail=f"'{body.symbol.upper()}' is not currently tradable")

    sym = body.symbol.upper()

    # Patch qty into entry actions
    entry_tree = template["entry_rule"]
    for action in entry_tree.get("actions", []):
        if action["action"] in ("enter_long", "enter_short", "limit_entry_at"):
            action["params"]["qty"] = body.qty

    # Create entry rule
    entry_rule = rule_repo.create_rule(
        db,
        user_id=user_id,
        symbol=sym,
        name=f"{sym} — {template['name']} (Entry)",
        description=template["description"],
        rule_type="entry",
        rule_tree=entry_tree,
        is_active=True,
    )

    # Create exit rule
    exit_rule = rule_repo.create_rule(
        db,
        user_id=user_id,
        symbol=sym,
        name=f"{sym} — {template['name']} (Exit)",
        description=template["description"],
        rule_type="exit",
        rule_tree=template["exit_rule"],
        is_active=True,
    )

    db.commit()
    db.refresh(entry_rule)
    db.refresh(exit_rule)

    _logger.info("[%s] Applied template '%s' to %s (entry=%d, exit=%d)",
                 user_id, body.template_id, sym, entry_rule.id, exit_rule.id)

    return {
        "template": template["name"],
        "symbol": sym,
        "entry_rule": _rule_to_out(entry_rule).model_dump(),
        "exit_rule": _rule_to_out(exit_rule).model_dump(),
        "explanation": template.get("explanation", {}),
    }


@router.get(
    "/catalog",
    summary="Get rule builder catalog",
    description="""Returns all available building blocks for the visual rule builder:

- **indicators**: EMA, SMA, RSI, ATR, ATR%, VWAP, Price, Volume, Volume vs Avg — each with parameter definitions (period, field, etc.)
- **comparators**: crosses_above, crosses_below, is_above, is_below, between
- **actions**: enter_long, enter_short, exit_position, set_stop_loss, set_take_profit, set_trailing_stop

The frontend uses this catalog to populate the toolbox and validate user input.""",
)
def get_catalog(user_id: str, current_user: CurrentUser) -> dict[str, Any]:
    """Return available indicators, comparators, and actions for the rule builder UI."""
    _check_access(current_user, user_id)
    return {
        "indicators": INDICATOR_CATALOG,
        "comparators": COMPARATOR_CATALOG,
        "actions": ACTION_CATALOG,
    }


class SymbolValidation(BaseModel):
    valid: bool = Field(..., description="Whether the symbol exists and is tradable")
    symbol: str | None = Field(None, description="Normalized symbol (uppercase)")
    name: str | None = Field(None, description="Company/asset name")
    exchange: str | None = Field(None, description="Exchange the asset trades on")
    message: str = Field(..., description="Human-readable result")


@router.get(
    "/validate-symbol/{symbol}",
    response_model=SymbolValidation,
    summary="Validate a ticker symbol",
    description="Checks if a ticker symbol exists and is tradable on Alpaca. Use this before creating a rule to ensure the ticker is valid.",
)
def validate_symbol(
    user_id: str,
    symbol: str,
    db: DbSession,
    current_user: CurrentUser,
) -> SymbolValidation:
    _check_access(current_user, user_id)
    broker = get_live_broker(db, user_id)
    if broker is None:
        return SymbolValidation(valid=False, symbol=symbol.upper(), message="Connect your broker in Settings to validate symbols")
    asset = broker.validate_symbol(symbol)
    if asset is None:
        return SymbolValidation(valid=False, symbol=symbol.upper(), message=f"'{symbol.upper()}' is not a recognized symbol")
    if not asset.get("tradable", False):
        return SymbolValidation(valid=False, symbol=asset["symbol"], name=asset.get("name"), exchange=asset.get("exchange"),
                                message=f"{asset['symbol']} ({asset.get('name', '')}) is not currently tradable")
    return SymbolValidation(valid=True, symbol=asset["symbol"], name=asset.get("name"), exchange=asset.get("exchange"),
                            message=f"{asset['symbol']} — {asset.get('name', '')}")


class OrderPreflightRequest(BaseModel):
    symbol: str = Field(..., description="Ticker symbol")
    qty: int = Field(..., description="Number of shares", ge=1)
    price: float = Field(..., description="Expected entry price", gt=0)


class OrderPreflightResponse(BaseModel):
    affordable: bool = Field(..., description="Whether the user can afford this order")
    order_cost: float = Field(..., description="Total cost of the order (qty x price)")
    buying_power: float | None = Field(None, description="User's current buying power")
    equity: float | None = Field(None, description="User's account equity")
    pct_of_equity: float | None = Field(None, description="Order cost as % of total equity")
    message: str = Field(..., description="Human-readable result")


@router.post(
    "/preflight-order",
    response_model=OrderPreflightResponse,
    summary="Check if an order is affordable",
    description="""Checks whether the user has enough buying power for a proposed order.
Flags if the order exceeds buying power or is a large % of equity.

Use this before placing an order to prevent rejection by the broker.""",
)
def preflight_order(
    user_id: str,
    body: OrderPreflightRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> OrderPreflightResponse:
    _check_access(current_user, user_id)
    broker = get_live_broker(db, user_id)
    if broker is None:
        return OrderPreflightResponse(
            affordable=False, order_cost=body.qty * body.price,
            message="Connect your broker in Settings to check buying power",
        )

    try:
        snapshot = broker.get_account_snapshot()
        buying_power = float(snapshot.get("buying_power", 0))
        equity = float(snapshot.get("equity", 0))
    except Exception:
        return OrderPreflightResponse(
            affordable=False, order_cost=body.qty * body.price,
            message="Could not fetch account data from broker",
        )

    order_cost = body.qty * body.price
    pct_of_equity = (order_cost / equity * 100) if equity > 0 else 0

    if order_cost > buying_power:
        return OrderPreflightResponse(
            affordable=False, order_cost=order_cost,
            buying_power=buying_power, equity=equity, pct_of_equity=pct_of_equity,
            message=f"Not enough buying power. Order costs ${order_cost:,.2f} but you only have ${buying_power:,.2f} available.",
        )

    warnings = []
    if pct_of_equity > 50:
        warnings.append(f"This order is {pct_of_equity:.0f}% of your total equity — very concentrated")
    elif pct_of_equity > 25:
        warnings.append(f"This order is {pct_of_equity:.0f}% of your equity — consider sizing down")

    msg = f"Order OK — ${order_cost:,.2f} ({pct_of_equity:.1f}% of equity)"
    if warnings:
        msg += ". " + ". ".join(warnings)

    return OrderPreflightResponse(
        affordable=True, order_cost=order_cost,
        buying_power=buying_power, equity=equity, pct_of_equity=pct_of_equity,
        message=msg,
    )


@router.get(
    "",
    response_model=list[RuleOut],
    summary="List all trading rules",
    description="Returns all trading rules for a user, ordered by priority. Filter by rule_type (entry/exit) or active_only.",
)
def list_rules(
    user_id: str,
    db: DbSession,
    current_user: CurrentUser,
    symbol: str | None = Query(None),
    rule_type: str | None = Query(None),
    active_only: bool = Query(False),
) -> list[RuleOut]:
    """List all trading rules for a user."""
    _check_access(current_user, user_id)
    rules = rule_repo.get_rules(db, user_id, symbol=symbol, rule_type=rule_type, active_only=active_only)
    return [_rule_to_out(r) for r in rules]


@router.post(
    "",
    response_model=RuleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a trading rule",
    description="""Create a new entry or exit trading rule. The rule tree defines conditions (indicator + comparator + value) grouped with AND/OR logic, plus actions to execute when conditions are met.

**Tip**: Use the `/validate` endpoint first to check your rule for errors before saving.""",
)
def create_rule(
    user_id: str,
    body: RuleCreateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> RuleOut:
    """Create a new trading rule."""
    _check_access(current_user, user_id)
    # Validate symbol via broker if credentials are available
    broker = get_live_broker(db, user_id)
    if broker is not None:
        asset = broker.validate_symbol(body.symbol)
        if asset is None:
            raise HTTPException(status_code=422, detail=f"'{body.symbol.upper()}' is not a recognized ticker symbol")
        if not asset.get("tradable", False):
            raise HTTPException(status_code=422, detail=f"'{body.symbol.upper()}' is not currently tradable")
    rule = rule_repo.create_rule(
        db,
        user_id=user_id,
        symbol=body.symbol,
        name=body.name,
        description=body.description,
        rule_type=body.rule_type,
        rule_tree=body.rule_tree.model_dump(),
        is_active=body.is_active,
        expires_at=body.expires_at,
        priority=body.priority,
    )
    db.commit()
    db.refresh(rule)
    _logger.info("[%s] Created %s rule: %s (id=%d)", user_id, body.rule_type, body.name, rule.id)
    return _rule_to_out(rule)


@router.get(
    "/{rule_id}",
    response_model=RuleOut,
    summary="Get a trading rule",
    description="Returns a single trading rule by ID, including the full rule tree JSON.",
)
def get_rule(
    user_id: str,
    rule_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> RuleOut:
    """Get a single trading rule by ID."""
    _check_access(current_user, user_id)
    rule = rule_repo.get_rule_by_id(db, rule_id)
    if rule is None or rule.user_id != user_id:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _rule_to_out(rule)


@router.put(
    "/{rule_id}",
    response_model=RuleOut,
    summary="Update a trading rule",
    description="Update any fields of an existing rule: name, description, rule_type, rule_tree, is_active, or priority. Only provided fields are changed.",
)
def update_rule(
    user_id: str,
    rule_id: int,
    body: RuleUpdateRequest,
    db: DbSession,
    current_user: CurrentUser,
) -> RuleOut:
    """Update an existing trading rule."""
    _check_access(current_user, user_id)
    rule = rule_repo.get_rule_by_id(db, rule_id)
    if rule is None or rule.user_id != user_id:
        raise HTTPException(status_code=404, detail="Rule not found")

    update_kwargs: dict[str, Any] = {}
    if body.symbol is not None:
        update_kwargs["symbol"] = body.symbol
    if body.name is not None:
        update_kwargs["name"] = body.name
    if body.description is not None:
        update_kwargs["description"] = body.description
    if body.rule_type is not None:
        update_kwargs["rule_type"] = body.rule_type
    if body.rule_tree is not None:
        update_kwargs["rule_tree"] = body.rule_tree.model_dump()
    if body.is_active is not None:
        update_kwargs["is_active"] = body.is_active
    if body.expires_at is not None:
        update_kwargs["expires_at"] = body.expires_at if body.expires_at != '' else None
    if body.priority is not None:
        update_kwargs["priority"] = body.priority

    rule_repo.update_rule(db, rule, **update_kwargs)
    db.commit()
    db.refresh(rule)
    _logger.info("[%s] Updated rule id=%d", user_id, rule.id)
    return _rule_to_out(rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a trading rule",
    description="Permanently deletes a trading rule. This action cannot be undone.",
)
def delete_rule(
    user_id: str,
    rule_id: int,
    db: DbSession,
    current_user: CurrentUser,
) -> None:
    """Delete a trading rule."""
    _check_access(current_user, user_id)
    rule = rule_repo.get_rule_by_id(db, rule_id)
    if rule is None or rule.user_id != user_id:
        raise HTTPException(status_code=404, detail="Rule not found")
    rule_repo.delete_rule(db, rule)
    db.commit()
    _logger.info("[%s] Deleted rule id=%d", user_id, rule_id)
