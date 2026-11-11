"""歷史日 payload 重建時，as-of 欄位須沿用當時的值，不可用最新資料重算。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
import build_docs  # noqa: E402

SUMMARY = {
    'date': '20230308', 'sectors': {}, 'market': {},
    'qualified': [{'id': '3450', 'name': '聯鈞', 'sector': '光通訊', 'price': 42.35},
                  {'id': '9999', 'name': '新入選', 'sector': 'X', 'price': 10}],
    'dual_filter': [{'id': '2330', 'name': '台積電', 'sector': 'Y', 'price': 500}],
}
FROZEN = {
    'qualified': [{'id': '3450', 'trend_analysis': {'ma60': 40.1}, 'product_mix': {'x': 1}}],
    'dualFilterExcluded': [{'id': '2330', 'trend_analysis': {'ma60': 480}}],
    'signalFlips': [{'id': '1111'}],
}


def test_frozen_fields_carried_not_recomputed(monkeypatch):
    def boom(*a, **k):
        raise AssertionError('歷史日不應重算')
    monkeypatch.setattr(build_docs, '_analyze_stock', boom)
    monkeypatch.setattr(build_docs, 'build_signal_flips', boom)
    p = build_docs.build_daily_payload(SUMMARY, frozen=FROZEN)
    q = {x['id']: x for x in p['qualified']}
    assert q['3450']['trend_analysis'] == {'ma60': 40.1}
    assert q['3450']['product_mix'] == {'x': 1}
    assert 'trend_analysis' not in q['9999']      # 舊檔沒有就留空，不拿今天的資料補
    assert p['dualFilterExcluded'][0]['trend_analysis'] == {'ma60': 480}
    assert p['signalFlips'] == [{'id': '1111'}]


def test_latest_day_still_computes(monkeypatch):
    called = []
    monkeypatch.setattr(build_docs, 'build_signal_flips',
                        lambda *a, **k: called.append(1) or [])
    build_docs.build_daily_payload(SUMMARY)
    assert called
