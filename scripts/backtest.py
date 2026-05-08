"""
回測系統：9 種停利/停損組合勝率分析
用法：python scripts/backtest.py [--no-backfill] [--no-notion]
"""
import sys, os, json, subprocess, argparse, math
import pandas as pd
from pathlib import Path
from datetime import datetime

# TP / SL 組合
TP_LIST = [0.10, 0.12, 0.15]
SL_LIST = [0.05, 0.10, 0.12]
BACKTEST_START = '20260301'


# ── 工具函式 ──────────────────────────────────────────────────────────────────

def calc_next_trading_day(date_str: str, trading_days: list[str]) -> str | None:
    """取 date_str 之後的第一個交易日；若 date_str 不在清單也從清單找第一個比它大的。"""
    trading_days = sorted(trading_days)
    for d in trading_days:
        if d > date_str:
            return d
    return None


def simulate_position(
    entry_date: str,
    entry_price: float,
    amount: float,
    prices: dict,          # {date_str: close_price}
    trading_days: list[str],
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


# ── 信號收集 ──────────────────────────────────────────────────────────────────

def load_buy_signals(reports_dir: str = 'daily_reports',
                     start_date: str = BACKTEST_START) -> list[dict]:
    """
    讀所有 daily_reports/*/summary.json，回傳 BUY 訊號清單（已去重）。
    回傳格式：[{'date', 'stock_id', 'stock_name', 'signal_close'}, ...]
    """
    signals = []
    seen = set()   # (date, stock_id)

    p = Path(reports_dir)
    if not p.is_dir():
        print(f"  ⚠️  找不到報告目錄：{reports_dir}")
        return []

    for date_dir in sorted(p.iterdir()):
        if not date_dir.is_dir():
            continue
        date_str = date_dir.name
        if len(date_str) != 8 or not date_str.isdigit():
            continue
        if date_str < start_date:
            continue
        summary_file = date_dir / 'summary.json'
        if not summary_file.exists():
            continue

        try:
            summary = json.loads(summary_file.read_text(encoding='utf-8'))
        except Exception as e:
            print(f"  ⚠️  {date_str} 讀取失敗：{e}")
            continue

        for sector_data in summary.get('sectors', {}).values():
            for stock in sector_data.get('stocks', []):
                if stock.get('signal') != 'BUY':
                    continue
                stock_id = stock.get('id')
                stock_name = stock.get('name', '')
                signal_close = stock.get('price')
                if not stock_id or signal_close is None:
                    continue
                key = (date_str, stock_id)
                if key in seen:
                    continue
                seen.add(key)
                signals.append({
                    'date': date_str,
                    'stock_id': stock_id,
                    'stock_name': stock_name,
                    'signal_close': signal_close,
                })

    print(f"  📋 共收集 {len(signals)} 筆 BUY 訊號（{start_date} 起）")
    return signals


def apply_position_limits(
    signals: list[dict],
    per_trade: float = 3000,
    max_per_stock: float = 10000,
) -> list[dict]:
    """
    為每筆訊號計算實際投入金額（考慮累計上限）。
    回傳含 'amount' 欄位的訊號清單；累積已達上限的個股跳過。
    """
    stock_invested: dict[str, float] = {}
    result = []
    for sig in signals:
        sid = sig['stock_id']
        invested = stock_invested.get(sid, 0.0)
        remaining = max_per_stock - invested
        if remaining <= 0:
            continue
        amount = min(per_trade, remaining)
        stock_invested[sid] = invested + amount
        result.append({**sig, 'amount': amount})
    return result


# ── 價格抓取 ──────────────────────────────────────────────────────────────────

SECTORS_ALL_IDS = [
    '4979','3450','3665','3105','8086','2455','4906','2345',  # 光通訊
    '2408','2337','2344','3006','2451','5289','3205',          # 記憶體
    '2317','2382','3231','2376','4938','2357',                 # AI伺服器
    '3711','2449','6510','2441','6257','6239',                 # 封測
    '3008','3406',                                            # 光學
    '2454','3034','4966','4919','2388',                       # IC設計
    '3552','1533','2243',                                     # 車用電子
    '5292',                                                   # 綠能環保
]


def fetch_price_data(dl, stock_ids: list[str],
                     start: str, end: str) -> dict:
    """
    從 FinMind 批次下載 OHLC，回傳：
    {stock_id: {date_str: {'open': float, 'close': float}}}
    start/end 格式：'YYYY-MM-DD'
    """
    print(f"  📥 下載開盤/收盤價：{start} ~ {end}（{len(stock_ids)} 檔）")
    price_data: dict[str, dict] = {}
    for sid in stock_ids:
        try:
            df = dl.taiwan_stock_daily(stock_id=sid, start_date=start, end_date=end)
            if df is None or df.empty:
                price_data[sid] = {}
                continue
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
            price_data[sid] = {
                row['date']: {'open': float(row['open']), 'close': float(row['close'])}
                for _, row in df.iterrows()
            }
        except Exception as e:
            print(f"    ⚠️  {sid} 下載失敗：{e}")
            price_data[sid] = {}
    return price_data


def get_sorted_trading_days(price_data: dict) -> list[str]:
    """從價格資料中萃取所有交易日（排序）。"""
    days = set()
    for stock_prices in price_data.values():
        days.update(stock_prices.keys())
    return sorted(days)


# ── 完整回測 ──────────────────────────────────────────────────────────────────

def run_backtest_combo(
    signals_with_amount: list[dict],
    price_data: dict,
    trading_days: list[str],
    tp: float,
    sl: float,
) -> dict:
    """
    執行單一 (TP, SL) 組合的完整回測。
    回傳 {'stats': {...}, 'trades': [...]}
    """
    trades = []
    for sig in signals_with_amount:
        sid = sig['stock_id']
        signal_date = sig['date']
        amount = sig['amount']

        # 找進場日（訊號日的下一交易日）
        entry_date = calc_next_trading_day(signal_date, trading_days)
        if entry_date is None:
            continue

        # 進場價（開盤價）
        stock_prices = price_data.get(sid, {})
        entry_info = stock_prices.get(entry_date)
        if not entry_info:
            print(f"    ⚠️  {sid} {entry_date} 開盤價缺失，跳過")
            continue
        entry_price = entry_info['open']
        if entry_price <= 0 or math.isnan(entry_price):
            continue

        # 收集進場日之後的收盤價序列
        close_prices = {
            d: v['close'] for d, v in stock_prices.items() if d > entry_date
        }

        trade = simulate_position(
            entry_date=entry_date,
            entry_price=entry_price,
            amount=amount,
            prices=close_prices,
            trading_days=[d for d in trading_days if d >= entry_date],
            tp=tp, sl=sl,
        )
        trade['stock_id'] = sid
        trade['stock_name'] = sig['stock_name']
        trade['signal_date'] = signal_date
        trade['tp'] = tp
        trade['sl'] = sl
        trades.append(trade)

    return {'stats': calc_stats(trades), 'trades': trades}


def run_all_backtests(
    signals_with_amount: list[dict],
    price_data: dict,
    trading_days: list[str],
) -> dict:
    """執行全部 9 組 TP×SL 組合，回傳結果字典。"""
    combinations = {}
    for tp in TP_LIST:
        for sl in SL_LIST:
            key = f"TP{int(tp*100)}_SL{int(sl*100)}"
            print(f"  🔄 回測 {key} ...")
            result = run_backtest_combo(signals_with_amount, price_data, trading_days, tp, sl)
            combinations[key] = result
            s = result['stats']
            print(f"      勝率 {s['win_rate']:.1%}  交易數 {s['total']}  "
                  f"未實現 {s['open_count']}  avg報酬 {s['avg_return']:+.1f}%")
    return combinations
