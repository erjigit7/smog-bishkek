"""Project-wide settings. All timestamps in the project are UTC."""
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"

# Bishkek city centre and the radius (km) used to pick sensors.
CITY_LAT, CITY_LON = 42.87, 74.59
SENSOR_RADIUS_KM = 15

# sensor.community particulate sensor types we read (archive file prefix = type in lowercase).
PM_SENSOR_TYPES = {"SDS011", "SPS30", "PMS5003", "PMS7003"}

# Sensors known in Bishkek. The live API adds whatever is online today;
# this list keeps sensors that have gone offline but still have archive history.
# Every SDS011 that reported from Bishkek in winters 2022/23..2025/26. Found by
# `smog.find_sensors` (2022/23 sample days) plus 83895 from the live API.
# Completeness check against sensor.community's own daily SDS011 count for KG
# (archive.sensor.community/sensors_per_country_per_day.json): on all 480 season days
# KG count - our reporting sensors is 0..2 (0 on every day since Nov 2024); on the days
# checked the difference is exactly the two southern sensors 42866, 42876.
# Online/offline as of 2026-09-30.
KNOWN_SENSORS = {
    # id: type,        lat, lon (from the archive)
    33016: "SDS011",   # 42.923, 74.606  offline now
    33527: "SDS011",   # 42.868, 74.608  offline now
    34313: "SDS011",   # 42.885, 74.554  online
    35677: "SDS011",   # 42.828, 74.582  offline now
    35745: "SDS011",   # 42.812, 74.628  online
    52798: "SDS011",   # 42.872, 74.622  online
    55837: "SDS011",   # 42.850, 74.633  offline now
    66706: "SDS011",   # 42.877, 74.581  offline now
    67538: "SDS011",   # 42.882, 74.552  offline now
    76617: "SDS011",   # 42.816, 74.648  offline now
    83895: "SDS011",   # 42.836, 74.622  online, since Dec 2025
}

# Heating seasons we study, by the year they start in: 2022 = 2022-11-01..2023-02-28.
# Always ends on 28 Feb (also in leap years) so every season has the same 120 days.
SEASONS = [2022, 2023, 2024, 2025]


def season_range(start_year: int) -> tuple[date, date]:
    return date(start_year, 11, 1), date(start_year + 1, 2, 28)

# Weather variables that drive winter smog: inversions, stagnant air, cold (= more heating).
WEATHER_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "wind_speed_10m",
    "wind_direction_10m",
    "boundary_layer_height",
    "surface_pressure",
    "precipitation",
    "snow_depth",
]

# US EPA PM2.5 breakpoints (µg/m³) used for "was the category right?" scoring.
PM25_CATEGORIES = [
    (0, 9.0, "good"),
    (9.0, 35.4, "moderate"),
    (35.4, 55.4, "unhealthy_sensitive"),
    (55.4, 125.4, "unhealthy"),
    (125.4, 225.4, "very_unhealthy"),
    (225.4, float("inf"), "hazardous"),
]


def pm25_category(value: float) -> str:
    for low, high, name in PM25_CATEGORIES:
        if low <= value < high:
            return name
    return "hazardous"
