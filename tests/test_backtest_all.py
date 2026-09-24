"""backtest_all：期間與旗標對照、抓價失敗時中止且不覆寫。"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
import backtest
import backtest_all


def test_configs_cover_18_result_files_with_matching_flags():
    cfg = {n: f for n, _, f in backtest_all.CONFIGS}
    assert len(cfg) == 18
    assert cfg['backtest_results'] == ['--compare']
    assert cfg['backtest_results_al'] == ['--action-list']
    assert cfg['backtest_results_12m'] == ['--fixed']
    assert cfg['backtest_results_12m_al'] == ['--action-list', '--fixed']
    assert cfg['backtest_results_12m_trailing_al'] == ['--action-list']


def test_start_of_rolls_back_calendar_months():
    d = date(2026, 9, 23)
    assert backtest_all.start_of(1, d) == '20260823'
    assert backtest_all.start_of(36, d) == '20230923'


def test_missing_prices_abort_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(backtest_all, 'ROOT', tmp_path)
    monkeypatch.setattr(backtest_all.backtest, 'fetch_price_data',
                        lambda dl, ids, s, e: {'1101': {'20260901': {'open': 1.0}}, '2202': {}})

    def fake_main():  # 模擬 backtest.main 抓價
        backtest.fetch_price_data(None, ['1101', '2202'], '2026-08-23', '2026-09-23')

    monkeypatch.setattr(backtest_all.backtest, 'main', fake_main)
    with pytest.raises(RuntimeError, match='股價抓取失敗 1 檔'):
        backtest_all.main()
    assert not list(tmp_path.glob('backtest_results*.json'))
