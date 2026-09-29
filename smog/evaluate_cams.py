"""Baseline: how well does the global CAMS model match real Bishkek sensors?

This is the number our own forecast has to beat.

Usage:
    py -m smog.evaluate_cams --start 2025-11-01 --end 2026-02-28
"""
import argparse
import csv
import statistics
from collections import defaultdict
from datetime import date

from . import config
from .backfill import daterange


def hourly_city_pm25(start: date, end: date) -> dict[str, float]:
    """Median across sensors of each sensor's hourly-mean PM2.5. Key: 'YYYY-MM-DDTHH:00:00'."""
    per_sensor_hour = defaultdict(list)
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

    by_hour = defaultdict(list)
    for (_, hour), values in per_sensor_hour.items():
        by_hour[hour].append(statistics.mean(values))
    # Median is robust to one broken or badly placed sensor.
    return {h: statistics.median(v) for h, v in by_hour.items() if len(v) >= 2}


def load_cams(start: date, end: date) -> dict[str, float]:
    path = config.RAW_DIR / f"cams_{start}_{end}.csv"
    with path.open(encoding="utf-8") as f:
        return {r["timestamp"]: float(r["pm2_5"]) for r in csv.DictReader(f) if r["pm2_5"]}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", type=date.fromisoformat, required=True)
    p.add_argument("--end", type=date.fromisoformat, required=True)
    args = p.parse_args()

    observed = hourly_city_pm25(args.start, args.end)
    cams = load_cams(args.start, args.end)
    hours = sorted(set(observed) & set(cams))
    if not hours:
        raise SystemExit("No overlapping hours. Run smog.backfill for the same range first.")

    errors = [cams[h] - observed[h] for h in hours]
    category_hits = sum(config.pm25_category(cams[h]) == config.pm25_category(observed[h]) for h in hours)
    bad_hours = [h for h in hours if observed[h] >= 55.4]
    bad_caught = sum(cams[h] >= 55.4 for h in bad_hours)

    print(f"Hours compared:            {len(hours)}")
    print(f"Mean observed PM2.5:       {statistics.mean(observed[h] for h in hours):7.1f} µg/m³")
    print(f"Mean CAMS PM2.5:           {statistics.mean(cams[h] for h in hours):7.1f} µg/m³")
    print(f"Bias (CAMS - observed):    {statistics.mean(errors):+7.1f} µg/m³")
    print(f"Mean absolute error:       {statistics.mean(abs(e) for e in errors):7.1f} µg/m³")
    print(f"Same AQI category:         {category_hits / len(hours):7.0%}")
    if bad_hours:
        print(f"'Unhealthy' hours caught:  {bad_caught}/{len(bad_hours)} ({bad_caught / len(bad_hours):.0%})")

    worst = sorted(hours, key=lambda h: observed[h] - cams[h], reverse=True)[:5]
    print("\nWorst misses (observed vs CAMS, UTC):")
    for h in worst:
        print(f"  {h}  {observed[h]:6.0f} vs {cams[h]:5.0f}")


if __name__ == "__main__":
    main()
