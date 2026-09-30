# smog-bishkek

Local PM2.5 forecast for Bishkek, 1–3 days ahead, by district, delivered through a Telegram bot in Russian and Kyrgyz.

Background research, rejected alternatives, competitors and verified data sources: `docs/CONTEXT.md` (Russian). Read it before changing the project's direction.
The owner is learning Claude Code: explain non-obvious decisions briefly in Russian in PR descriptions.

## Why this exists
Global models (CAMS, used by Open-Meteo and many weather apps) badly underestimate Bishkek winter smog.
Baseline on winter 2025-11-01..2026-02-28 (`py -m smog.evaluate_cams`):
- mean observed PM2.5 51 µg/m³ vs CAMS 18 µg/m³ (bias −33)
- same AQI category only 35% of hours
- "unhealthy" hours (PM2.5 ≥ 55.4) caught: 0 of 916

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
Not filled yet: the cloud session that wrote the code had no network access to the data hosts.
Run the three commands above and paste the table printed by `evaluate_cams --all-seasons` here.

## Roadmap
1. [x] Data sources + backfill of last winter
2. [x] CAMS baseline evaluation
3. [ ] Backfill earlier winters (2022–2025), humidity correction for SDS011 — code done (`find_sensors`, `--season`, `humidity.py`); data download and per-season numbers pending
4. [ ] Feature table: weather + lagged PM + hour/weekday/heating-season flags
5. [ ] First model (gradient boosting) for city PM2.5 at +24h/+48h; compare with baseline
6. [ ] Daily forecast job + storage
7. [ ] Telegram bot (RU/KG): daily morning forecast, alerts, "when to ventilate"
8. [ ] Per-district forecasts once enough sensors
