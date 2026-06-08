"""
編排器：依序跑三階段，輸出到 macro/output/（獨立，不影響 docs/ 靜態網址）

用法：
    python macro/run_macro.py              # 完整跑（需網路 + GEMINI_API_KEY）
    python macro/run_macro.py --no-events  # 只跑階段一（國際指標）
    python macro/run_macro.py --demo       # 離線自測，用假資料驗證流程

輸出：
    macro/output/macro_YYYYMMDD.json       # 完整結果
    macro/output/macro_YYYYMMDD.md         # 人類可讀摘要
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from macro import market_regime, macro_events, regime_score  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "output"


def _demo_payloads() -> tuple[dict, dict]:
    """離線自測用的假資料。"""
    market = {
        "stage": "market_regime", "risk_score": 22.0, "regime": "mild_risk_on",
        "components": {"SOX": 18, "VIX": 6, "US10Y": -2}, "coverage": "8/8",
        "indicators": {"SOX": {"last": 5800, "chg_1d_pct": 1.5}},
    }
    events = {"stage": "macro_events", "global_sentiment": {
        "events": [
            {"headline": "川普稱將對半導體加徵關稅", "category": "trump",
             "impact": "bearish", "severity": 4,
             "rationale": "衝擊台廠出口與評價", "source": "demo"},
            {"headline": "美 CPI 低於預期", "category": "macro",
             "impact": "bullish", "severity": 3,
             "rationale": "降息預期升溫", "source": "demo"},
        ],
        "overall_bias": "neutral",
        "summary": "關稅利空與通膨利多相抵，情緒中性偏觀望。",
    }, "twse_material_news": [
        {"date": "115/06/08", "code": "2330", "name": "台積電", "subject": "代子公司公告"},
    ]}
    return market, events


def run(with_events: bool = True, demo: bool = False) -> dict:
    if demo:
        market, events = _demo_payloads()
    else:
        market = market_regime.run()
        events = macro_events.run() if with_events else {"global_sentiment": {}}

    final = regime_score.combine(market, events)
    return {
        "date": _dt.date.today().strftime("%Y%m%d"),
        "market_regime": market,
        "macro_events": events,
        "regime_score": final,
    }


def _to_markdown(result: dict) -> str:
    r = result["regime_score"]
    s = r["suggestion"]
    sent = result["macro_events"].get("global_sentiment", {})
    lines = [
        f"# 宏觀情緒面 Regime 報告 — {result['date']}",
        "",
        f"## 綜合結論：**{r['regime']}**　regime_score = **{r['regime_score']}**",
        f"- 建議曝險：**{s['exposure_pct']}%**（{s['stance']}）— {s['note']}",
        f"- 拆解：技術/資金面 {r['breakdown']['market_score']}"
        f"（權重 {r['breakdown']['weights']['market']}）"
        f"｜事件面 {r['breakdown']['event_score']}"
        f"（權重 {r['breakdown']['weights']['event']}，{r['breakdown']['event_basis']}）",
        "",
        "## 國際指標 (階段一)",
    ]
    mr = result["market_regime"]
    lines.append(f"- risk_score: {mr.get('risk_score')}　coverage: {mr.get('coverage')}")
    for k, v in (mr.get("components") or {}).items():
        lines.append(f"  - {k}: {v:+}")

    lines += ["", "## 國際事件情緒 (階段二)"]
    if "error" in sent:
        lines.append(f"- （無資料：{sent['error']}）")
    else:
        lines.append(f"- 總結：{sent.get('summary', '')}")
        for ev in (sent.get("events") or []):
            lines.append(
                f"  - [{ev.get('category')}|{ev.get('impact')}|sev{ev.get('severity')}] "
                f"{ev.get('headline')} — {ev.get('rationale')}（{ev.get('source')}）"
            )

    news = result["macro_events"].get("twse_material_news") or []
    if news and "error" not in news[0]:
        lines += ["", "## 台股當日重大訊息 (節錄)"]
        for n in news[:10]:
            lines.append(f"  - {n.get('code')} {n.get('name')}：{n.get('subject')}")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-events", action="store_true", help="只跑階段一（國際指標）")
    ap.add_argument("--demo", action="store_true", help="離線自測（假資料）")
    args = ap.parse_args()

    result = run(with_events=not args.no_events, demo=args.demo)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    date = result["date"]
    json_path = OUT_DIR / f"macro_{date}.json"
    md_path = OUT_DIR / f"macro_{date}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_to_markdown(result), encoding="utf-8")

    print(_to_markdown(result))
    print(f"\n→ 已輸出：{json_path}\n→ 已輸出：{md_path}")


if __name__ == "__main__":
    main()
