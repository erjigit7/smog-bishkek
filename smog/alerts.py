"""Morning alert model: "will tomorrow be an unhealthy day?" plus tomorrow's mean PM2.5.

The bot's message: sent in the morning (~09:45 Bishkek time, see smog.forecast) about the
next calendar day (Bishkek time). It may only use data up to 05:00 Bishkek time (23:00 UTC):
sensor.community publishes a day's archive files at ~03:30 UTC the next morning.
- Unhealthy day = at least ALERT_HOURS hours with city PM2.5 >= 55.4 (47% of winter days).
- The alert threshold on the predicted probability is chosen on the TRAINING winters only
  (out-of-fold predictions), as the highest one that still catches RECALL_TARGET of their
  unhealthy days: missing a smog day is worse for people than an extra warning.
- Every score is on a winter the model did not see (leave-one-season-out).
- Two feature sets, as in smog.train: known at forecast time, and + perfect weather for tomorrow
  (observed ERA5 daily values); real skill lies in between.

Usage:
    py -m smog.features
    py -m smog.alerts            # evaluation
    py -m smog.alerts --save     # train on all winters -> models/alert.pkl for smog.forecast
"""
import csv
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

from . import config
from .evaluate_cams import load_cams
from .features import WEATHER_COLS, load_weather

DATA_CUTOFF_UTC_HOUR = 23   # last hour the morning forecast can see: 05:00 Bishkek (UTC+6), previous UTC day
LOCAL = timedelta(hours=6)
UNHEALTHY = 55.4
ALERT_HOURS = 3
MIN_HOURS = 18              # a day needs this many city hours to be scored
RECALL_TARGET = 0.8
KNOWN_COLS_SKIP = {"timestamp", "season", "hour_local", "y_24", "y_48", "weekday_24", "weekday_48"}


def build_days() -> list[dict]:
    """One row per forecast made on day D: features at the data cutoff (23:00 UTC on D-1),
    targets for day D+1 (Bishkek time)."""
    with (config.ROOT / "data" / "features.csv").open(encoding="utf-8") as f:
        table = {datetime.fromisoformat(r["timestamp"]): r for r in csv.DictReader(f)}
    city = {t: float(r["pm_now"]) for t, r in table.items()}
    known = [c for c in next(iter(table.values())) if c not in KNOWN_COLS_SKIP and not c.startswith(("era5_", "cams_"))]

    days = []
    for year in config.SEASONS:
        start, end = config.season_range(year)
        weather = load_weather(start, end)
        cams = {datetime.fromisoformat(h): v for h, v in load_cams(start, end).items()}
        d = start
        while d < end:
            issue = datetime(d.year, d.month, d.day) - timedelta(hours=24 - DATA_CUTOFF_UTC_HOUR)
            # Local day D+1 = UTC 18:00 on D .. 17:00 on D+1.
            hours = [datetime(d.year, d.month, d.day, 18) + timedelta(hours=k) for k in range(24)]
            values = [city[h] for h in hours if h in city]
            row = table.get(issue)
            d += timedelta(days=1)
            if row is None or len(values) < MIN_HOURS:
                continue
            day = {"date": (hours[0] + LOCAL).date().isoformat(), "season": year}
            day.update({c: float(row[c]) if row[c] != "" else math.nan for c in known})
            day["weekday_tomorrow"] = (hours[0] + LOCAL).weekday()
            # Perfect weather: observed ERA5 over tomorrow, plus CAMS for tomorrow.
            for col in WEATHER_COLS:
                vals = [weather[h][col] for h in hours if col in weather.get(h, {})]
                day[f"era5_day_{col}"] = statistics.mean(vals) if vals else math.nan
            blh = [weather[h]["boundary_layer_height"] for h in hours if "boundary_layer_height" in weather.get(h, {})]
            day["era5_day_blh_min"] = min(blh) if blh else math.nan
            cams_day = [cams[h] for h in hours if h in cams]
            day["cams_day_mean"] = statistics.mean(cams_day) if cams_day else math.nan
            day["cams_day_unhealthy_h"] = sum(v >= UNHEALTHY for v in cams_day)
            # Baselines' inputs: the 24 hours before the forecast.
            past = [city[h] for h in (issue - timedelta(hours=k) for k in range(24)) if h in city]
            day["past_unhealthy_h"] = sum(v >= UNHEALTHY for v in past)
            # Targets.
            day["y_unhealthy_h"] = sum(v >= UNHEALTHY for v in values)
            day["y_alert"] = int(day["y_unhealthy_h"] >= ALERT_HOURS)
            day["y_mean"] = statistics.mean(values)
            days.append(day)
    return days


def feature_sets(days: list[dict]) -> dict[str, list[str]]:
    cols = [c for c in days[0] if c not in ("date", "season") and not c.startswith(("y_", "past_"))]
    known = [c for c in cols if not c.startswith(("era5_", "cams_"))]
    return {"at forecast time": known, "+ perfect weather": cols}


def classifier():
    return HistGradientBoostingClassifier(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                          min_samples_leaf=20, l2_regularization=1.0,
                                          early_stopping=False, random_state=0)


def regressor():
    return HistGradientBoostingRegressor(max_iter=200, learning_rate=0.05, max_leaf_nodes=15,
                                         min_samples_leaf=20, l2_regularization=1.0,
                                         early_stopping=False, random_state=0)


def matrix(days, feats, mask):
    return np.array([[d[c] for c in feats] for d, m in zip(days, mask) if m])


def out_of_fold(days, feats, seasons) -> tuple[list[float], list[int]]:
    """Probabilities for each day of `seasons` from a model trained on the other `seasons`."""
    probs, ys = [], []
    for s in seasons:
        fit = [d["season"] in seasons and d["season"] != s for d in days]
        val = [d["season"] == s for d in days]
        m = classifier().fit(matrix(days, feats, fit), [d["y_alert"] for d, f in zip(days, fit) if f])
        probs += list(m.predict_proba(matrix(days, feats, val))[:, 1])
        ys += [d["y_alert"] for d, v in zip(days, val) if v]
    return probs, ys


def choose_threshold(days, feats, train_seasons) -> float:
    """Highest probability threshold that still catches RECALL_TARGET of the unhealthy days,
    judged on out-of-fold predictions inside the training winters only."""
    probs, ys = out_of_fold(days, feats, train_seasons)
    bad = sorted(p for p, y in zip(probs, ys) if y)
    return bad[int(math.floor((1 - RECALL_TARGET) * len(bad)))]


MODEL_PATH = config.ROOT / "models" / "alert.pkl"
RISK_LEVELS = [(0.3, "low"), (0.6, "medium"), (1.01, "high")]  # on the calibrated probability


def save_model() -> None:
    """Train on all winters and save what the daily job needs (smog.forecast).

    - classifier + alert threshold (chosen out-of-fold, as in the evaluation);
    - calibrator: isotonic map from the raw probability to the share of unhealthy days actually
      seen at that probability, fitted on out-of-fold predictions (the raw one is overconfident).
      Its honesty is only proven by the live forecasts in forecasts/forecasts.csv;
    - regressor for tomorrow's mean PM2.5.
    """
    import json
    import pickle
    import sklearn
    from sklearn.isotonic import IsotonicRegression

    days = build_days()
    seasons = sorted({d["season"] for d in days})
    feats = feature_sets(days)["at forecast time"]
    probs, ys = out_of_fold(days, feats, seasons)
    calibrator = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(probs, ys)
    everything = [True] * len(days)
    bundle = {
        "features": feats,
        "classifier": classifier().fit(matrix(days, feats, everything), [d["y_alert"] for d in days]),
        "threshold": choose_threshold(days, feats, seasons),
        "calibrator": calibrator,
        "regressor": regressor().fit(matrix(days, feats, everything), [d["y_mean"] for d in days]),
    }
    meta = {
        "trained_on_seasons": seasons, "days": len(days), "threshold": round(bundle["threshold"], 3),
        "unhealthy_day": f">= {ALERT_HOURS} h with city PM2.5 >= {UNHEALTHY}",
        "data_cutoff": f"{DATA_CUTOFF_UTC_HOUR}:00 UTC of the previous day", "features": feats,
        "risk_levels_on_calibrated_probability": RISK_LEVELS,
        "sklearn": sklearn.__version__, "numpy": np.__version__,
    }
    MODEL_PATH.parent.mkdir(exist_ok=True)
    with MODEL_PATH.open("wb") as f:
        pickle.dump(bundle, f)
    MODEL_PATH.with_suffix(".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Saved {MODEL_PATH} (threshold {bundle['threshold']:.3f}, {len(days)} days, seasons {seasons})")


def alert_scores(alert: list[bool], y: list[int]) -> dict:
    caught = sum(a and t for a, t in zip(alert, y))
    false = sum(a and not t for a, t in zip(alert, y))
    return {"bad": sum(y), "caught": caught, "good": len(y) - sum(y), "false": false,
            "precision": caught / max(caught + false, 1)}


def alert_line(name: str, s: dict) -> str:
    return (f"  {name:30s} caught {s['caught']:3d}/{s['bad']:3d} unhealthy days ({s['caught'] / max(s['bad'], 1):4.0%}), "
            f"false alarms {s['false']:3d}/{s['good']:3d}; when it warns, right {s['precision']:4.0%}")


def main() -> None:
    import sys
    if "--save" in sys.argv:
        return save_model()
    days = build_days()
    seasons = sorted({d["season"] for d in days})
    print(f"Days scored: {len(days)}; unhealthy (>= {ALERT_HOURS} h >= {UNHEALTHY}): "
          f"{sum(d['y_alert'] for d in days)} ({sum(d['y_alert'] for d in days) / len(days):.0%})")

    pooled = defaultdict(lambda: {"alert": [], "y": [], "prob": [], "mean_pred": [], "mean_y": []})
    for test in seasons:
        train = [s for s in seasons if s != test]
        tr = [d["season"] in train for d in days]
        te = [d["season"] == test for d in days]
        y_te = [d["y_alert"] for d, m in zip(days, te) if m]
        mean_te = [d["y_mean"] for d, m in zip(days, te) if m]
        test_days = [d for d, m in zip(days, te) if m]

        print(f"\n#### Test winter {test}/{str(test + 1)[2:]} ({len(y_te)} days, {sum(y_te)} unhealthy); model trained on {train}")
        base = {
            "persistence (>= 3 h in last 24 h)": [d["past_unhealthy_h"] >= ALERT_HOURS for d in test_days],
            "CAMS (>= 3 h forecast)": [d["cams_day_unhealthy_h"] >= ALERT_HOURS for d in test_days],
        }
        for name, alert in base.items():
            print(alert_line(name, alert_scores(alert, y_te)))
            pooled[name]["alert"] += alert
            pooled[name]["y"] += y_te
        for name, mean_pred in [("persistence (mean of last 24 h)", [d["pm_mean24h"] for d in test_days]),
                                ("CAMS", [d["cams_day_mean"] for d in test_days])]:
            pooled[name]["mean_pred"] += mean_pred
            pooled[name]["mean_y"] += mean_te

        for fs_name, feats in feature_sets(days).items():
            threshold = choose_threshold(days, feats, train)
            clf = classifier().fit(matrix(days, feats, tr), [d["y_alert"] for d, m in zip(days, tr) if m])
            prob = list(clf.predict_proba(matrix(days, feats, te))[:, 1])
            alert = [p >= threshold for p in prob]
            name = f"model, {fs_name}"
            print(alert_line(name, alert_scores(alert, y_te)) + f"  [threshold {threshold:.2f}]")
            reg = regressor().fit(matrix(days, feats, tr), [d["y_mean"] for d, m in zip(days, tr) if m])
            p = pooled[name]
            p["alert"] += alert
            p["y"] += y_te
            p["prob"] += prob
            p["mean_pred"] += list(np.clip(reg.predict(matrix(days, feats, te)), 0, None))
            p["mean_y"] += mean_te

    print(f"\n#### All {len(seasons)} winters (each predicted by a model trained on the others)")
    print("Alert 'tomorrow >= 3 unhealthy hours':")
    for name, p in pooled.items():
        if p["alert"]:
            print(alert_line(name, alert_scores(p["alert"], p["y"])))
    print("Tomorrow's mean PM2.5:")
    for name, p in pooled.items():
        if p["mean_pred"]:
            err = [a - b for a, b in zip(p["mean_pred"], p["mean_y"])]
            same = statistics.mean(config.pm25_category(a) == config.pm25_category(b)
                                   for a, b in zip(p["mean_pred"], p["mean_y"]))
            print(f"  {name:30s} MAE={statistics.mean(abs(e) for e in err):5.1f} bias={statistics.mean(err):+5.1f} "
                  f"same category={same:.0%}")
    print("Is the probability honest? (model at forecast time; predicted vs observed share of unhealthy days)")
    p = pooled["model, at forecast time"]
    for lo, hi in [(0, .2), (.2, .4), (.4, .6), (.6, .8), (.8, 1.01)]:
        b = [(q, y) for q, y in zip(p["prob"], p["y"]) if lo <= q < hi]
        if b:
            print(f"  P {lo:.1f}-{min(hi, 1):.1f}: {len(b):3d} days, predicted {statistics.mean(q for q, _ in b):.0%}, "
                  f"observed {statistics.mean(y for _, y in b):.0%}")


if __name__ == "__main__":
    main()
