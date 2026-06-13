from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from weekly_summary import compute_sector_metrics


def _report(sectors):
    return {'sectors': {name: {'avg_ret_20d': v, 'stocks': []} for name, v in sectors.items()}}


def test_compute_sector_metrics_change_and_level():
    week = [
        _report({'A': 1.0, 'B': 5.0}),   # 週一
        _report({'A': 4.0, 'B': 2.0}),   # 週五（最後一日 = level）
    ]
    metrics = compute_sector_metrics(week, prev_changes={})
    by = {m['sector']: m for m in metrics}
    assert by['A']['change'] == 3.0      # 4 - 1
    assert by['A']['level'] == 4.0       # 最後一日水位
    assert by['B']['change'] == -3.0     # 2 - 5
    assert by['B']['level'] == 2.0
    assert by['A']['prev_change'] is None


def test_compute_sector_metrics_sorted_by_change_desc():
    week = [_report({'A': 0.0, 'B': 0.0}), _report({'A': 1.0, 'B': 9.0})]
    metrics = compute_sector_metrics(week, prev_changes={})
    assert [m['sector'] for m in metrics] == ['B', 'A']


def test_compute_sector_metrics_prev_change_lookup():
    week = [_report({'A': 0.0}), _report({'A': 2.0})]
    metrics = compute_sector_metrics(week, prev_changes={'A': 5.0})
    assert metrics[0]['prev_change'] == 5.0
