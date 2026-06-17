"""
掃描「自選清單」族群並將結果追加到今日 summary.json。

從環境變數 SCAN_STOCKS 讀取逗號分隔的股票代碼（如 2330,2317,6669），
掃描這些股票，把結果寫進今日 daily_reports/YYYYMMDD/summary.json 的「自選清單」族群，
並更新 docs/YYYYMMDD.json。

策略（優先順序）：
1. 若今日 summary.json 已有該股票資料（在其他族群）→ 直接複製
2. 若 backtest_cache 有 OHLCV CSV → 轉為 price_cache JSON 後掃描
3. 其他 → 記錄失敗

用法：SCAN_STOCKS=2330,2317,6669 python scripts/scan_watchlist.py
"""
import sys, os, json, math
from datetime import datetime, timedelta
from pathlib import Path
import csv

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))

# ─── 從環境變數取得目標股票清單 ───────────────────────────────────────────────
SECTOR_NAME = '自選清單'

_raw_stocks = os.environ.get('SCAN_STOCKS', '').strip()
if not _raw_stocks:
    print("⚠ 未設定環境變數 SCAN_STOCKS，程式結束。")
    sys.exit(0)

# 讀取 stocks_index.json 以取得股票名稱
_stocks_index_path = ROOT / 'docs' / 'stocks_index.json'
_stocks_index = {}
if _stocks_index_path.exists():
    try:
        with open(_stocks_index_path, encoding='utf-8') as _f:
            for item in json.load(_f):
                _stocks_index[item['id']] = item['name']
    except Exception as _e:
        print(f"⚠ 讀取 stocks_index.json 失敗：{_e}")

# 建立 {id: name} dict，名稱從 stocks_index 查詢，找不到就用代碼
SECTOR_STOCKS = {}
for _sid in _raw_stocks.split(','):
    _sid = _sid.strip().upper()
    if _sid:
        SECTOR_STOCKS[_sid] = _stocks_index.get(_sid, _sid)

if not SECTOR_STOCKS:
    print("⚠ SCAN_STOCKS 解析後無有效股票代碼，程式結束。")
    sys.exit(0)

TODAY        = datetime.now().strftime('%Y%m%d')
TODAY_DASH   = datetime.now().strftime('%Y-%m-%d')
SUMMARY_PATH = ROOT / f'daily_reports/{TODAY}/summary.json'
DOCS_PATH    = ROOT / f'docs/{TODAY}.json'
CACHE_DIR    = ROOT / 'backtest_cache'
PRICE_CACHE  = ROOT / 'price_cache'


# ─── 工具 ─────────────────────────────────────────────────────────────────────
def _nan_to_none(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def load_summary():
    if not SUMMARY_PATH.exists():
        return None
    with open(SUMMARY_PATH, encoding='utf-8') as f:
        return json.load(f)


def extract_from_summary(summary, target_ids):
    """從現有 summary.json 的各族群找出目標股票資料。"""
    found = {}
    if not summary:
        return found
    for sector, data in summary.get('sectors', {}).items():
        for st in data.get('stocks', []):
            if st['id'] in target_ids and st['id'] not in found:
                found[st['id']] = st
    return found


def backtest_to_price_cache(sid):
    """將 backtest_cache/{sid}_ohlcv.csv 轉換成 price_cache/{sid}.json。"""
    src = CACHE_DIR / f'{sid}_ohlcv.csv'
    if not src.exists():
        return False
    PRICE_CACHE.mkdir(exist_ok=True)
    rows = []
    with open(src, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    if not rows:
        return False
    dates  = [datetime.strptime(r['date'], '%Y%m%d').strftime('%Y-%m-%d') for r in rows]
    closes = [float(r['close']) for r in rows]
    highs  = [float(r['high'])  for r in rows]
    lows   = [float(r['low'])   for r in rows]
    vols   = [int(r['volume'])  for r in rows]
    out = {
        'sid': sid,
        'updated_at': dates[-1],
        'dates':  dates,
        'prices': closes,
        'highs':  highs,
        'lows':   lows,
        'volumes': vols,
    }
    with open(PRICE_CACHE / f'{sid}.json', 'w', encoding='utf-8') as f:
        json.dump(out, f, separators=(',', ':'))
    return True


def scan_missing(missing_ids):
    """對沒有今日資料的股票嘗試用 daily_scan.scan_sector 掃描。"""
    if not missing_ids:
        return {}

    # 先把有 backtest_cache 的轉成 price_cache
    converted = []
    for sid in missing_ids:
        if backtest_to_price_cache(sid):
            converted.append(sid)
    if converted:
        print(f"  已轉換 {len(converted)} 檔 backtest_cache → price_cache：{converted}")

    # 載入 daily_scan 模組
    import importlib.util
    _scripts_dir = str(ROOT / 'scripts')
    if _scripts_dir not in sys.path:
        sys.path.insert(0, _scripts_dir)

    spec = importlib.util.spec_from_file_location(
        'daily_scan', str(ROOT / 'scripts' / 'daily_scan.py'))
    ds = importlib.util.module_from_spec(spec)
    sys.modules['daily_scan'] = ds
    spec.loader.exec_module(ds)

    stocks_to_scan = {sid: SECTOR_STOCKS[sid] for sid in missing_ids}
    results = ds.scan_sector(SECTOR_NAME, stocks_to_scan)
    return {r['id']: r for r in results if 'error' not in r}


def build_sector_entry(stock_list):
    """stock_list: list of stock dicts（已標準化格式）"""
    import pandas as _pd
    if not stock_list:
        return None
    df = _pd.DataFrame(stock_list)

    avg_ret    = df['ret_20d'].dropna().mean() if len(df['ret_20d'].dropna()) > 0 else 0
    avg_rsi    = df['rsi'].mean()
    avg_sharpe = df['cv_sharpe'].mean()
    buy_cnt    = sum(1 for s in stock_list if s.get('signal') == 'BUY')
    hot_cnt    = sum(1 for s in stock_list if (s.get('rsi') or 0) > 70)
    qualified_cnt = sum(
        1 for s in stock_list
        if (s.get('cv_sharpe') or 0) >= 0.3 and (s.get('cv_win_rate') or 0) >= 0.4
    )

    return {
        'avg_ret_20d':     round(float(avg_ret), 2),
        'avg_rsi':         round(float(avg_rsi), 1),
        'avg_sharpe':      round(float(avg_sharpe), 2),
        'stocks':          stock_list,
        'buy_count':       buy_cnt,
        'hot_count':       hot_cnt,
        'qualified_count': qualified_cnt,
    }


def normalize_scan_result(r):
    """將 scan_sector 原始結果轉為 summary 格式。"""
    return {
        'id':          r['id'],
        'name':        r['name'],
        'price':       _nan_to_none(r.get('price')),
        'rsi':         round(float(_nan_to_none(r['rsi']) or 0), 1),
        'rsi10':       round(float(_nan_to_none(r['rsi10']) or 0), 1) if r.get('rsi10') else None,
        'ret_20d':     round(float(_nan_to_none(r['ret_20d'])) * 100, 1) if r.get('ret_20d') is not None else None,
        'signal':      r.get('signal', 'HOLD'),
        'cv_sharpe':   round(float(_nan_to_none(r.get('cv_sharpe')) or 0), 2),
        'cv_win_rate': round(float(_nan_to_none(r.get('cv_win_rate')) or 0), 2),
        'news':        r.get('news', []),
        'chip':        r.get('chip', {}),
        'target_short': _nan_to_none(r.get('target_short')),
        'target_mid':   _nan_to_none(r.get('target_mid')),
        'target_long':  _nan_to_none(r.get('target_long')),
        'atr14':        _nan_to_none(r.get('atr14')),
        'stop_loss':    _nan_to_none(r.get('stop_loss')),
        'broker':       r.get('broker'),
        'main_force':   r.get('main_force'),
    }


def main():
    print(f"\n{'='*60}")
    print(f"  掃描族群：{SECTOR_NAME}（{len(SECTOR_STOCKS)} 檔）")
    print(f"  股票清單：{', '.join(f'{sid} {name}' for sid, name in SECTOR_STOCKS.items())}")
    print(f"  目標日期：{TODAY}")
    print(f"{'='*60}\n")

    # 確保 daily_reports 目錄存在
    SUMMARY_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 1. 讀取今日 summary
    summary = load_summary()
    if not summary:
        print("⚠ 找不到今日 summary.json，建立空摘要")
        summary = {'date': TODAY, 'timestamp': datetime.now().isoformat(),
                   'market': {}, 'sectors': {}}

    # 2. 從現有族群萃取已有資料的股票
    existing = extract_from_summary(summary, set(SECTOR_STOCKS.keys()))
    print(f"✅ 今日 summary.json 已有資料：{len(existing)}/{len(SECTOR_STOCKS)} 檔")
    for sid, st in sorted(existing.items()):
        print(f"   {sid} {st['name']} @ {st.get('price')} — {st.get('signal')}")

    # 3. 找出缺少資料的股票，嘗試從 backtest_cache 掃描
    missing_ids = [sid for sid in SECTOR_STOCKS if sid not in existing]
    print(f"\n🔍 嘗試掃描缺少資料的 {len(missing_ids)} 檔股票…")
    scanned = {}
    if missing_ids:
        try:
            scanned = scan_missing(missing_ids)
            if scanned:
                print(f"  掃描成功：{list(scanned.keys())}")
        except Exception as e:
            print(f"  掃描失敗（可能為網路限制）：{e}")

    # 4. 彙整最終清單（保持 SECTOR_STOCKS 順序）
    stock_list = []
    skipped = []
    for sid, name in SECTOR_STOCKS.items():
        if sid in existing:
            st = dict(existing[sid])
            stock_list.append(st)
        elif sid in scanned:
            st = normalize_scan_result(scanned[sid])
            stock_list.append(st)
        else:
            skipped.append(f"{sid} {name}")

    print(f"\n📊 彙整結果：{len(stock_list)} 檔有資料，{len(skipped)} 檔待下次掃描")
    if skipped:
        print(f"   待補：{', '.join(skipped)}")

    # 5. 建立族群摘要
    sector_entry = build_sector_entry(stock_list)
    if sector_entry is None:
        print("⚠ 無任何股票資料，中止更新。")
        return

    # 6. 寫回 summary.json
    summary['sectors'][SECTOR_NAME] = sector_entry
    summary['timestamp'] = datetime.now().isoformat()
    with open(SUMMARY_PATH, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n✅ summary.json 已更新：{SUMMARY_PATH}")

    # 7. 同步 docs/{TODAY}.json（格式不同：sectors/stocks 均為 list）
    if DOCS_PATH.exists():
        with open(DOCS_PATH, encoding='utf-8') as f:
            docs = json.load(f)
    else:
        # 今日尚無 docs JSON，建立空結構
        docs = {'date': TODAY, 'sectors': [], 'stocks': []}

    # 移除舊的同名族群條目
    docs['sectors'] = [s for s in docs.get('sectors', []) if s.get('sector') != SECTOR_NAME]
    docs['stocks']  = [s for s in docs.get('stocks', [])  if s.get('sector') != SECTOR_NAME]

    # 新增族群摘要列
    docs['sectors'].append({
        'sector': SECTOR_NAME,
        'ret20': sector_entry['avg_ret_20d'],
        'rsi':   sector_entry['avg_rsi'],
        'buy':   sector_entry['buy_count'],
        'hot':   sector_entry['hot_count'],
    })

    # 新增個股列
    for st in sector_entry['stocks']:
        chip = st.get('chip') or {}
        docs['stocks'].append({
            'date':      TODAY,
            'sector':    SECTOR_NAME,
            'id':        st['id'],
            'name':      st['name'],
            'price':     st.get('price'),
            'rsi':       st.get('rsi'),
            'rsi10':     st.get('rsi10'),
            'ret20':     st.get('ret_20d'),
            'signal':    st.get('signal', ''),
            'sharpe':    st.get('cv_sharpe'),
            'foreign':   chip.get('外資', ''),
            'trust':     chip.get('投信', ''),
            'dealer':    chip.get('自營', ''),
            'chipTotal': chip.get('合計', ''),
            'news':      ' / '.join(n['title'] for n in st.get('news', [])[:2]),
        })

    with open(DOCS_PATH, 'w', encoding='utf-8') as f:
        json.dump(docs, f, ensure_ascii=False, indent=2, default=str)
    print(f"✅ docs JSON 已更新：{DOCS_PATH}")

    print(f"\n{'='*60}\n")


if __name__ == '__main__':
    main()
