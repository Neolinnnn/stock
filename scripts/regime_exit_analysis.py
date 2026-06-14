# -*- coding: utf-8 -*-
"""
波段 × 出場策略交叉分析
========================
1. 將 TAIEX 三年走勢用演算法分類為四種市場波段：
   上漲期 / 停滯期 / 修正期 / 下跌期
2. 對每筆 BUY 訊號，以進場日所屬波段歸類
3. 比較三種出場策略在各波段的勝率與報酬：
   TP15SL15（固定）/ TRAIL15（追蹤）/ HYBRID（三段式）

三段式 HYBRID 出場：
  Phase 1（進場 → 達 +15%）
    - 停損：收盤 ≤ 進場×0.85
    - 觸發 Phase 2：收盤 ≥ 進場×1.15
  Phase 2（已賺 15%，拉長抱）
    - MA10 出場：收盤跌破 MA10 → 次日開盤賣
    - 利潤地板：收盤 ≤ 進場×1.07 → 立即出場
    - 時間停損：進入 Phase 2 後 25 日未創新高 → 出場

用法：python scripts/regime_exit_analysis.py
輸出：docs/regime_exit.json + console 報告
"""
import json
import math
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
import sys
sys.path.insert(0, str(ROOT / 'scripts'))

import pandas as pd
from backtest_combo_search import (
    fetch_ohlcv, compute_indicators, load_signals,
    sim_tpsl, sim_trailing, MAX_HOLD_DAYS, HARD_STOP,
)


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """濾掉 FinMind 資料缺口列（close/open<=0，停牌或無資料記成 0）。
    這些列會讓停損被假觸發成 -100%，污染報酬統計。"""
    if df is None or df.empty:
        return df
    mask = (df['close'] > 0) & (df['open'] > 0)
    return df[mask].reset_index(drop=True)


# ── 波段分類 ──────────────────────────────────────────────────────────────────

def classify_regimes(taiex: pd.DataFrame) -> dict:
    """逐日將 TAIEX 分類為四種波段，回傳 {date: regime}。

    判準（嚴格使用 ≤ 當日資料）：
      - ma60_slope = MA60 相對 20 日前變化率
      - pos = 收盤相對 MA60
      - dd = 距 60 日高點回檔幅度
      上漲期：ma60_slope > +1.5% 且 收盤 > MA60
      下跌期：ma60_slope < -1.5% 且 收盤 < MA60
      修正期：回檔 > 8%（距 60 日高）但尚未轉空（MA60 未明顯下彎）
      停滯期：其餘（MA60 走平、收盤在 MA60 附近震盪）
    """
    t = taiex.copy().reset_index(drop=True)
    t['ma20']  = t['close'].rolling(20).mean()
    t['ma60']  = t['close'].rolling(60).mean()
    t['hh60']  = t['close'].rolling(60).max()
    t['ma60_20ago'] = t['ma60'].shift(20)

    out = {}
    for _, r in t.iterrows():
        d = r['date']
        if pd.isna(r['ma60']) or pd.isna(r['ma60_20ago']):
            out[d] = '暖身'
            continue
        slope = (r['ma60'] - r['ma60_20ago']) / r['ma60_20ago'] if r['ma60_20ago'] else 0
        pos   = (r['close'] - r['ma60']) / r['ma60'] if r['ma60'] else 0
        dd    = (r['close'] - r['hh60']) / r['hh60'] if r['hh60'] else 0

        if slope > 0.015 and r['close'] > r['ma60']:
            reg = '上漲期'
        elif slope < -0.015 and r['close'] < r['ma60']:
            reg = '下跌期'
        elif dd < -0.08:
            reg = '修正期'
        else:
            reg = '停滯期'
        out[d] = reg
    return out


# ── 三段式 HYBRID 出場 ────────────────────────────────────────────────────────

PROFIT_TRIGGER = 0.15   # 達 +15% 進入 Phase 2
PROFIT_FLOOR   = 0.07   # Phase 2 利潤地板 +7%
PHASE1_SL      = 0.15   # Phase 1 停損 -15%
PHASE2_TIMEOUT = 25     # Phase 2 未創新高的時間停損（交易日）


def sim_hybrid(ohlcv, entry_date, entry_price):
    """三段式出場模擬。回傳 {ret, days, why} 或 None（未結算）。"""
    df = ohlcv.copy()
    df['ma10'] = df['close'].rolling(10).mean()
    rows = df[df['date'] > entry_date]

    trigger_price = entry_price * (1 + PROFIT_TRIGGER)
    floor_price   = entry_price * (1 + PROFIT_FLOOR)
    p1_sl_price   = entry_price * (1 - PHASE1_SL)

    phase = 1
    hwm   = entry_price            # Phase 2 啟用後追蹤新高
    days_since_high = 0
    pending_open = False           # MA10 觸發 → 次日開盤賣

    for hold, (_, r) in enumerate(rows.iterrows(), 1):
        cl = float(r['close'])

        # 待售：次日開盤出場
        if pending_open:
            op = float(r['open'])
            return {'ret': op / entry_price - 1, 'days': hold, 'why': 'MA10'}

        # ── Phase 1 ──
        if phase == 1:
            if cl <= p1_sl_price:
                return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'SL'}
            if cl >= trigger_price:
                phase = 2
                hwm = cl
                days_since_high = 0
            if hold >= MAX_HOLD_DAYS:
                return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'MAX'}
            continue

        # ── Phase 2 ──
        if cl > hwm:
            hwm = cl
            days_since_high = 0
        else:
            days_since_high += 1

        # 利潤地板：滑回 +7% 立即出場
        if cl <= floor_price:
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'FLOOR'}
        # MA10 跌破 → 次日開盤賣
        if not pd.isna(r['ma10']) and cl < float(r['ma10']):
            pending_open = True
            continue
        # 時間停損：Phase 2 內 25 日未創新高
        if days_since_high >= PHASE2_TIMEOUT:
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'TIME'}
        if hold >= MAX_HOLD_DAYS:
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'MAX'}

    return None


EXITS = {
    'TP15SL15': lambda o, d, p: sim_tpsl(o, d, p, 0.15, 0.15),
    'TRAIL15':  lambda o, d, p: sim_trailing(o, d, p, 0.15),
    'HYBRID':   lambda o, d, p: sim_hybrid(o, d, p),
}


# ── 統計 ──────────────────────────────────────────────────────────────────────

def wilson_lb(wins, n, z=1.96):
    if n == 0:
        return 0
    p = wins / n
    denom  = 1 + z*z/n
    centre = p + z*z/(2*n)
    margin = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (centre - margin) / denom


def summarize(results: list) -> dict | None:
    settled = [r for r in results if r is not None]
    if not settled:
        return None
    rets   = [r['ret'] for r in settled]
    wins   = sum(1 for r in rets if r > 0)
    gains  = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r <= 0)
    return {
        'n':        len(settled),
        'open':     len(results) - len(settled),
        'win_rate': round(wins / len(settled), 4),
        'wilson':   round(wilson_lb(wins, len(settled)), 4),
        'avg_ret':  round(sum(rets) / len(rets) * 100, 2),
        'med_ret':  round(sorted(rets)[len(rets)//2] * 100, 2),
        'pf':       round(gains / losses, 2) if losses > 0 else 99.0,
        'avg_days': round(sum(r['days'] for r in settled) / len(settled), 1),
        'best':     round(max(rets) * 100, 2),
        'worst':    round(min(rets) * 100, 2),
    }


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    signals = load_signals()
    sids = sorted(set(s['stock_id'] for s in signals))
    print(f'訊號 {len(signals)} 筆，個股 {len(sids)} 檔')

    # 載入快取（combo_search 已抓過），清理資料缺口後計算指標
    ohlcv_map = {}
    for sid in sids:
        df = clean_ohlcv(fetch_ohlcv(sid))
        ohlcv_map[sid] = compute_indicators(df) if not df.empty else pd.DataFrame()
    taiex = compute_indicators(clean_ohlcv(fetch_ohlcv('TAIEX')))

    regimes = classify_regimes(taiex)
    # 波段天數統計
    reg_days = defaultdict(int)
    for d, reg in regimes.items():
        if d >= '20230614':
            reg_days[reg] += 1
    print('\n波段天數分布（2023/06/14 起）：')
    for reg in ['上漲期', '停滯期', '修正期', '下跌期', '暖身']:
        print(f'  {reg}: {reg_days.get(reg, 0)} 天')

    # 對每筆訊號模擬三種出場，並標記進場日波段
    records = []
    for sig in signals:
        ohlcv = ohlcv_map.get(sig['stock_id'])
        if ohlcv is None or ohlcv.empty:
            continue
        nxt = ohlcv[ohlcv['date'] > sig['date']]
        if nxt.empty or pd.isna(nxt.iloc[0]['open']) or float(nxt.iloc[0]['open']) <= 0:
            continue
        entry_date  = nxt.iloc[0]['date']
        entry_price = float(nxt.iloc[0]['open'])
        regime = regimes.get(sig['date'], '暖身')
        rec = {'date': sig['date'], 'regime': regime,
               'exits': {eid: fn(ohlcv, entry_date, entry_price)
                         for eid, fn in EXITS.items()}}
        records.append(rec)

    print(f'\n有效樣本 {len(records)} 筆')

    # 整體三種出場比較
    print('\n════════ 整體出場比較（全波段） ════════')
    print(f'{"出場":<10}{"n":>5}{"勝率":>6}{"Wil.":>6}{"avg%":>7}{"中位%":>7}{"PF":>5}{"天數":>6}{"最佳%":>7}{"最差%":>7}')
    overall = {}
    for eid in EXITS:
        s = summarize([r['exits'][eid] for r in records])
        overall[eid] = s
        if s:
            print(f'{eid:<10}{s["n"]:>5}{s["win_rate"]:>6.0%}{s["wilson"]:>6.0%}'
                  f'{s["avg_ret"]:>7.2f}{s["med_ret"]:>7.2f}{s["pf"]:>5.2f}'
                  f'{s["avg_days"]:>6.1f}{s["best"]:>7.1f}{s["worst"]:>7.1f}')

    # 波段 × 出場交叉
    cross = {}
    print('\n════════ 波段 × 出場交叉分析 ════════')
    for reg in ['上漲期', '停滯期', '修正期', '下跌期']:
        subset = [r for r in records if r['regime'] == reg]
        cross[reg] = {'n_signals': len(subset), 'exits': {}}
        print(f'\n【{reg}】訊號 {len(subset)} 筆')
        if not subset:
            print('  （無訊號）')
            continue
        print(f'  {"出場":<10}{"n":>5}{"勝率":>6}{"avg%":>7}{"中位%":>7}{"PF":>5}{"天數":>6}{"最佳%":>7}')
        for eid in EXITS:
            s = summarize([r['exits'][eid] for r in subset])
            cross[reg]['exits'][eid] = s
            if s:
                print(f'  {eid:<10}{s["n"]:>5}{s["win_rate"]:>6.0%}'
                      f'{s["avg_ret"]:>7.2f}{s["med_ret"]:>7.2f}{s["pf"]:>5.2f}'
                      f'{s["avg_days"]:>6.1f}{s["best"]:>7.1f}')

    out = {
        'generated_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        'regime_days':  dict(reg_days),
        'overall':      overall,
        'cross':        cross,
        'hybrid_params': {
            'profit_trigger': PROFIT_TRIGGER, 'profit_floor': PROFIT_FLOOR,
            'phase1_sl': PHASE1_SL, 'phase2_timeout': PHASE2_TIMEOUT,
        },
    }
    dest = ROOT / 'docs' / 'regime_exit.json'
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已輸出 {dest}')


if __name__ == '__main__':
    main()
