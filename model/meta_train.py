# -*- coding: utf-8 -*-
"""
Meta-labeling 訓練 — 學「哪些 BUY 訊號會贏」，不預測市場方向
==============================================================
輸入：model/signal_dataset.csv（scripts/build_signal_dataset.py 產出）
輸出：model/meta_model.pkl（模型 bundle）+ model/meta_report.json（驗證報告）

設計：
  - 二分類：HYBRID 結案 WIN(1)/LOSS(0)，與實際交易目標直接對齊。
  - HistGradientBoostingClassifier：原生處理 NaN（歷史欄位覆蓋不齊），
    淺樹 + 強正則以對抗小樣本過擬合。
  - 滾動窗訓練：只用最近 TRAIN_WINDOW 筆結案樣本。2026-07 掃描窗口
    150~450 的結果：150~300 AUC 穩定 0.60±0.03、350 以上單調衰退至 0.52
    （擴張窗全歷史更差，AUC 0.46）——市場 regime 會漂移，舊年代樣本反而
    是雜訊，取平台區中段 200。
  - Walk-forward 驗證：按訊號日期排序，前 40% 起始，其餘切 5 段逐段
    外推，任何指標都來自「模型沒看過的未來」。
  - 評估看交易指標（篩選後勝率 / 平均報酬 / 保留率），AUC 僅參考。

用法：
    python model/meta_train.py
"""
import json
import pickle
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from model.meta_features import FEATURES  # noqa: E402

MODEL_DIR   = Path(__file__).resolve().parent
DATASET_CSV = MODEL_DIR / "signal_dataset.csv"
MODEL_PKL   = MODEL_DIR / "meta_model.pkl"
REPORT_JSON = MODEL_DIR / "meta_report.json"

N_TEST_FOLDS = 5
INIT_TRAIN_FRAC = 0.4
TRAIN_WINDOW = 200              # 滾動訓練窗（最近 N 筆結案樣本）
THRESHOLDS = [0.5, 0.55, 0.6]   # 影子模式觀察用的信心門檻


def _make_model():
    return HistGradientBoostingClassifier(
        max_depth=3, learning_rate=0.06, max_iter=200,
        min_samples_leaf=25, l2_regularization=1.0,
        random_state=42,
    )


def load_settled():
    df = pd.read_csv(DATASET_CSV, dtype={"stock_id": str, "signal_date": str})
    settled = df[df["label"].notna()].copy()
    settled["label"] = settled["label"].astype(int)
    settled = settled.sort_values("signal_date").reset_index(drop=True)
    return settled


def _policy_stats(y, ret, prob, thr):
    """門檻 thr 下的交易指標：保留率 / 勝率 / 平均報酬。"""
    sel = prob >= thr
    n = int(sel.sum())
    if n == 0:
        return {"threshold": thr, "kept": 0, "kept_pct": 0.0,
                "win_rate": None, "avg_return": None}
    return {
        "threshold": thr,
        "kept": n,
        "kept_pct": round(n / len(y) * 100, 1),
        "win_rate": round(float(y[sel].mean()), 4),
        "avg_return": round(float(ret[sel].mean()), 2),
    }


def walk_forward(df):
    """外推驗證：回傳 OOF 預測（與 df 對齊，訓練窗內為 NaN）與逐段摘要。"""
    n = len(df)
    X = df[FEATURES].astype(float).values
    y = df["label"].values
    oof = np.full(n, np.nan)
    start = int(n * INIT_TRAIN_FRAC)
    bounds = np.linspace(start, n, N_TEST_FOLDS + 1).astype(int)
    folds = []
    for k in range(N_TEST_FOLDS):
        lo, hi = bounds[k], bounds[k + 1]
        if hi <= lo:
            continue
        tr_lo = max(0, lo - TRAIN_WINDOW)
        model = _make_model()
        model.fit(X[tr_lo:lo], y[tr_lo:lo])
        prob = model.predict_proba(X[lo:hi])[:, 1]
        oof[lo:hi] = prob
        folds.append({
            "fold": k + 1,
            "train_n": int(lo - tr_lo),
            "test_range": [df["signal_date"].iloc[lo], df["signal_date"].iloc[hi - 1]],
            "test_n": int(hi - lo),
            "test_base_win_rate": round(float(y[lo:hi].mean()), 4),
        })
    return oof, folds


def train():
    df = load_settled()
    y = df["label"].values
    ret = df["return_pct"].values.astype(float)
    print(f"結案樣本 {len(df)} 筆（{df['signal_date'].iloc[0]} ~ "
          f"{df['signal_date'].iloc[-1]}）")
    print(f"基礎勝率 {y.mean():.1%}、平均報酬 {ret.mean():+.2f}%\n")

    # ── Walk-forward 驗證 ──
    oof, folds = walk_forward(df)
    mask = ~np.isnan(oof)
    y_oof, ret_oof, p_oof = y[mask], ret[mask], oof[mask]

    try:
        auc = round(float(roc_auc_score(y_oof, p_oof)), 4)
    except ValueError:
        auc = None
    baseline = {
        "n": int(mask.sum()),
        "win_rate": round(float(y_oof.mean()), 4),
        "avg_return": round(float(ret_oof.mean()), 2),
    }
    policies = [_policy_stats(y_oof, ret_oof, p_oof, t) for t in THRESHOLDS]

    print(f"Walk-forward OOF：{baseline['n']} 筆、AUC={auc}")
    print(f"  基準（不過濾）      勝率 {baseline['win_rate']:.1%}  "
          f"平均 {baseline['avg_return']:+.2f}%")
    for p in policies:
        if p["win_rate"] is None:
            print(f"  信心 ≥{p['threshold']:.2f}         無樣本")
            continue
        print(f"  信心 ≥{p['threshold']:.2f}  保留 {p['kept_pct']:4.1f}%  "
              f"勝率 {p['win_rate']:.1%}  平均 {p['avg_return']:+.2f}%")

    # ── 最終模型：只用最近 TRAIN_WINDOW 筆訓練（與驗證方式一致）──
    tail = df.tail(TRAIN_WINDOW)
    model = _make_model()
    model.fit(tail[FEATURES].astype(float).values, tail["label"].values)

    bundle = {
        "model": model,
        "features": FEATURES,
        "trained_at": datetime.now().isoformat(timespec="seconds"),
        "n_train": len(tail),
        "train_window": TRAIN_WINDOW,
        "dataset_range": [df["signal_date"].iloc[0], df["signal_date"].iloc[-1]],
        "oof_auc": auc,
        "oof_baseline": baseline,
        "oof_policies": policies,
    }
    with open(MODEL_PKL, "wb") as f:
        pickle.dump(bundle, f)

    report = {k: v for k, v in bundle.items() if k != "model"}
    report["folds"] = folds
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"\n模型 → {MODEL_PKL}")
    print(f"報告 → {REPORT_JSON}")
    return bundle


if __name__ == "__main__":
    train()
