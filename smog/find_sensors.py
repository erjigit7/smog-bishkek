"""Find PM sensors that stood in Bishkek in past winters, including ones that are offline now.

The live API only knows sensors that are online today. The archive knows every sensor,
but not where it is: the location is only inside each sensor's daily CSV. So for a few
sample days per season we list the archive directory and read the first line of every
PM sensor's file (a few KB each, not the whole file).

Usage:
    py -m smog.find_sensors

Takes a while on the first run (the first sample day has ~15 000 files to check).
Locations are cached in data/raw/sensor_locations.csv, so later days only check sensors
not seen before, and an interrupted run continues where it stopped.
Prints a KNOWN_SENSORS block to paste into smog/config.py.
"""
import argparse
import csv
import math
import time
from collections import defaultdict
from datetime import date

from . import config, sources

CACHE = config.RAW_DIR / "sensor_locations.csv"


def sample_days() -> list[date]:
    """The 15th of every month of every season: catches sensors that lived only a month or two."""
    days = []
    for year in config.SEASONS:
        start, end = config.season_range(year)
        for y, m in [(start.year, 11), (start.year, 12), (end.year, 1), (end.year, 2)]:
            days.append(date(y, m, 15))
    return days


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(a))


def load_cache() -> dict[int, tuple[float, float] | None]:
    if not CACHE.exists():
        return {}
    with CACHE.open(encoding="utf-8") as f:
        return {
            int(r["sensor_id"]): (float(r["lat"]), float(r["lon"])) if r["lat"] else None
            for r in csv.DictReader(f)
        }


def main() -> None:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    locations = load_cache()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    new_file = not CACHE.exists()
    in_city = defaultdict(list)  # (id, type) -> days seen in the city

    with CACHE.open("a", newline="", encoding="utf-8") as f:
        cache = csv.writer(f)
        if new_file:
            cache.writerow(["sensor_id", "sensor_type", "lat", "lon"])

        for day in sample_days():
            files = sources.archive_pm_files(day)
            # Sensors without a location yet (e.g. an empty file that day) are retried on later days.
            todo = [sid for sid in files if locations.get(sid) is None]
            print(f"{day}: {len(files)} PM sensors in archive, {len(todo)} not located yet")
            for i, sid in enumerate(todo, 1):
                loc = sources.sensor_location(sid, files[sid], day)
                locations[sid] = loc
                cache.writerow([sid, files[sid], *(loc or ("", ""))])
                if i % 500 == 0:
                    f.flush()
                    print(f"  {i}/{len(todo)}")
                time.sleep(0.05)  # be polite to a volunteer-run archive
            f.flush()

            # A sensor's location is taken from the first day it was checked;
            # nodes rarely move between cities, so that is good enough here.
            for sid, stype in files.items():
                loc = locations.get(sid)
                if loc and distance_km(*loc, config.CITY_LAT, config.CITY_LON) <= config.SENSOR_RADIUS_KM:
                    in_city[(sid, stype)].append(day)

    try:
        live = set(sources.live_sensors())
    except OSError as e:  # the live API is a nice-to-have here
        print(f"Live API unavailable ({e}); cannot mark which sensors are online now.")
        live = set()

    print(f"\nPM sensors within {config.SENSOR_RADIUS_KM} km of Bishkek on the sample days:")
    print("KNOWN_SENSORS = {")
    for (sid, stype), days in sorted(in_city.items()):
        lat, lon = locations[sid]
        status = "online now" if sid in live else "offline now" if live else ""
        print(f'    {sid}: "{stype}",  # {lat:.3f},{lon:.3f}; seen {days[0]}..{days[-1]} '
              f"({len(days)}/{len(sample_days())} sample days) {status}".rstrip())
    print("}")


if __name__ == "__main__":
    main()
