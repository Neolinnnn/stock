"""
回測系統：9 種停利/停損組合勝率分析（進階出場邏輯）
用法：python scripts/backtest.py [--no-backfill] [--no-notion]
"""
import sys, os, json, subprocess, argparse, math
import pandas as pd
from pathlib import Path
from datetime import datetime

# TP / SL 組合
TP_LIST = [0.15, 0.18, 0.20]
SL_LIST = [0.10, 0.12, 0.15]
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


def simulate_position_v2(
    entry_date: str,
    entry_price: float,
    amount: float,
    ohlc_prices: dict,       # {date_str: {'open','high','low','close'}}
    trading_days: list[str],
    tp: float,
    sl: float,
) -> dict:
    """
    追蹤止損出場邏輯（v2，已移除 20 天強制出場）：
    - 追蹤止損：每日更新最高收盤（high_watermark），
      trailing_sl = high_watermark × (1 − sl)，只升不降。
      收盤 <= trailing_sl → 出場（高於進場價=WIN，否則=LOSS）
    - TP：收盤觸及停利
        • 若當日 high > prev_close×1.05（盤中漲逾5%）且收盤仍強
          → PULLBACK 模式（等回吐 open×0.97 再出）
        • 否則 → 隔日開盤出場
    - PULLBACK 模式中追蹤止損同步更新，兩者皆可觸發出場
    """
    tp_price      = entry_price * (1 + tp)
    high_watermark = entry_price
    trailing_sl   = entry_price * (1 - sl)

    state      = 'HOLDING'   # 'HOLDING' | 'SELL_OPEN' | 'PULLBACK'
    prev_close = None
    holding    = 0

    active_days = [d for d in trading_days if d > entry_date]

    for d in active_days:
        px = ohlc_prices.get(d)
        if not px:
            continue
        o = px.get('open', 0)
        h = px.get('high', 0)
        l = px.get('low',  0)
        c = px.get('close', 0)
        if not o or not c or math.isnan(o) or math.isnan(c):
            if c:
                prev_close = c
            continue

        holding += 1

        def _ret(price):
            return round((price - entry_price) / entry_price * 100, 2)

        def _rec(result, price, hdays=holding):
            return {'result': result, 'exit_date': d, 'exit_price': round(price, 2),
                    'return_pct': _ret(price), 'holding_days': hdays,
                    'entry_date': entry_date, 'entry_price': entry_price, 'amount': amount}

        # ── 待售：隔日開盤賣 ──
        if state == 'SELL_OPEN':
            return _rec('WIN', o)

        # ── 更新追蹤止損 ──
        if c > high_watermark:
            high_watermark = c
            trailing_sl    = high_watermark * (1 - sl)

        # ── 回吐等待（TP 後追蹤模式） ──
        if state == 'PULLBACK':
            target = o * 0.97
            if l <= target:
                return _rec('WIN', target)
            if c <= trailing_sl:
                return _rec('WIN' if c > entry_price else 'LOSS', c)
            prev_close = c
            continue

        # ── HOLDING：追蹤止損 → TP ──
        if c <= trailing_sl:
            return _rec('WIN' if c > entry_price else 'LOSS', c)

        if c >= tp_price:
            intraday_strong = (prev_close is not None and h > 0
                               and (h - prev_close) / prev_close > 0.05)
            close_still_strong = (prev_close is not None
                                  and (c - prev_close) / prev_close >= 0.05)
            state = 'PULLBACK' if (intraday_strong and close_still_strong) else 'SELL_OPEN'

        prev_close = c

    return {'result': 'OPEN', 'exit_date': None, 'exit_price': None,
            'return_pct': None, 'holding_days': holding,
            'exit_state': state,
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
                     start_date: str = BACKTEST_START,
                     qualified_only: bool = False) -> list[dict]:
    """
    讀所有 daily_reports/*/summary.json，回傳 BUY 訊號清單（已去重）。
    qualified_only=True 時額外要求 cv_sharpe>=0.3 & cv_win_rate>=0.4。
    回傳格式：[{'date', 'stock_id', 'stock_name', 'signal_close', 'cv_sharpe', 'cv_win_rate'}, ...]
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
                cv_sharpe = stock.get('cv_sharpe', 0) or 0
                cv_win_rate = stock.get('cv_win_rate', 0) or 0
                if not stock_id or signal_close is None:
                    continue
                if qualified_only and (cv_sharpe < 0.3 or cv_win_rate < 0.4):
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
                    'cv_sharpe': cv_sharpe,
                    'cv_win_rate': cv_win_rate,
                })

    label = '雙條件達標' if qualified_only else '全 BUY'
    print(f"  📋 {label}：共 {len(signals)} 筆訊號（{start_date} 起）")
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
    {stock_id: {date_str: {'open','high','low','close': float}}}
    start/end 格式：'YYYY-MM-DD'
    FinMind 欄名：max=high, min=low
    """
    print(f"  📥 下載 OHLC：{start} ~ {end}（{len(stock_ids)} 檔）")
    price_data: dict[str, dict] = {}
    for sid in stock_ids:
        try:
            df = dl.taiwan_stock_daily(stock_id=sid, start_date=start, end_date=end)
            if df is None or df.empty:
                price_data[sid] = {}
                continue
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
            price_data[sid] = {
                row['date']: {
                    'open' : float(row['open']),
                    'high' : float(row.get('max', row['open'])),
                    'low'  : float(row.get('min', row['close'])),
                    'close': float(row['close']),
                }
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

        # 收集進場日之後的 OHLC 序列
        ohlc_prices = {d: v for d, v in stock_prices.items() if d > entry_date}

        trade = simulate_position_v2(
            entry_date=entry_date,
            entry_price=entry_price,
            amount=amount,
            ohlc_prices=ohlc_prices,
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


# ── 資料補齊 ──────────────────────────────────────────────────────────────────

def ensure_backfill(start_date: str = BACKTEST_START):
    """
    呼叫 backfill_history.py 補充 start_date 至今缺失的掃描資料。
    """
    from datetime import date as dt_date
    today = dt_date.today()
    start = datetime.strptime(start_date, '%Y%m%d').date()
    days_back = (today - start).days + 10   # 多抓幾天保險

    backfill_script = Path(__file__).parent / 'backfill_history.py'
    print(f"  🔄 執行資料補齊（--days {days_back}）...")
    subprocess.run(
        [sys.executable, str(backfill_script), '--days', str(days_back)],
        check=True
    )


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='台股族群掃描回測')
    parser.add_argument('--no-backfill',     action='store_true', help='跳過資料補齊步驟')
    parser.add_argument('--no-notion',       action='store_true', help='跳過 Notion 上傳')
    parser.add_argument('--qualified-only',  action='store_true', help='只回測雙條件達標個股')
    parser.add_argument('--compare',         action='store_true', help='同時回測全BUY與雙條件，對比輸出')
    parser.add_argument('--start', default=BACKTEST_START, help='回測起始日 YYYYMMDD')
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  台股回測系統  起始日：{args.start}")
    print(f"{'='*60}\n")

    # Step 1: 補資料
    if not args.no_backfill:
        print("【Step 1】資料補齊")
        ensure_backfill(args.start)
    else:
        print("【Step 1】跳過資料補齊")

    # Step 2: 收集訊號
    print("\n【Step 2】收集 BUY 訊號")
    from datetime import date as dt_date

    signals_all  = load_buy_signals(start_date=args.start, qualified_only=False)
    signals_qual = load_buy_signals(start_date=args.start, qualified_only=True)

    swa_all  = apply_position_limits(signals_all)
    swa_qual = apply_position_limits(signals_qual)
    print(f"  全 BUY 投資上限後：{len(swa_all)} 筆　雙條件達標：{len(swa_qual)} 筆")

    if not swa_all:
        print("  ❌ 無 BUY 訊號，終止")
        return

    # Step 3: 抓開盤價（兩組共用同一份價格資料）
    print("\n【Step 3】抓取開盤/收盤價")
    from finmind_client import get_dataloader
    dl = get_dataloader()
    start_fmt = f"{args.start[:4]}-{args.start[4:6]}-{args.start[6:]}"
    end_fmt = dt_date.today().strftime('%Y-%m-%d')
    price_data = fetch_price_data(dl, SECTORS_ALL_IDS, start_fmt, end_fmt)
    trading_days = get_sorted_trading_days(price_data)
    print(f"  共 {len(trading_days)} 個交易日")

    # Step 4: 執行回測
    if args.qualified_only:
        # 只跑雙條件
        print("\n【Step 4】執行 9 組回測（雙條件達標）")
        combos_qual = run_all_backtests(swa_qual, price_data, trading_days)
        combos_all  = None
    elif args.compare:
        # 兩組都跑
        print("\n【Step 4a】執行 9 組回測（全 BUY）")
        combos_all  = run_all_backtests(swa_all,  price_data, trading_days)
        print("\n【Step 4b】執行 9 組回測（雙條件達標）")
        combos_qual = run_all_backtests(swa_qual, price_data, trading_days)
    else:
        # 預設只跑全 BUY
        print("\n【Step 4】執行 9 組回測（全 BUY）")
        combos_all  = run_all_backtests(swa_all,  price_data, trading_days)
        combos_qual = None

    # Step 5: 存檔
    out_path = Path('backtest_results.json')
    results = {
        'generated_at': datetime.now().isoformat(),
        'date_range': {'start': args.start, 'end': dt_date.today().strftime('%Y%m%d')},
        'total_signals':         len(signals_all),
        'signals_after_limit':   len(swa_all),
        'qualified_signals':     len(signals_qual),
        'qualified_after_limit': len(swa_qual),
        'combinations': {
            k: {'stats': v['stats'], 'trades': v['trades']}
            for k, v in (combos_all or {}).items()
        },
        'combinations_qualified': {
            k: {'stats': v['stats'], 'trades': v['trades']}
            for k, v in (combos_qual or {}).items()
        } if combos_qual else {},
    }
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2, default=str),
                        encoding='utf-8')
    print(f"\n  ✅ 結果已存至 {out_path}")

    # Step 6: 上傳 Notion
    if not args.no_notion:
        print("\n【Step 6】上傳 Notion")
        from notion_upload import upload_backtest_results
        page_id = upload_backtest_results(results)
        print(f"  ✅ Notion 頁面：{page_id}")
    else:
        print("\n【Step 6】跳過 Notion 上傳")

    print(f"\n{'='*60}")
    print("  回測完成！")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
