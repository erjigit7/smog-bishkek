"""First forecast model: city PM2.5 at +24h / +48h, gradient boosting (scikit-learn).

Usage:
    pip install -r requirements.txt
    py -m smog.features
    py -m smog.train

Every score is on a winter the model did not see:
- main test: train on 2022/23..2024/25, predict 2025/26 (the latest winter);
- leave-one-season-out: each winter predicted by a model trained on the other three.

Two feature sets (see smog/features.py):
- "at forecast time": only what is known when the forecast is issued -> honest lower bound;
- "+ perfect weather": adds observed weather and CAMS at the target hour -> upper bound.
Real skill, with a real weather forecast, lies in between.
Baselines on the same rows: persistence (PM2.5 at t+h = now) and CAMS.
"""
import csv
import math

import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.inspection import permutation_importance

from . import config

HORIZONS = [24, 48]
UNHEALTHY = 55.4
SKIP = {"timestamp", "season", "y_24", "y_48"}


def load_table() -> tuple[list[str], np.ndarray, list[str], list[int]]:
    with (config.ROOT / "data" / "features.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cols = [c for c in rows[0] if c not in ("timestamp",)]
    data = np.array([[float(r[c]) if r[c] != "" else math.nan for c in cols] for r in rows])
    return cols, data, [r["timestamp"] for r in rows], [int(r["season"]) for r in rows]


def feature_sets(cols: list[str], h: int) -> dict[str, list[str]]:
    known = [c for c in cols if c not in SKIP and not c.startswith(("era5_", "cams_", "weekday_"))]
    known.append(f"weekday_{h}")
    perfect = known + [c for c in cols if c.startswith((f"era5_{h}_", f"cams_{h}"))]
    return {"at forecast time": known, "+ perfect weather": perfect}


def model() -> HistGradientBoostingRegressor:
    # Fixed, ordinary settings: nothing is tuned on the test winters.
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                         min_samples_leaf=40, l2_regularization=1.0,
                                         early_stopping=False, random_state=0)


def metrics(pred: np.ndarray, y: np.ndarray) -> dict:
    cat = lambda v: [config.pm25_category(x) for x in v]
    bad = y >= UNHEALTHY
    return {
        "n": len(y),
        "mae": float(np.mean(np.abs(pred - y))),
        "bias": float(np.mean(pred - y)),
        "same_cat": float(np.mean([a == b for a, b in zip(cat(pred), cat(y))])),
        "caught": int(np.sum(pred[bad] >= UNHEALTHY)),
        "bad": int(np.sum(bad)),
        "false_alarms": int(np.sum((pred >= UNHEALTHY) & ~bad)),
    }


def line(name: str, m: dict) -> str:
    return (f"  {name:28s} MAE={m['mae']:5.1f} bias={m['bias']:+6.1f} same category={m['same_cat']:4.0%} "
            f"unhealthy caught={m['caught']}/{m['bad']} false alarms={m['false_alarms']}")


def evaluate(cols, data, seasons, train_seasons, test_seasons, h) -> tuple[dict, dict]:
    """({method: (pred, y)} on the test rows, {feature set: (model, X_test, y_test, features)})."""
    idx = {c: i for i, c in enumerate(cols)}
    season = np.array(seasons)
    y = data[:, idx[f"y_{h}"]]
    cams = data[:, idx[f"cams_{h}"]]
    usable = ~np.isnan(y) & ~np.isnan(cams)  # same rows for every method
    train = usable & np.isin(season, train_seasons)
    test = usable & np.isin(season, test_seasons)

    out = {"persistence": (data[test, idx["pm_now"]], y[test]), "CAMS": (cams[test], y[test])}
    fitted = {}
    for name, feats in feature_sets(cols, h).items():
        X = data[:, [idx[c] for c in feats]]
        m = model().fit(X[train], y[train])
        out[f"model, {name}"] = (np.clip(m.predict(X[test]), 0, None), y[test])
        fitted[name] = (m, X[test], y[test], feats)
    return out, fitted


def main() -> None:
    cols, data, _, seasons = load_table()
    all_seasons = sorted(set(seasons))
    summary = []

    for h in HORIZONS:
        test_season = all_seasons[-1]
        res, fitted = evaluate(cols, data, seasons, all_seasons[:-1], [test_season], h)
        print(f"\n#### +{h}h, main test: train {all_seasons[:-1]}, test {test_season}/{str(test_season + 1)[2:]}")
        for name, (pred, y) in res.items():
            m = metrics(pred, y)
            print(line(name, m))
            summary.append((f"+{h}h", f"{test_season}/{str(test_season + 1)[2:]}", name, m))

        if h == 24:
            m, X, y, feats = fitted["at forecast time"]
            imp = permutation_importance(m, X, y, scoring="neg_mean_absolute_error", n_repeats=3, random_state=0)
            order = np.argsort(-imp.importances_mean)[:10]
            print("  Most useful inputs (at forecast time, +24h; MAE increase when shuffled):")
            for i in order:
                print(f"    {feats[i]:28s} +{imp.importances_mean[i]:.1f}")

        print(f"\n#### +{h}h, leave-one-season-out (each winter predicted by a model trained on the others)")
        pooled = {}
        for s in all_seasons:
            res, _ = evaluate(cols, data, seasons, [x for x in all_seasons if x != s], [s], h)
            for name, (pred, y) in res.items():
                p, t = pooled.get(name, (np.array([]), np.array([])))
                pooled[name] = (np.concatenate([p, pred]), np.concatenate([t, y]))
                if name.startswith("model, at"):
                    print(f"  {s}/{str(s + 1)[2:]}: " + line(name, metrics(pred, y)).strip())
        print("  All four winters together:")
        for name, (pred, y) in pooled.items():
            m = metrics(pred, y)
            print(line(name, m))
            summary.append((f"+{h}h", "all 4 (LOSO)", name, m))

    print("\n| Horizon | Test | Method | MAE | Bias | Same category | Unhealthy caught | False alarms |")
    print("|---|---|---|---|---|---|---|---|")
    for hz, test, name, m in summary:
        print(f"| {hz} | {test} | {name} | {m['mae']:.1f} | {m['bias']:+.1f} | {m['same_cat']:.0%} "
              f"| {m['caught']} of {m['bad']} | {m['false_alarms']} |")


if __name__ == "__main__":
    main()
