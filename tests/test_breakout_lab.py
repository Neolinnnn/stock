"""backtest_breakout_lab：同股不重複進場、逐筆 0050 對照與 vs 0050 統計。"""
import pandas as pd

import backtest_breakout_lab as bo
from benchmark import Benchmark

DAYS = [d.strftime('%Y%m%d') for d in pd.bdate_range('2026-01-01', periods=40)]


def _stock():
    """先每日 +2% 漲 12 天，再每日 −3%：TP18 與追蹤停損 15% 都會在期間內出場。"""
    closes = [100 * 1.02 ** i if i <= 12 else 100 * 1.02 ** 12 * 0.97 ** (i - 12)
              for i in range(len(DAYS))]
    return pd.DataFrame({'date': DAYS, 'open': closes, 'high': closes, 'low': closes,
                         'close': closes, 'volume': 1000.0})


def _bench():
    """0050：開盤固定 100、收盤每日 +1，同期報酬可直接手算。"""
    return Benchmark({d: {'open': 100.0, 'close': 100.0 + i} for i, d in enumerate(DAYS)})


def _sig(i):
    return {'date': DAYS[i], 'stock_id': '2330'}


def test_build_trades_skips_reentry_and_attaches_0050():
    bench = _bench()
    trades = bo.build_trades([_sig(38), _sig(0), _sig(2)], {'2330': _stock()}, bench)
    # day2 時 day0 的倉位尚未出場 → 略過；day38 時已出場 → 再進場
    assert [t['sig']['date'] for t in trades] == [DAYS[0], DAYS[38]]
    first = trades[0]
    for ex in bo.EXITS:
        r = first['exits'][ex]
        assert r is not None
        assert first['bench'][ex] == bench.ret_pct(first['entry_date'], r['exit'])
    # 最後一天才進場、之後無 K 棒 → 未結算，無 0050 對照
    assert trades[1]['exits']['TRAIL15'] is None and trades[1]['bench']['TRAIL15'] is None


def test_exit_stats_reports_excess_vs_0050():
    trades = bo.build_trades([_sig(0), _sig(30)], {'2330': _stock()}, _bench())
    stats, mid = bo.exit_stats(trades)
    assert mid == DAYS[30]
    for ex in bo.EXITS:
        settled = [t for t in trades if t['exits'][ex]]
        want = sum(t['exits'][ex]['ret'] * 100 - t['bench'][ex] for t in settled) / len(settled)
        assert stats[ex]['n'] == len(settled)
        assert stats[ex]['excess_return'] == round(want, 2)
        assert {'wf_front', 'wf_back'} <= set(stats[ex])


def test_exit_stats_without_0050_omits_excess():
    trades = bo.build_trades([_sig(0)], {'2330': _stock()}, Benchmark({}))
    stats, _ = bo.exit_stats(trades)
    assert stats['TP18SL15']['n'] == 1 and 'excess_return' not in stats['TP18SL15']


def test_exit_stats_empty():
    stats, mid = bo.exit_stats([])
    assert mid == '' and all(s == {'n': 0, 'wf_front': {'n': 0}, 'wf_back': {'n': 0}}
                             for s in stats.values())
