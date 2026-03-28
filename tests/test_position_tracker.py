"""Tests for position_tracker — per-user scoping, CRUD, migration."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

from src import position_tracker as pt


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def data_dir(tmp_path):
    """Return a clean temporary data directory."""
    d = tmp_path / "data"
    d.mkdir()
    return d


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

class TestUserPath:

    def test_default_user(self, data_dir):
        p = pt._user_path("default", data_dir)
        assert p.name == "positions_default.json"

    def test_named_user(self, data_dir):
        p = pt._user_path("alice", data_dir)
        assert p.name == "positions_alice.json"


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------

class TestLegacyMigration:

    def test_migrates_legacy_file(self, data_dir):
        legacy = data_dir / "positions_tracked.json"
        legacy.write_text(json.dumps({"AAPL": {"qty": 10}}))

        pt._migrate_legacy(data_dir)

        target = data_dir / "positions_default.json"
        assert target.exists()
        assert json.loads(target.read_text()) == {"AAPL": {"qty": 10}}
        # Legacy renamed to .bak
        assert not legacy.exists()
        assert (data_dir / "positions_tracked.json.bak").exists()

    def test_no_migration_when_target_exists(self, data_dir):
        legacy = data_dir / "positions_tracked.json"
        legacy.write_text(json.dumps({"OLD": {}}))
        target = data_dir / "positions_default.json"
        target.write_text(json.dumps({"NEW": {}}))

        pt._migrate_legacy(data_dir)

        # Target not overwritten
        assert json.loads(target.read_text()) == {"NEW": {}}
        # Legacy not renamed
        assert legacy.exists()

    def test_no_migration_when_no_legacy(self, data_dir):
        pt._migrate_legacy(data_dir)
        assert not (data_dir / "positions_default.json").exists()


# ---------------------------------------------------------------------------
# CRUD operations with user_id
# ---------------------------------------------------------------------------

class TestLoadSave:

    def test_load_empty(self, data_dir):
        result = pt.load("alice", data_dir=data_dir)
        assert result == {}

    def test_save_and_load(self, data_dir):
        pt.save({"AAPL": {"qty": 5}}, "alice", data_dir=data_dir)
        result = pt.load("alice", data_dir=data_dir)
        assert result == {"AAPL": {"qty": 5}}

    def test_users_isolated(self, data_dir):
        pt.save({"AAPL": {"qty": 5}}, "alice", data_dir=data_dir)
        pt.save({"TSLA": {"qty": 10}}, "bob", data_dir=data_dir)

        assert "AAPL" in pt.load("alice", data_dir=data_dir)
        assert "TSLA" not in pt.load("alice", data_dir=data_dir)
        assert "TSLA" in pt.load("bob", data_dir=data_dir)
        assert "AAPL" not in pt.load("bob", data_dir=data_dir)

    def test_base_path_backward_compat(self, data_dir):
        custom = data_dir / "custom.json"
        pt.save({"SPY": {"qty": 1}}, base_path=custom)
        assert pt.load(base_path=custom) == {"SPY": {"qty": 1}}


class TestAdd:

    def test_add_creates_position(self, data_dir):
        pt.add("AAPL", 10, 150.0, 1.5, user_id="alice", data_dir=data_dir)
        data = pt.load("alice", data_dir=data_dir)
        assert "AAPL" in data
        assert data["AAPL"]["qty"] == 10
        assert data["AAPL"]["entry_price"] == 150.0
        assert data["AAPL"]["stop_pct"] == 1.5
        assert data["AAPL"]["side"] == "long"

    def test_add_uppercases_symbol(self, data_dir):
        pt.add("aapl", 5, 100.0, 1.0, user_id="alice", data_dir=data_dir)
        assert "AAPL" in pt.load("alice", data_dir=data_dir)

    def test_add_same_symbol_different_users(self, data_dir):
        pt.add("AAPL", 10, 150.0, 1.5, user_id="alice", data_dir=data_dir)
        pt.add("AAPL", 20, 160.0, 2.0, user_id="bob", data_dir=data_dir)

        alice = pt.load("alice", data_dir=data_dir)
        bob = pt.load("bob", data_dir=data_dir)
        assert alice["AAPL"]["qty"] == 10
        assert bob["AAPL"]["qty"] == 20


class TestMergeAddShares:

    def test_merge_adds_to_existing(self, data_dir):
        pt.add("AAPL", 10, 100.0, 1.5, user_id="alice", data_dir=data_dir)
        pt.merge_add_shares("AAPL", 10, 120.0, user_id="alice", data_dir=data_dir)
        data = pt.load("alice", data_dir=data_dir)
        assert data["AAPL"]["qty"] == 20
        # Weighted average: (100*10 + 120*10) / 20 = 110
        assert data["AAPL"]["entry_price"] == pytest.approx(110.0)

    def test_merge_creates_if_not_tracked(self, data_dir):
        pt.merge_add_shares("NVDA", 5, 200.0, user_id="alice", data_dir=data_dir)
        data = pt.load("alice", data_dir=data_dir)
        assert "NVDA" in data
        assert data["NVDA"]["qty"] == 5

    def test_merge_zero_qty_noop(self, data_dir):
        pt.merge_add_shares("AAPL", 0, 100.0, user_id="alice", data_dir=data_dir)
        assert pt.load("alice", data_dir=data_dir) == {}


class TestUpdate:

    def test_update_fields(self, data_dir):
        pt.add("AAPL", 10, 150.0, 1.5, user_id="alice", data_dir=data_dir)
        pt.update("AAPL", qty=5, partial_taken=True, trail_high=155.0,
                  user_id="alice", data_dir=data_dir)
        data = pt.load("alice", data_dir=data_dir)
        assert data["AAPL"]["qty"] == 5
        assert data["AAPL"]["partial_taken"] is True
        assert data["AAPL"]["trail_high"] == 155.0

    def test_update_missing_symbol_noop(self, data_dir):
        pt.update("AAPL", qty=5, user_id="alice", data_dir=data_dir)
        assert pt.load("alice", data_dir=data_dir) == {}


class TestRemove:

    def test_remove_position(self, data_dir):
        pt.add("AAPL", 10, 150.0, 1.5, user_id="alice", data_dir=data_dir)
        pt.remove("AAPL", user_id="alice", data_dir=data_dir)
        assert "AAPL" not in pt.load("alice", data_dir=data_dir)

    def test_remove_missing_noop(self, data_dir):
        pt.remove("AAPL", user_id="alice", data_dir=data_dir)


class TestClearAll:

    def test_clears_all(self, data_dir):
        pt.add("AAPL", 10, 150.0, 1.5, user_id="alice", data_dir=data_dir)
        pt.add("TSLA", 5, 200.0, 2.0, user_id="alice", data_dir=data_dir)
        pt.clear_all(user_id="alice", data_dir=data_dir)
        assert pt.load("alice", data_dir=data_dir) == {}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

class TestTimeHelpers:

    def test_bars_held(self):
        two_days_ago = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        assert pt.bars_held(two_days_ago) == 2

    def test_bars_held_invalid_string(self):
        # Invalid string falls back to now → 0 days
        assert pt.bars_held("not-a-date") == 0

    def test_minutes_held(self):
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        result = pt.minutes_held(one_hour_ago)
        assert 59.0 <= result <= 61.0

    def test_minutes_held_empty_string(self):
        assert pt.minutes_held("") == 0.0

    def test_minutes_held_invalid(self):
        assert pt.minutes_held("garbage") == 0.0

    def test_minutes_held_z_suffix(self):
        one_hour_ago = (datetime.now(timezone.utc) - timedelta(hours=1))
        iso = one_hour_ago.strftime("%Y-%m-%dT%H:%M:%S") + "Z"
        result = pt.minutes_held(iso)
        assert 59.0 <= result <= 61.0
