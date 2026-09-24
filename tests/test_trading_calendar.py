"""交易日曆：當週最後交易日判斷（不連網，以固定休市日驗證）。"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
from trading_calendar import is_last_trading_day_of_week, is_trading_day

# 2026 年實例：9/25（五）中秋；2/12~2/20 春節（含無交易結算日）
HOL = {date(2026, 9, 25), date(2026, 9, 28)} | {date(2026, 2, d) for d in range(12, 21)}


def test_friday_normal_week():
    assert is_last_trading_day_of_week(date(2026, 9, 18), HOL)


def test_thursday_when_friday_is_holiday():
    assert is_last_trading_day_of_week(date(2026, 9, 24), HOL)
    assert not is_last_trading_day_of_week(date(2026, 9, 23), HOL)


def test_holiday_itself_is_not_last_trading_day():
    assert not is_trading_day(date(2026, 9, 25), HOL)
    assert not is_last_trading_day_of_week(date(2026, 9, 25), HOL)


def test_wednesday_before_lunar_new_year():
    assert is_last_trading_day_of_week(date(2026, 2, 11), HOL)


def test_fallback_without_calendar_is_friday():
    assert is_last_trading_day_of_week(date(2026, 9, 25), None)
    assert not is_last_trading_day_of_week(date(2026, 9, 24), None)
