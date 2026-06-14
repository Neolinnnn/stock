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


import json
from weekly_summary import load_prev_week_changes


def test_load_prev_week_changes_reads_latest_prior(tmp_path):
    # 建兩個舊週報資料夾，應讀較新的那個（且早於 today）
    (tmp_path / 'weekly_20260605').mkdir()
    (tmp_path / 'weekly_20260605' / 'weekly.json').write_text(
        json.dumps({'sector_changes': [{'sector': 'A', 'change': 5.0}]}), encoding='utf-8')
    (tmp_path / 'weekly_20260529').mkdir()
    (tmp_path / 'weekly_20260529' / 'weekly.json').write_text(
        json.dumps({'sector_changes': [{'sector': 'A', 'change': 1.0}]}), encoding='utf-8')

    prev = load_prev_week_changes('20260612', base_dir=tmp_path)
    assert prev == {'A': 5.0}


def test_load_prev_week_changes_none_when_absent(tmp_path):
    assert load_prev_week_changes('20260612', base_dir=tmp_path) == {}


from weekly_summary import build_narrative_context, generate_narrative


def test_build_narrative_context_picks_top_movers():
    metrics = [
        {'sector': 'A', 'change': 9.0, 'level': 10.0, 'prev_change': 1.0},
        {'sector': 'B', 'change': 3.0, 'level': 2.0, 'prev_change': None},
        {'sector': 'C', 'change': -8.0, 'level': -5.0, 'prev_change': 2.0},
    ]
    top_buys = [{'stock': '2368 金像電', 'buy_days': 5}]
    ctx = build_narrative_context(metrics, top_buys)
    assert ctx['accelerating'][0]['sector'] == 'A'
    assert ctx['decelerating'][0]['sector'] == 'C'
    assert ctx['top_buys'] == top_buys
    assert ctx['sector_metrics'] == metrics


class _FakeWriter:
    def __init__(self, raise_it=False):
        self.raise_it = raise_it
    def generate(self, task, context, **kw):
        if self.raise_it:
            raise RuntimeError('api down')
        return '本週輪動回顧…\n下週聚焦…'


def test_generate_narrative_returns_text():
    out = generate_narrative(_FakeWriter(), {'x': 1}, '20260612')
    assert '回顧' in out


def test_generate_narrative_fallbacks_to_empty_on_error():
    out = generate_narrative(_FakeWriter(raise_it=True), {'x': 1}, '20260612')
    assert out == ''


from weekly_summary import build_weekly_payload


def test_build_weekly_payload_carries_new_fields():
    summary = {
        'week_ending': '2026-06-12',
        'days_covered': 5,
        'sector_changes': [{'sector': 'A', 'change': 3.0, 'level': 4.0, 'prev_change': 1.0}],
        'top_buys': [{'stock': '2368 金像電', 'buy_days': 5}],
        'narrative': '本週輪動回顧…',
    }
    payload = build_weekly_payload(summary)
    assert payload['meta']['date'] == '2026-06-12'
    assert payload['changes'][0] == {'sector': 'A', 'change': 3.0, 'level': 4.0, 'prev_change': 1.0}
    assert payload['buys'][0] == {'stock': '2368 金像電', 'days': 5}
    assert payload['narrative'] == '本週輪動回顧…'


def test_build_weekly_payload_narrative_defaults_empty():
    summary = {'week_ending': '2026-06-12', 'days_covered': 5,
               'sector_changes': [], 'top_buys': []}
    assert build_weekly_payload(summary)['narrative'] == ''
