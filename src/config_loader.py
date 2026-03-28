"""Load and validate app configuration from YAML."""
import copy
from pathlib import Path
from typing import Any

import yaml


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).resolve().parent.parent / "config" / "default.yaml"
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}")
    with open(path) as f:
        return yaml.safe_load(f) or {}


def deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge *overrides* into a deep copy of *base*.

    - Dict values are merged recursively (nested keys update in place).
    - All other types (lists, scalars, None) in *overrides* replace the
      corresponding key in *base* outright.
    - Keys present in *overrides* but not in *base* are added.
    - The original *base* dict is never mutated.
    """
    result = copy.deepcopy(base)
    for key, override_val in overrides.items():
        base_val = result.get(key)
        if isinstance(base_val, dict) and isinstance(override_val, dict):
            result[key] = deep_merge(base_val, override_val)
        else:
            result[key] = copy.deepcopy(override_val)
    return result
