"""
單檔即時掃描腳本。
吃一個股票代碼，依序產生三層資料（全部複用每日批次既有函式）：
  1. 技術面 → docs/stocks/{sid}.json      （build_docs._build_single_stock）
  2. 基本面 → docs/fundamentals/{sid}.json （fundamentals_fetcher.build_fundamentals）
  3. 產銷組合 → 寫回 fundamentals 的 product_mix（enrich_product_mix.enrich_one）

供「加入自選即掃」的 scan_one.yml workflow 使用，不在此 commit（交給 workflow）。

用法：
  python scripts/scan_one_stock.py 2316
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT))

DOCS_DIR = ROOT / "docs"


def resolve_name(sid: str) -> str:
    """先用 twstock 本地代碼表，查不到再退回 FinMind taiwan_stock_info。"""
    try:
        import twstock
        c = twstock.codes.get(sid)
        if c and c.name:
            return c.name
    except Exception:
        pass
    try:
        from datafeed import finmind_fetch
        df = finmind_fetch("taiwan_stock_info", stock_id=sid)
        if df is not None and len(df):
            col = "stock_name" if "stock_name" in df.columns else None
            if col:
                return str(df.iloc[0][col])
    except Exception:
        pass
    return sid  # 最後退回代碼本身


def scan_technical(sid: str, name: str) -> bool:
    """產生 docs/stocks/{sid}.json，並把個股併入 stocks_index.json。"""
    import build_docs
    stocks_dir = DOCS_DIR / "stocks"
    stocks_dir.mkdir(parents=True, exist_ok=True)
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=180)).strftime("%Y-%m-%d")
    info = {"id": sid, "name": name, "sector": "自選"}
    build_docs._build_single_stock(
        sid, info, stocks_dir, start_date, end_date, start_date
    )
    ok = (stocks_dir / f"{sid}.json").exists()
    if ok:
        _upsert_index(sid, name)
    return ok


def _upsert_index(sid: str, name: str) -> None:
    """個股不存在於 stocks_index.json 才 append，避免索引漏掉自選股。"""
    import json
    path = DOCS_DIR / "stocks_index.json"
    try:
        idx = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except Exception:
        idx = []
    if not any(x.get("id") == sid for x in idx):
        idx.append({"id": sid, "name": name, "sector": "自選"})
        path.write_text(json.dumps(idx, ensure_ascii=False, separators=(",", ":")),
                        encoding="utf-8")


def scan_fundamentals(sid: str, name: str) -> bool:
    """產生 docs/fundamentals/{sid}.json（月營收/EPS/三率）。skip_days=0 強制更新。"""
    import fundamentals_fetcher
    fundamentals_fetcher.build_fundamentals([{"id": sid, "name": name}], skip_days=0)
    return (DOCS_DIR / "fundamentals" / f"{sid}.json").exists()


def scan_product_mix(sid: str, name: str) -> bool:
    """呼叫 Gemini 抓產銷組合，寫回 fundamentals 的 product_mix。"""
    import enrich_product_mix
    from gemini_writer import GeminiWriter
    writer = GeminiWriter()
    return enrich_product_mix.enrich_one(sid, name, writer, force=True)


def main():
    if len(sys.argv) < 2:
        print("用法：python scripts/scan_one_stock.py <股票代碼>")
        sys.exit(1)
    sid = sys.argv[1].strip().upper()
    name = resolve_name(sid)
    print(f"[scan_one] {sid} {name} — 開始單檔三層掃描")

    # 各層獨立容錯：某層失敗印警告，不中斷其餘層
    for label, fn in (
        ("技術面", scan_technical),
        ("基本面", scan_fundamentals),
        ("產銷組合", scan_product_mix),
    ):
        try:
            ok = fn(sid, name)
            print(f"  [{'OK' if ok else 'WARN'}] {label}{'完成' if ok else '無產出'}")
        except Exception as e:
            print(f"  [WARN] {label}失敗：{e}")

    print(f"[scan_one] {sid} {name} — 結束")


if __name__ == "__main__":
    main()
