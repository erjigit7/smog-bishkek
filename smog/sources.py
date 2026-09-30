"""Clients for the open data sources. Standard library only."""
import csv
import io
import json
import re
import time
import zlib
import urllib.error
import urllib.request
from datetime import date

from . import config

USER_AGENT = "smog-bishkek/0.1 (open research project)"


def _get(url: str, retries: int = 3, max_bytes: int | None = None) -> bytes | None:
    """GET a URL (or only its first max_bytes). Returns None on 404, raises after repeated other failures."""
    headers = {"User-Agent": USER_AGENT}
    if max_bytes:
        headers["Range"] = f"bytes=0-{max_bytes - 1}"
    req = urllib.request.Request(url, headers=headers)
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                # read(n) also covers servers that ignore Range and send the whole file.
                return resp.read(max_bytes) if max_bytes else resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 416:  # Range request on an empty file
                return b""
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


ARCHIVE = "https://archive.sensor.community"
ARCHIVE_FILE = re.compile(r'href="(\d{4}-\d{2}-\d{2})_([a-z0-9]+)_sensor_(\d+)\.csv(?:\.gz)?"')


def _archive_dir(day: date) -> list[tuple[str, str]]:
    """Candidate (directory URL, file suffix) pairs for a day, most likely first.

    The current year lives at /<day>/<file>.csv; past years are moved to
    /<year>/<day>/<file>.csv.gz. Callers try both, in this order.
    """
    d = day.isoformat()
    current = (f"{ARCHIVE}/{d}/", ".csv")
    past = (f"{ARCHIVE}/{day.year}/{d}/", ".csv.gz")
    # Trying the likely location first halves the 404s we send to the archive.
    return [past, current] if day.year < date.today().year else [current, past]


def _archive_file(day: date, name: str, max_bytes: int | None = None) -> bytes | None:
    """One archive CSV (decompressed), or its first bytes. None if the file does not exist."""
    for url, suffix in _archive_dir(day):
        raw = _get(url + name + suffix, max_bytes=max_bytes)
        if raw is None:
            continue
        if suffix.endswith(".gz"):
            # decompressobj accepts a truncated stream, unlike gzip.decompress.
            raw = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw)
        return raw
    return None


def archive_pm_files(day: date) -> dict[int, str]:
    """All PM sensors that have a file in the archive for a day: {id: type}, from the directory listing."""
    for url, _ in _archive_dir(day):
        html = _get(url)
        if html:
            break
    else:
        return {}
    types = {t.lower(): t for t in config.PM_SENSOR_TYPES}
    return {
        int(sid): types[stype]
        for _, stype, sid in ARCHIVE_FILE.findall(html.decode("utf-8", "replace"))
        if stype in types
    }


def sensor_location(sensor_id: int, sensor_type: str, day: date) -> tuple[float, float] | None:
    """Where a sensor stood on a day, read from the first line of its archive file.

    Only the first few KB are downloaded, so checking thousands of sensors is cheap.
    """
    name = f"{day.isoformat()}_{sensor_type.lower()}_sensor_{sensor_id}"
    raw = _archive_file(day, name, max_bytes=4096)
    if not raw:
        return None
    lines = raw.decode("utf-8", "replace").splitlines()
    if len(lines) < 2:
        return None
    row = dict(zip(lines[0].split(";"), lines[1].split(";")))
    try:
        return float(row["lat"]), float(row["lon"])
    except (KeyError, ValueError):
        return None  # empty coordinates happen for sensors registered without a location


def sensor_day(sensor_id: int, sensor_type: str, day: date) -> list[dict]:
    """Raw readings of one sensor for one UTC day from the public archive.

    Returns [{timestamp, lat, lon, pm10, pm25}] or [] if the sensor has no file that day.
    """
    raw = _archive_file(day, f"{day.isoformat()}_{sensor_type.lower()}_sensor_{sensor_id}")
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
