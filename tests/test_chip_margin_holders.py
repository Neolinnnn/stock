"""融資融券 parse_margin / 千張大戶 parse_major_holders 測試。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from indicators.chip import parse_margin, parse_major_holders, _level_lower_bound


# ── parse_margin ─────────────────────────────────────────────────────────────

def _margin_df(n=7, start_bal=1000, step=10, short_bal=50):
    """n 個交易日的融資融券表，融資餘額每日 +step。"""
    rows = []
    for i in range(n):
        bal = start_bal + i * step
        rows.append({
            'date': f'2026-08-{10 + i:02d}',
            'MarginPurchaseTodayBalance': bal,
            'MarginPurchaseYesterdayBalance': bal - step,
            'ShortSaleTodayBalance': short_bal,
            'ShortSaleYesterdayBalance': short_bal - 5,
        })
    return pd.DataFrame(rows)


def test_margin_basic():
    """最新餘額、今日增減、券資比正確"""
    m = parse_margin(_margin_df())
    assert m['融資餘額'] == 1060          # 1000 + 6*10
    assert m['融資增減'] == 10
    assert m['融券餘額'] == 50
    assert m['融券增減'] == 5
    assert m['券資比'] == round(50 / 1060 * 100, 2)
    assert m['date'] == '2026-08-16'


def test_margin_5d_change():
    """近5日增減＝最新餘額 − 5個交易日前餘額"""
    m = parse_margin(_margin_df(n=7))
    assert m['融資5日增減'] == 50          # 1060 - 1010


def test_margin_5d_falls_back_to_earliest():
    """不足 6 筆時，近5日增減退回與最早一筆相減"""
    m = parse_margin(_margin_df(n=3))
    assert m['融資5日增減'] == 20          # 1020 - 1000


def test_margin_unsorted_input():
    """輸入未排序仍以日期最新一筆為準"""
    df = _margin_df().iloc[::-1].reset_index(drop=True)
    assert parse_margin(df)['融資餘額'] == 1060


def test_margin_empty():
    """空資料 → {}"""
    assert parse_margin(pd.DataFrame()) == {}
    assert parse_margin(None) == {}


def test_margin_missing_balance_column():
    """缺融資餘額欄位 → {}"""
    df = pd.DataFrame([{'date': '2026-08-16', 'ShortSaleTodayBalance': 10}])
    assert parse_margin(df) == {}


def test_margin_zero_balance_ratio_none():
    """融資餘額為 0 時券資比回 None，不除零"""
    df = pd.DataFrame([{
        'date': '2026-08-16',
        'MarginPurchaseTodayBalance': 0,
        'MarginPurchaseYesterdayBalance': 0,
        'ShortSaleTodayBalance': 0,
        'ShortSaleYesterdayBalance': 0,
    }])
    assert parse_margin(df)['券資比'] is None


# ── _level_lower_bound ───────────────────────────────────────────────────────

def test_level_lower_bound():
    assert _level_lower_bound('1-999') == 1
    assert _level_lower_bound('1,000-5,000') == 1000
    assert _level_lower_bound('800,001-1,000,000') == 800001
    assert _level_lower_bound('more than 1,000,001') == 1000001
    assert _level_lower_bound('total') is None
    assert _level_lower_bound('合計') is None


# ── parse_major_holders ──────────────────────────────────────────────────────

def _holders_df(pcts):
    """pcts: 各週千張大戶比例；每週另附一筆小額分級與 total 作干擾。"""
    rows = []
    for i, p in enumerate(pcts):
        date = f'2026-0{7 + i // 4}-{(i % 4) * 7 + 1:02d}'
        rows += [
            {'date': date, 'HoldingSharesLevel': '1-999', 'percent': 5.0},
            {'date': date, 'HoldingSharesLevel': '800,001-1,000,000', 'percent': 3.0},
            {'date': date, 'HoldingSharesLevel': 'more than 1,000,001', 'percent': p},
            {'date': date, 'HoldingSharesLevel': 'total', 'percent': 100.0},
        ]
    return pd.DataFrame(rows)


def test_major_holders_basic():
    """只加總千張以上分級，排除 800,001-1,000,000 與 total"""
    h = parse_major_holders(_holders_df([60.0, 61.5]))
    assert h['千張大戶比例'] == 61.5
    assert h['週增減'] == 1.5


def test_major_holders_4w_change():
    """四週增減＝最新 − 5 週前（共 5 個統計日）"""
    h = parse_major_holders(_holders_df([60.0, 60.5, 61.0, 61.5, 62.0]))
    assert h['千張大戶比例'] == 62.0
    assert h['週增減'] == 0.5
    assert h['四週增減'] == 2.0


def test_major_holders_declining():
    """大戶減碼 → 增減為負"""
    h = parse_major_holders(_holders_df([62.0, 60.0]))
    assert h['週增減'] == -2.0


def test_major_holders_single_week():
    """只有一週資料 → 比例仍給值，增減為 None"""
    h = parse_major_holders(_holders_df([60.0]))
    assert h['千張大戶比例'] == 60.0
    assert h['週增減'] is None
    assert h['四週增減'] is None


def test_major_holders_sums_multiple_big_levels():
    """千張以上有多個分級時全部加總"""
    df = pd.DataFrame([
        {'date': '2026-08-01', 'HoldingSharesLevel': 'more than 1,000,001', 'percent': 40.0},
        {'date': '2026-08-01', 'HoldingSharesLevel': '1,000,001-5,000,000', 'percent': 20.0},
        {'date': '2026-08-01', 'HoldingSharesLevel': 'total', 'percent': 100.0},
    ])
    assert parse_major_holders(df)['千張大戶比例'] == 60.0


def test_major_holders_empty_and_missing_columns():
    """空資料或缺欄位 → {}"""
    assert parse_major_holders(pd.DataFrame()) == {}
    assert parse_major_holders(None) == {}
    assert parse_major_holders(pd.DataFrame([{'date': '2026-08-01'}])) == {}


def test_major_holders_no_big_level():
    """完全沒有千張以上分級 → {}"""
    df = pd.DataFrame([
        {'date': '2026-08-01', 'HoldingSharesLevel': '1-999', 'percent': 5.0},
        {'date': '2026-08-01', 'HoldingSharesLevel': 'total', 'percent': 100.0},
    ])
    assert parse_major_holders(df) == {}
