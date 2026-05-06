import pickle
from pathlib import Path

import pandas as pd

_MODEL_PATH = Path(__file__).parent / "rf_model.pkl"
_cache: dict = {}


def _load() -> dict:
    if "model" not in _cache:
        with open(_MODEL_PATH, "rb") as f:
            _cache.update(pickle.load(f))
    return _cache


def _predict_from_row(row_df: pd.DataFrame) -> dict:
    bundle = _load()
    clf      = bundle["model"]
    features = bundle["features"]
    accuracy = bundle["accuracy"]

    proba   = clf.predict_proba(row_df[features])[0]
    classes = list(clf.classes_)
    prob    = dict(zip(classes, proba))

    return {
        "up":       round(prob.get(1,  0.0), 2),
        "sideways": round(prob.get(0,  0.0), 2),
        "down":     round(prob.get(-1, 0.0), 2),
        "accuracy": round(accuracy, 2),
    }


def predict(df: pd.DataFrame, chip_raw: pd.DataFrame,
            margin_raw: pd.DataFrame | None = None) -> dict:
    try:
        from model.train import build_features, FEATURES
        import numpy as np
        built = build_features(df, chip_raw, margin_raw)
        built = built.replace([np.inf, -np.inf], np.nan).dropna()
        if built.empty:
            raise ValueError("empty after feature building")
        return _predict_from_row(built.tail(1))
    except FileNotFoundError:
        return {"up": 0.33, "sideways": 0.34, "down": 0.33, "accuracy": 0.0,
                "error": "model not trained — run python model/train.py first"}
    except Exception as e:
        return {"up": 0.33, "sideways": 0.34, "down": 0.33, "accuracy": 0.0, "error": str(e)}
