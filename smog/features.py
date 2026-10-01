"""Feature table for the city PM2.5 forecast at +24h and +48h. Standard library only.

One row per hour t (UTC) of each heating season that has a city value. Columns:

  Known when the forecast is issued at hour t (safe to use):
    pm_now, pm_lag{1,3,6,12,24,48}h   city median PM2.5 at t and before (raw SDS011, as in the baseline)
    pm_mean24h, pm_max24h             over the 24 hours ending at t
    n_sensors                         sensors in the city median at t (the median jumps when the set changes)
    now_<weather>                     ERA5 weather at t (ERA5 comes ~5 days late; the daily job
                                      will use the current weather analysis instead)
    hour_local, weekday_local, days_into_season   at t, Bishkek time (UTC+6)
    weekday_24, weekday_48            weekday of the target hour (the hour of day is the same as t)

  Weather at the target hour: OBSERVED (ERA5), i.e. a perfect weather forecast.
    era5_24_<weather>, era5_48_<weather>, cams_24, cams_48
  A real forecast only has a weather FORECAST for t+24/t+48. Archived day-ahead forecasts
  (Open-Meteo previous-runs API, checked 2026-10-01) have no boundary layer height at all
  and most other variables only from winter 2024/25, so a model trained with these columns
  shows the best case. Score models with and without them: real skill lies in between.

  Targets: y_24, y_48 = city median PM2.5 at t+24h, t+48h (empty if unknown).

Usage:
    py -m smog.features                 # config.SEASONS -> data/features.csv
    py -m smog.features --season 2022
"""
import argparse
import csv
import math
import statistics
from datetime import datetime, timedelta

from . import config
from .backfill import write_csv
from .evaluate_cams import hourly_city_pm25, load_cams, sensor_hourly_pm25

LAGS_H = [1, 3, 6, 12, 24, 48]
HORIZONS_H = [24, 48]
LOCAL = timedelta(hours=6)  # Asia/Bishkek, no daylight saving


def load_weather(start, end) -> dict[datetime, dict[str, float]]:
    """ERA5 hourly weather, wind direction turned into sin/cos (359° and 1° are neighbours)."""
    out = {}
    with (config.RAW_DIR / f"weather_{start}_{end}.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            row = {}
            for var in config.WEATHER_VARS:
                if r.get(var) in (None, ""):
                    continue
                if var == "wind_direction_10m":
                    rad = math.radians(float(r[var]))
                    row["wind_dir_sin"], row["wind_dir_cos"] = round(math.sin(rad), 3), round(math.cos(rad), 3)
                else:
                    row[var] = float(r[var])
            out[datetime.fromisoformat(r["timestamp"])] = row
    return out


WEATHER_COLS = [v for v in config.WEATHER_VARS if v != "wind_direction_10m"] + ["wind_dir_sin", "wind_dir_cos"]


def season_rows(year: int) -> list[dict]:
    start, end = config.season_range(year)
    city = {datetime.fromisoformat(h): v for h, v in hourly_city_pm25(start, end).items()}
    per_sensor, _ = sensor_hourly_pm25(start, end)
    n_sensors = {}
    for series in per_sensor.values():
        for h in series:
            n_sensors[datetime.fromisoformat(h)] = n_sensors.get(datetime.fromisoformat(h), 0) + 1
    weather = load_weather(start, end)
    cams = {datetime.fromisoformat(h): v for h, v in load_cams(start, end).items()}
    season_start = datetime(start.year, start.month, start.day)

    rows = []
    for t in sorted(city):
        local = t + LOCAL
        row = {"timestamp": t.isoformat(), "season": year, "pm_now": round(city[t], 1)}
        for lag in LAGS_H:
            v = city.get(t - timedelta(hours=lag))
            row[f"pm_lag{lag}h"] = "" if v is None else round(v, 1)
        window = [city[h] for h in (t - timedelta(hours=k) for k in range(24)) if h in city]
        row["pm_mean24h"] = round(statistics.mean(window), 1)
        row["pm_max24h"] = round(max(window), 1)
        row["n_sensors"] = n_sensors.get(t, 0)
        for col in WEATHER_COLS:
            row[f"now_{col}"] = weather.get(t, {}).get(col, "")
        row["hour_local"] = local.hour
        row["weekday_local"] = local.weekday()
        row["days_into_season"] = (t - season_start).days
        for hz in HORIZONS_H:
            target = t + timedelta(hours=hz)
            row[f"weekday_{hz}"] = (target + LOCAL).weekday()
            for col in WEATHER_COLS:
                row[f"era5_{hz}_{col}"] = weather.get(target, {}).get(col, "")
            row[f"cams_{hz}"] = cams.get(target, "")
            y = city.get(target)
            row[f"y_{hz}"] = "" if y is None else round(y, 1)
        rows.append(row)
    return rows


def summarize(rows: list[dict]) -> None:
    """Sanity checks: coverage, how each column relates to y_24, and the persistence bar to beat."""
    print(f"\nRows: {len(rows)}; with y_24: {sum(r['y_24'] != '' for r in rows)}; with y_48: {sum(r['y_48'] != '' for r in rows)}")
    with_y = [r for r in rows if r["y_24"] != ""]
    print("Correlation with y_24 (rows where both are known):")
    for col in rows[0]:
        if col in ("timestamp", "season", "y_24", "y_48"):
            continue
        pairs = [(float(r[col]), float(r["y_24"])) for r in with_y if r[col] != ""]
        if len(pairs) > 100 and len({x for x, _ in pairs}) > 1:
            r = statistics.correlation([x for x, _ in pairs], [y for _, y in pairs])
            print(f"  {col:32s} r={r:+.2f}  ({len(pairs)} rows)")

    # "Tomorrow at this hour = now": the simplest forecast. A model that cannot beat it is useless.
    print("Persistence (y_h = pm_now) vs CAMS at the target hour, same rows:")
    for hz in HORIZONS_H:
        both = [r for r in rows if r[f"y_{hz}"] != "" and r[f"cams_{hz}"] != ""]
        for name, pred in [("persistence", lambda r: r["pm_now"]), ("CAMS", lambda r: r[f"cams_{hz}"])]:
            err = [float(pred(r)) - float(r[f"y_{hz}"]) for r in both]
            bad = [r for r in both if float(r[f"y_{hz}"]) >= 55.4]
            caught = sum(float(pred(r)) >= 55.4 for r in bad)
            print(f"  +{hz}h {name:12s} MAE={statistics.mean(abs(e) for e in err):5.1f} "
                  f"bias={statistics.mean(err):+5.1f} unhealthy caught={caught}/{len(bad)}  ({len(both)} rows)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--season", type=int, action="append", help="start year, repeatable; default config.SEASONS")
    args = p.parse_args()
    rows = []
    for year in args.season or config.SEASONS:
        season = season_rows(year)
        print(f"Season {year}/{str(year + 1)[2:]}: {len(season)} rows")
        rows += season
    path = config.ROOT / "data" / "features.csv"
    write_csv(path, rows)
    print(f"Saved {path}")
    summarize(rows)


if __name__ == "__main__":
    main()
