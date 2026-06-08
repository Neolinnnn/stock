"""
編排器：讀 summary.json + macro regime → 跑族群/各股多代理分析 → 輸出 JSON

用法：
    python agents/pipeline.py                      # 用最新 daily_report + 離線 demo regime
    python agents/pipeline.py --date 20260605      # 指定日期
    python agents/pipeline.py --gemini             # 啟用 Gemini 文字敘述（需金鑰）
    python agents/pipeline.py --macro              # 串接 macro 模組即時 regime（需網路）

輸出：agents/output/analysis_<date>.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import analysts, gemini_text, news  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "output"
REPORTS = ROOT / "daily_reports"
STOCKS_DIR = ROOT / "docs" / "stocks"   # analyzer_daily.py 產出的個股 OHLCV/指標

_CHART_POINTS = 60   # 迷你走勢圖取最近 N 個交易日，控制 HTML 體積


def _load_chart(stock_id: str) -> dict | None:
    """從 docs/stocks/<id>.json 取最近 N 日收盤 + 20MA 供前端畫迷你走勢圖。"""
    fp = STOCKS_DIR / f"{stock_id}.json"
    if not fp.exists():
        return None
    try:
        d = json.loads(fp.read_text(encoding="utf-8"))
        o = d.get("ohlcv", {})
        ind = d.get("indicators", {})
        close = o.get("close") or []
        dates = o.get("date") or []
        ma20 = ind.get("ma20") or []
        if len(close) < 5:
            return None
        n = _CHART_POINTS
        return {
            "dates": dates[-n:],
            "close": close[-n:],
            "ma20": ma20[-n:] if len(ma20) == len(close) else [],
        }
    except Exception:
        return None


def _latest_report() -> Path:
    days = sorted(
        p for p in REPORTS.iterdir()
        if p.is_dir() and p.name.isdigit() and (p / "summary.json").exists()
    )
    if not days:
        raise FileNotFoundError("找不到任何 daily_reports/<date>/summary.json")
    return days[-1]


def _load_regime(use_macro: bool) -> dict:
    """取得大盤 regime：--macro 走即時模組，否則用離線 demo 值。"""
    if use_macro:
        from macro import market_regime, macro_events, regime_score
        m = market_regime.run()
        e = macro_events.run()
        combined = regime_score.combine(m, e)
        combined["_events"] = e.get("global_sentiment", {})  # 供 UI 顯示外電
        return combined
    # 離線 demo（與 macro/run_macro.py --demo 一致；附示範外電供介面預覽）
    return {
        "regime_score": 12.4, "regime": "mild_risk_on",
        "suggestion": {"exposure_pct": 70, "stance": "偏多"},
        "_events": {"events": [
            {"headline": "川普稱將對半導體加徵關稅", "category": "trump",
             "impact": "bearish", "severity": 4,
             "rationale": "衝擊台廠出口與評價", "source": ""},
            {"headline": "美 5 月 CPI 低於預期，降息預期升溫", "category": "macro",
             "impact": "bullish", "severity": 3,
             "rationale": "利率敏感的成長股受惠", "source": ""},
        ]},
    }


def run(date: str | None, use_gemini: bool, use_macro: bool) -> dict:
    report_dir = (REPORTS / date) if date else _latest_report()
    summary = json.loads((report_dir / "summary.json").read_text(encoding="utf-8"))
    regime = _load_regime(use_macro)

    sectors_out = []
    for sec_name, sec in summary.get("sectors", {}).items():
        stocks_out = []
        for s in sec.get("stocks", []):
            r = analysts.analyze_stock(s, regime)
            r["news"] = news.prepare_news(s, report_dir.name)
            r["chart"] = _load_chart(str(s.get("id", "")))
            r["summary_text"] = gemini_text.summarize(r, use_gemini=use_gemini)
            stocks_out.append(r)
        if not stocks_out:
            continue
        sec_score = round(sum(x["decision"]["composite"] for x in stocks_out) / len(stocks_out), 1)
        stocks_out.sort(key=lambda x: x["decision"]["composite"], reverse=True)
        sectors_out.append({
            "name": sec_name,
            "sector_score": sec_score,
            "verdict": analysts._verdict(sec_score),
            "top_pick": stocks_out[0]["name"] if stocks_out else None,
            "synthesis": _sector_synthesis(stocks_out),
            "stocks": stocks_out,
        })

    sectors_out.sort(key=lambda x: x["sector_score"], reverse=True)
    # 移除內部欄位，避免外洩進輸出
    clean_regime = {k: v for k, v in regime.items() if k != "_events"}
    return {
        "date": report_dir.name,
        "regime": clean_regime,
        "events": news.prepare_events(regime),
        "weights": analysts.WEIGHTS,
        "sectors": sectors_out,
        "stock_count": sum(len(x["stocks"]) for x in sectors_out),
    }


def _sector_synthesis(stocks: list[dict]) -> dict:
    """族群級綜述：行動分布 + 多空重點 + 首選清單。"""
    from collections import Counter
    actions = Counter(x["decision"]["action"] for x in stocks)
    picks = [f"{x['name']}({x['decision']['composite']})" for x in stocks[:3]]
    bull, bear = [], []
    n_tech_pos = sum(x["analysts"]["technical"]["score"] > 10 for x in stocks)
    n_buy = sum(x["analysts"]["sentiment"]["signals"]["合計"] > 20000 for x in stocks)
    n_yoy = sum(bool((x["analysts"]["fundamental"]["signals"].get("max_yoy_pct") or 0) > 50)
                for x in stocks)
    if n_tech_pos:
        bull.append(f"{n_tech_pos}/{len(stocks)} 檔技術面偏多。")
    if n_yoy:
        bull.append(f"{n_yoy} 檔營收年增逾 50%。")
    if n_buy:
        bull.append(f"{n_buy} 檔法人明顯買超。")
    n_overbought = sum(x["analysts"]["technical"]["signals"]["rsi"] > 70 for x in stocks)
    n_sell = sum(x["analysts"]["sentiment"]["signals"]["合計"] < 0 for x in stocks)
    if n_overbought:
        bear.append(f"{n_overbought} 檔 RSI 過熱，留意追高。")
    if n_sell:
        bear.append(f"{n_sell} 檔法人賣超，籌碼鬆動。")
    if not bull:
        bull.append("族群多方訊號有限。")
    if not bear:
        bear.append("族群空方訊號有限。")
    return {"actions": dict(actions), "top_picks": picks, "bull": bull, "bear": bear}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定 daily_reports 日期 (YYYYMMDD)")
    ap.add_argument("--gemini", action="store_true", help="啟用 Gemini 文字敘述")
    ap.add_argument("--macro", action="store_true", help="串接 macro 即時 regime（需網路）")
    args = ap.parse_args()

    result = run(args.date, use_gemini=args.gemini, use_macro=args.macro)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"analysis_{result['date']}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"日期 {result['date']}｜族群 {len(result['sectors'])}｜個股 {result['stock_count']}")
    print(f"大盤 regime：{result['regime']['regime']} ({result['regime']['regime_score']})")
    print("族群評分排行：")
    for sec in result["sectors"][:5]:
        print(f"  {sec['sector_score']:>6}  {sec['name']}（首選 {sec['top_pick']}）")
    print(f"→ 已輸出：{out}")


if __name__ == "__main__":
    main()
