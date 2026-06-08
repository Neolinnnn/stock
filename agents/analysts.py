"""
Claude 邏輯核心：4 位分析師評分 + 多空辯論 + 交易員/風控/投組經理決策

所有評分皆為 -100 ~ +100 的確定性規則（可回測、可稽核），
文字判讀預設用模板，若啟用 Gemini 則改由 gemini_text 產出。
"""
from __future__ import annotations

import re
from typing import Any

# 4 分析師在綜合分數中的權重（技術面為主，補上基本/總經/情緒）
WEIGHTS = {"technical": 0.35, "fundamental": 0.20, "macro": 0.20, "sentiment": 0.25}

_YOY_RE = re.compile(r"年增[率]*[^0-9\-]*?(-?\d+\.?\d*)\s*[%％]")
_HIGH_RE = re.compile(r"高達\s*(-?\d+\.?\d*)\s*[%％]")
_POS_KW = ("創新高", "創歷史新高", "歷史同期新高", "大增", "成長", "登頂", "強勁")
_NEG_KW = ("衰退", "下滑", "虧損", "減少", "年減", "下修", "示警")


def _clamp(x: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def _verdict(score: float) -> str:
    if score >= 30:
        return "偏多"
    if score >= 10:
        return "中性偏多"
    if score > -10:
        return "中性"
    if score > -30:
        return "中性偏空"
    return "偏空"


# ──────────────────────────────────────────────────────────────────────────
# 分析師①  技術面
# ──────────────────────────────────────────────────────────────────────────
def technical_analyst(ind: dict[str, Any]) -> dict[str, Any]:
    """
    TradingAgents 風格技術分析師：跨類別挑互補指標（趨勢/動能/擺盪/波動），
    各給一段「人話判讀」與分數貢獻，對應到走勢圖上的線。

    ind: 最新指標快照
      {close, ma5, ma20, ma60, macd, macd_signal, macd_hist,
       kd_k, kd_d, bb_upper, bb_lower, bb_mid, atr, rsi}
    回傳 {"parts": {...分數貢獻}, "report": [{category, indicator, reading, bias}]}
    """
    parts: dict[str, float] = {}
    report: list[dict[str, Any]] = []

    def add(cat: str, name: str, reading: str, bias: str, pts: float, key: str):
        report.append({"category": cat, "indicator": name,
                       "reading": reading, "bias": bias})
        parts[key] = pts

    def num(k: str):
        v = ind.get(k)
        return float(v) if isinstance(v, (int, float)) else None

    close = num("close")
    ma5, ma20, ma60 = num("ma5"), num("ma20"), num("ma60")

    # 1) 趨勢 — 均線排列（對應圖上 5/20/60MA 三線）
    if None not in (ma5, ma20, ma60):
        if ma5 > ma20 > ma60:
            add("趨勢", "均線排列(5/20/60MA)", "多頭排列：短中長期均線由上而下，趨勢偏多",
                "bullish", 18, "均線排列")
        elif ma5 < ma20 < ma60:
            add("趨勢", "均線排列(5/20/60MA)", "空頭排列：均線由上而下翻空，趨勢偏弱",
                "bearish", -18, "均線排列")
        else:
            add("趨勢", "均線排列(5/20/60MA)", "均線糾結，方向未明，宜等待表態",
                "neutral", 0, "均線排列")
        # 價格相對 20MA
        if close is not None:
            if close > ma20:
                add("趨勢", "價格 vs 20MA", "收盤站上月線，中期趨勢轉強", "bullish", 6, "價格位階")
            else:
                add("趨勢", "價格 vs 20MA", "收盤跌破月線，中期趨勢轉弱", "bearish", -6, "價格位階")

    # 2) 動能 — MACD（線、訊號、柱狀）
    macd, sig, hist = num("macd"), num("macd_signal"), num("macd_hist")
    if None not in (macd, sig):
        if macd > sig and (hist or 0) > 0:
            add("動能", "MACD", "黃金交叉且柱狀翻紅，多頭動能增強", "bullish", 12, "MACD")
        elif macd > sig:
            add("動能", "MACD", "MACD 在訊號線之上，動能偏多", "bullish", 6, "MACD")
        elif macd < sig and (hist or 0) < 0:
            add("動能", "MACD", "死亡交叉且柱狀翻黑，空頭動能增強", "bearish", -12, "MACD")
        else:
            add("動能", "MACD", "MACD 在訊號線之下，動能偏空", "bearish", -6, "MACD")

    # 3) 擺盪 — KD 隨機指標
    k, d = num("kd_k"), num("kd_d")
    if k is not None:
        if k > 80:
            add("擺盪", "KD", f"K={k:.0f} 高檔鈍化(>80)，短線過熱", "bearish", -6, "KD")
        elif k < 20:
            add("擺盪", "KD", f"K={k:.0f} 低檔(<20)，超跌可留意反彈", "bullish", 6, "KD")
        elif d is not None and k > d:
            add("擺盪", "KD", "KD 黃金交叉於中性區，偏多", "bullish", 8, "KD")
        elif d is not None and k < d:
            add("擺盪", "KD", "KD 死亡交叉，偏空", "bearish", -6, "KD")
        else:
            add("擺盪", "KD", "KD 中性", "neutral", 0, "KD")

    # 4) 波動 — 布林通道 %b（對應圖上上/下軌）
    ub, lb = num("bb_upper"), num("bb_lower")
    if None not in (close, ub, lb) and ub > lb:
        pb = (close - lb) / (ub - lb)
        if pb > 0.95:
            add("波動", "布林通道 %b", "貼近上軌，強勢但短線過熱、留意壓回", "bearish", -4, "布林")
        elif pb >= 0.5:
            add("波動", "布林通道 %b", "位於中軌與上軌之間，量價偏強", "bullish", 5, "布林")
        elif pb >= 0.05:
            add("波動", "布林通道 %b", "位於中軌與下軌之間，量價偏弱", "bearish", -2, "布林")
        else:
            add("波動", "布林通道 %b", "貼近下軌，超跌、具均值回歸空間", "bullish", 3, "布林")
        atr = num("atr")
        if atr is not None:
            report.append({"category": "波動", "indicator": "ATR(14)",
                           "reading": f"波動幅度約 {atr:.1f}，可作為停損間距參考",
                           "bias": "neutral"})

    return {"parts": parts, "report": report}


def technical(s: dict[str, Any], ind: dict[str, Any] | None = None) -> dict[str, Any]:
    ret = s.get("ret_20d") or 0.0
    rsi = s.get("rsi") or 50.0
    sharpe = s.get("cv_sharpe") or 0.0
    signal = (s.get("signal") or "HOLD").upper()

    parts: dict[str, float] = {
        "momentum": _clamp(ret * 2, -40, 40),
        "sharpe": _clamp(sharpe * 5, -20, 20),
        "signal": {"BUY": 15, "HOLD": 0, "SELL": -20}.get(signal, 0),
    }
    if rsi < 35:
        parts["rsi"] = 10        # 超賣，具反彈空間
    elif rsi < 50:
        parts["rsi"] = 5
    elif rsi <= 70:
        parts["rsi"] = 0
    else:
        parts["rsi"] = -15       # 超買，追高風險

    # TradingAgents 風格：有指標快照時，疊加跨類別技術訊號
    tech_report: list[dict[str, Any]] = []
    if ind:
        ta = technical_analyst({**ind, "rsi": rsi})
        parts.update(ta["parts"])
        tech_report = ta["report"]

    score = _clamp(sum(parts.values()))
    return {
        "score": round(score, 1),
        "verdict": _verdict(score),
        "signals": {"ret_20d": ret, "rsi": rsi, "cv_sharpe": sharpe, "signal": signal},
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "tech_report": tech_report,
    }


# ──────────────────────────────────────────────────────────────────────────
# 分析師②  基本面（自個股新聞標題抽營收年增）
# ──────────────────────────────────────────────────────────────────────────
def _extract_yoy(news: list[dict[str, Any]], stock_id: str) -> float | None:
    best: float | None = None
    for n in news or []:
        title = n.get("title", "")
        # 優先抓「屬於這檔」的營收速報，否則退而求其次抓族群內最高
        for m in list(_YOY_RE.finditer(title)) + list(_HIGH_RE.finditer(title)):
            try:
                v = float(m.group(1))
            except ValueError:
                continue
            if best is None or v > best:
                best = v
    return best


def fundamental(s: dict[str, Any]) -> dict[str, Any]:
    news = s.get("news") or []
    yoy = _extract_yoy(news, s.get("id", ""))
    titles = " ".join(n.get("title", "") for n in news)

    score = 0.0
    if yoy is not None:
        if yoy > 100:
            score += 30
        elif yoy > 50:
            score += 20
        elif yoy > 20:
            score += 12
        elif yoy > 0:
            score += 5
        else:
            score -= 15
    score += 8 * sum(k in titles for k in _POS_KW)
    score -= 10 * sum(k in titles for k in _NEG_KW)
    score = _clamp(score)

    return {
        "score": round(score, 1),
        "verdict": _verdict(score),
        "signals": {"max_yoy_pct": yoy, "news_count": len(news)},
        "coverage": "news" if news else "none",
    }


# ──────────────────────────────────────────────────────────────────────────
# 分析師③  新聞總經（吃 macro 模組的 regime，全市場共用 + 個股新聞量微調）
# ──────────────────────────────────────────────────────────────────────────
def macro(s: dict[str, Any], regime: dict[str, Any]) -> dict[str, Any]:
    base = float(regime.get("regime_score", 0.0)) if regime else 0.0
    news_n = len(s.get("news") or [])
    score = _clamp(base + min(news_n, 5) * 1.0)
    return {
        "score": round(score, 1),
        "verdict": _verdict(score),
        "signals": {
            "regime_score": round(base, 1),
            "regime": (regime or {}).get("regime", "n/a"),
            "stock_news": news_n,
        },
    }


# ──────────────────────────────────────────────────────────────────────────
# 分析師④  情緒/籌碼面（法人買賣超 + 新聞聲量）
# ──────────────────────────────────────────────────────────────────────────
def sentiment(s: dict[str, Any]) -> dict[str, Any]:
    chip = s.get("chip") or {}
    foreign = chip.get("外資", 0) or 0
    trust = chip.get("投信", 0) or 0
    total = chip.get("合計", 0) or 0

    def bucket(v: float) -> float:
        a = abs(v)
        sign = 1 if v > 0 else (-1 if v < 0 else 0)
        if a >= 100_000:
            return sign * 25
        if a >= 20_000:
            return sign * 15
        if a >= 3_000:
            return sign * 8
        return sign * 3

    parts = {
        "法人合計": bucket(total),
        "投信": 8 if trust > 0 else (-5 if trust < 0 else 0),
        "新聞聲量": min(len(s.get("news") or []), 5) * 1.0,
    }
    score = _clamp(sum(parts.values()))
    return {
        "score": round(score, 1),
        "verdict": _verdict(score),
        "signals": {"外資": foreign, "投信": trust, "合計": total},
        "parts": {k: round(v, 1) for k, v in parts.items()},
    }


# ──────────────────────────────────────────────────────────────────────────
# 多空研究員辯論（依分析師訊號生成多/空論點）
# ──────────────────────────────────────────────────────────────────────────
def researchers(a: dict[str, dict]) -> dict[str, list[str]]:
    bull, bear = [], []
    t, f, m, se = a["technical"], a["fundamental"], a["macro"], a["sentiment"]

    if t["signals"]["ret_20d"] > 5:
        bull.append(f"技術動能轉強，20 日報酬 +{t['signals']['ret_20d']:.1f}%。")
    if t["signals"]["rsi"] < 35:
        bull.append(f"RSI {t['signals']['rsi']:.0f} 落入超賣區，具反彈空間。")
    if f["signals"]["max_yoy_pct"] and f["signals"]["max_yoy_pct"] > 50:
        bull.append(f"營收動能強，年增達 {f['signals']['max_yoy_pct']:.0f}%。")
    if se["signals"]["合計"] > 20000:
        bull.append(f"法人買超 {se['signals']['合計']:,} 張，籌碼偏多。")
    if m["score"] > 10:
        bull.append(f"大盤 regime 偏多（{m['signals']['regime']}）。")

    if t["signals"]["rsi"] > 70:
        bear.append(f"RSI {t['signals']['rsi']:.0f} 過熱，追高風險高。")
    if t["signals"]["cv_sharpe"] < 0:
        bear.append(f"回測 Sharpe 為負（{t['signals']['cv_sharpe']:.2f}），穩定度差。")
    if t["signals"]["ret_20d"] < 0:
        bear.append(f"20 日報酬翻黑（{t['signals']['ret_20d']:.1f}%），動能轉弱。")
    if se["signals"]["合計"] < 0:
        bear.append(f"法人賣超 {se['signals']['合計']:,} 張，籌碼鬆動。")
    if m["score"] < -10:
        bear.append(f"大盤 regime 偏空（{m['signals']['regime']}），系統性風險高。")
    if not f["signals"]["max_yoy_pct"]:
        bear.append("缺營收動能佐證，基本面訊號不足。")

    if not bull:
        bull.append("多方論點有限，僅技術面中性支撐。")
    if not bear:
        bear.append("空方論點有限，主要風險為估值與大盤波動。")
    return {"bull": bull, "bear": bear}


# ──────────────────────────────────────────────────────────────────────────
# 交易員 → 風控 → 投組經理
# ──────────────────────────────────────────────────────────────────────────
def composite_score(a: dict[str, dict]) -> float:
    return _clamp(sum(a[k]["score"] * w for k, w in WEIGHTS.items()))


def decision(s: dict[str, Any], a: dict[str, dict], regime: dict[str, Any]) -> dict[str, Any]:
    comp = composite_score(a)
    regime_score = float(regime.get("regime_score", 0.0)) if regime else 0.0
    exposure = (regime.get("suggestion", {}) or {}).get("exposure_pct", 50)

    # 交易員行動（綜合分數 × 大盤 regime）
    if comp >= 30 and regime_score >= 10:
        action = "買進"
    elif comp >= 15:
        action = "分批布局"
    elif comp > -10:
        action = "觀望"
    else:
        action = "減碼/避開"

    # 信心度：綜合分數正規化 + 四師方向一致度
    agree = sum(1 for k in WEIGHTS if (a[k]["score"] > 0) == (comp > 0))
    confidence = int(_clamp(50 + comp * 0.4 + agree * 3, 5, 95))

    # 風控旗標
    flags = []
    label = (regime or {}).get("regime", "")
    if label in ("risk_off", "mild_risk_off"):
        flags.append("大盤逆風")
    if a["technical"]["signals"]["rsi"] > 70:
        flags.append("超買追高")
    if a["technical"]["signals"]["cv_sharpe"] < 0:
        flags.append("回測穩定度差")

    price = s.get("price")
    return {
        "action": action,
        "confidence": confidence,
        "composite": round(comp, 1),
        "exposure_pct": exposure,
        "entry_zone": [round(price * 0.97, 1), price] if price else None,
        "stop_loss": s.get("stop_loss"),
        "target_short": s.get("target_short"),
        "atr14": s.get("atr14"),
        "flags": flags or ["無重大風險旗標"],
    }


def analyze_stock(s: dict[str, Any], regime: dict[str, Any],
                  tech: dict[str, Any] | None = None) -> dict[str, Any]:
    """單檔完整 pipeline：4 分析師 → 辯論 → 決策。

    tech: 個股最新技術指標快照（來自 docs/stocks/<id>.json），
          供 TradingAgents 風格技術分析師疊加跨類別訊號；缺則退回基本技術評分。
    """
    a = {
        "technical": technical(s, tech),
        "fundamental": fundamental(s),
        "macro": macro(s, regime),
        "sentiment": sentiment(s),
    }
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "price": s.get("price"),
        "analysts": a,
        "debate": researchers(a),
        "decision": decision(s, a, regime),
    }
