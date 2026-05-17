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


import math


def compute_metrics(equity: list[float], rebalances: list[dict], rf: float = 0.01) -> dict:
    """從 equity curve 與 rebalances 計算 CAGR / vol / Sharpe / MDD / 勝率"""
    if not equity or len(equity) < 2:
        period_rets_short = [rb.get('period_return') for rb in rebalances
                             if 'period_return' in rb]
        if period_rets_short:
            wr = sum(1 for r in period_rets_short if r > 0) / len(period_rets_short)
            ap = sum(period_rets_short) / len(period_rets_short)
        else:
            wr = 0.0
            ap = 0.0
        return {'cagr': 0.0, 'vol': 0.0, 'sharpe': 0.0,
                'mdd': 0.0, 'win_rate': wr,
                'avg_period_return': ap, 'turnover': 0.0}

    n = len(equity)
    final = equity[-1]
    initial = equity[0]
    cagr = (final / initial) ** (252 / max(n - 1, 1)) - 1 if initial > 0 else 0.0

    rets = [equity[i] / equity[i - 1] - 1
            for i in range(1, n) if equity[i - 1] > 0]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    daily_vol = math.sqrt(var)
    vol = daily_vol * math.sqrt(252)
    sharpe = (cagr - rf) / vol if vol > 0 else 0.0

    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = v / peak - 1 if peak > 0 else 0.0
        if dd < mdd:
            mdd = dd

    period_rets = [rb.get('period_return') for rb in rebalances
                   if 'period_return' in rb]
    if period_rets:
        win_rate = sum(1 for r in period_rets if r > 0) / len(period_rets)
        avg_pr  = sum(period_rets) / len(period_rets)
    else:
        win_rate = 0.0
        avg_pr  = 0.0

    return {
        'cagr':     round(cagr, 4),
        'vol':      round(vol, 4),
        'sharpe':   round(sharpe, 4),
        'mdd':      round(mdd, 4),
        'win_rate': win_rate,
        'avg_period_return': avg_pr,
        'turnover': 0.0,
    }


SECTOR_RULES = ['ret20', 'rsi', 'hot', 'composite']
STOCK_RULES  = ['ret20_individual', 'chip_concentration']
FREQUENCIES  = ['weekly', 'monthly']

_RULE_LABEL_SECTOR = {
    'ret20': 'ret20', 'rsi': 'RSI', 'hot': '熱度', 'composite': '複合分數',
}
_RULE_LABEL_STOCK = {
    'ret20_individual': 'ret20選股', 'chip_concentration': '籌碼選股',
}
_RULE_LABEL_FREQ = {'weekly': '週調', 'monthly': '月調'}


def variant_id(sector_rule: str, frequency: str, stock_rule: str) -> str:
    freq_short = 'W' if frequency == 'weekly' else 'M'
    stock_short = 'ret20' if stock_rule == 'ret20_individual' else 'chip'
    return f'{sector_rule}_{freq_short}_{stock_short}'


def variant_label(sector_rule: str, frequency: str, stock_rule: str) -> str:
    return (f'{_RULE_LABEL_SECTOR[sector_rule]} / '
            f'{_RULE_LABEL_FREQ[frequency]} / '
            f'{_RULE_LABEL_STOCK[stock_rule]}')


def _compute_period_returns(equity: list[float], dates: list[str],
                            rebalances: list[dict]) -> None:
    """In-place: 在每個 rebalance 上加 period_return 欄位"""
    rb_idx = [dates.index(rb['date']) for rb in rebalances if rb['date'] in dates]
    for i, rb in enumerate(rebalances):
        if rb['date'] not in dates:
            continue
        start_i = dates.index(rb['date'])
        end_i = rb_idx[i + 1] if i + 1 < len(rb_idx) else len(equity) - 1
        if equity[start_i] > 0 and end_i > start_i:
            rb['period_return'] = round(equity[end_i] / equity[start_i] - 1, 6)
        else:
            rb['period_return'] = 0.0


def _avg_turnover(rebalances: list[dict]) -> float:
    if len(rebalances) < 2:
        return 0.0
    turnovers = []
    for i in range(1, len(rebalances)):
        prev_ids = {h['stock_id'] for h in rebalances[i - 1]['holdings']}
        curr_ids = {h['stock_id'] for h in rebalances[i]['holdings']}
        n = max(len(curr_ids), 1)
        turnovers.append(len(prev_ids.symmetric_difference(curr_ids)) / (2 * n))
    return sum(turnovers) / len(turnovers) if turnovers else 0.0


def simulate_benchmark_buyhold(
    dates: list[str], prices_by_id: dict[str, pd.DataFrame],
    stock_ids: list[str],
) -> dict:
    """等權持有清單；遺失資料的股以剩餘股權重平均代位"""
    weight = 1.0 / max(len(stock_ids), 1)
    equity: list[float] = []
    nav = 1.0
    for i, d in enumerate(dates):
        if i == 0:
            equity.append(1.0)
            continue
        prev_d = dates[i - 1]
        day_return = 0.0
        active = 0
        for sid in stock_ids:
            p_prev = _price_on(prices_by_id, sid, prev_d)
            p_now  = _price_on(prices_by_id, sid, d)
            if p_prev and p_now:
                day_return += (p_now / p_prev - 1)
                active += 1
        if active > 0:
            nav *= (1 + day_return / active)
        equity.append(round(nav, 6))
    return {'equity': equity, 'dates': dates, 'rebalances': []}


def simulate_benchmark_taiex(dates: list[str], taiex: pd.DataFrame) -> dict:
    """加權指數 buy & hold"""
    if taiex.empty:
        return {'equity': [1.0] * len(dates), 'dates': dates, 'rebalances': []}
    iso_dates = [f'{d[:4]}-{d[4:6]}-{d[6:8]}' for d in dates]
    closes = []
    last_close = None
    for iso in iso_dates:
        row = taiex.loc[taiex['date'] == iso]
        if not row.empty:
            last_close = float(row['close'].iloc[0])
        closes.append(last_close)
    base = next((c for c in closes if c is not None), 1.0)
    equity = [(c / base) if c else 1.0 for c in closes]
    return {'equity': [round(e, 6) for e in equity],
            'dates': dates, 'rebalances': []}


def build_results(
    signals: dict[str, dict],
    prices_by_id: dict[str, pd.DataFrame],
    taiex: pd.DataFrame,
    benchmark_stock_ids: list[str],
    cost_per_turn: float = 0.00585,
    sectors_picked: int = 3,
    stocks_per_sector: int = 3,
    rf: float = 0.01,
) -> dict:
    """跑 16 variants + 2 benchmarks，組合 results.json"""
    from datetime import datetime
    all_dates = sorted(signals.keys())

    variants_out = []
    for sr in SECTOR_RULES:
        for freq in FREQUENCIES:
            for stk in STOCK_RULES:
                sim = simulate_strategy(
                    signals=signals, prices=prices_by_id,
                    sector_rule=sr, stock_rule=stk, frequency=freq,
                    sectors_picked=sectors_picked,
                    stocks_per_sector=stocks_per_sector,
                    cost_per_turn=cost_per_turn,
                )
                _compute_period_returns(sim['equity'], sim['dates'],
                                        sim['rebalances'])
                metrics = compute_metrics(sim['equity'], sim['rebalances'], rf=rf)
                metrics['turnover'] = round(_avg_turnover(sim['rebalances']), 4)
                variants_out.append({
                    'id':           variant_id(sr, freq, stk),
                    'label':        variant_label(sr, freq, stk),
                    'sector_rule':  sr,
                    'frequency':    freq,
                    'stock_rule':   stk,
                    'metrics':      metrics,
                    'equity':       sim['equity'],
                    'dates':        sim['dates'],
                    'rebalances':   sim['rebalances'],
                })

    bench_taiex = simulate_benchmark_taiex(all_dates, taiex)
    bench_ew = simulate_benchmark_buyhold(all_dates, prices_by_id,
                                          benchmark_stock_ids)
    bench_taiex_m = compute_metrics(bench_taiex['equity'], [], rf=rf)
    bench_ew_m    = compute_metrics(bench_ew['equity'],    [], rf=rf)

    ranking = sorted(
        [{'id': v['id'], 'sharpe': v['metrics']['sharpe']} for v in variants_out],
        key=lambda x: x['sharpe'], reverse=True,
    )

    return {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'period': {
            'start': all_dates[0] if all_dates else '',
            'end':   all_dates[-1] if all_dates else '',
            'trading_days': len(all_dates),
        },
        'config': {
            'portfolio_size': sectors_picked * stocks_per_sector,
            'sectors_picked': sectors_picked,
            'stocks_per_sector': stocks_per_sector,
            'cost_per_turn': cost_per_turn,
            'rf_rate': rf,
        },
        'benchmarks': {
            'TAIEX': {**bench_taiex_m, **bench_taiex},
            'EqualWeight67': {**bench_ew_m, **bench_ew},
        },
        'variants': variants_out,
        'ranking':  ranking,
    }


def main():
    repo = Path(__file__).parent.parent
    docs = repo / 'docs'
    cache = repo / 'data' / 'cache' / 'prices'

    print('[1/4] 載入 daily signals...')
    signals = load_daily_signals(docs)
    if not signals:
        print('  沒有任何 daily JSON，結束')
        return
    all_dates = sorted(signals.keys())
    start, end = all_dates[0], all_dates[-1]
    print(f'  {len(signals)} 個交易日 ({start} ~ {end})')

    print('[2/4] 蒐集需要的個股 ID...')
    stock_ids = set()
    for d in signals.values():
        for s in d['stocks']:
            stock_ids.add(s['id'])
    print(f'  {len(stock_ids)} 檔股票')

    print('[3/4] 抓取價格（個股 + TAIEX）...')
    prices: dict[str, pd.DataFrame] = {}
    for i, sid in enumerate(sorted(stock_ids), 1):
        prices[sid] = load_prices_cached(
            cache_dir=cache, stock_id=sid, start=start, end=end,
            fetch_fn=fetch_prices_finmind,
        )
        if i % 10 == 0:
            print(f'    {i}/{len(stock_ids)}')
    taiex = load_prices_cached(
        cache_dir=cache, stock_id='TAIEX', start=start, end=end,
        fetch_fn=fetch_prices_finmind,
    )

    print('[4/4] 跑 16 variants + benchmarks...')
    results = build_results(
        signals=signals, prices_by_id=prices, taiex=taiex,
        benchmark_stock_ids=sorted(stock_ids),
    )

    out_dir = docs / 'backtest'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / 'results.json'
    out_path.write_text(json.dumps(results, ensure_ascii=False, separators=(',', ':')),
                        encoding='utf-8')
    size_kb = out_path.stat().st_size / 1024
    print(f'  寫出 {out_path} ({size_kb:.1f} KB)')
    print(f'  Top variant: {results["ranking"][0]["id"]} '
          f'(Sharpe={results["ranking"][0]["sharpe"]})')


if __name__ == '__main__':
    main()
