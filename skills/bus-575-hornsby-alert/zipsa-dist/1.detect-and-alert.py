# /// script
# dependencies = ["gtfs-realtime-bindings", "requests"]
# ///
"""Poll TfNSW GTFS-RT trip updates for Route 575 departure from Hornsby, then send Telegram."""

import json
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from google.transit import gtfs_realtime_pb2

# TfNSW GTFS-RT trip updates; route 575 has route_id suffix "_575" (e.g. "2514_575")
TU_URL = "https://api.transport.nsw.gov.au/v1/gtfs/realtime/buses"
ROUTE_SUFFIX = "_575"
POLL_INTERVAL_S = 30
CUTOFF_HOUR = 8  # stop polling at 08:00 local time


def load_creds():
    missing = [
        p for p in ("/mnt/creds/tfnsw.json", "/mnt/creds/telegram.json")
        if not Path(p).exists()
    ]
    if missing:
        for p in missing:
            print(f"ERROR: missing credential file: {p}", file=sys.stderr)
        sys.exit(1)

    tfnsw = json.loads(Path("/mnt/creds/tfnsw.json").read_text())
    tg = json.loads(Path("/mnt/creds/telegram.json").read_text())

    for key in ("api_key",):
        if key not in tfnsw:
            print(f"ERROR: tfnsw.json missing key '{key}'", file=sys.stderr)
            sys.exit(1)
    for key in ("bot_token", "chat_id"):
        if key not in tg:
            print(f"ERROR: telegram.json missing key '{key}'", file=sys.stderr)
            sys.exit(1)

    return tfnsw["api_key"], tg["bot_token"], tg["chat_id"]


def poll_departure(api_key, seen_at_hornsby):
    """
    Fetch trip updates and check if a tracked 575 trip has left Hornsby (stop seq 1).

    seen_at_hornsby: set of trip_ids confirmed to have been at seq=1 on a previous poll.
    Mutated in-place when seq=1 trips are discovered.

    Returns (departed: bool, dep_time_str: str | None).
    """
    resp = requests.get(
        TU_URL,
        headers={"Authorization": f"apikey {api_key}"},
        timeout=15,
    )
    resp.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)

    # Gather active 575 trips: trip_id → first remaining stop_sequence
    active = {}
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        if ROUTE_SUFFIX not in tu.trip.route_id:
            continue
        stops = list(tu.stop_time_update)
        if not stops:
            continue
        active[tu.trip.trip_id] = stops[0].stop_sequence

    # Mark any trip currently at seq=1 (Hornsby, route origin) as seen
    for trip_id, first_seq in active.items():
        if first_seq == 1:
            seen_at_hornsby.add(trip_id)

    # If a previously-seen trip is no longer at seq=1, it has departed Hornsby
    for trip_id in seen_at_hornsby:
        current_seq = active.get(trip_id)
        if current_seq is None or current_seq > 1:
            print(
                f"  → trip {trip_id} was at Hornsby (seq=1), now seq={current_seq} — departed!",
                file=sys.stderr,
            )
            return True, datetime.now().strftime("%H:%M")

    return False, None


def send_telegram(bot_token, chat_id, dep_time):
    msg = f"🚌 575번 Hornsby 출발! 약 7분 뒤 Wahroonga 도착 ({dep_time} 출발)"
    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        json={"chat_id": chat_id, "text": msg},
        timeout=10,
    )
    resp.raise_for_status()
    return msg


def main():
    _payload = json.loads(sys.stdin.read())

    now = datetime.now()

    # Weekend guard — exit cleanly without touching credentials
    if now.weekday() >= 5:
        print("Weekend — skipping.", file=sys.stderr)
        print(json.dumps({"status": "skipped", "reason": "weekend"}))
        return

    # Past-window guard
    cutoff = now.replace(hour=CUTOFF_HOUR, minute=0, second=0, microsecond=0)
    if now >= cutoff:
        print("Past 08:00 polling window — skipping.", file=sys.stderr)
        print(json.dumps({"status": "skipped", "reason": "past_window"}))
        return

    api_key, bot_token, chat_id = load_creds()

    seen_at_hornsby = set()
    polls = 0

    print(
        f"Polling TfNSW GTFS-RT for 575 departure from Hornsby until {cutoff.strftime('%H:%M')}…",
        file=sys.stderr,
    )

    while datetime.now() < cutoff:
        polls += 1
        try:
            departed, dep_time = poll_departure(api_key, seen_at_hornsby)
            print(
                f"[poll {polls}] tracked={len(seen_at_hornsby)} "
                f"active_trips_seen_at_hornsby={seen_at_hornsby}",
                file=sys.stderr,
            )
            if departed:
                msg = send_telegram(bot_token, chat_id, dep_time)
                print(f"[poll {polls}] Telegram sent: {msg}", file=sys.stderr)
                print(
                    json.dumps(
                        {"status": "sent", "departure_time": dep_time, "polls": polls}
                    )
                )
                return
            print(
                f"[poll {polls}] Not yet departed — sleeping {POLL_INTERVAL_S}s",
                file=sys.stderr,
            )
        except Exception as exc:
            print(f"[poll {polls}] Error: {exc}", file=sys.stderr)

        time.sleep(POLL_INTERVAL_S)

    print(
        f"Polling window ended at {CUTOFF_HOUR:02d}:00 after {polls} poll(s).",
        file=sys.stderr,
    )
    print(json.dumps({"status": "timeout", "polls": polls}))


main()
