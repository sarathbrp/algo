"""Static rule validation — no market data needed.

Checks for logical errors, missing actions, contradictory conditions, and
conflicts between rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


ENTRY_ACTIONS = {"enter_long", "enter_short"}
EXIT_ACTIONS = {"exit_position"}
MAX_PERIOD = 500
WARN_PERIOD = 200


@dataclass
class ValidationIssue:
    severity: str  # "error" or "warning"
    field: str
    message: str


# ---------------------------------------------------------------------------
# Single-rule validation
# ---------------------------------------------------------------------------

def validate_rule(rule_tree: dict, rule_type: str) -> list[ValidationIssue]:
    """Run all validation checks on a rule tree. Returns empty list if valid."""
    issues: list[ValidationIssue] = []
    groups = rule_tree.get("groups", [])
    actions = rule_tree.get("actions", [])

    # Empty groups
    if not groups:
        issues.append(ValidationIssue("error", "groups", "Rule has no condition groups"))

    for gi, group in enumerate(groups):
        conditions = group.get("conditions", [])
        if not conditions:
            issues.append(ValidationIssue("error", f"groups[{gi}]", "Condition group is empty"))

        for ci, cond in enumerate(conditions):
            path = f"groups[{gi}].conditions[{ci}]"
            _validate_condition(cond, path, issues)

        # Check for contradictory conditions within an AND group
        if group.get("logic", "AND").upper() == "AND":
            _check_contradictions(conditions, gi, issues)

    # Action validation
    action_ids = {a.get("action") for a in actions}

    if rule_type == "entry":
        if not action_ids & ENTRY_ACTIONS:
            issues.append(ValidationIssue(
                "error", "actions",
                "Entry rule must have an entry action (Buy / Enter Long or Sell Short)",
            ))
        if action_ids & EXIT_ACTIONS:
            issues.append(ValidationIssue(
                "error", "actions",
                "Entry rule should not contain 'Exit Position' action",
            ))
    elif rule_type == "exit":
        has_exit = bool(action_ids & EXIT_ACTIONS)
        has_stop = bool(action_ids & {"set_stop_loss", "set_take_profit", "set_trailing_stop"})
        if not has_exit and not has_stop:
            issues.append(ValidationIssue(
                "error", "actions",
                "Exit rule must have an exit action or a stop/take-profit",
            ))
        if action_ids & ENTRY_ACTIONS:
            issues.append(ValidationIssue(
                "error", "actions",
                "Exit rule should not contain entry actions (Buy/Sell Short)",
            ))

    return issues


def _validate_condition(cond: dict, path: str, issues: list[ValidationIssue]) -> None:
    """Validate a single condition's params."""
    params = cond.get("params", {})
    period = params.get("period")
    if period is not None:
        if period > MAX_PERIOD:
            issues.append(ValidationIssue("error", path, f"Period {period} exceeds maximum ({MAX_PERIOD})"))
        elif period > WARN_PERIOD:
            issues.append(ValidationIssue("warning", path, f"Period {period} requires significant historical data"))

    # Check value field for indicator refs
    value = cond.get("value")
    if isinstance(value, dict) and "indicator" in value:
        vp = value.get("params", {}).get("period")
        if vp is not None and vp > MAX_PERIOD:
            issues.append(ValidationIssue("error", f"{path}.value", f"Value indicator period {vp} exceeds maximum"))

    # Between bounds
    if cond.get("comparator") == "between":
        if not isinstance(value, list) or len(value) != 2:
            issues.append(ValidationIssue("error", f"{path}.value", "'between' comparator requires [low, high] value"))
        elif value[0] > value[1]:
            issues.append(ValidationIssue("error", f"{path}.value", f"Lower bound ({value[0]}) is greater than upper bound ({value[1]})"))


def _check_contradictions(conditions: list[dict], group_idx: int, issues: list[ValidationIssue]) -> None:
    """Check for contradictory conditions within an AND group."""
    # Index conditions by (indicator, params_key)
    by_indicator: dict[str, list[tuple[int, dict]]] = {}
    for ci, cond in enumerate(conditions):
        key = f"{cond.get('indicator')}:{sorted(cond.get('params', {}).items())}"
        by_indicator.setdefault(key, []).append((ci, cond))

    for key, conds in by_indicator.items():
        if len(conds) < 2:
            continue
        for i in range(len(conds)):
            for j in range(i + 1, len(conds)):
                ci_a, a = conds[i]
                ci_b, b = conds[j]
                comp_a, comp_b = a.get("comparator"), b.get("comparator")
                val_a, val_b = a.get("value"), b.get("value")

                if not (isinstance(val_a, (int, float)) and isinstance(val_b, (int, float))):
                    continue

                # is_above X AND is_below Y where Y <= X → always false
                if comp_a == "is_above" and comp_b == "is_below" and val_b <= val_a:
                    issues.append(ValidationIssue(
                        "error",
                        f"groups[{group_idx}]",
                        f"Contradictory: condition {ci_a} requires above {val_a} but condition {ci_b} requires below {val_b}",
                    ))
                elif comp_a == "is_below" and comp_b == "is_above" and val_a <= val_b:
                    issues.append(ValidationIssue(
                        "error",
                        f"groups[{group_idx}]",
                        f"Contradictory: condition {ci_a} requires below {val_a} but condition {ci_b} requires above {val_b}",
                    ))


# ---------------------------------------------------------------------------
# Cross-rule conflict detection
# ---------------------------------------------------------------------------

def detect_conflicts(rules: list[dict]) -> list[ValidationIssue]:
    """Check for conflicts between multiple rules.

    Each item in rules should have keys: name, rule_type, rule_tree, is_active.
    Only active rules are checked.
    """
    issues: list[ValidationIssue] = []
    active = [r for r in rules if r.get("is_active", True)]
    entries = [r for r in active if r.get("rule_type") == "entry"]
    exits = [r for r in active if r.get("rule_type") == "exit"]

    # Entry vs Exit: same symbol + same indicator + overlapping thresholds
    for entry in entries:
        for exit_rule in exits:
            if entry.get("symbol") and exit_rule.get("symbol") and entry["symbol"] != exit_rule["symbol"]:
                continue  # different tickers can't conflict
            _check_entry_exit_conflict(entry, exit_rule, issues)

    # Duplicate entry rules: only flag if same symbol
    for i in range(len(entries)):
        for j in range(i + 1, len(entries)):
            if entries[i].get("symbol") and entries[j].get("symbol") and entries[i]["symbol"] != entries[j]["symbol"]:
                continue  # different tickers can't create duplicate positions
            _check_duplicate_entry(entries[i], entries[j], issues)

    return issues


def _iter_conditions(rule: dict):
    """Yield all conditions from a rule's tree."""
    for group in rule.get("rule_tree", {}).get("groups", []):
        yield from group.get("conditions", [])


def _check_entry_exit_conflict(entry: dict, exit_rule: dict, issues: list[ValidationIssue]) -> None:
    for ec in _iter_conditions(entry):
        for xc in _iter_conditions(exit_rule):
            if ec.get("indicator") != xc.get("indicator"):
                continue
            if sorted(ec.get("params", {}).items()) != sorted(xc.get("params", {}).items()):
                continue
            val_e, val_x = ec.get("value"), xc.get("value")
            comp_e, comp_x = ec.get("comparator"), xc.get("comparator")
            if not (isinstance(val_e, (int, float)) and isinstance(val_x, (int, float))):
                continue
            if (comp_e == "is_above" and comp_x == "is_below" and val_e <= val_x) or \
               (comp_e == "is_below" and comp_x == "is_above" and val_e >= val_x):
                issues.append(ValidationIssue(
                    "warning",
                    f"{entry.get('name')} vs {exit_rule.get('name')}",
                    f"Entry and exit rules have overlapping thresholds on same indicator — may trigger simultaneously",
                ))


def _check_duplicate_entry(a: dict, b: dict, issues: list[ValidationIssue]) -> None:
    for ac in _iter_conditions(a):
        for bc in _iter_conditions(b):
            if (ac.get("indicator") == bc.get("indicator") and
                ac.get("comparator") == bc.get("comparator") and
                sorted(ac.get("params", {}).items()) == sorted(bc.get("params", {}).items())):
                issues.append(ValidationIssue(
                    "warning",
                    f"{a.get('name')} vs {b.get('name')}",
                    f"Both entry rules use same indicator and comparator — may create duplicate positions",
                ))
                return  # one warning per pair is enough
