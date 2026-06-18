"""Send Korean Telegram umbrella alert if AM rain probability >= 30%."""

import json
import sys
import urllib.error
import urllib.request

CREDS_PATH = "/mnt/creds/telegram.json"


def send_telegram(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    body = json.dumps({"chat_id": chat_id, "text": text, "parse_mode": "HTML"}).encode()
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.load(resp)
        if not result.get("ok"):
            print(f"Telegram API error: {result}", file=sys.stderr)
            sys.exit(1)
    except (urllib.error.URLError, TimeoutError) as e:
        print(f"Telegram request failed: {e}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    data = json.loads(sys.stdin.read())
    prev = data["prev"]

    rain_pct = prev.get("morning_rain_pct", 0)

    if not prev.get("should_notify"):
        print(f"강수확률 {rain_pct}% — 우산 불필요, 알림 없음")
        print(json.dumps({"sent": False, "rain_pct": rain_pct}))
        return

    try:
        creds = json.loads(open(CREDS_PATH).read())
        bot_token = creds["bot_token"]
        chat_id = str(creds["chat_id"])
    except FileNotFoundError:
        print(f"credentials file not found: {CREDS_PATH}", file=sys.stderr)
        sys.exit(1)
    except (KeyError, json.JSONDecodeError) as e:
        print(f"credentials parse error: {e}", file=sys.stderr)
        sys.exit(1)

    location = prev.get("location", "Wahroonga")
    precip_mm = prev.get("morning_precip_mm", 0)
    temp_c = prev.get("temp_c", "?")
    feels_like_c = prev.get("feels_like_c", "?")
    condition = prev.get("condition", "")

    message = (
        f"☔ <b>{location} 우산 알림</b>\n\n"
        f"오전 출근 시간대 비 예보가 있습니다.\n\n"
        f"🌧 날씨: {condition}\n"
        f"🌡 기온: {temp_c}°C (체감 {feels_like_c}°C)\n"
        f"💧 강수확률: {rain_pct}%\n"
        f"🌂 예상 강수량: {precip_mm}mm\n\n"
        f"우산을 꼭 챙기세요! ☂️"
    )

    send_telegram(bot_token, chat_id, message)
    print(f"텔레그램 알림 전송 완료 (강수확률 {rain_pct}%)")
    print(json.dumps({"sent": True, "rain_pct": rain_pct, "precip_mm": precip_mm}))


if __name__ == "__main__":
    main()
