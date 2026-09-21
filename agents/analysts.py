"""
Claude 邏輯核心：4 位分析師評分 + 多空辯論 + 交易員/風控/投組經理決策

所有評分皆為 -100 ~ +100 的確定性規則（可回測、可稽核），
文字判讀預設用模板，若啟用 Gemini 則改由 gemini_text 產出。
"""
from __future__ import annotations

import re
from typing import Any

# 個股綜合分數的權重（技術面為主，補上基本面/籌碼面）
#
# 總經(macro)刻意不在此列：大盤 regime 對當日所有個股是同一個常數，
# 放進個股加權只是替每檔加上同樣的偏移，不產生任何鑑別度，卻會壓縮
# 其他面向的分數空間。總經改為只在 decision() 調整部位上限與行動門檻。
WEIGHTS = {"technical": 0.45, "fundamental": 0.25, "sentiment": 0.30}

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
    has_price_data = any(s.get(k) is not None for k in ("ret_20d", "rsi", "signal"))
    return {
        "score": round(score, 1),
        "verdict": _verdict(score),
        "signals": {"ret_20d": ret, "rsi": rsi, "cv_sharpe": sharpe, "signal": signal},
        "parts": {k: round(v, 1) for k, v in parts.items()},
        "tech_report": tech_report,
        "coverage": ("indicators" if ind else "basic") if has_price_data else "none",
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


def _last_valid(arr: list | None, n: int = 1) -> list[float]:
    """取序列末端最後 n 個非 None 值（新→舊）。"""
    out = []
    for v in reversed(arr or []):
        if isinstance(v, (int, float)):
            out.append(float(v))
            if len(out) >= n:
                break
    return out


def _revenue_signals(fund: dict[str, Any] | None) -> dict[str, Any]:
    """從 docs/fundamentals/<id>.json 取營收／EPS／毛利率訊號。"""
    if not fund:
        return {}
    rev = fund.get("revenue") or {}
    eps = fund.get("eps") or {}
    mg = fund.get("margins") or {}

    yoy_series = rev.get("yoy") or []
    yoy_recent = _last_valid(yoy_series, 3)
    months = [m for m in (rev.get("month") or [])]
    cum = _last_valid(rev.get("cum_yoy"), 1)
    eps_yoy = _last_valid(eps.get("yoy"), 1)
    gm = _last_valid(mg.get("gross_margin"), 2)

    return {
        "rev_month": months[-1] if months else None,
        "rev_yoy": round(yoy_recent[0], 1) if yoy_recent else None,
        "rev_yoy_3m": round(sum(yoy_recent) / len(yoy_recent), 1) if yoy_recent else None,
        "cum_yoy": round(cum[0], 1) if cum else None,
        # EPS 基期極小時 yoy 會出現數萬 % 的失真值，截斷到 ±999 避免污染畫面
        "eps_yoy": round(_clamp(eps_yoy[0], -999, 999), 1) if eps_yoy else None,
        "gross_margin": round(gm[0], 1) if gm else None,
        "gm_delta": round(gm[0] - gm[1], 1) if len(gm) >= 2 else None,
    }


def _yoy_points(yoy: float) -> float:
    """月營收年增率 → 分數（正負不對稱：衰退的懲罰大於成長的獎勵）。"""
    if yoy > 100:
        return 35
    if yoy > 50:
        return 25
    if yoy > 20:
        return 15
    if yoy > 0:
        return 6
    if yoy > -20:
        return -12
    return -25


def fundamental(s: dict[str, Any], fund: dict[str, Any] | None = None) -> dict[str, Any]:
    """基本面分析師。

    主資料源為 docs/fundamentals/<id>.json（FinMind 月營收/EPS/毛利率）——
    這是硬數字，不受新聞抓取成敗影響。新聞標題的年增率／關鍵字僅作為
    「本月速報尚未進 FinMind」時的補充，權重刻意壓低。
    """
    news = s.get("news") or []
    sig = _revenue_signals(fund)
    titles = " ".join(n.get("title", "") for n in news)
    news_yoy = _extract_yoy(news, s.get("id", ""))

    score = 0.0
    if sig.get("rev_yoy") is not None:
        # 最新月 60%、近三月平均 40%：避免單月基期效應主導
        score += _yoy_points(sig["rev_yoy"]) * 0.6
        score += _yoy_points(sig["rev_yoy_3m"]) * 0.4
        if sig.get("cum_yoy") is not None:
            score += 6 if sig["cum_yoy"] > 0 else -6
        if sig.get("eps_yoy") is not None:
            score += 10 if sig["eps_yoy"] > 20 else (-10 if sig["eps_yoy"] < 0 else 0)
        if sig.get("gm_delta") is not None:
            score += 8 if sig["gm_delta"] > 0.5 else (-8 if sig["gm_delta"] < -0.5 else 0)
    elif news_yoy is not None:
        score += _yoy_points(news_yoy) * 0.5   # 只有新聞可用時打對折

    # 新聞情緒僅微調（有硬數字時不該被標題帶著走）
    score += 4 * sum(k in titles for k in _POS_KW)
    score -= 5 * sum(k in titles for k in _NEG_KW)
    score = _clamp(score)

    if sig.get("rev_yoy") is not None:
        coverage = "financials"
    elif news_yoy is not None:
        coverage = "news"
    else:
        coverage = "none"

    return {
        "score": round(score, 1),
        "verdict": _verdict(score),
        "signals": {
            "rev_month": sig.get("rev_month"),
            "rev_yoy": sig.get("rev_yoy"),
            "rev_yoy_3m": sig.get("rev_yoy_3m"),
            "cum_yoy": sig.get("cum_yoy"),
            "eps_yoy": sig.get("eps_yoy"),
            "gross_margin": sig.get("gross_margin"),
            "gm_delta": sig.get("gm_delta"),
            "news_count": len(news),
        },
        "coverage": coverage,
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
        "coverage": "regime" if regime else "none",
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
        "coverage": "chip" if chip else "none",
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
    fs = f["signals"]
    if fs.get("rev_yoy") is not None and fs["rev_yoy"] > 20:
        bull.append(f"{fs.get('rev_month') or '最新月'}營收年增 {fs['rev_yoy']:.0f}%，成長動能明確。")
    if fs.get("eps_yoy") is not None and fs["eps_yoy"] > 20:
        bull.append(f"最新季 EPS 年增 {fs['eps_yoy']:.0f}%，獲利同步放大。")
    if fs.get("gm_delta") is not None and fs["gm_delta"] > 0.5:
        bull.append(f"毛利率較上季 +{fs['gm_delta']:.1f} 個百分點，產品組合轉佳。")
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
    if fs.get("rev_yoy") is not None and fs["rev_yoy"] < 0:
        bear.append(f"{fs.get('rev_month') or '最新月'}營收年減 {abs(fs['rev_yoy']):.0f}%，基本面轉弱。")
    if fs.get("gm_delta") is not None and fs["gm_delta"] < -0.5:
        bear.append(f"毛利率較上季 {fs['gm_delta']:.1f} 個百分點，成本壓力升溫。")
    if f.get("coverage") == "none":
        bear.append("查無月營收／財報資料，基本面無法評估（該面向已排除於綜合分數外）。")

    if not bull:
        bull.append("多方論點有限，僅技術面中性支撐。")
    if not bear:
        bear.append("空方論點有限，主要風險為估值與大盤波動。")
    return {"bull": bull, "bear": bear}


# ──────────────────────────────────────────────────────────────────────────
# 交易員 → 風控 → 投組經理
# ──────────────────────────────────────────────────────────────────────────
def composite_score(a: dict[str, dict]) -> float:
    """依「實際有資料」的面向重新歸一化權重後加權平均。

    缺資料的面向（coverage == "none"）代表「不知道」，不是「中性 0 分」。
    若把它當 0 分計入，等於用固定權重稀釋其餘面向，全體分數會被系統性
    往 0 拉、決策整體偏保守。故排除後以剩餘權重歸一化。
    """
    covered = [(k, w) for k, w in WEIGHTS.items()
               if a.get(k, {}).get("coverage") != "none"]
    if not covered:
        return 0.0
    total_w = sum(w for _, w in covered)
    return _clamp(sum(a[k]["score"] * w for k, w in covered) / total_w)


def covered_weights(a: dict[str, dict]) -> dict[str, float]:
    """回傳本檔實際生效的權重（歸一化後），供 UI 說明分數怎麼算出來的。"""
    covered = {k: w for k, w in WEIGHTS.items()
               if a.get(k, {}).get("coverage") != "none"}
    total_w = sum(covered.values())
    if not total_w:
        return {}
    return {k: round(w / total_w, 3) for k, w in covered.items()}


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

    # 信心度：綜合分數 + 有資料的分析師方向一致度（缺資料者不計入一致度）
    weights = covered_weights(a)
    agree = sum(1 for k in weights if (a[k]["score"] > 0) == (comp > 0))
    agree_ratio = agree / len(weights) if weights else 0.0
    # 覆蓋率低（只有 1~2 個面向有料）時信心上限跟著降，避免單一面向獨大卻報高信心
    coverage_ratio = len(weights) / len(WEIGHTS)
    confidence = int(_clamp((50 + comp * 0.4 + agree_ratio * 12) * (0.7 + 0.3 * coverage_ratio),
                            5, 95))

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
        "weights_used": weights,
    }


def analyze_stock(s: dict[str, Any], regime: dict[str, Any],
                  tech: dict[str, Any] | None = None,
                  fund: dict[str, Any] | None = None) -> dict[str, Any]:
    """單檔完整 pipeline：4 分析師 → 辯論 → 決策。

    tech: 個股最新技術指標快照（來自 docs/stocks/<id>.json），
          供 TradingAgents 風格技術分析師疊加跨類別訊號；缺則退回基本技術評分。
    fund: 個股財務資料（來自 docs/fundamentals/<id>.json），供基本面分析師使用。
    """
    a = {
        "technical": technical(s, tech),
        "fundamental": fundamental(s, fund),
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
