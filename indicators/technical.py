import pandas as pd
import numpy as np
from scipy.signal import find_peaks


# ── 指標計算 ──────────────────────────────────────────────────────────────────

def _ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """計算所有技術指標，回傳含新欄位的 DataFrame。"""
    df = df.copy()

    # Bollinger Bands (20, 2)
    df["ma20"]     = df["close"].rolling(20).mean()
    _std20         = df["close"].rolling(20).std()
    df["bb_upper"] = df["ma20"] + 2 * _std20
    df["bb_mid"]   = df["ma20"]
    df["bb_lower"] = df["ma20"] - 2 * _std20
    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / (df["bb_mid"] + 1e-9)

    # KD Stochastic (9, 3, 3)
    _low9  = df["low"].rolling(9).min()
    _high9 = df["high"].rolling(9).max()
    _rsv   = (df["close"] - _low9) / (_high9 - _low9 + 1e-9) * 100
    df["kd_k"] = _rsv.ewm(com=2, adjust=False).mean()   # 1/3 smoothing = com=2
    df["kd_d"] = df["kd_k"].ewm(com=2, adjust=False).mean()
    df["kd_j"] = 3 * df["kd_k"] - 2 * df["kd_d"]

    # MACD (12, 26, 9)
    _ema12            = _ema(df["close"], 12)
    _ema26            = _ema(df["close"], 26)
    df["macd_dif"]    = _ema12 - _ema26
    df["macd_signal"] = _ema(df["macd_dif"], 9)
    df["macd_osc"]    = df["macd_dif"] - df["macd_signal"]

    # MA
    df["ma5"]  = df["close"].rolling(5).mean()
    # ma20 already computed above
    df["ma60"] = df["close"].rolling(60).mean()

    # RSI (14)
    _delta = df["close"].diff()
    _gain  = _delta.clip(lower=0)
    _loss  = (-_delta).clip(lower=0)
    _avg_g = _gain.ewm(com=13, adjust=False).mean()
    _avg_l = _loss.ewm(com=13, adjust=False).mean()
    df["rsi14"] = 100 - 100 / (1 + _avg_g / (_avg_l + 1e-9))

    # Volume MA
    df["vol_ma20"] = df["volume"].rolling(20).mean()

    return df


# ── 技術分析總覽 ───────────────────────────────────────────────────────────────

def technical_summary(df: pd.DataFrame) -> list[dict]:
    """
    回傳 8 項技術分析摘要，每項含 label / value / direction。
    direction 值域：'up' | 'down' | 'neutral'
    """
    row  = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else row
    close = row["close"]

    bb_range = row["bb_upper"] - row["bb_lower"]
    bb_pos   = (close - row["bb_lower"]) / (bb_range + 1e-9)

    items = []

    # 1. 趨勢方向
    if row["ma5"] > row["ma20"] > row["ma60"]:
        items.append({"label": "趨勢方向", "value": "多頭趨勢", "direction": "up"})
    elif row["ma5"] < row["ma20"] < row["ma60"]:
        items.append({"label": "趨勢方向", "value": "空頭趨勢", "direction": "down"})
    else:
        items.append({"label": "趨勢方向", "value": "盤整趨勢", "direction": "neutral"})

    # 2. 價格位置
    if bb_pos > 0.85:
        items.append({"label": "價格位置", "value": "高檔整理（貼近上軌）", "direction": "down"})
    elif bb_pos >= 0.5:
        items.append({"label": "價格位置", "value": "中軌以上", "direction": "up"})
    elif bb_pos >= 0.15:
        items.append({"label": "價格位置", "value": "中軌以下", "direction": "neutral"})
    else:
        items.append({"label": "價格位置", "value": "低檔整理（貼近下軌）", "direction": "up"})

    # 3. 均線排列
    if row["ma5"] > row["ma20"] > row["ma60"]:
        items.append({"label": "均線排列", "value": "多頭排列（5>20>60）", "direction": "up"})
    elif row["ma5"] < row["ma20"] < row["ma60"]:
        items.append({"label": "均線排列", "value": "空頭排列（5<20<60）", "direction": "down"})
    else:
        items.append({"label": "均線排列", "value": "多空交錯", "direction": "neutral"})

    # 4. 量價關係
    vol_ratio = row["volume"] / (row["vol_ma20"] + 1e-9)
    if vol_ratio > 1.5 and close > prev["close"]:
        items.append({"label": "量價關係", "value": "放量上漲，動能強勁", "direction": "up"})
    elif vol_ratio < 0.7:
        items.append({"label": "量價關係", "value": "量縮整理，動能降溫", "direction": "neutral"})
    elif vol_ratio > 1.5 and close < prev["close"]:
        items.append({"label": "量價關係", "value": "放量下跌，注意風險", "direction": "down"})
    else:
        items.append({"label": "量價關係", "value": "量能平穩", "direction": "neutral"})

    # 5. 布林位置
    if bb_pos > 0.9:
        items.append({"label": "布林位置", "value": "貼近上軌（過熱）", "direction": "down"})
    elif bb_pos < 0.1:
        items.append({"label": "布林位置", "value": "貼近下軌（超跌）", "direction": "up"})
    else:
        items.append({"label": "布林位置", "value": f"中軌區間（{bb_pos:.0%}）", "direction": "neutral"})

    # 6. 布林通道開口
    prev5_width = df.iloc[-5]["bb_width"] if len(df) >= 5 else row["bb_width"]
    if row["bb_width"] > prev5_width * 1.1:
        items.append({"label": "布林通道", "value": "開口擴大（趨勢加速）", "direction": "up"})
    elif row["bb_width"] < prev5_width * 0.9:
        items.append({"label": "布林通道", "value": "開口收斂（蓄勢待發）", "direction": "neutral"})
    else:
        items.append({"label": "布林通道", "value": "開口平穩", "direction": "neutral"})

    # 7. KD 狀態
    if row["kd_k"] > 80:
        items.append({"label": "KD狀態", "value": "高檔鈍化", "direction": "down"})
    elif row["kd_k"] < 20:
        items.append({"label": "KD狀態", "value": "低檔鈍化", "direction": "up"})
    elif row["kd_k"] > row["kd_d"] and prev["kd_k"] <= prev["kd_d"]:
        items.append({"label": "KD狀態", "value": "黃金交叉", "direction": "up"})
    elif row["kd_k"] < row["kd_d"] and prev["kd_k"] >= prev["kd_d"]:
        items.append({"label": "KD狀態", "value": "死亡交叉", "direction": "down"})
    else:
        items.append({"label": "KD狀態", "value": f"K {row['kd_k']:.1f} / D {row['kd_d']:.1f}", "direction": "neutral"})

    # 8. 綜合評估
    up_count = sum(1 for i in items if i["direction"] == "up")
    dn_count = sum(1 for i in items if i["direction"] == "down")
    if up_count >= 5:
        conclusion, direction = "主升段延續", "up"
    elif dn_count >= 5:
        conclusion, direction = "弱勢下跌", "down"
    elif bb_pos > 0.8 and row["kd_k"] > 70:
        conclusion, direction = "高檔震盪出貨初期", "down"
    elif bb_pos < 0.25 and row["kd_k"] < 35:
        conclusion, direction = "底部蓄積", "up"
    else:
        conclusion, direction = "盤整觀望", "neutral"
    items.append({"label": "綜合評估", "value": conclusion, "direction": direction})

    return items


# ── 關鍵價位 ───────────────────────────────────────────────────────────────────

def key_levels(df: pd.DataFrame) -> dict:
    """回傳壓力、回檔、支撐、跌破、突破等關鍵價位。"""
    row    = df.iloc[-1]
    high60 = df["close"].tail(60).max()

    r_lo = round(row["bb_upper"] * 0.995 / 10) * 10
    r_hi = round(row["bb_upper"] * 1.005 / 10) * 10
    bb_lo = round(row["bb_lower"] * 0.99  / 100) * 100
    bb_hi = round(row["bb_lower"] * 1.01  / 100) * 100

    return {
        "resistance": f"{r_lo:.0f} ～ {r_hi:.0f}",
        "pullback":   f"{row['ma20'] * 0.975:.0f} ～ {row['ma20'] * 1.025:.0f}",
        "support":    f"{bb_lo:.0f} ～ {bb_hi:.0f}",
        "breakdown":  round(row["ma20"], 0),
        "breakout":   round(high60, 0),
    }


# ── 型態偵測 ───────────────────────────────────────────────────────────────────

def detect_patterns(df: pd.DataFrame) -> dict:
    """
    偵測近 60 根 K 棒的 W 底（雙底）與 M 頭（雙頂）型態。
    回傳 {'w_bottom': {'formed': bool, 'reason': str}, 'm_top': {...}}
    """
    closes = df["close"].tail(60).values
    std    = closes.std()

    lows_idx,  _ = find_peaks(-closes, distance=5, prominence=std * 0.3)
    highs_idx, _ = find_peaks( closes, distance=5, prominence=std * 0.3)

    w = {"formed": False, "reason": "右肩未完成，且頸線未突破"}
    m = {"formed": False, "reason": "右峰未確認，且尚未跌破頸線"}

    if len(lows_idx) >= 2:
        l1, l2 = lows_idx[-2], lows_idx[-1]
        if closes[l2] > closes[l1] * 0.97:
            neckline = closes[l1:l2].max()
            if closes[-1] > neckline:
                w = {"formed": True,  "reason": "雙底確認，已突破頸線"}
            else:
                w = {"formed": False, "reason": "右低已形成，等待突破頸線"}

    if len(highs_idx) >= 2:
        h1, h2 = highs_idx[-2], highs_idx[-1]
        if closes[h2] < closes[h1] * 1.03:
            neckline = closes[h1:h2].min()
            if closes[-1] < neckline:
                m = {"formed": True,  "reason": "雙頂確認，已跌破頸線"}
            else:
                m = {"formed": False, "reason": "右峰已形成，尚未跌破頸線"}

    return {"w_bottom": w, "m_top": m}
