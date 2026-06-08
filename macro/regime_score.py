"""
階段三：彙整 → 最終 regime_score 與部位建議

把階段一（國際指標 risk_score，量化）與階段二（事件面情緒，質化）
融合成單一 regime_score (-100 ~ +100)，並轉成可執行的部位建議。

分工：這是純決策邏輯（Claude 負責）。事件面的「情緒方向」由 Gemini 判讀，
但「方向 → 分數 → 部位」的對應規則寫死在這裡，可被回測與稽核。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

# 階段一 vs 階段二的權重（技術/資金面為主，事件面為輔）
_W_MARKET = 0.7
_W_EVENT = 0.3

_BIAS_SCORE = {"risk_on": 100, "neutral": 0, "risk_off": -100}
_IMPACT_SIGN = {"bullish": 1, "bearish": -1, "neutral": 0}


def _event_score(sentiment: dict[str, Any]) -> tuple[float, str]:
    """
    把 Gemini 事件情緒轉成 -100~+100 的分數。

    優先用個別事件的 impact×severity 加總；若缺，退回 overall_bias。
    """
    if not sentiment or "error" in sentiment:
        return 0.0, "事件面無資料，以中性計"

    events = sentiment.get("events") or []
    if events:
        total = 0.0
        for ev in events:
            sign = _IMPACT_SIGN.get(str(ev.get("impact", "neutral")).lower(), 0)
            sev = float(ev.get("severity", 1) or 1)
            total += sign * sev
        # severity 1~5，數顆事件加總後正規化：除以 (事件數 * 5) * 100
        norm = total / (len(events) * 5.0) * 100.0
        norm = max(-100.0, min(100.0, norm))
        return norm, f"{len(events)} 則事件加權"

    bias = str(sentiment.get("overall_bias", "neutral")).lower()
    return float(_BIAS_SCORE.get(bias, 0)), f"採 overall_bias={bias}"


def _suggest_exposure(score: float) -> dict[str, Any]:
    """regime_score → 建議曝險與操作基調。"""
    if score >= 35:
        return {"exposure_pct": 90, "stance": "積極", "note": "順勢加碼，可放大強勢族群部位"}
    if score >= 10:
        return {"exposure_pct": 70, "stance": "偏多", "note": "標準偏多，聚焦領漲族群"}
    if score > -10:
        return {"exposure_pct": 50, "stance": "中性", "note": "標準部位，嚴設停損"}
    if score > -35:
        return {"exposure_pct": 30, "stance": "保守", "note": "減碼，僅留最強勢標的"}
    return {"exposure_pct": 10, "stance": "避險", "note": "高度避險，現金為王、嚴控曝險"}


def combine(market: dict[str, Any], events: dict[str, Any]) -> dict[str, Any]:
    """
    融合階段一與階段二。

    Args:
        market: market_regime.run() 的輸出
        events: macro_events.run() 的輸出
    """
    market_score = float(market.get("risk_score", 0.0))
    sentiment = (events or {}).get("global_sentiment", {})
    event_score, event_basis = _event_score(sentiment)

    final = _W_MARKET * market_score + _W_EVENT * event_score
    final = round(max(-100.0, min(100.0, final)), 1)

    return {
        "stage": "regime_score",
        "asof": _dt.datetime.now().isoformat(timespec="seconds"),
        "regime_score": final,
        "regime": _label(final),
        "breakdown": {
            "market_score": round(market_score, 1),
            "event_score": round(event_score, 1),
            "event_basis": event_basis,
            "weights": {"market": _W_MARKET, "event": _W_EVENT},
        },
        "suggestion": _suggest_exposure(final),
    }


def _label(score: float) -> str:
    if score >= 35:
        return "risk_on"
    if score >= 10:
        return "mild_risk_on"
    if score > -10:
        return "neutral"
    if score > -35:
        return "mild_risk_off"
    return "risk_off"


if __name__ == "__main__":
    import json
    # 自測：用假資料驗證融合邏輯
    demo_market = {"risk_score": 40}
    demo_events = {"global_sentiment": {
        "events": [
            {"impact": "bearish", "severity": 4},
            {"impact": "neutral", "severity": 2},
        ],
        "overall_bias": "risk_off",
    }}
    print(json.dumps(combine(demo_market, demo_events), ensure_ascii=False, indent=2))
