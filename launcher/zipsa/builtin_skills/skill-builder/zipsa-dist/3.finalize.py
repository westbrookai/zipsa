"""Finalize: name the skill (now, after it works), validate, install.

This is the only phase where the skill gets a name. Until now it lived
under /tmp/zipsa-staging-<timestamp>/ so the user could change their
mind about what it does without rewriting the directory tree every time.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from zipsa import hitl


NAME_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def run(ctx: dict, prev: dict) -> dict:
    if prev.get("short_circuited"):
        return {
            "status": "short_circuited",
            "user_facing_summary": "Nothing to finalize — phase 2 exited early.",
        }

    staging = Path(prev["staging_path"])
    final_intent = prev.get("final_intent", "").strip()

    intent_line = f"Intent: {final_intent}\n" if final_intent else ""
    prompt = f"{intent_line}What should this skill be called? (kebab-case, e.g. `morning-notion-log`)"

    name = hitl.ask(prompt).strip()
    while not NAME_RE.match(name):
        name = hitl.ask(
            f"`{name}` is not valid. Use kebab-case (a-z, 0-9, hyphen; "
            "must start with a letter):",
        ).strip()

    final = staging.parent / name
    if final.exists():
        if not hitl.confirm(
            f"`{final}` already exists. Overwrite?",
            default=False,
        ):
            return {
                "status": "aborted",
                "skill_name": name,
                "staging_path": str(staging),
                "user_facing_summary": (
                    f"Not overwriting. Staging left at {staging}. "
                    f"Rerun finalize with a different name when ready."
                ),
            }
        shutil.rmtree(final)
    staging.rename(final)

    validate = subprocess.run(
        ["zipsa", "validate", str(final)],
        capture_output=True,
        text=True,
    )
    if validate.returncode != 0:
        return {
            "status": "validation_failed",
            "skill_name": name,
            "staging_path": str(final),
            "stderr": validate.stderr.strip(),
            "user_facing_summary": (
                f"`{name}` failed validation. Fix the issues at {final} "
                f"and run `zipsa install --link {final}` directly."
            ),
        }

    if not hitl.confirm(
        f"Validation passed. Install `{name}`?",
        default=True,
    ):
        return {
            "status": "user_declined_install",
            "skill_name": name,
            "staging_path": str(final),
            "user_facing_summary": (
                f"Did not install `{name}`. Source is at {final}. "
                f"Run `zipsa install --link {final}` when ready."
            ),
        }

    install = subprocess.run(
        ["zipsa", "install", "--link", str(final)],
        capture_output=True,
        text=True,
    )
    if install.returncode != 0:
        return {
            "status": "install_failed",
            "skill_name": name,
            "stderr": install.stderr.strip(),
        }

    return {
        "status": "installed",
        "skill_name": name,
        "user_facing_summary": (
            f"Installed `{name}`. Try `zipsa run {name}` to verify."
        ),
    }
