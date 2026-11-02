# tests/test_backfill_stock.py
"""backfill_stock：推薦名單推導、族群強弱翻轉、規則沿革切換。"""
import copy
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
import backfill_stock as bs  # noqa: E402

REPORTS = Path(__file__).resolve().parent.parent / 'daily_reports'


def _row(sid, price, ma10, signal='HOLD', ret=0.0, rsi=50.0, sharpe=1.0, wr=0.5):
    return {'id': sid, 'name': sid, 'price': price, 'rsi': rsi, 'ret_20d': ret,
            'signal': signal, 'ma10': ma10, 'cv_sharpe': sharpe, 'cv_win_rate': wr}


def _summary(date, avg_ret, strong):
    """一個族群 S（兩檔，A 命中雙篩選且乖離 0%）＋一個強勢族群 T。"""
    return {
        'date': date,
        'sectors': {
            'S': {'avg_ret_20d': avg_ret, 'avg_rsi': 50.0, 'avg_sharpe': 1.0,
                  'stocks': [_row('A', 100.0, 100.0, 'BUY', avg_ret),
                             _row('B', 100.0, 100.0, 'HOLD', avg_ret)],
                  'hot_count': 0, 'buy_count': 1, 'qualified_count': 2},
            'T': {'avg_ret_20d': 5.0, 'avg_rsi': 50.0, 'avg_sharpe': 1.0,
                  'stocks': [], 'hot_count': 0, 'buy_count': 0, 'qualified_count': 0},
        },
        'strong_sectors': (['S'] if strong else []) + ['T'],
        'weak_sectors': [],
        'dual_filter': [{'sector': 'S', 'id': 'A', 'name': 'A', 'price': 100.0,
                         'rsi': 50.0, 'cv_sharpe': 1.0, 'bias_ma10': 0.0,
                         'sector_strong': strong, 'passes_bias': True,
                         'passes_all': True if date >= '20260924' else strong}],
        'qualified': [], 'alerts': [],
    }


def _new_stock(ret):
    """待補建的單股結果（HOLD，不命中雙篩選）。"""
    r = {'id': 'N', 'name': 'N', 'price': 50.0, 'rsi': 40.0, 'ret_20d': ret,
         'signal': 'HOLD', 'cv_sharpe': 1.0, 'cv_win_rate': 0.5}
    single = {'sectors': {'S': {'stocks': [dict(r, ret_20d=round(ret * 100, 1))]}},
              'dual_filter': [], 'qualified': [], 'alerts': []}
    return r, single


def test_derive_qualified_reproduces_every_stored_report():
    """推導式須重現 repo 內所有含 passes_bias 欄位的報告（兩種閘門／捨入時期）。"""
    checked = 0
    for f in sorted(REPORTS.glob('2026*/summary.json')):
        s = json.loads(f.read_text(encoding='utf-8'))
        df = s.get('dual_filter') or []
        if not df or 'passes_bias' not in df[0]:
            continue
        assert bs.derive_qualified(s) == s['qualified'], f.parent.name
        checked += 1
    assert checked > 0


def test_rules_asof_cutover():
    assert bs.rules_asof('20260922')['ENABLE_TREND_CONTINUATION_BUY'] is False
    assert bs.rules_asof('20260923')['ENABLE_TREND_CONTINUATION_BUY'] is True
    assert bs.rules_asof('20260923')['REQUIRE_STRONG_SECTOR'] is True
    assert bs.rules_asof('20260924')['REQUIRE_STRONG_SECTOR'] is False


def test_flip_to_strong_under_gate_admits_sector_peers():
    """閘門時期：新股把族群拉成強勢 → 同族群原本被擋的 A 進推薦。"""
    s = _summary('20260908', 2.0, strong=False)
    s['qualified'] = bs.derive_qualified(s)
    assert s['qualified'] == []
    bs.merge_into(s, 'S', *_new_stock(0.10), stamp='X')   # (2+2+10)/3 = 4.67 > 3
    assert s['strong_sectors'] == ['S', 'T']
    assert [q['id'] for q in s['qualified']] == ['A']
    assert 'sector_strong' not in s['qualified'][0]       # 閘門時期無此欄
    assert s['dual_filter'][0]['passes_all'] is True
    assert s['sectors']['S']['stocks'][-1]['backfilled'] == 'X'


def test_flip_to_weak_under_gate_drops_sector_peers():
    s = _summary('20260908', 4.0, strong=True)
    s['qualified'] = bs.derive_qualified(s)
    assert [q['id'] for q in s['qualified']] == ['A']
    bs.merge_into(s, 'S', *_new_stock(-0.05), stamp='X')  # (4+4-5)/3 = 1.0
    assert s['strong_sectors'] == ['T']
    assert s['qualified'] == []
    assert s['dual_filter'][0]['passes_all'] is False


def test_flip_after_gate_only_changes_labels():
    s = _summary('20261015', 2.0, strong=False)
    s['qualified'] = bs.derive_qualified(s)
    before = [q['id'] for q in s['qualified']]
    bs.merge_into(s, 'S', *_new_stock(0.10), stamp='X')
    assert [q['id'] for q in s['qualified']] == before == ['A']
    assert s['qualified'][0]['sector_strong'] is True
    assert s['dual_filter'][0]['passes_all'] is True


def test_merge_refuses_when_stored_qualified_not_reproducible():
    s = _summary('20261015', 2.0, strong=False)
    s['qualified'] = []                                   # 與推導結果（['A']）不符
    original = copy.deepcopy(s)
    with pytest.raises(RuntimeError):
        bs.merge_into(s, 'S', *_new_stock(0.10), stamp='X')
    assert s == original                                  # 未動到任何欄位
