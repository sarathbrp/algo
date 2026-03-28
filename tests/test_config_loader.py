"""Tests for config_loader — specifically the deep_merge utility."""

from src.config_loader import deep_merge


class TestDeepMerge:
    """deep_merge should recursively merge overrides into a base dict."""

    def test_flat_override(self):
        base = {"a": 1, "b": 2}
        overrides = {"b": 99}
        result = deep_merge(base, overrides)
        assert result == {"a": 1, "b": 99}

    def test_nested_override(self):
        base = {"position_sizing": {"risk_per_trade_pct": 0.25, "max_position_dollar_cap": 2000}}
        overrides = {"position_sizing": {"max_position_dollar_cap": 1000}}
        result = deep_merge(base, overrides)
        assert result["position_sizing"]["risk_per_trade_pct"] == 0.25
        assert result["position_sizing"]["max_position_dollar_cap"] == 1000

    def test_deeply_nested(self):
        base = {"a": {"b": {"c": 1, "d": 2}, "e": 3}}
        overrides = {"a": {"b": {"c": 99}}}
        result = deep_merge(base, overrides)
        assert result == {"a": {"b": {"c": 99, "d": 2}, "e": 3}}

    def test_new_keys_added(self):
        base = {"a": 1}
        overrides = {"b": 2}
        result = deep_merge(base, overrides)
        assert result == {"a": 1, "b": 2}

    def test_list_replaced_not_merged(self):
        base = {"symbols": ["SPY", "QQQ"]}
        overrides = {"symbols": ["AAPL"]}
        result = deep_merge(base, overrides)
        assert result["symbols"] == ["AAPL"]

    def test_base_not_mutated(self):
        base = {"a": {"b": 1}}
        overrides = {"a": {"b": 99}}
        deep_merge(base, overrides)
        assert base["a"]["b"] == 1

    def test_empty_overrides(self):
        base = {"a": 1, "b": {"c": 2}}
        result = deep_merge(base, {})
        assert result == base

    def test_override_scalar_with_dict(self):
        base = {"a": 1}
        overrides = {"a": {"nested": True}}
        result = deep_merge(base, overrides)
        assert result == {"a": {"nested": True}}

    def test_override_dict_with_scalar(self):
        base = {"a": {"nested": True}}
        overrides = {"a": "flat"}
        result = deep_merge(base, overrides)
        assert result == {"a": "flat"}

    def test_none_override_value(self):
        base = {"a": 1}
        overrides = {"a": None}
        result = deep_merge(base, overrides)
        assert result == {"a": None}
