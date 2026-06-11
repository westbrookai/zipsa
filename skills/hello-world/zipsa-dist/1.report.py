"""Smoke-test phase: report Python runtime and confirm zipsa is up."""

from __future__ import annotations

import platform
import sys


def run(ctx: dict, prev: dict) -> dict:
    return {
        "status": "OK",
        "runtime": "Python",
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "skill_name": ctx.get("skill_name", "hello-world"),
        "user_facing_summary": (
            f"hello-world OK — Python {sys.version.split()[0]} on "
            f"{platform.system()}."
        ),
    }
