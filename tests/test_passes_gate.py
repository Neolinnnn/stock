"""進場閘門 passes_gate 測試：基本多頭排列 + 族群強勢 + 乖離率閘門。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from position_tracker import passes_gate


def _stock(**kw):
    """多頭排列、貼均線的基準個股，可用 kw 覆寫欄位。"""
    base = {'signal': 'BUY', 'price': 101.0,
            'ma5': 100.0, 'ma10': 100.0, 'ma20': 98.0, 'ma60': 95.0}
    base.update(kw)
    return base


def test_pass_basic_gate():
    """大盤多頭 + BUY + 多頭排列 → 通過（未啟用新閘門）"""
    assert passes_gate(_stock(), taiex_bull=True) is True


def test_reject_taiex_bear():
    """大盤空頭 → 擋下"""
    assert passes_gate(_stock(), taiex_bull=False) is False


def test_reject_not_buy():
    """非 BUY 訊號 → 擋下"""
    assert passes_gate(_stock(signal='HOLD'), taiex_bull=True) is False


def test_reject_not_ma_stack():
    """非多頭排列（收盤 < MA5）→ 擋下"""
    assert passes_gate(_stock(price=99.0), taiex_bull=True) is False


def test_reject_weak_sector():
    """族群非強勢 → 擋下"""
    assert passes_gate(_stock(), taiex_bull=True, sector_strong=False) is False


def test_pass_strong_sector():
    """族群強勢 → 通過"""
    assert passes_gate(_stock(), taiex_bull=True, sector_strong=True) is True


def test_reject_chasing_high():
    """乖離 MA10 超過上限（追高）→ 擋下。price=106, ma10=100 → 乖離 +6% > 2%"""
    assert passes_gate(_stock(price=106.0, ma5=104.0), taiex_bull=True,
                       max_bias_ma10=2.0) is False


def test_pass_near_ma10():
    """乖離 MA10 在上限內（貼均線）→ 通過。price=101, ma10=100 → 乖離 +1% ≤ 2%"""
    assert passes_gate(_stock(), taiex_bull=True, max_bias_ma10=2.0) is True


def test_reject_missing_ma10_when_bias_checked():
    """要檢查乖離但缺 ma10 → 保守擋下"""
    s = _stock(); s['ma10'] = None
    assert passes_gate(s, taiex_bull=True, max_bias_ma10=2.0) is False


def test_combined_gate():
    """族群強勢 + 貼均線同時成立 → 通過；任一不成立 → 擋下"""
    assert passes_gate(_stock(), taiex_bull=True,
                       sector_strong=True, max_bias_ma10=2.0) is True
    assert passes_gate(_stock(price=106.0, ma5=104.0), taiex_bull=True,
                       sector_strong=True, max_bias_ma10=2.0) is False
    assert passes_gate(_stock(), taiex_bull=True,
                       sector_strong=False, max_bias_ma10=2.0) is False
