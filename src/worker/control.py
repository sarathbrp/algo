"""Account-level bot control helpers for the trading worker."""
from __future__ import annotations

BOT_STATE_RUNNING = "running"
BOT_STATE_PAUSED = "paused"
BOT_STATE_STOPPED = "stopped"
VALID_BOT_STATES = {
    BOT_STATE_RUNNING,
    BOT_STATE_PAUSED,
    BOT_STATE_STOPPED,
}


def normalize_bot_state(raw: str | None, *, trading_enabled: bool = True) -> str:
    value = (raw or "").strip().lower()
    if value in VALID_BOT_STATES:
        return value
    return BOT_STATE_RUNNING if trading_enabled else BOT_STATE_PAUSED


def can_open_new_trades(bot_state: str) -> bool:
    return normalize_bot_state(bot_state) == BOT_STATE_RUNNING


def can_manage_open_positions(bot_state: str) -> bool:
    return normalize_bot_state(bot_state) in {BOT_STATE_RUNNING, BOT_STATE_PAUSED}


def describe_bot_state(bot_state: str) -> str:
    state = normalize_bot_state(bot_state)
    if state == BOT_STATE_RUNNING:
        return "Bot can open new trades and manage open positions."
    if state == BOT_STATE_PAUSED:
        return "Bot syncs account data and manages open positions, but will not open new trades."
    return "Bot is observation-only. It syncs account data, but will not place automated orders."
