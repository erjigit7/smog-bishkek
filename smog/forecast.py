"""Daily morning forecast for tomorrow (roadmap item 6). Run by GitHub Actions every day.

Runs at ~03:45 UTC = 09:45 Bishkek: sensor.community publishes yesterday's archive files at
~03:30 UTC, and the model only uses data up to 23:00 UTC yesterday (05:00 Bishkek), exactly
as in its evaluation (smog.alerts).

1. Fill in what actually happened for earlier forecasts whose day is over, so the real accuracy
   of the live forecasts can be checked (forecasts/forecasts.csv, committed by the job).
2. Build the model inputs from the archive + Open-Meteo, the same way smog.features did.
3. Predict tomorrow: unhealthy-day alert, risk level, tomorrow's mean PM2.5; append a row.

Usage:
    py -m smog.forecast                               # does nothing outside the heating season
    py -m smog.forecast --force                       # also outside it (model trained on Nov-Feb only)
    py -m smog.forecast --date 2026-01-15 --dry-run   # as if run that morning; prints, saves nothing
"""
import argparse
import csv
import json
import math
import pickle
import statistics
import time
from datetime import date, datetime, timedelta, timezone

from . import config, sources
from .alerts import ALERT_HOURS, DATA_CUTOFF_UTC_HOUR, MIN_HOURS, MODEL_PATH, RISK_LEVELS, UNHEALTHY
from .evaluate_cams import city_median, per_sensor_hourly

LOCAL = timedelta(hours=6)
OUT = config.ROOT / "forecasts" / "forecasts.csv"
COLUMNS = ["issued_at_utc", "run_date", "target_date", "data_until_utc", "status", "p_raw", "p_calibrated",
           "risk", "alert", "mean_pm25", "category", "pm_at_cutoff", "n_sensors", "model_trained_on",
           "observed_unhealthy_h", "observed_mean", "observed_unhealthy_day", "note"]
RISK_RU = {"low": "низкий", "medium": "средний", "high": "высокий"}
CATEGORY_RU = {"good": "хорошо", "moderate": "умеренно", "unhealthy_sensitive": "вредно для чувствительных",
               "unhealthy": "вредно", "very_unhealthy": "очень вредно", "hazardous": "опасно"}


def in_season(d: date) -> bool:
    return d.month in (11, 12, 1, 2)


def season_start(d: date) -> datetime:
    return datetime(d.year if d.month >= 11 else d.year - 1, 11, 1)


def city_hours(first: date, last: date) -> tuple[dict[datetime, float], dict[datetime, int]]:
    """City median PM2.5 per UTC hour from the archive, and how many sensors made each hour."""
    sensors = dict(config.KNOWN_SENSORS)
    try:
        sensors.update({sid: m["type"] for sid, m in sources.live_sensors().items()})
    except OSError as e:
        print(f"Live API unavailable ({e}); using KNOWN_SENSORS only.")
    rows = []
    d = first
    while d <= last:
        for sid, stype in sensors.items():
            rows += [{"sensor_id": sid, "sensor_type": stype, **r} for r in sources.sensor_day(sid, stype, d)]
            time.sleep(0.2)  # be polite to a volunteer-run archive
        d += timedelta(days=1)
    per_sensor, types = per_sensor_hourly(rows)
    city = {datetime.fromisoformat(h): v for h, v in city_median(per_sensor, types).items()}
    counts = {}
    for hours in per_sensor.values():
        for h in hours:
            counts[datetime.fromisoformat(h)] = counts.get(datetime.fromisoformat(h), 0) + 1
    return city, counts


def weather_at(t: datetime, today: date) -> dict[str, float]:
    """Weather at hour t: Open-Meteo forecast-model analysis for recent hours (what the live job
    can get), ERA5 for older dates (what the model was trained on)."""
    if (today - t.date()).days <= 5:
        rows = sources.weather_recent(past_days=3)
    else:
        rows = sources.weather_history(t.date(), t.date())
    row = next((r for r in rows if r["timestamp"] == t.isoformat()), {})
    out = {}
    for var in config.WEATHER_VARS:
        if row.get(var) is None:
            continue
        if var == "wind_direction_10m":
            rad = math.radians(row[var])
            out["wind_dir_sin"], out["wind_dir_cos"] = round(math.sin(rad), 3), round(math.cos(rad), 3)
        else:
            out[var] = float(row[var])
    return out


def build_inputs(run_date: date, today: date) -> tuple[dict, datetime]:
    """The model inputs for a forecast made on run_date, mirroring smog.features + smog.alerts."""
    cutoff = datetime(run_date.year, run_date.month, run_date.day) - timedelta(hours=24 - DATA_CUTOFF_UTC_HOUR)
    # pm_lag48h and the 24 h window need ~3 UTC days before the cutoff.
    city, counts = city_hours(cutoff.date() - timedelta(days=2), cutoff.date())
    x = {"pm_now": city.get(cutoff)}
    for lag in [1, 3, 6, 12, 24, 48]:
        x[f"pm_lag{lag}h"] = city.get(cutoff - timedelta(hours=lag))
    window = [city[h] for h in (cutoff - timedelta(hours=k) for k in range(24)) if h in city]
    x["pm_mean24h"] = statistics.mean(window) if window else None
    x["pm_max24h"] = max(window) if window else None
    x["n_sensors"] = counts.get(cutoff, 0)
    for col, v in weather_at(cutoff, today).items():
        x[f"now_{col}"] = v
    x["weekday_local"] = (cutoff + LOCAL).weekday()
    x["days_into_season"] = (cutoff - season_start(cutoff.date())).days
    x["weekday_tomorrow"] = (run_date + timedelta(days=1)).weekday()
    # features.csv stores PM rounded to 0.1; do the same so live inputs match training exactly.
    for k in list(x):
        if k.startswith("pm_") and x[k] is not None:
            x[k] = round(x[k], 1)
    return x, cutoff


def risk_level(p: float) -> str:
    return next(name for limit, name in RISK_LEVELS if p < limit)


def predict(x: dict) -> dict:
    with MODEL_PATH.open("rb") as f:
        m = pickle.load(f)
    row = [[x.get(c) if x.get(c) is not None else math.nan for c in m["features"]]]
    p_raw = float(m["classifier"].predict_proba(row)[0, 1])
    p_cal = float(m["calibrator"].predict([p_raw])[0])
    alert = p_raw >= m["threshold"]
    risk = risk_level(p_cal)
    if alert and risk == "low":
        risk = "medium"  # never say "low risk" in the same message as a warning
    mean = max(0.0, float(m["regressor"].predict(row)[0]))
    return {"p_raw": round(p_raw, 3), "p_calibrated": round(p_cal, 2), "risk": risk, "alert": int(alert),
            "mean_pm25": round(mean), "category": config.pm25_category(mean)}


def observe(target: date) -> dict | None:
    """What happened on a Bishkek-time day: None if the archive does not cover it well enough yet."""
    first = datetime(target.year, target.month, target.day) - LOCAL  # 18:00 UTC the day before
    city, _ = city_hours(first.date(), first.date() + timedelta(days=1))
    values = [city[first + timedelta(hours=k)] for k in range(24) if first + timedelta(hours=k) in city]
    if len(values) < MIN_HOURS:
        return None
    hours = sum(v >= UNHEALTHY for v in values)
    return {"observed_unhealthy_h": hours, "observed_mean": round(statistics.mean(values)),
            "observed_unhealthy_day": int(hours >= ALERT_HOURS)}


def load_rows() -> list[dict]:
    if not OUT.exists():
        return []
    with OUT.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_rows(rows: list[dict]) -> None:
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in COLUMNS} for r in rows)


def message_ru(target: date, p: dict) -> str:
    lines = [f"Прогноз смога в Бишкеке на {target.strftime('%d.%m.%Y')}: риск {RISK_RU[p['risk']]}.",
             f"Средний PM2.5 за день ≈ {p['mean_pm25']} мкг/м³ ({CATEGORY_RU[p['category']]})."]
    if p["alert"]:
        lines.append("⚠️ Ожидается 3 и больше часов вредного воздуха: закройте окна вечером и ночью, "
                     "сократите прогулки с детьми.")
    return "\n".join(lines)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--date", type=date.fromisoformat, help="run as if on this UTC date")
    p.add_argument("--force", action="store_true", help="forecast also outside the heating season")
    p.add_argument("--dry-run", action="store_true", help="print only, do not touch forecasts.csv")
    args = p.parse_args()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    today = now.date()
    run_date = args.date or today
    target = run_date + timedelta(days=1)  # Bishkek-time day after the run day
    rows = [] if args.dry_run else load_rows()

    # 1. Verify earlier forecasts whose day is over and fully published (day ends 18:00 UTC).
    for r in rows:
        t = date.fromisoformat(r["target_date"])
        if r["status"] == "ok" and r["observed_mean"] == "" and t < today:
            obs = observe(t)
            if obs:
                r.update(obs)
                print(f"Verified {t}: forecast risk {r['risk']}, alert {r['alert']}; observed "
                      f"{obs['observed_unhealthy_h']} unhealthy h, mean {obs['observed_mean']}")

    # 2-3. Tomorrow's forecast.
    if any(r["target_date"] == target.isoformat() for r in rows):
        print(f"A forecast for {target} already exists; not adding another.")
    elif not in_season(target) and not args.force:
        print(f"{target} is outside the heating season (Nov-Feb) the model was trained on; no forecast.")
    else:
        with MODEL_PATH.with_suffix(".json").open(encoding="utf-8") as f:
            trained_on = "+".join(str(s) for s in json.load(f)["trained_on_seasons"])
        x, cutoff = build_inputs(run_date, today)
        row = {"issued_at_utc": now.isoformat(timespec="minutes"), "run_date": run_date.isoformat(),
               "target_date": target.isoformat(), "data_until_utc": cutoff.isoformat(),
               "pm_at_cutoff": x["pm_now"] if x["pm_now"] is not None else "", "n_sensors": x["n_sensors"],
               "model_trained_on": trained_on, "note": "" if in_season(target) else "outside heating season"}
        if x["pm_now"] is None:
            # The model never saw a day without a city value at the cutoff: do not guess.
            row["status"] = "no forecast: fewer than 2 sensors at the data cutoff"
            print(row["status"])
        else:
            row.update(predict(x), status="ok")
            print(message_ru(target, row))
        print("Inputs:", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in x.items()})
        rows.append(row)

    if not args.dry_run:
        save_rows(rows)
        print(f"Saved {OUT} ({len(rows)} rows)")


if __name__ == "__main__":
    main()
