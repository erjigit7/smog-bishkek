# smog-bishkek

Local PM2.5 forecast for Bishkek, 1–3 days ahead, by district, delivered through a Telegram bot in Russian and Kyrgyz.

Background research, rejected alternatives, competitors and verified data sources: `docs/CONTEXT.md` (Russian). Read it before changing the project's direction.
The owner is learning Claude Code: explain non-obvious decisions briefly in Russian in PR descriptions.

## Why this exists
Global models (CAMS, used by Open-Meteo and many weather apps) badly underestimate Bishkek winter smog.
Baseline over four heating seasons 2022/23..2025/26, raw sensor readings (`py -m smog.evaluate_cams --all-seasons`, full table below):
- mean observed PM2.5 26–50 µg/m³ vs CAMS 9–19 µg/m³ (bias −17..−39)
- same AQI category only 26–46% of hours
- "unhealthy" hours (PM2.5 ≥ 55.4) caught: 0 in every season (0 of 873, 301, 627, 591)

Every model we build must beat these numbers on the same metrics.

## Data sources (all open, no keys)
- sensor.community archive: per-sensor daily CSV. Current year at `/<day>/`, past years at `/<year>/<day>/*.csv.gz`.
- Open-Meteo archive API: ERA5 weather (temperature, wind, boundary layer height, humidity...).
- Open-Meteo air-quality API: CAMS PM2.5/PM10, the baseline to beat.
- Later: MoveGreen / aq.kg sensors, aqicn (free token), Kyrgyzhydromet.

## Conventions
- All timestamps UTC in storage and code; convert to Asia/Bishkek (UTC+6) only for display.
- Data layer (`smog/sources.py`, `smog/backfill.py`) uses the standard library only.
- Run modules as `py -m smog.<module>` (Windows) or `python -m smog.<module>`.
- `data/` is not committed; recreate with `smog.backfill`.
- Be polite to sensor.community (volunteer-run): keep the delay between requests.
- SDS011 sensors overestimate PM at high humidity. `smog/humidity.py` corrects them with the kappa-Koehler formula (gamma 0.22, sensor.community wiki) using ERA5 humidity. gamma is not calibrated for Bishkek, so always report raw and corrected numbers side by side.
- Heating season = 1 Nov .. 28 Feb (28 Feb also in leap years), named by its start year: season 2022 = 2022/23. List in `config.SEASONS`.

## Commands
```
py -m smog.find_sensors                  # sensors in Bishkek on sample days of past winters -> KNOWN_SENSORS
py -m smog.backfill --all-seasons        # or --season 2022, or --start/--end
py -m smog.evaluate_cams --all-seasons   # raw + humidity-corrected, prints a Markdown table
```

## CAMS baseline by season
Output of `py -m smog.evaluate_cams --all-seasons` (2026-09-30). City value = median of the sensors' hourly means, hours with ≥ 2 sensors.
Sensors: all 11 Bishkek SDS011 in `KNOWN_SENSORS`, 3–10 reporting per day.

| Season | Sensors | Hours | Observed | CAMS | Bias | MAE | Same category | Unhealthy caught |
|---|---|---|---|---|---|---|---|---|
| 2022/23 | raw | 2880 | 50 | 10 | -39 | 40 | 26% | 0 of 873 |
| 2022/23 | humidity-corrected | 2880 | 36 | 10 | -25 | 27 | 41% | 0 of 544 |
| 2023/24 | raw | 2880 | 26 | 9 | -17 | 18 | 39% | 0 of 301 |
| 2023/24 | humidity-corrected | 2880 | 15 | 9 | -6 | 9 | 59% | 0 of 62 |
| 2024/25 | raw | 2842 | 40 | 19 | -21 | 23 | 45% | 0 of 627 |
| 2024/25 | humidity-corrected | 2842 | 26 | 19 | -7 | 14 | 58% | 0 of 294 |
| 2025/26 | raw | 2872 | 39 | 18 | -22 | 24 | 46% | 0 of 591 |
| 2025/26 | humidity-corrected | 2872 | 24 | 18 | -6 | 14 | 57% | 0 of 274 |

Notes:
- The earlier 2025/26 baseline (51 vs 18, 35%, 0 of 916) used only sensors online in Sept 2026. Sensor 76617 (offline now, low readings) reported all 120 days of that winter; without it the old numbers reproduce exactly.
- The humidity correction halves "unhealthy" hours or more (2023/24: 301 → 62). gamma 0.22 is uncalibrated; do not base alerts on corrected values until it is checked against a reference instrument.
- Sensor 34313 reads 2–3× the others (2025/26 mean 93 vs 28–42); the median limits its effect but on 2-sensor hours it still pulls the city value up.

## Roadmap
1. [x] Data sources + backfill of last winter
2. [x] CAMS baseline evaluation
3. [x] Backfill earlier winters (2022–2025), humidity correction for SDS011 (gamma uncalibrated, see notes above)
4. [ ] Feature table: weather + lagged PM + hour/weekday/heating-season flags
5. [ ] First model (gradient boosting) for city PM2.5 at +24h/+48h; compare with baseline
6. [ ] Daily forecast job + storage
7. [ ] Telegram bot (RU/KG): daily morning forecast, alerts, "when to ventilate"
8. [ ] Per-district forecasts once enough sensors
