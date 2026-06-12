# weather

Report current weather for a location. Phase 1 (Python) fetches
structured data from wttr.in and leaves a `weather.json` artifact;
phase 2 (LLM) writes a short natural-language report in the user's
language.

Run it: `zipsa exec skills/weather "서울"` (empty query → IP-based
location).
