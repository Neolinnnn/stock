"""
進出場策略全面比較回測（2025/06/01 ~ 2026/06/01）
=================================================
篩選法：
  A. 原本程式篩選 = qualified 清單（族群掃描雙篩選輸出）
  B. 雙程式篩選   = qualified + 分析引擎趨勢評分 ≥ 60（掃描程式 + 分析引擎）

出場策略（9 種）：
  MA5跌破 / MA10跌破 / MA20跌破
  TP15SL10 / TP15SL15 / TP20SL10 / TP20SL15
  追蹤停損10% / 追蹤停損15%

進場：訊號日次日開盤
保護：硬停損 -20%（MA系列與追蹤系列適用），最長持倉 60 交易日

額外：統計各指標評等的準確度（勝率）與信心度（樣本數 + Wilson下界）

用法：python scripts/backtest_strategy_compare.py
"""
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))  # indicators package

import pandas as pd
from datafeed import finmind_fetch
from indicators.stock_analyzer import analyze_stock

# ── 設定 ─────────────────────────────────────────────────────────────────────
RANGE_START = '20250601'
RANGE_END   = '20260601'
MIN_TREND_SCORE = 60
HARD_STOP = -0.20
MAX_HOLD_DAYS = 60
FETCH_START = '2025-03-01'   # 提前抓資料供 MA60/暖身
FETCH_END   = '2026-06-01'


# ── 訊號收集 ─────────────────────────────────────────────────────────────────
def load_qualified_signals() -> list[dict]:
    signals, seen = [], set()
    for d in sorted((ROOT / 'daily_reports').iterdir()):
        if not d.is_dir() or d.name.startswith('weekly'):
            continue
        if not (RANGE_START <= d.name <= RANGE_END):
            continue
        sf = d / 'summary.json'
        if not sf.exists():
            continue
        data = json.loads(sf.read_text(encoding='utf-8'))
        for s in data.get('qualified', []):
            sid = s.get('id', '')
            if not sid or (d.name, sid) in seen:
                continue
            seen.add((d.name, sid))
            signals.append({
                'date':         d.name,
                'stock_id':     sid,
                'stock_name':   s.get('name', sid),
                'sector':       s.get('sector', ''),
                'signal_close': s.get('price', 0),
                'cv_sharpe':    s.get('cv_sharpe', 0) or 0,
                'rsi5':         s.get('rsi', 0) or 0,
            })
    return signals


# ── 價格 ─────────────────────────────────────────────────────────────────────
def fetch_price_data(stock_ids, start, end):
    pd_out = {}
    for sid in stock_ids:
        try:
            df = finmind_fetch('taiwan_stock_daily', stock_id=sid, start_date=start, end_date=end)
            if df is None or df.empty:
                pd_out[sid] = {}
                continue
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
            pd_out[sid] = {
                row['date']: {
                    'open':  float(row['open']),
                    'high':  float(row.get('max', row['open'])),
                    'low':   float(row.get('min', row['close'])),
                    'close': float(row['close']),
                }
                for _, row in df.iterrows()
            }
        except Exception as e:
            print(f'    {sid} 下載失敗：{e}')
            pd_out[sid] = {}
    return pd_out


# ── 趨勢分析（含完整指標） ───────────────────────────────────────────────────
def compute_analysis(sid, signal_date, price_data):
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
        return analyze_stock(df, sid).to_dict()
    except Exception:
        return None


# ── MA 序列 ─────────────────────────────────────────────────────────────────
def calc_ma(prices, period):
    all_dates = sorted(prices.keys())
    ma = {}
    for i, d in enumerate(all_dates):
        window = [prices[dd]['close'] for dd in all_dates[max(0, i-period+1):i+1]]
        ma[d] = sum(window) / len(window) if len(window) >= period else None
    return ma


# ── 出場模擬 ─────────────────────────────────────────────────────────────────
def sim_ma_exit(entry_date, entry_price, prices, ma_period):
    """收盤跌破 MA → 次日開盤出場；硬停損 -20%；最長 60 天。"""
    ma = calc_ma(prices, ma_period)
    active = [d for d in sorted(prices) if d > entry_date]
    hard_sl = entry_price * (1 + HARD_STOP)
    holding, pending = 0, False
    for d in active:
        px = prices.get(d)
        if not px or not px['open'] or not px['close']:
            continue
        holding += 1
        if pending:
            return _rec('WIN' if px['open'] >= entry_price else 'LOSS',
                        d, px['open'], entry_date, entry_price, holding, 'MA跌破')
        if px['low'] <= hard_sl:
            ep = max(hard_sl, px['low'])
            return _rec('LOSS', d, ep, entry_date, entry_price, holding, '硬停損-20%')
        if holding >= MAX_HOLD_DAYS:
            return _rec('WIN' if px['close'] >= entry_price else 'LOSS',
                        d, px['close'], entry_date, entry_price, holding, '最長持倉')
        m = ma.get(d)
        if m is not None and px['close'] < m:
            pending = True
    return _open(entry_date, entry_price, holding)


def sim_tpsl_exit(entry_date, entry_price, prices, tp, sl):
    """逐日收盤判斷 TP/SL；最長 60 天。"""
    active = [d for d in sorted(prices) if d > entry_date]
    tp_p, sl_p = entry_price * (1 + tp), entry_price * (1 - sl)
    holding = 0
    for d in active:
        px = prices.get(d)
        if not px or not px['close']:
            continue
        holding += 1
        c = px['close']
        if c >= tp_p:
            return _rec('WIN', d, c, entry_date, entry_price, holding, f'停利+{int(tp*100)}%')
        if c <= sl_p:
            return _rec('LOSS', d, c, entry_date, entry_price, holding, f'停損-{int(sl*100)}%')
        if holding >= MAX_HOLD_DAYS:
            return _rec('WIN' if c >= entry_price else 'LOSS',
                        d, c, entry_date, entry_price, holding, '最長持倉')
    return _open(entry_date, entry_price, holding)


def sim_trailing_exit(entry_date, entry_price, prices, sl):
    """追蹤停損：高水位 ×(1-sl)，只升不降；收盤跌破 → 次日開盤出。"""
    active = [d for d in sorted(prices) if d > entry_date]
    hwm = entry_price
    trail = entry_price * (1 - sl)
    holding, pending = 0, False
    for d in active:
        px = prices.get(d)
        if not px or not px['open'] or not px['close']:
            continue
        holding += 1
        if pending:
            return _rec('WIN' if px['open'] >= entry_price else 'LOSS',
                        d, px['open'], entry_date, entry_price, holding, f'追蹤停損-{int(sl*100)}%')
        c = px['close']
        if c > hwm:
            hwm = c
            trail = hwm * (1 - sl)
        if holding >= MAX_HOLD_DAYS:
            return _rec('WIN' if c >= entry_price else 'LOSS',
                        d, c, entry_date, entry_price, holding, '最長持倉')
        if c <= trail:
            pending = True
    return _open(entry_date, entry_price, holding)


def _rec(result, xd, xp, ed, ep, hold, reason):
    return {'result': result, 'exit_date': xd, 'exit_price': round(xp, 2),
            'return_pct': round((xp - ep) / ep * 100, 2), 'holding_days': hold,
            'entry_date': ed, 'entry_price': ep, 'exit_reason': reason}


def _open(ed, ep, hold):
    return {'result': 'OPEN', 'exit_date': None, 'exit_price': None,
            'return_pct': None, 'holding_days': hold,
            'entry_date': ed, 'entry_price': ep, 'exit_reason': '持倉中'}


EXIT_STRATEGIES = {
    'MA5跌破':      lambda ed, ep, px: sim_ma_exit(ed, ep, px, 5),
    'MA10跌破':     lambda ed, ep, px: sim_ma_exit(ed, ep, px, 10),
    'MA20跌破':     lambda ed, ep, px: sim_ma_exit(ed, ep, px, 20),
    'TP15_SL10':    lambda ed, ep, px: sim_tpsl_exit(ed, ep, px, 0.15, 0.10),
    'TP15_SL15':    lambda ed, ep, px: sim_tpsl_exit(ed, ep, px, 0.15, 0.15),
    'TP20_SL10':    lambda ed, ep, px: sim_tpsl_exit(ed, ep, px, 0.20, 0.10),
    'TP20_SL15':    lambda ed, ep, px: sim_tpsl_exit(ed, ep, px, 0.20, 0.15),
    '追蹤停損10%':  lambda ed, ep, px: sim_trailing_exit(ed, ep, px, 0.10),
    '追蹤停損15%':  lambda ed, ep, px: sim_trailing_exit(ed, ep, px, 0.15),
}


# ── 統計 ─────────────────────────────────────────────────────────────────────
def calc_stats(trades):
    settled = [t for t in trades if t['result'] != 'OPEN']
    wins = [t for t in settled if t['result'] == 'WIN']
    losses = [t for t in settled if t['result'] == 'LOSS']
    n = len(settled)
    if n == 0:
        return None
    win_rets = [t['return_pct'] for t in wins]
    loss_rets = [t['return_pct'] for t in losses]
    avg_win = sum(win_rets) / len(wins) if wins else 0
    avg_loss = sum(loss_rets) / len(losses) if losses else 0
    avg_ret = sum(t['return_pct'] for t in settled) / n
    pf = abs(sum(win_rets) / sum(loss_rets)) if loss_rets and sum(loss_rets) != 0 else float('inf')
    return {
        'settled': n, 'wins': len(wins), 'losses': len(losses),
        'open_count': len(trades) - n,
        'win_rate': round(len(wins) / n, 4),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'avg_return': round(avg_ret, 2),
        'win_loss_ratio': round(abs(avg_win / avg_loss), 2) if avg_loss else None,
        'profit_factor': round(pf, 2) if pf != float('inf') else None,
        'avg_holding_days': round(sum(t['holding_days'] for t in settled) / n, 1),
        'expectancy': round(avg_ret, 2),
        'max_win': round(max(win_rets), 2) if wins else 0,
        'max_loss': round(min(loss_rets), 2) if losses else 0,
    }


def wilson_lower(wins, n, z=1.96):
    """Wilson 信賴區間下界（95%）。"""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z*z/n
    centre = p + z*z/(2*n)
    margin = z * math.sqrt((p*(1-p) + z*z/(4*n)) / n)
    return round((centre - margin) / denom, 4)


def confidence_label(n):
    if n >= 30: return '高'
    if n >= 15: return '中'
    if n >= 7:  return '低'
    return '極低'


# ── 主程式 ───────────────────────────────────────────────────────────────────
def main():
    print('='*60)
    print('  進出場策略全面比較回測 2025/06 ~ 2026/06')
    print('='*60)

    # 1. 訊號
    signals = load_qualified_signals()
    print(f'\n[1] qualified 訊號：{len(signals)} 筆')
    stock_ids = sorted({s['stock_id'] for s in signals})
    print(f'    涵蓋股票：{len(stock_ids)} 檔')

    # 2. 價格
    print('\n[2] 下載 OHLC...')
    price_data = fetch_price_data(stock_ids, FETCH_START, FETCH_END)
    trading_days = sorted({d for px in price_data.values() for d in px})

    # 3. 分析每筆訊號
    print('\n[3] 計算趨勢分析...')
    for s in signals:
        a = compute_analysis(s['stock_id'], s['date'], price_data)
        s['analysis'] = a
        s['trend_score'] = a['signal_score'] if a else None

    # 進場日 + 進場價
    def attach_entry(s):
        entry_date = next((d for d in trading_days if d > s['date']), None)
        if not entry_date:
            return None
        info = price_data.get(s['stock_id'], {}).get(entry_date)
        if not info or not info['open'] or info['open'] <= 0:
            return None
        return entry_date, info['open']

    # 4. 兩種篩選 × 9 種出場
    filters = {
        'A_原本程式': [s for s in signals if s['trend_score'] is not None],
        'B_雙程式':   [s for s in signals if s['trend_score'] is not None and s['trend_score'] >= MIN_TREND_SCORE],
    }
    print(f'\n[4] 篩選結果：A={len(filters["A_原本程式"])} 筆 / B={len(filters["B_雙程式"])} 筆')

    results = {}
    best_trades_for_calib = None
    for fname, sigs in filters.items():
        results[fname] = {}
        for ename, efunc in EXIT_STRATEGIES.items():
            trades = []
            open_pos = {}
            for s in sigs:
                ent = attach_entry(s)
                if not ent:
                    continue
                entry_date, entry_price = ent
                if open_pos.get(s['stock_id']):
                    continue
                # 只用進場後的價格序列
                px_after = {d: v for d, v in price_data[s['stock_id']].items() if d >= entry_date}
                t = efunc(entry_date, entry_price, px_after)
                t.update({
                    'stock_id': s['stock_id'], 'stock_name': s['stock_name'],
                    'sector': s['sector'], 'signal_date': s['date'],
                    'trend_score': s['trend_score'], 'cv_sharpe': s['cv_sharpe'],
                    'rsi5': s['rsi5'], 'analysis': s['analysis'],
                })
                trades.append(t)
                if t['result'] == 'OPEN':
                    open_pos[s['stock_id']] = entry_date
                else:
                    open_pos.pop(s['stock_id'], None)
            stats = calc_stats(trades)
            results[fname][ename] = {'stats': stats, 'trades': trades}

    # 5. 找最佳組合（以期望值 = avg_return 為主，需 settled>=10）
    print('\n[5] 各組合表現：')
    best = None
    for fname in results:
        for ename, r in results[fname].items():
            s = r['stats']
            if not s:
                continue
            score = s['avg_return'] if s['settled'] >= 10 else -999
            print(f'    {fname:12s} {ename:12s} 勝率{s["win_rate"]:.0%} '
                  f'avg{s["avg_return"]:+.1f}% 盈虧比{s["win_loss_ratio"]} n={s["settled"]}')
            if best is None or score > best[2]:
                best = (fname, ename, score, r)

    print(f'\n  ★ 最佳組合：{best[0]} × {best[1]}（avg {best[3]["stats"]["avg_return"]:+.2f}%）')

    # 6. 指標準確度分析
    #    用 Filter A（超集，含 <60 評分）搭配最佳出場策略 → 最大化樣本數，
    #    讓各指標分桶的信心度更可靠，並能觀察評分 <60 的表現。
    print('\n[6] 指標準確度分析（Filter A 超集 × 最佳出場）...')
    best_exit_name = best[1]
    calib_trades = [t for t in results['A_原本程式'][best_exit_name]['trades']
                    if t['result'] != 'OPEN']
    calib = analyze_indicators(calib_trades)
    best_trades = [t for t in best[3]['trades'] if t['result'] != 'OPEN']

    # 7. 輸出 JSON
    out = {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'range': {'start': RANGE_START, 'end': RANGE_END},
        'signal_count': len(signals),
        'stock_count': len(stock_ids),
        'filters': {'A_原本程式': len(filters['A_原本程式']), 'B_雙程式': len(filters['B_雙程式'])},
        'min_trend_score': MIN_TREND_SCORE,
        'best': {'filter': best[0], 'exit': best[1]},
        'grid': {f: {e: r['stats'] for e, r in results[f].items()} for f in results},
        'best_trades': best_trades,
        'calibration': calib,
        'calibration_basis': {'filter': 'A_原本程式', 'exit': best_exit_name,
                              'settled': len(calib_trades)},
    }
    dest = ROOT / 'backtest_strategy_compare.json'
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    print(f'\n✅ 已儲存 {dest}（{dest.stat().st_size // 1024} KB）')
    return out


def analyze_indicators(trades):
    """對每個指標分桶，計算勝率（準確度）+ 樣本數/Wilson下界（信心度）。"""
    def bucketize(trades, keyfn, buckets):
        out = {}
        for label, pred in buckets:
            sub = [t for t in trades if t.get('__skip__') is None and pred(keyfn(t))]
            n = len(sub)
            wins = len([t for t in sub if t['result'] == 'WIN'])
            avg = round(sum(t['return_pct'] for t in sub) / n, 2) if n else 0
            out[label] = {
                'n': n, 'wins': wins,
                'win_rate': round(wins / n, 4) if n else 0,
                'avg_return': avg,
                'wilson_lower': wilson_lower(wins, n),
                'confidence': confidence_label(n),
            }
        return out

    def safe(v, default=None):
        return v if v is not None else default

    calib = {}

    # 趨勢評分桶
    calib['趨勢評分'] = bucketize(
        trades, lambda t: safe(t.get('trend_score'), 0),
        [('≥80', lambda v: v >= 80), ('75-79', lambda v: 75 <= v < 80),
         ('70-74', lambda v: 70 <= v < 75), ('65-69', lambda v: 65 <= v < 70),
         ('60-64', lambda v: 60 <= v < 65), ('<60', lambda v: v < 60)])

    # CV 夏普桶
    calib['CV夏普'] = bucketize(
        trades, lambda t: safe(t.get('cv_sharpe'), 0),
        [('≥8', lambda v: v >= 8), ('5-8', lambda v: 5 <= v < 8),
         ('2-5', lambda v: 2 <= v < 5), ('1-2', lambda v: 1 <= v < 2),
         ('<1', lambda v: v < 1)])

    # RSI5 桶
    calib['RSI5'] = bucketize(
        trades, lambda t: safe(t.get('rsi5'), 0),
        [('60+', lambda v: v >= 60), ('55-60', lambda v: 55 <= v < 60),
         ('50-55', lambda v: 50 <= v < 55), ('45-50', lambda v: 45 <= v < 50),
         ('<45', lambda v: v < 45)])

    # 買入訊號標籤
    calib['買入訊號'] = bucketize(
        trades, lambda t: (t.get('analysis') or {}).get('buy_signal', ''),
        [('強力買入', lambda v: v == '強力買入'), ('買入', lambda v: v == '買入'),
         ('持有', lambda v: v == '持有'), ('觀望', lambda v: v == '觀望')])

    # MA 排列（趨勢狀態）
    calib['趨勢排列'] = bucketize(
        trades, lambda t: (t.get('analysis') or {}).get('trend_status', ''),
        [('強勢多頭', lambda v: v == '強勢多頭'), ('多頭排列', lambda v: v == '多頭排列'),
         ('弱勢多頭', lambda v: v == '弱勢多頭'),
         ('其他', lambda v: v not in ('強勢多頭', '多頭排列', '弱勢多頭'))])

    # MACD 狀態
    calib['MACD'] = bucketize(
        trades, lambda t: (t.get('analysis') or {}).get('macd_status', ''),
        [('金叉', lambda v: '金叉' in v or '多頭' in v),
         ('死叉', lambda v: '死叉' in v or '空頭' in v),
         ('其他', lambda v: '金叉' not in v and '多頭' not in v and '死叉' not in v and '空頭' not in v)])

    # 量能狀態
    calib['量能'] = bucketize(
        trades, lambda t: (t.get('analysis') or {}).get('volume_status', ''),
        [('放量上漲', lambda v: v == '放量上漲'), ('縮量回調', lambda v: v == '縮量回調'),
         ('量能正常', lambda v: v == '量能正常'),
         ('其他', lambda v: v not in ('放量上漲', '縮量回調', '量能正常'))])

    return calib


if __name__ == '__main__':
    main()
