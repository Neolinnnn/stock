# -*- coding: utf-8 -*-
"""
最終策略回測：進場閘門 + 波段自適應出場
==========================================
進場閘門（訊號日須同時成立）：
  1. 大盤多頭：TAIEX 收盤 > MA60
  2. 個股多頭排列：收盤 > MA5 > MA20 > MA60

波段自適應出場（依進場日所屬波段）：
  上漲期         → HYBRID（達+15%後抱MA10，吃大波段）
  停滯期/其他     → TP15SL15（快進快出）

對照基準：
  A 現行：全 BUY × TP15SL15（無閘門）
  B 閘門 × TP15SL15
  C 閘門 × HYBRID
  D 閘門 × 自適應          ← 最終策略

walk-forward 以中位日期分前後段驗證。
用法：python scripts/backtest_final_strategy.py
輸出：docs/final_strategy.json + console 報告
"""
import json
import math
from pathlib import Path

ROOT = Path(__file__).parent.parent
import sys
sys.path.insert(0, str(ROOT / 'scripts'))

import pandas as pd
from backtest_combo_search import (
    fetch_ohlcv, compute_indicators, load_signals, build_features,
    sim_tpsl,
)
from regime_exit_analysis import clean_ohlcv, classify_regimes, sim_hybrid


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
        # 總報酬（假設每筆等額投入，複利近似用加總）
        'sum_ret':  round(sum(rets) * 100, 1),
    }


def fmt(s):
    if not s:
        return '  —'
    return (f'{s["n"]:>5}{s["win_rate"]:>6.0%}{s["wilson"]:>6.0%}'
            f'{s["avg_ret"]:>7.2f}{s["med_ret"]:>7.2f}{s["pf"]:>5.2f}'
            f'{s["avg_days"]:>6.1f}{s["sum_ret"]:>9.0f}')


def main():
    signals = load_signals()
    sids = sorted(set(s['stock_id'] for s in signals))
    print(f'訊號 {len(signals)} 筆，個股 {len(sids)} 檔')

    ohlcv_map = {}
    chip_dummy = {}
    for sid in sids:
        df = clean_ohlcv(fetch_ohlcv(sid))
        ohlcv_map[sid] = compute_indicators(df) if not df.empty else pd.DataFrame()
    taiex = compute_indicators(clean_ohlcv(fetch_ohlcv('TAIEX')))
    regimes = classify_regimes(taiex)

    # 對每筆訊號：算特徵判定閘門、標記波段、模擬兩種出場
    rows = []
    for sig in signals:
        ohlcv = ohlcv_map.get(sig['stock_id'])
        if ohlcv is None or ohlcv.empty:
            continue
        feat = build_features(sig, ohlcv, None, taiex)
        if feat is None:
            continue
        nxt = ohlcv[ohlcv['date'] > sig['date']]
        if nxt.empty or pd.isna(nxt.iloc[0]['open']) or float(nxt.iloc[0]['open']) <= 0:
            continue
        entry_date  = nxt.iloc[0]['date']
        entry_price = float(nxt.iloc[0]['open'])
        regime = regimes.get(sig['date'], '暖身')
        gate = bool(feat['ma_stack'] and feat['taiex_bull'])

        exit_tp  = sim_tpsl(ohlcv, entry_date, entry_price, 0.15, 0.15)
        exit_hyb = sim_hybrid(ohlcv, entry_date, entry_price)
        # 自適應：上漲期用 HYBRID，其餘用 TP15SL15
        exit_ada = exit_hyb if regime == '上漲期' else exit_tp

        rows.append({
            'date': sig['date'], 'regime': regime, 'gate': gate,
            'tp': exit_tp, 'hyb': exit_hyb, 'ada': exit_ada,
        })

    print(f'有效樣本 {len(rows)} 筆；通過閘門 {sum(1 for r in rows if r["gate"])} 筆')

    dates = sorted(r['date'] for r in rows)
    mid = dates[len(dates) // 2]

    def wf(subset, key):
        full  = summarize([r[key] for r in subset])
        front = summarize([r[key] for r in subset if r['date'] <= mid])
        back  = summarize([r[key] for r in subset if r['date'] >  mid])
        return full, front, back

    gated = [r for r in rows if r['gate']]

    scenarios = {
        'A 現行：全BUY × TP15SL15':  (rows,  'tp'),
        'B 閘門 × TP15SL15':         (gated, 'tp'),
        'C 閘門 × HYBRID':           (gated, 'hyb'),
        'D 閘門 × 自適應(最終)':      (gated, 'ada'),
    }

    print('\n════════ 策略對照（全期間） ════════')
    hdr = f'{"策略":<26}{"n":>5}{"勝率":>6}{"Wil.":>6}{"avg%":>7}{"中位":>7}{"PF":>5}{"天數":>6}{"總報酬%":>9}'
    print(hdr)
    print('─' * len(hdr))
    results = {}
    for name, (subset, key) in scenarios.items():
        full, front, back = wf(subset, key)
        results[name] = {'full': full, 'front': front, 'back': back}
        print(f'{name:<26}{fmt(full)}')

    print('\n════════ Walk-forward 前後段一致性 ════════')
    print(f'分割點 {mid}')
    print(f'{"策略":<26}{"前段勝率":>9}{"後段勝率":>9}{"前段avg":>9}{"後段avg":>9}')
    for name in scenarios:
        f1 = results[name]['front']
        f2 = results[name]['back']
        wr1 = f'{f1["win_rate"]:.0%}' if f1 else '—'
        wr2 = f'{f2["win_rate"]:.0%}' if f2 else '—'
        a1  = f'{f1["avg_ret"]:+.1f}' if f1 else '—'
        a2  = f'{f2["avg_ret"]:+.1f}' if f2 else '—'
        print(f'{name:<26}{wr1:>9}{wr2:>9}{a1:>9}{a2:>9}')

    # 自適應出場在閘門內的波段分布
    print('\n════════ 最終策略內部組成 ════════')
    from collections import Counter
    reg_cnt = Counter(r['regime'] for r in gated)
    for reg in ['上漲期', '停滯期', '修正期', '下跌期']:
        n = reg_cnt.get(reg, 0)
        ex = 'HYBRID' if reg == '上漲期' else 'TP15SL15'
        print(f'  {reg}: {n} 筆 → 用 {ex}')

    out = {
        'generated_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        'wf_split': mid,
        'total_signals': len(rows),
        'gated_signals': len(gated),
        'scenarios': {name: results[name] for name in scenarios},
        'gate_regime_dist': dict(reg_cnt),
    }
    dest = ROOT / 'docs' / 'final_strategy.json'
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已輸出 {dest}')


if __name__ == '__main__':
    main()
