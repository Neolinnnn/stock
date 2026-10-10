"""backtest_condition_universe：同股不重複進場、分年統計、三項標準判定。"""
import pandas as pd

import backtest_condition_universe as cu
from benchmark import Benchmark


def _df(closes):
    days = [d.strftime('%Y%m%d') for d in pd.bdate_range('2025-01-01', periods=len(closes))]
    return pd.DataFrame({'date': days, 'open': closes, 'high': closes, 'low': closes,
                         'close': closes, 'volume': [1000] * len(closes)})


def test_run_filter_no_reentry_while_holding():
    # 100 → 第 3 根起漲到 120（TP18 觸發）→ 之後平盤
    df = _df([100, 100, 100, 120, 120, 120, 120])
    d = list(df['date'])
    feats = {'2330': {day: {'hit': True} for day in d}}   # 每天都成立
    trades = cu.run_filter(lambda f, s: f['hit'], feats, {'2330': df}, Benchmark({}), 'TP18SL15', {})
    # d0 訊號 → d1 開盤 100 進場、d3 收 120 停利出場；d1、d2 持倉中不重複進場；
    # d3（出場當日）可再訊號 → d4 進場，之後平盤未結算 → 不再進場
    assert [t['date'] for t in trades] == [d[0], d[3]]
    assert trades[0]['result']['exit'] == d[3] and trades[1]['result'] is None


def test_evaluate_splits_years():
    trades = [{'date': '20250310', 'result': {'ret': 0.1, 'days': 3}},
              {'date': '20260310', 'result': {'ret': -0.1, 'days': 3}}]
    st = cu.evaluate(trades)
    assert (st['all']['n'], st['2025']['win_rate'], st['2026']['win_rate']) == (2, 1.0, 0.0)


def _st(n, wr, wlb, y25, y26, excess=None):
    s = {'n': n, 'win_rate': wr, 'wilson_lb': wlb}
    if excess is not None:
        s['excess_return'] = excess
    return {'all': s, '2025': {'n': 10, 'win_rate': y25}, '2026': {'n': 10, 'win_rate': y26}}


BASE = _st(1000, 0.60, 0.57, 0.60, 0.60)


def test_verdict_rules():
    assert cu.verdict(_st(10, 0.9, 0.6, 0.9, 0.9), BASE) == '樣本不足'
    assert cu.verdict(_st(300, 0.70, 0.65, 0.7, 0.7, excess=1.0), BASE) == '✅ 通過'
    assert cu.verdict(_st(300, 0.70, 0.65, 0.7, 0.7), BASE) == '⚠️ ①②過，0050 未判定'
    assert cu.verdict(_st(300, 0.70, 0.65, 0.7, 0.55, excess=-1.0), BASE) == '❌ ②2026、③0050'
    assert cu.verdict(_st(300, 0.62, 0.58, 0.7, 0.7), BASE) == '❌ ①Wilson'
