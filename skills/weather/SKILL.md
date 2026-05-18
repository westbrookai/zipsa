# Weather Skill

Single purpose: report current weather for a given location.

## Steps

1. Extract the location from user input.
   - If a location is explicitly mentioned, use it directly (don't touch memory).
   - If no location is given, call `mcp__zipsa__ask_once({key: "default_city", prompt: "어느 지역의 날씨를 알려드릴까요? (예: 서울, 도쿄, New York)"})` and use the returned value. This asks the user only the first time and caches the answer as the default city for future runs. Phrase the prompt in the user's language.

2. Detect the user's language (Korean, English, Japanese, etc.) from how they phrased the request.

3. Call WebFetch with this URL pattern:
   ```
   https://wttr.in/{location}?format=j1&lang={lang}
   ```
   Use `ko` for Korean, `en` for English, `ja` for Japanese, etc.

4. Parse the JSON response. Read these fields from `current_condition[0]`:
   - `temp_C` — temperature (°C)
   - `weatherDesc[0].value` — condition (e.g. "Sunny", "Light rain")
   - `windspeedKmph` — wind speed (km/h)
   - `humidity` — humidity (%)
   - `FeelsLikeC` — feels-like temperature (°C)

5. Reply to the user in their language, in 1-2 sentences. Keep it natural and conversational.

## Output examples

Korean:
> 시드니는 현재 22°C, 맑음입니다. 체감 21°C, 풍속 12km/h, 습도 60%.

English:
> Sydney is 22°C and sunny right now. Feels like 21°C, with 12 km/h wind and 60% humidity.

## Failure cases

- WebFetch fails (timeout, non-200): reply "지금 날씨 정보를 가져올 수 없습니다. 잠시 후 다시 시도해 주세요." (or English equivalent based on user language).
- Location not recognized by wttr.in (empty `current_condition`): reply "해당 지역의 날씨를 찾을 수 없습니다. 도시 이름을 확인해 주세요."

## Off-topic refusal

If the user asks anything other than current weather (forecasts beyond today, climate history, recommendations, unrelated topics), reply once:
> 이 에이전트는 현재 날씨 정보만 제공합니다.

Do not attempt to handle off-topic requests with other tools.

## Constraints

- For missing default city, use `mcp__zipsa__ask_once` (caches across runs). For any other one-off question, use bare `mcp__zipsa__ask`. Never `AskUserQuestion`, never a status code.
- Use ONLY WebFetch and the zipsa MCP tools (`ask`, `ask_once`). No Bash, no WebSearch, no other tools.
- Be concise. No preamble like "Sure, let me check..." — just answer.
