# Two-Phase Fail (resume integration fixture)

## Phase: succeed

Return this exact JSON envelope. Do not call any tool first.

```json
{
  "status": "ok",
  "phase": "succeed",
  "result": {"phase": 1},
  "state_updates": null,
  "next_phase_input": {"phase1_done": true, "marker": "abc"},
  "user_facing_summary": "phase 1 done",
  "error": null
}
```

## Phase: fail

Return this exact JSON envelope. Do not call any tool first.

```json
{
  "status": "failed",
  "phase": "fail",
  "result": null,
  "state_updates": null,
  "next_phase_input": null,
  "user_facing_summary": "phase 2 intentional failure",
  "error": {"code": "test_failure", "message": "intentional"}
}
```
