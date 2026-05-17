"""
基本面資料抓取模組
每日掃描後執行，輸出 docs/fundamentals/{stock_id}.json
"""
import json
import math
import os
import time
from datetime import datetime, timedelta
from pathlib import Path


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def _pct_change(new, old):
    """計算百分比變化，old 為 0 或 None 時回傳 None。"""
    if old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100, 2)


def _round2(v):
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 2)


# ── 月營收解析 ────────────────────────────────────────────────────────────────

def parse_revenue(df):
    """
    輸入：FinMind taiwan_stock_month_revenue DataFrame
    輸出：{month, revenue, mom, yoy, cum_yoy} 平行陣列 dict
    """
    if df is None or df.empty:
        return None

    df = df.sort_values(['revenue_year', 'revenue_month']).reset_index(drop=True)

    months  = [f"{int(r.revenue_year)}{int(r.revenue_month):02d}" for _, r in df.iterrows()]
    revs    = [int(r.revenue) for _, r in df.iterrows()]
    n       = len(revs)

    # MoM
    mom = [None] + [_pct_change(revs[i], revs[i-1]) for i in range(1, n)]

    # YoY（需要 12 期前的資料）
    yoy = [None] * n
    for i in range(12, n):
        yoy[i] = _pct_change(revs[i], revs[i-12])

    # 累計 YoY：同年累計 vs 前年同期累計
    cum_yoy = [None] * n
    year_of = [int(m[:4]) for m in months]
    month_of = [int(m[4:]) for m in months]
    for i in range(n):
        y, m = year_of[i], month_of[i]
        cur_cum = sum(
            revs[j] for j in range(n)
            if year_of[j] == y and month_of[j] <= m
        )
        prev_cum = sum(
            revs[j] for j in range(n)
            if year_of[j] == y - 1 and month_of[j] <= m
        )
        if prev_cum > 0:
            cum_yoy[i] = _pct_change(cur_cum, prev_cum)

    return {
        'month':    months,
        'revenue':  revs,
        'mom':      mom,
        'yoy':      yoy,
        'cum_yoy':  cum_yoy,
    }


# ── 財務報表解析 ──────────────────────────────────────────────────────────────

def _date_to_quarter(date_str):
    """'2023-03-31' → '2023Q1'"""
    d = datetime.strptime(date_str[:10], '%Y-%m-%d')
    q = (d.month - 1) // 3 + 1
    return f"{d.year}Q{q}"


def parse_financials(df):
    """
    輸入：FinMind taiwan_stock_financial_statement DataFrame
    輸出：(eps_dict, margins_dict) 平行陣列 dict 的 tuple
          任一資料缺失時回傳 (None, None)
    """
    if df is None or df.empty:
        return None, None

    needed = {'EPS', 'GrossProfit', 'OperatingIncome', 'Revenue',
              'EquityAttributableToOwnersOfParent'}
    available = set(df['type'].unique())
    if not needed.issubset(available):
        return None, None

    df = df[df['type'].isin(needed)].copy()
    pivot = df.pivot_table(index='date', columns='type', values='value', aggfunc='first')
    pivot = pivot.sort_index()

    quarters = [_date_to_quarter(d) for d in pivot.index]
    n = len(quarters)

    eps_vals  = [_round2(pivot.loc[d, 'EPS']) for d in pivot.index]
    rev_vals  = [float(pivot.loc[d, 'Revenue']) for d in pivot.index]
    gp_vals   = [float(pivot.loc[d, 'GrossProfit']) for d in pivot.index]
    oi_vals   = [float(pivot.loc[d, 'OperatingIncome']) for d in pivot.index]
    ni_vals   = [float(pivot.loc[d, 'EquityAttributableToOwnersOfParent']) for d in pivot.index]

    # QoQ / YoY for EPS
    eps_qoq = [None] + [_pct_change(eps_vals[i], eps_vals[i-1]) for i in range(1, n)]
    eps_yoy = [None] * n
    for i in range(4, n):
        eps_yoy[i] = _pct_change(eps_vals[i], eps_vals[i-4])

    # 三率 = 各項 / Revenue * 100
    def to_margin(vals):
        return [_round2(v / r * 100) if r and r != 0 else None
                for v, r in zip(vals, rev_vals)]

    eps_dict = {
        'quarter': quarters,
        'eps':     eps_vals,
        'qoq':     eps_qoq,
        'yoy':     eps_yoy,
    }
    margins_dict = {
        'quarter':           quarters,
        'gross_margin':      to_margin(gp_vals),
        'operating_margin':  to_margin(oi_vals),
        'net_margin':        to_margin(ni_vals),
    }
    return eps_dict, margins_dict
