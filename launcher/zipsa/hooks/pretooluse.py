#!/usr/bin/env python3
"""PreToolUse hook for zipsa: enforce per-phase tool whitelist.

Reads tool invocation from stdin, looks up the phase's allowed tools from
the file pointed to by ZIPSA_PHASE_ALLOW (default /.zipsa/phase-allow.json),
and emits a permissionDecision JSON to stdout.

For Bash, supports prefix-based command restrictions: an entry like
`Bash(git:*)` allows commands whose first word is `git`. Compound commands
(joined by `&&`, `||`, `;`, `|`) require every segment to be allowed.
Constructs that could circumvent the prefix check (`bash -c`, `sh -c`,
`eval`, command substitution `$(...)`, backticks) are always denied.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys


DEFAULT_PHASE_ALLOW_PATH = "/.zipsa/phase-allow.json"

# Constructs that could bypass first-word prefix matching.
_DANGEROUS_TOKENS = ("bash", "sh", "zsh", "eval")
_SUBSTITUTION_RE = re.compile(r"\$\(|`")


def emit(decision: str, reason: str) -> None:
    """Print the hook response and exit."""
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    json.dump(out, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0)


def deny(reason: str) -> None:
    emit("deny", reason)


def allow(reason: str = "ok") -> None:
    emit("allow", reason)


def load_allowed_tools() -> list[str]:
    path = os.environ.get("ZIPSA_PHASE_ALLOW", DEFAULT_PHASE_ALLOW_PATH)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    tools = data.get("allowed_tools", [])
    if not isinstance(tools, list):
        raise ValueError("allowed_tools must be a list")
    return [str(t) for t in tools]


def parse_bash_patterns(allowed_tools: list[str]) -> tuple[set[str], bool]:
    """Extract Bash prefix patterns from the allow list.

    Returns (prefixes, has_wildcard).
    - `Bash(git:*)` → adds 'git' to prefixes
    - `Bash(*)` → sets has_wildcard=True
    - bare `Bash` → ignored (strict mode: no commands allowed)
    """
    prefixes: set[str] = set()
    has_wildcard = False
    for entry in allowed_tools:
        if entry == "Bash(*)":
            has_wildcard = True
            continue
        m = re.fullmatch(r"Bash\(([^)]+):\*\)", entry)
        if m:
            prefixes.add(m.group(1))
    return prefixes, has_wildcard


def split_compound(command: str) -> list[str]:
    """Split a shell command into segments by &&, ||, ;, |."""
    # Replace the multi-char operators first, then single-char.
    # Order matters: && before &, || before |.
    placeholder = "\x00SPLIT\x00"
    tmp = command
    for op in ("&&", "||", ";", "|"):
        tmp = tmp.replace(op, placeholder)
    return [seg.strip() for seg in tmp.split(placeholder) if seg.strip()]


def first_word(segment: str) -> str:
    """Return the first command word of a shell segment, ignoring env assignments."""
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return ""
    for tok in tokens:
        # Skip env assignments like FOO=bar
        if "=" in tok and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            continue
        return tok
    return ""


def check_bash(command: str, prefixes: set[str], has_wildcard: bool) -> tuple[bool, str]:
    """Return (allowed, reason) for a Bash command."""
    if _SUBSTITUTION_RE.search(command):
        return False, "command substitution ($(...) or backticks) is not allowed"

    segments = split_compound(command)
    if not segments:
        return False, "empty command"

    for seg in segments:
        word = first_word(seg)
        if not word:
            return False, f"unparseable segment: {seg!r}"
        if word in _DANGEROUS_TOKENS:
            return False, f"dangerous shell construct: {word!r}"
        if has_wildcard:
            continue
        if word not in prefixes:
            allowed_str = ", ".join(sorted(f"Bash({p}:*)" for p in prefixes)) or "(none)"
            return False, f"command {word!r} not allowed; allowed: {allowed_str}"
    return True, "ok"


def main() -> None:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        deny("malformed hook input")
        return

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}

    try:
        allowed_tools = load_allowed_tools()
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError) as e:
        deny(f"phase allow list unavailable: {e}")
        return

    if tool_name == "Bash":
        prefixes, has_wildcard = parse_bash_patterns(allowed_tools)
        if not prefixes and not has_wildcard:
            deny("Bash is not allowed in this phase (use Bash(prefix:*) or Bash(*))")
            return
        command = tool_input.get("command", "")
        ok, reason = check_bash(command, prefixes, has_wildcard)
        if ok:
            allow(reason)
        else:
            deny(reason)
        return

    # Non-Bash: exact name match
    if tool_name in allowed_tools:
        allow("ok")
    else:
        deny(f"tool {tool_name!r} not in allowed list for this phase")


if __name__ == "__main__":
    main()
