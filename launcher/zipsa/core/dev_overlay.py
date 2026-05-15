"""Development overlay loaded from the ZIPSA_DEV_OVERLAY env var.

Allows a developer to add extra mounts, run a startup script inside the
container, and inject env vars without editing any skill's manifest.yaml.
Off by default — only kicks in when ZIPSA_DEV_OVERLAY points to a YAML file.

Typical use case: develop an MCP server / CLI tool locally and bind-mount
it into the runtime container while iterating, instead of releasing a new
version every change.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class DevOverlay(BaseModel):
    """Schema for a dev overlay file.

    All fields optional. Unknown fields are rejected so typos surface early.
    """

    model_config = ConfigDict(extra="forbid")

    description: Optional[str] = None
    mounts: list[str] = Field(default_factory=list)
    preamble: Union[str, list[str]] = ""
    env: dict[str, str] = Field(default_factory=dict)

    @field_validator("mounts")
    @classmethod
    def _check_mount_format(cls, v: list[str]) -> list[str]:
        for entry in v:
            if not entry or ":" not in entry:
                raise ValueError(
                    f"invalid mount {entry!r}; expected 'host:container[:mode]'"
                )
        return v

    @property
    def preamble_str(self) -> str:
        """Render preamble as a single shell snippet (list joined by &&)."""
        if isinstance(self.preamble, list):
            return " && ".join(s for s in self.preamble if s)
        return self.preamble


def load_dev_overlay() -> Optional[DevOverlay]:
    """Load and validate the overlay file pointed to by ZIPSA_DEV_OVERLAY.

    Returns None when the env var is unset or empty. Raises FileNotFoundError
    when the path is set but doesn't exist. Raises PermissionError when the
    file isn't owned by the current user or is group/world-writable (someone
    else could inject mounts or shell snippets into the dev's container).
    Raises pydantic.ValidationError when the file is malformed.
    """
    raw = os.environ.get("ZIPSA_DEV_OVERLAY")
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"ZIPSA_DEV_OVERLAY path not found: {path}")

    st = path.stat()
    if st.st_uid != os.getuid():
        raise PermissionError(
            f"overlay {path} is owned by uid={st.st_uid}, not the current user "
            f"(uid={os.getuid()})"
        )
    if st.st_mode & 0o022:
        raise PermissionError(
            f"overlay {path} is group/world-writable (mode={oct(st.st_mode & 0o777)}); "
            "tighten with `chmod go-w`"
        )

    data = yaml.safe_load(path.read_text()) or {}
    return DevOverlay.model_validate(data)
