"""Fetch current weather from wttr.in as structured JSON.

City comes from ctx.user_query; empty query lets wttr.in geolocate by
IP. Writes the full structured result to /out/weather.json so other
skills (or the user) can pick it up, and returns the same dict as the
phase result for the report phase.
"""

import json
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request


def main() -> None:
    data = json.loads(sys.stdin.read())
    ctx = data["ctx"]
    city = ctx["user_query"].strip()

    location = urllib.parse.quote(city) if city else ""
    url = f"https://wttr.in/{location}?format=j1"
    request = urllib.request.Request(url, headers={"User-Agent": "curl/8"})

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"wttr.in fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        current = payload["current_condition"][0]
        area = payload["nearest_area"][0]
        result = {
            "location": area["areaName"][0]["value"],
            "country": area["country"][0]["value"],
            "condition": current["weatherDesc"][0]["value"],
            "temp_c": int(current["temp_C"]),
            "feels_like_c": int(current["FeelsLikeC"]),
            "humidity_pct": int(current["humidity"]),
            "wind_kmph": int(current["windspeedKmph"]),
            "observed_at_utc": current["observation_time"],
            "queried_city": city,
        }
    except (KeyError, IndexError, ValueError) as e:
        print(f"unexpected wttr.in response shape: {e}", file=sys.stderr)
        sys.exit(1)

    artifact = pathlib.Path(ctx["out_dir"], "weather.json")
    artifact.write_text(json.dumps(result, ensure_ascii=False))

    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
