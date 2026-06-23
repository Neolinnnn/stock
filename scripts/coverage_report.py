"""
資料覆蓋率報告。掃描 docs/fundamentals 與 docs/stocks，
輸出每檔個股的「基本面 / 產銷組合」有無，產生：
  docs/coverage.json  — 結構化（給網站或程式讀）
  docs/coverage.md    — Markdown 表格（可在 GitHub 直接看）

每日掃描後執行並 commit，狀態隨補齊進度更新。
用法： python scripts/coverage_report.py
"""
import json
import glob
import os
from datetime import datetime
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"


def _has_pm(d: dict) -> bool:
    """產銷組合是否「完整」：存在、佔比和約 100%、無 None。"""
    pm = d.get("product_mix")
    if not pm:
        return False
    lines = pm.get("product_lines", [])
    shares = [l.get("share_pct") for l in lines]
    if not lines or any(s is None for s in shares):
        return False
    total = sum(s for s in shares if isinstance(s, (int, float)))
    return 80 <= total <= 120


def main():
    # 代碼→(name, sector) 來自 stocks_index
    info = {}
    idx_path = DOCS / "stocks_index.json"
    if idx_path.exists():
        for x in json.loads(idx_path.read_text(encoding="utf-8")):
            info[x["id"]] = (x.get("name", x["id"]), x.get("sector", ""))

    rows = []
    for p in sorted(glob.glob(str(DOCS / "fundamentals" / "*.json"))):
        sid = os.path.basename(p)[:-5]
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        name, sector = info.get(sid, (d.get("name", sid), ""))
        rows.append({
            "id": sid,
            "name": name,
            "sector": sector,
            "fundamentals": bool(d.get("revenue") and d.get("eps") and d.get("margins")),
            "product_mix": _has_pm(d),
        })

    pm_have = sum(1 for r in rows if r["product_mix"])
    fund_have = sum(1 for r in rows if r["fundamentals"])
    total = len(rows)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    coverage = {
        "generated_at": now,
        "summary": {
            "total": total,
            "product_mix_have": pm_have,
            "product_mix_missing": total - pm_have,
            "fundamentals_have": fund_have,
            "fundamentals_missing": total - fund_have,
        },
        "stocks": rows,
    }
    (DOCS / "coverage.json").write_text(
        json.dumps(coverage, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # Markdown：缺產銷的排前面，方便追補進度
    rows_sorted = sorted(rows, key=lambda r: (r["product_mix"], r["fundamentals"], r["id"]))
    lines = [
        f"# 資料覆蓋率（{now}）",
        "",
        f"- 產銷組合：**{pm_have}/{total}** 有，缺 **{total-pm_have}**",
        f"- 基本面：**{fund_have}/{total}** 有，缺 **{total-fund_have}**",
        "",
        "| 代碼 | 名稱 | 族群 | 基本面 | 產銷組合 |",
        "|------|------|------|:---:|:---:|",
    ]
    for r in rows_sorted:
        lines.append(
            f"| {r['id']} | {r['name']} | {r['sector']} | "
            f"{'✅' if r['fundamentals'] else '❌'} | "
            f"{'✅' if r['product_mix'] else '❌'} |")
    (DOCS / "coverage.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"[coverage] 產銷 {pm_have}/{total}（缺 {total-pm_have}）、"
          f"基本面 {fund_have}/{total}（缺 {total-fund_have}）→ docs/coverage.md, coverage.json")


if __name__ == "__main__":
    main()
