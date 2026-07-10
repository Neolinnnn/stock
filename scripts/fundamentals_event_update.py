"""
事件驅動基本面更新（零 FinMind 額度、零 LLM token）
每日 CI 在 fundamentals_fetcher 之前執行：

1. 月營收：抓 TWSE / TPEx 全公司月營收彙總表（各 1 次呼叫、免金鑰），
   名單內個股若出現比快取新的月份 → 直接 append 進 docs/fundamentals/{sid}.json
   並以 compute_revenue_stats 本地重算 MoM/YoY/累計YoY。
2. 財報：抓 TWSE / TPEx 綜合損益表彙總（一般業），只比對「年度/季別」是否比
   快取的最新一季新（不 append 數值，避免單季/累計語意風險）。有新一季者寫入
   data/cache/fundamentals_stale.json，由 fundamentals_fetcher 強制重抓（FinMind）。

彙總表每月換月時間約在申報截止（10 日）後數個工作天，故新月營收會在
每月 11~15 日左右自動進場；財報季（5/15、8/14、11/14、3/31）同理。
"""
import json
import time
from datetime import datetime

from fundamentals_fetcher import (
    DOCS_DIR, FUNDAMENTALS_DIR, STALE_PATH, compute_revenue_stats,
)

TWSE_REVENUE_URL = 'https://openapi.twse.com.tw/v1/opendata/t187ap05_L'
TPEX_REVENUE_URL = 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap05_O'
TWSE_INCOME_URL = 'https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci'
TPEX_INCOME_URL = 'https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci'


def fetch_json(url: str, retries: int = 3):
    """抓 OpenAPI JSON；TPEx 憑證缺 SKI 在新版 Python 會驗證失敗，降級重試。"""
    import httpx
    last_err = None
    for i in range(retries):
        for verify in (True, False):
            try:
                r = httpx.get(url, timeout=60, verify=verify,
                              headers={'Accept': 'application/json'})
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_err = e
        time.sleep(3 * (i + 1))
    raise RuntimeError(f'{url} 抓取失敗：{last_err}')


def _g(row: dict, *keys):
    """欄位名容錯（TPEx 部分端點 meta 欄位為英文）。"""
    for k in keys:
        v = row.get(k)
        if v not in (None, ''):
            return v
    return None


def _sid_of(row: dict):
    v = _g(row, '公司代號', 'SecuritiesCompanyCode')
    return str(v).strip() if v else None


def roc_ym_to_ad(ym) -> str | None:
    """民國年月 '11505' → '202605'"""
    s = str(ym).strip()
    if len(s) != 5 or not s.isdigit():
        return None
    return f'{int(s[:3]) + 1911}{s[3:]}'


def roc_quarter_to_ad(row) -> str | None:
    """民國 年度/季別 → '2026Q1'"""
    y, q = _g(row, '年度', 'Year'), _g(row, '季別', 'Season')
    try:
        return f'{int(str(y).strip()) + 1911}Q{int(str(q).strip())}'
    except (TypeError, ValueError):
        return None


def build_map(data: list) -> dict:
    return {sid: row for row in data if (sid := _sid_of(row))}


def append_revenue(data: dict, row: dict) -> bool:
    """若彙總表月份比快取新則 append 並重算指標。回傳是否有更新。"""
    rev = data.get('revenue')
    if not rev or not rev.get('month'):
        return False
    ym = roc_ym_to_ad(_g(row, '資料年月'))
    if not ym or ym <= rev['month'][-1]:
        return False
    try:
        # 彙總表單位：千元 → 元（與 FinMind 對齊）
        amount = int(float(str(_g(row, '營業收入-當月營收') or 0).replace(',', ''))) * 1000
    except ValueError:
        return False
    if amount <= 0:
        return False
    data['revenue'] = compute_revenue_stats(rev['month'] + [ym], rev['revenue'] + [amount])
    return True


def main():
    index_path = DOCS_DIR / 'stocks_index.json'
    if not index_path.exists():
        print('[WARN] docs/stocks_index.json 不存在，跳過事件更新')
        return
    stock_list = json.loads(index_path.read_text(encoding='utf-8'))

    rev_map, fin_map = {}, {}
    for url in (TWSE_REVENUE_URL, TPEX_REVENUE_URL):
        try:
            rev_map.update(build_map(fetch_json(url)))
        except Exception as e:
            print(f'[WARN] 月營收彙總抓取失敗 {url}：{e}')
    for url in (TWSE_INCOME_URL, TPEX_INCOME_URL):
        try:
            fin_map.update(build_map(fetch_json(url)))
        except Exception as e:
            print(f'[WARN] 損益表彙總抓取失敗 {url}：{e}')

    updated, stale = 0, []
    for info in stock_list:
        sid = info['id']
        path = FUNDAMENTALS_DIR / f'{sid}.json'
        if not path.exists():
            continue  # 尚無快取者交給 fundamentals_fetcher 全抓
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue

        row = rev_map.get(sid)
        if row and append_revenue(data, row):
            # 更新 generated_at 讓 7 天 skip 生效，本月不再耗 FinMind 額度
            data['generated_at'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
            updated += 1

        frow = fin_map.get(sid)
        eps = data.get('eps')
        if frow and eps and eps.get('quarter'):
            q = roc_quarter_to_ad(frow)
            if q and q > eps['quarter'][-1]:
                stale.append(sid)

    STALE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STALE_PATH.write_text(json.dumps({
        'stocks': stale,
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
    }, ensure_ascii=False), encoding='utf-8')

    print(f'[event-update] 月營收 append {updated} 檔；財報過期待重抓 {len(stale)} 檔')


if __name__ == '__main__':
    main()
