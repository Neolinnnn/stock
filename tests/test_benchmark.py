"""benchmark：0050 分割還原、同期報酬、超額報酬統計。"""
import math

from benchmark import Benchmark, adjust_splits, excess_stats


def bar(o, c, vol=1000):
    return {'open': o, 'high': max(o, c), 'low': min(o, c), 'close': c, 'volume': vol}


# 0050 2025-06 一拆四：06-10 收 188.65，停牌至 06-17，06-18 以約四分之一價開出
SPLIT_BARS = {
    '20250609': bar(186.0, 187.0),
    '20250610': bar(187.5, 188.65),
    '20250618': bar(47.2, 47.5),
    '20250619': bar(47.5, 48.0),
}


def test_adjust_splits_divides_pre_split_prices_only():
    adj = adjust_splits(SPLIT_BARS)
    assert math.isclose(adj['20250610']['close'], 188.65 / 4)
    assert math.isclose(adj['20250609']['open'], 186.0 / 4)
    assert adj['20250618']['open'] == 47.2          # 分割後不動
    assert adj['20250609']['volume'] == 1000        # 非價格欄位不動
    assert SPLIT_BARS['20250610']['close'] == 188.65  # 不改原資料


def test_adjust_splits_ignores_normal_limit_down():
    bars = {'20260101': bar(100, 100), '20260102': bar(90, 90)}   # 跌停 10%
    assert adjust_splits(bars) == bars


def test_adjust_splits_detects_split_across_zero_filled_halt():
    bars = dict(SPLIT_BARS, **{'20250611': bar(0, 0), '20250612': bar(0, 0)})
    adj = adjust_splits(bars)
    assert math.isclose(adj['20250610']['close'], 188.65 / 4)


def test_ret_pct_across_split_is_real_return_not_minus_75():
    b = Benchmark(SPLIT_BARS)
    r = b.ret_pct('20250609', '20250619')   # 186/4=46.5 → 48.0
    assert math.isclose(r, round((48.0 / 46.5 - 1) * 100, 2))


def test_ret_pct_snaps_to_nearest_trading_days_during_halt():
    b = Benchmark(SPLIT_BARS)
    # 06-12 停牌：進場取之後首日 06-18 開盤；出場 06-19 收盤
    assert b.ret_pct('20250612', '20250619') == round((48.0 / 47.2 - 1) * 100, 2)
    # 進出場都落在停牌期間 → 無法對照
    assert b.ret_pct('20250611', '20250613') is None


def test_ret_pct_missing_dates_and_out_of_range():
    b = Benchmark(SPLIT_BARS)
    assert b.ret_pct(None, '20250619') is None
    assert b.ret_pct('20250620', '20250630') is None   # 進場在資料之後
    assert Benchmark({}).ret_pct('20250609', '20250619') is None


def test_period_buy_and_hold():
    p = Benchmark(SPLIT_BARS).period('20250601', '20250630')
    assert p == {'id': '0050', 'start': '20250609', 'end': '20250619',
                 'return_pct': round((48.0 / 46.5 - 1) * 100, 2)}


def test_excess_stats_skips_unmatched_and_counts_beats():
    s = excess_stats([(10.0, 2.0), (-5.0, 1.0), (3.0, None), (None, 1.0)])
    assert s == {'bench_n': 2, 'bench_avg_return': 1.5,
                 'excess_return': round(((10 - 2) + (-5 - 1)) / 2, 2), 'beat_rate': 0.5}
    assert excess_stats([(1.0, None)]) == {}
