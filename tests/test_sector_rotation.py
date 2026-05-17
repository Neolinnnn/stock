import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from sector_rotation_backtest import load_daily_signals, score_sectors


SECTORS = [
    {'sector': 'A', 'ret20': 10.0, 'rsi': 60, 'hot': 3, 'buy': 2},
    {'sector': 'B', 'ret20':  5.0, 'rsi': 55, 'hot': 5, 'buy': 0},
    {'sector': 'C', 'ret20': 15.0, 'rsi': 70, 'hot': 1, 'buy': 1},
    {'sector': 'D', 'ret20': -2.0, 'rsi': 45, 'hot': 0, 'buy': 3},
]


def test_score_sectors_ret20_picks_highest():
    top = score_sectors(SECTORS, rule='ret20', top_n=2)
    assert top == ['C', 'A']  # 15.0, 10.0


def test_score_sectors_rsi_picks_highest():
    top = score_sectors(SECTORS, rule='rsi', top_n=2)
    assert top == ['C', 'A']  # 70, 60


def test_score_sectors_hot_picks_highest():
    top = score_sectors(SECTORS, rule='hot', top_n=2)
    assert top == ['B', 'A']  # 5, 3


def test_score_sectors_composite_normalizes():
    # composite = 0.5*ret20_norm + 0.3*hot_norm + 0.2*buy_norm
    # ret20 range -2~15, hot 0~5, buy 0~3 → normalize to [0,1]
    top = score_sectors(SECTORS, rule='composite', top_n=2)
    # A: 0.5*((10+2)/17) + 0.3*(3/5) + 0.2*(2/3) = 0.353 + 0.18 + 0.133 = 0.666
    # B: 0.5*((5+2)/17)  + 0.3*(5/5) + 0.2*(0/3) = 0.206 + 0.30 + 0      = 0.506
    # C: 0.5*((15+2)/17) + 0.3*(1/5) + 0.2*(1/3) = 0.500 + 0.06 + 0.067 = 0.627
    # D: 0.5*((-2+2)/17) + 0.3*(0/5) + 0.2*(3/3) = 0     + 0    + 0.200 = 0.200
    assert top[0] == 'A'
    assert top[1] == 'C'


def test_score_sectors_unknown_rule_raises():
    import pytest
    with pytest.raises(ValueError, match='unknown rule'):
        score_sectors(SECTORS, rule='banana', top_n=2)


def test_load_daily_signals_returns_sorted_dates(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / '20250416.json').write_text(json.dumps({
        'sectors': [{'sector': 'A', 'ret20': 5.0, 'rsi': 50, 'hot': 1, 'buy': 0}],
        'stocks':  [{'id': '2330', 'name': '台積電', 'sector': 'A',
                     'ret20': 3.2, 'chipTotal': 100}],
    }), encoding='utf-8')
    (docs / '20250415.json').write_text(json.dumps({
        'sectors': [{'sector': 'A', 'ret20': 4.0, 'rsi': 48, 'hot': 0, 'buy': 0}],
        'stocks':  [{'id': '2330', 'name': '台積電', 'sector': 'A',
                     'ret20': 2.0, 'chipTotal': 50}],
    }), encoding='utf-8')

    result = load_daily_signals(docs)

    assert list(result.keys()) == ['20250415', '20250416']
    assert result['20250415']['sectors'][0]['ret20'] == 4.0
    assert result['20250416']['stocks'][0]['chipTotal'] == 100


def test_load_daily_signals_skips_non_date_files(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / '20250415.json').write_text(json.dumps({
        'sectors': [], 'stocks': []
    }), encoding='utf-8')
    (docs / 'dates.json').write_text('[]', encoding='utf-8')
    (docs / 'random.json').write_text('{}', encoding='utf-8')

    result = load_daily_signals(docs)
    assert list(result.keys()) == ['20250415']


from sector_rotation_backtest import select_stocks_in_sector


STOCKS = [
    {'id': '2330', 'sector': 'IC', 'ret20':  5.0, 'chipTotal': 1000},
    {'id': '2454', 'sector': 'IC', 'ret20': 10.0, 'chipTotal':  500},
    {'id': '3008', 'sector': 'IC', 'ret20':  2.0, 'chipTotal': 2000},
    {'id': '2308', 'sector': 'IC', 'ret20':  8.0, 'chipTotal':  100},
    {'id': '1234', 'sector': 'OTHER', 'ret20': 99.0, 'chipTotal': 9999},
]


def test_select_stocks_ret20_top_k():
    out = select_stocks_in_sector(STOCKS, sector='IC', rule='ret20_individual', top_k=2)
    assert [s['id'] for s in out] == ['2454', '2308']  # 10.0, 8.0


def test_select_stocks_chip_top_k():
    out = select_stocks_in_sector(STOCKS, sector='IC', rule='chip_concentration', top_k=2)
    assert [s['id'] for s in out] == ['3008', '2330']  # 2000, 1000


def test_select_stocks_ignores_other_sectors():
    out = select_stocks_in_sector(STOCKS, sector='IC', rule='ret20_individual', top_k=10)
    assert '1234' not in [s['id'] for s in out]


def test_select_stocks_handles_fewer_than_top_k():
    out = select_stocks_in_sector(STOCKS, sector='IC', rule='ret20_individual', top_k=10)
    assert len(out) == 4  # IC only has 4 stocks


def test_select_stocks_empty_sector_returns_empty():
    out = select_stocks_in_sector(STOCKS, sector='NONE', rule='ret20_individual', top_k=3)
    assert out == []


from sector_rotation_backtest import rebalance_dates


def test_rebalance_dates_weekly_picks_first_trading_day_each_week():
    # 2025-04-14 (Mon), 04-15 (Tue), 04-16 (Wed), 04-17 (Thu), 04-18 (Fri),
    # 04-21 (Mon next week), 04-22 (Tue)
    all_dates = ['20250414', '20250415', '20250416', '20250417', '20250418',
                 '20250421', '20250422']
    out = rebalance_dates(all_dates, frequency='weekly')
    # 第一個交易日 + 每週首個交易日
    assert out == ['20250414', '20250421']


def test_rebalance_dates_monthly_picks_first_trading_day_each_month():
    all_dates = ['20250415', '20250416', '20250430',
                 '20250501', '20250502', '20250531',
                 '20250602']
    out = rebalance_dates(all_dates, frequency='monthly')
    assert out == ['20250415', '20250501', '20250602']


def test_rebalance_dates_handles_holiday_gap():
    # 跳過 04-19 (Sat), 04-20 (Sun)
    all_dates = ['20250418', '20250421']  # Fri then Mon
    out = rebalance_dates(all_dates, frequency='weekly')
    assert out == ['20250418', '20250421']


def test_rebalance_dates_invalid_frequency_raises():
    import pytest
    with pytest.raises(ValueError, match='unknown frequency'):
        rebalance_dates(['20250415'], frequency='daily')


import pandas as pd
from sector_rotation_backtest import load_prices_cached, _cache_path_for


def test_cache_path_for_stock_id(tmp_path):
    assert _cache_path_for(tmp_path, '2330') == tmp_path / '2330.csv'
    assert _cache_path_for(tmp_path, 'TAIEX') == tmp_path / 'TAIEX.csv'


def test_load_prices_cached_reads_existing_csv(tmp_path):
    df = pd.DataFrame({
        'date':  ['2025-04-15', '2025-04-16'],
        'close': [100.0, 102.0],
    })
    df.to_csv(tmp_path / '2330.csv', index=False)

    out = load_prices_cached(cache_dir=tmp_path, stock_id='2330',
                             start='20250415', end='20250416', fetch_fn=None)
    assert list(out['close']) == [100.0, 102.0]


def test_load_prices_cached_calls_fetch_when_missing(tmp_path):
    fake_df = pd.DataFrame({'date': ['2025-04-15'], 'close': [100.0]})

    def fake_fetch(stock_id, start, end):
        assert stock_id == '2330'
        return fake_df

    out = load_prices_cached(cache_dir=tmp_path, stock_id='2330',
                             start='20250415', end='20250415',
                             fetch_fn=fake_fetch)
    assert (tmp_path / '2330.csv').exists()
    assert list(out['close']) == [100.0]


def test_load_prices_cached_returns_empty_on_fetch_failure(tmp_path):
    def fake_fetch(stock_id, start, end):
        raise RuntimeError('API down')

    out = load_prices_cached(cache_dir=tmp_path, stock_id='2330',
                             start='20250415', end='20250415',
                             fetch_fn=fake_fetch)
    assert out.empty


from sector_rotation_backtest import simulate_strategy


def test_simulate_strategy_buy_and_hold_equal_weight():
    # 2 stocks, both rise 10%, equal weight → portfolio +10%
    signals = {
        '20250415': {
            'sectors': [{'sector': 'A', 'ret20': 5, 'rsi': 50, 'hot': 1, 'buy': 0}],
            'stocks':  [
                {'id': '111', 'sector': 'A', 'ret20': 5, 'chipTotal': 100},
                {'id': '222', 'sector': 'A', 'ret20': 4, 'chipTotal':  50},
            ],
        },
        '20250416': {'sectors': [], 'stocks': []},
    }
    prices = {
        '111': pd.DataFrame({'date': ['2025-04-15', '2025-04-16'],
                             'close': [100.0, 110.0]}),
        '222': pd.DataFrame({'date': ['2025-04-15', '2025-04-16'],
                             'close': [50.0, 55.0]}),
    }
    result = simulate_strategy(
        signals=signals, prices=prices,
        sector_rule='ret20', stock_rule='ret20_individual',
        frequency='weekly', sectors_picked=1, stocks_per_sector=2,
        cost_per_turn=0.0,
    )
    assert result['equity'][0] == 1.0
    assert abs(result['equity'][-1] - 1.10) < 1e-6
    assert len(result['rebalances']) == 1


def test_simulate_strategy_applies_transaction_cost_on_rebalance():
    # First rebalance: full position change (initial buy) → cost charged
    signals = {
        '20250415': {
            'sectors': [{'sector': 'A', 'ret20': 5, 'rsi': 50, 'hot': 1, 'buy': 0}],
            'stocks':  [{'id': '111', 'sector': 'A', 'ret20': 5, 'chipTotal': 100}],
        },
    }
    prices = {
        '111': pd.DataFrame({'date': ['2025-04-15'], 'close': [100.0]}),
    }
    result = simulate_strategy(
        signals=signals, prices=prices,
        sector_rule='ret20', stock_rule='ret20_individual',
        frequency='weekly', sectors_picked=1, stocks_per_sector=1,
        cost_per_turn=0.01,  # 1% per turn
    )
    # Initial buy = 0.5 turn (only one side) = 0.5%
    assert abs(result['equity'][0] - (1.0 - 0.005)) < 1e-6


def test_simulate_strategy_skips_missing_signal_days():
    signals = {
        '20250415': {
            'sectors': [{'sector': 'A', 'ret20': 5, 'rsi': 50, 'hot': 1, 'buy': 0}],
            'stocks':  [{'id': '111', 'sector': 'A', 'ret20': 5, 'chipTotal': 100}],
        },
        '20250416': {'sectors': [], 'stocks': []},
    }
    prices = {
        '111': pd.DataFrame({'date': ['2025-04-15', '2025-04-16'],
                             'close': [100.0, 110.0]}),
    }
    result = simulate_strategy(
        signals=signals, prices=prices,
        sector_rule='ret20', stock_rule='ret20_individual',
        frequency='weekly', sectors_picked=1, stocks_per_sector=1,
        cost_per_turn=0.0,
    )
    assert len(result['rebalances']) == 1


import math
from sector_rotation_backtest import compute_metrics


def test_compute_metrics_flat_curve():
    eq = [1.0, 1.0, 1.0, 1.0]
    rebalances = [{'date': '20250415', 'holdings': []}]
    m = compute_metrics(eq, rebalances, rf=0.0)
    assert m['cagr'] == 0.0
    assert m['mdd'] == 0.0
    assert m['vol'] == 0.0


def test_compute_metrics_doubling_one_year():
    # 252 trading days, ends at 2.0 → CAGR ≈ 100%
    eq = [1.0 + i / 251 for i in range(252)]
    m = compute_metrics(eq, [], rf=0.0)
    assert 0.99 < m['cagr'] < 1.01
    assert m['mdd'] == 0.0


def test_compute_metrics_mdd():
    eq = [1.0, 1.2, 0.9, 1.1]
    m = compute_metrics(eq, [], rf=0.0)
    assert abs(m['mdd'] - (-0.25)) < 1e-6


def test_compute_metrics_win_rate_counts_positive_periods():
    eq = [1.0, 1.0]
    rebalances = [
        {'date': '20250415', 'holdings': [], 'period_return': 0.05},
        {'date': '20250422', 'holdings': [], 'period_return': -0.02},
        {'date': '20250429', 'holdings': [], 'period_return': 0.03},
    ]
    m = compute_metrics(eq, rebalances, rf=0.0)
    assert abs(m['win_rate'] - (2 / 3)) < 1e-6


def test_compute_metrics_handles_empty_curve():
    m = compute_metrics([], [], rf=0.0)
    assert m['cagr'] == 0.0
    assert m['sharpe'] == 0.0


from sector_rotation_backtest import simulate_benchmark_buyhold


def test_benchmark_buyhold_missing_price_treated_as_flat():
    dates = ['20250415', '20250416']
    prices = {
        # Stock A has full data, rises 10%
        'A': pd.DataFrame({'date': ['2025-04-15', '2025-04-16'],
                           'close': [100.0, 110.0]}),
        # Stock B has no data on day 2 → contributes 0 to return that day
        'B': pd.DataFrame({'date': ['2025-04-15'], 'close': [50.0]}),
    }
    out = simulate_benchmark_buyhold(dates, prices, ['A', 'B'])
    # Equal weight 0.5 each. A rises 10%, B contributes 0.
    # day_return = 0.5 * 0.10 + 0 = 0.05 → equity[-1] = 1.05
    assert abs(out['equity'][-1] - 1.05) < 1e-6
