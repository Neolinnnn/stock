"""
階段一：國際指標 → risk_score

抓取與台股連動度最高的國際指標，計算一個 -100 ~ +100 的 risk_score，
用來判斷「今天市場風險偏好高不高、該不該進場」。

風險偏好越高 (risk_on) → 分數越正；越避險 (risk_off) → 分數越負。

指標與權重（依對台股的影響力排序）：
  - 費半 SOX  : 台股電子權重股的領先指標，連動最高
  - VIX       : 市場恐慌指數，水準 + 變化
  - 美債 10Y  : 殖利率快速上行通常壓抑成長股
  - 美元 DXY  : 強勢美元 → 資金回流美國、新興市場承壓
  - 台幣匯率  : 台幣急貶常伴隨外資匯出
  - S&P 500   : 大盤趨勢（站上/跌破 50MA）

注意：yfinance 為本模組「額外」依賴，見 macro/requirements.txt。
此沙箱網路政策可能擋掉 Yahoo（403），實際抓取請在有網路的環境執行。
"""
from __future__ import annotations

import datetime as _dt
from typing import Any

# yfinance 代碼對照（主要 + ETF 備援，^ prefix 在部分環境不穩定）
TICKERS = {
    "SOX": "^SOX",      # 費城半導體指數
    "VIX": "^VIX",      # 恐慌指數
    "US10Y": "^TNX",    # 美債 10 年殖利率
    "DXY": "DX-Y.NYB",  # 美元指數
    "TWD": "TWD=X",     # 美元兌台幣
    "SP500": "^GSPC",   # 標普 500
    "NASDAQ": "^IXIC",  # 那斯達克
    "TWII": "^TWII",    # 台灣加權指數
}

# ETF fallback：當主要 ticker 失敗時（GitHub Actions 環境 ^ prefix 常被擋）
_FALLBACK = {
    "SOX": "SOXX",       # iShares PHLX Semiconductor ETF
    "VIX": "VIXY",       # ProShares VIX Short-Term Futures ETF
    "SP500": "SPY",      # SPDR S&P 500 ETF
    "NASDAQ": "QQQ",     # Invesco QQQ Trust
    "TWII": "0050.TW",   # 元大台灣50（追蹤加權指數）
    "DXY": "UUP",        # Invesco DB US Dollar ETF
    "US10Y": "^TNX",     # 無好的 ETF 替代，維持原值
    "TWD": "USDTWD=X",   # TWD 備援代碼
}
# VIX ETF 的絕對水準 ≠ VIX 指數，level 評分需依 ETF 校正
_VIX_ETF_NAMES = {"VIXY", "VXX"}


def fetch_indicators(period: str = "3mo") -> dict[str, Any]:
    """
    用 yfinance 抓取各國際指標的近期收盤，回傳精簡後的原始數據。

    Returns:
        {代碼: {last, prev, chg_1d_pct, chg_5d_pct, ma50, above_ma50}}
        抓取失敗的代碼會標記 {"error": "..."}。
    """
    try:
        import yfinance as yf
    except ImportError as e:  # pragma: no cover
        raise RuntimeError(
            "需要 yfinance，請先 `pip install -r macro/requirements.txt`"
        ) from e

    out: dict[str, Any] = {}
    for name, ticker in TICKERS.items():
        result = _fetch_one(yf, name, ticker, period)
        if "error" in result:
            fb = _FALLBACK.get(name)
            if fb and fb != ticker:
                result = _fetch_one(yf, name, fb, period)
                if "error" not in result:
                    result["_fallback"] = fb
        out[name] = result
    return out


def _fetch_one(yf: Any, name: str, ticker: str, period: str) -> dict[str, Any]:
    """抓單一 ticker，失敗回傳 {"error": ...}。"""
    try:
        hist = yf.download(ticker, period=period, progress=False, auto_adjust=True)
        if hist is None or hist.empty:
            return {"error": "資料不足"}
        close = hist["Close"].dropna()
        # yfinance 新版多層 column，需 squeeze
        if hasattr(close, "columns"):
            close = close.iloc[:, 0]
        if len(close) < 2:
            return {"error": "資料不足"}
        last = float(close.iloc[-1])
        prev = float(close.iloc[-2])
        base5 = float(close.iloc[-6]) if len(close) >= 6 else prev
        ma50 = float(close.tail(50).mean())
        return {
            "last": round(last, 3),
            "prev": round(prev, 3),
            "chg_1d_pct": round((last / prev - 1) * 100, 2),
            "chg_5d_pct": round((last / base5 - 1) * 100, 2),
            "ma50": round(ma50, 3),
            "above_ma50": last >= ma50,
        }
    except Exception as e:
        return {"error": str(e)[:80]}


def _clamp(x: float, lo: float = -100.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def compute_risk_score(ind: dict[str, Any]) -> dict[str, Any]:
    """
    依規則把原始指標彙整成 risk_score (-100 ~ +100) 與 regime 標籤。

    這是純邏輯（Claude 負責）：每個指標貢獻一個分量，加權後夾在 ±100。
    分量為正代表「偏多/risk-on」。
    """
    parts: dict[str, float] = {}

    def ok(name: str) -> bool:
        return name in ind and "error" not in ind[name]

    # 1) 費半 SOX — 權重最高：1 日 +5 日動能
    if ok("SOX"):
        s = ind["SOX"]
        parts["SOX"] = _clamp(s["chg_1d_pct"] * 6 + s["chg_5d_pct"] * 2, -40, 40)

    # 2) VIX — 水準 + 變化（VIX 上升 = 避險，分數轉負）
    if ok("VIX"):
        v = ind["VIX"]
        level = v["last"]
        is_etf = v.get("_fallback") in _VIX_ETF_NAMES
        if is_etf:
            # VIXY ETF 通常在 10–30 區間，對應 VIX 15–35，閾值等比縮放
            # level 評分僅用動能，不用絕對水準避免誤判
            level_part = 0
        else:
            # ^VIX 原始水準評分：<15 偏多、15-20 中性、20-30 偏空、>30 恐慌
            if level < 15:
                level_part = 15
            elif level < 20:
                level_part = 5
            elif level < 30:
                level_part = -15
            else:
                level_part = -30
        change_part = _clamp(-v["chg_1d_pct"] * 1.0, -15, 15)
        parts["VIX"] = level_part + change_part

    # 3) 美債 10Y — 殖利率快速上行壓抑股市（變化轉負）
    if ok("US10Y"):
        parts["US10Y"] = _clamp(-ind["US10Y"]["chg_1d_pct"] * 8, -15, 15)

    # 4) 美元 DXY — 走強壓抑新興市場
    if ok("DXY"):
        parts["DXY"] = _clamp(-ind["DXY"]["chg_1d_pct"] * 10, -10, 10)

    # 5) 台幣 — 急貶（TWD=X 上升）= 外資匯出，轉負
    if ok("TWD"):
        parts["TWD"] = _clamp(-ind["TWD"]["chg_1d_pct"] * 10, -10, 10)

    # 6) S&P 趨勢 — 站上 50MA 加分
    if ok("SP500"):
        sp = ind["SP500"]
        trend = 10 if sp["above_ma50"] else -10
        parts["SP500"] = trend + _clamp(sp["chg_1d_pct"] * 3, -10, 10)

    score = _clamp(sum(parts.values()))
    return {
        "risk_score": round(score, 1),
        "regime": _label(score),
        "components": {k: round(v, 1) for k, v in parts.items()},
        "coverage": f"{len(parts)}/{len(TICKERS)}",
    }


def _label(score: float) -> str:
    if score >= 35:
        return "risk_on"      # 偏多，可積極
    if score >= 10:
        return "mild_risk_on"
    if score > -10:
        return "neutral"      # 中性，標準部位
    if score > -35:
        return "mild_risk_off"
    return "risk_off"         # 避險，宜保守/減碼


def run(period: str = "3mo") -> dict[str, Any]:
    """抓取 + 評分，回傳完整結果（含原始指標）。"""
    ind = fetch_indicators(period=period)
    scored = compute_risk_score(ind)
    return {
        "stage": "market_regime",
        "asof": _dt.datetime.now().isoformat(timespec="seconds"),
        "indicators": ind,
        **scored,
    }


if __name__ == "__main__":
    import json
    print(json.dumps(run(), ensure_ascii=False, indent=2))
