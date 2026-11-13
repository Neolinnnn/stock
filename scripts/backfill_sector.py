"""
把新設族群補進既有的歷史日報（daily_reports/YYYYMMDD/summary.json）。

新族群加入 daily_scan.SECTORS 後只會出現在往後的掃描；本腳本以「截至各報告日」
的日 K 與三大法人資料，補算該族群在歷史各日的個股紀錄與族群統計。

原則（見 CLAUDE.md「歷史報告不得事後改寫」）：
  - 每一日只用該日（含）以前的資料，計算函式與該日報告的產生方式一致：
      舊格式（回補產生，個股無 rsi10）→ backfill_history.analyze_stock_on_date
      現行格式（每日掃描產生）        → batch_scan.analyze_stock ＋ daily_scan.build_summary
    「趨勢延續 BUY」自 2026-09-23 起才生效，之前的日期關閉該規則。
  - 只新增該族群區塊並更新強／弱勢族群名單；既有族群、qualified、dual_filter、
    alerts、positions 一律不動——那些是當時實際發佈的內容
  - 個股欄位以同日既有個股為準，同日報告沒有的欄位不寫（例：2026-11-11 前無 trade_value）
  - 無法回溯的欄位留空：news、broker、main_force、target_mid、target_long
  - 補建的族群區塊帶 'backfilled': 補建日期；--force 只重算帶此標記者，
    每日掃描實際產出的區塊永不覆寫

用法：
    python scripts/backfill_sector.py 玻纖布
    python scripts/backfill_sector.py 玻纖布 --dry-run
    python scripts/backfill_sector.py 玻纖布 --force     # 重算先前補建的日期
"""
import argparse
import contextlib
import io
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / 'daily_reports'
sys.path.insert(0, str(Path(__file__).resolve().parent))

with contextlib.redirect_stdout(io.StringIO()):   # 兩個模組匯入時會印載入訊息
    import backfill_history as bf
    import daily_scan as ds
from datafeed import CachedStock, finmind_fetch
from indicators.chip import chip_tier

# 「趨勢延續 BUY」（batch_scan.ENABLE_TREND_CONTINUATION_BUY）生效的第一份日報。
# 以既有個股對照：此日之前關閉該規則才能重現當時的訊號
TREND_BUY_SINCE = '20260923'
# 第一份日報之前需預留的曆日：CV 與 6 維評分取最近 500 根 K 棒
LOOKBACK_DAYS = 1000


def fetch_history(sid, start, end):
    """抓一檔的日 K 與三大法人買賣超，回傳 (price_df, inst_df)，date 欄為 date 物件。"""
    px = finmind_fetch('taiwan_stock_daily', stock_id=sid, start_date=start, end_date=end)
    if px is None or px.empty:
        raise ValueError(f'{sid} 無日 K 資料')
    px = px.rename(columns={'max': 'high', 'min': 'low', 'Trading_Volume': 'volume'})
    px['date'] = pd.to_datetime(px['date']).dt.date
    for col in ('open', 'high', 'low', 'close', 'volume'):
        px[col] = pd.to_numeric(px[col], errors='coerce')
    px = (px.dropna(subset=['close']).sort_values('date')
          [['date', 'open', 'high', 'low', 'close', 'volume']].reset_index(drop=True))

    inst = finmind_fetch('taiwan_stock_institutional_investors', stock_id=sid,
                         start_date=start, end_date=end)
    if inst is None or inst.empty:
        inst = pd.DataFrame(columns=['date', 'name', 'buy', 'sell'])
    else:
        inst = inst[['date', 'name', 'buy', 'sell']].copy()
        inst['date'] = pd.to_datetime(inst['date']).dt.date
    return px, inst


def chip_asof(inst, day, vol5, days=5):
    """截至 day 的三大法人籌碼，欄位與單位（張）同 daily_scan.fetch_chip_data。

    法人資料最新一日不是 day 時回 {}：與每日掃描「法人資料非當日即清空」一致。
    """
    df = inst[inst['date'] <= day]
    dates = sorted(df['date'].unique(), reverse=True)[:days]
    if not dates or dates[0] != day:
        return {}
    daily = []
    for d in dates:
        agg = {'外資': 0, '投信': 0, '自營': 0}
        for _, row in df[df['date'] == d].iterrows():
            zh = ds._CHIP_MAP.get(str(row['name']))
            if zh:
                agg[zh] += int(row['buy'] - row['sell'])
        for k in agg:
            agg[k] = int(agg[k] / 1000)      # 股 → 張，向零截斷
        agg['合計'] = agg['外資'] + agg['投信'] + agg['自營']
        daily.append(agg)

    def streak(key):
        n = 0
        for d in daily:
            if d[key] <= 0:
                break
            n += 1
        return n

    chip = {
        'date': str(day), **daily[0],
        '近5日':     sum(d['合計'] for d in daily),
        '近5日外資': sum(d['外資'] for d in daily),
        '近5日投信': sum(d['投信'] for d in daily),
        '連買日':     streak('合計'),
        '投信連買日': streak('投信'),
    }
    v5 = [v for v in vol5 if v]
    chip['集中度'] = ds.chip_concentration(chip['近5日'], sum(v5) if len(v5) == 5 else None)
    chip['土洋同買'] = bool(chip['近5日外資'] > 0 and chip['近5日投信'] > 0)
    return chip


def live_record(sid, name, day, date_str, px, inst):
    """現行格式：以截至 day 的 K 棒重跑每日掃描的個股分析（對應 scan_sector._scan_one_uncached）。"""
    sub = px[px['date'] <= day]
    hist = CachedStock(sub['close'].tolist(), sub['date'].tolist(), sub['high'].tolist(),
                       sub['low'].tolist(), sub['volume'].astype(int).tolist())
    ds.ENABLE_TREND_CONTINUATION_BUY = date_str >= TREND_BUY_SINCE
    r = ds.analyze_stock(sid, name, hist=hist)
    if 'error' in r:
        return r
    p = hist.price
    r['trade_value'] = round(p[-1] * hist.volume[-1])
    r['ma10'] = round(sum(p[-10:]) / 10, 2) if len(p) >= 10 else None
    r['ma60'] = round(sum(p[-60:]) / 60, 2) if len(p) >= 60 else None
    r['ret_20d'] = (p[-1] - p[-21]) / p[-21]
    r['target_short'] = round(sum(p[-20:]) / 20 + 2 * statistics.stdev(p[-20:]), 1)   # 布林上軌
    r['atr14'], r['stop_loss'] = ds._atr_stop_loss(hist.high, hist.low, p)
    r['chip'] = chip_asof(inst, day, hist.volume[-5:])
    r['chip_tier'] = chip_tier(r['chip'].get('集中度'), r['chip'].get('土洋同買', False), None)
    r['news'] = []
    return r


def build_sector_entry(sector, stocks, date_str, data, keys):
    """算出某日的族群區塊。回傳 (entry, 強弱名單 dict)；全部個股資料不足時 entry 為 None。"""
    day = datetime.strptime(date_str, '%Y%m%d').date()
    live = 'rsi10' in keys
    results = []
    for sid, name in stocks.items():
        px, inst = data[sid]
        if live:
            results.append(live_record(sid, name, day, date_str, px, inst))
        else:
            results.append(bf.analyze_stock_on_date(sid, name, day, px, {}))
    all_results = {sector: results}
    built = (ds.build_summary(date_str, None, all_results, None) if live
             else bf.build_summary(date_str, all_results, None))
    entry = built['sectors'].get(sector)
    if entry:
        entry['stocks'] = [{k: v for k, v in st.items() if k in keys} for st in entry['stocks']]
    return entry, built


def _dump_like(original, obj):
    """沿用原檔的 JSON 排版（多數為 indent=2，少數為單行），避免整檔格式變動。"""
    styles = [dict(indent=2), dict(), dict(separators=(',', ':'))]
    for style in styles:
        if json.dumps(json.loads(original), ensure_ascii=False, default=str, **style) == original:
            return json.dumps(obj, ensure_ascii=False, default=str, **style)
    return json.dumps(obj, ensure_ascii=False, default=str, indent=2)


def backfill_report(path, sector, stocks, data, tag, force=False, dry_run=False):
    """補一份日報。回傳 'patched'／'exists'（已有且非補建或未 --force）／'no_data'。"""
    original = path.read_text(encoding='utf-8')
    summary = json.loads(original)
    sectors = summary.setdefault('sectors', {})
    existing = sectors.get(sector)
    if existing and not (force and existing.get('backfilled')):
        return 'exists'
    peers = [st for name, sec in sectors.items() if name != sector
             for st in sec.get('stocks', []) if 'error' not in st]
    if not peers:
        return 'no_data'
    keys = set().union(*(st.keys() for st in peers))

    entry, built = build_sector_entry(sector, stocks, path.parent.name, data, keys)
    if not entry:
        return 'no_data'
    entry['backfilled'] = tag
    sectors[sector] = entry
    for key in ('strong_sectors', 'weak_sectors'):
        names = [s for s in summary.get(key, []) if s != sector]
        if sector in built[key]:
            names.append(sector)
        summary[key] = names
    if not dry_run:
        path.write_text(_dump_like(original, summary), encoding='utf-8')
    return 'patched'


def main():
    ap = argparse.ArgumentParser(description='把新設族群補進歷史日報')
    ap.add_argument('sector', help='daily_scan.SECTORS 中的族群名稱')
    ap.add_argument('--force', action='store_true', help='重算先前補建（帶 backfilled 標記）的日期')
    ap.add_argument('--dry-run', action='store_true', help='只計算不寫檔')
    args = ap.parse_args()

    if args.sector not in ds.SECTORS:
        sys.exit(f'daily_scan.SECTORS 沒有「{args.sector}」')
    stocks = ds.SECTORS[args.sector]
    paths = sorted(p for p in REPORTS_DIR.glob('[0-9]' * 8 + '/summary.json'))
    if not paths:
        sys.exit('找不到任何日報')

    first, last = paths[0].parent.name, paths[-1].parent.name
    start = (datetime.strptime(first, '%Y%m%d') - timedelta(days=LOOKBACK_DAYS)).strftime('%Y-%m-%d')
    end = datetime.strptime(last, '%Y%m%d').strftime('%Y-%m-%d')
    print(f'📥 下載 {len(stocks)} 檔日 K 與法人資料：{start} ~ {end}')
    data = {sid: fetch_history(sid, start, end) for sid in stocks}

    tag = datetime.now().strftime('%Y-%m-%d')
    counts = {'patched': 0, 'exists': 0, 'no_data': 0}
    for p in paths:
        counts[backfill_report(p, args.sector, stocks, data, tag,
                               force=args.force, dry_run=args.dry_run)] += 1
    print(f"{'（dry-run）' if args.dry_run else ''}{args.sector}：補入 {counts['patched']} 日、"
          f"已有 {counts['exists']} 日、資料不足 {counts['no_data']} 日")


if __name__ == '__main__':
    main()
