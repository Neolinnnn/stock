# -*- coding: utf-8 -*-
"""
領頭羊突破系統回測（Breakout Lab）
==================================
獨立於雙篩選的 bottom-up 選股：不看族群排名，直接抓「自己先動」的領頭羊。

進場條件（全部成立才進場，訊號日次日開盤買入）：
  0. 市場開關：TAIEX 收盤 > MA60
  1. 突破：收盤創 20 日新高
  2. 量能驗證：量比 ≥ 1.5（當日量 / 20日均量）
  3. 籌碼同向：外資+投信近 5 日淨買超 > 0
  4. 結構健康：收盤 > MA20、MA20 上揚、乖離 MA20 ≤ 10%

倉位規則：同股有未出場倉位時不重複進場。
出場：追蹤停損 15%（主）/ TP18SL15（對照），硬停損 -20%，最長 60 日。

範圍：84 檔追蹤池（docs/stocks/）、2024-10 起暖身、訊號期 2025-01 ~ 今。
用法：python scripts/backtest_breakout_lab.py [--refresh]
輸出：docs/breakout_lab.json + console 報告
"""
import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import pandas as pd

from backtest_entry_lab import (
    fetch_ohlcv, fetch_chip, compute_indicators,
    sim_tpsl, sim_trailing, get_entry, summarize, wilson_lb,
)

SIGNAL_START = '20250101'
VOL_RATIO_MIN = 1.5
DIST_MA20_MAX = 0.10
CHIP_DAYS = 5


def load_universe() -> list[str]:
    return sorted(p.stem for p in (ROOT / 'docs' / 'stocks').glob('*.json'))


def taiex_bull_map(taiex: pd.DataFrame) -> dict:
    """date -> TAIEX 收盤 > MA60"""
    t = taiex.copy()
    t['ma60'] = t['close'].rolling(60).mean()
    return {r['date']: bool(r['close'] > r['ma60']) for _, r in t.iterrows() if pd.notna(r['ma60'])}


def find_signals(sid: str, df: pd.DataFrame, chip: pd.DataFrame, bull: dict) -> list[dict]:
    """逐日掃描突破訊號（嚴格使用 ≤ 當日資料）。"""
    sigs = []
    chip_net = {}
    if chip is not None and not chip.empty:
        chip_net = dict(zip(chip['date'], chip['net']))
    dates = list(df['date'])
    closes = list(df['close'])
    for i in range(60, len(df)):
        d = dates[i]
        if d < SIGNAL_START:
            continue
        if not bull.get(d, False):
            continue
        r = df.iloc[i]
        c = r['close']
        # 1. 突破 20 日收盤新高
        if c < max(closes[i - 20:i]):
            continue
        # 2. 量比
        if not r['vol_ma20'] or r['volume'] / r['vol_ma20'] < VOL_RATIO_MIN:
            continue
        # 3. 法人 5 日淨買超
        c5 = sum(chip_net.get(dd, 0) for dd in dates[max(0, i - CHIP_DAYS + 1):i + 1])
        if c5 <= 0:
            continue
        # 4. 結構：> MA20、MA20 上揚、乖離 ≤ 10%
        if pd.isna(r['ma20']) or c <= r['ma20']:
            continue
        if df.iloc[i - 5]['ma20'] and r['ma20'] <= df.iloc[i - 5]['ma20']:
            continue
        if (c - r['ma20']) / r['ma20'] > DIST_MA20_MAX:
            continue
        sigs.append({
            'date': d, 'stock_id': sid, 'close': float(c),
            'vol_ratio': round(float(r['volume'] / r['vol_ma20']), 2),
            'chip5': float(c5),
            'dist_ma20': round(float((c - r['ma20']) / r['ma20'] * 100), 2),
        })
    return sigs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true')
    args = ap.parse_args()

    universe = load_universe()
    print(f'追蹤池 {len(universe)} 檔')

    taiex = fetch_ohlcv('TAIEX', args.refresh)
    bull = taiex_bull_map(taiex)

    all_signals, ohlcv_map = [], {}
    for n, sid in enumerate(universe, 1):
        df = fetch_ohlcv(sid, args.refresh)
        if df.empty or len(df) < 80:
            continue
        df = compute_indicators(df)
        ohlcv_map[sid] = df
        chip = fetch_chip(sid, args.refresh)
        sigs = find_signals(sid, df, chip, bull)
        all_signals.extend(sigs)
        if n % 20 == 0:
            print(f'  進度 {n}/{len(universe)}，累計訊號 {len(all_signals)}')

    all_signals.sort(key=lambda s: s['date'])
    print(f'原始訊號 {len(all_signals)} 筆')

    # 同股未出場不重複進場（以追蹤停損15%的出場日為準）
    trades = []
    open_until = {}   # sid -> exit_date（'9999' = 未結算）
    for sig in all_signals:
        sid = sig['stock_id']
        if sid in open_until and sig['date'] < open_until[sid]:
            continue
        ohlcv = ohlcv_map[sid]
        entry_date, entry_price = get_entry(ohlcv, sig['date'])
        if entry_date is None or not entry_price:
            continue
        ex_trail = sim_trailing(ohlcv, entry_date, entry_price, 0.15)
        ex_tpsl = sim_tpsl(ohlcv, entry_date, entry_price, 0.18, 0.15)
        open_until[sid] = ex_trail['exit'] if ex_trail else '99999999'
        trades.append({
            'sig': sig, 'entry_date': entry_date, 'entry_price': entry_price,
            'exits': {'TRAIL15': ex_trail, 'TP18SL15': ex_tpsl},
        })
    print(f'實際進場 {len(trades)} 筆（同股不重複）')

    dates = [t['sig']['date'] for t in trades]
    mid = sorted(dates)[len(dates) // 2] if dates else ''

    out_exits = {}
    for ex in ['TRAIL15', 'TP18SL15']:
        rows = [{'result': t['exits'][ex]} for t in trades]
        s = summarize(rows)
        s['wf_front'] = summarize([{'result': t['exits'][ex]} for t in trades if t['sig']['date'] <= mid])
        s['wf_back'] = summarize([{'result': t['exits'][ex]} for t in trades if t['sig']['date'] > mid])
        out_exits[ex] = s

    # 報告
    print('\n════════ 領頭羊突破系統 ════════')
    for ex, s in out_exits.items():
        if not s.get('n'):
            continue
        f, b = s['wf_front'], s['wf_back']
        print(f"{ex}: n={s['n']} 勝率={s['win_rate']:.0%} (Wilson下界 {s['wilson_lb']:.0%}) "
              f"平均={s['avg_ret']}% PF={s['profit_factor']} 持有={s['avg_days']}天")
        print(f"  walk-forward: 前段 {f.get('win_rate', 0):.0%}(n={f.get('n', 0)}) / "
              f"後段 {b.get('win_rate', 0):.0%}(n={b.get('n', 0)})")

    # 交易明細（給 UI）
    trade_rows = []
    for t in trades:
        ex = t['exits']['TRAIL15']
        trade_rows.append({
            'stock_id': t['sig']['stock_id'],
            'signal_date': t['sig']['date'],
            'entry_date': t['entry_date'],
            'entry_price': round(t['entry_price'], 2),
            'vol_ratio': t['sig']['vol_ratio'],
            'chip5': t['sig']['chip5'],
            'ret_pct': round(ex['ret'] * 100, 2) if ex else None,
            'exit_date': ex['exit'] if ex else None,
            'days': ex['days'] if ex else None,
            'why': ex['why'] if ex else 'OPEN',
        })

    out = {
        'generated_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        'rules': {
            'gate': 'TAIEX > MA60', 'breakout': '收盤創20日新高',
            'volume': f'量比 ≥ {VOL_RATIO_MIN}', 'chip': f'法人{CHIP_DAYS}日淨買超 > 0',
            'structure': f'收盤>MA20、MA20上揚、乖離≤{int(DIST_MA20_MAX*100)}%',
            'exit': '追蹤停損15%（硬停損-20%、最長60日）',
        },
        'period': {'start': SIGNAL_START, 'signals': len(trades), 'wf_split': mid,
                   'universe': len(universe)},
        'exits': out_exits,
        'trades': trade_rows,
    }
    (ROOT / 'docs' / 'breakout_lab.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n已輸出 docs/breakout_lab.json')


if __name__ == '__main__':
    main()
