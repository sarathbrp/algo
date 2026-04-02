"""Trading rules repository."""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import TradingRule


def create_rule(
    session: Session,
    *,
    user_id: str,
    symbol: str,
    name: str,
    rule_type: str,
    rule_tree: dict[str, Any],
    description: str | None = None,
    is_active: bool = True,
    expires_at: Any = None,
    priority: int = 0,
) -> TradingRule:
    """Create a new trading rule for a user."""
    from datetime import datetime
    exp = None
    if expires_at:
        exp = datetime.fromisoformat(str(expires_at)) if isinstance(expires_at, str) else expires_at
    rule = TradingRule(
        user_id=user_id,
        symbol=symbol.upper(),
        name=name,
        description=description,
        rule_type=rule_type,
        rule_tree=json.dumps(rule_tree),
        is_active=is_active,
        expires_at=exp,
        priority=priority,
    )
    session.add(rule)
    return rule


def get_rules(
    session: Session,
    user_id: str,
    *,
    symbol: str | None = None,
    rule_type: str | None = None,
    active_only: bool = False,
) -> list[TradingRule]:
    """Return trading rules for a user, ordered by priority."""
    q = select(TradingRule).where(TradingRule.user_id == user_id)
    if symbol is not None:
        q = q.where(TradingRule.symbol == symbol.upper())
    if rule_type is not None:
        q = q.where(TradingRule.rule_type == rule_type)
    if active_only:
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        q = q.where(TradingRule.is_active.is_(True))
        # Exclude expired rules (expires_at is set and in the past)
        q = q.where(
            (TradingRule.expires_at.is_(None)) | (TradingRule.expires_at > now)
        )
    return list(session.scalars(q.order_by(TradingRule.priority.asc(), TradingRule.id.asc())))


def get_rule_by_id(session: Session, rule_id: int) -> TradingRule | None:
    """Return a single rule by ID."""
    return session.get(TradingRule, rule_id)


def update_rule(
    session: Session,
    rule: TradingRule,
    *,
    symbol: str | None = None,
    name: str | None = None,
    description: str | None = None,
    rule_type: str | None = None,
    rule_tree: dict[str, Any] | None = None,
    is_active: bool | None = None,
    expires_at: Any = ...,
    priority: int | None = None,
) -> TradingRule:
    """Update fields on an existing rule."""
    if symbol is not None:
        rule.symbol = symbol.upper()
    if name is not None:
        rule.name = name
    if description is not None:
        rule.description = description
    if rule_type is not None:
        rule.rule_type = rule_type
    if rule_tree is not None:
        rule.rule_tree = json.dumps(rule_tree)
    if is_active is not None:
        rule.is_active = is_active
    if expires_at is not ...:
        from datetime import datetime
        if expires_at is None or expires_at == '':
            rule.expires_at = None
        elif isinstance(expires_at, str):
            rule.expires_at = datetime.fromisoformat(expires_at)
        else:
            rule.expires_at = expires_at
    if priority is not None:
        rule.priority = priority
    return rule


def get_active_symbols(session: Session, user_id: str) -> list[str]:
    """Return distinct symbols that have at least one active, non-expired rule for a user."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    q = (
        select(TradingRule.symbol)
        .where(
            TradingRule.user_id == user_id,
            TradingRule.is_active.is_(True),
            (TradingRule.expires_at.is_(None)) | (TradingRule.expires_at > now),
        )
        .distinct()
    )
    return list(session.scalars(q))


def delete_rule(session: Session, rule: TradingRule) -> None:
    """Delete a trading rule."""
    session.delete(rule)
