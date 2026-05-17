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
