"""Fetch Wahroonga weather from wttr.in and extract AM 8-noon rain forecast."""

import json
import sys
import urllib.error
import urllib.parse
import urllib.request

LOCATION = "Wahroonga"
# wttr.in hourly slots covering 8am-noon (slot = hour * 100 as string)
MORNING_SLOTS = {"600", "900", "1200"}


def main() -> None:
    json.loads(sys.stdin.read())  # consume stdin per contract

    url = f"https://wttr.in/{urllib.parse.quote(LOCATION)}?format=j1"
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.load(resp)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"wttr.in fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        today = payload["weather"][0]
        morning = [h for h in today["hourly"] if h["time"] in MORNING_SLOTS]
        if not morning:
            print("no morning hourly slots in wttr.in response", file=sys.stderr)
            sys.exit(1)

        max_rain_pct = max(int(h.get("chanceofrain", 0)) for h in morning)
        total_precip_mm = sum(float(h.get("precipMM", 0)) for h in morning)
        # 9am slot as representative for commute conditions
        rep = next((h for h in morning if h["time"] == "900"), morning[-1])

        result = {
            "location": LOCATION,
            "date": today["date"],
            "morning_rain_pct": max_rain_pct,
            "morning_precip_mm": round(total_precip_mm, 1),
            "temp_c": int(rep["tempC"]),
            "feels_like_c": int(rep["FeelsLikeC"]),
            "condition": rep["weatherDesc"][0]["value"],
            "should_notify": max_rain_pct >= 30,
        }
    except (KeyError, IndexError, ValueError) as e:
        print(f"unexpected wttr.in response shape: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Wahroonga {today['date']}: rain {max_rain_pct}%, precip {round(total_precip_mm,1)}mm")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
