# -*- coding: utf-8 -*-
"""
Meta-labeling 影子評分介面 — daily_scan 每日呼叫。

score_signal(features) 回傳該 BUY 訊號的預估勝率（0~1）；
模型檔缺失或特徵異常時回傳 None，呼叫端顯示「—」即可，絕不拋錯
（影子模式：評分失敗不得影響掃描與訊號）。
"""
import math
import pickle
from pathlib import Path

import numpy as np

from model.meta_features import FEATURES, build_features  # noqa: F401（re-export 供呼叫端組特徵）

_MODEL_PKL = Path(__file__).resolve().parent / "meta_model.pkl"
_cache: dict = {}


def _load():
    if "bundle" not in _cache:
        with open(_MODEL_PKL, "rb") as f:
            _cache["bundle"] = pickle.load(f)
    return _cache["bundle"]


def model_info() -> dict | None:
    """回傳模型摘要（訓練時間 / 樣本數 / OOF 指標），無模型 → None。"""
    try:
        b = _load()
        return {k: b[k] for k in
                ("trained_at", "n_train", "oof_auc", "oof_baseline", "oof_policies")
                if k in b}
    except Exception:
        return None


def score_signal(features: dict) -> float | None:
    """features：model.meta_features.build_features() 的輸出 dict。"""
    try:
        bundle = _load()
        row = np.array([[float(features.get(k, math.nan)) for k in bundle["features"]]])
        prob = bundle["model"].predict_proba(row)[0, 1]
        return round(float(prob), 3)
    except Exception:
        return None
