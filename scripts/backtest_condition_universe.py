# -*- coding: utf-8 -*-
"""
進場條件全池回測（Condition Universe Backtest）
==============================================
entry_lab 只在 qualified 清單出現的日子測過濾器，而該清單 2025 年僅 37 筆
（5~8 月為 0），無法驗證條件在 2025 年的表現。本腳本把每個條件當成獨立進場
訊號，在追蹤池（docs/stocks/）逐日掃描 SIGNAL_START 起的全部交易日，
2025、2026 兩年都有足量樣本。

規則（與 entry_lab / breakout_lab 一致）：
- 特徵：entry_lab.build_features，嚴格只用 ≤ 訊號日資料（無前視偏差）
- 進場：訊號日次日開盤；同股未出場不重複進場（各出場策略各自判定）
- 出場：TP18/SL15（主）、追蹤停損 15%；最長 60 日
- 基準 BASE：無條件（未持倉即進場）＝同一追蹤池、隨機時點進場
- 價格先經 regime_exit_analysis.clean_ohlcv 濾掉 0 價缺口列，避免停損被假觸發成 −100%

回測標準（CLAUDE.md）：
  ① Wilson 下界 > 基準勝率  ② 2025、2026 各自勝過同年基準  ③ 超額報酬 vs 0050 > 0
不適用：SECTOR、CV1（需 qualified 清單的族群／CV 欄位）

用法：python scripts/backtest_condition_universe.py [--refresh]
輸出：回測數據/進場條件全池回測_<起>~<迄>.md + console 報告
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import pandas as pd

from backtest_breakout_lab import load_universe
from backtest_entry_lab import (
    FILTER_SOURCES, FILTERS, build_features, compute_indicators, fetch_chip,
    fetch_ohlcv, get_entry, load_benchmark, sim_tpsl, sim_trailing, summarize,
)
from regime_exit_analysis import clean_ohlcv

SIGNAL_START = '20250101'
YEARS = ('2025', '2026')
MIN_N = 20                        # 樣本低於此數不判定
SKIP = {'SECTOR', 'CV1'}          # 需 qualified 清單欄位
INST_IDS = {'TRUST10', 'TRUST3', 'INST3'}   # 需分法人籌碼（fetch_chip 的 trust/dealer 欄）
EXITS = {
    'TP18SL15': lambda df, d, p: sim_tpsl(df, d, p, 0.18, 0.15),
    'TRAIL15': lambda df, d, p: sim_trailing(df, d, p, 0.15),
}


def stock_features(df: pd.DataFrame, chip: pd.DataFrame, taiex: pd.DataFrame) -> dict:
    """date -> 特徵；只收 SIGNAL_START 起、暖身足夠（≥60 根）的交易日。"""
    out = {}
    for d in df['date']:
        if d >= SIGNAL_START:
            f = build_features({'date': d}, df, chip, taiex)
            if f is not None:
                out[d] = f
    return out


def run_filter(fn, feats: dict, ohlcv_map: dict, bench, ex: str, memo: dict) -> list[dict]:
    """條件 fn 在全池逐日觸發，同股未出場不重複進場。回傳 summarize 的輸入。
    memo 以 (股票, 訊號日, 出場策略) 快取出場模擬，各條件共用。"""
    trades = []
    for sid, fdays in feats.items():
        df = ohlcv_map[sid]
        busy_until = ''
        for d, f in fdays.items():          # 依日期遞增（build 時即按序插入）
            if d < busy_until or not fn(f, {}):
                continue
            key = (sid, d, ex)
            if key not in memo:
                entry_date, entry_price = get_entry(df, d)
                res = EXITS[ex](df, entry_date, entry_price) if entry_date and entry_price else None
                memo[key] = (entry_date, res)
            entry_date, res = memo[key]
            if entry_date is None:
                continue
            busy_until = res['exit'] if res else '99999999'   # 未結算＝持有至今
            trades.append({'date': d, 'result': res,
                           'bench': bench.ret_pct(entry_date, res['exit']) if res else None})
    return trades


def evaluate(trades: list[dict]) -> dict:
    """全期與分年統計。"""
    return {'all': summarize(trades),
            **{y: summarize([t for t in trades if t['date'][:4] == y]) for y in YEARS}}


def verdict(st: dict, base: dict) -> str:
    """依三項回測標準判定（只看主出場 TP18SL15）。"""
    s = st['all']
    if s.get('n', 0) < MIN_N:
        return '樣本不足'
    fails = []
    if s['wilson_lb'] <= base['all']['win_rate']:
        fails.append('①Wilson')
    for y in YEARS:
        if not st[y].get('n') or st[y]['win_rate'] <= base[y].get('win_rate', 0):
            fails.append(f'②{y}')
    if 'excess_return' not in s:
        return '❌ ' + '、'.join(fails) if fails else '⚠️ ①②過，0050 未判定'
    if s['excess_return'] <= 0:
        fails.append('③0050')
    return '❌ ' + '、'.join(fails) if fails else '✅ 通過'


def _pct(v):
    return f'{v:.0%}' if v is not None else '—'


def _yr(s):
    return f"{s['win_rate']:.0%}（{s['n']}）" if s.get('n') else '—'


def _vs(s):
    return f"{s['excess_return']:+.2f}pp" if 'excess_return' in s else '—'


def render_md(rows: list[dict], meta: dict) -> str:
    lines = [
        f"# 進場條件全池回測 {meta['start']}～{meta['end']}",
        '',
        f"- 追蹤池 {meta['universe']} 檔（有價格資料 {meta['priced']} 檔"
        + (f"；缺：{'、'.join(meta['missing'])}" if meta['missing'] else '') + '）',
        '- 價格：含息還原價（除權息、減資、分割、面額變更，見 CLAUDE.md「回測價格」）',
        '- 進場：條件成立日次日開盤；同股未出場不重複進場；最長持有 60 日',
        '- 基準：無條件進場（同追蹤池、隨機時點）',
        f"- 0050 同期比較：{'有' if meta['bench'] else '**無資料，標準③未判定**'}",
        f"- 分法人籌碼（投信／三大法人條件）：{meta['inst_chip']}/{meta['priced']} 檔有資料",
        '- 判定：① Wilson 下界 > 基準勝率 ② 2025、2026 各自勝過同年基準 ③ 超額報酬 vs 0050 > 0；'
        f'樣本 < {MIN_N} 不判定',
        '- 腳本：`scripts/backtest_condition_universe.py`',
        '',
        '## 主出場 TP18/SL15（依 Wilson 下界排序）',
        '',
        '| 條件 | 樣本 | 勝率 | Wilson下界 | 平均報酬 | PF | 2025 勝率（筆） | 2026 勝率（筆） | vs 0050 | 判定 |',
        '|------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|-----:|------|',
    ]
    for r in rows:
        s = r['TP18SL15']['all']
        if not s.get('n'):
            why = '缺分法人籌碼資料' if r['id'] in INST_IDS and not meta['inst_chip'] else '無觸發'
            lines.append(f"| {r['label']} | 0 | — | — | — | — | — | — | — | {why} |")
            continue
        lines.append(
            f"| {r['label']} | {s['n']} | {_pct(s['win_rate'])} | {_pct(s['wilson_lb'])} | "
            f"{s['avg_ret']:+.2f}% | {s['profit_factor']} | {_yr(r['TP18SL15']['2025'])} | "
            f"{_yr(r['TP18SL15']['2026'])} | {_vs(s)} | {r['verdict']} |")
    lines += ['', '## 對照：追蹤停損 15%', '',
              '| 條件 | 樣本 | 勝率 | 平均報酬 | PF | 2025 勝率（筆） | 2026 勝率（筆） | vs 0050 |',
              '|------|-----:|-----:|-----:|-----:|-----:|-----:|-----:|']
    for r in rows:
        s = r['TRAIL15']['all']
        if s.get('n'):
            lines.append(f"| {r['label']} | {s['n']} | {_pct(s['win_rate'])} | {s['avg_ret']:+.2f}% | "
                         f"{s['profit_factor']} | {_yr(r['TRAIL15']['2025'])} | "
                         f"{_yr(r['TRAIL15']['2026'])} | {_vs(s)} |")
    src = [r for r in rows if r['id'] in FILTER_SOURCES]
    lines += ['', '## 外部來源條件出處', ''] + [f"- **{r['label']}**：{FILTER_SOURCES[r['id']]}" for r in src]
    return '\n'.join(lines) + '\n'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true', help='強制重抓 FinMind 資料')
    args = ap.parse_args()

    universe = load_universe()
    taiex = fetch_ohlcv('TAIEX', args.refresh)
    bench = load_benchmark(args.refresh)
    print(f'追蹤池 {len(universe)} 檔，計算特徵中…')

    ohlcv_map, feats, missing, inst_chip = {}, {}, [], 0
    for n, sid in enumerate(universe, 1):
        df = clean_ohlcv(fetch_ohlcv(sid, args.refresh))
        if df.empty or len(df) < 80:
            missing.append(sid)
            continue
        df = compute_indicators(df)
        ohlcv_map[sid] = df
        chip = fetch_chip(sid, args.refresh)
        inst_chip += int(not chip.empty and chip['trust'].notna().any())
        feats[sid] = stock_features(df, chip, taiex)
        if n % 20 == 0:
            print(f'  進度 {n}/{len(universe)}')
    days = sorted({d for f in feats.values() for d in f})
    print(f'有效 {len(feats)} 檔、{len(days)} 個交易日（{days[0]}～{days[-1]}）')

    memo, rows = {}, []
    for fid, (label, fn) in FILTERS.items():
        if fid in SKIP:
            continue
        row = {'id': fid, 'label': '基準（無條件進場）' if fid == 'BASE' else label}
        for ex in EXITS:
            row[ex] = evaluate(run_filter(fn, feats, ohlcv_map, bench, ex, memo))
        rows.append(row)
    base = next(r for r in rows if r['id'] == 'BASE')['TP18SL15']
    for r in rows:
        r['verdict'] = '（基準）' if r['id'] == 'BASE' else verdict(r['TP18SL15'], base)
    rows.sort(key=lambda r: -(r['TP18SL15']['all'].get('wilson_lb') or -1))

    print(f"\n{'條件':<26}{'樣本':>6}{'勝率':>6}{'Wilson':>8}{'平均%':>8}"
          f"{'2025':>12}{'2026':>12}{'vs0050':>9}  判定")
    for r in rows:
        s = r['TP18SL15']['all']
        if s.get('n'):
            print(f"{r['label']:<26}{s['n']:>6}{s['win_rate']:>6.0%}{s['wilson_lb']:>8.0%}"
                  f"{s['avg_ret']:>8.2f}{_yr(r['TP18SL15']['2025']):>12}{_yr(r['TP18SL15']['2026']):>12}"
                  f"{_vs(s):>9}  {r['verdict']}")

    meta = {'start': days[0], 'end': days[-1], 'universe': len(universe), 'priced': len(feats),
            'missing': missing, 'bench': bool(bench.days), 'inst_chip': inst_chip}
    dest = ROOT / '回測數據' / f"進場條件全池回測_{days[0]}~{days[-1]}.md"
    dest.write_text(render_md(rows, meta), encoding='utf-8')
    print(f'\n已輸出 {dest.relative_to(ROOT)}')


if __name__ == '__main__':
    main()
