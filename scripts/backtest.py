"""
回測系統：9 種停利/停損組合勝率分析
用法：python scripts/backtest.py [--no-backfill] [--no-notion]
"""
import sys, os, json, subprocess, argparse
from pathlib import Path
from datetime import datetime

# TP / SL 組合
TP_LIST = [0.10, 0.12, 0.15]
SL_LIST = [0.05, 0.10, 0.12]
BACKTEST_START = '20260301'


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def calc_next_trading_day(date_str: str, trading_days: list) -> str | None:
    """取 date_str 之後的第一個交易日；若 date_str 不在清單也從清單找第一個比它大的。"""
    for d in trading_days:
        if d > date_str:
            return d
    return None


def simulate_position(
    entry_date: str,
    entry_price: float,
    amount: float,
    prices: dict,          # {date_str: close_price}
    trading_days: list,
    tp: float,
    sl: float,
) -> dict:
    """
    模擬單筆持倉，逐日用收盤價判斷 TP/SL。
    回傳 {'result': WIN/LOSS/OPEN, 'exit_date', 'exit_price', 'return_pct', 'holding_days'}
    """
    tp_price = entry_price * (1 + tp)
    sl_price = entry_price * (1 - sl)

    holding = 0
    for d in trading_days:
        if d <= entry_date:
            continue
        close = prices.get(d)
        if close is None:
            continue
        holding += 1
        if close >= tp_price:
            ret = (close - entry_price) / entry_price * 100
            return {'result': 'WIN', 'exit_date': d, 'exit_price': close,
                    'return_pct': round(ret, 2), 'holding_days': holding,
                    'entry_date': entry_date, 'entry_price': entry_price, 'amount': amount}
        if close <= sl_price:
            ret = (close - entry_price) / entry_price * 100
            return {'result': 'LOSS', 'exit_date': d, 'exit_price': close,
                    'return_pct': round(ret, 2), 'holding_days': holding,
                    'entry_date': entry_date, 'entry_price': entry_price, 'amount': amount}

    return {'result': 'OPEN', 'exit_date': None, 'exit_price': None,
            'return_pct': None, 'holding_days': None,
            'entry_date': entry_date, 'entry_price': entry_price, 'amount': amount}


def calc_stats(trades: list) -> dict:
    """計算已出場交易的勝率、平均報酬、平均持有天數。OPEN 不計入。"""
    closed = [t for t in trades if t['result'] != 'OPEN']
    wins   = [t for t in closed if t['result'] == 'WIN']
    losses = [t for t in closed if t['result'] == 'LOSS']
    total  = len(closed)

    win_rate   = len(wins) / total if total else 0
    avg_return = sum(t['return_pct'] for t in closed) / total if total else 0
    avg_hold   = sum(t['holding_days'] for t in closed) / total if total else 0

    return {
        'total': total,
        'wins': len(wins),
        'losses': len(losses),
        'open_count': len(trades) - total,
        'win_rate': round(win_rate, 4),
        'avg_return': round(avg_return, 4),
        'avg_holding_days': round(avg_hold, 1),
    }
