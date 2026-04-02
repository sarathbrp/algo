"""Tests for src/rules_engine/validator.py — rule validation and conflict detection."""
from __future__ import annotations

import pytest

from src.rules_engine.validator import validate_rule, detect_conflicts, ValidationIssue


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _entry_tree(conditions=None, actions=None, logic="AND"):
    """Build a minimal entry rule tree."""
    return {
        "groups": [{"logic": logic, "conditions": conditions if conditions is not None else []}],
        "actions": actions if actions is not None else [{"action": "enter_long", "params": {}}],
    }


def _exit_tree(conditions=None, actions=None, logic="AND"):
    """Build a minimal exit rule tree."""
    return {
        "groups": [{"logic": logic, "conditions": conditions if conditions is not None else []}],
        "actions": actions if actions is not None else [{"action": "exit_position", "params": {}}],
    }


def _simple_condition(indicator="rsi", period=14, comparator="is_above", value=50):
    return {
        "indicator": indicator,
        "params": {"period": period},
        "comparator": comparator,
        "value": value,
    }


def _make_rule(name, rule_type, rule_tree, is_active=True):
    return {
        "name": name,
        "rule_type": rule_type,
        "rule_tree": rule_tree,
        "is_active": is_active,
    }


# ---------------------------------------------------------------------------
# validate_rule — action checks
# ---------------------------------------------------------------------------

class TestValidateRuleActions:
    """Action presence and compatibility checks."""

    def test_entry_missing_entry_action(self):
        tree = _entry_tree(
            conditions=[_simple_condition()],
            actions=[{"action": "set_stop_loss", "params": {"pct": 2}}],
        )
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert any("entry action" in i.message.lower() or "Buy" in i.message for i in errors)

    def test_exit_missing_exit_action(self):
        tree = _exit_tree(
            conditions=[_simple_condition()],
            actions=[],  # no actions at all
        )
        issues = validate_rule(tree, "exit")
        errors = [i for i in issues if i.severity == "error"]
        assert any("exit" in i.message.lower() for i in errors)

    def test_entry_with_exit_position_action(self):
        tree = _entry_tree(
            conditions=[_simple_condition()],
            actions=[
                {"action": "enter_long", "params": {}},
                {"action": "exit_position", "params": {}},
            ],
        )
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert any("exit" in i.message.lower() for i in errors)

    def test_exit_with_enter_long_action(self):
        tree = _exit_tree(
            conditions=[_simple_condition()],
            actions=[
                {"action": "exit_position", "params": {}},
                {"action": "enter_long", "params": {}},
            ],
        )
        issues = validate_rule(tree, "exit")
        errors = [i for i in issues if i.severity == "error"]
        assert any("entry" in i.message.lower() or "Buy" in i.message for i in errors)

    def test_valid_entry_rule_no_issues(self):
        tree = _entry_tree(
            conditions=[_simple_condition()],
            actions=[{"action": "enter_long", "params": {}}],
        )
        issues = validate_rule(tree, "entry")
        assert issues == []

    def test_valid_exit_rule_no_issues(self):
        tree = _exit_tree(
            conditions=[_simple_condition()],
            actions=[{"action": "exit_position", "params": {}}],
        )
        issues = validate_rule(tree, "exit")
        assert issues == []

    def test_exit_with_stop_loss_only_is_valid(self):
        """Exit rule with set_stop_loss (no exit_position) should be valid."""
        tree = _exit_tree(
            conditions=[_simple_condition()],
            actions=[{"action": "set_stop_loss", "params": {"pct": 2}}],
        )
        issues = validate_rule(tree, "exit")
        errors = [i for i in issues if i.severity == "error"]
        assert not errors


# ---------------------------------------------------------------------------
# validate_rule — condition checks
# ---------------------------------------------------------------------------

class TestValidateRuleConditions:
    """Condition-level validations (periods, groups, bounds)."""

    def test_empty_condition_group(self):
        tree = {
            "groups": [{"logic": "AND", "conditions": []}],
            "actions": [{"action": "enter_long", "params": {}}],
        }
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert any("empty" in i.message.lower() for i in errors)

    def test_period_above_500_is_error(self):
        tree = _entry_tree(
            conditions=[_simple_condition(period=501)],
        )
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert any("period" in i.message.lower() or "exceeds" in i.message.lower() for i in errors)

    def test_period_above_200_is_warning_not_error(self):
        tree = _entry_tree(
            conditions=[_simple_condition(period=250)],
        )
        issues = validate_rule(tree, "entry")
        warnings = [i for i in issues if i.severity == "warning"]
        errors = [i for i in issues if i.severity == "error"]
        assert len(warnings) >= 1
        assert any("period" in w.message.lower() or "historical" in w.message.lower() for w in warnings)
        # Should NOT produce an error for period=250
        assert not any("period" in e.message.lower() for e in errors)

    def test_contradictory_conditions_is_above_and_is_below(self):
        """is_above 70 AND is_below 30 on same indicator => always false => error."""
        tree = _entry_tree(
            conditions=[
                _simple_condition(indicator="rsi", period=14, comparator="is_above", value=70),
                _simple_condition(indicator="rsi", period=14, comparator="is_below", value=30),
            ],
            logic="AND",
        )
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert any("contradictory" in i.message.lower() for i in errors)

    def test_between_reversed_bounds(self):
        """between [80, 20] should produce an error — lower > upper."""
        cond = {
            "indicator": "rsi",
            "params": {"period": 14},
            "comparator": "between",
            "value": [80, 20],
        }
        tree = _entry_tree(conditions=[cond])
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert any("bound" in i.message.lower() or "greater" in i.message.lower() for i in errors)

    def test_between_valid_bounds_no_error(self):
        cond = {
            "indicator": "rsi",
            "params": {"period": 14},
            "comparator": "between",
            "value": [20, 80],
        }
        tree = _entry_tree(conditions=[cond])
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert not errors

    def test_no_groups_is_error(self):
        tree = {
            "groups": [],
            "actions": [{"action": "enter_long", "params": {}}],
        }
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert any("no condition" in i.message.lower() for i in errors)

    def test_value_indicator_period_exceeds_max(self):
        """Period inside the value indicator ref should also be validated."""
        cond = {
            "indicator": "price",
            "params": {"field": "close"},
            "comparator": "is_above",
            "value": {"indicator": "sma", "params": {"period": 600}},
        }
        tree = _entry_tree(conditions=[cond])
        issues = validate_rule(tree, "entry")
        errors = [i for i in issues if i.severity == "error"]
        assert any("period" in i.message.lower() or "exceeds" in i.message.lower() for i in errors)


# ---------------------------------------------------------------------------
# detect_conflicts
# ---------------------------------------------------------------------------

class TestDetectConflicts:
    """Cross-rule conflict detection."""

    def test_entry_exit_overlapping_thresholds_warning(self):
        """Entry is_above 30 + Exit is_below 70 on same indicator => overlapping."""
        entry_tree = _entry_tree(
            conditions=[_simple_condition(indicator="rsi", period=14, comparator="is_above", value=30)],
        )
        exit_tree = _exit_tree(
            conditions=[_simple_condition(indicator="rsi", period=14, comparator="is_below", value=70)],
        )
        rules = [
            _make_rule("RSI Entry", "entry", entry_tree),
            _make_rule("RSI Exit", "exit", exit_tree),
        ]
        issues = detect_conflicts(rules)
        warnings = [i for i in issues if i.severity == "warning"]
        assert len(warnings) >= 1
        assert any("overlap" in w.message.lower() or "simultaneously" in w.message.lower() for w in warnings)

    def test_two_entry_rules_same_indicator_comparator_warning(self):
        entry1 = _entry_tree(
            conditions=[_simple_condition(indicator="rsi", period=14, comparator="is_above", value=50)],
        )
        entry2 = _entry_tree(
            conditions=[_simple_condition(indicator="rsi", period=14, comparator="is_above", value=60)],
        )
        rules = [
            _make_rule("Entry A", "entry", entry1),
            _make_rule("Entry B", "entry", entry2),
        ]
        issues = detect_conflicts(rules)
        warnings = [i for i in issues if i.severity == "warning"]
        assert len(warnings) >= 1
        assert any("duplicate" in w.message.lower() or "same indicator" in w.message.lower() for w in warnings)

    def test_inactive_rules_are_skipped(self):
        entry1 = _entry_tree(
            conditions=[_simple_condition(indicator="rsi", period=14, comparator="is_above", value=50)],
        )
        entry2 = _entry_tree(
            conditions=[_simple_condition(indicator="rsi", period=14, comparator="is_above", value=60)],
        )
        rules = [
            _make_rule("Entry A", "entry", entry1, is_active=True),
            _make_rule("Entry B", "entry", entry2, is_active=False),
        ]
        issues = detect_conflicts(rules)
        # The inactive rule should be excluded, so no duplicate warning
        assert len(issues) == 0

    def test_no_conflicts_different_indicators(self):
        entry_tree = _entry_tree(
            conditions=[_simple_condition(indicator="rsi", period=14, comparator="is_above", value=50)],
        )
        exit_tree = _exit_tree(
            conditions=[_simple_condition(indicator="sma", period=20, comparator="is_below", value=100)],
        )
        rules = [
            _make_rule("RSI Entry", "entry", entry_tree),
            _make_rule("SMA Exit", "exit", exit_tree),
        ]
        issues = detect_conflicts(rules)
        assert issues == []

    def test_empty_rules_list(self):
        issues = detect_conflicts([])
        assert issues == []
