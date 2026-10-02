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

Every model we build must beat these numbers on the same metrics, and also the simplest
forecast "PM2.5 at t+24h = PM2.5 now" (persistence; `py -m smog.features`, all 4 seasons, hourly city median):
- +24h: persistence MAE 23.1, unhealthy caught 1178/2384; CAMS MAE 26.4, caught 0/2384
- +48h: persistence MAE 28.1, unhealthy caught 870/2374; CAMS MAE 26.5, caught 0/2374

## Data sources (all open, no keys)
- sensor.community archive: per-sensor daily CSV. Current year at `/<day>/`, past years at `/<year>/<day>/*.csv.gz`.
- Open-Meteo archive API: ERA5 weather (temperature, wind, boundary layer height, humidity...).
- Open-Meteo air-quality API: CAMS PM2.5/PM10, the baseline to beat.
- US Embassy Bishkek PM2.5 monitor (EPA-approved), hourly Feb 2019..Feb 2024: our only regulatory-grade reference. AirNow deleted it in 2025; public Kaggle mirror, see `sources.embassy_history`. Winter 2023/24 is faulty (half the hours near 0) and is not used.
- Later: MoveGreen / aq.kg sensors, aqicn (free token), Kyrgyzhydromet.

## Conventions
- All timestamps UTC in storage and code; convert to Asia/Bishkek (UTC+6) only for display.
- Data layer (`smog/sources.py`, `smog/backfill.py`) uses the standard library only. Modelling (`smog/train.py`) uses numpy + scikit-learn (`requirements.txt`).
- Never tune or select anything on a test winter: score on winters the model did not see (leave-one-season-out, or train ≤2024/25 / test 2025/26).
- Run modules as `py -m smog.<module>` (Windows) or `python -m smog.<module>`.
- `data/` is not committed; recreate with `smog.backfill`. Committed on purpose: `models/` (the trained alert model the daily job loads) and `forecasts/forecasts.csv` (written by the daily GitHub Action).
- `requirements.txt` pins exact versions: `models/alert.pkl` is a pickle and only loads reliably with the same scikit-learn. After upgrading, retrain with `py -m smog.alerts --save`.
- Be polite to sensor.community (volunteer-run): keep the delay between requests.
- SDS011 sensors overestimate PM at high humidity. `smog/humidity.py` corrects that with the kappa-Koehler formula, gamma 0.05 fitted against the embassy monitor (the wiki value 0.22 was 2–3× too strong for Bishkek). The correction only removes the humidity part: SDS011 also reads 0.4–0.6× of the reference in dry air, so corrected values are still too low. Report raw and corrected side by side.
- Heating season = 1 Nov .. 28 Feb (28 Feb also in leap years), named by its start year: season 2022 = 2022/23. List in `config.SEASONS`.

## Commands
```
py -m smog.find_sensors                  # sensors in Bishkek on sample days of past winters -> KNOWN_SENSORS
py -m smog.backfill --all-seasons        # or --season 2022, or --start/--end
py -m smog.evaluate_cams --all-seasons   # raw + humidity-corrected, prints a Markdown table
py -m smog.backfill --season 2021 && py -m smog.compare_reference --season 2021 --season 2022   # sensors vs embassy
py -m smog.features                      # data/features.csv: one row per hour, targets y_24 / y_48
pip install -r requirements.txt && py -m smog.train   # first model vs persistence and CAMS
py -m smog.alerts                        # morning alert: "tomorrow >= 3 unhealthy hours?" + tomorrow's mean
py -m smog.alerts --save                 # train on all winters -> models/alert.pkl (+ alert.json)
py -m smog.forecast                      # the daily job: verify past forecasts, forecast tomorrow
py -m smog.forecast --date 2026-01-20 --dry-run   # as if run that morning
py -m smog.forecast --date 2026-01-20 --test-send # also posts it to the Telegram channel, marked TEST (needs TELEGRAM_* env)
```

## Feature table (`smog/features.py`)
- Target: raw city median PM2.5 at t+24h / t+48h, same series as the baseline.
- `pm_*`, `now_*`, calendar columns are known at forecast time.
- `era5_24_*`, `era5_48_*`, `cams_*` are OBSERVED weather at the target hour, i.e. a perfect weather forecast. Archived day-ahead forecasts (Open-Meteo previous-runs API) have no boundary layer height at all and most other variables only from winter 2024/25. Always score models with and without these columns; real skill lies in between.
- ERA5 boundary layer height is missing in Open-Meteo for Jan–Feb 2024 (also with `models=era5`); left empty.

## First model (`smog/train.py`)
Gradient boosting (scikit-learn `HistGradientBoostingRegressor`, fixed settings, nothing tuned on test winters).
Output of `py -m smog.train` (2026-10-01), hourly city median PM2.5, leave-one-season-out over all 4 winters (each winter predicted by a model trained on the other three):

| Horizon | Method | MAE | Bias | Same category | Unhealthy caught | False alarms |
|---|---|---|---|---|---|---|
| +24h | persistence | 23.1 | -0.2 | 44% | 1178 of 2384 | 1181 |
| +24h | CAMS | 26.4 | -24.8 | 39% | 0 of 2384 | 0 |
| +24h | model, at forecast time | 21.0 | -1.4 | 46% | 1000 of 2384 | 767 |
| +24h | model, + perfect weather | 18.8 | -1.4 | 49% | 1153 of 2384 | 789 |
| +48h | persistence | 28.1 | -0.4 | 38% | 870 of 2374 | 1450 |
| +48h | CAMS | 26.5 | -25.0 | 39% | 0 of 2374 | 0 |
| +48h | model, at forecast time | 22.8 | -1.6 | 43% | 834 of 2374 | 794 |
| +48h | model, + perfect weather | 20.0 | -2.0 | 48% | 1037 of 2374 | 806 |

Main test (train 2022/23..2024/25, test 2025/26), +24h: persistence MAE 25.6, CAMS 24.3, model 21.0 (at forecast time) / 18.2 (+ perfect weather); +48h: 30.4, 24.4, 23.8 / 19.1.

Notes:
- The model beats persistence and CAMS on MAE at both horizons, but catches FEWER unhealthy hours than persistence (it smooths peaks). For alerts this is the next thing to fix (e.g. predict P(PM2.5 ≥ 55.4), choose the threshold on training winters only).
- Real skill with a real weather forecast lies between "at forecast time" and "+ perfect weather".
- Most useful inputs (+24h, permutation importance on 2025/26): surface pressure, hour of day, PM2.5 now, temperature.

## Alert model (`smog/alerts.py`)
The bot's morning message about the next calendar day (Bishkek time). It may only use data up to 23:00 UTC the day before (05:00 Bishkek): sensor.community publishes a day's archive at ~03:26 UTC next morning (checked via Last-Modified). Unhealthy day = ≥ 3 hours with city PM2.5 ≥ 55.4 (222 of 468 winter days, 47%). Probability threshold chosen on the training winters only (out-of-fold), the highest one that still catches 80% of their unhealthy days.
Output of `py -m smog.alerts` (2026-10-01), leave-one-season-out, all 4 winters:

| Method | Unhealthy days caught | False alarms | Right when it warns | Tomorrow's mean: MAE | Same category |
|---|---|---|---|---|---|
| persistence (last 24 h) | 132 of 222 (59%) | 86 of 246 | 61% | 20.5 | 51% |
| CAMS | 0 of 222 | 0 | — | 25.1 | 38% |
| model, at forecast time | 167 of 222 (75%) | 131 of 246 | 56% | 18.1 | 54% |
| model, + perfect weather | 165 of 222 (74%) | 102 of 246 | 62% | 15.4 | 63% |

Notes:
- With the same number of warnings as persistence (218), the model catches 142 vs 132 and raises 76 vs 86 false alarms: better, but modestly. Its extra catches come mostly from warning more often, by design.
- Not uniform: in 2025/26 the model caught 29 of 57 (51%) vs persistence 36 (63%).
- The raw probability is overconfident (P 0–0.2: 32% of those days were unhealthy; P 0.8–1.0: 76%). `--save` fits an isotonic calibrator on out-of-fold predictions and the daily job reports words (low < 0.3 ≤ medium < 0.6 ≤ high) from the calibrated value. Whether that calibration holds is only shown by the live forecasts.
- Tomorrow's mean PM2.5: the model beats persistence and CAMS (MAE 18.1 vs 20.5 vs 25.1).

## Daily forecast (`smog/forecast.py`, `.github/workflows/daily-forecast.yml`)
- GitHub Actions runs it every day at 03:45 UTC (09:45 Bishkek) and commits `forecasts/forecasts.csv`.
- Each run: first fills in what actually happened (observed_*) for earlier forecasts whose day is published, then forecasts tomorrow with `models/alert.pkl`. Only for targets in Nov–Feb (the model knows nothing else); `--force` overrides, and a forced October run indeed raised a nonsense warning at 7 µg/m³.
- If fewer than 2 sensors report at the data cutoff, it records "no forecast" instead of guessing (the model never saw such inputs).
- If a data source is down (Open-Meteo answers 429 or drops connections from shared IPs), it records "no forecast: data source unavailable", exits 1 (red run, e-mail to the owner) and still commits the CSV; verification is saved before the forecast step, and the next run (or a manual re-run) replaces the failed row.
- Inputs are built the same way as in training; checked on 3 past dates (2025-12-15, 2026-01-20, 2024-01-10): all 22 inputs identical to the training rows.
- Weather "now" comes from the Open-Meteo forecast API (ERA5 is ~5 days late); training used ERA5. Small, accepted mismatch.
- Live accuracy = compare risk/alert/mean_pm25 with observed_* in `forecasts/forecasts.csv`. Retrain after each winter: `py -m smog.features && py -m smog.alerts --save`.

## Telegram channel (`smog/telegram.py`, called by `smog/forecast.py`)
- Variant A of roadmap item 7: the daily job posts to a public channel; the bot cannot answer commands (that needs a server running 24/7, a later decision). Standard library only.
- Secrets `TELEGRAM_BOT_TOKEN` (@BotFather) and `TELEGRAM_CHANNEL` (`@name`) live in GitHub repository secrets, never in code or in the repo. Without them the job only prints. The bot must be a channel admin allowed to post. Error text is scrubbed of the token.
- One post = Russian, separator, Kyrgyz: risk word, tomorrow's mean PM2.5 and category, the alert text (only with an alert), "when to ventilate" (only with medium/high risk or an alert), a line checking yesterday's forecast against what was observed (only when it was verified), a note that the sensors read low. The Kyrgyz text was written by a model: needs a native reader.
- "When to ventilate" is the typical winter day, not a forecast: city median PM2.5 by Bishkek hour over 4 winters is ~17–18 at 05–08 h and ~21–22 at 12–15 h, but ~40–46 at 18–23 h (31–39% of those hours unhealthy). The model has no hourly forecast.
- Never posts: "no forecast" rows, forecasts forced outside Nov–Feb. A post is recorded in `forecasts.csv` (`sent_at_utc`): a re-run does not post twice, a failed post is retried by the next run and turns the run red.
- Test without publishing a real forecast: Actions → Daily smog forecast → Run workflow → `test_date` = a past winter date (posts that day's forecast marked TEST, saves nothing). Tested locally against a fake Telegram server (send, 4xx without retry, 5xx/429 retries, token never in errors, no double post), not yet against the real API.

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
4. [x] Feature table: weather + lagged PM + hour/weekday/heating-season flags (`smog.features`)
5. [x] First model (gradient boosting) for city PM2.5 at +24h/+48h; compare with baseline (`smog.train`; next: alert-oriented model, daily summary)
6. [x] Daily forecast job + storage (`smog.forecast`, GitHub Actions, `forecasts/forecasts.csv` with live verification)
7. [~] Telegram (RU/KG): channel with the daily morning post, alert and "when to ventilate" is built (`smog.telegram`); an interactive bot (commands, personal alerts) is not
8. [ ] Per-district forecasts once enough sensors
