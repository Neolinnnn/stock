"""
離線訓練腳本。執行一次，輸出 model/rf_model.pkl。

用法：
    cd 台股研究
    python model/train.py
"""
import sys
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators.technical import compute_indicators
from indicators.chip import aggregate_chip

FEATURES = [
    "kd_k", "kd_d", "macd_osc", "bb_pos", "rsi14",
    "ma5_ma20", "ma20_ma60", "vol_ratio",
    "mom1", "mom3", "mom5",
    "chip_foreign_5d", "chip_trust_5d", "chip_dealer_5d",
    "bb_width", "pos60_high", "pos60_low",
    "margin_5d",
]


def build_features(df: pd.DataFrame, chip_raw: pd.DataFrame,
                   margin_raw: pd.DataFrame | None = None) -> pd.DataFrame:
    df = compute_indicators(df).copy()

    chip = aggregate_chip(chip_raw, days=len(chip_raw) + 1)
    chip["date"] = pd.to_datetime(chip["日期"])
    df["date"]   = pd.to_datetime(df["date"])
    df = df.merge(chip[["date", "外資", "投信", "自營"]], on="date", how="left").fillna(0)

    df["chip_foreign_5d"] = df["外資"].rolling(5).sum()
    df["chip_trust_5d"]   = df["投信"].rolling(5).sum()
    df["chip_dealer_5d"]  = df["自營"].rolling(5).sum()

    df["bb_pos"]    = (df["close"] - df["bb_lower"]) / (df["bb_upper"] - df["bb_lower"] + 1e-9)
    df["ma5_ma20"]  = df["ma5"]  / (df["ma20"] + 1e-9)
    df["ma20_ma60"] = df["ma20"] / (df["ma60"] + 1e-9)
    df["vol_ratio"] = df["volume"] / (df["vol_ma20"] + 1e-9)
    df["mom1"]      = df["close"].pct_change(1)
    df["mom3"]      = df["close"].pct_change(3)
    df["mom5"]      = df["close"].pct_change(5)
    df["pos60_high"] = df["close"] / (df["close"].rolling(60).max() + 1e-9)
    df["pos60_low"]  = df["close"] / (df["close"].rolling(60).min() + 1e-9)

    if margin_raw is not None and not margin_raw.empty:
        m = margin_raw.copy()
        m["date"] = pd.to_datetime(m["date"])
        m["margin_chg"] = (
            pd.to_numeric(m.get("MarginPurchaseBuy", 0), errors="coerce").fillna(0)
            - pd.to_numeric(m.get("MarginPurchaseSell", 0), errors="coerce").fillna(0)
            - pd.to_numeric(m.get("MarginPurchaseCashRepayment", 0), errors="coerce").fillna(0)
        )
        m = m[["date", "margin_chg"]].groupby("date").sum().reset_index()
        df = df.merge(m, on="date", how="left")
        df["margin_5d"] = df["margin_chg"].fillna(0).rolling(5).sum()
    else:
        df["margin_5d"] = 0.0

    df["next_ret"] = df["close"].shift(-1) / df["close"] - 1
    df["label"] = 0
    df.loc[df["next_ret"] >  0.01, "label"] =  1
    df.loc[df["next_ret"] < -0.01, "label"] = -1

    return df


def train(stock_ids: list[str] | None = None):
    from datetime import datetime, timedelta
    from FinMind.data import DataLoader

    if stock_ids is None:
        stock_ids = ["2330", "2317", "2454", "2382", "3711"]

    dl = DataLoader()
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=800)).strftime("%Y-%m-%d")

    all_rows = []
    for sid in stock_ids:
        print(f"Fetching {sid} ...")
        try:
            df_raw = dl.taiwan_stock_daily(stock_id=sid, start_date=start, end_date=end)
            if df_raw.empty:
                print(f"  {sid}: no data, skipped"); continue
            df_raw = df_raw.sort_values("date").rename(columns={"max": "high", "min": "low", "Trading_Volume": "volume"})
            for col in ["open", "high", "low", "close", "volume"]:
                df_raw[col] = pd.to_numeric(df_raw[col], errors="coerce")

            chip_raw   = dl.taiwan_stock_institutional_investors(stock_id=sid, start_date=start, end_date=end)
            margin_raw = dl.taiwan_stock_margin_purchase_short_sale(stock_id=sid, start_date=start, end_date=end)

            built = build_features(df_raw, chip_raw, margin_raw)
            built = built.replace([np.inf, -np.inf], np.nan).dropna()
            all_rows.append(built)
        except Exception as e:
            print(f"  {sid}: error — {e}")

    if not all_rows:
        raise RuntimeError("No data fetched — check FINMIND_TOKEN and internet connection")

    full = pd.concat(all_rows, ignore_index=True)
    X = full[FEATURES]
    y = full["label"]

    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)

    clf = RandomForestClassifier(
        n_estimators=200, max_depth=6, min_samples_leaf=20,
        random_state=42, class_weight="balanced",
    )
    clf.fit(X_train, y_train)

    accuracy = accuracy_score(y_test, clf.predict(X_test))
    print(f"Test accuracy: {accuracy:.2%}")

    model_path = Path(__file__).parent / "rf_model.pkl"
    with open(model_path, "wb") as f:
        pickle.dump({"model": clf, "accuracy": accuracy, "features": FEATURES}, f)
    print(f"Saved → {model_path}")


if __name__ == "__main__":
    train()
