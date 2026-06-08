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

from agents import analysts, gemini_text  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "output"
REPORTS = ROOT / "daily_reports"


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
        return regime_score.combine(m, e)
    # 離線 demo（與 macro/run_macro.py --demo 一致）
    return {
        "regime_score": 12.4, "regime": "mild_risk_on",
        "suggestion": {"exposure_pct": 70, "stance": "偏多"},
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
            "stocks": stocks_out,
        })

    sectors_out.sort(key=lambda x: x["sector_score"], reverse=True)
    return {
        "date": report_dir.name,
        "regime": regime,
        "weights": analysts.WEIGHTS,
        "sectors": sectors_out,
        "stock_count": sum(len(x["stocks"]) for x in sectors_out),
    }


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
