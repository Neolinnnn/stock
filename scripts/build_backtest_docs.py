"""
將 backtest_results*.json 整合為 docs/backtest_summary.json，
backtest_results*_al.json（只用今日行動清單進場）整合為 docs/backtest_summary_al.json，
並用 indicators/stock_analyzer 對所有持倉中的股票補充趨勢分析。

用法：
  python scripts/build_backtest_docs.py
"""
import csv
import json
import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
    ('3m',  'backtest_results_3m.json',    '近3個月'),
    ('6m',  'backtest_results_6m.json',    '近6個月'),
    ('9m',  'backtest_results_9m.json',    '近9個月'),
    ('12m', 'backtest_results_12m.json',   '近12個月'),
    ('36m', 'backtest_results_36m.json',   '近3年'),
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
    'MA5_MA10':  '跌破MA5賣半 / 跌破MA10清倉',
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

# Load latest daily context for sector strength and CV Sharpe
_daily_context: dict = {}
try:
    with open(ROOT / 'docs' / 'daily.json', encoding='utf-8') as _f:
        _daily_raw = json.load(_f)
    _strong = set(_daily_raw.get('meta', {}).get('強勢族群', '').split(', '))
    _weak   = set(_daily_raw.get('meta', {}).get('弱勢族群', '').split(', '))
    for _s in _daily_raw.get('stocks', []):
        _daily_context[_s['id']] = {
            'sector': _s.get('sector', ''),
            'sector_is_strong': _s.get('sector', '') in _strong or None,
        }
    # Mark weak sectors
    for _sid, _ctx in _daily_context.items():
        if _ctx['sector'] in _weak:
            _ctx['sector_is_strong'] = False
        elif _ctx['sector'] in _strong:
            _ctx['sector_is_strong'] = True
        else:
            _ctx['sector_is_strong'] = None
except Exception:
    pass


def _get_analysis(sid: str) -> dict:
    if sid in _analysis_cache:
        return _analysis_cache[sid]
    if not _ANALYZER_OK:
        return {}
    df = _load_stock_df(sid)
    if df is None:
        return {}
    ctx = _daily_context.get(sid, {})
    result = analyze_stock(df, sid, sector_is_strong=ctx.get('sector_is_strong'))
    d = result.to_dict()
    d['sector_is_strong'] = ctx.get('sector_is_strong')
    _analysis_cache[sid] = d
    return d


# ── 交易紀錄補充：現價與參考訊號 ──────────────────────────────────────────────
# 回測結果只有進出場，這裡用本地資料回推：
#   現價    → docs/stocks（每日更新）與 backtest_cache（回測抓價快取）取日期較新者
#   參考訊號 → 訊號日當天的條件，定義與線上一致：
#     雙篩選       cv_sharpe≥0.3 且 cv_win_rate≥0.4（backtest.load_buy_signals）
#     今日行動清單 雙篩選＋乖離MA10≤2%（backtest.filter_bias_ma10）
#     動能進場     量比≥1.2 或 MACD柱>0且增長（build_docs.entry_strategy）
#   資料不足時為 None（前端顯示「?」），不當成未命中。

_ohlcv_cache: dict = {}
_summary_cache: dict = {}
_tag_cache: dict = {}


def _load_cache_ohlcv(sid: str) -> list[dict]:
    """backtest_cache/<sid>_ohlcv.csv → [{'date','close','volume'}]（日期升冪），無檔回空。"""
    if sid in _ohlcv_cache:
        return _ohlcv_cache[sid]
    rows = []
    path = ROOT / 'backtest_cache' / f'{sid}_ohlcv.csv'
    if path.exists():
        with open(path, encoding='utf-8') as f:
            for r in csv.DictReader(f):
                try:
                    rows.append({'date': r['date'], 'close': float(r['close']),
                                 'volume': float(r['volume'])})
                except (KeyError, ValueError):
                    continue
        rows.sort(key=lambda r: r['date'])
    _ohlcv_cache[sid] = rows
    return rows


def _latest_close(sid: str) -> dict | None:
    """最新收盤 {'close', 'date'(YYYYMMDD)}，docs/stocks 與 backtest_cache 取較新者。"""
    best = None
    path = ROOT / 'docs' / 'stocks' / f'{sid}.json'
    if path.exists():
        with open(path, encoding='utf-8') as f:
            o = json.load(f).get('ohlcv', {})
        if o.get('date') and o.get('close'):
            best = {'close': o['close'][-1], 'date': o['date'][-1].replace('-', '')}
    rows = _load_cache_ohlcv(sid)
    if rows and (best is None or rows[-1]['date'] > best['date']):
        best = {'close': rows[-1]['close'], 'date': rows[-1]['date']}
    return best


def _summary_buy(date: str, sid: str) -> dict | None:
    """daily_reports/<date>/summary.json 中該股的 BUY 紀錄（同 load_buy_signals 取第一筆）。"""
    if date not in _summary_cache:
        path = ROOT / 'daily_reports' / date / 'summary.json'
        buys = {}
        if path.exists():
            with open(path, encoding='utf-8') as f:
                s = json.load(f)
            for sec in s.get('sectors', {}).values():
                for st in sec.get('stocks', []):
                    if st.get('signal') == 'BUY' and st.get('id'):
                        buys.setdefault(st['id'], st)
        _summary_cache[date] = buys
    return _summary_cache[date].get(sid)


def _ema(values: list[float], span: int) -> list[float]:
    """同 pandas ewm(span, adjust=False)。"""
    a = 2 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(a * v + (1 - a) * out[-1])
    return out


def _signal_tags(sid: str, date: str, action_list: bool) -> dict:
    """訊號日 date 的參考訊號 {'momentum','dual','action_list'}，值為 True/False/None。
    action_list=True（_al 回測）時該筆本就是依行動清單進場，直接標 True。"""
    key = (sid, date, action_list)
    if key in _tag_cache:
        return _tag_cache[key]

    st = _summary_buy(date, sid)
    dual = None
    if st is not None:
        dual = (st.get('cv_sharpe') or 0) >= 0.3 and (st.get('cv_win_rate') or 0) >= 0.4

    rows = [r for r in _load_cache_ohlcv(sid) if r['date'] <= date]
    has_day = bool(rows) and rows[-1]['date'] == date
    closes = [r['close'] for r in rows]

    # 動能：量比與 MACD 柱，需 60 根暖身（同 backtest_entry_lab.build_features）
    momentum = None
    if has_day and len(rows) >= 60:
        vols = [r['volume'] for r in rows[-20:]]
        vma20 = sum(vols) / 20
        vol_ok = bool(vma20) and vols[-1] / vma20 >= 1.2
        macd = [f - s for f, s in zip(_ema(closes, 12), _ema(closes, 26))]
        sig = _ema(macd, 9)
        hist = [m - s for m, s in zip(macd, sig)]
        momentum = vol_ok or (hist[-1] > 0 and hist[-1] > hist[-2])

    if action_list:
        al = True
    elif dual is False:
        al = False
    elif dual and has_day and len(closes) >= 10:
        ma10 = sum(closes[-10:]) / 10
        al = (closes[-1] - ma10) / ma10 * 100 <= 2.0
    else:
        al = None

    tags = {'momentum': momentum, 'dual': dual, 'action_list': al}
    _tag_cache[key] = tags
    return tags


def _process_period(fname: str, label: str, action_list: bool = False) -> dict | None:
    path = ROOT / fname
    if not path.exists():
        return None
    with open(path, encoding='utf-8') as f:
        raw = json.load(f)

    combos_out = {}
    all_open_ids = set()
    signal_tags = {}   # '<stock_id>_<signal_date>' → 參考訊號；各組合共用，避免逐筆重複存

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
                'signal_date': t.get('signal_date'),
            })
            if t.get('signal_date'):
                tk = f"{t['stock_id']}_{t['signal_date']}"
                if tk not in signal_tags:
                    signal_tags[tk] = _signal_tags(t['stock_id'], t['signal_date'], action_list)
            if t['result'] == 'OPEN':
                all_open_ids.add(t['stock_id'])

        wins_n  = stats['wins']
        losses_n = stats['losses']
        open_n  = stats['open_count']
        settled = wins_n + losses_n
        total_all = settled + open_n

        # 已結算勝率（WIN / (WIN+LOSS)）— 不含持倉中
        wr_settled = wins_n / settled if settled else 0
        # 悲觀勝率（假設持倉中全部虧損）
        wr_pessimistic = wins_n / total_all if total_all else 0
        # 樂觀勝率（假設持倉中全部獲利）
        wr_optimistic = (wins_n + open_n) / total_all if total_all else 0

        combos_out[combo_id] = {
            'id':    combo_id,
            'label': COMBO_LABELS.get(combo_id, combo_id),
            'stats': {
                # 已結算
                'wins':              wins_n,
                'losses':            losses_n,
                'settled':           settled,
                'win_rate_settled':  round(wr_settled, 4),
                # 持倉中
                'open_count':        open_n,
                'total_all':         total_all,
                # 區間勝率（持倉中尚未知結果）
                'win_rate_pessimistic': round(wr_pessimistic, 4),
                'win_rate_optimistic':  round(wr_optimistic, 4),
                # 其他
                'avg_return':       round(stats['avg_return'], 2),
                'avg_holding_days': round(stats['avg_holding_days'], 1),
            },
            'trades': trades,
        }

    # Run analysis on all open-position stocks
    open_analysis = {}
    for sid in all_open_ids:
        a = _get_analysis(sid)
        if a:
            open_analysis[sid] = a

    # 現價：本期間所有出現過的個股（含已出場，供對照出場後走勢）
    prices = {}
    for sid in sorted({t['stock_id'] for c in combos_out.values() for t in c['trades']}):
        px = _latest_close(sid)
        if px:
            prices[sid] = px

    # Pick best combo by win_rate * avg_return (balanced score)
    def _score(c):
        s = c['stats']
        if s['settled'] < 5:
            return 0
        return s['win_rate_settled'] * s['avg_return']

    best_id = max(combos_out.values(), key=_score)['id'] if combos_out else None

    return {
        'label':       label,
        'date_range':  raw.get('date_range', {}),
        'best_combo':  best_id,
        'combos':      combos_out,
        'open_analysis': open_analysis,
        'prices':      prices,
        'signal_tags': signal_tags,
    }


def build(suffix: str = '', dest_name: str = 'backtest_summary.json'):
    """suffix：回測結果檔名後綴（'_al' = 只用今日行動清單進場，見 backtest.py --action-list）。"""
    out = {'periods': {}}

    for key, fname, label in PERIODS + TRAILING:
        result = _process_period(fname.replace('.json', suffix + '.json'), label,
                                 action_list=(suffix == '_al'))
        if result:
            out['periods'][key] = result
            print(f'  [{key}] {label} — {len(result["combos"])} 組合, '
                  f'最佳: {result["best_combo"]}')
        else:
            print(f'  [{key}] 檔案不存在，跳過')

    dest = ROOT / 'docs' / dest_name
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f'\ndocs/{dest_name} 已寫入（{dest.stat().st_size // 1024} KB）')


if __name__ == '__main__':
    build()
    build('_al', 'backtest_summary_al.json')
