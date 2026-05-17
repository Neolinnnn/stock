import pandas as pd
import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from fundamentals_fetcher import parse_revenue, parse_financials


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


def test_parse_revenue_cum_yoy_with_cross_year_data():
    """cum_yoy 跨年資料時應有數值。"""
    df = pd.DataFrame({
        'date':          ['2023-02-01', '2023-03-01',
                          '2024-02-01', '2024-03-01'],
        'stock_id':      ['2330'] * 4,
        'revenue':       [100, 110, 120, 143],
        'revenue_month': [1, 2, 1, 2],
        'revenue_year':  [2023, 2023, 2024, 2024],
        'country':       ['Taiwan'] * 4,
        'create_time':   [''] * 4,
    })
    result = parse_revenue(df)
    # 2024 年第 1 筆（202401）: cum_yoy = (120-100)/100*100 = 20.0
    idx_2024_01 = result['month'].index('202401')
    assert result['cum_yoy'][idx_2024_01] is not None
    assert abs(result['cum_yoy'][idx_2024_01] - 20.0) < 0.1


# ── 財務報表解析測試 ─────────────────────────────────────────────────────────


def _make_financial_df():
    """模擬 FinMind taiwan_stock_financial_statement 回傳格式。"""
    rows = []
    quarters = ['2023-03-31', '2023-06-30', '2023-09-30', '2023-12-31',
                '2024-03-31', '2024-06-30', '2024-09-30', '2024-12-31']
    data = {
        'EPS':             [7.98, 7.01, 8.14, 9.21, 8.70, 9.56, 12.54, 14.45],
        'GrossProfit':     [286e9, 260e9, 296e9, 331e9, 302e9, 332e9, 390e9, 440e9],
        'OperatingIncome': [231e9, 201e9, 228e9, 260e9, 250e9, 275e9, 330e9, 380e9],
        'Revenue':         [508e9, 480e9, 546e9, 625e9, 592e9, 673e9, 759e9, 868e9],
        'EquityAttributableToOwnersOfParent': [206e9, 181e9, 210e9, 238e9, 225e9, 247e9, 325e9, 374e9],
    }
    for q in quarters:
        for t, vals in data.items():
            idx = quarters.index(q)
            rows.append({'date': q, 'stock_id': '2330', 'type': t,
                         'value': vals[idx], 'origin_name': ''})
    return pd.DataFrame(rows)


def test_parse_financials_keys():
    eps_result, margins_result = parse_financials(_make_financial_df())
    assert set(eps_result.keys()) == {'quarter', 'eps', 'qoq', 'yoy'}
    assert set(margins_result.keys()) == {'quarter', 'gross_margin', 'operating_margin', 'net_margin'}


def test_parse_financials_quarter_label():
    eps_result, _ = parse_financials(_make_financial_df())
    assert eps_result['quarter'][0] == '2023Q1'
    assert eps_result['quarter'][-1] == '2024Q4'


def test_parse_financials_eps_values():
    eps_result, _ = parse_financials(_make_financial_df())
    assert eps_result['eps'][0] == 7.98
    assert eps_result['eps'][-1] == 14.45


def test_parse_financials_gross_margin():
    _, margins_result = parse_financials(_make_financial_df())
    # GrossProfit / Revenue * 100 for Q1 2023: 286/508*100 ≈ 56.3
    assert abs(margins_result['gross_margin'][0] - 56.3) < 0.5


def test_parse_financials_qoq_first_is_none():
    eps_result, _ = parse_financials(_make_financial_df())
    assert eps_result['qoq'][0] is None


def test_parse_financials_yoy_first_four_none():
    eps_result, _ = parse_financials(_make_financial_df())
    # 前 4 季沒有去年同期
    assert all(v is None for v in eps_result['yoy'][:4])
    # 第 5 季（2024Q1）應有 YoY: (8.70-7.98)/7.98*100 ≈ 9.0
    assert eps_result['yoy'][4] is not None
