"""
MA10 出場策略回測
===================================
進場：股票出現在 qualified 清單（雙篩選通過）
      + 趨勢分析引擎評分 >= 60（買入級以上）= 雙模型高信心
出場：當日收盤跌破 10 日均線 → 次日開盤出場
保護：硬停損 -20%（防黑天鵝），最多持倉 60 個交易日

資料來源：FinMind
回測區間：daily_reports 所有有 qualified 的日期
"""
import json
import math
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # indicators package

import pandas as pd

try:
    from datafeed import finmind_fetch
    _FINMIND_OK = True
except Exception as e:
    print(f'[WARN] FinMind 載入失敗：{e}')
    _FINMIND_OK = False

try:
    from indicators.stock_analyzer import analyze_stock
    _ANALYZER_OK = True
except Exception as e:
    print(f'[WARN] 分析引擎載入失敗：{e}')
    _ANALYZER_OK = False

HARD_STOP = -0.20        # 硬停損 -20%
MAX_HOLD_DAYS = 60       # 最多持倉 60 個交易日
MIN_TREND_SCORE = 60     # 趨勢評分門檻（買入 = 60+，強力買入 = 75+）


# ── 訊號收集 ─────────────────────────────────────────────────────────────────

def load_qualified_signals() -> list[dict]:
    signals = []
    seen = set()  # (date, stock_id) dedup
    reports_dir = ROOT / 'daily_reports'
    for d in sorted(reports_dir.iterdir()):
        if not d.is_dir() or d.name.startswith('weekly'):
            continue
        sf = d / 'summary.json'
        if not sf.exists():
            continue
        data = json.loads(sf.read_text(encoding='utf-8'))
        for s in data.get('qualified', []):
            sid = s.get('id', '')
            if not sid:
                continue
            key = (d.name, sid)
            if key in seen:
                continue
            seen.add(key)
            signals.append({
                'date':         d.name,
                'stock_id':     sid,
                'stock_name':   s.get('name', sid),
                'sector':       s.get('sector', ''),
                'signal_close': s.get('price', 0),
                'cv_sharpe':    s.get('cv_sharpe', 0) or 0,
                'rsi':          s.get('rsi', 0) or 0,
            })
    print(f'  收集到 qualified 訊號：{len(signals)} 筆（已去重）')
    return signals


# ── 價格抓取 ─────────────────────────────────────────────────────────────────

def fetch_price_data(stock_ids: list[str],
                     start: str, end: str) -> dict:
    """回傳 {stock_id: {date_str: {open, high, low, close}}}"""
    print(f'  下載 OHLC：{start} ~ {end}（{len(stock_ids)} 檔）')
    price_data = {}
    for sid in stock_ids:
        try:
            df = finmind_fetch('taiwan_stock_daily', stock_id=sid, start_date=start, end_date=end)
            if df is None or df.empty:
                price_data[sid] = {}
                continue
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
            price_data[sid] = {
                row['date']: {
                    'open':  float(row['open']),
                    'high':  float(row.get('max', row['open'])),
                    'low':   float(row.get('min', row['close'])),
                    'close': float(row['close']),
                }
                for _, row in df.iterrows()
            }
            print(f'    {sid}: {len(price_data[sid])} 筆')
        except Exception as e:
            print(f'    {sid} 下載失敗：{e}')
            price_data[sid] = {}
    return price_data


# ── 趨勢分析（雙模型第二層） ─────────────────────────────────────────────────

def compute_trend_score(sid: str, signal_date: str,
                        price_data: dict) -> int | None:
    """用訊號日之前 90 根 K 棒跑趨勢分析，回傳評分。無資料回傳 None。"""
    if not _ANALYZER_OK:
        return None
    px = price_data.get(sid, {})
    dates = sorted(k for k in px if k <= signal_date)
    if len(dates) < 20:
        return None
    recent = dates[-90:]
    rows = [px[d] for d in recent]
    df = pd.DataFrame({
        'date':   recent,
        'open':   [r['open']  for r in rows],
        'high':   [r['high']  for r in rows],
        'low':    [r['low']   for r in rows],
        'close':  [r['close'] for r in rows],
        'volume': [0] * len(rows),
    })
    try:
        result = analyze_stock(df, sid)
        return result.signal_score
    except Exception as e:
        print(f'    [WARN] {sid} 分析失敗：{e}')
        return None


# ── MA10 出場模擬 ─────────────────────────────────────────────────────────────

def calc_ma10(dates_before: list[str], prices: dict) -> dict[str, float | None]:
    """計算所有日期的 MA10（用收盤價）。"""
    closes = [prices[d]['close'] for d in dates_before if d in prices]
    all_dates = sorted(prices.keys())
    ma10 = {}
    for i, d in enumerate(all_dates):
        closes_up_to = [prices[dd]['close'] for dd in all_dates[max(0, i-9):i+1]]
        if len(closes_up_to) >= 10:
            ma10[d] = sum(closes_up_to) / len(closes_up_to)
        else:
            ma10[d] = None
    return ma10


def simulate_ma10_exit(
    entry_date: str,
    entry_price: float,
    stock_prices: dict,
    trading_days: list[str],
) -> dict:
    """
    從 entry_date 次一交易日開盤買入，
    當日收盤 < MA10 則次日開盤出場。
    """
    active_days = [d for d in trading_days if d > entry_date]
    if not active_days:
        return _open_result(entry_date, entry_price, 0)

    # 計算全部 MA10（需要 entry 之前的收盤資料）
    all_ma10 = calc_ma10([], stock_prices)

    hard_sl = entry_price * (1 + HARD_STOP)
    holding = 0
    pending_exit = False    # 已觸發，次日開盤出

    for i, d in enumerate(active_days):
        px = stock_prices.get(d)
        if not px:
            continue

        o = px['open']
        c = px['close']
        if not o or not c or math.isnan(o) or math.isnan(c):
            continue

        holding += 1

        # 若前一日已觸發出場，今日開盤賣
        if pending_exit:
            ret = round((o - entry_price) / entry_price * 100, 2)
            res = 'WIN' if o >= entry_price else 'LOSS'
            return {'result': res, 'exit_date': d, 'exit_price': o,
                    'return_pct': ret, 'holding_days': holding,
                    'entry_date': entry_date, 'entry_price': entry_price,
                    'exit_reason': 'MA10跌破'}

        # 硬停損（盤中低點觸發）
        if px['low'] <= hard_sl:
            exit_price = max(hard_sl, px['low'])
            ret = round((exit_price - entry_price) / entry_price * 100, 2)
            return {'result': 'LOSS', 'exit_date': d, 'exit_price': round(exit_price, 2),
                    'return_pct': ret, 'holding_days': holding,
                    'entry_date': entry_date, 'entry_price': entry_price,
                    'exit_reason': '硬停損-20%'}

        # 最長持倉
        if holding >= MAX_HOLD_DAYS:
            ret = round((c - entry_price) / entry_price * 100, 2)
            res = 'WIN' if c >= entry_price else 'LOSS'
            return {'result': res, 'exit_date': d, 'exit_price': c,
                    'return_pct': ret, 'holding_days': holding,
                    'entry_date': entry_date, 'entry_price': entry_price,
                    'exit_reason': '最長持倉到期'}

        # MA10 跌破偵測
        ma = all_ma10.get(d)
        if ma is not None and c < ma:
            pending_exit = True   # 次日開盤出

    return _open_result(entry_date, entry_price, holding)


def _open_result(entry_date, entry_price, holding):
    return {'result': 'OPEN', 'exit_date': None, 'exit_price': None,
            'return_pct': None, 'holding_days': holding,
            'entry_date': entry_date, 'entry_price': entry_price,
            'exit_reason': '持倉中'}


# ── 統計 ─────────────────────────────────────────────────────────────────────

def calc_stats(trades: list) -> dict:
    settled = [t for t in trades if t['result'] != 'OPEN']
    wins    = [t for t in settled if t['result'] == 'WIN']
    losses  = [t for t in settled if t['result'] == 'LOSS']
    open_t  = [t for t in trades  if t['result'] == 'OPEN']

    n  = len(settled)
    wa = len(wins)
    la = len(losses)
    op = len(open_t)
    ta = n + op

    wr_settled    = wa / n  if n  else 0
    wr_pessimistic = wa / ta if ta else 0
    wr_optimistic  = (wa + op) / ta if ta else 0

    avg_ret   = sum(t['return_pct'] for t in settled) / n if n else 0
    avg_hold  = sum(t['holding_days'] for t in settled if t['holding_days']) / n if n else 0

    # 分組退出原因
    reasons = {}
    for t in trades:
        r = t.get('exit_reason', '未知')
        reasons[r] = reasons.get(r, 0) + 1

    return {
        'wins':    wa, 'losses': la, 'settled': n,
        'win_rate_settled':    round(wr_settled, 4),
        'open_count':          op,
        'total_all':           ta,
        'win_rate_pessimistic': round(wr_pessimistic, 4),
        'win_rate_optimistic':  round(wr_optimistic, 4),
        'avg_return':          round(avg_ret, 2),
        'avg_holding_days':    round(avg_hold, 1),
        'exit_reasons':        reasons,
    }


# ── 主程式 ───────────────────────────────────────────────────────────────────

def main():
    print('\n' + '='*60)
    print('  MA10 出場策略回測（雙模型高信心進場）')
    print('='*60 + '\n')

    if not _FINMIND_OK:
        print('❌ FinMind 不可用，終止')
        return

    # 1. 收集 qualified 訊號
    print('【Step 1】收集 qualified 訊號')
    signals = load_qualified_signals()
    if not signals:
        print('❌ 無訊號，終止')
        return

    stock_ids = sorted({s['stock_id'] for s in signals})

    # 2. 下載歷史 OHLCV
    print('\n【Step 2】下載歷史 OHLCV（FinMind）')
    first_date = signals[0]['date']
    start_dt = f'{first_date[:4]}-{first_date[4:6]}-01'  # 月初多抓 MA10 暖身
    end_dt = '2026-06-01'
    price_data = fetch_price_data(stock_ids, start_dt, end_dt)

    # 取得所有交易日
    all_days = set()
    for px in price_data.values():
        all_days.update(px.keys())
    trading_days = sorted(all_days)
    print(f'  共 {len(trading_days)} 個交易日')

    # 3. 雙模型過濾（第二層：趨勢評分 >= MIN_TREND_SCORE）
    print(f'\n【Step 3】趨勢分析過濾（門檻：{MIN_TREND_SCORE} 分 = 買入級以上）')
    filtered_signals = []
    skipped_no_data  = 0
    skipped_low_score = 0

    for sig in signals:
        score = compute_trend_score(sig['stock_id'], sig['date'], price_data)
        sig['trend_score'] = score
        if score is None:
            skipped_no_data += 1
            continue
        if score < MIN_TREND_SCORE:
            skipped_low_score += 1
            print(f'    ✗ {sig["date"]} {sig["stock_id"]} {sig["stock_name"]} '
                  f'趨勢評分 {score} < {MIN_TREND_SCORE}，跳過')
            continue
        filtered_signals.append(sig)
        print(f'    ✓ {sig["date"]} {sig["stock_id"]} {sig["stock_name"]} '
              f'評分 {score}，cv夏普 {sig["cv_sharpe"]:.2f}')

    print(f'\n  原始 qualified：{len(signals)} 筆')
    print(f'  資料不足跳過：{skipped_no_data} 筆')
    print(f'  趨勢評分不足：{skipped_low_score} 筆')
    print(f'  ✅ 雙模型高信心訊號：{len(filtered_signals)} 筆')

    # 4. 執行 MA10 出場模擬
    print('\n【Step 4】MA10 出場回測')
    trades = []
    open_positions: dict[str, str] = {}  # stock_id -> entry_date（持倉中不重複進場）
    for sig in filtered_signals:
        sid   = sig['stock_id']
        sdate = sig['date']

        # 進場日 = 訊號日的次一交易日
        entry_date = None
        for d in trading_days:
            if d > sdate:
                entry_date = d
                break
        if not entry_date:
            continue

        stock_px = price_data.get(sid, {})
        entry_info = stock_px.get(entry_date)
        if not entry_info or not entry_info['open']:
            print(f'  ⚠️  {sid} {entry_date} 開盤價缺失，跳過')
            continue

        entry_price = entry_info['open']
        if entry_price <= 0 or math.isnan(entry_price):
            continue

        # 同股已有未出場倉位，跳過（避免連續訊號重複入場）
        if open_positions.get(sid):
            continue

        trade = simulate_ma10_exit(entry_date, entry_price, stock_px, trading_days)
        trade['stock_id']    = sid
        trade['stock_name']  = sig['stock_name']
        trade['sector']      = sig['sector']
        trade['signal_date'] = sdate
        trade['signal_close']= sig['signal_close']
        trade['cv_sharpe']   = sig['cv_sharpe']
        trade['trend_score'] = sig['trend_score']
        trades.append(trade)

        if trade['result'] == 'OPEN':
            open_positions[sid] = entry_date
        else:
            open_positions.pop(sid, None)

        r = trade['result']
        ret = f"{trade['return_pct']:+.1f}%" if trade['return_pct'] is not None else 'OPEN'
        print(f'  {sid} {sig["stock_name"]:6s} {sdate}→{entry_date} '
              f'{r:4s} {ret:8s} 持 {trade["holding_days"] or "?":>3} 天 '
              f'[{trade.get("exit_reason","?")}]')

    # 5. 統計
    print('\n【Step 5】統計')
    stats = calc_stats(trades)
    print(f'  已結算勝率：{stats["win_rate_settled"]:.1%} '
          f'（{stats["wins"]}勝 / {stats["losses"]}敗 / 共{stats["settled"]}筆）')
    print(f'  悲觀勝率（持倉中全虧）：{stats["win_rate_pessimistic"]:.1%}')
    print(f'  樂觀勝率（持倉中全勝）：{stats["win_rate_optimistic"]:.1%}')
    print(f'  持倉中：{stats["open_count"]} 筆')
    print(f'  平均報酬：{stats["avg_return"]:+.2f}%')
    print(f'  平均持倉：{stats["avg_holding_days"]:.1f} 天')
    print(f'  出場原因：{stats["exit_reasons"]}')

    # 6. 輸出 JSON
    output = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'strategy':     'MA10_EXIT',
        'description':  '雙模型高信心進場（qualified + 趨勢評分≥60）+ MA10跌破出場',
        'params': {
            'min_trend_score': MIN_TREND_SCORE,
            'hard_stop_pct':   int(HARD_STOP * 100),
            'max_hold_days':   MAX_HOLD_DAYS,
        },
        'date_range': {
            'start': signals[0]['date'],
            'end':   signals[-1]['date'],
        },
        'signal_funnel': {
            'qualified_total':        len(signals),
            'skipped_no_price_data':  skipped_no_data,
            'skipped_low_trend_score': skipped_low_score,
            'high_confidence_trades': len(filtered_signals),
        },
        'stats':  stats,
        'trades': trades,
    }

    dest = ROOT / 'backtest_results_ma10.json'
    dest.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'\n✅ 結果已儲存：{dest}（{dest.stat().st_size // 1024} KB）')
    return output


if __name__ == '__main__':
    main()
