"""
產銷組合資料抓取腳本
對指定股票呼叫 Gemini（含 Google Search grounding）自動整理產銷組合，
結果快取至 docs/fundamentals/{sid}.json 的 product_mix 欄位。

用法：
  python scripts/enrich_product_mix.py --sids 2330 2345 2379
  python scripts/enrich_product_mix.py --all          # 掃描所有 fundamentals
  python scripts/enrich_product_mix.py --qualified    # 只處理今日 qualified 股票
  python scripts/enrich_product_mix.py --force --sids 2345  # 強制重新抓取
"""
import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gemini_writer import GeminiWriter

DOCS_DIR = ROOT / "docs"
FUND_DIR = DOCS_DIR / "fundamentals"
REFRESH_DAYS = 30  # 超過此天數才重新抓取


def _load_fund(sid: str) -> dict:
    path = FUND_DIR / f"{sid}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_fund(sid: str, data: dict) -> None:
    path = FUND_DIR / f"{sid}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _is_stale(pm: dict) -> bool:
    """product_mix 超過 REFRESH_DAYS 天或不存在則視為過期"""
    if not pm:
        return True
    updated = pm.get("updated_at", "")
    if not updated:
        return True
    try:
        dt = datetime.strptime(updated[:10], "%Y-%m-%d").date()
        return (date.today() - dt).days > REFRESH_DAYS
    except Exception:
        return True


def _parse_json_from_text(text: str) -> dict:
    """從 Gemini 回覆中萃取 JSON（防止被 markdown 包住）"""
    # 先嘗試直接 parse
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 嘗試從 code block 中抽取
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass
    # 嘗試找最外層的 { }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    return {}


def enrich_one(sid: str, name: str, writer: GeminiWriter, force: bool = False) -> bool:
    """
    對單一股票抓取產銷組合。
    回傳 True 表示有更新，False 表示略過（已是最新）。
    """
    fund = _load_fund(sid)
    existing_pm = fund.get("product_mix", {})

    if not force and not _is_stale(existing_pm):
        print(f"  [{sid}] {name} — 資料仍新鮮，略過（更新於 {existing_pm.get('updated_at','')}）")
        return False

    print(f"  [{sid}] {name} — 呼叫 Gemini 抓取中…")
    today = date.today().isoformat()
    try:
        raw = writer.generate(
            task="product_mix",
            context={
                "data": f"{sid} {name}（台灣上市公司）",
                "date": today,
                "extra": "如果搜尋不到足夠資訊，請根據已知的公司類型給出合理估計，並在 summary 中說明資料可信度。",
            },
            use_grounding=True,
        )
    except Exception as e:
        print(f"  [{sid}] Gemini 呼叫失敗：{e}")
        return False

    pm = _parse_json_from_text(raw)
    if not pm or "product_lines" not in pm:
        print(f"  [{sid}] JSON 解析失敗，原始回覆前 200 字：{raw[:200]}")
        return False

    pm["updated_at"] = today
    fund["product_mix"] = pm
    _save_fund(sid, fund)
    lines = pm.get("product_lines", [])
    print(f"  [{sid}] 完成 — {len(lines)} 條產品線，期間：{pm.get('data_period','')}")
    return True


def get_sids_from_fundamentals() -> list[tuple[str, str]]:
    """從所有 fundamentals JSON 取得 (sid, name) 清單"""
    result = []
    for path in sorted(FUND_DIR.glob("*.json")):
        sid = path.stem
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            name = d.get("name", sid)
            result.append((sid, name))
        except Exception:
            pass
    return result


def get_sids_from_qualified() -> list[tuple[str, str]]:
    """從最近一份每日 JSON 取得 qualified 股票的 (sid, name)"""
    date_jsons = sorted(DOCS_DIR.glob("????????.json"), reverse=True)
    for path in date_jsons[:5]:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            qualified = d.get("qualified", [])
            if qualified:
                return [(q["id"], q.get("name", q["id"])) for q in qualified if "id" in q]
        except Exception:
            continue
    return []


def get_sids_missing_pm() -> list[tuple[str, str]]:
    """從 fundamentals 目錄取得尚無 product_mix 的 (sid, name) 清單"""
    result = []
    for path in sorted(FUND_DIR.glob("*.json")):
        sid = path.stem
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if not d.get("product_mix"):
                result.append((sid, d.get("name", sid)))
        except Exception:
            pass
    return result


def main():
    parser = argparse.ArgumentParser(description="產銷組合資料抓取")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--all", action="store_true", help="處理所有 fundamentals 股票")
    group.add_argument("--missing", action="store_true", help="只處理尚無 product_mix 的股票")
    group.add_argument("--qualified", action="store_true", help="只處理今日 qualified 股票")
    group.add_argument("--sids", nargs="+", metavar="SID", help="指定股票代碼")
    parser.add_argument("--force", action="store_true", help="強制重新抓取（忽略快取時效）")
    parser.add_argument("--limit", type=int, default=0, metavar="N",
                        help="每次最多處理 N 支（用於每日分批，預設不限）")
    args = parser.parse_args()

    # 建立 Gemini writer
    try:
        writer = GeminiWriter()
    except ValueError as e:
        print(f"[ERROR] {e}")
        sys.exit(1)

    # 決定目標清單
    if args.all:
        targets = get_sids_from_fundamentals()
    elif args.missing:
        targets = get_sids_missing_pm()
        if not targets:
            print("[INFO] 所有 fundamentals 股票已有 product_mix，無需更新")
            sys.exit(0)
    elif args.qualified:
        targets = get_sids_from_qualified()
        if not targets:
            print("[WARN] 找不到 qualified 股票")
            sys.exit(0)
    elif args.sids:
        fund_map = {sid: name for sid, name in get_sids_from_fundamentals()}
        targets = [(sid, fund_map.get(sid, sid)) for sid in args.sids]
    else:
        parser.print_help()
        sys.exit(0)

    if args.limit > 0:
        targets = targets[: args.limit]

    print(f"[enrich_product_mix] 共 {len(targets)} 支股票，force={args.force}")
    updated = 0
    for sid, name in targets:
        if enrich_one(sid, name, writer, force=args.force):
            updated += 1

    print(f"\n[完成] 更新 {updated}/{len(targets)} 支")


if __name__ == "__main__":
    main()
