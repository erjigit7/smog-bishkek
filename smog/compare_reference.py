"""Compare our sensors with the US Embassy reference monitor.

The embassy monitor is regulatory-grade (EPA-approved); SDS011 sensors are cheap laser
counters. This shows how far each sensor is from the truth and tests the humidity
correction on real data instead of trusting a formula from another country.

Usage:
    py -m smog.compare_reference --season 2021 --season 2022
    py -m smog.compare_reference --all-seasons   # config.SEASONS; reference ends Feb 2024

Needs smog.backfill for the same seasons (it also downloads the reference).
"""
import argparse
import csv
import statistics
from datetime import datetime

from . import config, humidity
from .evaluate_cams import hourly_city_pm25, load_humidity, sensor_hourly_pm25
from .find_sensors import distance_km

UNHEALTHY = 55.4
# A season is unusable as a reference if too many hours are near zero: winter 2023/24 has
# 40-60% of hours below 5 µg/m³ while every sensor shows 25+, so the monitor was faulty.
MAX_NEAR_ZERO_SHARE = 0.2


def load_reference() -> dict[str, float]:
    with (config.RAW_DIR / "embassy_pm25.csv").open(encoding="utf-8") as f:
        return {r["timestamp"]: float(r["pm25"]) for r in csv.DictReader(f)}


def sensor_positions() -> dict[str, tuple[float, float]]:
    """Coordinates from the downloaded sensor files (first reading of each sensor)."""
    pos = {}
    for path in sorted((config.RAW_DIR / "sensors").glob("*.csv")):
        with path.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                pos.setdefault(r["sensor_id"], (float(r["lat"]), float(r["lon"])))
    return pos


def fit_calibration(pairs: list[tuple[float, float, float]]) -> tuple[float, float]:
    """Least-squares a, gamma in  reference = a * sensor / g(rh, gamma)."""
    best = None
    for gi in range(41):
        gamma = gi / 100
        x = [s / humidity.growth_factor(rh, gamma) for s, _, rh in pairs]
        y = [r for _, r, _ in pairs]
        a = sum(xi * yi for xi, yi in zip(x, y)) / sum(xi * xi for xi in x)
        sse = sum((a * xi - yi) ** 2 for xi, yi in zip(x, y))
        if best is None or sse < best[0]:
            best = (sse, a, gamma)
    return best[1], best[2]


def score_line(name: str, est: list[tuple[float, float]]) -> str:
    bad = [(x, r) for x, r in est if r >= UNHEALTHY]
    false_alarms = sum(x >= UNHEALTHY > r for x, r in est)
    return (f"  {name:34s} bias={statistics.mean(x - r for x, r in est):+6.1f} "
            f"MAE={statistics.mean(abs(x - r) for x, r in est):5.1f} "
            f"unhealthy caught={sum(x >= UNHEALTHY for x, _ in bad)}/{len(bad)} false alarms={false_alarms}")


def print_sensor_correlations(per_sensor: dict[str, dict[str, float]]) -> None:
    """Sensor-to-sensor r: shows which sensors see the same air (and which are broken)."""
    ids = sorted(per_sensor)
    print("Sensor-to-sensor correlation (hours both reported, '-' if under 200):")
    print("        " + " ".join(f"{i:>6}" for i in ids))
    for a in ids:
        cells = []
        for b in ids:
            common = [h for h in per_sensor[a] if h in per_sensor[b]]
            cells.append(f"{statistics.correlation([per_sensor[a][h] for h in common], [per_sensor[b][h] for h in common]):6.2f}"
                         if len(common) >= 200 else "     -")
        print(f"  {a:>6} " + " ".join(cells))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--season", type=int, action="append", help="start year, repeatable")
    p.add_argument("--all-seasons", action="store_true")
    args = p.parse_args()
    seasons = config.SEASONS if args.all_seasons or not args.season else args.season

    ref = load_reference()
    pos = sensor_positions()
    colocated = {}  # season -> [(sensor, reference, rh)]

    for year in seasons:
        start, end = config.season_range(year)
        hours = {h: v for h, v in ref.items() if start.isoformat() <= h[:10] <= end.isoformat()}
        print(f"\n#### Season {year}/{str(year + 1)[2:]}")
        if not hours:
            print("No reference data (the embassy archive ends in Feb 2024).")
            continue
        near_zero = sum(v < 5 for v in hours.values()) / len(hours)
        usable = near_zero <= MAX_NEAR_ZERO_SHARE
        print(f"Reference: {len(hours)} valid hours, mean {statistics.mean(hours.values()):.1f}, "
              f"median {statistics.median(hours.values()):.1f}, below 5 µg/m³: {near_zero:.0%}"
              + ("" if usable else "  -> SUSPECT, not used for calibration"))

        per_sensor, _ = sensor_hourly_pm25(start, end)
        print("Sensor vs reference (ratio = sensor mean / reference mean, same hours):")
        for sid, series in sorted(per_sensor.items()):
            common = [h for h in series if h in hours]
            if len(common) < 200:
                continue
            s = [series[h] for h in common]
            r = [hours[h] for h in common]
            dist = distance_km(*pos[sid], config.EMBASSY_LAT, config.EMBASSY_LON) if sid in pos else float("nan")
            print(f"  {sid:>6} {dist:5.1f} km  hours={len(common):4d}  sensor={statistics.mean(s):6.1f}  "
                  f"ref={statistics.mean(r):6.1f}  ratio={statistics.mean(s) / statistics.mean(r):.2f}  "
                  f"r={statistics.correlation(s, r):.2f}")

        print_sensor_correlations(per_sensor)

        city = hourly_city_pm25(start, end)
        common = [h for h in city if h in hours]
        if common:
            s = [city[h] for h in common]
            r = [hours[h] for h in common]
            print(f"  city median: hours={len(common)} ratio={statistics.mean(s) / statistics.mean(r):.2f} "
                  f"r={statistics.correlation(s, r):.2f}; unhealthy hours: sensors {sum(v >= UNHEALTHY for v in s)}, "
                  f"reference {sum(v >= UNHEALTHY for v in r)}")

        series = per_sensor.get(str(config.COLOCATED_SENSOR), {})
        if usable and series:
            rh = load_humidity(start, end)
            colocated[year] = [(series[h], hours[h], rh[h]) for h in series if h in hours and h in rh]

    if not colocated:
        return
    print(f"\n#### Humidity and sensitivity of SDS011 {config.COLOCATED_SENSOR} (50 m from the reference)")
    for year, pairs in colocated.items():
        print(f"{year}/{str(year + 1)[2:]}: sensor/reference by humidity (hours with reference 25-150 µg/m³);"
              f" growth = ratio / ratio at RH < 60%:")
        dry = None
        for lo, hi in [(0, 60), (60, 80), (80, 90), (90, 101)]:
            b = [(s, r, h) for s, r, h in pairs if lo <= h < hi and 25 <= r < 150]
            if not b:
                continue
            ratio = statistics.mean(s for s, _, _ in b) / statistics.mean(r for _, r, _ in b)
            dry = dry or ratio
            rh_mid = statistics.median(h for _, _, h in b)
            print(f"  RH {lo:3d}-{hi:3d}%: hours={len(b):4d} ratio={ratio:.2f} growth x{ratio / dry:.2f}"
                  f"   formula with gamma {humidity.GAMMA_PM25}: x{humidity.growth_factor(rh_mid) / humidity.growth_factor(45):.2f}")

    # Fit on one winter, score on the other: a fit scored on its own data proves nothing.
    for train, test in [(a, b) for a in colocated for b in colocated if a != b]:
        a, gamma = fit_calibration(colocated[train])
        k = fit_calibration([(s, r, 0.0) for s, r, _ in colocated[train]])[0]
        pairs = colocated[test]
        print(f"\nFitted on {train}/{str(train + 1)[2:]}: a={a:.2f}, gamma={gamma:.2f}; factor only k={k:.2f}. "
              f"Scored on {test}/{str(test + 1)[2:]} ({len(pairs)} hours):")
        print(score_line("raw sensor", [(s, r) for s, r, _ in pairs]))
        print(score_line(f"humidity only, gamma {humidity.GAMMA_PM25}", [(humidity.correct_pm(s, h), r) for s, r, h in pairs]))
        print(score_line(f"factor x{k:.2f}", [(k * s, r) for s, r, _ in pairs]))
        print(score_line(f"a={a:.2f}, gamma={gamma:.2f}", [(a * humidity.correct_pm(s, h, gamma), r) for s, r, h in pairs]))


if __name__ == "__main__":
    main()
