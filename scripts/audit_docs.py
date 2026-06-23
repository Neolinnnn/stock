"""
靜態網站內容健檢（read-only）。
掃描 docs/ 下的個股、基本面、payload 與索引，列出不合理資料。

用法：
  python scripts/audit_docs.py            # 完整報告
  python scripts/audit_docs.py --strict   # 有 critical 問題時回非零 exit（供 CI gate）

設計為純讀取、不修改任何檔案；接進每日 workflow 以 continue-on-error 監測退化。
"""
import argparse
import glob
import json
import os
from datetime import datetime
from pathlib import Path

DOCS = Path(__file__).resolve().parent.parent / "docs"
TODAY = datetime.now()

# (類別, 嚴重度) → 問題清單
report: dict = {}


def add(cat: str, critical: bool, msg: str):
    report.setdefault((cat, critical), []).append(msg)


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        add("JSON 解析", True, f"{os.path.basename(path)}: {e}")
        return None


def audit_stocks():
    for p in sorted(glob.glob(str(DOCS / "stocks" / "*.json"))):
        sid = os.path.basename(p)[:-5]
        d = _load(p)
        if d is None:
            continue
        o = d.get("ohlcv", {})
        cl, dt = o.get("close", []), o.get("date", [])
        if [c for c in cl if c is None or c <= 0]:
            add("個股 OHLCV", True, f"{sid} 含 0/負收盤")
        if len(cl) < 30:
            add("個股 OHLCV", False, f"{sid} K 棒僅 {len(cl)} 根")
        if len(dt) != len(set(dt)):
            add("個股 OHLCV", True, f"{sid} 日期重複")
        ind = d.get("indicators", {})
        for k in ("ma5", "ma20", "ma60", "macd", "kd_k"):
            v = ind.get(k, [])
            if v and v[-1] is None:
                add("個股指標", True, f"{sid}.{k} 末值為 null")
                break
        ma20 = ind.get("ma20", [])
        if cl and ma20 and ma20[-1]:
            gap = abs(cl[-1] / ma20[-1] - 1) * 100
            if gap > 40:
                add("個股指標", False, f"{sid} close/ma20 背離 {gap:.0f}%（{cl[-1]} vs {round(ma20[-1],1)}）")
        g = d.get("generated_at", "")
        try:
            if (TODAY - datetime.strptime(g[:10], "%Y-%m-%d")).days > 5:
                add("個股過期", False, f"{sid} generated_at={g}")
        except Exception:
            add("個股過期", False, f"{sid} generated_at 無效：{g!r}")


def audit_fundamentals():
    for p in sorted(glob.glob(str(DOCS / "fundamentals" / "*.json"))):
        sid = os.path.basename(p)[:-5]
        d = _load(p)
        if d is None:
            continue
        if not d.get("revenue"):
            add("基本面缺營收", False, sid)
        if not d.get("eps"):
            add("基本面缺 EPS", False, sid)
        m = d.get("margins")
        if not m:
            add("基本面缺三率", False, sid)
        else:
            nm, gm = m.get("net_margin", []), m.get("gross_margin", [])
            if nm and gm and nm[-1] == 0 and gm[-1] and gm[-1] > 0:
                add("淨利率=0 異常", True, f"{sid}: net=0 gross={gm[-1]}")
            if nm and gm and nm[-1] and gm[-1] and nm[-1] > gm[-1] + 5:
                add("淨利率>毛利率", False, f"{sid}: net={nm[-1]} gross={gm[-1]}")
        pm = d.get("product_mix")
        if not pm:
            add("缺產銷組合", False, sid)
        else:
            lines = pm.get("product_lines", [])
            shares = [l.get("share_pct") for l in lines]
            total = sum(s for s in shares if isinstance(s, (int, float)))
            if any(s is None for s in shares):
                add("產銷佔比含 None", False, f"{sid}（{len(lines)} 條）")
            elif lines and (total > 120 or total < 80):
                add("產銷佔比和異常", True, f"{sid}: 和={total}")


def audit_consistency():
    dates = _load(DOCS / "dates.json") or []
    actual = sorted([os.path.basename(p)[:-5]
                     for p in glob.glob(str(DOCS / "[0-9]*.json"))], reverse=True)
    for d in set(actual) - set(dates):
        add("日期索引不一致", False, f"有檔但 dates.json 漏列 {d}")
    for d in set(dates) - set(actual):
        add("日期索引不一致", True, f"dates.json 有 {d} 但無檔")

    idx = _load(DOCS / "stocks_index.json") or []
    idx_ids = {x.get("id") for x in idx}
    files = {os.path.basename(p)[:-5] for p in glob.glob(str(DOCS / "stocks" / "*.json"))}
    for s in sorted(idx_ids - files):
        add("個股索引不一致", True, f"index 有 {s} 但無檔")
    for s in sorted(files - idx_ids):
        add("個股索引不一致", False, f"有檔但 index 漏列 {s}")

    daily = _load(DOCS / "daily.json")
    if daily:
        sd = daily.get("meta", {}).get("掃描日期")
        if sd and actual and sd != actual[0]:
            add("daily 落後", False, f"daily.json={sd} 但最新日期檔={actual[0]}")


def main():
    ap = argparse.ArgumentParser(description="docs/ 靜態內容健檢")
    ap.add_argument("--strict", action="store_true", help="有 critical 問題時回非零 exit")
    args = ap.parse_args()

    audit_stocks()
    audit_fundamentals()
    audit_consistency()

    crit = sum(len(v) for (c, k), v in report.items() if k)
    warn = sum(len(v) for (c, k), v in report.items() if not k)
    print(f"=== docs 健檢報告 {TODAY:%Y-%m-%d %H:%M} ===")
    print(f"critical={crit}  warning={warn}\n")
    for (cat, critical), items in sorted(report.items(), key=lambda x: (not x[0][1], x[0][0])):
        tag = "🔴" if critical else "🟡"
        print(f"{tag} [{cat}] {len(items)} 筆")
        for x in items[:20]:
            print("    ", x)
        if len(items) > 20:
            print(f"     ...另 {len(items)-20} 筆")
    if not report:
        print("✅ 無異常")

    if args.strict and crit:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
