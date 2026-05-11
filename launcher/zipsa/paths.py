"""Centralized path resolution for the zipsa home directory.

Set ZIPSA_HOME to override the default ~/.zipsa location (useful in tests).
"""

import os
from pathlib import Path


def zipsa_home() -> Path:
    env = os.environ.get("ZIPSA_HOME")
    return Path(env) if env else Path.home() / ".zipsa"


def skill_data_dir(name: str, version: str) -> Path:
    return zipsa_home() / f"{name}@{version}"


def skill_runs_dir(name: str, version: str) -> Path:
    return skill_data_dir(name, version) / "runs"


def skill_env_file(name: str, version: str) -> Path:
    return skill_data_dir(name, version) / ".env"


def global_env_file() -> Path:
    return zipsa_home() / ".env"


def credentials_dir() -> Path:
    return zipsa_home() / "credentials"


class SkillNotInstalledError(Exception):
    pass


def skills_dir() -> Path:
    return zipsa_home() / "skills"


def installed_skill_dir(name: str) -> Path:
    return skills_dir() / name


def resolve_skill(name: str) -> Path:
    path = installed_skill_dir(name)
    if not path.exists():
        raise SkillNotInstalledError(
            f"Skill '{name}' not found. Try: zipsa install <source>"
        )
    return path
