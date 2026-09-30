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

The sensors themselves read low: against the US Embassy reference monitor the city median
was 0.90× (2021/22) and 0.56× (2022/23) of the truth (`py -m smog.compare_reference`).
So real smog is worse than these numbers, and CAMS misses it by even more.

Every model we build must beat these numbers on the same metrics.

## Data sources (all open, no keys)
- sensor.community archive: per-sensor daily CSV. Current year at `/<day>/`, past years at `/<year>/<day>/*.csv.gz`.
- Open-Meteo archive API: ERA5 weather (temperature, wind, boundary layer height, humidity...).
- Open-Meteo air-quality API: CAMS PM2.5/PM10, the baseline to beat.
- US Embassy Bishkek PM2.5 monitor (EPA-approved), hourly Feb 2019..Feb 2024: our only regulatory-grade reference. AirNow deleted it in 2025; public Kaggle mirror, see `sources.embassy_history`. Winter 2023/24 is faulty (half the hours near 0) and is not used.
- Later: MoveGreen / aq.kg sensors, aqicn (free token), Kyrgyzhydromet.

## Conventions
- All timestamps UTC in storage and code; convert to Asia/Bishkek (UTC+6) only for display.
- Data layer (`smog/sources.py`, `smog/backfill.py`) uses the standard library only.
- Run modules as `py -m smog.<module>` (Windows) or `python -m smog.<module>`.
- `data/` is not committed; recreate with `smog.backfill`.
- Be polite to sensor.community (volunteer-run): keep the delay between requests.
- SDS011 sensors overestimate PM at high humidity. `smog/humidity.py` corrects that with the kappa-Koehler formula, gamma 0.05 fitted against the embassy monitor (the wiki value 0.22 was 2–3× too strong for Bishkek). The correction only removes the humidity part: SDS011 also reads 0.4–0.6× of the reference in dry air, so corrected values are still too low. Report raw and corrected side by side.
- Heating season = 1 Nov .. 28 Feb (28 Feb also in leap years), named by its start year: season 2022 = 2022/23. List in `config.SEASONS`.

## Commands
```
py -m smog.find_sensors                  # sensors in Bishkek on sample days of past winters -> KNOWN_SENSORS
py -m smog.backfill --all-seasons        # or --season 2022, or --start/--end
py -m smog.evaluate_cams --all-seasons   # raw + humidity-corrected, prints a Markdown table
py -m smog.backfill --season 2021 && py -m smog.compare_reference --season 2021 --season 2022   # sensors vs embassy
```

## CAMS baseline by season
Output of `py -m smog.evaluate_cams --all-seasons` (2026-09-30). City value = median of the sensors' hourly means, hours with ≥ 2 sensors.
Sensors: all 11 Bishkek SDS011 in `KNOWN_SENSORS`, 3–10 reporting per day.

| Season | Sensors | Hours | Observed | CAMS | Bias | MAE | Same category | Unhealthy caught |
|---|---|---|---|---|---|---|---|---|
| 2022/23 | raw | 2880 | 50 | 10 | -39 | 40 | 26% | 0 of 873 |
| 2022/23 | humidity-corrected | 2880 | 45 | 10 | -34 | 35 | 31% | 0 of 725 |
| 2023/24 | raw | 2880 | 26 | 9 | -17 | 18 | 39% | 0 of 301 |
| 2023/24 | humidity-corrected | 2880 | 21 | 9 | -12 | 14 | 47% | 0 of 194 |
| 2024/25 | raw | 2842 | 40 | 19 | -21 | 23 | 45% | 0 of 627 |
| 2024/25 | humidity-corrected | 2842 | 35 | 19 | -16 | 19 | 52% | 0 of 469 |
| 2025/26 | raw | 2872 | 39 | 18 | -22 | 24 | 46% | 0 of 591 |
| 2025/26 | humidity-corrected | 2872 | 33 | 18 | -16 | 19 | 53% | 0 of 468 |

Notes:
- The earlier 2025/26 baseline (51 vs 18, 35%, 0 of 916) used only sensors online in Sept 2026. Sensor 76617 (offline now, low readings) reported all 120 days of that winter; without it the old numbers reproduce exactly.
- "humidity-corrected" is lower than raw, but the truth is higher than both (see below). Use it to compare humidity effects, not as a better estimate of real PM2.5.

## Sensors vs the embassy reference
Output of `py -m smog.compare_reference --season 2021 --season 2022` (2026-09-30), details in the PR #1 description.
- City median / reference: 0.90 (2021/22), 0.56 (2022/23); correlation 0.79 / 0.89. Unhealthy hours: sensors 684 vs reference 750 (2021/22), 872 vs 1206 (2022/23).
- 35677 (50 m from the monitor): r 0.93 / 0.97; reads 0.60 / 0.42 of the reference in dry air. The factor differs between winters, so no fixed multiplier is applied yet. Fitted on one winter and scored on the other, a*sensor/g(RH) cut the error (MAE 51 → 31 in 2022/23, 16 → 12.5 in 2021/22) and caught more unhealthy hours (629 → 781 of 1052, 448 → 717 of 750), at the cost of more false alarms (2 → 7, 29 → 207).
- Two air regimes: south/centre sensors (35677, 35745, 55837, 76617) track the embassy (r 0.74–0.97); west/north ones (34313, 67538, 66706, 52798) track each other (r 0.69–0.85 in 2022/23) and the embassy much less (r 0.22–0.60). The embassy represents the south, not the whole city.
- 34313 is not broken: r 0.83 with 67538, 250 m away, but it reads ~2× that neighbour (local source or a high-reading unit).
- 33527 worked in 2021/22 (r 0.67) and broke by 2022/23 (reads ~1 µg/m³, correlates with nothing). 33016 (far north) does not track the embassy (r ≤ 0.1) and only partly its west/north neighbours (r 0.40–0.62 in 2022/23). The median absorbs both; they are not excluded.

## Roadmap
1. [x] Data sources + backfill of last winter
2. [x] CAMS baseline evaluation
3. [x] Backfill earlier winters (2022–2025), humidity correction for SDS011 (gamma fitted against the embassy reference)
4. [ ] Feature table: weather + lagged PM + hour/weekday/heating-season flags
5. [ ] First model (gradient boosting) for city PM2.5 at +24h/+48h; compare with baseline
6. [ ] Daily forecast job + storage
7. [ ] Telegram bot (RU/KG): daily morning forecast, alerts, "when to ventilate"
8. [ ] Per-district forecasts once enough sensors
