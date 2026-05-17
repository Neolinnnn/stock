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
