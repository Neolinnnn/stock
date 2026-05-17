import pandas as pd
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from fundamentals_fetcher import parse_revenue


def _make_revenue_df():
    """模擬 FinMind taiwan_stock_month_revenue 回傳格式。"""
    return pd.DataFrame({
        'date':          ['2024-02-01', '2024-03-01', '2024-04-01',
                          '2024-05-01', '2024-06-01'],
        'stock_id':      ['2330'] * 5,
        'revenue':       [100, 120, 90, 108, 130],   # 單位任意
        'revenue_month': [1, 2, 3, 4, 5],
        'revenue_year':  [2024] * 5,
        'country':       ['Taiwan'] * 5,
        'create_time':   [''] * 5,
    })


def test_parse_revenue_keys():
    result = parse_revenue(_make_revenue_df())
    assert set(result.keys()) == {'month', 'revenue', 'mom', 'yoy', 'cum_yoy'}


def test_parse_revenue_month_format():
    result = parse_revenue(_make_revenue_df())
    # YYYYMM 格式，最舊到最新
    assert result['month'][0] == '202401'
    assert result['month'][-1] == '202405'


def test_parse_revenue_mom():
    result = parse_revenue(_make_revenue_df())
    # 202401→202402: (120-100)/100*100 = 20.0
    assert abs(result['mom'][1] - 20.0) < 0.1
    # 第一筆沒有前期，為 None
    assert result['mom'][0] is None


def test_parse_revenue_yoy_none_when_insufficient():
    """不足 12 期時，YoY 應為 None。"""
    result = parse_revenue(_make_revenue_df())
    # 只有 5 個月，YoY 全部應為 None
    assert all(v is None for v in result['yoy'])
