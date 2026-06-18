---
name: weather
description: Report current weather for a location as a short natural-language summary. Use when the user asks about the weather, temperature, or conditions for a place (or their current location).
---

# weather

Report current weather for a location. Phase 1 fetches structured data
from wttr.in and leaves a `weather.json` artifact; phase 2 turns it into a
short natural-language report in the user's language.

Run it: `zipsa run weather "서울"` (empty query → IP-based location), or
deterministically with `zipsa exec skills/weather "서울"`.
