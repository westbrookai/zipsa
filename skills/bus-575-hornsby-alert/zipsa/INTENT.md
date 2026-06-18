# Intent

Alert a Korean-speaking family when the weekday 07:45 Route 575 (CDC NSW, southbound) **actually departs** Hornsby station, so they know to leave for Wahroonga — the next boarding stop, roughly 7 minutes downstream.

## Why

Route 575 frequently runs late. The family needs the real departure time, not the schedule.

## Acceptance criteria

- Runs on weekdays only; exits cleanly (code 0, `status: "skipped"`) on weekends.
- Polls TfNSW GTFS-Realtime bus vehicle-positions every 30 s from invocation until 08:00.
- Detects departure when a vehicle whose `route_id` or `trip_id` contains "575" advances past stop sequence 1 (Hornsby, the route origin).
- On detection, sends a concise Korean Telegram message and exits immediately:
  `🚌 575번 Hornsby 출발! 약 7분 뒤 Wahroonga 도착 (HH:MM 출발)`
- Departure time in the message is derived from the vehicle's GTFS-RT timestamp.
- If invoked after 08:00, exits cleanly without polling.
- Missing credential file → stderr message + exit 1.

## Out of scope

- Scheduling (handled externally via `zipsa schedule`, fired at 07:40 weekdays).
- Northbound 575 or trips not originating at Hornsby.
- Multiple routes or stops.

## Route details

| Field | Value |
|---|---|
| Route | 575, CDC NSW |
| Direction | SOUTHBOUND — Hornsby → Turramurra → Macquarie University |
| Origin (stop 1) | Hornsby |
| Family boarding | Wahroonga (~7 min from Hornsby) |
| Family alighting | Turramurra |
| Target trip | ~07:45 weekday morning |

## Prerequisites

| What | Host path | Container mount |
|---|---|---|
| TfNSW Open Data API key | `~/.zipsa/credentials/tfnsw.json` | `/mnt/creds/tfnsw.json` |
| Telegram bot | `~/.zipsa/credentials/telegram.json` | `/mnt/creds/telegram.json` |

JSON shapes:
- `tfnsw.json` → `{"api_key": "..."}`
- `telegram.json` → `{"bot_token": "...", "chat_id": "..."}`
