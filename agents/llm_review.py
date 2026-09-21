"""
LLM 辯論／覆核層（獨立執行，不接入每日掃描）

定位：確定性主幹（analysts.py）算完之後，挑少數幾檔請 LLM 當魔鬼代言人覆核。
      LLM 只出「意見」，不覆寫任何評分或決策欄位——這樣回測基礎設施才不會失效，
      也才能事後比對「LLM 的異議事後看對不對」。

為什麼不整包導入 TauricResearch/TradingAgents：
  它的 dataflows 全為美股資料源（Alpha Vantage / FRED / Reddit / StockTwits），
  拿不到台股三大法人與月營收；且每檔約 12-15 次 LLM 呼叫，102 檔／天成本不可行。
  這裡只借它的角色分工概念，資料與評分留在本專案既有管線。

用法：
    python agents/llm_review.py                    # 最新一日，Top 10
    python agents/llm_review.py --date 20260804 --top 15
    python agents/llm_review.py --dry-run          # 不呼叫 API，只跑規則檢查並印 prompt

輸出：
    agents/output/review_<date>.json
    agents/output/review_<date>.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = Path(__file__).resolve().parent / "output"

# 建議進場的行動——只有這些個股的進場區/目標價才是真的建議，
# 「觀望 / 減碼」的那兩個欄位不會被拿來下單，算風報比只是製造雜訊。
_ACTIONABLE = ("買進", "分批布局")

# 風報比低於此值偏低。注意：目前這是「常態」而非個案，
# 因為進場區是固定 -3%、停損卻是 2×ATR（距離中位數約 15%），分母天生大於分子。
# 故列為 warn 級，只做統計，不逐檔標記。
_MIN_RR = 1.5


def risk_reward(st: dict) -> dict:
    """規則層的硬指標檢查：這些是算術，不該交給 LLM 算。

    issues 分兩級：
      error — 邏輯上說不通，必須看（例如做對了也不賺）
      warn  — 值得留意但普遍存在，彙總呈現即可
    """
    d = st.get("decision") or {}
    ez, sl, ts = d.get("entry_zone"), d.get("stop_loss"), d.get("target_short")
    price = st.get("price")
    out: dict = {"rr": None, "stop_pct": None, "target_pct": None,
                 "actionable": d.get("action") in _ACTIONABLE,
                 "errors": [], "warns": []}
    if not (ez and sl and ts and price):
        if out["actionable"]:
            out["errors"].append("建議進場卻缺價格資料，無法評估風險")
        return out

    entry = ez[1]                      # 保守起見以進場區上緣（即現價）計
    risk = entry - sl
    reward = ts - entry
    out["stop_pct"] = round((entry - sl) / entry * 100, 1)
    out["target_pct"] = round((ts - entry) / entry * 100, 1)
    out["rr"] = round(reward / risk, 2) if risk > 0 else None

    if not out["actionable"]:
        return out                     # 不進場者不做風報比判定

    if reward <= 0:
        out["errors"].append(
            f"短期目標價 {ts} 不高於進場價 {entry}，做對了也不賺")
    elif out["rr"] is not None and out["rr"] < _MIN_RR:
        out["warns"].append(f"風報比 {out['rr']}，低於 {_MIN_RR}")
    used = d.get("weights_used") or {}
    if len(used) <= 1 and d.get("confidence", 0) >= 60:
        out["errors"].append(
            f"僅 {len(used)} 個面向有資料卻報信心 {d.get('confidence')}%")
    return out


def structural_summary(stocks: list[dict]) -> dict:
    """把「普遍存在的偏差」彙總成一則結論，而不是逐檔喊 21 次。"""
    act = [s for s in stocks if s["_rule_check"]["actionable"]]
    rrs = [s["_rule_check"]["rr"] for s in act
           if s["_rule_check"]["rr"] is not None]
    low = [r for r in rrs if r < _MIN_RR]
    stops = [s["_rule_check"]["stop_pct"] for s in act
             if s["_rule_check"]["stop_pct"] is not None]
    note = None
    if rrs and len(low) / len(rrs) > 0.6:
        note = (f"{len(low)}/{len(rrs)} 檔建議進場的個股風報比低於 {_MIN_RR}，"
                "屬結構性偏差而非個案：進場區為固定 -3%，停損為 2×ATR"
                f"（距離中位數 {sorted(stops)[len(stops)//2]:.1f}%），分母天生大於分子。"
                "要改善須讓進場區也以 ATR 為基準，或短期目標改用更遠的壓力位。")
    return {"actionable": len(act),
            "rr_median": round(sorted(rrs)[len(rrs) // 2], 2) if rrs else None,
            "rr_below_threshold": len(low),
            "structural_note": note}


def _compact(st: dict, rc: dict) -> dict:
    """送給 LLM 的精簡記錄：剔除 chart 與新聞全文，附上規則層算好的硬指標。"""
    a = st["analysts"]
    d = st["decision"]
    return {
        "id": st["id"], "name": st["name"], "price": st["price"],
        "sector": st.get("_sector"),
        "decision": {k: d.get(k) for k in
                     ("action", "composite", "confidence", "exposure_pct",
                      "entry_zone", "stop_loss", "target_short", "flags",
                      "weights_used")},
        "analysts": {k: {"score": a[k]["score"], "verdict": a[k]["verdict"],
                         "coverage": a[k].get("coverage"),
                         "signals": a[k]["signals"]} for k in a},
        "debate_rules": st.get("debate"),
        "rule_check": rc,
    }


def select(data: dict, top: int) -> list[dict]:
    """挑選覆核標的：綜合分數 Top-N，外加任何被規則標記異常的個股。

    後者是重點——分數高的未必有問題，有問題的才需要人（或 LLM）看一眼。
    """
    stocks = []
    for sec in data.get("sectors", []):
        for st in sec.get("stocks", []):
            st = {**st, "_sector": sec["name"]}
            st["_rule_check"] = risk_reward(st)
            stocks.append(st)

    stocks.sort(key=lambda x: x["decision"]["composite"], reverse=True)
    picked = stocks[:top]
    picked_ids = {x["id"] for x in picked}
    # 只補進 error 級（邏輯說不通的）；warn 級普遍存在，交給 structural_summary 彙總
    flagged = [x for x in stocks
               if x["_rule_check"]["errors"] and x["id"] not in picked_ids]
    return picked + flagged


def _parse_json(text: str) -> dict:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


def run(date: str | None, top: int, dry_run: bool) -> dict:
    if date:
        src = OUT_DIR / f"analysis_{date}.json"
    else:
        cands = sorted(OUT_DIR.glob("analysis_*.json"))
        if not cands:
            raise FileNotFoundError("找不到 analysis_*.json，請先跑 pipeline.py")
        src = cands[-1]
    data = json.loads(src.read_text(encoding="utf-8"))

    picked = select(data, top)
    payload = [_compact(st, st["_rule_check"]) for st in picked]
    n_err = sum(bool(st["_rule_check"]["errors"]) for st in picked)
    struct = structural_summary(
        [{**s, "_rule_check": risk_reward(s)}
         for sec in data.get("sectors", []) for s in sec.get("stocks", [])])
    print(f"日期 {data['date']}｜覆核 {len(picked)} 檔"
          f"（Top {top} + error 級異常）｜其中 {n_err} 檔規則層判定說不通")
    if struct["structural_note"]:
        print(f"⚠ 結構性偏差：{struct['structural_note']}")

    opinions: dict = {}
    if dry_run:
        print("\n--- dry-run：以下為將送出的 payload（未呼叫 API）---")
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:2000] + "\n…")
    else:
        from gemini_writer import GeminiWriter
        writer = GeminiWriter()
        text = writer.generate("llm_debate", {"date": data["date"], "data": payload})
        opinions = _parse_json(text)
        if not opinions:
            print("⚠ LLM 回應無法解析為 JSON，僅輸出規則層檢查結果", file=sys.stderr)
        else:
            print(f"LLM 覆核完成：{len(opinions)}/{len(picked)} 檔取得意見")

    result = {
        "date": data["date"],
        "source": src.name,
        "top": top,
        "note": "LLM 意見僅供覆核參考，不覆寫 analysis 的評分與決策",
        "structural": struct,
        "stocks": [{
            "id": st["id"], "name": st["name"], "sector": st["_sector"],
            "action": st["decision"]["action"],
            "composite": st["decision"]["composite"],
            "confidence": st["decision"]["confidence"],
            "rule_check": st["_rule_check"],
            "llm": opinions.get(str(st["id"]), {}),
        } for st in picked],
    }
    return result


def to_markdown(r: dict) -> str:
    st = r["structural"]
    lines = [f"# 多代理決策覆核 {r['date']}", "",
             f"來源：`{r['source']}`｜覆核 {len(r['stocks'])} 檔｜{r['note']}", ""]
    if st.get("structural_note"):
        lines += ["## 🏗 結構性偏差（非個案）", "",
                  st["structural_note"], "",
                  f"建議進場 {st['actionable']} 檔｜風報比中位數 {st['rr_median']}"
                  f"｜低於門檻 {st['rr_below_threshold']} 檔", ""]
    flagged = [s for s in r["stocks"] if s["rule_check"]["errors"]]
    if flagged:
        lines += ["## ⚠ 規則層判定說不通（逐檔）", ""]
        for s in flagged:
            lines.append(f"- **{s['name']}（{s['id']}）** {s['action']}"
                         f"｜分數 {s['composite']}｜風報比 {s['rule_check']['rr']}")
            for i in s["rule_check"]["errors"]:
                lines.append(f"  - {i}")
        lines.append("")
    lines += ["## 逐檔覆核", ""]
    for s in r["stocks"]:
        llm = s["llm"] or {}
        lines += [f"### {s['name']}（{s['id']}）· {s['sector']}", "",
                  f"規則建議 **{s['action']}**（分數 {s['composite']}、信心 {s['confidence']}%）"
                  f"｜風報比 {s['rule_check']['rr']}"
                  f"｜停損距離 {s['rule_check']['stop_pct']}%"
                  f"｜目標空間 {s['rule_check']['target_pct']}%", ""]
        if llm:
            lines += [f"- 覆核結論：**{llm.get('verdict', '—')}** — {llm.get('reason', '')}",
                      f"- 主要疑慮：{llm.get('risk_objection', '—')}"]
            for k, label in (("bull", "多方"), ("bear", "空方")):
                for x in llm.get(k) or []:
                    lines.append(f"  - {label}：{x}")
        else:
            lines.append("- （無 LLM 意見）")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="analysis 日期 (YYYYMMDD)，省略取最新")
    ap.add_argument("--top", type=int, default=10, help="覆核綜合分數前 N 檔（預設 10）")
    ap.add_argument("--dry-run", action="store_true", help="不呼叫 API，只跑規則檢查")
    args = ap.parse_args()

    r = run(args.date, args.top, args.dry_run)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    jp = OUT_DIR / f"review_{r['date']}.json"
    mp = OUT_DIR / f"review_{r['date']}.md"
    jp.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
    mp.write_text(to_markdown(r), encoding="utf-8")

    print("\n覆核摘要：")
    for s in r["stocks"]:
        v = (s["llm"] or {}).get("verdict", "—")
        mark = "⚠" if s["rule_check"]["errors"] else " "
        print(f"  {mark} {s['name']}({s['id']}) {s['action']:<8}"
              f" 分數 {s['composite']:>6}  RR {str(s['rule_check']['rr']):>6}  覆核 {v}")
    print(f"\n→ {jp}\n→ {mp}")


if __name__ == "__main__":
    main()
