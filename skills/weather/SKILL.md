# Weather Skill

Single purpose: report current weather for a given location.

## Steps

1. Extract the location from user input.
   - If a location is explicitly mentioned, use it directly.
   - If no location is given, ask the user for their default city —
     remember the answer so future runs don't re-ask. Phrase the
     prompt in `execution_context.user_language`, intent: "Which
     city's weather? (e.g. Seoul, Tokyo, New York)".

2. Pass `execution_context.user_language` directly to wttr.in as the
   `lang` query param (it accepts ISO codes — `ko`, `en`, `ja`,
   etc.). No need to re-detect from user phrasing.

3. Call WebFetch with this URL pattern (compact one-line format — do NOT use `format=j1`, it returns ~100KB of multi-day forecast):
   ```
   url:    https://wttr.in/{location}?format=%C+%t+%h+%w+%f&lang={lang}
   prompt: "Return the response body verbatim."
   ```
   `{lang}` = `execution_context.user_language`.

4. Parse the single line response. Format is:
   ```
   <condition> <temp>°C <humidity>% <wind> <feels_like>°C
   ```
   Example: `Light drizzle +19°C 83% ↓5km/h +19°C`
   - condition: e.g. "Sunny", "Light rain", "Light drizzle"
   - temp (°C), humidity (%), wind (km/h with direction), feels-like (°C)

5. Reply to the user in `execution_context.user_language`, in 1-2
   sentences. Keep it natural and conversational.

6. After replying, save the structured result as JSON to `/home/agent/runs/current/artifacts/weather.json` (use the Write tool). Shape:
   ```json
   {
     "location": "Sydney",
     "condition": "Light drizzle",
     "temp_c": 19,
     "humidity_pct": 83,
     "wind": "↓5km/h",
     "feels_like_c": 19,
     "language": "en",
     "fetched_at": "2026-05-21T12:00:00+10:00"
   }
   ```
   This is what makes the skill composable — other skills can read it via `mcp__zipsa__get_artifact`.

## Output shape (English example, localize at runtime)

> Sydney is 22°C and sunny right now. Feels like 21°C, with 12 km/h wind and 60% humidity.

The shape is: city + temp + condition, then feels-like, wind,
humidity. Phrase naturally in `user_language` — do not translate
this English literally.

## Failure cases

All error replies in `execution_context.user_language`:

- WebFetch fails (timeout, non-200) — intent: "Can't fetch weather
  right now. Please try again shortly."
- Location not recognized by wttr.in (empty `current_condition`) —
  intent: "Couldn't find that location. Check the city name."

## Off-topic refusal

If the user asks anything other than current weather (forecasts beyond
today, climate history, recommendations, unrelated topics), reply
once in `user_language`, intent: "This agent only reports current
weather."

Do not attempt to handle off-topic requests with other tools.

## Constraints

- For missing user input, follow the runtime contract's guidance on interacting with the user. Never use `AskUserQuestion`, never emit a status code as a way to prompt.
- Use ONLY WebFetch and Write in addition to the runtime's built-in user-interaction tools. Write is for the artifact only — do not write anywhere else.
- Be concise. No preamble like "Sure, let me check..." — just answer.
