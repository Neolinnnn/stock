"""
單檔歷史補建：把新加入追蹤池的個股，以「截至當日」的資料補進既有每日報告。

為何需要：個股加入 daily_scan.SECTORS 後只會從下一次掃描開始出現，
回測（backtest.load_buy_signals 讀各日 sectors[*].stocks）與個股頁在加入前
都看不到它。本腳本逐日重建該股當日的掃描列，插入既有 summary.json。

前視偏差防護（CLAUDE.md「歷史報告不得事後改寫」）：
  - 直接呼叫 daily_scan.scan_sector() / build_summary()，訊號、CV、目標價、
    停損、籌碼的計算與每日掃描同一份程式碼
  - 只替換資料源：日 K 截至當日、本益比取當日往前一年、法人買賣超截至
    該報告其他個股實際拿到的籌碼日（daily_scan 跑太早時會沿用前一日，照實重現）
  - 規則套「當日生效版本」：RULE_CUTOVERS 列出期間內改過的規則與生效報告日，
    生效日前改回舊值（例：趨勢延續 BUY 自 20260923 才上線，之前不得給 BUY）
  - 新聞與分點無法回溯取得：news=[]、broker/main_force=None，
    chip_tier 因主力分數缺漏照 chip_tier() 規則降為 neutral
  - 只「新增」該股列並遞增更新所屬族群的彙總、強弱、推薦與警示；
    既有個股列一字不改。已存在該股的報告直接跳過（可重複執行）
  - 不動 positions（持倉追蹤是逐日累積的即時紀錄，不可事後補進場）
  - 補建列加註 "backfilled": 補建日期，與即時掃描列區分

驗證：--verify 對「報告中已存在」的個股用同一流程重算並逐欄比對，
用來確認資料源切片與當日掃描一致；不寫入任何檔案。

用法：
  python scripts/backfill_stock.py 6669 --sector AI伺服器 --start 20260901
  python scripts/backfill_stock.py 2382 --sector AI伺服器 --start 20260901 --verify
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))

import daily_scan as ds  # noqa: E402

REPORTS = ROOT / 'daily_reports'
PRICE_LOOKBACK = int(ds.DATA_DAYS * 1.6)   # 與 datafeed.get_history 的抓取視窗相同
PER_LOOKBACK = 365                          # 與 fetch_price_stats 相同
CHIP_LOOKBACK = 5 + 5                       # 與 fetch_chip_data(days=5) 相同

# 期間內改過、會影響報告內容的規則：(首份生效報告日, daily_scan 屬性, 生效前的值)。
# 生效日 = 第一份含該 commit 的「auto: 每日掃描」commit；更早的報告依舊規則補建。
# 往後再改這類規則時須在此補一列，否則補建會把新規則套到舊日期上。
RULE_CUTOVERS = [
    ('20260923', 'ENABLE_TREND_CONTINUATION_BUY', False),  # 230803ccb 趨勢延續 BUY
    ('20260924', 'REQUIRE_STRONG_SECTOR', True),           # f08e2b599 族群閘門改標籤
]
# 5849c3996：雙篩選 passes_bias 自此改用未四捨五入的乖離（之前用四捨五入到 0.1 的值）
UNROUNDED_BIAS_FROM = '20261012'


def rules_asof(date: str) -> dict:
    return {attr: (getattr(ds, attr) if date >= since else old)
            for since, attr, old in RULE_CUTOVERS}


# --verify 比對欄位；news/broker/main_force/chip_tier 無法回溯，不比
VERIFY_FIELDS = ['price', 'rsi', 'rsi10', 'ret_20d', 'signal', 'ma5', 'ma10',
                 'ma20', 'ma60', 'cv_sharpe', 'cv_win_rate', 'target_short',
                 'target_mid', 'target_long', 'atr14', 'stop_loss', 'chip']


def _d(s: str) -> datetime:
    return datetime.strptime(s, '%Y%m%d')


def _iso(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d')


def report_dates(start: str, end: str) -> list[str]:
    return sorted(p.name for p in REPORTS.iterdir()
                  if p.is_dir() and p.name.isdigit() and len(p.name) == 8
                  and start <= p.name <= end and (p / 'summary.json').exists())


def fetch_raw(sid: str, first: str, last: str):
    """一次抓齊整段所需原始資料（日 K、本益比、法人），之後逐日切片。"""
    real = ds._finmind_fetch
    last_iso = _iso(_d(last))
    px = real('taiwan_stock_daily', stock_id=sid,
              start_date=_iso(_d(first) - timedelta(days=PRICE_LOOKBACK)),
              end_date=last_iso)
    per = real('taiwan_stock_per_pbr', stock_id=sid,
               start_date=_iso(_d(first) - timedelta(days=PER_LOOKBACK)),
               end_date=last_iso)
    chip = real('taiwan_stock_institutional_investors', stock_id=sid,
                start_date=_iso(_d(first) - timedelta(days=CHIP_LOOKBACK)),
                end_date=last_iso)
    # 任一缺漏都會讓 fetch_price_stats／fetch_chip_data 默默降級，寫進報告就難以分辨，直接中止
    for label, df in (('日 K', px), ('本益比', per), ('法人買賣超', chip)):
        if df is None or df.empty:
            raise SystemExit(f'{sid} 抓不到{label}，中止')
    px = px.dropna(subset=['close']).sort_values('date').reset_index(drop=True)
    return px, per, chip


def hist_asof(px: pd.DataFrame, date: str) -> ds.datafeed.CachedStock:
    """截至 date 的 K 棒，視窗與 datafeed.get_history 相同。"""
    lo = _iso(_d(date) - timedelta(days=PRICE_LOOKBACK))
    df = px[(px['date'] >= lo) & (px['date'] <= _iso(_d(date)))]
    return ds.datafeed.CachedStock(
        [float(v) for v in df['close']],
        [datetime.strptime(str(v), '%Y-%m-%d').date() for v in df['date']],
        [float(v) for v in df['max']],
        [float(v) for v in df['min']],
        [int(v) for v in df['Trading_Volume']],
    )


def chip_asof_date(summary: dict, date: str) -> str:
    """該報告其他個股實際拿到的籌碼日（眾數）；全無籌碼則用報告日。"""
    dates = Counter(st['chip']['date']
                    for sec in summary['sectors'].values()
                    for st in sec['stocks'] if (st.get('chip') or {}).get('date'))
    return dates.most_common(1)[0][0] if dates else _iso(_d(date))


def scan_asof(sid, name, sector, date, raw, chip_end):
    """以截至 date 的資料跑 daily_scan 的單檔流程，回傳 (原始結果, 單股 summary)。"""
    px, per, chip = raw
    hist = hist_asof(px, date)
    per_lo = _iso(_d(date) - timedelta(days=PER_LOOKBACK))
    chip_lo = _iso(_d(date) - timedelta(days=CHIP_LOOKBACK))

    def fake_fetch(method, **_kw):
        if method == 'taiwan_stock_per_pbr':
            return per[(per['date'] >= per_lo) & (per['date'] <= _iso(_d(date)))]
        if method == 'taiwan_stock_institutional_investors':
            return chip[(chip['date'] >= chip_lo) & (chip['date'] <= chip_end)]
        raise RuntimeError(f'補建流程未預期的資料請求：{method}')

    saved = (ds._finmind_fetch, ds._get_history, ds.fetch_news,
             ds.ENABLE_TREND_CONTINUATION_BUY, ds.REQUIRE_STRONG_SECTOR,
             sys.modules.get('indicators.broker'))
    ds._finmind_fetch = fake_fetch
    ds._get_history = lambda _sid: hist
    ds.fetch_news = lambda *_a, **_k: []
    ds.ENABLE_TREND_CONTINUATION_BUY = rules_asof(date)['ENABLE_TREND_CONTINUATION_BUY']
    sys.modules['indicators.broker'] = None   # 分點無法回溯 → scan_sector 走「未抓」分支
    try:
        r = ds.scan_sector(sector, {sid: name})[0]
        if 'error' in r:
            return r, None
        # 單股 summary 只用來取格式化後的列與推薦候選；族群閘門要看併入後的
        # 族群強弱，由 merge_into 依當日規則判定，這裡先關閉以免單股均值誤擋
        ds.REQUIRE_STRONG_SECTOR = False
        return r, ds.build_summary(date, {}, {sector: [r]}, None)
    finally:
        (ds._finmind_fetch, ds._get_history, ds.fetch_news,
         ds.ENABLE_TREND_CONTINUATION_BUY, ds.REQUIRE_STRONG_SECTOR, broker) = saved
        if broker is None:
            sys.modules.pop('indicators.broker', None)
        else:
            sys.modules['indicators.broker'] = broker


def _insert_by_sector(items: list, item: dict, order: list):
    """依族群順序插入（與 build_summary 逐族群 append 的順序一致）。"""
    idx = order.index(item['sector'])
    pos = 0
    for i, x in enumerate(items):
        if x.get('sector') in order and order.index(x['sector']) <= idx:
            pos = i + 1
    items.insert(pos, item)


def derive_qualified(summary: dict) -> list:
    """依 build_summary 的推薦規則，由報告中的個股列推出 qualified。

    - 雙篩選（BUY＋CV 門檻）與族群無關：dual_filter 已列出全部命中者（跨族群去重）
    - 乖離以列中已存的 price／ma10 計算——build_summary 用的正是這兩個已四捨五入
      的值，故結果與當日完全相同
    - 族群閘門依當日規則；同一檔屬多族群時取第一個通過的族群（同 _qualified_seen）
    """
    gate = rules_asof(summary['date'])['REQUIRE_STRONG_SECTOR']
    strong = set(summary['strong_sectors'])
    hits = {q['id'] for q in summary['dual_filter']}
    out, seen = [], set()
    for sector, sec in summary['sectors'].items():
        for st in sec['stocks']:
            ma10 = st.get('ma10')
            if st['id'] not in hits or st['id'] in seen or not ma10:
                continue
            bias = (st['price'] - ma10) / ma10 * 100
            if bias > ds.MAX_BIAS_MA10 or (gate and sector not in strong):
                continue
            seen.add(st['id'])
            q = {'sector': sector, 'id': st['id'], 'name': st['name'],
                 'price': st['price'], 'rsi': st['rsi'], 'cv_sharpe': st['cv_sharpe'],
                 'bias_ma10': round(bias, 1)}
            if not gate:
                q['sector_strong'] = sector in strong   # 閘門時期的 qualified 沒有此欄
            out.append(q)
    return out


def merge_into(summary: dict, sector: str, r: dict, single: dict, stamp: str):
    """把單股結果併入既有報告：只新增該股列，族群彙總以遞增方式更新，
    推薦名單依當日規則重新推導（族群強弱翻轉時同族群他股的資格會跟著變）。"""
    date = summary['date']
    gate = rules_asof(date)['REQUIRE_STRONG_SECTOR']
    if derive_qualified(summary) != summary['qualified']:
        # 推導式無法重現現存名單＝報告格式或規則沿革與預期不同，寧可不寫
        raise RuntimeError('推薦名單推導與報告現存值不符，未寫入')

    sec = summary['sectors'][sector]
    stocks = sec['stocks']
    n = len(stocks)
    n_ret = sum(1 for st in stocks if st.get('ret_20d') is not None)
    avg_ret = sec['avg_ret_20d']
    if r.get('ret_20d') is not None:
        avg_ret = (avg_ret * n_ret + r['ret_20d'] * 100) / (n_ret + 1)
    sec['avg_ret_20d'] = round(avg_ret, 2)
    sec['avg_rsi'] = round((sec['avg_rsi'] * n + r['rsi']) / (n + 1), 1)
    sec['avg_sharpe'] = round((sec['avg_sharpe'] * n + r['cv_sharpe']) / (n + 1), 2)
    sec['hot_count'] += int(r['rsi'] > 70)
    sec['buy_count'] += int(r['signal'] == 'BUY')
    sec['qualified_count'] += int(r['cv_sharpe'] >= 0.3 and r['cv_win_rate'] >= 0.4)

    row = dict(single['sectors'][sector]['stocks'][0])
    row['backfilled'] = stamp
    stocks.append(row)

    # 族群強弱（avg_ret>3 強、<-3 弱），與 build_summary 同定義
    order = list(summary['sectors'].keys())
    strong = avg_ret > 3
    for key, flag in (('strong_sectors', strong), ('weak_sectors', avg_ret < -3)):
        lst = [s for s in summary[key] if s != sector]
        if flag:
            lst.append(sector)
        summary[key] = sorted(lst, key=order.index)

    # 雙篩選：同族群既有項目更新強弱標籤，再加入本股（若命中）
    for q in summary['dual_filter']:
        if q['sector'] == sector:
            q['sector_strong'] = strong
            q['passes_all'] = q['passes_bias'] and (strong if gate else True)
    if not any(q['id'] == r['id'] for q in summary['dual_filter']):
        for q in single['dual_filter']:
            q = dict(q, sector_strong=strong)
            if date < UNROUNDED_BIAS_FROM:
                q['passes_bias'] = q['bias_ma10'] <= ds.MAX_BIAS_MA10
            q['passes_all'] = q['passes_bias'] and (strong if gate else True)
            _insert_by_sector(summary['dual_filter'], q, order)

    summary['qualified'] = derive_qualified(summary)
    for a in single['alerts']:
        _insert_by_sector(summary['alerts'], a, order)
    return row


def _same(a, b) -> bool:
    if isinstance(a, float) or isinstance(b, float):
        return a is not None and b is not None and abs(a - b) < 1e-6
    return a == b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('sid')
    ap.add_argument('--sector', required=True)
    ap.add_argument('--name', default=None, help='預設取 daily_scan.SECTORS 內的名稱')
    ap.add_argument('--start', required=True, help='YYYYMMDD')
    ap.add_argument('--end', default='99999999', help='YYYYMMDD，預設到最新一份報告')
    ap.add_argument('--verify', action='store_true',
                    help='重算報告中已存在的個股並逐欄比對，不寫檔')
    args = ap.parse_args()

    name = args.name or ds.SECTORS.get(args.sector, {}).get(args.sid)
    if not name:
        raise SystemExit(f'{args.sid} 不在 SECTORS[{args.sector!r}]，請先加入或以 --name 指定')
    dates = report_dates(args.start, args.end)
    if not dates:
        raise SystemExit('範圍內沒有報告')
    print(f'{args.sid} {name}［{args.sector}］{dates[0]}～{dates[-1]}，共 {len(dates)} 份報告')

    raw = fetch_raw(args.sid, dates[0], dates[-1])
    stamp = datetime.now().strftime('%Y%m%d')
    done = skipped = failed = mismatched = 0

    for date in dates:
        path = REPORTS / date / 'summary.json'
        summary = json.loads(path.read_text(encoding='utf-8'))
        sec = summary['sectors'].get(args.sector)
        if sec is None:
            print(f'  {date} 無族群 {args.sector}，略過')
            skipped += 1
            continue
        stored = next((st for st in sec['stocks'] if st['id'] == args.sid), None)
        if args.verify == (stored is None):
            print(f'  {date} {"報告中無此股，無從比對" if args.verify else "已存在，略過"}')
            skipped += 1
            continue

        r, single = scan_asof(args.sid, name, args.sector, date, raw,
                              chip_asof_date(summary, date))
        if single is None:
            print(f'  {date} 計算失敗：{r.get("error")}')
            failed += 1
            continue
        row = single['sectors'][args.sector]['stocks'][0]

        if args.verify:
            diff = [f'{k}: 存 {stored.get(k)!r} / 算 {row.get(k)!r}'
                    for k in VERIFY_FIELDS if not _same(stored.get(k), row.get(k))]
            mismatched += bool(diff)
            print(f'  {date} {"OK" if not diff else "不符"}' +
                  ''.join(f'\n      {x}' for x in diff))
            continue

        try:
            merge_into(summary, args.sector, r, single, stamp)
        except RuntimeError as e:
            print(f'  {date} 未寫入：{e}')
            failed += 1
            continue
        path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                        encoding='utf-8')
        (REPORTS / date / 'summary.md').write_text(ds.summary_to_markdown(summary),
                                                   encoding='utf-8')
        done += 1
        print(f'  {date} 價={row["price"]} RSI={row["rsi"]} 訊號={row["signal"]} '
              f'夏普={row["cv_sharpe"]}')

    if args.verify:
        print(f'\n比對完成：{len(dates) - skipped - failed} 份中 {mismatched} 份不符，'
              f'失敗 {failed}、略過 {skipped}')
        sys.exit(1 if mismatched or failed else 0)
    print(f'\n補建完成：寫入 {done}、略過 {skipped}、失敗 {failed}')
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
