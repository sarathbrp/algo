"""Track open positions (entry price, time) for exit logic. Persisted to JSON."""
import json
from pathlib import Path
from datetime import datetime, timezone
from typing import Any


def _default_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "positions_tracked.json"


def load(base_path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = base_path or _default_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def save(data: dict[str, dict[str, Any]], base_path: Path | None = None) -> None:
    path = base_path or _default_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def add(
    base_path: Path | None,
    symbol: str,
    qty: int,
    entry_price: float,
    stop_pct: float,
    partial_taken: bool = False,
    trail_high: float | None = None,
    side: str = "long",
) -> None:
    data = load(base_path)
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
    save(data, base_path)


def merge_add_shares(
    base_path: Path | None,
    symbol: str,
    add_qty: int,
    fill_price: float,
    stop_pct: float | None = None,
    *,
    extras: dict[str, Any] | None = None,
) -> None:
    """Increase qty for an open position; weight-average entry_price. If not tracked, same as add()."""
    if add_qty <= 0:
        return
    data = load(base_path)
    key = symbol.upper()
    if key not in data:
        add(base_path, symbol, add_qty, fill_price, stop_pct if stop_pct is not None else 2.0)
        if extras:
            data = load(base_path)
            if key in data:
                for ek, ev in extras.items():
                    data[key][ek] = ev
                save(data, base_path)
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
    save(data, base_path)


def update(
    base_path: Path | None,
    symbol: str,
    qty: int | None = None,
    partial_taken: bool | None = None,
    trail_high: float | None = None,
) -> None:
    """Update one or more fields for an existing position."""
    data = load(base_path)
    key = symbol.upper()
    if key not in data:
        return
    if qty is not None:
        data[key]["qty"] = qty
    if partial_taken is not None:
        data[key]["partial_taken"] = partial_taken
    if trail_high is not None:
        data[key]["trail_high"] = trail_high
    save(data, base_path)


def remove(base_path: Path | None, symbol: str) -> None:
    data = load(base_path)
    data.pop(symbol.upper(), None)
    save(data, base_path)


def clear_all(base_path: Path | None = None) -> None:
    """Clear all tracked positions (e.g. after resetting paper account)."""
    save({}, base_path)


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
    path = Path(tracker_path) if tracker_path is not None else _default_path()
    data = load(path)
    key = str(symbol).upper()
    row = data.get(key) or data.get(symbol) or {}
    return row if isinstance(row, dict) else {}
