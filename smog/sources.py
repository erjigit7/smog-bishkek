"""Clients for the open data sources. Standard library only."""
import csv
import gzip
import io
import json
import time
import urllib.error
import urllib.request
from datetime import date

from . import config

USER_AGENT = "smog-bishkek/0.1 (open research project)"


def _get(url: str, retries: int = 3) -> bytes | None:
    """GET a URL. Returns None on 404, raises after repeated other failures."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except urllib.error.URLError:
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt)
    return None


# --- sensor.community ------------------------------------------------------

def live_sensors() -> dict[int, dict]:
    """PM sensors currently online around Bishkek: {id: {type, lat, lon}}."""
    url = (
        "https://data.sensor.community/airrohr/v1/filter/"
        f"area={config.CITY_LAT},{config.CITY_LON},{config.SENSOR_RADIUS_KM}"
    )
    found = {}
    for row in json.loads(_get(url) or b"[]"):
        stype = row["sensor"]["sensor_type"]["name"]
        if stype in config.PM_SENSOR_TYPES:
            found[row["sensor"]["id"]] = {
                "type": stype,
                "lat": float(row["location"]["latitude"]),
                "lon": float(row["location"]["longitude"]),
            }
    return found


def sensor_day(sensor_id: int, sensor_type: str, day: date) -> list[dict]:
    """Raw readings of one sensor for one UTC day from the public archive.

    Returns [{timestamp, lat, lon, pm10, pm25}] or [] if the sensor has no file that day.
    """
    d = day.isoformat()
    name = f"{d}_{sensor_type.lower()}_sensor_{sensor_id}.csv"
    # Current year lives at /<day>/<file>.csv; past years are moved to /<year>/<day>/<file>.csv.gz.
    raw = _get(f"https://archive.sensor.community/{d}/{name}")
    if raw is None:
        gz = _get(f"https://archive.sensor.community/{day.year}/{d}/{name}.gz")
        raw = gzip.decompress(gz) if gz else None
    if raw is None:
        return []
    rows = []
    for r in csv.DictReader(io.StringIO(raw.decode("utf-8", "replace")), delimiter=";"):
        try:
            rows.append({
                "timestamp": r["timestamp"][:19],  # UTC, "YYYY-MM-DDTHH:MM:SS"
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "pm10": float(r["P1"]),
                "pm25": float(r["P2"]),
            })
        except (KeyError, ValueError):
            continue
    return rows


# --- Open-Meteo ------------------------------------------------------------

def weather_history(start: date, end: date) -> list[dict]:
    """Hourly observed-weather reanalysis (ERA5) for the city centre, UTC."""
    url = (
        "https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={config.CITY_LAT}&longitude={config.CITY_LON}"
        f"&start_date={start}&end_date={end}"
        f"&hourly={','.join(config.WEATHER_VARS)}&timezone=UTC"
    )
    return _hourly_rows(json.loads(_get(url)))


def cams_history(start: date, end: date) -> list[dict]:
    """Hourly PM2.5/PM10 from the global CAMS model (what Open-Meteo, and many apps, show)."""
    url = (
        "https://air-quality-api.open-meteo.com/v1/air-quality"
        f"?latitude={config.CITY_LAT}&longitude={config.CITY_LON}"
        f"&start_date={start}&end_date={end}"
        "&hourly=pm2_5,pm10&timezone=UTC"
    )
    return _hourly_rows(json.loads(_get(url)))


def _hourly_rows(payload: dict) -> list[dict]:
    if "hourly" not in payload:
        raise RuntimeError(f"Open-Meteo error: {payload.get('reason', payload)}")
    hourly = payload["hourly"]
    keys = [k for k in hourly if k != "time"]
    return [
        {"timestamp": t + ":00", **{k: hourly[k][i] for k in keys}}
        for i, t in enumerate(hourly["time"])
    ]
