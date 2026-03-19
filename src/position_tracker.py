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
    }
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
