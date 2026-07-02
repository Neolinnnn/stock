"""籌碼分層 chip_tier 測試：集中度/土洋同買/主力分數 三因子與資料缺漏降級。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from indicators.chip import chip_tier


def test_strong_by_concentration():
    """集中度 >=+10% + 主力分數>=55 → strong（不需土洋同買）"""
    assert chip_tier(10.0, False, 55) == 'strong'
    assert chip_tier(25.0, False, 80) == 'strong'


def test_strong_by_dual_buy():
    """土洋同買時集中度門檻放寬到 +5%"""
    assert chip_tier(5.0, True, 55) == 'strong'
    assert chip_tier(8.0, True, 60) == 'strong'


def test_not_strong_below_threshold():
    """集中度 5~10% 且無土洋同買 → neutral"""
    assert chip_tier(8.0, False, 60) == 'neutral'
    assert chip_tier(4.9, True, 60) == 'neutral'


def test_not_strong_weak_main_force():
    """集中度夠但主力分數 <55 → neutral"""
    assert chip_tier(15.0, True, 54.9) == 'neutral'


def test_weak():
    """集中度 <=-10% + 主力分數<45 → weak"""
    assert chip_tier(-10.0, False, 44) == 'weak'
    assert chip_tier(-30.0, False, 0) == 'weak'


def test_not_weak():
    """賣壓但主力分數不弱、或集中度未達 -10% → neutral"""
    assert chip_tier(-15.0, False, 45) == 'neutral'
    assert chip_tier(-9.9, False, 0) == 'neutral'


def test_neutral_missing_data():
    """任一資料缺漏 → 降級 neutral，不擋訊號"""
    assert chip_tier(None, True, 60) == 'neutral'
    assert chip_tier(15.0, True, None) == 'neutral'
    assert chip_tier(None, False, None) == 'neutral'
