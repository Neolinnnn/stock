"""update_positions 的同時持倉上限：名額用盡即停，超額時取 CV 夏普較高者。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
import position_tracker
from position_tracker import update_positions


def _use_tmp_state(monkeypatch, tmp_path, open_positions):
    """把狀態檔導到 tmp，避免動到 daily_reports/positions.json。"""
    f = tmp_path / 'positions.json'
    f.write_text(__import__('json').dumps({'open': open_positions}), encoding='utf-8')
    monkeypatch.setattr(position_tracker, 'POSITIONS_FILE', f)


def _holding(sid):
    return {'id': sid, 'name': sid, 'status': 'holding', 'phase': 1,
            'entry_date': '20260917', 'entry_price': 100.0,
            'high_watermark': 100.0, 'days_since_high': 0}


def _candidate(sid, sharpe):
    return {'id': sid, 'name': sid, 'price': 50.0, 'sector': 'X', 'sharpe': sharpe}


def _lookup(ids, price):
    """價格持平、MA10 在下方，確保既有持倉不會被出場規則掃掉。"""
    return {sid: {'price': price, 'ma10': price - 1.0} for sid in ids}


def test_cap_limits_new_entries(monkeypatch, tmp_path):
    """既有 3 檔 + 候選 15 檔，上限 12 → 只補 9 檔。"""
    _use_tmp_state(monkeypatch, tmp_path, [_holding(f'H{i}') for i in range(3)])
    cands = [_candidate(f'N{i:02d}', float(i)) for i in range(15)]
    lookup = {**_lookup([f'H{i}' for i in range(3)], 100.0),
              **_lookup([c['id'] for c in cands], 50.0)}

    out = update_positions('20260918', lookup, True, cands)

    assert len(out['new_entries']) == position_tracker.MAX_CONCURRENT - 3


def test_cap_keeps_highest_sharpe(monkeypatch, tmp_path):
    """名額不足時保留 CV 夏普最高者。"""
    _use_tmp_state(monkeypatch, tmp_path, [])
    cands = [_candidate(f'N{i:02d}', float(i)) for i in range(15)]
    out = update_positions('20260918', _lookup([c['id'] for c in cands], 50.0), True, cands)

    kept = sorted(e['id'] for e in out['new_entries'])
    top = sorted(f'N{i:02d}' for i in range(15 - position_tracker.MAX_CONCURRENT, 15))
    assert kept == top


def test_full_book_rejects_new_candidate(monkeypatch, tmp_path):
    """已滿倉 → 再高夏普的候選也不進場。"""
    held = [_holding(f'H{i}') for i in range(position_tracker.MAX_CONCURRENT)]
    _use_tmp_state(monkeypatch, tmp_path, held)
    lookup = {**_lookup([p['id'] for p in held], 100.0), **_lookup(['Z1'], 50.0)}

    out = update_positions('20260918', lookup, True, [_candidate('Z1', 99.0)])

    assert 'Z1' not in [e['id'] for e in out['new_entries']]


def test_missing_sharpe_sorts_last(monkeypatch, tmp_path):
    """gate_buys 缺 sharpe 欄位時視為 0，不應拋錯也不應插隊。"""
    _use_tmp_state(monkeypatch, tmp_path, [])
    cands = [{'id': 'NOSHARPE', 'name': 'x', 'price': 50.0, 'sector': 'X'},
             _candidate('GOOD', 5.0)]
    out = update_positions('20260918', _lookup(['NOSHARPE', 'GOOD'], 50.0), True, cands)

    assert [e['id'] for e in out['new_entries']] == ['GOOD', 'NOSHARPE']
