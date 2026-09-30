"""Download historical sensor, weather and CAMS data for a date range.

Usage:
    py -m smog.backfill --start 2025-11-01 --end 2026-02-28
    py -m smog.backfill --season 2022        # 2022-11-01..2023-02-28
    py -m smog.backfill --all-seasons        # every season in config.SEASONS

Writes to data/raw/. Days already downloaded are skipped, so the command can be re-run.
"""
import argparse
import csv
import time
from datetime import date, timedelta
from pathlib import Path

from . import config, sources


def write_csv(path: Path, rows: list[dict]) -> None:
    """Write rows; an empty day still gets an empty file so re-runs skip it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        if not rows:
            return
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def backfill_sensors(start: date, end: date) -> None:
    try:
        sensors = {sid: meta["type"] for sid, meta in sources.live_sensors().items()}
    except OSError as e:  # past seasons only need KNOWN_SENSORS
        print(f"Live API unavailable ({e}); using KNOWN_SENSORS only.")
        sensors = {}
    sensors.update(config.KNOWN_SENSORS)
    print(f"Sensors: {len(sensors)} -> {sorted(sensors)}")

    out_dir = config.RAW_DIR / "sensors"
    for day in daterange(start, end):
        path = out_dir / f"{day}.csv"
        if path.exists():
            continue
        rows = []
        for sid, stype in sensors.items():
            for r in sources.sensor_day(sid, stype, day):
                rows.append({"sensor_id": sid, "sensor_type": stype, **r})
            time.sleep(0.2)  # be polite to a volunteer-run archive
        write_csv(path, rows)
        active = len({r["sensor_id"] for r in rows})
        print(f"{day}: {len(rows):6d} readings from {active} sensors")


def backfill_hourly(start: date, end: date) -> None:
    # One request per source for the whole range; Open-Meteo handles long ranges fine.
    write_csv(config.RAW_DIR / f"weather_{start}_{end}.csv", sources.weather_history(start, end))
    write_csv(config.RAW_DIR / f"cams_{start}_{end}.csv", sources.cams_history(start, end))
    print(f"Weather and CAMS saved for {start}..{end}")


def add_range_args(p: argparse.ArgumentParser) -> None:
    """--start/--end, or --season YEAR, or --all-seasons. Shared with evaluate_cams."""
    p.add_argument("--start", type=date.fromisoformat)
    p.add_argument("--end", type=date.fromisoformat)
    p.add_argument("--season", type=int, help="heating season by start year: 2022 = 2022-11-01..2023-02-28")
    p.add_argument("--all-seasons", action="store_true", help=f"every season in config.SEASONS {config.SEASONS}")


def ranges_from_args(p: argparse.ArgumentParser, args) -> list[tuple[date, date]]:
    if args.all_seasons:
        return [config.season_range(y) for y in config.SEASONS]
    if args.season:
        return [config.season_range(args.season)]
    if args.start and args.end:
        return [(args.start, args.end)]
    p.error("give --start and --end, or --season, or --all-seasons")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_range_args(p)
    args = p.parse_args()

    for start, end in ranges_from_args(p, args):
        backfill_hourly(start, end)
        backfill_sensors(start, end)


if __name__ == "__main__":
    main()
