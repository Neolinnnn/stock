import pandas as pd
import numpy as np
import pytest
from indicators.technical import compute_indicators, detect_mj_signals


def _make_df(kd_j_vals, macd_osc_vals):
    """建立最小測試 DataFrame，直接指定 kd_j 與 macd_osc。"""
    n = len(kd_j_vals)
    df = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=n).astype(str),
        "close": [100.0] * n,
        "high":  [102.0] * n,
        "low":   [98.0] * n,
        "kd_j":      kd_j_vals,
        "macd_osc":  macd_osc_vals,
    })
    return df


def test_long_signal_detected():
    """J 從負穿正且 OSC 正值 → 做多訊號"""
    df = _make_df(
        kd_j_vals=  [-5.0, -2.0,  3.0, 10.0],
        macd_osc_vals=[ 0.1,  0.2,  0.5,  0.3],
    )
    signals = detect_mj_signals(df)
    assert len(signals) == 1
    assert signals.iloc[0]["signal"] == "LONG"
    assert signals.iloc[0]["date"] == "2025-01-03"


def test_short_signal_detected():
    """J 從正穿負且 OSC 負值 → 做空訊號"""
    df = _make_df(
        kd_j_vals=  [10.0,  5.0, -2.0, -8.0],
        macd_osc_vals=[-0.1, -0.3, -0.5, -0.2],
    )
    signals = detect_mj_signals(df)
    assert len(signals) == 1
    assert signals.iloc[0]["signal"] == "SHORT"
    assert signals.iloc[0]["date"] == "2025-01-03"


def test_no_signal_when_osc_wrong_direction():
    """J 向上穿零但 OSC 為負 → 動能不足，不進場"""
    df = _make_df(
        kd_j_vals=  [-5.0,  3.0],
        macd_osc_vals=[-0.3, -0.1],
    )
    signals = detect_mj_signals(df)
    assert len(signals) == 0


def test_no_signal_when_no_crossover():
    """J 一直在零軸同側 → 無訊號"""
    df = _make_df(
        kd_j_vals=  [2.0, 5.0, 8.0],
        macd_osc_vals=[0.1, 0.2, 0.3],
    )
    signals = detect_mj_signals(df)
    assert len(signals) == 0


def test_return_columns():
    """回傳 DataFrame 含必要欄位"""
    df = _make_df([-3.0, 4.0], [0.2, 0.5])
    signals = detect_mj_signals(df)
    for col in ["date", "signal", "close", "kd_j", "macd_osc"]:
        assert col in signals.columns
