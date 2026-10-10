"""台股含息還原價（自算）：以 FinMind 免費的公司行動表，向後還原日 K。

FinMind 官方還原股價 TaiwanStockPriceAdj 只限 backer／sponsor 會員，本專案為 register
等級；改用下列資料表自算（做法同 GitHub JeffCC/Total-Return-Calc）：

| 事件 | DataLoader 方法 | 查詢範圍 | 前收盤欄 | 參考價欄 |
|------|----------------|---------|---------|---------|
| 除權息 | taiwan_stock_dividend_result | 逐檔 | before_price | reference_price |
| 減資 | taiwan_stock_capital_reduction_reference_price | 逐檔 | ClosingPriceonTheLastTradingDay | PostReductionReferencePrice |
| 分割／反分割 | taiwan_stock_split_price | 全市場 | before_price | after_price |
| 面額變更 | taiwan_stock_par_value_change | 全市場 | before_close | after_ref_close |

還原方式（向後還原）：
- 事件係數 = 參考價 / 前收盤；事件日（除權息日／恢復買賣日）之前的價格乘上其後所有
  事件係數的連乘積。最新價格不變，報酬即含息總報酬
- 股數改變的事件（減資、分割、面額變更）同步調整成交量（除以係數），量比才前後可比
- 同日同係數只算一次（同一事件可能同時出現在多張表）
- 事件日晚於最後一根 K 棒者不套用，維持「最新價＝實際收盤」
"""
from datafeed import _is_permanent, finmind_fetch

PRICE_COLS = ('open', 'high', 'low', 'close')

SOURCES = (
    # (DataLoader 方法, 逐檔查詢, 前收盤欄, 參考價欄, 改變股數)
    ('taiwan_stock_dividend_result', True, 'before_price', 'reference_price', False),
    ('taiwan_stock_capital_reduction_reference_price', True,
     'ClosingPriceonTheLastTradingDay', 'PostReductionReferencePrice', True),
    ('taiwan_stock_split_price', False, 'before_price', 'after_price', True),
    ('taiwan_stock_par_value_change', False, 'before_close', 'after_ref_close', True),
)

_market_cache: dict = {}    # 全市場表一次查詢供所有個股共用：(方法, 起, 迄) -> DataFrame


def parse_events(df, method: str, before_col: str, ref_col: str, shares: bool) -> list[dict]:
    """資料表 → [{'date': YYYYMMDD, 'factor': 參考價/前收盤, 'shares': 改變股數}]。
    欄位缺少代表 FinMind 改了格式，直接拋錯，不悄悄退回未還原價。"""
    if df is None or df.empty:
        return []
    missing = {'date', before_col, ref_col} - set(df.columns)
    if missing:
        raise ValueError(f'{method} 缺少欄位 {sorted(missing)}，FinMind 格式可能已變更')
    out = []
    for d, before, ref in zip(df['date'], df[before_col], df[ref_col]):
        try:
            before, ref = float(before), float(ref)
        except (TypeError, ValueError):
            continue
        if before > 0 and ref > 0:    # NaN 比較為 False，一併排除
            out.append({'date': str(d).replace('-', '')[:8], 'factor': ref / before, 'shares': shares})
    return out


def dedupe(events: list[dict]) -> list[dict]:
    """同日同係數合併為一筆（任一來源改變股數即視為改變股數），依日期排序。"""
    merged: dict = {}
    for e in events:
        key = (e['date'], round(e['factor'], 6))
        if key in merged:
            merged[key]['shares'] |= e['shares']
        else:
            merged[key] = dict(e)
    return sorted(merged.values(), key=lambda e: e['date'])


def fetch_events(sid: str, start: str, end: str) -> list[dict]:
    """sid 於 start~end（YYYY-MM-DD）的公司行動事件。

    網路／額度錯誤直接拋出，由呼叫端決定中止（每週回測的安全閥）或略過；
    帳號等級不足（FinMind 回 your level is …）則警示後略過該表——全部個股
    一致退回未還原，不會只有部分個股還原。"""
    events = []
    for method, per_stock, before_col, ref_col, shares in SOURCES:
        try:
            if per_stock:
                df = finmind_fetch(method, stock_id=sid, start_date=start, end_date=end)
            else:
                key = (method, start, end)
                if key not in _market_cache:
                    _market_cache[key] = finmind_fetch(method, start_date=start, end_date=end)
                df = _market_cache[key]
                if df is not None and not df.empty:
                    df = df[df['stock_id'].astype(str) == sid]
        except Exception as e:
            if not _is_permanent(e):
                raise
            print(f'  ⚠️ {method} 帳號等級不足，略過此類事件（價格未完整還原）：{e}')
            continue
        events += parse_events(df, method, before_col, ref_col, shares)
    return dedupe(events)


def multipliers(dates: list[str], events: list[dict]) -> tuple[list[float], list[float]]:
    """各日期（遞增 YYYYMMDD）的價格乘數與成交量乘數。
    事件當日視為事件後，不乘該事件係數；晚於最後一日的事件不套用。"""
    last = dates[-1] if dates else ''
    evs = [e for e in sorted(events, key=lambda e: e['date']) if e['date'] <= last]
    price_m, vol_m = [1.0] * len(dates), [1.0] * len(dates)
    pf = vf = 1.0
    k = len(evs) - 1
    for i in range(len(dates) - 1, -1, -1):   # 由新到舊，跨過事件日後累乘
        while k >= 0 and evs[k]['date'] > dates[i]:
            pf *= evs[k]['factor']
            if evs[k]['shares']:
                vf /= evs[k]['factor']
            k -= 1
        price_m[i], vol_m[i] = pf, vf
    return price_m, vol_m


def adjust_df(df, events: list[dict]):
    """日 K DataFrame（date 遞增，YYYYMMDD）→ 還原後的新 DataFrame。"""
    if df.empty or not events:
        return df
    pm, vm = multipliers(list(df['date']), events)
    out = df.copy()
    for col in PRICE_COLS:
        if col in out.columns:
            out[col] = out[col] * pm
    if 'volume' in out.columns:
        out['volume'] = out['volume'] * vm
    return out


def adjust_bars(bars: dict[str, dict], events: list[dict]) -> dict[str, dict]:
    """{YYYYMMDD: {'open','high','low','close'}} → 還原後的新 dict。"""
    if not bars or not events:
        return bars
    days = sorted(bars)
    pm, _ = multipliers(days, events)
    return {d: {k: (v * m if k in PRICE_COLS else v) for k, v in bars[d].items()}
            for d, m in zip(days, pm)}
