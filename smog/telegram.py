"""Telegram channel posts (roadmap item 7, first step: the daily job posts, nobody talks to a bot). Standard library only.

smog.forecast calls build_message() + send() after the daily forecast. The bot token and the
channel come only from the environment (GitHub repository secrets), never from the code:

    TELEGRAM_BOT_TOKEN   from @BotFather
    TELEGRAM_CHANNEL     @channel_name or a numeric id; the bot must be an admin that may post

Without them nothing is sent (build_message still works, handy for previews).
One post = Russian text, a separator, Kyrgyz text. Plain text, no markup: nothing to escape.
The Kyrgyz text was written by a model, not a native speaker: have it read before the first real post.
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date

API = "https://api.telegram.org"
SEPARATOR = "\n\n———\n\n"

RISK = {"ru": {"low": "низкий", "medium": "средний", "high": "высокий"},
        "ky": {"low": "төмөн", "medium": "орточо", "high": "жогору"}}
CATEGORY = {
    "ru": {"good": "хорошо", "moderate": "умеренно", "unhealthy_sensitive": "вредно для чувствительных",
           "unhealthy": "вредно", "very_unhealthy": "очень вредно", "hazardous": "опасно"},
    "ky": {"good": "жакшы", "moderate": "орточо", "unhealthy_sensitive": "сезимтал адамдарга зыяндуу",
           "unhealthy": "зыяндуу", "very_unhealthy": "абдан зыяндуу", "hazardous": "кооптуу"},
}
# "When to ventilate": the typical winter day, not a forecast. City median PM2.5 by Bishkek hour over the
# four winters 2022/23..2025/26 (data/features.csv): ~17-18 at 05-08 h and ~21-22 at 12-15 h, but ~40-46 at
# 18-23 h, when 31-39% of the hours are unhealthy.
VENTILATE = {
    "ru": "Когда проветривать: в обычный зимний день воздух чище всего рано утром (05–08 ч) и днём "
          "(12–15 ч), грязнее всего вечером (18–23 ч).",
    "ky": "Желдетүү убактысы: кадимки кышкы күнү аба эң таза — таң эрте (05–08) жана күндүз (12–15), "
          "эң булганган — кечинде (18–23).",
}
TEXT = {
    "ru": {
        "title": "Прогноз смога в Бишкеке на {day}",
        "risk": "Риск: {risk}.",
        "mean": "Средний PM2.5 за день ≈ {mean} мкг/м³ ({category}).",
        "alert": "⚠️ Ожидается 3 часа и больше вредного воздуха: закройте окна вечером и ночью, "
                 "сократите прогулки с детьми.",
        "yesterday": "Вчера ({day}): {was}; на деле вредный воздух держался {hours} ч, средний PM2.5 {mean} мкг/м³.",
        "was_alert": "было предупреждение", "was_no_alert": "предупреждения не было",
        "note": "ℹ️ Данные народных датчиков sensor.community. Они обычно занижают PM2.5, реальный смог хуже.",
        "test": "ТЕСТ: это не сегодняшний прогноз, а расчёт для {day}.",
    },
    "ky": {
        "title": "{day} үчүн Бишкектеги смог болжолу",
        "risk": "Тобокел: {risk}.",
        "mean": "Күндүн орточо PM2.5 ≈ {mean} мкг/м³ ({category}).",
        "alert": "⚠️ 3 саат жана андан көп зыяндуу аба күтүлүүдө: кечинде жана түнкүсүн терезелерди жабыңыз, "
                 "балдар менен сейилдөөнү кыскартыңыз.",
        "yesterday": "Кечээ ({day}): {was}; чындыгында зыяндуу аба {hours} саат болду, орточо PM2.5 {mean} мкг/м³.",
        "was_alert": "эскертүү болгон", "was_no_alert": "эскертүү болгон эмес",
        "note": "ℹ️ sensor.community элдик сенсорлорунун маалыматы. Алар PM2.5'ти көбүнчө аз көрсөтөт, "
                "чындыгында аба булганыраак.",
        "test": "СЫНОО: бул бүгүнкү болжол эмес, {day} үчүн эсептөө.",
    },
}


class SendError(Exception):
    """The post was not delivered. The message never contains the bot token."""


def configured() -> bool:
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHANNEL"))


def _one_language(lang: str, target: date, p: dict, yesterday: dict | None) -> str:
    t = TEXT[lang]
    mean = int(float(p["mean_pm25"]))
    alert = int(float(p["alert"]))
    lines = [t["title"].format(day=target.strftime("%d.%m.%Y")),
             t["risk"].format(risk=RISK[lang][p["risk"]]),
             t["mean"].format(mean=mean, category=CATEGORY[lang][p["category"]])]
    if alert:
        lines.append(t["alert"])
    if alert or p["risk"] != "low":
        lines.append(VENTILATE[lang])
    if yesterday:
        was = t["was_alert"] if int(float(yesterday["alert"])) else t["was_no_alert"]
        day = date.fromisoformat(yesterday["target_date"]).strftime("%d.%m")
        lines.append(t["yesterday"].format(day=day, was=was, hours=yesterday["observed_unhealthy_h"],
                                           mean=yesterday["observed_mean"]))
    lines.append(t["note"])
    return "\n".join(lines)


def build_message(target: date, p: dict, yesterday: dict | None = None, test_day: date | None = None) -> str:
    """RU + KY post for `target`. p: risk, mean_pm25, category, alert (as in forecasts.csv).
    yesterday: the forecasts.csv row of the day that just ended, only if it was verified."""
    parts = [_one_language(lang, target, p, yesterday) for lang in ("ru", "ky")]
    text = SEPARATOR.join(parts)
    if test_day:
        day = test_day.strftime("%d.%m.%Y")
        text = "\n".join(TEXT[lang]["test"].format(day=day) for lang in ("ru", "ky")) + "\n\n" + text
    return text


def send(text: str, retries: int = 3) -> None:
    """Post to the channel. Retries network errors, 429 and 5xx; other errors are final (SendError)."""
    token, channel = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHANNEL", "")
    if not token or not channel:
        raise SendError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHANNEL are not set")
    data = urllib.parse.urlencode({"chat_id": channel, "text": text}).encode()
    url = f"{API}/bot{token}/sendMessage"
    last = ""
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as r:
                if json.load(r).get("ok"):
                    return
                last = "Telegram answered ok=false"
        except urllib.error.HTTPError as e:
            try:
                last = json.load(e).get("description", "")
            except (ValueError, OSError):
                last = ""
            last = f"HTTP {e.code}: {last}"
            if e.code != 429 and e.code < 500:
                break  # wrong token, bot not an admin of the channel, bad chat id: retrying cannot help
        except (OSError, ValueError) as e:
            last = f"{type(e).__name__}: {e}"
    raise SendError(last.replace(token, "***"))
