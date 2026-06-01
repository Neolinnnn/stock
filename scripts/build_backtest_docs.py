"""
將 backtest_results*.json 整合為 docs/backtest_summary.json
並用 indicators/stock_analyzer 對所有持倉中的股票補充趨勢分析。

用法：
  python scripts/build_backtest_docs.py
"""
import json
import sys
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    import pandas as pd
    from indicators.stock_analyzer import analyze_stock
    _ANALYZER_OK = True
except Exception as e:
    print(f'[WARN] 分析引擎載入失敗：{e}')
    _ANALYZER_OK = False

PERIODS = [
    ('1m',  'backtest_results.json',       '近1個月'),
    ('6m',  'backtest_results_6m.json',    '近6個月'),
    ('9m',  'backtest_results_9m.json',    '近9個月'),
    ('12m', 'backtest_results_12m.json',   '近12個月'),
]

TRAILING = [
    ('6m_tr',  'backtest_results_6m_trailing.json',  '近6個月（追蹤停損）'),
    ('9m_tr',  'backtest_results_9m_trailing.json',  '近9個月（追蹤停損）'),
    ('12m_tr', 'backtest_results_12m_trailing.json', '近12個月（追蹤停損）'),
]

COMBO_LABELS = {
    'TP15_SL10': '停利15% / 停損10%',
    'TP15_SL12': '停利15% / 停損12%',
    'TP15_SL15': '停利15% / 停損15%',
    'TP18_SL10': '停利18% / 停損10%',
    'TP18_SL12': '停利18% / 停損12%',
    'TP18_SL15': '停利18% / 停損15%',
    'TP20_SL10': '停利20% / 停損10%',
    'TP20_SL12': '停利20% / 停損12%',
    'TP20_SL15': '停利20% / 停損15%',
}


def _load_stock_df(sid: str) -> 'pd.DataFrame | None':
    path = ROOT / 'docs' / 'stocks' / f'{sid}.json'
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        st = json.load(f)
    ohlcv = st.get('ohlcv', {})
    if not ohlcv.get('date'):
        return None
    return pd.DataFrame({
        'date':   ohlcv['date'],
        'open':   ohlcv['open'],
        'high':   ohlcv['high'],
        'low':    ohlcv['low'],
        'close':  ohlcv['close'],
        'volume': ohlcv['volume'],
    })


_analysis_cache = {}

def _get_analysis(sid: str) -> dict:
    if sid in _analysis_cache:
        return _analysis_cache[sid]
    if not _ANALYZER_OK:
        return {}
    df = _load_stock_df(sid)
    if df is None:
        return {}
    result = analyze_stock(df, sid)
    d = result.to_dict()
    _analysis_cache[sid] = d
    return d


def _process_period(fname: str, label: str) -> dict | None:
    path = ROOT / fname
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)

    combos_out = {}
    all_open_ids = set()

    for combo_id, combo_data in raw.get('combinations', {}).items():
        stats = combo_data['stats']
        trades_raw = combo_data.get('trades', [])

        trades = []
        for t in trades_raw:
            trades.append({
                'stock_id':    t['stock_id'],
                'stock_name':  t['stock_name'],
                'result':      t['result'],
                'entry_date':  t['entry_date'],
                'entry_price': t['entry_price'],
                'exit_date':   t.get('exit_date'),
                'exit_price':  t.get('exit_price'),
                'return_pct':  t.get('return_pct'),
                'holding_days': t.get('holding_days'),
            })
            if t['result'] == 'OPEN':
                all_open_ids.add(t['stock_id'])

        combos_out[combo_id] = {
            'id':    combo_id,
            'label': COMBO_LABELS.get(combo_id, combo_id),
            'stats': {
                'win_rate':         round(stats['win_rate'], 4),
                'avg_return':       round(stats['avg_return'], 2),
                'avg_holding_days': round(stats['avg_holding_days'], 1),
                'total':    stats['total'],
                'wins':     stats['wins'],
                'losses':   stats['losses'],
                'open_count': stats['open_count'],
            },
            'trades': trades,
        }

    # Run analysis on all open-position stocks
    open_analysis = {}
    for sid in all_open_ids:
        a = _get_analysis(sid)
        if a:
            open_analysis[sid] = a

    # Pick best combo by win_rate * avg_return (balanced score)
    def _score(c):
        s = c['stats']
        if s['total'] < 5:
            return 0
        return s['win_rate'] * s['avg_return']

    best_id = max(combos_out.values(), key=_score)['id'] if combos_out else None

    return {
        'label':       label,
        'date_range':  raw.get('date_range', {}),
        'best_combo':  best_id,
        'combos':      combos_out,
        'open_analysis': open_analysis,
    }


def build():
    out = {'periods': {}}

    for key, fname, label in PERIODS + TRAILING:
        result = _process_period(fname, label)
        if result:
            out['periods'][key] = result
            print(f'  [{key}] {label} — {len(result["combos"])} 組合, '
                  f'最佳: {result["best_combo"]}')
        else:
            print(f'  [{key}] 檔案不存在，跳過')

    dest = ROOT / 'docs' / 'backtest_summary.json'
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f'\ndocs/backtest_summary.json 已寫入（{dest.stat().st_size // 1024} KB）')


if __name__ == '__main__':
    build()
