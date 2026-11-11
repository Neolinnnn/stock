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
