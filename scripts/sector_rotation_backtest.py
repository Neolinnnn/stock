"""族群輪動回測：讀 docs/{date}.json，跑 16 個策略 variant，輸出 results.json"""
from __future__ import annotations
import json
from pathlib import Path


def load_daily_signals(docs_dir: Path) -> dict[str, dict]:
    """讀取 docs/{YYYYMMDD}.json，回傳 {date: {sectors, stocks}}，依日期升序"""
    result: dict[str, dict] = {}
    for f in sorted(docs_dir.glob('[0-9]*.json')):
        if len(f.stem) != 8 or not f.stem.isdigit():
            continue
        data = json.loads(f.read_text(encoding='utf-8'))
        result[f.stem] = {
            'sectors': data.get('sectors', []),
            'stocks':  data.get('stocks',  []),
        }
    return result


def _normalize(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def score_sectors(sectors: list[dict], rule: str, top_n: int) -> list[str]:
    """回傳前 top_n 個族群名稱，依 rule 排序由高至低"""
    if rule in ('ret20', 'rsi', 'hot'):
        ranked = sorted(sectors, key=lambda s: s.get(rule, 0), reverse=True)
        return [s['sector'] for s in ranked[:top_n]]

    if rule == 'composite':
        rets = _normalize([s.get('ret20', 0) for s in sectors])
        hots = _normalize([s.get('hot',   0) for s in sectors])
        buys = _normalize([s.get('buy',   0) for s in sectors])
        scored = [
            (s['sector'], 0.5 * r + 0.3 * h + 0.2 * b)
            for s, r, h, b in zip(sectors, rets, hots, buys)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored[:top_n]]

    raise ValueError(f'unknown rule: {rule}')


_STOCK_RULE_KEY = {
    'ret20_individual':   'ret20',
    'chip_concentration': 'chipTotal',
}


def select_stocks_in_sector(
    stocks: list[dict], sector: str, rule: str, top_k: int
) -> list[dict]:
    """從 stocks 中過濾 sector，依 rule 排序，回傳前 top_k 個 dict"""
    if rule not in _STOCK_RULE_KEY:
        raise ValueError(f'unknown stock rule: {rule}')
    key = _STOCK_RULE_KEY[rule]
    pool = [s for s in stocks if s.get('sector') == sector]
    pool.sort(key=lambda s: s.get(key, 0), reverse=True)
    return pool[:top_k]


from datetime import date


def _parse_yyyymmdd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def rebalance_dates(all_dates: list[str], frequency: str) -> list[str]:
    """從交易日清單篩出 rebalance 日，每週/月的第一個交易日"""
    if frequency not in ('weekly', 'monthly'):
        raise ValueError(f'unknown frequency: {frequency}')

    if not all_dates:
        return []

    out: list[str] = []
    prev_key: tuple | None = None
    for d in sorted(all_dates):
        dt = _parse_yyyymmdd(d)
        if frequency == 'weekly':
            iso = dt.isocalendar()
            key: tuple = (iso[0], iso[1])  # (year, ISO week)
        else:
            key = (dt.year, dt.month)
        if key != prev_key:
            out.append(d)
            prev_key = key
    return out


import pandas as pd


def _cache_path_for(cache_dir: Path, stock_id: str) -> Path:
    return cache_dir / f'{stock_id}.csv'


def load_prices_cached(
    cache_dir: Path,
    stock_id: str,
    start: str,
    end: str,
    fetch_fn,
) -> pd.DataFrame:
    """讀本地 cache CSV；若無則呼叫 fetch_fn 抓取並寫入。失敗回空 DataFrame。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path_for(cache_dir, stock_id)
    if path.exists():
        return pd.read_csv(path)
    if fetch_fn is None:
        return pd.DataFrame(columns=['date', 'close'])
    try:
        df = fetch_fn(stock_id, start, end)
        if df is None or df.empty:
            return pd.DataFrame(columns=['date', 'close'])
        df.to_csv(path, index=False)
        return df
    except Exception as e:
        print(f'[warn] fetch failed for {stock_id}: {e}')
        return pd.DataFrame(columns=['date', 'close'])


def fetch_prices_finmind(stock_id: str, start: str, end: str) -> pd.DataFrame:
    """從 FinMind 抓 TaiwanStockPrice（含 TAIEX），回傳 date/close 欄位"""
    from finmind_client import get_dataloader
    dl = get_dataloader()
    start_iso = f'{start[:4]}-{start[4:6]}-{start[6:8]}'
    end_iso   = f'{end[:4]}-{end[4:6]}-{end[6:8]}'
    df = dl.taiwan_stock_daily(stock_id=stock_id,
                               start_date=start_iso, end_date=end_iso)
    if df is None or df.empty:
        return pd.DataFrame(columns=['date', 'close'])
    return df[['date', 'close']].copy()


def _price_on(prices: dict[str, pd.DataFrame], stock_id: str, date_str: str) -> float | None:
    df = prices.get(stock_id)
    if df is None or df.empty:
        return None
    iso = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
    row = df.loc[df['date'] == iso]
    if row.empty:
        return None
    return float(row['close'].iloc[0])


def simulate_strategy(
    signals: dict[str, dict],
    prices: dict[str, pd.DataFrame],
    sector_rule: str,
    stock_rule: str,
    frequency: str,
    sectors_picked: int,
    stocks_per_sector: int,
    cost_per_turn: float,
) -> dict:
    """跑單一 variant；回傳 {equity, dates, rebalances}"""
    all_dates = sorted(signals.keys())
    rb_dates = set(rebalance_dates(all_dates, frequency))

    equity_curve: list[float] = []
    out_dates: list[str] = []
    rebalances: list[dict] = []

    current_holdings: list[dict] = []  # [{id, name, sector, weight, entry_price}]
    nav = 1.0

    for i, d in enumerate(all_dates):
        # Mark-to-market: update nav from previous day's holdings using today's prices
        if i > 0 and current_holdings:
            prev_d = all_dates[i - 1]
            day_return = 0.0
            for h in current_holdings:
                p_prev = _price_on(prices, h['id'], prev_d)
                p_now  = _price_on(prices, h['id'], d)
                if p_prev and p_now:
                    day_return += h['weight'] * (p_now / p_prev - 1)
            nav *= (1 + day_return)

        # Rebalance check
        sigs = signals[d]
        if d in rb_dates and sigs.get('sectors') and sigs.get('stocks'):
            top_sectors = score_sectors(sigs['sectors'], sector_rule, sectors_picked)
            new_holdings: list[dict] = []
            for sec in top_sectors:
                picks = select_stocks_in_sector(
                    sigs['stocks'], sec, stock_rule, stocks_per_sector
                )
                for p in picks:
                    new_holdings.append({
                        'id': p['id'],
                        'name': p.get('name', p['id']),
                        'sector': sec,
                    })

            if new_holdings:
                w = 1.0 / len(new_holdings)
                for h in new_holdings:
                    h['weight'] = w

                old_ids = {h['id'] for h in current_holdings}
                new_ids = {h['id'] for h in new_holdings}
                # Turnover = fraction of portfolio changed (each side counted half)
                changed = len(old_ids.symmetric_difference(new_ids)) / max(
                    2 * len(new_holdings), 1
                )
                nav *= (1 - cost_per_turn * changed)

                rebalances.append({
                    'date': d,
                    'sectors': top_sectors,
                    'holdings': [
                        {'stock_id': h['id'], 'name': h['name'],
                         'sector': h['sector'], 'weight': round(h['weight'], 4)}
                        for h in new_holdings
                    ],
                })
                current_holdings = new_holdings

        equity_curve.append(round(nav, 6))
        out_dates.append(d)

    return {
        'equity': equity_curve,
        'dates':  out_dates,
        'rebalances': rebalances,
    }
