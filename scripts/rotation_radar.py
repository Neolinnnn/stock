"""
全市場動能雷達（名單汰換掃描）
資料來源：TWSE STOCK_DAY_ALL + TPEx 上櫃日行情（各 1 次呼叫、免金鑰、零 LLM token）

用法：
  python scripts/rotation_radar.py --collect   # 每日收盤後累積全市場 close/成交值快取
  python scripts/rotation_radar.py --report    # （每週五）產出 docs/rotation_radar.json

--report 產出內容：
  - market：市場廣度（上漲家數比、動能中位數）
  - sectors：官方產業別動能排行（中位數報酬），標記與現有名單的重疊度
  - entry_candidates：動能前 10%、流動性夠、但不在 104 檔名單內的個股（進場候選）
  - exit_candidates：名單內動能落在全市場後 30% 的個股，含連續週數（汰除候選）
名單增刪由使用者決定，本腳本只產生建議。
"""
import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCS_DIR = ROOT / 'docs'
CACHE_DIR = ROOT / 'data' / 'radar_cache'
HISTORY_PATH = CACHE_DIR / 'history.json'
INDUSTRY_PATH = CACHE_DIR / 'industry_map.json'
REPORT_PATH = DOCS_DIR / 'rotation_radar.json'

TWSE_QUOTES_URL = 'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL'
TPEX_QUOTES_URL = 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'
TWSE_REVENUE_URL = 'https://openapi.twse.com.tw/v1/opendata/t187ap05_L'
TPEX_REVENUE_URL = 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O'

KEEP_DAYS = 90            # 快取保留天數（60 日動能 + 緩衝）
MIN_AVG_VALUE = 100_000   # 流動性門檻：20 日均成交值 ≥ 1 億（快取單位：千元）
ENTRY_PCT = 90            # 進場候選：動能百分位 ≥ 90
EXIT_PCT = 30             # 汰除候選：動能百分位 ≤ 30
SECTOR_MIN_N = 5          # 產業動能至少 5 檔成分股


def fetch_json(url: str, retries: int = 3):
    """抓 OpenAPI JSON；TPEx 憑證缺 SKI 在新版 Python 會驗證失敗，降級重試。"""
    import httpx
    last_err = None
    for i in range(retries):
        for verify in (True, False):
            try:
                r = httpx.get(url, timeout=90, verify=verify,
                              headers={'Accept': 'application/json'})
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_err = e
        time.sleep(3 * (i + 1))
    raise RuntimeError(f'{url} 抓取失敗：{last_err}')


def _is_stock(code: str) -> bool:
    """一般個股為不含前導 0 的 4 碼數字（排除 ETF/權證/受益證券）。"""
    return len(code) == 4 and code.isdigit() and code[0] != '0'


def _num(v):
    try:
        f = float(str(v).replace(',', ''))
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


def _roc_date_to_ad(s: str) -> str | None:
    """'1150709' → '20260709'"""
    s = str(s).strip()
    if len(s) != 7 or not s.isdigit():
        return None
    return f'{int(s[:3]) + 1911}{s[3:]}'


# ── 每日收集 ─────────────────────────────────────────────────────────────────

def parse_quotes():
    """回傳 (date, {sid: {'name', 'mkt', 'close', 'value'}})；value 單位千元。"""
    rows = {}
    date = None
    for r in fetch_json(TWSE_QUOTES_URL):
        code = str(r.get('Code', '')).strip()
        if not _is_stock(code):
            continue
        date = date or _roc_date_to_ad(r.get('Date', ''))
        close = _num(r.get('ClosingPrice'))
        value = _num(r.get('TradeValue'))
        if close:
            rows[code] = {'name': r.get('Name', ''), 'mkt': 'twse', 'close': close,
                          'value': int(value / 1000) if value else 0}
    try:
        for r in fetch_json(TPEX_QUOTES_URL):
            code = str(r.get('SecuritiesCompanyCode', '')).strip()
            if not _is_stock(code) or code in rows:
                continue
            date = date or _roc_date_to_ad(r.get('Date', ''))
            close = _num(r.get('Close'))
            value = _num(r.get('TransactionAmount'))
            if close:
                rows[code] = {'name': r.get('CompanyName', ''), 'mkt': 'tpex', 'close': close,
                              'value': int(value / 1000) if value else 0}
    except Exception as e:
        print(f'[WARN] TPEx 行情抓取失敗（只收上市）：{e}')
    return date, rows


def load_history() -> dict:
    try:
        return json.loads(HISTORY_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {'dates': [], 'stocks': {}}


def collect():
    date, quotes = parse_quotes()
    if not date or not quotes:
        print('[WARN] 無行情資料（假日？），跳過收集')
        return
    hist = load_history()
    dates = hist['dates']
    if date in dates:
        print(f'[radar-collect] {date} 已收集過，跳過')
        return

    n_before = len(dates)
    for sid, q in quotes.items():
        s = hist['stocks'].get(sid)
        if s is None:
            s = hist['stocks'][sid] = {'name': q['name'], 'mkt': q['mkt'],
                                       'close': [None] * n_before, 'value': [None] * n_before}
        s['name'] = q['name'] or s['name']
        s['close'].append(q['close'])
        s['value'].append(q['value'])
    # 今日停牌／下市者補 None 對齊
    for sid, s in hist['stocks'].items():
        if len(s['close']) == n_before:
            s['close'].append(None)
            s['value'].append(None)
    dates.append(date)

    # 裁掉超過保留天數的舊資料；全 None（已下市）的個股移除
    if len(dates) > KEEP_DAYS:
        cut = len(dates) - KEEP_DAYS
        hist['dates'] = dates[cut:]
        for s in hist['stocks'].values():
            s['close'] = s['close'][cut:]
            s['value'] = s['value'][cut:]
    hist['stocks'] = {sid: s for sid, s in hist['stocks'].items()
                      if any(v is not None for v in s['close'])}

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_PATH.write_text(json.dumps(hist, ensure_ascii=False), encoding='utf-8')
    refresh_industry_map()
    print(f'[radar-collect] {date} 收集 {len(quotes)} 檔，快取共 {len(hist["dates"])} 日 '
          f'{len(hist["stocks"])} 檔')


def refresh_industry_map(max_age_days: int = 30):
    """以月營收彙總表的「產業別」欄建立 sid → 產業名稱對照（30 天更新一次）。"""
    try:
        cached = json.loads(INDUSTRY_PATH.read_text(encoding='utf-8'))
        fetched = datetime.strptime(cached.get('fetched_at', ''), '%Y-%m-%d')
        if (datetime.now() - fetched).days < max_age_days:
            return
    except Exception:
        pass
    mapping = {}
    for url in (TWSE_REVENUE_URL, TPEX_REVENUE_URL):
        try:
            for r in fetch_json(url):
                sid = str(r.get('公司代號', '')).strip()
                ind = str(r.get('產業別', '')).strip()
                if sid and ind:
                    mapping[sid] = ind
        except Exception as e:
            print(f'[WARN] 產業別對照抓取失敗 {url}：{e}')
    if mapping:
        INDUSTRY_PATH.write_text(json.dumps({
            'fetched_at': datetime.now().strftime('%Y-%m-%d'),
            'map': mapping,
        }, ensure_ascii=False), encoding='utf-8')
        print(f'[radar-collect] 產業別對照更新：{len(mapping)} 檔')


# ── 週報表 ───────────────────────────────────────────────────────────────────

def _ret(close: list, window: int):
    """近 window 日報酬率（%）；頭尾任一端無值則回 None。"""
    if len(close) < window + 1:
        return None
    a, b = close[-window - 1], close[-1]
    if not a or not b:
        return None
    return round((b - a) / a * 100, 2)


def report():
    hist = load_history()
    dates = hist['dates']
    days = len(dates)
    out = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'data_start': dates[0] if dates else None,
        'data_end': dates[-1] if dates else None,
        'days_collected': days,
    }
    if days < 6:
        out['status'] = 'warming_up'
        out['note'] = f'快取僅 {days} 日，至少需 6 日才計算動能（20 日動能需 21 日）'
        REPORT_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
        print(f'[radar-report] 資料暖機中（{days} 日），已寫入狀態')
        return

    window = min(20, days - 1)  # 資料不足 21 日時用現有天數，並在輸出標明
    try:
        ind_map = json.loads(INDUSTRY_PATH.read_text(encoding='utf-8'))['map']
    except Exception:
        ind_map = {}
    try:
        watch = {s['id']: s for s in
                 json.loads((DOCS_DIR / 'stocks_index.json').read_text(encoding='utf-8'))}
    except Exception:
        watch = {}

    # 個股動能 + 流動性
    universe = []
    for sid, s in hist['stocks'].items():
        r = _ret(s['close'], window)
        if r is None:
            continue
        vals = [v for v in s['value'][-window:] if v]
        avg_value = int(sum(vals) / len(vals)) if vals else 0
        universe.append({
            'id': sid, 'name': s['name'], 'mkt': s['mkt'],
            'industry': ind_map.get(sid, '未分類'),
            'ret': r, 'ret60': _ret(s['close'], 60), 'avg_value': avg_value,
        })

    # 動能百分位（只在流動性達標的池內排名，避免殭屍股干擾）
    liquid = [u for u in universe if u['avg_value'] >= MIN_AVG_VALUE]
    liquid.sort(key=lambda u: u['ret'])
    n = len(liquid)
    for i, u in enumerate(liquid):
        u['pct'] = round(i / max(n - 1, 1) * 100, 1)

    # 市場廣度
    rets = [u['ret'] for u in liquid]
    out.update({
        'status': 'ok' if window == 20 else 'partial_window',
        'window': window,
        'universe_size': n,
        'market': {
            'breadth_pos_ratio': round(sum(1 for r in rets if r > 0) / n * 100, 1) if n else None,
            'median_ret': round(statistics.median(rets), 2) if rets else None,
        },
    })

    # 產業動能排行
    by_ind = {}
    for u in liquid:
        by_ind.setdefault(u['industry'], []).append(u)
    sectors = []
    for ind, members in by_ind.items():
        if ind == '未分類' or len(members) < SECTOR_MIN_N:
            continue
        r60s = [m['ret60'] for m in members if m['ret60'] is not None]
        sectors.append({
            'industry': ind,
            'n': len(members),
            'median_ret': round(statistics.median([m['ret'] for m in members]), 2),
            'median_ret60': round(statistics.median(r60s), 2) if r60s else None,
            'watchlist_n': sum(1 for m in members if m['id'] in watch),
            'leaders': [{'id': m['id'], 'name': m['name'], 'ret': m['ret']}
                        for m in sorted(members, key=lambda x: -x['ret'])[:3]],
        })
    sectors.sort(key=lambda s: -s['median_ret'])
    out['sectors'] = sectors

    # 進場候選：動能前段班且不在名單
    out['entry_candidates'] = sorted(
        [{k: u[k] for k in ('id', 'name', 'mkt', 'industry', 'ret', 'ret60', 'avg_value', 'pct')}
         for u in liquid if u['pct'] >= ENTRY_PCT and u['id'] not in watch],
        key=lambda u: -u['ret'])[:30]

    # 汰除候選：名單內動能後段班，累計連續週數
    prev_streak = {}
    try:
        prev_streak = json.loads(REPORT_PATH.read_text(encoding='utf-8')).get('low_streak', {})
    except Exception:
        pass
    liquid_map = {u['id']: u for u in liquid}
    low_streak, exit_candidates = {}, []
    for sid, info in watch.items():
        u = liquid_map.get(sid)
        if u is None:
            continue  # 流動性不足或無資料，不評分
        if u['pct'] <= EXIT_PCT:
            low_streak[sid] = prev_streak.get(sid, 0) + 1
            exit_candidates.append({
                'id': sid, 'name': info.get('name', u['name']),
                'sector': info.get('sector', ''), 'industry': u['industry'],
                'ret': u['ret'], 'ret60': u['ret60'], 'pct': u['pct'],
                'weeks_low': low_streak[sid],
            })
    exit_candidates.sort(key=lambda x: (-x['weeks_low'], x['pct']))
    out['exit_candidates'] = exit_candidates
    out['low_streak'] = low_streak

    REPORT_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
    print(f'[radar-report] window={window} 池={n} 檔，產業 {len(sectors)} 類，'
          f'進場候選 {len(out["entry_candidates"])}、汰除候選 {len(exit_candidates)}')


def main():
    ap = argparse.ArgumentParser(description='全市場動能雷達')
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--collect', action='store_true', help='每日收集全市場行情快取')
    g.add_argument('--report', action='store_true', help='產出 docs/rotation_radar.json')
    args = ap.parse_args()
    if args.collect:
        collect()
    else:
        report()


if __name__ == '__main__':
    main()
