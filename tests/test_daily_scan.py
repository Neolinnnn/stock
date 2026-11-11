"""daily_scan 的純函式測試（不打網路）。"""
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
ds = runpy.run_path(str(ROOT / 'scripts' / 'daily_scan.py'), run_name='daily_scan_test')


def test_chip_concentration_converts_lots_to_shares():
    # 法人近5日買超 2,000 張 = 2,000,000 股；5日成交 20,000,000 股 → 10%
    assert ds['chip_concentration'](2000, 20_000_000) == 10.0
    assert ds['chip_concentration'](-500, 10_000_000) == -5.0


def test_chip_concentration_missing_volume():
    assert ds['chip_concentration'](100, None) is None
    assert ds['chip_concentration'](100, 0) is None


def test_indicators_importable_from_scan(tmp_path):
    """模擬 CI 的 `python scripts/daily_scan.py`：sys.path[0] 只有 scripts/，且 cwd 不在 repo。

    在 pytest 行程內測不出來（pytest 已把 repo 根目錄放進 sys.path），故開子行程。
    """
    code = (
        "import runpy, sys\n"
        f"sys.path[0] = {str(ROOT / 'scripts')!r}\n"
        f"runpy.run_path({str(ROOT / 'scripts' / 'daily_scan.py')!r}, run_name='probe')\n"
        "from indicators.chip import chip_tier\n"
        "from indicators.broker import main_force_score\n"
    )
    r = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]


def _res(sid, price_date, chip_date):
    return {'id': sid, 'price_date': price_date,
            'chip': {'date': chip_date, '合計': 1} if chip_date else {}}


def test_freshness_rejects_previous_day_prices():
    # 20261023 案例：K 棒全停在前一日
    res = {'A': [_res(str(i), '2026-10-22', '2026-10-22') for i in range(10)]}
    ok, _ = ds['check_freshness']('2026-10-23', res)
    assert not ok


def test_freshness_rejects_stale_chips():
    # 20261104 案例：K 棒已更新、法人表尚未（77/105 停在前一日）
    res = {'A': [_res(str(i), '2026-11-04', '2026-11-04' if i < 3 else '2026-11-03')
                 for i in range(10)]}
    ok, msg = ds['check_freshness']('2026-11-04', res)
    assert not ok and '法人為當日 3/10' in msg


def test_freshness_tolerates_few_laggards_and_dedupes():
    stocks = [_res(str(i), '2026-11-05', '2026-11-05') for i in range(9)]
    stocks.append(_res('9', '2026-11-04', '2026-11-04'))   # 停牌一檔
    res = {'A': stocks, 'B': [stocks[0], {'id': 'x', 'error': 'boom'}]}
    ok, msg = ds['check_freshness']('2026-11-05', res)
    assert ok and '9/10' in msg


def test_drop_stale_chips_only_clears_old_dates():
    res = {'A': [_res('1', '2026-11-05', '2026-11-05'),
                 dict(_res('2', '2026-11-05', '2026-11-04'), chip_tier='strong')]}
    assert ds['drop_stale_chips']('2026-11-05', res) == 1
    assert res['A'][0]['chip'] and res['A'][1]['chip'] == {}
    assert res['A'][1]['chip_tier'] == 'neutral'


def _row(sid):
    return {'id': sid, 'name': sid, 'price': 100.0, 'rsi': 50.0, 'rsi10': 50.0,
            'ret_20d': 0.01, 'signal': 'HOLD', 'cv_sharpe': 0.1, 'cv_win_rate': 0.3,
            'cv_max_dd': 0.1, 'ma10': 100.0, 'chip': {}}


def test_coverage_counts_stocks_not_sectors():
    # 每族群都有一檔成功，舊算法（以族群計）會是 100%
    res = {'A': [_row('1'), {'id': '2', 'name': '2', 'error': 'x'}],
           'B': [_row('3'), {'id': '4', 'name': '4', 'error': 'x'}],
           'C': [_row('1')]}   # 跨族群重複只計一次
    s = ds['build_summary']('20261111', {}, res, None)
    assert s['scan_coverage'] == 50
    assert s['partial_sectors'] == {'A': 1, 'B': 1}


def test_coverage_zero_when_all_failed():
    res = {'A': [{'id': '1', 'name': '1', 'error': 'x'}]}
    assert ds['build_summary']('20261111', {}, res, None)['scan_coverage'] == 0
