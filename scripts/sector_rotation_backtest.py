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
