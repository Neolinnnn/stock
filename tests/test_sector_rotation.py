import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from sector_rotation_backtest import load_daily_signals


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
