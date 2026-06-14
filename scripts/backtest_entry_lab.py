# -*- coding: utf-8 -*-
"""
進場策略實驗室（Entry Strategy Lab）
====================================
在「訊號日當天可得資訊」上測試多種進場過濾器，找出比現行基準更強的進場條件。
所有特徵計算嚴格使用 ≤ 訊號日的資料（無前視偏差），進場為訊號日次日開盤。

訊號來源：daily_reports/*/summary.json 的 qualified 清單
出場策略：TP18/SL15（現行最佳）、追蹤停損15%、MA10跌破
驗證：全期間統計 + 前後段 walk-forward 一致性

用法：python scripts/backtest_entry_lab.py [--refresh]
輸出：docs/entry_lab.json + console 報告
"""
import json
import math
import time
import argparse
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent

import pandas as pd
from datafeed import make_dataloader

CACHE_DIR = ROOT / 'backtest_cache'
CACHE_DIR.mkdir(exist_ok=True)

FETCH_START = '2024-10-01'   # 提前抓供 MA60 + RSI 暖身
MAX_HOLD_DAYS = 60
HARD_STOP = -0.20

_DL = None

def get_dl():
    global _DL
    if _DL is None:
        _DL = make_dataloader()
    return _DL


def fetch_ohlcv(sid: str, refresh=False) -> pd.DataFrame:
    """日 K（含成交量），本地快取。"""
    cf = CACHE_DIR / f'{sid}_ohlcv.csv'
    if cf.exists() and not refresh:
        return pd.read_csv(cf, dtype={'date': str})
    dl = get_dl()
    df = dl.taiwan_stock_daily(stock_id=sid, start_date=FETCH_START)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={'max': 'high', 'min': 'low', 'Trading_Volume': 'volume'})
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    out = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
    out.to_csv(cf, index=False)
    time.sleep(0.3)
    return out


def fetch_chip(sid: str, refresh=False) -> pd.DataFrame:
    """三大法人買賣超，本地快取。回傳 date, net（外資+投信）。"""
    cf = CACHE_DIR / f'{sid}_chip.csv'
    if cf.exists() and not refresh:
        return pd.read_csv(cf, dtype={'date': str})
    dl = get_dl()
    try:
        df = dl.taiwan_stock_institutional_investors(stock_id=sid, start_date=FETCH_START)
    except Exception as e:
        print(f'    {sid} 籌碼下載失敗：{e}')
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df['net'] = df['buy'] - df['sell']
    keep = df[df['name'].isin(['Foreign_Investor', 'Investment_Trust'])]
    out = keep.groupby('date', as_index=False)['net'].sum()
    out.to_csv(cf, index=False)
    time.sleep(0.3)
    return out


# ── 指標 ─────────────────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df['close']
    df['ma5'] = c.rolling(5).mean()
    df['ma10'] = c.rolling(10).mean()
    df['ma20'] = c.rolling(20).mean()
    df['ma60'] = c.rolling(60).mean()
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    # RSI14
    delta = c.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, 1e-9)
    df['rsi14'] = 100 - 100 / (1 + rs)
    # MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df['macd'] = ema12 - ema26
    df['macd_sig'] = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_sig']
    # KD
    low9 = df['low'].rolling(9).min()
    high9 = df['high'].rolling(9).max()
    rsv = (c - low9) / (high9 - low9).replace(0, 1e-9) * 100
    k_list, d_list = [], []
    k, d = 50.0, 50.0
    for v in rsv:
        if pd.isna(v):
            k_list.append(float('nan')); d_list.append(float('nan'))
            continue
        k = k * 2 / 3 + v / 3
        d = d * 2 / 3 + k / 3
        k_list.append(k); d_list.append(d)
    df['kd_k'] = k_list
    df['kd_d'] = d_list
    # 20 日收盤新高
    df['hh20'] = c.rolling(20).max()
    return df


# ── 訊號收集 ─────────────────────────────────────────────────────────────────

def load_signals() -> list[dict]:
    signals, seen = [], set()
    for d in sorted((ROOT / 'daily_reports').iterdir()):
        if not d.is_dir() or d.name.startswith('weekly'):
            continue
        sf = d / 'summary.json'
        if not sf.exists():
            continue
        data = json.loads(sf.read_text(encoding='utf-8'))
        strong = set(data.get('strong_sectors', []))
        for s in data.get('qualified', []):
            sid = s.get('id', '')
            if not sid or (d.name, sid) in seen:
                continue
            seen.add((d.name, sid))
            signals.append({
                'date': d.name,
                'stock_id': sid,
                'name': s.get('name', sid),
                'sector': s.get('sector', ''),
                'rsi5': s.get('rsi') or 0,
                'cv_sharpe': s.get('cv_sharpe') or 0,
                'sector_strong': s.get('sector', '') in strong,
            })
    return signals


# ── 特徵（訊號日 D 當天收盤後可得） ──────────────────────────────────────────

def build_features(sig, ohlcv: pd.DataFrame, chip: pd.DataFrame, taiex: pd.DataFrame):
    D = sig['date']
    df = ohlcv[ohlcv['date'] <= D]
    if len(df) < 60:
        return None
    r = df.iloc[-1]
    prev = df.iloc[-2]
    c = r['close']
    feat = {}
    feat['vol_ratio'] = r['volume'] / r['vol_ma20'] if r['vol_ma20'] else 0
    feat['ma_stack'] = bool(c > r['ma5'] > r['ma20'] > r['ma60'])
    feat['above_ma20'] = bool(c > r['ma20'])
    feat['dist_ma20'] = (c - r['ma20']) / r['ma20'] if r['ma20'] else 0
    feat['ma20_up'] = bool(r['ma20'] > df.iloc[-6]['ma20']) if len(df) >= 6 else False
    feat['break20'] = bool(c >= df.iloc[-21:-1]['close'].max()) if len(df) >= 21 else False
    feat['rsi14'] = r['rsi14']
    feat['macd_pos'] = bool(r['macd_hist'] > 0)
    feat['macd_rising'] = bool(r['macd_hist'] > prev['macd_hist'])
    feat['kd_gold'] = bool(r['kd_k'] > r['kd_d'])
    feat['kd_k'] = r['kd_k']
    feat['mom5'] = c / df.iloc[-6]['close'] - 1 if len(df) >= 6 else 0
    # 法人近 3 日淨買超
    if chip is not None and not chip.empty:
        c3 = chip[chip['date'] <= D].tail(3)
        feat['chip3'] = float(c3['net'].sum()) if not c3.empty else 0
    else:
        feat['chip3'] = 0
    # 大盤 regime：TAIEX 收盤 > MA60
    t = taiex[taiex['date'] <= D]
    if len(t) >= 60:
        feat['taiex_bull'] = bool(t.iloc[-1]['close'] > t['close'].rolling(60).mean().iloc[-1])
    else:
        feat['taiex_bull'] = True
    return feat


# ── 出場模擬 ─────────────────────────────────────────────────────────────────

def get_entry(ohlcv: pd.DataFrame, sig_date: str):
    nxt = ohlcv[ohlcv['date'] > sig_date]
    if nxt.empty:
        return None, None
    return nxt.iloc[0]['date'], float(nxt.iloc[0]['open'])


def sim_tpsl(ohlcv, entry_date, entry_price, tp=0.18, sl=0.15):
    rows = ohlcv[ohlcv['date'] > entry_date]
    tp_p, sl_p = entry_price * (1 + tp), entry_price * (1 - sl)
    for hold, (_, r) in enumerate(rows.iterrows(), 1):
        cl = r['close']
        if cl >= tp_p:
            return {'ret': cl / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'TP'}
        if cl <= sl_p:
            return {'ret': cl / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'SL'}
        if hold >= MAX_HOLD_DAYS:
            return {'ret': cl / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'MAX'}
    return None  # 未結算


def sim_trailing(ohlcv, entry_date, entry_price, sl=0.15):
    rows = ohlcv[ohlcv['date'] > entry_date]
    hwm = entry_price
    pending = False
    for hold, (_, r) in enumerate(rows.iterrows(), 1):
        if pending:
            return {'ret': r['open'] / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'TRAIL'}
        cl = r['close']
        if cl <= entry_price * (1 + HARD_STOP):
            return {'ret': cl / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'HARD'}
        if cl > hwm:
            hwm = cl
        if cl < hwm * (1 - sl):
            pending = True
        if hold >= MAX_HOLD_DAYS:
            return {'ret': cl / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'MAX'}
    return None


def sim_ma10(ohlcv, entry_date, entry_price):
    df = ohlcv.copy()
    df['ma10'] = df['close'].rolling(10).mean()
    rows = df[df['date'] > entry_date]
    pending = False
    for hold, (_, r) in enumerate(rows.iterrows(), 1):
        if pending:
            return {'ret': r['open'] / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'MA10'}
        cl = r['close']
        if cl <= entry_price * (1 + HARD_STOP):
            return {'ret': cl / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'HARD'}
        if not pd.isna(r['ma10']) and cl < r['ma10']:
            pending = True
        if hold >= MAX_HOLD_DAYS:
            return {'ret': cl / entry_price - 1, 'days': hold, 'exit': r['date'], 'why': 'MAX'}
    return None


# ── 過濾器定義 ───────────────────────────────────────────────────────────────

FILTERS = {
    'BASE':        ('基準（全部 qualified）', lambda f, s: True),
    'VOL12':       ('量比≥1.2',              lambda f, s: f['vol_ratio'] >= 1.2),
    'VOL15':       ('量比≥1.5',              lambda f, s: f['vol_ratio'] >= 1.5),
    'MASTACK':     ('多頭排列(C>5>20>60)',   lambda f, s: f['ma_stack']),
    'MA20UP':      ('站上MA20且MA20上揚',    lambda f, s: f['above_ma20'] and f['ma20_up']),
    'NOCHASE':     ('乖離MA20≤8%',           lambda f, s: f['dist_ma20'] <= 0.08),
    'PULLBACK':    ('乖離0~8%（近均線）',    lambda f, s: 0 <= f['dist_ma20'] <= 0.08),
    'BREAK20':     ('創20日收盤新高',        lambda f, s: f['break20']),
    'MACD':        ('MACD柱>0且增長',        lambda f, s: f['macd_pos'] and f['macd_rising']),
    'KDGOLD':      ('KD金叉(K>D)',           lambda f, s: f['kd_gold']),
    'RSI_MID':     ('RSI14∈[50,75]',         lambda f, s: 50 <= f['rsi14'] <= 75),
    'CHIP':        ('法人3日淨買超>0',       lambda f, s: f['chip3'] > 0),
    'REGIME':      ('大盤多頭(>MA60)',       lambda f, s: f['taiex_bull']),
    'SECTOR':      ('強勢族群',              lambda f, s: s['sector_strong']),
    'CV1':         ('cv_sharpe≥1.0',         lambda f, s: s['cv_sharpe'] >= 1.0),
    # 組合
    'MASTACK_VOL': ('多頭排列+量比≥1.2',     lambda f, s: f['ma_stack'] and f['vol_ratio'] >= 1.2),
    'BREAK_VOL':   ('突破20日高+量比≥1.5',   lambda f, s: f['break20'] and f['vol_ratio'] >= 1.5),
    'MACD_KD':     ('MACD增長+KD金叉',       lambda f, s: f['macd_pos'] and f['macd_rising'] and f['kd_gold']),
    'TREND_NOCHASE': ('多頭排列+乖離≤8%',    lambda f, s: f['ma_stack'] and f['dist_ma20'] <= 0.08),
    'CHIP_TREND':  ('法人買超+站上MA20',     lambda f, s: f['chip3'] > 0 and f['above_ma20']),
    'TRIPLE':      ('多頭排列+量比1.2+MACD', lambda f, s: f['ma_stack'] and f['vol_ratio'] >= 1.2 and f['macd_pos']),
    'VOL_AND_MACD': ('量比1.2 且 MACD增長',  lambda f, s: f['vol_ratio'] >= 1.2 and f['macd_pos'] and f['macd_rising']),
    'VOL_OR_MACD': ('量比1.2 或 MACD增長',   lambda f, s: f['vol_ratio'] >= 1.2 or (f['macd_pos'] and f['macd_rising'])),
    'VOL_OR_BREAK': ('量比1.2 或 突破20日高', lambda f, s: f['vol_ratio'] >= 1.2 or f['break20']),
}


def entry_score(f, s) -> int:
    """綜合評分 0-7：每符合一項 +1。"""
    return sum([
        f['vol_ratio'] >= 1.2,
        f['ma_stack'],
        f['macd_pos'] and f['macd_rising'],
        f['kd_gold'],
        f['chip3'] > 0,
        f['taiex_bull'],
        f['dist_ma20'] <= 0.08,
    ])


# ── 統計 ─────────────────────────────────────────────────────────────────────

def wilson_lb(wins, n, z=1.96):
    if n == 0:
        return 0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def summarize(trades: list[dict]) -> dict:
    settled = [t for t in trades if t['result'] is not None]
    if not settled:
        return {'n': 0}
    rets = [t['result']['ret'] for t in settled]
    wins = sum(1 for r in rets if r > 0)
    gains = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r <= 0)
    return {
        'n': len(settled),
        'win_rate': round(wins / len(settled), 4),
        'wilson_lb': round(wilson_lb(wins, len(settled)), 4),
        'avg_ret': round(sum(rets) / len(rets) * 100, 2),
        'median_ret': round(sorted(rets)[len(rets) // 2] * 100, 2),
        'profit_factor': round(gains / losses, 2) if losses > 0 else 99,
        'expectancy': round(sum(rets) / len(rets) * 100, 2),
        'avg_days': round(sum(t['result']['days'] for t in settled) / len(settled), 1),
        'worst': round(min(rets) * 100, 2),
        'best': round(max(rets) * 100, 2),
    }


# ── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true', help='強制重抓 FinMind 資料')
    args = ap.parse_args()

    signals = load_signals()
    sids = sorted(set(s['stock_id'] for s in signals))
    print(f'訊號 {len(signals)} 筆，個股 {len(sids)} 檔')

    print('抓取價格與籌碼資料…')
    ohlcv_map, chip_map = {}, {}
    for sid in sids:
        df = fetch_ohlcv(sid, args.refresh)
        ohlcv_map[sid] = compute_indicators(df) if not df.empty else df
        chip_map[sid] = fetch_chip(sid, args.refresh)
        print(f'  {sid}: {len(ohlcv_map[sid])} 天價格, {len(chip_map[sid])} 天籌碼')
    taiex = fetch_ohlcv('TAIEX', args.refresh)
    print(f'  TAIEX: {len(taiex)} 天')

    # 建立特徵 + 三種出場結果
    enriched = []
    for sig in signals:
        ohlcv = ohlcv_map.get(sig['stock_id'])
        if ohlcv is None or ohlcv.empty:
            continue
        feat = build_features(sig, ohlcv, chip_map.get(sig['stock_id']), taiex)
        if feat is None:
            continue
        entry_date, entry_price = get_entry(ohlcv, sig['date'])
        if entry_date is None or not entry_price:
            continue
        enriched.append({
            'sig': sig, 'feat': feat,
            'entry_date': entry_date, 'entry_price': entry_price,
            'exits': {
                'TP18SL15': sim_tpsl(ohlcv, entry_date, entry_price, 0.18, 0.15),
                'TRAIL15': sim_trailing(ohlcv, entry_date, entry_price, 0.15),
                'MA10': sim_ma10(ohlcv, entry_date, entry_price),
            },
            'score': entry_score(feat, sig),
        })
    print(f'有效樣本 {len(enriched)} 筆')

    dates = sorted(e['sig']['date'] for e in enriched)
    mid = dates[len(dates) // 2]
    print(f'walk-forward 分割點：{mid}')

    results = {}
    for fid, (label, fn) in FILTERS.items():
        picked = [e for e in enriched if fn(e['feat'], e['sig'])]
        row = {'label': label, 'picked': len(picked), 'exits': {}}
        for ex in ['TP18SL15', 'TRAIL15', 'MA10']:
            trades = [{'result': e['exits'][ex]} for e in picked]
            row['exits'][ex] = summarize(trades)
            # walk-forward
            t1 = [{'result': e['exits'][ex]} for e in picked if e['sig']['date'] <= mid]
            t2 = [{'result': e['exits'][ex]} for e in picked if e['sig']['date'] > mid]
            row['exits'][ex]['wf_front'] = summarize(t1)
            row['exits'][ex]['wf_back'] = summarize(t2)
        results[fid] = row

    # 評分分層
    tier_stats = {}
    for lo, hi, tier in [(5, 7, 'A(5-7分)'), (3, 4, 'B(3-4分)'), (0, 2, 'C(0-2分)')]:
        picked = [e for e in enriched if lo <= e['score'] <= hi]
        tier_stats[tier] = {
            'picked': len(picked),
            'TP18SL15': summarize([{'result': e['exits']['TP18SL15']} for e in picked]),
            'TRAIL15': summarize([{'result': e['exits']['TRAIL15']} for e in picked]),
        }

    # ── 報告 ──
    print('\n════════ 進場過濾器 × TP18SL15 ════════')
    print(f"{'過濾器':<24}{'樣本':>5}{'勝率':>7}{'Wilson下界':>10}{'平均%':>8}{'PF':>6}{'天數':>6}")
    base = results['BASE']['exits']['TP18SL15']
    for fid, row in sorted(results.items(), key=lambda kv: -(kv[1]['exits']['TP18SL15'].get('win_rate') or 0)):
        s = row['exits']['TP18SL15']
        if s.get('n', 0) == 0:
            continue
        mark = ' ★' if s['wilson_lb'] > (base.get('win_rate') or 0) else ''
        print(f"{row['label']:<24}{s['n']:>5}{s['win_rate']:>7.0%}{s['wilson_lb']:>10.0%}"
              f"{s['avg_ret']:>8.2f}{s['profit_factor']:>6.2f}{s['avg_days']:>6.1f}{mark}")

    print('\n════════ 評分分層（7 項條件計分） ════════')
    for tier, st in tier_stats.items():
        s = st['TP18SL15']
        if s.get('n'):
            print(f"{tier}: 樣本={s['n']} 勝率={s['win_rate']:.0%} 平均={s['avg_ret']}% PF={s['profit_factor']}")

    out = {
        'generated_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        'period': {'start': dates[0], 'end': dates[-1], 'signals': len(enriched), 'wf_split': mid},
        'baseline': {'exits': results['BASE']['exits']},
        'filters': results,
        'tiers': tier_stats,
        'score_items': ['量比≥1.2', '多頭排列', 'MACD柱>0且增長', 'KD金叉', '法人3日買超', '大盤>MA60', '乖離MA20≤8%'],
    }
    (ROOT / 'docs' / 'entry_lab.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print('\n已輸出 docs/entry_lab.json')


if __name__ == '__main__':
    main()
