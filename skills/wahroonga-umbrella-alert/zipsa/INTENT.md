# Intent — wahroonga-umbrella-alert

Why: warn the family to take an umbrella for the AM 8–noon Wahroonga
commute without anyone manually checking the forecast — and stay silent
on clear days so the alert keeps meaning something.

What the user wanted: check Wahroonga's morning rain forecast; if
precipitation probability is ≥ 30% anywhere in the 8am–noon window, send
a Korean Telegram alert; otherwise send nothing.

Boundary: morning commute window only; Telegram delivery only; current
forecast, no multi-day outlook. Missing the Telegram credential file →
exit 1.

## Prerequisites

| What | Host path | Container mount |
|---|---|---|
| Telegram bot | `~/.zipsa/credentials/telegram.json` | `/mnt/creds/telegram.json` |

JSON shape: `telegram.json` → `{"bot_token": "...", "chat_id": "..."}`
