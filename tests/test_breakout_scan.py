"""breakout_scan.load_backtest_ref：回測數字從 breakout_lab.json 讀入，不再寫死。"""
import json

import breakout_scan


def test_backtest_ref_reads_lab_json(monkeypatch, tmp_path):
    f = tmp_path / 'breakout_lab.json'
    f.write_text(json.dumps({
        'generated_at': '2026-10-12 21:00',
        'period': {'start': '20250101', 'signals': 250},
        'exits': {'TP18SL15': {'win_rate': 0.64, 'avg_ret': 7.5},
                  'TRAIL15': {'win_rate': 0.58, 'avg_ret': 17.76, 'profit_factor': 4.51}},
    }), encoding='utf-8')
    monkeypatch.setattr(breakout_scan, 'BACKTEST_JSON', f)
    assert breakout_scan.load_backtest_ref() == {
        'n': 250, 'tp18sl15_win': 0.64, 'trail15_avg': 17.76,
        'trail15_pf': 4.51, 'period': '2025/01~2026/10'}


def test_backtest_ref_missing_or_malformed_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(breakout_scan, 'BACKTEST_JSON', tmp_path / 'none.json')
    assert breakout_scan.load_backtest_ref() == {}
    bad = tmp_path / 'bad.json'
    bad.write_text('{"exits": {}}', encoding='utf-8')
    monkeypatch.setattr(breakout_scan, 'BACKTEST_JSON', bad)
    assert breakout_scan.load_backtest_ref() == {}
