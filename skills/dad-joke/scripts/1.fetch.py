"""Fetch a random dad joke from icanhazdadjoke.com as structured JSON.

ctx.user_query is an optional search term: empty fetches a fully random
joke from the root endpoint; non-empty searches and picks one matching
joke at random. A term that matches nothing is a hard error (exit 1) so
the chain stops with a clear message instead of reporting an empty joke.
Writes the chosen joke to /out/joke.json and returns it as the phase
result for the report phase.
"""

import json
import pathlib
import random
import sys
import urllib.error
import urllib.parse
import urllib.request

# icanhazdadjoke asks callers to send a descriptive User-Agent.
HEADERS = {
    "Accept": "application/json",
    "User-Agent": "zipsa dad-joke skill (https://github.com/westbrookai/zipsa)",
}


def fetch(url: str) -> dict:
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"icanhazdadjoke fetch failed: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    data = json.loads(sys.stdin.read())
    ctx = data["ctx"]
    term = ctx["user_query"].strip()

    if term:
        query = urllib.parse.urlencode({"term": term, "limit": 30})
        payload = fetch(f"https://icanhazdadjoke.com/search?{query}")
        results = payload.get("results") or []
        if not results:
            print(f"no dad jokes found for '{term}'", file=sys.stderr)
            sys.exit(1)
        chosen = random.choice(results)
    else:
        chosen = fetch("https://icanhazdadjoke.com/")

    joke = chosen.get("joke")
    if not joke:
        print(f"unexpected icanhazdadjoke response shape: {chosen}", file=sys.stderr)
        sys.exit(1)

    result = {"joke": joke, "id": chosen.get("id", ""), "search_term": term}

    artifact = pathlib.Path(ctx["out_dir"], "joke.json")
    artifact.write_text(json.dumps(result, ensure_ascii=False))

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
