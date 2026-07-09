# -*- coding: utf-8 -*-
"""
Meta-labeling 特徵定義 — 離線資料集建置與線上影子評分的單一事實來源。

原則：每個特徵在「訊號日收盤後」即可取得，且跨個股可比（比率/百分比/方向，
不用絕對股數金額）。缺值一律留 NaN，模型端用 HistGradientBoosting 原生處理。

特徵說明（單位）：
  rsi            RSI(5)，每日掃描主 RSI
  ret_20d        近 20 日報酬（%）
  cv_sharpe      CV 夏普比率（策略歷史穩定度）
  cv_win_rate    CV 勝率（0~1）
  bias_ma5/10/20 收盤對 MA5/MA10/MA20 乖離（%）
  ma_bull        多頭排列 收盤>MA5>MA20>MA60（1/0）
  mom5 / mom20   近 5 / 20 個掃描日動能（%）
  vol20          近 20 日日報酬標準差（%，波動度）
  dist_high60    收盤距近 60 日最高收盤（%，≤0）
  sector_ret20   所屬族群近 20 日平均報酬（%）
  sector_strong  族群強勢 avg_ret>3（1/0）
  chip_dir       當日三大法人合計買賣超方向（-1/0/1）
  taiex_bull     大盤收盤 > MA60（1/0）
  taiex_bias     大盤收盤對 MA60 乖離（%）

外資/投信明細與連買日數不納入：歷史報告 2026 年中才有這些欄位（缺值率
93% 且與年代強相關），入模會讓模型把「缺值＝舊年代」當訊號（年代洩漏）。
"""
import math

FEATURES = [
    "rsi", "ret_20d", "cv_sharpe", "cv_win_rate",
    "bias_ma5", "bias_ma10", "bias_ma20", "ma_bull",
    "mom5", "mom20", "vol20", "dist_high60",
    "sector_ret20", "sector_strong",
    "chip_dir",
    "taiex_bull", "taiex_bias",
]

NAN = float("nan")


def _num(v):
    """轉 float，None/非數值 → NaN。"""
    try:
        f = float(v)
        return f if math.isfinite(f) else NAN
    except (TypeError, ValueError):
        return NAN


def _sign(v):
    f = _num(v)
    if math.isnan(f):
        return NAN
    return 1.0 if f > 0 else (-1.0 if f < 0 else 0.0)


def _bias_pct(price, ma):
    p, m = _num(price), _num(ma)
    if math.isnan(p) or math.isnan(m) or m == 0:
        return NAN
    return (p - m) / m * 100.0


def build_features(stock: dict, *, sector_ret20=None, sector_strong=None,
                   taiex_bull=None, taiex_bias=None) -> dict:
    """由「個股掃描記錄 + 環境資訊」組出特徵 dict（鍵 = FEATURES）。

    stock 需含（缺值容忍）：price, rsi, ret_20d(%), cv_sharpe, cv_win_rate,
      ma5, ma10, ma20, ma60, mom5, mom20, vol20, dist_high60, chip(dict)
    """
    chip = stock.get("chip") or {}
    price = stock.get("price")
    ma5, ma10 = stock.get("ma5"), stock.get("ma10")
    ma20, ma60 = stock.get("ma20"), stock.get("ma60")

    p, m5, m20, m60 = _num(price), _num(ma5), _num(ma20), _num(ma60)
    if any(math.isnan(x) for x in (p, m5, m20, m60)):
        ma_bull = NAN
    else:
        ma_bull = 1.0 if (p > m5 > m20 > m60) else 0.0

    return {
        "rsi":         _num(stock.get("rsi")),
        "ret_20d":     _num(stock.get("ret_20d")),
        "cv_sharpe":   _num(stock.get("cv_sharpe")),
        "cv_win_rate": _num(stock.get("cv_win_rate")),
        "bias_ma5":    _bias_pct(price, ma5),
        "bias_ma10":   _bias_pct(price, ma10),
        "bias_ma20":   _bias_pct(price, ma20),
        "ma_bull":     ma_bull,
        "mom5":        _num(stock.get("mom5")),
        "mom20":       _num(stock.get("mom20")),
        "vol20":       _num(stock.get("vol20")),
        "dist_high60": _num(stock.get("dist_high60")),
        "sector_ret20":  _num(sector_ret20),
        "sector_strong": _num(sector_strong),
        "chip_dir":   _sign(chip.get("合計")),
        "taiex_bull": _num(taiex_bull),
        "taiex_bias": _num(taiex_bias),
    }
