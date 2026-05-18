"""Tests for MemoryStore — JSON file-backed KV store."""

import json
import stat
from pathlib import Path

import pytest

from zipsa.core.memory_store import MemoryStore


class TestMemoryStore:
    def test_get_missing_returns_none(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        assert store.get("missing") is None

    def test_set_then_get_roundtrip_string(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("name", "Westbrook")
        assert store.get("name") == "Westbrook"

    def test_set_then_get_roundtrip_int(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("n", 42)
        assert store.get("n") == 42

    def test_set_then_get_roundtrip_list(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("items", ["a", "b", "c"])
        assert store.get("items") == ["a", "b", "c"]

    def test_set_then_get_roundtrip_dict(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("conf", {"workspace": "X", "db": "Y"})
        assert store.get("conf") == {"workspace": "X", "db": "Y"}

    def test_set_creates_file_with_0600_perms(self, tmp_path):
        path = tmp_path / "memory.json"
        store = MemoryStore(path)
        store.set("k", "v")
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_set_creates_parent_dir_if_missing(self, tmp_path):
        path = tmp_path / "deep" / "nested" / "memory.json"
        store = MemoryStore(path)
        store.set("k", "v")
        assert path.exists()

    def test_set_overwrites_existing_key(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("k", "v1")
        store.set("k", "v2")
        assert store.get("k") == "v2"

    def test_delete_existing_returns_true(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("k", "v")
        assert store.delete("k") is True
        assert store.get("k") is None

    def test_delete_missing_returns_false(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        assert store.delete("never_existed") is False

    def test_keys_empty_when_file_missing(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        assert store.keys() == []

    def test_keys_returns_stored_keys(self, tmp_path):
        store = MemoryStore(tmp_path / "memory.json")
        store.set("a", 1)
        store.set("b", 2)
        assert sorted(store.keys()) == ["a", "b"]

    def test_two_stores_share_file_see_same_data(self, tmp_path):
        """Reading on each get means file changes are picked up live."""
        path = tmp_path / "memory.json"
        store1 = MemoryStore(path)
        store2 = MemoryStore(path)
        store1.set("k", "from-1")
        assert store2.get("k") == "from-1"

    def test_get_with_corrupt_json_raises(self, tmp_path):
        path = tmp_path / "memory.json"
        path.write_text("not json {")
        store = MemoryStore(path)
        with pytest.raises(json.JSONDecodeError):
            store.get("k")
