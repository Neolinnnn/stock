# -*- coding: utf-8 -*-
"""
訊號結案資料集建置 — Meta-labeling 訓練資料的地基
====================================================
回放 daily_reports/*/summary.json 全部歷史（2023/02 起）：

  1. 重建每檔個股的「掃描日收盤價序列」（報告價已驗證與 FinMind 收盤一致），
     離線推導 MA5/10/20/60、動能、波動、60 日高點距離等特徵。
  2. 對每段「連續 BUY」的起始日各取一筆訊號（同段後續 BUY 日不重複取樣），
     次一掃描日收盤進場，之後依 HYBRID 三段式出場結算（與
     position_tracker.py 同參數：SL15% / 觸發15% / 地板7% / 破MA10 / 25日未創高）。
     註：不同段的持有期間可能重疊（真實交易同檔只會持一倉），取樣目的是
     最大化訓練樣本數；可交易基準勝率以回測腳本為準。
  3. 每筆 episode 配上「訊號日特徵快照」（見 model/meta_features.py），
     輸出 model/signal_dataset.csv。

大盤 MA60（taiex_bull/taiex_bias）取自 backtest_cache/TAIEX_ohlcv.csv，
並以報告內 market.加權指數 延伸；快取起點（2024/10）之前留 NaN。

用法：
    python scripts/build_signal_dataset.py
"""
import csv
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from model.meta_features import FEATURES, build_features  # noqa: E402

REPORTS_DIR = ROOT / "daily_reports"
TAIEX_CACHE = ROOT / "backtest_cache" / "TAIEX_ohlcv.csv"
TAIEX_HIST  = ROOT / "model" / "taiex_history.csv"   # 早期補齊（隨 repo 提交）
OUT_CSV     = ROOT / "model" / "signal_dataset.csv"

# HYBRID 出場參數（與 scripts/position_tracker.py 一致）
PROFIT_TRIGGER = 0.15
PROFIT_FLOOR   = 0.07
PHASE1_SL      = 0.15
PHASE2_TIMEOUT = 25
MAX_HORIZON    = 130   # 超過此掃描日數仍未出場 → 視為未結案（不進訓練）

META_COLS = [
    "stock_id", "name", "sector", "signal_date",
    "entry_date", "entry_price", "exit_date", "exit_price",
    "exit_reason", "holding_days", "return_pct", "label",
]


def load_reports():
    """讀入全部 summary.json → (掃描日清單, 個股日記錄, 族群日資訊, 大盤日收盤)。"""
    records = {}       # sid -> {date: stock_dict(+sector)}
    sector_info = {}   # date -> {sector: {"ret20": float, "strong": bool}}
    taiex_report = {}  # date -> close（近期報告才有）
    dates = []
    for day_dir in sorted(REPORTS_DIR.iterdir()):
        f = day_dir / "summary.json"
        if not day_dir.is_dir() or not f.exists():
            continue
        try:
            summary = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        date = day_dir.name
        dates.append(date)
        tx = (summary.get("market") or {}).get("加權指數")
        if tx:
            taiex_report[date] = float(tx)
        sector_info[date] = {}
        for sector, data in (summary.get("sectors") or {}).items():
            avg_ret = data.get("avg_ret_20d")
            sector_info[date][sector] = {
                "ret20": avg_ret,
                "strong": bool(avg_ret is not None and avg_ret > 3),
            }
            for st in data.get("stocks", []):
                sid = st.get("id")
                if not sid or st.get("price") is None:
                    continue
                # 同檔跨族群重複入選（如 3711）只取首見族群
                records.setdefault(sid, {}).setdefault(
                    date, {**st, "sector": sector})
    return dates, records, sector_info, taiex_report


def _backfill_taiex_history(first_needed):
    """補齊 backtest_cache 起點以前的 TAIEX 收盤（FinMind 免 token 資料集）。

    只在 TAIEX_HIST 不存在時抓一次，寫入後隨 repo 提交；離線環境直接略過。
    first_needed：最早掃描日 YYYYMMDD，往前多抓 120 曆日以涵蓋 MA60。
    """
    if TAIEX_HIST.exists():
        return
    import urllib.request
    from datetime import datetime, timedelta
    start = (datetime.strptime(first_needed, "%Y%m%d")
             - timedelta(days=120)).strftime("%Y-%m-%d")
    url = ("https://api.finmindtrade.com/api/v4/data?dataset=TaiwanStockPrice"
           f"&data_id=TAIEX&start_date={start}&end_date=2024-10-01")
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            data = json.loads(r.read()).get("data", [])
        if not data:
            return
        with open(TAIEX_HIST, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "close"])
            for row in data:
                w.writerow([row["date"].replace("-", ""), row["close"]])
        print(f"TAIEX 早期歷史已補齊 → {TAIEX_HIST}（{len(data)} 日）")
    except Exception as e:
        print(f"TAIEX 歷史補齊失敗（離線？）：{e} — 早期大盤特徵將留 NaN")


def load_taiex(taiex_report, first_scan_date):
    """合併早期補齊檔、快取 CSV、報告內大盤收盤 → 依日期排序的 (dates, closes)。"""
    _backfill_taiex_history(first_scan_date)
    closes = {}
    if TAIEX_HIST.exists():
        with open(TAIEX_HIST, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                closes[row["date"]] = float(row["close"])
    if TAIEX_CACHE.exists():
        with open(TAIEX_CACHE, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                closes[row["date"]] = float(row["close"])
    for d, c in taiex_report.items():
        closes.setdefault(d, c)
    ds = sorted(closes)
    return ds, [closes[d] for d in ds]


def taiex_state(scan_date, tx_dates, tx_closes):
    """回傳掃描日的 (taiex_bull, taiex_bias%)；資料不足 → (None, None)。"""
    # 找最後一個 <= scan_date 的索引（tx_dates 已排序）
    import bisect
    i = bisect.bisect_right(tx_dates, scan_date) - 1
    if i < 59:
        return None, None
    close = tx_closes[i]
    ma60 = sum(tx_closes[i - 59:i + 1]) / 60
    return (1 if close > ma60 else 0), round((close - ma60) / ma60 * 100, 2)


def derive_series_features(closes, i):
    """由掃描日收盤序列在索引 i 推導特徵。closes[:i+1] 為訊號日（含）以前。"""
    window = closes[:i + 1]
    n = len(window)
    c = window[-1]

    def ma(k):
        return sum(window[-k:]) / k if n >= k else None

    def mom(k):
        return (c / window[-k - 1] - 1) * 100 if n >= k + 1 and window[-k - 1] else None

    vol20 = None
    if n >= 21:
        rets = [(window[j] / window[j - 1] - 1) * 100
                for j in range(n - 20, n) if window[j - 1]]
        if len(rets) >= 2:
            vol20 = statistics.stdev(rets)

    hi60 = max(window[-60:]) if n >= 20 else None  # 不足 60 日用現有窗口
    dist_high60 = (c / hi60 - 1) * 100 if hi60 else None

    return {
        "ma5": ma(5), "ma10": ma(10), "ma20": ma(20), "ma60": ma(60),
        "mom5": mom(5), "mom20": mom(20),
        "vol20": vol20, "dist_high60": dist_high60,
    }


def settle_hybrid(closes, ma10s, entry_i):
    """自 entry_i（進場日索引）起依 HYBRID 規則結算。

    回傳 (exit_i, exit_reason, return_pct) 或 (None, "OPEN", None) 未結案。
    """
    entry = closes[entry_i]
    trigger = entry * (1 + PROFIT_TRIGGER)
    floor_p = entry * (1 + PROFIT_FLOOR)
    sl_p    = entry * (1 - PHASE1_SL)
    phase, hwm, days_since_high = 1, entry, 0

    for j in range(entry_i + 1, min(entry_i + 1 + MAX_HORIZON, len(closes))):
        p = closes[j]
        if phase == 1:
            if p <= sl_p:
                return j, "SL", (p - entry) / entry * 100
            if p >= trigger:
                phase, hwm, days_since_high = 2, p, 0
            continue
        # Phase 2
        if p > hwm:
            hwm, days_since_high = p, 0
        else:
            days_since_high += 1
        if p <= floor_p:
            return j, "FLOOR", (p - entry) / entry * 100
        ma10 = ma10s[j]
        if ma10 is not None and p < ma10:
            return j, "MA10", (p - entry) / entry * 100
        if days_since_high >= PHASE2_TIMEOUT:
            return j, "TIME", (p - entry) / entry * 100
    return None, "OPEN", None


def build_dataset():
    dates, records, sector_info, taiex_report = load_reports()
    tx_dates, tx_closes = load_taiex(taiex_report, dates[0])
    print(f"報告 {len(dates)} 日、個股 {len(records)} 檔、"
          f"大盤序列 {len(tx_dates)} 日")

    rows = []
    for sid, day_map in sorted(records.items()):
        sdates = sorted(day_map)
        closes = [float(day_map[d]["price"]) for d in sdates]
        ma10s = [sum(closes[max(0, k - 9):k + 1]) / min(k + 1, 10)
                 if k >= 9 else None for k in range(len(closes))]

        prev_buy = False  # 連續 BUY 段只取起始日
        for i, d in enumerate(sdates):
            st = day_map[d]
            is_buy = st.get("signal") == "BUY"
            run_start = is_buy and not prev_buy
            prev_buy = is_buy
            if not run_start:
                continue
            if i + 1 >= len(sdates):
                continue  # 無次日可進場
            entry_i = i + 1
            # 訊號日與進場日間隔過久（個股長期缺掃描）→ 放棄該訊號
            if _gap_days(d, sdates[entry_i]) > 7:
                continue

            exit_i, reason, ret = settle_hybrid(closes, ma10s, entry_i)

            sec = st.get("sector", "")
            sinfo = (sector_info.get(d) or {}).get(sec) or {}
            tb, tbias = taiex_state(d, tx_dates, tx_closes)
            feats = build_features(
                {**st, **derive_series_features(closes, i)},
                sector_ret20=sinfo.get("ret20"),
                sector_strong=(1 if sinfo.get("strong") else 0),
                taiex_bull=tb, taiex_bias=tbias,
            )
            rows.append({
                "stock_id": sid,
                "name": st.get("name", ""),
                "sector": sec,
                "signal_date": d,
                "entry_date": sdates[entry_i],
                "entry_price": closes[entry_i],
                "exit_date": sdates[exit_i] if exit_i is not None else "",
                "exit_price": round(closes[exit_i], 2) if exit_i is not None else "",
                "exit_reason": reason,
                "holding_days": (exit_i - entry_i) if exit_i is not None else "",
                "return_pct": round(ret, 2) if ret is not None else "",
                "label": ("" if ret is None else (1 if ret > 0 else 0)),
                **{k: ("" if v is None or (isinstance(v, float) and math.isnan(v))
                       else round(v, 4)) for k, v in feats.items()},
            })

    rows.sort(key=lambda r: (r["signal_date"], r["stock_id"]))
    OUT_CSV.parent.mkdir(exist_ok=True)
    with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=META_COLS + FEATURES)
        w.writeheader()
        w.writerows(rows)

    settled = [r for r in rows if r["label"] != ""]
    wins = sum(1 for r in settled if r["label"] == 1)
    rets = [r["return_pct"] for r in settled]
    print(f"\n輸出 → {OUT_CSV}")
    print(f"訊號樣本 {len(rows)}（結案 {len(settled)}、未結案 {len(rows) - len(settled)}）")
    if settled:
        print(f"基礎勝率 {wins / len(settled):.1%}、平均報酬 {sum(rets) / len(rets):+.2f}%")
        from collections import Counter
        print("出場原因分布：", dict(Counter(r["exit_reason"] for r in settled)))
    return rows


def _gap_days(d1, d2):
    from datetime import datetime
    return (datetime.strptime(d2, "%Y%m%d") - datetime.strptime(d1, "%Y%m%d")).days


if __name__ == "__main__":
    build_dataset()
