# bus-575-hornsby-alert

Monitor TfNSW GTFS-Realtime for Route 575 (CDC NSW, southbound, Hornsby → Wahroonga → Turramurra) actual departure from Hornsby on weekday mornings, then send a Korean Telegram alert to the family. Scheduling is external — this skill runs once and exits.

Before calling the script, call `mcp__zipsa__report` with "575번 버스 모니터링 시작 (Hornsby 출발 대기 중)…". Then run `1.detect-and-alert.py` with no arguments. When it returns, call `mcp__zipsa__report` with the outcome: `status=sent` → "알림 전송 완료!", `status=skipped` → "조건 미충족 — 건너뜀", `status=timeout` → "시간 초과 (08:00) — 감지 실패".

## Run example

```bash
zipsa run . \
  --mount ~/.zipsa/credentials/tfnsw.json:/mnt/creds/tfnsw.json \
  --mount ~/.zipsa/credentials/telegram.json:/mnt/creds/telegram.json
```

Credential file shapes:
- `tfnsw.json` → `{"api_key": "..."}`
- `telegram.json` → `{"bot_token": "...", "chat_id": "..."}`
