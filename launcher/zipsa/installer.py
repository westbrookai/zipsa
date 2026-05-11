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


def _github_headers() -> dict:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_commit_sha(source: GitHubSource) -> str:
    """Resolve ref to full commit SHA via GitHub commits API."""
    url = (
        f"https://api.github.com/repos/{source.user}/{source.repo}"
        f"/commits/{source.ref}"
    )
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read())["sha"]
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FileNotFoundError(
                f"Repository not found: {source.user}/{source.repo}"
            )
        raise RuntimeError(f"Failed to fetch commit info: {e}")


def _download_tarball(source: GitHubSource, dest: Path) -> None:
    """Download GitHub tarball and extract skill files into dest."""
    url = (
        f"https://api.github.com/repos/{source.user}/{source.repo}"
        f"/tarball/{source.ref}"
    )
    req = urllib.request.Request(url, headers=_github_headers())
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise FileNotFoundError(
                f"Repository or path not found: {source.user}/{source.repo}"
            )
        raise RuntimeError(f"Failed to download: {e}")

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        members = tar.getmembers()
        if not members:
            raise RuntimeError("Downloaded tarball is empty")

        # Root dir is "user-repo-{sha}/"
        root_prefix = members[0].name.split("/")[0] + "/"

        for member in members:
            if not member.name.startswith(root_prefix):
                continue
            member_rel = member.name[len(root_prefix):]

            if source.subpath:
                if not member_rel.startswith(source.subpath + "/"):
                    continue
                file_rel = member_rel[len(source.subpath) + 1:]
            else:
                file_rel = member_rel

            if not file_rel:
                continue

            target = dest / file_rel
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                target.parent.mkdir(parents=True, exist_ok=True)
                f = tar.extractfile(member)
                if f:
                    target.write_bytes(f.read())


def _write_install_json(
    dest: Path,
    source_str: str,
    ref: str,
    version: str,
    install_type: str,
    commit_sha: str = "",
) -> None:
    meta: dict = {
        "source": source_str,
        "ref": ref,
        "version": version,
        "type": install_type,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    if commit_sha:
        meta["commit_sha"] = commit_sha
    (dest / "_install.json").write_text(json.dumps(meta, indent=2))


def install_from_github(source_str: str, force: bool = False) -> str:
    """Download and install a skill from GitHub. Returns installed skill name."""
    from .paths import skills_dir
    from .core.skill import Skill
    from pydantic import ValidationError

    source = parse_github_source(source_str)
    canonical = f"github:{source.user}/{source.repo}"
    if source.subpath:
        canonical += f"/{source.subpath}"
    canonical += f"@{source.ref}"

    commit_sha = _get_commit_sha(source)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _download_tarball(source, tmp_path)

        try:
            skill = Skill.load(tmp_path)
        except FileNotFoundError:
            subpath_hint = source.subpath or "(repo root)"
            raise FileNotFoundError(
                f"No manifest.yaml found at {source.user}/{source.repo}/{subpath_hint}"
            )
        except ValidationError as e:
            raise ValueError(f"Install failed: invalid manifest — {e}")

        name = skill.name
        version = skill.manifest.metadata.version
        dest = skills_dir() / name

        if dest.exists() and not force:
            raise FileExistsError(
                f"Skill '{name}' is already installed. Use --force to overwrite."
            )
        if dest.exists():
            shutil.rmtree(dest)

        shutil.copytree(tmp_path, dest)

    _write_install_json(dest, canonical, source.ref, version, "github", commit_sha)
    return name
