"""
Qlib 風格 Alpha 因子計算
靈感來源：Microsoft Qlib Alpha158 / Alpha360 特徵集
為避免 Windows 安裝 Qlib 的相依地獄（torch 等），這裡用 pandas 自行實作
輸入統一為 OHLCV DataFrame（欄位：date, open, high, low, close, volume）
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_last(s: pd.Series) -> float | None:
    """取最後一個非 NaN 值"""
    s = s.dropna()
    return float(s.iloc[-1]) if not s.empty else None


def alpha_momentum(df: pd.DataFrame, window: int = 20) -> float | None:
    """MOM(n) = close[t]/close[t-n] - 1  過去 n 日累積報酬"""
    if len(df) < window + 1:
        return None
    c = df["close"]
    return float(c.iloc[-1] / c.iloc[-window - 1] - 1)


def alpha_reversal(df: pd.DataFrame, window: int = 5) -> float | None:
    """短期反轉因子：負的短期報酬（越大代表跌越多、越可能反彈）"""
    m = alpha_momentum(df, window)
    return -m if m is not None else None


def alpha_volatility(df: pd.DataFrame, window: int = 20) -> float | None:
    """20 日日報酬標準差（年化以 sqrt(252)）"""
    if len(df) < window + 1:
        return None
    r = df["close"].pct_change().tail(window)
    return float(r.std() * np.sqrt(252))


def alpha_rsi(df: pd.DataFrame, window: int = 14) -> float | None:
    if len(df) < window + 1:
        return None
    diff = df["close"].diff().tail(window)
    gain = diff.clip(lower=0).mean()
    loss = (-diff.clip(upper=0)).mean()
    if loss == 0:
        return 100.0
    rs = gain / loss
    return float(100 - 100 / (1 + rs))


def alpha_bias(df: pd.DataFrame, window: int = 20) -> float | None:
    """乖離率：(close - MA)/MA"""
    if len(df) < window:
        return None
    ma = df["close"].tail(window).mean()
    return float(df["close"].iloc[-1] / ma - 1)


def alpha_volume_ratio(df: pd.DataFrame, window: int = 20) -> float | None:
    """量比：近 5 日均量 / 過去 n 日均量"""
    if len(df) < window:
        return None
    short = df["volume"].tail(5).mean()
    long_ = df["volume"].tail(window).mean()
    if long_ == 0:
        return None
    return float(short / long_)


def alpha_price_range(df: pd.DataFrame, window: int = 20) -> float | None:
    """位階：(close - Low_n) / (High_n - Low_n)  接近 1 = 近高點"""
    if len(df) < window:
        return None
    tail = df.tail(window)
    hi = tail["high"].max()
    lo = tail["low"].min()
    if hi == lo:
        return None
    return float((df["close"].iloc[-1] - lo) / (hi - lo))


def alpha_ma_cross(df: pd.DataFrame) -> float | None:
    """MA5 / MA20 - 1  ( >0 多頭 )"""
    if len(df) < 20:
        return None
    ma5 = df["close"].tail(5).mean()
    ma20 = df["close"].tail(20).mean()
    return float(ma5 / ma20 - 1)


def alpha_max_drawdown(df: pd.DataFrame, window: int = 60) -> float | None:
    """過去 n 日最大回撤"""
    if len(df) < window:
        return None
    c = df["close"].tail(window)
    roll_max = c.cummax()
    dd = c / roll_max - 1
    return float(dd.min())


FACTOR_FUNCS = {
    "MOM20（20日動能）": alpha_momentum,
    "REV5（5日反轉）": alpha_reversal,
    "VOL20（20日波動率）": alpha_volatility,
    "RSI14": alpha_rsi,
    "BIAS20（20日乖離）": alpha_bias,
    "VR20（量比）": alpha_volume_ratio,
    "POS20（20日位階）": alpha_price_range,
    "MA5/MA20": alpha_ma_cross,
    "MDD60（最大回撤）": alpha_max_drawdown,
}


def compute_all_factors(df: pd.DataFrame) -> dict[str, float | None]:
    """一次計算全部因子"""
    out = {}
    for name, fn in FACTOR_FUNCS.items():
        try:
            out[name] = fn(df)
        except Exception:
            out[name] = None
    return out


def factor_description() -> dict[str, str]:
    return {
        "MOM20（20日動能）": "過去 20 日累積報酬；> 0 代表中期多頭。",
        "REV5（5日反轉）": "近 5 日跌幅越深（因子值越大）代表反彈機會較高。",
        "VOL20（20日波動率）": "年化日報酬標準差；>0.5 代表高波動。",
        "RSI14": "相對強弱指數；>70 超買，<30 超賣。",
        "BIAS20（20日乖離）": "現價相對 20 日均線偏離程度；過高可能回落。",
        "VR20（量比）": "近 5 日均量 / 近 20 日均量；>1.5 代表爆量。",
        "POS20（20日位階）": "0~1，越接近 1 代表在 20 日高檔區。",
        "MA5/MA20": "短均線 / 長均線；> 0 為多頭排列。",
        "MDD60（最大回撤）": "過去 60 日從高點的最大跌幅；越接近 0 越健康。",
    }
