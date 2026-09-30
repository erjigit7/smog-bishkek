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
KNOWN_SENSORS = {
    # id: type
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
