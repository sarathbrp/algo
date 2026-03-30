"""Track open positions (entry price, time) for exit logic.

The production path persists tracked positions in the database. JSON files
under ``data/`` remain as a compatibility backend for local development and
older tooling that passes an explicit file path.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _data_dir() -> Path:
    """Return the default ``data/`` directory (project root / data)."""
    return Path(__file__).resolve().parent.parent / "data"


def _use_db_backend(*, base_path: Path | None = None) -> bool:
    """Return True when tracked positions should be persisted in DB."""
    if base_path is not None:
        return False
    backend = os.environ.get("POSITION_TRACKER_BACKEND", "auto").strip().lower()
    if backend == "file":
        return False
    if backend == "db":
        return True
    return bool(os.environ.get("TIDB_DSN", "").strip())


def _user_path(user_id: str = "default", data_dir: Path | None = None) -> Path:
    """Return the JSON path for *user_id*: ``<data_dir>/positions_<user_id>.json``."""
    d = data_dir or _data_dir()
    return d / f"positions_{user_id}.json"


_LEGACY_FILENAME = "positions_tracked.json"


def _migrate_legacy(data_dir: Path | None = None) -> None:
    """If the legacy ``positions_tracked.json`` exists, copy it to
    ``positions_default.json`` and rename the old file to a ``.bak``
    so it is preserved but no longer used.
    """
    d = data_dir or _data_dir()
    legacy = d / _LEGACY_FILENAME
    target = _user_path("default", d)
    if legacy.exists() and not target.exists():
        shutil.copy2(legacy, target)
        backup = legacy.with_suffix(".json.bak")
        legacy.rename(backup)
        logger.info(
            "Migrated legacy %s → %s (backup at %s)",
            legacy.name, target.name, backup.name,
        )


def _parse_entry_time(value: Any) -> datetime | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _db_position_to_row(pos: Any) -> dict[str, Any]:
    side = getattr(pos.side, "value", pos.side)
    qty = float(pos.qty)
    if qty.is_integer():
        qty = int(qty)
    return {
        "qty": qty,
        "entry_price": float(pos.avg_entry_price) if pos.avg_entry_price is not None else 0.0,
        "entry_time": pos.entered_at.isoformat() if pos.entered_at else "",
        "stop_pct": float(pos.stop_pct) if pos.stop_pct is not None else None,
        "partial_taken": bool(pos.partial_taken),
        "trail_high": float(pos.trail_high) if pos.trail_high is not None else None,
        "side": str(side or "long"),
        "last_buy_price": (
            float(pos.last_buy_price)
            if getattr(pos, "last_buy_price", None) is not None
            else float(pos.avg_entry_price) if pos.avg_entry_price is not None else 0.0
        ),
    }


def _tracker_path_to_user_id(tracker_path: Path | str | None) -> str | None:
    if tracker_path is None:
        return "default"
    path = Path(tracker_path)
    if path.name == _LEGACY_FILENAME:
        return "default"
    stem = path.stem
    if stem.startswith("positions_"):
        return stem[len("positions_"):]
    return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def load(
    user_id: str = "default",
    *,
    data_dir: Path | None = None,
    base_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load tracked positions for *user_id*.

    ``base_path`` is accepted for backward compatibility but ``user_id``
    is the preferred interface.
    """
    if _use_db_backend(base_path=base_path):
        from src.db import get_session
        from src.db.repos import portfolio_repo

        with get_session() as session:
            positions = portfolio_repo.get_positions(session, user_id)
            return {pos.symbol.upper(): _db_position_to_row(pos) for pos in positions}

    if base_path is not None:
        path = base_path
    else:
        _migrate_legacy(data_dir)
        path = _user_path(user_id, data_dir)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save(
    data: dict[str, dict[str, Any]],
    user_id: str = "default",
    *,
    data_dir: Path | None = None,
    base_path: Path | None = None,
) -> None:
    """Persist *data* for *user_id*."""
    if _use_db_backend(base_path=base_path):
        from src.db import get_session
        from src.db.repos import portfolio_repo

        with get_session() as session:
            portfolio_repo.clear_positions(session, user_id)
            for symbol, row in data.items():
                if not isinstance(row, dict):
                    continue
                portfolio_repo.upsert_position(
                    session,
                    user_id=user_id,
                    symbol=str(symbol).upper(),
                    side=str(row.get("side") or "long"),
                    qty=float(row.get("qty") or 0),
                    avg_entry_price=float(row.get("entry_price") or 0),
                    last_buy_price=float(row.get("last_buy_price") or row.get("entry_price") or 0),
                    stop_pct=float(row["stop_pct"]) if row.get("stop_pct") is not None else None,
                    partial_taken=bool(row.get("partial_taken", False)),
                    trail_high=float(row["trail_high"]) if row.get("trail_high") is not None else None,
                    entered_at=_parse_entry_time(row.get("entry_time")),
                )
        return

    path = base_path if base_path is not None else _user_path(user_id, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def add(
    symbol: str,
    qty: int,
    entry_price: float,
    stop_pct: float,
    partial_taken: bool = False,
    trail_high: float | None = None,
    side: str = "long",
    user_id: str = "default",
    *,
    data_dir: Path | None = None,
    base_path: Path | None = None,
) -> None:
    """Add a new tracked position for *symbol*."""
    if _use_db_backend(base_path=base_path):
        from src.db import get_session
        from src.db.repos import portfolio_repo

        with get_session() as session:
            portfolio_repo.upsert_position(
                session,
                user_id=user_id,
                symbol=symbol.upper(),
                side=side,
                qty=float(qty),
                avg_entry_price=float(entry_price),
                last_buy_price=float(entry_price),
                stop_pct=float(stop_pct),
                partial_taken=partial_taken,
                trail_high=float(trail_high) if trail_high is not None else None,
                entered_at=datetime.now(timezone.utc),
            )
        return

    data = load(user_id, data_dir=data_dir, base_path=base_path)
    data[symbol.upper()] = {
        "qty": qty,
        "entry_price": entry_price,
        "entry_time": datetime.now(timezone.utc).isoformat(),
        "stop_pct": stop_pct,
        "partial_taken": partial_taken,
        "trail_high": float(trail_high) if trail_high is not None else None,
        "side": (side or "long").strip().lower(),
        "last_buy_price": float(entry_price),
    }
    save(data, user_id, data_dir=data_dir, base_path=base_path)


def merge_add_shares(
    symbol: str,
    add_qty: int,
    fill_price: float,
    stop_pct: float | None = None,
    user_id: str = "default",
    *,
    data_dir: Path | None = None,
    base_path: Path | None = None,
    extras: dict[str, Any] | None = None,
) -> None:
    """Increase qty for an open position; weight-average entry_price.

    If not tracked, behaves like :func:`add`.
    """
    if add_qty <= 0:
        return
    if _use_db_backend(base_path=base_path):
        from src.db import get_session
        from src.db.repos import portfolio_repo

        with get_session() as session:
            existing = portfolio_repo.get_positions(session, user_id)
            current = next((p for p in existing if p.symbol.upper() == symbol.upper()), None)
            if current is None:
                portfolio_repo.upsert_position(
                    session,
                    user_id=user_id,
                    symbol=symbol.upper(),
                    side="long",
                    qty=float(add_qty),
                    avg_entry_price=float(fill_price),
                    last_buy_price=float(fill_price),
                    stop_pct=float(stop_pct if stop_pct is not None else 2.0),
                    entered_at=datetime.now(timezone.utc),
                )
            else:
                oq = float(current.qty or 0)
                oe = float(current.avg_entry_price or 0)
                new_q = oq + add_qty
                new_e = ((oe * oq + fill_price * add_qty) / new_q) if oq > 0 and oe > 0 else fill_price
                portfolio_repo.upsert_position(
                    session,
                    user_id=user_id,
                    symbol=symbol.upper(),
                    side=getattr(current.side, "value", current.side),
                    qty=float(new_q),
                    avg_entry_price=float(new_e),
                    last_buy_price=float(fill_price),
                    stop_pct=float(stop_pct) if stop_pct is not None else (float(current.stop_pct) if current.stop_pct is not None else None),
                    partial_taken=bool(current.partial_taken),
                    trail_high=float(current.trail_high) if current.trail_high is not None else None,
                    entered_at=current.entered_at,
                )
                if extras:
                    # Extras remain unsupported in the DB backend until specific fields are modeled.
                    logger.debug("Ignoring unsupported tracker extras for DB backend: %s", sorted(extras))
        return

    data = load(user_id, data_dir=data_dir, base_path=base_path)
    key = symbol.upper()
    if key not in data:
        add(symbol, add_qty, fill_price, stop_pct if stop_pct is not None else 2.0,
            user_id=user_id, data_dir=data_dir, base_path=base_path)
        if extras:
            data = load(user_id, data_dir=data_dir, base_path=base_path)
            if key in data:
                for ek, ev in extras.items():
                    data[key][ek] = ev
                save(data, user_id, data_dir=data_dir, base_path=base_path)
        return
    old = data[key]
    oq = int(old.get("qty", 0))
    oe = float(old.get("entry_price", 0) or 0)
    new_q = oq + add_qty
    if new_q <= 0:
        return
    if oq > 0 and oe > 0:
        new_e = (oe * oq + fill_price * add_qty) / new_q
    else:
        new_e = fill_price
    data[key]["qty"] = new_q
    data[key]["entry_price"] = new_e
    data[key]["last_buy_price"] = float(fill_price)
    if stop_pct is not None:
        data[key]["stop_pct"] = stop_pct
    if extras:
        for ek, ev in extras.items():
            data[key][ek] = ev
    save(data, user_id, data_dir=data_dir, base_path=base_path)


def update(
    symbol: str,
    qty: int | None = None,
    partial_taken: bool | None = None,
    trail_high: float | None = None,
    user_id: str = "default",
    *,
    data_dir: Path | None = None,
    base_path: Path | None = None,
) -> None:
    """Update one or more fields for an existing position."""
    if _use_db_backend(base_path=base_path):
        from src.db import get_session
        from src.db.repos import portfolio_repo

        with get_session() as session:
            existing = next(
                (p for p in portfolio_repo.get_positions(session, user_id) if p.symbol.upper() == symbol.upper()),
                None,
            )
            if existing is None:
                return
            portfolio_repo.upsert_position(
                session,
                user_id=user_id,
                symbol=symbol.upper(),
                side=getattr(existing.side, "value", existing.side),
                qty=float(qty) if qty is not None else float(existing.qty),
                avg_entry_price=float(existing.avg_entry_price) if existing.avg_entry_price is not None else None,
                last_buy_price=float(existing.last_buy_price) if getattr(existing, "last_buy_price", None) is not None else None,
                stop_pct=float(existing.stop_pct) if existing.stop_pct is not None else None,
                partial_taken=partial_taken if partial_taken is not None else bool(existing.partial_taken),
                trail_high=float(trail_high) if trail_high is not None else (float(existing.trail_high) if existing.trail_high is not None else None),
                entered_at=existing.entered_at,
            )
        return

    data = load(user_id, data_dir=data_dir, base_path=base_path)
    key = symbol.upper()
    if key not in data:
        return
    if qty is not None:
        data[key]["qty"] = qty
    if partial_taken is not None:
        data[key]["partial_taken"] = partial_taken
    if trail_high is not None:
        data[key]["trail_high"] = trail_high
    save(data, user_id, data_dir=data_dir, base_path=base_path)


def remove(
    symbol: str,
    user_id: str = "default",
    *,
    data_dir: Path | None = None,
    base_path: Path | None = None,
) -> None:
    """Remove *symbol* from tracked positions."""
    if _use_db_backend(base_path=base_path):
        from src.db import get_session
        from src.db.repos import portfolio_repo

        with get_session() as session:
            portfolio_repo.remove_position(session, user_id, symbol)
        return

    data = load(user_id, data_dir=data_dir, base_path=base_path)
    data.pop(symbol.upper(), None)
    save(data, user_id, data_dir=data_dir, base_path=base_path)


def clear_all(
    user_id: str = "default",
    *,
    data_dir: Path | None = None,
    base_path: Path | None = None,
) -> None:
    """Clear all tracked positions (e.g. after resetting paper account)."""
    if _use_db_backend(base_path=base_path):
        from src.db import get_session
        from src.db.repos import portfolio_repo

        with get_session() as session:
            portfolio_repo.clear_positions(session, user_id)
        return

    save({}, user_id, data_dir=data_dir, base_path=base_path)


# ---------------------------------------------------------------------------
# Time helpers (stateless — no user_id needed)
# ---------------------------------------------------------------------------

def bars_held(entry_time_iso: str, now: datetime | None = None) -> int:
    """Days held (for daily bars)."""
    try:
        s = entry_time_iso.replace("Z", "+00:00")
        t = datetime.fromisoformat(s)
    except Exception:
        t = datetime.now(timezone.utc)
    now = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(0, (now - t).days)


def minutes_held(entry_time_iso: str, now: datetime | None = None) -> float:
    """Wall-clock minutes since entry (for strategy.exits.min_hold_minutes)."""
    if not entry_time_iso or not str(entry_time_iso).strip():
        return 0.0
    try:
        s = str(entry_time_iso).replace("Z", "+00:00")
        t = datetime.fromisoformat(s)
    except Exception:
        return 0.0
    now = now or datetime.now(timezone.utc)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return max(0.0, (now - t).total_seconds() / 60.0)


def minutes_since_iso(ts: str | None, now_dt: datetime) -> float | None:
    """
    Minutes from an ISO timestamp string to ``now_dt``.

    If ``ts`` has a timezone, it is converted to ``now_dt``'s zone (if any) before
    subtracting as naive datetimes, matching wall-clock comparison in the loop.
    Returns None if ``ts`` is missing or unparseable.
    """
    if not ts:
        return None
    try:
        t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if t.tzinfo is not None:
            if now_dt.tzinfo is not None:
                t = t.astimezone(now_dt.tzinfo).replace(tzinfo=None)
            else:
                t = t.replace(tzinfo=None)
            n = now_dt.replace(tzinfo=None)
        else:
            n = now_dt.replace(tzinfo=None)
        return (n - t).total_seconds() / 60.0
    except Exception:
        return None


def get_tracked_entry_info(tracker_path: Path | str | None, symbol: str) -> dict[str, Any]:
    """Return one symbol's row from the tracker file, or {} if missing / invalid."""
    if _use_db_backend() and tracker_path is not None:
        user_id = _tracker_path_to_user_id(tracker_path)
        if user_id:
            data = load(user_id=user_id)
            row = data.get(str(symbol).upper()) or data.get(symbol) or {}
            return row if isinstance(row, dict) else {}
    path = Path(tracker_path) if tracker_path is not None else _user_path("default")
    data = load(base_path=path)
    key = str(symbol).upper()
    row = data.get(key) or data.get(symbol) or {}
    return row if isinstance(row, dict) else {}
