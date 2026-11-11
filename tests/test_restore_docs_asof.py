"""restore_docs_asof.apply_as_of：as-of 欄位以當日版本為準，無當日版本就移除。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))
from restore_docs_asof import apply_as_of  # noqa: E402


def _cur():
    return {'qualified': [{'id': '1', 'price': 10, 'trend_analysis': {'cp': 99}},
                          {'id': '2', 'trend_analysis': {'cp': 99}, 'product_mix': {'x': 9}}],
            'dualFilterExcluded': [{'id': '3', 'trend_analysis': {'cp': 99}}],
            'signalFlips': [{'id': 'late'}]}


def test_restores_same_day_values():
    old = {'qualified': [{'id': '1', 'trend_analysis': {'cp': 10}}],
           'dualFilterExcluded': [{'id': '3', 'trend_analysis': {'cp': 30}}],
           'signalFlips': [{'id': '7', 'signal_date': '20261023'}]}
    out = apply_as_of(_cur(), old)
    q = {x['id']: x for x in out['qualified']}
    assert q['1']['trend_analysis'] == {'cp': 10} and q['1']['price'] == 10
    assert 'trend_analysis' not in q['2'] and 'product_mix' not in q['2']   # 當日沒有就移除
    assert out['dualFilterExcluded'][0]['trend_analysis'] == {'cp': 30}
    assert out['signalFlips'] == [{'id': '7', 'signal_date': '20261022'}]  # 休市日改回前一日


def test_no_as_of_version_strips_everything():
    out = apply_as_of(_cur(), None)
    assert all('trend_analysis' not in x for x in out['qualified'] + out['dualFilterExcluded'])
    assert out['signalFlips'] == []
