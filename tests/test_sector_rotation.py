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
