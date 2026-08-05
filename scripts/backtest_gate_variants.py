# -*- coding: utf-8 -*-
"""
進場閘門變體回測（Gate Variants Lab）
=====================================
針對現行 `passes_gate` 三道閘門（大盤 MA60／族群強勢／乖離 MA10≤2%）逐一替換，
量化「放寬 vs 收緊」對勝率、期望值與**出手頻率**的影響。

現行閘門（scripts/position_tracker.py:passes_gate）：
  1. 大盤多頭  TAIEX 收盤 > MA60
  2. 多頭排列  收盤 > MA5 > MA20 > MA60
  3. 族群強勢  該族群 avg_ret_20d > 3
  4. 乖離      (收盤-MA10)/MA10 ≤ 2%

三組變體各自單獨替換（其餘維持現行），最後跑幾組綜合候選：
  乖離   B0 不限 / B1 現行MA10≤2% / B2 MA20≤8% / B3 MA20≤5% / B4~B6 ATR 正規化
         （多頭排列已隱含 收盤>MA20，故乖離 MA20 恆為正，不需再設下界）
  大盤   M0 不限 / M1 現行>MA60 / M2 >MA20 / M3 MA20斜率>0 / M4 廣度>50%
  族群   S0 不限 / S1 現行avg_ret>3 / S2 排名前1/3 / S3 排名前1/2

出場沿用 production 自適應（上漲期→HYBRID，其餘→TP15SL15），另附 TRAIL15 對照。
資料一律取 ≤ 訊號日，進場為訊號日次日開盤，無前視偏差。

用法：python scripts/backtest_gate_variants.py
輸出：docs/gate_variants.json + console 報告
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

import pandas as pd

from backtest_entry_lab import fetch_ohlcv, compute_indicators, CACHE_DIR
from regime_exit_analysis import clean_ohlcv, classify_regimes, sim_hybrid
from backtest_combo_search import sim_tpsl, sim_trailing

# 快取覆蓋 2024-10 起；MA60 暖身後訊號自 2025-01 起算
SIGNAL_START = '20250101'


def load_ohlcv(sid: str) -> pd.DataFrame:
    """優先讀 backtest_cache；無快取才連線抓取（離線環境則略過該檔）。"""
    if not (CACHE_DIR / f'{sid}_ohlcv.csv').exists():
        try:
            return fetch_ohlcv(sid)
        except Exception as e:
            print(f'  略過 {sid}：無快取且無法取數（{e}）')
            return pd.DataFrame()
    return fetch_ohlcv(sid)


# ── 訊號與族群強弱 ────────────────────────────────────────────────────────────

def load_signals() -> list[dict]:
    """從 daily_reports 收集所有 BUY 訊號，附帶當日族群強弱與族群報酬排名。"""
    signals, seen = [], set()
    for d in sorted((ROOT / 'daily_reports').iterdir()):
        if not d.is_dir() or len(d.name) != 8 or not d.name.isdigit():
            continue
        if d.name < SIGNAL_START:
            continue
        sf = d / 'summary.json'
        if not sf.exists():
            continue
        data = json.loads(sf.read_text(encoding='utf-8'))
        sectors = data.get('sectors') or {}
        strong = set(data.get('strong_sectors') or [])
        # 當日族群依 20 日報酬排名（1 = 最強），供相對排名閘門使用
        rets = {name: (sd.get('avg_ret_20d') or 0) for name, sd in sectors.items()}
        order = sorted(rets, key=lambda k: rets[k], reverse=True)
        rank = {name: i + 1 for i, name in enumerate(order)}
        n_sec = len(order)
        for sec_name, sec_data in sectors.items():
            for st in sec_data.get('stocks') or []:
                if st.get('signal') != 'BUY':
                    continue
                sid = st.get('id', '')
                if not sid or (d.name, sid) in seen:
                    continue
                seen.add((d.name, sid))
                signals.append({
                    'date':          d.name,
                    'stock_id':      sid,
                    'name':          st.get('name', sid),
                    'sector':        sec_name,
                    'sector_strong': sec_name in strong,
                    'sector_pct':    rank[sec_name] / n_sec if n_sec else 1.0,
                })
    return signals


# ── 特徵（嚴格 ≤ 訊號日） ─────────────────────────────────────────────────────

def build_features(sig, ohlcv: pd.DataFrame, taiex: pd.DataFrame, breadth: dict):
    df = ohlcv[ohlcv['date'] <= sig['date']]
    if len(df) < 62:
        return None
    r, prev = df.iloc[-1], df.iloc[-2]
    c = float(r['close'])
    if any(pd.isna(r[k]) for k in ('ma5', 'ma10', 'ma20', 'ma60')):
        return None

    # ATR14（Wilder true range 的簡單均值即可，僅作波動尺度）
    tail = df.tail(15).copy()
    pc = tail['close'].shift(1)
    tr = pd.concat([tail['high'] - tail['low'],
                    (tail['high'] - pc).abs(),
                    (tail['low'] - pc).abs()], axis=1).max(axis=1)
    atr14 = float(tr.tail(14).mean())

    vol_ma = float(r['vol_ma20']) if not pd.isna(r['vol_ma20']) else 0

    t = taiex[taiex['date'] <= sig['date']]
    t_ma20 = t['close'].rolling(20).mean()
    t_ma60 = t['close'].rolling(60).mean()
    t_close = float(t.iloc[-1]['close'])

    return {
        'ma_stack':    bool(c > r['ma5'] > r['ma20'] > r['ma60']),
        'bias_ma10':   (c - r['ma10']) / r['ma10'],
        'bias_ma20':   (c - r['ma20']) / r['ma20'],
        'bias_atr':    (c - float(r['ma10'])) / atr14 if atr14 > 0 else 99.0,
        'vol_ratio':   float(r['volume']) / vol_ma if vol_ma else 0,
        'break20':     bool(c >= float(df.iloc[-21:-1]['close'].max())),
        'macd_rising': bool(r['macd_hist'] > prev['macd_hist']),
        'taiex_ma60':  bool(t_close > float(t_ma60.iloc[-1])),
        'taiex_ma20':  bool(t_close > float(t_ma20.iloc[-1])),
        'taiex_slope': bool(float(t_ma20.iloc[-1]) > float(t_ma20.iloc[-6])),
        'breadth':     breadth.get(sig['date'], 0.5),
    }


def compute_breadth(ohlcv_map: dict) -> dict:
    """全快取股池中「收盤站上 MA20」的家數比，作為市場廣度。"""
    up = defaultdict(int)
    tot = defaultdict(int)
    for df in ohlcv_map.values():
        if df.empty:
            continue
        sub = df.dropna(subset=['ma20'])
        for d, c, m in zip(sub['date'], sub['close'], sub['ma20']):
            tot[d] += 1
            if c > m:
                up[d] += 1
    return {d: up[d] / tot[d] for d in tot if tot[d] >= 20}


# ── 閘門定義 ──────────────────────────────────────────────────────────────────

BIAS_GATES = {
    'B0': ('乖離不限',            lambda f: True),
    'B1': ('MA10 ≤2%（現行）',    lambda f: f['bias_ma10'] <= 0.02),
    'B2': ('MA20 ≤8%',            lambda f: f['bias_ma20'] <= 0.08),
    'B3': ('MA20 ≤5%',            lambda f: f['bias_ma20'] <= 0.05),
    'B4': ('ATR：≤1.0 ATR',       lambda f: f['bias_atr'] <= 1.0),
    'B5': ('ATR：≤1.5 ATR',       lambda f: f['bias_atr'] <= 1.5),
    'B6': ('ATR：≤2.0 ATR',       lambda f: f['bias_atr'] <= 2.0),
}

MARKET_GATES = {
    'M0': ('大盤不限',            lambda f: True),
    'M1': ('TAIEX >MA60（現行）', lambda f: f['taiex_ma60']),
    'M2': ('TAIEX >MA20',         lambda f: f['taiex_ma20']),
    'M3': ('TAIEX MA20 斜率>0',   lambda f: f['taiex_slope']),
    'M4': ('廣度 >50%',           lambda f: f['breadth'] > 0.5),
}

SECTOR_GATES = {
    'S0': ('族群不限',            lambda s: True),
    'S1': ('avg_ret>3（現行）',   lambda s: s['sector_strong']),
    'S2': ('族群排名前 1/3',      lambda s: s['sector_pct'] <= 1 / 3),
    'S3': ('族群排名前 1/2',      lambda s: s['sector_pct'] <= 0.5),
}

# 動能替代條件（entry_lab 顯示比乖離/族群更有鑑別力）
MOMENTUM = {
    'A0': ('動能不限',            lambda f: True),
    'A1': ('量比≥1.2 或 突破20日高', lambda f: f['vol_ratio'] >= 1.2 or f['break20']),
    'A2': ('量比≥1.2 或 MACD增長',   lambda f: f['vol_ratio'] >= 1.2 or f['macd_rising']),
}


# ── 統計 ──────────────────────────────────────────────────────────────────────

def wilson_lb(wins, n, z=1.96):
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def summarize(results: list, months: float) -> dict | None:
    settled = [r for r in results if r is not None]
    if not settled:
        return None
    rets = [r['ret'] for r in settled]
    wins = sum(1 for r in rets if r > 0)
    gains = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r <= 0)
    return {
        'n':        len(settled),
        'open':     len(results) - len(settled),
        'per_mo':   round(len(results) / months, 1) if months else 0,
        'win_rate': round(wins / len(settled), 4),
        'wilson':   round(wilson_lb(wins, len(settled)), 4),
        'avg_ret':  round(sum(rets) / len(rets) * 100, 2),
        'pf':       round(gains / losses, 2) if losses > 0 else 99.0,
        'avg_days': round(sum(r['days'] for r in settled) / len(settled), 1),
        'sum_ret':  round(sum(rets) * 100, 1),
    }


def fmt(s):
    if not s:
        return f'{"—":>62}'
    return (f'{s["n"]:>5}{s["open"]:>5}{s["per_mo"]:>7.1f}{s["win_rate"]:>7.0%}'
            f'{s["wilson"]:>7.0%}{s["avg_ret"]:>8.2f}{s["pf"]:>6.2f}'
            f'{s["avg_days"]:>6.1f}{s["sum_ret"]:>9.0f}')


HDR = (f'{"變體":<30}{"n":>5}{"未結":>5}{"筆/月":>7}{"勝率":>7}'
       f'{"Wil.":>7}{"avg%":>8}{"PF":>6}{"天數":>6}{"總報酬%":>9}')


def main():
    signals = load_signals()
    sids = sorted({s['stock_id'] for s in signals})
    print(f'訊號 {len(signals)} 筆（{signals[0]["date"]}~{signals[-1]["date"]}），個股 {len(sids)} 檔')

    ohlcv_map = {}
    for sid in sids:
        df = clean_ohlcv(load_ohlcv(sid))
        ohlcv_map[sid] = compute_indicators(df) if not df.empty else pd.DataFrame()
    taiex = clean_ohlcv(load_ohlcv('TAIEX'))
    regimes = classify_regimes(taiex)
    breadth = compute_breadth(ohlcv_map)

    rows = []
    for sig in signals:
        ohlcv = ohlcv_map.get(sig['stock_id'])
        if ohlcv is None or ohlcv.empty:
            continue
        feat = build_features(sig, ohlcv, taiex, breadth)
        if feat is None:
            continue
        nxt = ohlcv[ohlcv['date'] > sig['date']]
        if nxt.empty or pd.isna(nxt.iloc[0]['open']) or float(nxt.iloc[0]['open']) <= 0:
            continue
        entry_date = nxt.iloc[0]['date']
        entry_price = float(nxt.iloc[0]['open'])
        regime = regimes.get(sig['date'], '暖身')
        exit_tp = sim_tpsl(ohlcv, entry_date, entry_price, 0.15, 0.15)
        exit_hyb = sim_hybrid(ohlcv, entry_date, entry_price)
        rows.append({
            'date': sig['date'], 'sig': sig, 'feat': feat, 'regime': regime,
            'ada': exit_hyb if regime == '上漲期' else exit_tp,
            'trail': sim_trailing(ohlcv, entry_date, entry_price, 0.15),
        })

    dates = sorted(r['date'] for r in rows)
    mid = dates[len(dates) // 2]
    span_mo = (pd.Timestamp(dates[-1]) - pd.Timestamp(dates[0])).days / 30.44
    print(f'有效樣本 {len(rows)} 筆，區間 {dates[0]}~{dates[-1]}（{span_mo:.1f} 個月）\n')

    def pick(bias, market, sector, momo='A0'):
        bf = BIAS_GATES[bias][1]
        mf = MARKET_GATES[market][1]
        sf = SECTOR_GATES[sector][1]
        af = MOMENTUM[momo][1]
        return [r for r in rows
                if r['feat']['ma_stack'] and bf(r['feat']) and mf(r['feat'])
                and sf(r['sig']) and af(r['feat'])]

    def evaluate(subset, key='ada'):
        full = summarize([r[key] for r in subset], span_mo)
        front = summarize([r[key] for r in subset if r['date'] <= mid], span_mo / 2)
        back = summarize([r[key] for r in subset if r['date'] > mid], span_mo / 2)
        return {'full': full, 'front': front, 'back': back}

    out = {
        'generated_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        'period': {'start': dates[0], 'end': dates[-1], 'months': round(span_mo, 1),
                   'signals': len(rows), 'wf_split': mid},
        'baseline': {}, 'bias': {}, 'market': {}, 'sector': {}, 'combos': {},
    }

    def block(title, cases, store):
        print(f'\n════════ {title} ════════')
        print(HDR)
        print('─' * len(HDR))
        for label, subset in cases:
            res = evaluate(subset)
            store[label] = res
            print(f'{label:<30}{fmt(res["full"])}')
            f1, f2 = res['front'], res['back']
            wf = (f'    walk-forward 前段 {f1["win_rate"]:.0%}/{f1["avg_ret"]:+.1f}%'
                  f'　後段 {f2["win_rate"]:.0%}/{f2["avg_ret"]:+.1f}%') if f1 and f2 else \
                 '    walk-forward 樣本不足'
            print(wf)

    # 0. 基準
    block('基準對照（出場：自適應）', [
        ('全 BUY（無任何閘門）', rows),
        ('僅多頭排列', pick('B0', 'M0', 'S0')),
        ('現行 production（M1+S1+B1）', pick('B1', 'M1', 'S1')),
    ], out['baseline'])

    # 1. 乖離變體（其餘維持現行）
    block('乖離變體｜固定 大盤>MA60 + 族群強勢', [
        (f'{k} {v[0]}', pick(k, 'M1', 'S1')) for k, v in BIAS_GATES.items()
    ], out['bias'])

    # 2. 大盤變體
    block('大盤變體｜固定 族群強勢 + 乖離 MA10≤2%', [
        (f'{k} {v[0]}', pick('B1', k, 'S1')) for k, v in MARKET_GATES.items()
    ], out['market'])
    block('大盤變體｜固定 族群強勢 + 乖離不限', [
        (f'{k} {v[0]}（乖離不限）', pick('B0', k, 'S1')) for k, v in MARKET_GATES.items()
    ], out['market'])

    # 3. 族群變體
    block('族群變體｜固定 大盤>MA60 + 乖離 MA10≤2%', [
        (f'{k} {v[0]}', pick('B1', 'M1', k)) for k, v in SECTOR_GATES.items()
    ], out['sector'])

    # 4. 綜合候選
    block('綜合候選（放寬乖離／族群，改用動能）', [
        ('現行 production',            pick('B1', 'M1', 'S1')),
        ('放寬乖離 MA20≤8%',           pick('B2', 'M1', 'S1')),
        ('乖離ATR≤1.5 + 族群前1/3',    pick('B5', 'M1', 'S2')),
        ('乖離ATR≤1.5 + 族群不限',     pick('B5', 'M1', 'S0')),
        ('乖離MA20≤8% + 族群前1/2',    pick('B2', 'M1', 'S3')),
        ('乖離MA20≤8% + 動能A1',       pick('B2', 'M1', 'S0', 'A1')),
        ('動能A1 + 大盤MA60（無乖離無族群）', pick('B0', 'M1', 'S0', 'A1')),
        ('動能A2 + 大盤MA60（無乖離無族群）', pick('B0', 'M1', 'S0', 'A2')),
        ('動能A1 + 乖離ATR≤2.0 + 大盤MA20', pick('B6', 'M2', 'S0', 'A1')),
        ('動能A1 + 廣度>50%',          pick('B0', 'M4', 'S0', 'A1')),
    ], out['combos'])

    # 5. 出場敏感度：同一組進場條件換 TRAIL15
    print('\n════════ 出場敏感度（同進場條件 × 追蹤停損15%） ════════')
    print(HDR)
    print('─' * len(HDR))
    trail = {}
    for label, subset in [('現行 production', pick('B1', 'M1', 'S1')),
                          ('乖離ATR≤1.5 + 族群前1/3', pick('B5', 'M1', 'S2')),
                          ('動能A1 + 大盤MA60', pick('B0', 'M1', 'S0', 'A1'))]:
        res = evaluate(subset, 'trail')
        trail[label] = res
        print(f'{label:<30}{fmt(res["full"])}')
    out['trail_exit'] = trail

    dest = ROOT / 'docs' / 'gate_variants.json'
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已輸出 {dest}')


if __name__ == '__main__':
    main()
