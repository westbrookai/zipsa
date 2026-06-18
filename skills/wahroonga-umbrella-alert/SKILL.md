---
name: wahroonga-umbrella-alert
description: Check Wahroonga weather for the AM 8–noon commute window and send a Korean Telegram umbrella alert when rain probability is ≥ 30%. Use to warn the family to take an umbrella; stays silent on clear days.
---

# wahroonga-umbrella-alert

Checks Wahroonga weather for the AM 8–noon commute window and sends a Korean
Telegram umbrella alert if precipitation probability is ≥ 30%. If rain is
unlikely, nothing is sent — no noise on clear days.

**Run (on-demand, no schedule baked in):**
```bash
zipsa exec ./wahroonga-umbrella-alert \
  --mount ~/.zipsa/credentials/telegram.json:/mnt/creds/telegram.json
```

Credentials file at `~/.zipsa/credentials/telegram.json` must contain:
```json
{"bot_token": "...", "chat_id": "..."}
```
