"""Smoke-test phase: report Python runtime and confirm zipsa is up.

Phase 0 contract: read {"ctx": ...} from stdin, print the result as a
JSON object on the last stdout line, exit 0.
"""

import json
import platform
import sys


def main() -> None:
    ctx = json.loads(sys.stdin.read())["ctx"]
    print(json.dumps({
        "status": "OK",
        "runtime": "Python",
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "skill_name": ctx["skill_name"],
        "user_query": ctx["user_query"],
    }))


if __name__ == "__main__":
    main()
