"""
Compliance: PDT rules, account constraints, best execution note.

- Pattern Day Trader (PDT): margin account day-trading may require $25,000 min equity.
- FINRA has discussed modernizing/eliminating the $25k PDT; treat as current but potentially changing.
- Broker best execution duty (enforced via app limits only).

Multi-user support: ``MultiUserComplianceManager`` holds per-user
``PDTState`` and per-user ``ComplianceManager`` (each user may have
different config overrides).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PDTState:
    equity: float
    day_trades_count_rolling: int
    day_trade_dates: list[date]  # last 5 business days of day-trades


class ComplianceManager:
    """
    Enforces PDT: if margin account and day-trading, require min equity and limit day-trades
    when below threshold.
    """

    def __init__(self, config: dict[str, Any]):
        comp = config.get("compliance", {})
        self.pdt_min_equity = float(comp.get("pdt_min_equity", 25_000))
        self.pdt_enabled = bool(comp.get("pdt_enabled", True))
        self.margin_account = bool(comp.get("margin_account", True))
        self.best_execution_note = str(comp.get("best_execution_note", ""))

    def can_day_trade(self, state: PDTState, trade_date: date) -> tuple[bool, str]:
        """
        PDT: In a margin account, if you make 4+ day trades in 5 business days and
        equity < $25k, you get flagged. Many brokers block further day trades.
        So: if equity < 25k, allow at most 3 day trades in rolling 5 business days.
        """
        if not self.pdt_enabled or not self.margin_account:
            return True, "PDT not applicable"

        if state.equity >= self.pdt_min_equity:
            return True, "equity above PDT threshold"

        # Below $25k: restrict to 3 day trades in rolling 5 business days
        max_day_trades_below_threshold = 3
        # Prune to last 5 business days
        cutoff = trade_date - timedelta(days=7)  # safe window to include 5 biz days
        recent = [d for d in state.day_trade_dates if d >= cutoff]
        if len(recent) >= max_day_trades_below_threshold:
            return False, (
                f"PDT: equity ${state.equity:,.0f} < ${self.pdt_min_equity:,.0f}; "
                f"day trade limit ({max_day_trades_below_threshold}) in rolling 5 business days reached"
            )
        return True, "ok"

    def record_day_trade(self, state: PDTState, trade_date: date) -> None:
        state.day_trade_dates.append(trade_date)
        # Keep only last 5 business days (simplified: keep last 10 dates then filter when checking)
        if len(state.day_trade_dates) > 20:
            state.day_trade_dates = state.day_trade_dates[-20:]

    def update_equity(self, state: PDTState, equity: float) -> None:
        state.equity = equity


# ---------------------------------------------------------------------------
# Multi-user wrapper
# ---------------------------------------------------------------------------

class MultiUserComplianceManager:
    """Per-user compliance (PDT) management.

    Each user gets their own ``PDTState`` and their own
    ``ComplianceManager`` (so per-user config overrides like different
    PDT thresholds or margin flags are respected).

    Parameters
    ----------
    user_configs : dict[str, dict]
        Mapping of ``user_id`` → fully-merged config dict.
    """

    def __init__(self, user_configs: dict[str, dict[str, Any]] | None = None) -> None:
        self._managers: dict[str, ComplianceManager] = {}
        self._states: dict[str, PDTState] = {}
        if user_configs:
            for uid, cfg in user_configs.items():
                self.register_user(uid, cfg)

    def register_user(self, user_id: str, config: dict[str, Any]) -> None:
        """Register a user with their config. Idempotent."""
        if user_id not in self._managers:
            self._managers[user_id] = ComplianceManager(config)
            self._states[user_id] = PDTState(equity=0.0, day_trades_count_rolling=0, day_trade_dates=[])
            logger.debug("[%s] Compliance state initialised", user_id)

    def _get(self, user_id: str) -> tuple[ComplianceManager, PDTState]:
        try:
            return self._managers[user_id], self._states[user_id]
        except KeyError:
            raise KeyError(
                f"User '{user_id}' not registered with MultiUserComplianceManager"
            ) from None

    def get_state(self, user_id: str) -> PDTState:
        """Return the raw state for *user_id* (read-only inspection)."""
        _, state = self._get(user_id)
        return state

    def can_day_trade(
        self,
        user_id: str,
        trade_date: date,
    ) -> tuple[bool, str]:
        mgr, state = self._get(user_id)
        return mgr.can_day_trade(state, trade_date)

    def record_day_trade(self, user_id: str, trade_date: date) -> None:
        mgr, state = self._get(user_id)
        mgr.record_day_trade(state, trade_date)

    def update_equity(self, user_id: str, equity: float) -> None:
        mgr, state = self._get(user_id)
        mgr.update_equity(state, equity)
