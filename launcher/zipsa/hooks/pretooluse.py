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


_DENIAL_PREFIX = "[HOOK_DENIAL]"


def deny(reason: str) -> None:
    # Prefix every denial so the launcher's limits tracker can distinguish
    # hook denials (deterministic config decisions) from other tool errors
    # (transient / recoverable). The contract instructs the agent to stop
    # retrying after a hook denial; the launcher enforces a hard cap as
    # defense in depth.
    emit("deny", f"{_DENIAL_PREFIX} {reason}")


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
    """Split a shell command into segments by &&, ||, ;, | — respecting
    single and double quotes.

    Previous implementation used `.replace()` which incorrectly split on
    operator characters inside quoted strings — e.g. `jq '.foo | .bar'`
    became two broken segments because the jq filter's `|` was treated
    as a shell pipe. This walker tracks quote state and only splits on
    unquoted operators.

    Limitations (acceptable for v1 — agents don't write these):
    - Does not handle backslash-escaping outside quotes (`\\|` would
      still split).
    - Does not handle here-docs or process substitution.
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0
    n = len(command)
    while i < n:
        c = command[i]

        # Quote tracking — single and double can't open inside each other.
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
            continue

        # Operators only outside quotes.
        if not in_single and not in_double:
            # Two-char operators first (so "&&" doesn't read as "& &").
            if c in "&|" and i + 1 < n and command[i + 1] == c:
                segments.append("".join(current).strip())
                current = []
                i += 2
                continue
            # Single-char operators: ; and |
            if c in ";|":
                segments.append("".join(current).strip())
                current = []
                i += 1
                continue

        current.append(c)
        i += 1

    if current:
        segments.append("".join(current).strip())
    return [s for s in segments if s]


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
