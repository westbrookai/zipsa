"""Token storage for OAuth credentials."""

import json
from pathlib import Path
from typing import Optional

from zipsa.paths import credentials_dir


class FileTokenStorage:
    """Persists OAuth tokens to ~/.zipsa/credentials/<name>.json with 600 permissions."""

    def __init__(self, server_name: str):
        self.path = credentials_dir() / f"{server_name}.json"

    async def load(self) -> Optional[dict]:
        """Load credentials from disk. Returns None if file doesn't exist."""
        if not self.path.exists():
            return None
        return json.loads(self.path.read_text())

    async def save(self, creds: dict) -> None:
        """Save credentials to disk with 600 permissions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(creds, indent=2))
        self.path.chmod(0o600)

    async def load_client_info(self) -> Optional[dict]:
        """Load DCR client_id/client_secret. Returns None if not registered yet."""
        creds = await self.load()
        if not creds or "client_id" not in creds:
            return None
        return {
            "client_id": creds["client_id"],
            "client_secret": creds.get("client_secret"),
        }

    async def save_client_info(self, info: dict) -> None:
        """Merge client_id/client_secret into existing credentials."""
        creds = await self.load() or {}
        creds.update(info)
        await self.save(creds)
