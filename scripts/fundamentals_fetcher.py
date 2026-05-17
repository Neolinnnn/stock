"""
基本面資料抓取模組
每日掃描後執行，輸出 docs/fundamentals/{stock_id}.json
"""
import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _pct_change(new, old):
    """計算百分比變化，old 為 0 或 None 時回傳 None。"""
    if old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def _round2(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 2)


# ── 月營收解析 ────────────────────────────────────────────────────────────────

def parse_revenue(df):
    """
    輸入：FinMind taiwan_stock_month_revenue DataFrame
    輸出：{month, revenue, mom, yoy, cum_yoy} 平行陣列 dict
    """
    if df is None or df.empty:
        return None

    df = df.sort_values(['revenue_year', 'revenue_month']).reset_index(drop=True)

    months  = [f"{int(r.revenue_year)}{int(r.revenue_month):02d}" for _, r in df.iterrows()]
    revs    = [int(r.revenue) for _, r in df.iterrows()]
    n       = len(revs)

    # MoM
    mom = [None] + [_pct_change(revs[i], revs[i-1]) for i in range(1, n)]

    # YoY（需要 12 期前的資料）
    yoy = [None] * n
    for i in range(12, n):
        yoy[i] = _pct_change(revs[i], revs[i-12])

    # 累計 YoY：同年累計 vs 前年同期累計
    cum_yoy = [None] * n
    year_of = [int(m[:4]) for m in months]
    month_of = [int(m[4:]) for m in months]
    for i in range(n):
        y, m = year_of[i], month_of[i]
        cur_cum = sum(
            revs[j] for j in range(n)
            if year_of[j] == y and month_of[j] <= m
        )
        prev_cum = sum(
            revs[j] for j in range(n)
            if year_of[j] == y - 1 and month_of[j] <= m
        )
        if prev_cum > 0:
            cum_yoy[i] = _pct_change(cur_cum, prev_cum)

    return {
        'month':    months,
        'revenue':  revs,
        'mom':      mom,
        'yoy':      yoy,
        'cum_yoy':  cum_yoy,
    }


# ── 財務報表解析 ──────────────────────────────────────────────────────────────

def _date_to_quarter(date_str):
    """'2023-03-31' → '2023Q1'"""
    d = datetime.strptime(date_str[:10], '%Y-%m-%d')
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


def parse_financials(df):
    """
    輸入：FinMind taiwan_stock_financial_statement DataFrame
    輸出：(eps_dict, margins_dict) 平行陣列 dict 的 tuple
          任一資料缺失時回傳 (None, None)
    """
    if df is None or df.empty:
        return None, None

    needed = {'EPS', 'GrossProfit', 'OperatingIncome', 'Revenue',
              'EquityAttributableToOwnersOfParent'}
    available = set(df['type'].unique())
    if not needed.issubset(available):
        return None, None

    df = df[df['type'].isin(needed)].copy()
    pivot = df.pivot_table(index='date', columns='type', values='value', aggfunc='first')
    pivot = pivot.sort_index()

    quarters = [_date_to_quarter(d) for d in pivot.index]
    n = len(quarters)

    eps_vals  = [_round2(v) for v in pivot['EPS'].tolist()]
    rev_vals  = [float(v) for v in pivot['Revenue'].tolist()]
    gp_vals   = [float(v) for v in pivot['GrossProfit'].tolist()]
    oi_vals   = [float(v) for v in pivot['OperatingIncome'].tolist()]
    ni_vals   = [float(v) for v in pivot['EquityAttributableToOwnersOfParent'].tolist()]

    # QoQ / YoY for EPS
    eps_qoq = [None] + [_pct_change(eps_vals[i], eps_vals[i-1]) for i in range(1, n)]
    eps_yoy = [None] * n
    for i in range(4, n):
        eps_yoy[i] = _pct_change(eps_vals[i], eps_vals[i-4])

    # 三率 = 各項 / Revenue * 100
    def to_margin(vals):
        return [_round2(v / r * 100) if r and r != 0 else None
                for v, r in zip(vals, rev_vals)]

    eps_dict = {
        'quarter': quarters,
        'eps':     eps_vals,
        'qoq':     eps_qoq,
        'yoy':     eps_yoy,
    }
    margins_dict = {
        'quarter':           quarters,
        'gross_margin':      to_margin(gp_vals),
        'operating_margin':  to_margin(oi_vals),
        'net_margin':        to_margin(ni_vals),
    }
    return eps_dict, margins_dict


# ── FinMind 抓取 ──────────────────────────────────────────────────────────────

def _get_dl():
    from finmind_client import get_dataloader
    return get_dataloader()


def fetch_revenue_finmind(dl, stock_id: str, months: int = 24):
    """以 FinMind 抓月營收，回傳 DataFrame 或拋出 Exception。"""
    end = datetime.now()
    start = end - timedelta(days=30 * (months + 14))
    df = dl.taiwan_stock_month_revenue(
        stock_id=stock_id,
        start_date=start.strftime('%Y-%m-%d'),
        end_date=end.strftime('%Y-%m-%d'),
    )
    if df.empty:
        raise ValueError(f"{stock_id}: FinMind 月營收回傳空白")
    return df


def fetch_financials_finmind(dl, stock_id: str, quarters: int = 8):
    """以 FinMind 抓財報，回傳 DataFrame 或拋出 Exception。"""
    end = datetime.now()
    start = end - timedelta(days=90 * (quarters + 2))
    df = dl.taiwan_stock_financial_statement(
        stock_id=stock_id,
        start_date=start.strftime('%Y-%m-%d'),
        end_date=end.strftime('%Y-%m-%d'),
    )
    if df.empty:
        raise ValueError(f"{stock_id}: FinMind 財報回傳空白")
    return df


# ── TWSE OpenAPI fallback ────────────────────────────────────────────────────

def _twse_get(url: str, timeout: int = 15):
    import httpx
    r = httpx.get(url, timeout=timeout,
                  headers={'Accept': 'application/json'})
    r.raise_for_status()
    return r.json()


def fetch_revenue_twse(stock_id: str):
    """TWSE OpenAPI 月營收 fallback（僅當前最新批次）。"""
    import pandas as pd
    data = _twse_get('https://openapi.twse.com.tw/v1/opendata/t187ap05_L')
    rows = [r for r in data if r.get('公司代號', '').strip() == stock_id]
    if not rows:
        return None
    records = []
    for row in rows:
        try:
            year = int(row['年份']) + 1911
            month = int(row['月份'])
            revenue = int(str(row.get('當月營收', '0')).replace(',', '') or 0)
            records.append({
                'date': f'{year}-{month:02d}-01',
                'stock_id': stock_id,
                'revenue': revenue,
                'revenue_month': month,
                'revenue_year': year,
                'country': 'Taiwan',
                'create_time': '',
            })
        except (ValueError, KeyError):
            continue
    return pd.DataFrame(records) if records else None


def fetch_financials_twse(stock_id: str):
    """TWSE OpenAPI 損益表 fallback（最新一期）。"""
    import pandas as pd
    import calendar
    data = _twse_get('https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci')
    rows = [r for r in data if r.get('公司代號', '').strip() == stock_id]
    if not rows:
        return None
    r = rows[0]
    try:
        year_tw = int(r.get('年度', 0))
        q = int(r.get('季別', 1))
        year = year_tw + 1911
        month_end = q * 3
        last_day = calendar.monthrange(year, month_end)[1]
        date_str = f'{year}-{month_end:02d}-{last_day:02d}'

        def to_num(key):
            v = str(r.get(key, '') or '').replace(',', '').strip()
            return float(v) * 1000 if v else 0.0

        records = [
            {'date': date_str, 'stock_id': stock_id, 'type': 'Revenue',
             'value': to_num('營業收入'), 'origin_name': ''},
            {'date': date_str, 'stock_id': stock_id, 'type': 'GrossProfit',
             'value': to_num('營業毛利（毛損）'), 'origin_name': ''},
            {'date': date_str, 'stock_id': stock_id, 'type': 'OperatingIncome',
             'value': to_num('營業利益（損失）'), 'origin_name': ''},
            {'date': date_str, 'stock_id': stock_id, 'type': 'EquityAttributableToOwnersOfParent',
             'value': to_num('本期歸屬於母公司業主之綜合損益總額'), 'origin_name': ''},
            {'date': date_str, 'stock_id': stock_id, 'type': 'EPS',
             'value': float(str(r.get('基本每股盈餘（元）', 0) or 0).replace(',', '')),
             'origin_name': ''},
        ]
        return pd.DataFrame(records)
    except (ValueError, KeyError):
        return None


# ── JSON 寫入 ─────────────────────────────────────────────────────────────────

DOCS_DIR = Path(__file__).parent.parent / 'docs'
FUNDAMENTALS_DIR = DOCS_DIR / 'fundamentals'


def _should_skip(stock_id: str, min_days: int = 7) -> bool:
    """若距上次寫入不足 min_days 天則 skip。"""
    path = FUNDAMENTALS_DIR / f'{stock_id}.json'
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text(encoding='utf-8'))
        generated = datetime.strptime(d.get('generated_at', ''), '%Y-%m-%d %H:%M')
        return (datetime.now() - generated).days < min_days
    except Exception:
        return False


def write_fundamentals(stock_id: str, name: str, result: dict):
    FUNDAMENTALS_DIR.mkdir(parents=True, exist_ok=True)
    result['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
    path = FUNDAMENTALS_DIR / f'{stock_id}.json'
    path.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')


# ── 主迴圈 ────────────────────────────────────────────────────────────────────

def build_fundamentals(stock_list: list, skip_days: int = 7):
    """
    stock_list: [{'id': '2330', 'name': '台積電'}, ...]
    """
    try:
        dl = _get_dl()
        finmind_ok = True
    except Exception as e:
        print(f'[WARN] FinMind 初始化失敗：{e}，改用 TWSE fallback')
        dl = None
        finmind_ok = False

    ok, skipped = 0, 0

    for info in stock_list:
        sid, name = info['id'], info['name']

        if _should_skip(sid, skip_days):
            skipped += 1
            continue

        result = {
            'stock_id': sid,
            'name': name,
            'source': 'finmind',
            'revenue': None,
            'eps': None,
            'margins': None,
            'meta': {'revenue_months': 24, 'financial_quarters': 8, 'fetch_errors': []},
        }

        # ── 月營收 ──
        try:
            df = fetch_revenue_finmind(dl, sid) if finmind_ok else None
            if df is None:
                raise ValueError('FinMind 未啟用')
            result['revenue'] = parse_revenue(df)
        except Exception as e:
            result['meta']['fetch_errors'].append({'field': 'revenue', 'error': str(e)})
            try:
                df = fetch_revenue_twse(sid)
                result['revenue'] = parse_revenue(df) if df is not None else None
                if result['revenue']:
                    result['source'] = 'twse_openapi'
            except Exception as e2:
                result['meta']['fetch_errors'].append({'field': 'revenue_twse', 'error': str(e2)})

        # ── EPS + 三率 ──
        try:
            df = fetch_financials_finmind(dl, sid) if finmind_ok else None
            if df is None:
                raise ValueError('FinMind 未啟用')
            result['eps'], result['margins'] = parse_financials(df)
        except Exception as e:
            result['meta']['fetch_errors'].append({'field': 'eps_margins', 'error': str(e)})
            try:
                df = fetch_financials_twse(sid)
                result['eps'], result['margins'] = parse_financials(df) if df is not None else (None, None)
                if result['eps'] and result['source'] == 'finmind':
                    result['source'] = 'mixed'
                elif result['eps']:
                    result['source'] = 'twse_openapi'
            except Exception as e2:
                result['meta']['fetch_errors'].append({'field': 'eps_margins_twse', 'error': str(e2)})

        write_fundamentals(sid, name, result)
        ok += 1
        time.sleep(0.5)

    print(f'[fundamentals] 完成 {ok} 檔，跳過 {skipped} 檔')


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    index_path = DOCS_DIR / 'stocks_index.json'
    if not index_path.exists():
        print('[WARN] docs/stocks_index.json 不存在，請先執行 build_docs.py')
        return
    stock_list = json.loads(index_path.read_text(encoding='utf-8'))
    build_fundamentals(stock_list)


if __name__ == '__main__':
    main()
