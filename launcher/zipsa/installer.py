"""Skill installer — source parsing, GitHub download, local copy/link."""

import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class GitHubSource:
    user: str
    repo: str
    subpath: str  # "" for repo root
    ref: str      # "HEAD" if unspecified


def parse_github_source(source: str) -> GitHubSource:
    """Parse a GitHub source string into (user, repo, subpath, ref)."""
    # Strip explicit scheme
    if source.startswith("github:"):
        source = source[7:]
    elif source.startswith("https://github.com/"):
        path = source[len("https://github.com/"):]
        # Handle /tree/{ref}/{subpath} form
        m = re.match(r"([^/]+)/([^/]+)/(?:tree|blob)/([^/]+)(?:/(.+))?$", path)
        if m:
            return GitHubSource(
                user=m.group(1),
                repo=m.group(2),
                ref=m.group(3),
                subpath=m.group(4) or "",
            )
        # Plain https://github.com/user/repo[/...]
        source = path

    # Extract @ref suffix
    ref = "HEAD"
    if "@" in source:
        source, ref = source.rsplit("@", 1)
        if not ref:
            raise ValueError(
                f"Invalid GitHub source: ref cannot be empty after '@'"
            )

    parts = source.split("/")
    if len(parts) < 2:
        raise ValueError(
            f"Invalid GitHub source: '{source}'. "
            "Expected format: user/repo[/subpath][@ref]"
        )

    return GitHubSource(
        user=parts[0],
        repo=parts[1],
        subpath="/".join(parts[2:]) if len(parts) > 2 else "",
        ref=ref,
    )
