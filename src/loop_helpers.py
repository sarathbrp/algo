"""Helpers for multi-user trading loop orchestration.

``UserLoopContext`` bundles all per-user runtime state (broker, engine,
risk managers, tracker path) so the main loop can iterate over users
with full isolation.

``init_user_contexts`` creates these contexts from a ``UserManager``,
and ``run_user_pass`` wraps per-user logic with error isolation.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.compliance import ComplianceManager, PDTState
from src.portfolio_risk import MultiUserPortfolioRiskManager, PortfolioRiskManager, PortfolioRiskState
from src.trading_engine import TradingEngine
from src.user_manager import UserContext, UserManager

logger = logging.getLogger(__name__)


@dataclass
class UserLoopContext:
    """All per-user state needed for one trading-loop iteration."""

    user_id: str
    user_ctx: UserContext
    broker: Any  # AlpacaBroker — typed as Any to avoid import
    engine: TradingEngine
    config: dict[str, Any]
    paper: bool
    data_dir: Path | None = None  # position tracker data dir (None → default)


def init_user_contexts(
    user_manager: UserManager,
    *,
    project_root: Path | None = None,
    user_filter: str | None = None,
) -> list[UserLoopContext]:
    """Create :class:`UserLoopContext` for every user in *user_manager*.

    Parameters
    ----------
    user_manager:
        Loaded ``UserManager`` instance.
    project_root:
        Project root for data dir resolution.  Falls back to
        ``Path(__file__).parent.parent``.
    user_filter:
        If provided, only create context for this ``user_id``.  Raises
        ``KeyError`` if the user is not registered.

    Returns
    -------
    list[UserLoopContext]
        One context per user, in registration order.
    """
    if project_root is None:
        project_root = Path(__file__).resolve().parent.parent

    users = user_manager.list_users()
    if user_filter:
        # Validate the filter early
        user_manager.get_user(user_filter)
        users = [u for u in users if u.user_id == user_filter]

    contexts: list[UserLoopContext] = []
    for uctx in users:
        uid = uctx.user_id
        try:
            broker = user_manager.get_broker(uid)
            engine = TradingEngine(config=uctx.config)
            ctx = UserLoopContext(
                user_id=uid,
                user_ctx=uctx,
                broker=broker,
                engine=engine,
                config=uctx.config,
                paper=uctx.paper,
                data_dir=project_root / "data",
            )
            contexts.append(ctx)
            logger.info(
                "[%s] Initialised loop context (paper=%s)", uid, uctx.paper
            )
        except ValueError as exc:
            logger.info("[%s] Skipping — %s", uid, exc)
        except Exception:
            logger.exception("[%s] Failed to initialise loop context — skipping user", uid)
    return contexts


def log_startup_summary(contexts: list[UserLoopContext]) -> None:
    """Print a human-readable startup summary of loaded users."""
    if not contexts:
        logger.info("No active broker accounts — worker will retry on next cycle.")
        return
    logger.info("Loaded %d user(s):", len(contexts))
    for ctx in contexts:
        mode = "PAPER" if ctx.paper else "LIVE"
        logger.info("  [%s] %s", ctx.user_id, mode)


def run_user_pass(
    ctx: UserLoopContext,
    callback: Any,
    **kwargs: Any,
) -> bool:
    """Execute *callback(ctx, **kwargs)* with error isolation.

    Returns ``True`` if the callback succeeded, ``False`` if an
    exception was caught (logged and swallowed so the next user
    can proceed).
    """
    try:
        callback(ctx, **kwargs)
        return True
    except Exception:
        logger.exception(
            "[%s] Error during trading pass — skipping to next user",
            ctx.user_id,
        )
        return False


def parse_cli_args(argv: list[str] | None = None) -> Any:
    """Parse CLI arguments for the multi-user trading loop.

    Returns the parsed ``argparse.Namespace``.
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Run trading loop until market close (multi-user)"
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Use live account (ignored in multi-user mode)",
    )
    parser.add_argument(
        "--paper", action="store_true",
        help="Use paper account (ignored in multi-user mode)",
    )
    parser.add_argument(
        "--user",
        type=str,
        default=None,
        help="Run only this user_id (useful for testing)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print skip reasons for all symbols",
    )
    return parser.parse_args(argv)
