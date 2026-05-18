"""JSON file-backed key/value store.

Reads the file on every get/keys so concurrent writers (e.g. another
process or another launcher run) are picked up live without cache
invalidation logic. Writes do a full read-modify-write to the same
file. File is created with 0600 permissions and the parent directory
is created on first write.

This is intentionally minimal: no schema validation, no TTL, no audit
log. Values must be JSON-serializable (str, int, float, bool, None,
list, dict). v1 makes no concurrency guarantee for simultaneous writers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class MemoryStore:
    """JSON dict on disk. One file per store."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        with open(self._path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._path.chmod(0o600)

    def get(self, key: str) -> Any | None:
        return self._read().get(key)

    def set(self, key: str, value: Any) -> None:
        data = self._read()
        data[key] = value
        self._write(data)

    def delete(self, key: str) -> bool:
        data = self._read()
        if key not in data:
            return False
        del data[key]
        self._write(data)
        return True

    def keys(self) -> list[str]:
        return list(self._read().keys())
