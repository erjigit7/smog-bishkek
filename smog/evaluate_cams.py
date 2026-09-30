"""Baseline: how well does the global CAMS model match real Bishkek sensors?

This is the number our own forecast has to beat.

Usage:
    py -m smog.evaluate_cams --start 2025-11-01 --end 2026-02-28
    py -m smog.evaluate_cams --all-seasons   # plus a Markdown table of all seasons

Each range is scored twice: on raw sensor readings and on humidity-corrected ones
(see smog/humidity.py). Needs weather and CAMS files from smog.backfill for the same range.
"""
import argparse
import csv
import statistics
from collections import defaultdict
from datetime import date

from . import config, humidity
from .backfill import add_range_args, daterange, ranges_from_args


def hourly_city_pm25(start: date, end: date, rh: dict[str, float] | None = None) -> dict[str, float]:
    """Median across sensors of each sensor's hourly-mean PM2.5. Key: 'YYYY-MM-DDTHH:00:00'.

    With rh (hour -> relative humidity %), SDS011 hourly means are humidity-corrected.
    """
    per_sensor_hour = defaultdict(list)
    sensor_types = {}
    for day in daterange(start, end):
        path = config.RAW_DIR / "sensors" / f"{day}.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                pm25 = float(r["pm25"])
                if not 0 <= pm25 < 1000:  # drop obvious sensor faults
                    continue
                hour = r["timestamp"][:13] + ":00:00"
                per_sensor_hour[(r["sensor_id"], hour)].append(pm25)
                # Files from before the sensor_type column: all Bishkek sensors then were SDS011.
                sensor_types[r["sensor_id"]] = r.get("sensor_type") or config.KNOWN_SENSORS.get(
                    int(r["sensor_id"]), "SDS011")

    by_hour = defaultdict(list)
    for (sid, hour), values in per_sensor_hour.items():
        value = statistics.mean(values)
        if rh is not None and sensor_types[sid] == "SDS011":
            value = humidity.correct_pm(value, rh.get(hour))
        by_hour[hour].append(value)
    # Median is robust to one broken or badly placed sensor.
    return {h: statistics.median(v) for h, v in by_hour.items() if len(v) >= 2}


def load_cams(start: date, end: date) -> dict[str, float]:
    path = config.RAW_DIR / f"cams_{start}_{end}.csv"
    with path.open(encoding="utf-8") as f:
        return {r["timestamp"]: float(r["pm2_5"]) for r in csv.DictReader(f) if r["pm2_5"]}


def load_humidity(start: date, end: date) -> dict[str, float]:
    path = config.RAW_DIR / f"weather_{start}_{end}.csv"
    with path.open(encoding="utf-8") as f:
        return {r["timestamp"]: float(r["relative_humidity_2m"]) for r in csv.DictReader(f)
                if r["relative_humidity_2m"]}


def score(observed: dict[str, float], cams: dict[str, float]) -> dict | None:
    hours = sorted(set(observed) & set(cams))
    if not hours:
        return None
    errors = [cams[h] - observed[h] for h in hours]
    bad_hours = [h for h in hours if observed[h] >= 55.4]
    return {
        "hours": hours,
        "observed": statistics.mean(observed[h] for h in hours),
        "cams": statistics.mean(cams[h] for h in hours),
        "bias": statistics.mean(errors),
        "mae": statistics.mean(abs(e) for e in errors),
        "same_category": sum(
            config.pm25_category(cams[h]) == config.pm25_category(observed[h]) for h in hours
        ) / len(hours),
        "bad_hours": len(bad_hours),
        "bad_caught": sum(cams[h] >= 55.4 for h in bad_hours),
    }


def print_report(title: str, m: dict, observed: dict[str, float], cams: dict[str, float]) -> None:
    print(f"\n== {title} ==")
    print(f"Hours compared:            {len(m['hours'])}")
    print(f"Mean observed PM2.5:       {m['observed']:7.1f} µg/m³")
    print(f"Mean CAMS PM2.5:           {m['cams']:7.1f} µg/m³")
    print(f"Bias (CAMS - observed):    {m['bias']:+7.1f} µg/m³")
    print(f"Mean absolute error:       {m['mae']:7.1f} µg/m³")
    print(f"Same AQI category:         {m['same_category']:7.0%}")
    if m["bad_hours"]:
        print(f"'Unhealthy' hours caught:  {m['bad_caught']}/{m['bad_hours']} "
              f"({m['bad_caught'] / m['bad_hours']:.0%})")

    worst = sorted(m["hours"], key=lambda h: observed[h] - cams[h], reverse=True)[:5]
    print("Worst misses (observed vs CAMS, UTC):")
    for h in worst:
        print(f"  {h}  {observed[h]:6.0f} vs {cams[h]:5.0f}")


def markdown_row(season: str, sensors: str, m: dict) -> str:
    return (f"| {season} | {sensors} | {len(m['hours'])} | {m['observed']:.0f} | {m['cams']:.0f} "
            f"| {m['bias']:+.0f} | {m['mae']:.0f} | {m['same_category']:.0%} "
            f"| {m['bad_caught']} of {m['bad_hours']} |")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_range_args(p)
    args = p.parse_args()

    table = []
    for start, end in ranges_from_args(p, args):
        try:
            cams = load_cams(start, end)
        except FileNotFoundError:
            print(f"\n{start}..{end}: no CAMS file. Run smog.backfill for the same range first.")
            continue
        variants = [("raw", None)]
        try:
            rh = load_humidity(start, end)
            humid = sum(v >= 90 for v in rh.values()) / max(len(rh), 1)
            print(f"\n#### {start}..{end}  (ERA5 humidity >= 90%: {humid:.0%} of hours)")
            variants.append(("humidity-corrected", rh))
        except FileNotFoundError:
            print(f"\n#### {start}..{end}  (no weather file: humidity-corrected score skipped)")

        season = f"{start.year}/{str(end.year)[2:]}" if (start.month, end.month) == (11, 2) else f"{start}..{end}"
        for label, rh_arg in variants:
            observed = hourly_city_pm25(start, end, rh_arg)
            m = score(observed, cams)
            if m is None:
                print(f"{label}: no overlapping hours (need >= 2 sensors per hour).")
                break
            print_report(label, m, observed, cams)
            table.append(markdown_row(season, label, m))

    if args.all_seasons and table:
        print("\n| Season | Sensors | Hours | Observed | CAMS | Bias | MAE | Same category | Unhealthy caught |")
        print("|---|---|---|---|---|---|---|---|---|")
        print("\n".join(table))


if __name__ == "__main__":
    main()
