"""Run agenthud for a target date, emit its raw JSON as an artifact.

Atomic: no projection, no filtering — downstream consumers slice the
artifact themselves. The result carries counts only.

Docker mode needs the caller to mount the session logs and project
roots at their real host paths (see zipsa-dist/README.md).
"""

import datetime
import json
import pathlib
import re
import subprocess
import sys

AGENTHUD_VERSION = "0.9.2"
ARTIFACT_NAME = "agenthud-report.json"


def resolve_target_date(user_query: str) -> str:
    query = user_query.strip().lower()
    if query in ("", "today"):
        return datetime.date.today().isoformat()
    if query == "yesterday":
        return (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", query):
        try:
            datetime.date.fromisoformat(query)
        except ValueError:
            print(f"invalid target_date: {user_query}", file=sys.stderr)
            sys.exit(1)
        return query
    print(
        f"invalid target_date: {user_query!r} "
        "(accepted: today, yesterday, YYYY-MM-DD)",
        file=sys.stderr,
    )
    sys.exit(1)


def main() -> None:
    data = json.loads(sys.stdin.read())
    ctx = data["ctx"]
    target_date = resolve_target_date(ctx["user_query"])

    # Warmup: the first npx call in a fresh container prints npm
    # install noise to stdout; a throwaway --version populates the
    # cache so the real call's stdout is clean JSON.
    subprocess.run(
        ["npx", "-y", f"agenthud@{AGENTHUD_VERSION}", "--version"],
        capture_output=True,
    )

    # agenthud (node) truncates stdout when it's a pipe — process.exit
    # fires before the pipe drains. Redirecting to a real file keeps
    # node's writes synchronous, so stream straight into the artifact
    # (the legacy skill's `> artifact.json` did the same, knowingly or
    # not).
    artifact = pathlib.Path(ctx["out_dir"], ARTIFACT_NAME)
    with artifact.open("w") as sink:
        proc = subprocess.run(
            [
                "npx", "-y", f"agenthud@{AGENTHUD_VERSION}",
                "report",
                "--date", target_date,
                "--format", "json",
                "--include", "all",
                "--with-git",
            ],
            stdout=sink,
            stderr=subprocess.PIPE,
            text=True,
        )
    if proc.returncode != 0:
        artifact.unlink(missing_ok=True)
        print(f"agenthud failed: {proc.stderr[:500]}", file=sys.stderr)
        sys.exit(1)

    try:
        report = json.loads(artifact.read_text())
    except json.JSONDecodeError as e:
        artifact.unlink(missing_ok=True)
        print(f"agenthud emitted non-JSON output: {e}", file=sys.stderr)
        sys.exit(1)

    sessions = report.get("sessions", [])
    result = {
        "target_date": target_date,
        "session_count": len(sessions),
        "activity_count": sum(len(s.get("activities", [])) for s in sessions),
        "project_count": len({s.get("project") for s in sessions}),
        "artifact": ARTIFACT_NAME,
    }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
