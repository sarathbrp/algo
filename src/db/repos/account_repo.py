"""Repositories for broker accounts and per-user/account settings."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import AccountSettings, BrokerAccount, UserSettings


def create_broker_account(
    session: Session,
    *,
    user_id: str,
    provider: str = "alpaca",
    provider_account_id: str | None = None,
    paper: bool = True,
    credentials_ref: str | None = None,
    is_active: bool = True,
) -> BrokerAccount:
    account = BrokerAccount(
        user_id=user_id,
        provider=provider,
        provider_account_id=provider_account_id,
        paper=paper,
        credentials_ref=credentials_ref,
        is_active=is_active,
    )
    session.add(account)
    return account


def get_broker_account_for_user(session: Session, user_id: str) -> BrokerAccount | None:
    return session.scalar(
        select(BrokerAccount).where(BrokerAccount.user_id == user_id)
    )


def list_active_broker_accounts(
    session: Session,
    *,
    provider: str | None = None,
) -> list[BrokerAccount]:
    stmt = select(BrokerAccount).where(BrokerAccount.is_active.is_(True))
    if provider is not None:
        stmt = stmt.where(BrokerAccount.provider == provider)
    return list(session.scalars(stmt.order_by(BrokerAccount.id.asc())))


def upsert_user_settings(
    session: Session,
    *,
    user_id: str,
    theme: str = "system",
    dashboard_layout: str | None = None,
    timezone: str = "America/New_York",
    notifications_enabled: bool = True,
) -> UserSettings:
    settings = session.scalar(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    if settings is None:
        settings = UserSettings(
            user_id=user_id,
            theme=theme,
            dashboard_layout=dashboard_layout,
            timezone=timezone,
            notifications_enabled=notifications_enabled,
        )
        session.add(settings)
        return settings

    settings.theme = theme
    settings.dashboard_layout = dashboard_layout
    settings.timezone = timezone
    settings.notifications_enabled = notifications_enabled
    return settings


def get_user_settings(session: Session, user_id: str) -> UserSettings | None:
    return session.scalar(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )


def upsert_account_settings(
    session: Session,
    *,
    broker_account_id: int,
    trading_enabled: bool = True,
    bot_state: str = "running",
    strategy_slug: str = "trend_following",
    risk_profile: str = "balanced",
    max_positions: int | None = None,
) -> AccountSettings:
    settings = session.scalar(
        select(AccountSettings).where(AccountSettings.broker_account_id == broker_account_id)
    )
    if settings is None:
        settings = AccountSettings(
            broker_account_id=broker_account_id,
            trading_enabled=trading_enabled,
            bot_state=bot_state,
            strategy_slug=strategy_slug,
            risk_profile=risk_profile,
            max_positions=max_positions,
        )
        session.add(settings)
        return settings

    settings.trading_enabled = trading_enabled
    settings.bot_state = bot_state
    settings.strategy_slug = strategy_slug
    settings.risk_profile = risk_profile
    settings.max_positions = max_positions
    return settings


def get_account_settings(session: Session, broker_account_id: int) -> AccountSettings | None:
    return session.scalar(
        select(AccountSettings).where(AccountSettings.broker_account_id == broker_account_id)
    )
