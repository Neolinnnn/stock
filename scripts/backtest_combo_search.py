# -*- coding: utf-8 -*-
"""
最強組合搜索：進場過濾器 × 出場策略 全矩陣搜索（3 年資料）
=============================================================
訊號來源：daily_reports/*/summary.json → sectors BUY（3 年回填）
進場：訊號日次日開盤
測試矩陣：24 種過濾器 × 11 種出場策略 = 264 組

Walk-forward 以中位日期分前後兩段驗證穩健性。
評分公式：Wilson下界 × avg_ret（正值） × min(PF, 5)

用法：python scripts/backtest_combo_search.py [--refresh] [--min-n 20]
輸出：docs/combo_search.json + console TOP-25 排行
"""
import json
import math
import argparse
from pathlib import Path

ROOT = Path(__file__).parent.parent

import sys
sys.path.insert(0, str(ROOT / 'scripts'))

import pandas as pd
from datafeed import finmind_fetch

CACHE_DIR = ROOT / 'backtest_cache'
CACHE_DIR.mkdir(exist_ok=True)

FETCH_START  = '2022-12-01'   # MA60 暖身（訊號從 2023-06 起）
MAX_HOLD_DAYS = 60
HARD_STOP    = -0.20


# ── 資料抓取 ──────────────────────────────────────────────────────────────────

def fetch_ohlcv(sid: str, refresh=False) -> pd.DataFrame:
    cf = CACHE_DIR / f'{sid}_ohlcv3y.csv'
    if cf.exists() and not refresh:
        return pd.read_csv(cf, dtype={'date': str})
    df = finmind_fetch('taiwan_stock_daily', stock_id=sid, start_date=FETCH_START)
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.rename(columns={'max': 'high', 'min': 'low', 'Trading_Volume': 'volume'})
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    out = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
    out.to_csv(cf, index=False)
    return out


def fetch_chip(sid: str, refresh=False) -> pd.DataFrame:
    cf = CACHE_DIR / f'{sid}_chip3y.csv'
    if cf.exists() and not refresh:
        return pd.read_csv(cf, dtype={'date': str})
    try:
        df = finmind_fetch('taiwan_stock_institutional_investors',
                           stock_id=sid, start_date=FETCH_START)
    except Exception:
        return pd.DataFrame()
    if df is None or df.empty:
        return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df['net']  = df['buy'] - df['sell']
    keep = df[df['name'].isin(['Foreign_Investor', 'Investment_Trust'])]
    out  = keep.groupby('date', as_index=False)['net'].sum()
    out.to_csv(cf, index=False)
    return out


# ── 指標計算 ──────────────────────────────────────────────────────────────────

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df['close']
    df['ma5']     = c.rolling(5).mean()
    df['ma20']    = c.rolling(20).mean()
    df['ma60']    = c.rolling(60).mean()
    df['vol_ma20']= df['volume'].rolling(20).mean()
    # RSI14
    delta = c.diff()
    gain  = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss  = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    df['rsi14'] = 100 - 100 / (1 + gain / loss.replace(0, 1e-9))
    # MACD
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df['macd']      = ema12 - ema26
    df['macd_sig']  = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist'] = df['macd'] - df['macd_sig']
    # KD（3/1 平滑）
    lo9 = df['low'].rolling(9).min()
    hi9 = df['high'].rolling(9).max()
    rsv = (c - lo9) / (hi9 - lo9).replace(0, 1e-9) * 100
    k, d, ks, ds = 50.0, 50.0, [], []
    for v in rsv:
        if pd.isna(v):
            ks.append(float('nan')); ds.append(float('nan')); continue
        k = k * 2/3 + v / 3
        d = d * 2/3 + k / 3
        ks.append(k); ds.append(d)
    df['kd_k'] = ks
    df['kd_d'] = ds
    return df


# ── 訊號收集（sectors BUY） ───────────────────────────────────────────────────

def load_signals() -> list[dict]:
    signals, seen = [], set()
    for d in sorted((ROOT / 'daily_reports').iterdir()):
        if not d.is_dir() or len(d.name) != 8 or not d.name.isdigit():
            continue
        sf = d / 'summary.json'
        if not sf.exists():
            continue
        data  = json.loads(sf.read_text(encoding='utf-8'))
        strong = set(data.get('strong_sectors', []))
        for sec_name, sec_data in data.get('sectors', {}).items():
            for st in sec_data.get('stocks', []):
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
                    'cv_sharpe':     st.get('cv_sharpe', 0) or 0,
                })
    return signals


# ── 特徵計算（嚴格使用 ≤ 訊號日的資料，無前視偏差） ─────────────────────────

def build_features(sig, ohlcv: pd.DataFrame, chip: pd.DataFrame, taiex: pd.DataFrame):
    D  = sig['date']
    df = ohlcv[ohlcv['date'] <= D]
    if len(df) < 62:   # MA60 + 2 天緩衝
        return None
    r, prev = df.iloc[-1], df.iloc[-2]
    c = float(r['close'])

    feat = {}
    vol_ma = r['vol_ma20']
    feat['vol_ratio']  = r['volume'] / vol_ma if vol_ma else 0
    feat['ma_stack']   = bool(c > r['ma5'] > r['ma20'] > r['ma60'])
    feat['above_ma20'] = bool(c > r['ma20'])
    feat['dist_ma20']  = (c - r['ma20']) / r['ma20'] if r['ma20'] else 0
    feat['ma20_up']    = bool(r['ma20'] > df.iloc[-6]['ma20']) if len(df) >= 6 else False
    feat['break20']    = bool(c >= df.iloc[-21:-1]['close'].max()) if len(df) >= 21 else False
    feat['rsi14']      = float(r['rsi14']) if not pd.isna(r['rsi14']) else 50
    feat['macd_pos']   = bool(r['macd_hist'] > 0)
    feat['macd_rising']= bool(r['macd_hist'] > prev['macd_hist'])
    feat['kd_gold']    = bool(r['kd_k'] > r['kd_d'])

    if chip is not None and not chip.empty:
        c3 = chip[chip['date'] <= D].tail(3)
        feat['chip3'] = float(c3['net'].sum()) if not c3.empty else 0
    else:
        feat['chip3'] = 0

    t = taiex[taiex['date'] <= D]
    if len(t) >= 60:
        ma60_val = t['close'].rolling(60).mean().iloc[-1]
        feat['taiex_bull'] = bool(float(t.iloc[-1]['close']) > float(ma60_val))
    else:
        feat['taiex_bull'] = True

    return feat


def entry_score(f) -> int:
    return sum([
        f['vol_ratio']   >= 1.2,
        f['ma_stack'],
        f['macd_pos'] and f['macd_rising'],
        f['kd_gold'],
        f['chip3']       > 0,
        f['taiex_bull'],
        f['dist_ma20']   <= 0.08,
    ])


# ── 過濾器 ────────────────────────────────────────────────────────────────────

FILTERS = {
    'BASE':          ('基準（全部 BUY）',          lambda f, s: True),
    'VOL12':         ('量比≥1.2',                  lambda f, s: f['vol_ratio'] >= 1.2),
    'VOL15':         ('量比≥1.5',                  lambda f, s: f['vol_ratio'] >= 1.5),
    'MASTACK':       ('多頭排列(C>MA5>MA20>MA60)',  lambda f, s: f['ma_stack']),
    'MA20UP':        ('站上MA20且MA20上揚',         lambda f, s: f['above_ma20'] and f['ma20_up']),
    'NOCHASE':       ('乖離MA20≤8%',               lambda f, s: f['dist_ma20'] <= 0.08),
    'PULLBACK':      ('乖離0~8%（近均線）',         lambda f, s: 0 <= f['dist_ma20'] <= 0.08),
    'BREAK20':       ('創20日收盤新高',             lambda f, s: f['break20']),
    'MACD':          ('MACD柱>0且增長',             lambda f, s: f['macd_pos'] and f['macd_rising']),
    'KDGOLD':        ('KD金叉(K>D)',                lambda f, s: f['kd_gold']),
    'RSI_MID':       ('RSI14∈[50,75]',              lambda f, s: 50 <= f['rsi14'] <= 75),
    'CHIP':          ('法人3日淨買超>0',            lambda f, s: f['chip3'] > 0),
    'REGIME':        ('大盤多頭(>MA60)',            lambda f, s: f['taiex_bull']),
    'SECTOR':        ('強勢族群',                   lambda f, s: s['sector_strong']),
    'MASTACK_VOL':   ('多頭排列+量比≥1.2',          lambda f, s: f['ma_stack'] and f['vol_ratio'] >= 1.2),
    'BREAK_VOL':     ('突破20日高+量比≥1.5',        lambda f, s: f['break20'] and f['vol_ratio'] >= 1.5),
    'MACD_KD':       ('MACD增長+KD金叉',            lambda f, s: f['macd_pos'] and f['macd_rising'] and f['kd_gold']),
    'TREND_NOCHASE': ('多頭排列+乖離≤8%',           lambda f, s: f['ma_stack'] and f['dist_ma20'] <= 0.08),
    'CHIP_TREND':    ('法人買超+站上MA20',          lambda f, s: f['chip3'] > 0 and f['above_ma20']),
    'TRIPLE':        ('多頭+量比1.2+MACD增長',      lambda f, s: f['ma_stack'] and f['vol_ratio'] >= 1.2 and f['macd_pos']),
    'SCORE_5':       ('評分≥5（7維綜合）',          lambda f, s: entry_score(f) >= 5),
    'SCORE_4':       ('評分≥4（7維綜合）',          lambda f, s: entry_score(f) >= 4),
    'REGIME_TREND':  ('大盤多頭+多頭排列',          lambda f, s: f['taiex_bull'] and f['ma_stack']),
    'FULL5':         ('多頭+量+MACD+KD+大盤',       lambda f, s: (f['ma_stack'] and f['vol_ratio'] >= 1.2
                                                                   and f['macd_pos'] and f['kd_gold']
                                                                   and f['taiex_bull'])),
}


# ── 出場模擬 ──────────────────────────────────────────────────────────────────

def sim_tpsl(ohlcv, entry_date, entry_price, tp, sl):
    rows = ohlcv[ohlcv['date'] > entry_date]
    tp_p, sl_p = entry_price * (1 + tp), entry_price * (1 - sl)
    for hold, (_, r) in enumerate(rows.iterrows(), 1):
        cl = float(r['close'])
        if cl >= tp_p:
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'TP'}
        if cl <= sl_p:
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'SL'}
        if hold >= MAX_HOLD_DAYS:
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'MAX'}
    return None


def sim_trailing(ohlcv, entry_date, entry_price, sl=0.15):
    rows = ohlcv[ohlcv['date'] > entry_date]
    hwm, pending = entry_price, False
    for hold, (_, r) in enumerate(rows.iterrows(), 1):
        if pending:
            op = float(r['open'])
            return {'ret': op / entry_price - 1, 'days': hold, 'why': 'TRAIL'}
        cl = float(r['close'])
        if cl <= entry_price * (1 + HARD_STOP):
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'HARD'}
        if cl > hwm:
            hwm = cl
        if cl < hwm * (1 - sl):
            pending = True
        if hold >= MAX_HOLD_DAYS:
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'MAX'}
    return None


def sim_ma10(ohlcv, entry_date, entry_price):
    df = ohlcv.copy()
    df['ma10'] = df['close'].rolling(10).mean()
    rows    = df[df['date'] > entry_date]
    pending = False
    for hold, (_, r) in enumerate(rows.iterrows(), 1):
        if pending:
            op = float(r['open'])
            return {'ret': op / entry_price - 1, 'days': hold, 'why': 'MA10'}
        cl = float(r['close'])
        if cl <= entry_price * (1 + HARD_STOP):
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'HARD'}
        if not pd.isna(r['ma10']) and cl < float(r['ma10']):
            pending = True
        if hold >= MAX_HOLD_DAYS:
            return {'ret': cl / entry_price - 1, 'days': hold, 'why': 'MAX'}
    return None


EXIT_CONFIGS = {
    'TP15_SL10': ('停利15%/停損10%', lambda o, d, p: sim_tpsl(o, d, p, 0.15, 0.10)),
    'TP15_SL12': ('停利15%/停損12%', lambda o, d, p: sim_tpsl(o, d, p, 0.15, 0.12)),
    'TP15_SL15': ('停利15%/停損15%', lambda o, d, p: sim_tpsl(o, d, p, 0.15, 0.15)),
    'TP18_SL10': ('停利18%/停損10%', lambda o, d, p: sim_tpsl(o, d, p, 0.18, 0.10)),
    'TP18_SL12': ('停利18%/停損12%', lambda o, d, p: sim_tpsl(o, d, p, 0.18, 0.12)),
    'TP18_SL15': ('停利18%/停損15%', lambda o, d, p: sim_tpsl(o, d, p, 0.18, 0.15)),
    'TP20_SL10': ('停利20%/停損10%', lambda o, d, p: sim_tpsl(o, d, p, 0.20, 0.10)),
    'TP20_SL12': ('停利20%/停損12%', lambda o, d, p: sim_tpsl(o, d, p, 0.20, 0.12)),
    'TP20_SL15': ('停利20%/停損15%', lambda o, d, p: sim_tpsl(o, d, p, 0.20, 0.15)),
    'TRAIL15':   ('追蹤停損15%',      lambda o, d, p: sim_trailing(o, d, p, 0.15)),
    'MA10':      ('MA10跌破出場',     lambda o, d, p: sim_ma10(o, d, p)),
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


def summarize(trades: list) -> dict | None:
    settled = [t for t in trades if t['result'] is not None]
    if not settled:
        return None
    rets   = [t['result']['ret'] for t in settled]
    wins   = sum(1 for r in rets if r > 0)
    gains  = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r <= 0)
    return {
        'n':        len(settled),
        'open':     len(trades) - len(settled),
        'win_rate': round(wins / len(settled), 4),
        'wilson':   round(wilson_lb(wins, len(settled)), 4),
        'avg_ret':  round(sum(rets) / len(rets) * 100, 2),
        'pf':       round(gains / losses, 2) if losses > 0 else 99.0,
        'avg_days': round(sum(t['result']['days'] for t in settled) / len(settled), 1),
    }


# ── 主流程 ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--refresh', action='store_true', help='強制重抓 FinMind')
    ap.add_argument('--min-n',   type=int, default=20, help='最低結算樣本數')
    args = ap.parse_args()

    signals = load_signals()
    sids    = sorted(set(s['stock_id'] for s in signals))
    print(f'訊號 {len(signals)} 筆，個股 {len(sids)} 檔')

    print('抓取 OHLCV + 籌碼（含 3 年暖身）…')
    ohlcv_map, chip_map = {}, {}
    for i, sid in enumerate(sids + ['TAIEX']):
        df = fetch_ohlcv(sid, args.refresh)
        ohlcv_map[sid] = compute_indicators(df) if not df.empty else pd.DataFrame()
        if sid != 'TAIEX':
            chip_map[sid] = fetch_chip(sid, args.refresh)
        if (i + 1) % 10 == 0:
            print(f'  {i+1}/{len(sids)+1} 完成')
    taiex = ohlcv_map.get('TAIEX', pd.DataFrame())
    print(f'  TAIEX: {len(taiex)} 天')

    print('計算特徵 + 模擬出場…')
    enriched = []
    skip_feat, skip_entry = 0, 0
    for sig in signals:
        ohlcv = ohlcv_map.get(sig['stock_id'])
        if ohlcv is None or ohlcv.empty:
            continue
        feat = build_features(sig, ohlcv, chip_map.get(sig['stock_id']), taiex)
        if feat is None:
            skip_feat += 1
            continue
        # 次日開盤進場
        nxt = ohlcv[ohlcv['date'] > sig['date']]
        if nxt.empty or pd.isna(nxt.iloc[0]['open']) or float(nxt.iloc[0]['open']) <= 0:
            skip_entry += 1
            continue
        entry_date  = nxt.iloc[0]['date']
        entry_price = float(nxt.iloc[0]['open'])

        exits = {eid: fn(ohlcv, entry_date, entry_price)
                 for eid, (_, fn) in EXIT_CONFIGS.items()}
        enriched.append({'sig': sig, 'feat': feat, 'exits': exits})

    print(f'有效樣本 {len(enriched)} 筆（跳過：特徵不足 {skip_feat} / 無次日開盤 {skip_entry}）')

    dates = sorted(e['sig']['date'] for e in enriched)
    mid   = dates[len(dates) // 2]
    print(f'Walk-forward 分割：{mid}（前 {sum(1 for d in dates if d<=mid)} / 後 {sum(1 for d in dates if d>mid)} 筆）')

    # 全矩陣搜索
    combos = []
    for fid, (flabel, ffn) in FILTERS.items():
        picked = [e for e in enriched if ffn(e['feat'], e['sig'])]
        for eid, (elabel, _) in EXIT_CONFIGS.items():
            trades = [{'result': e['exits'][eid]} for e in picked]
            s = summarize(trades)
            if not s or s['n'] < args.min_n:
                continue
            t1 = [{'result': e['exits'][eid]} for e in picked if e['sig']['date'] <= mid]
            t2 = [{'result': e['exits'][eid]} for e in picked if e['sig']['date'] > mid]
            s1 = summarize(t1)
            s2 = summarize(t2)
            # 評分：保守勝率 × 正報酬 × PF（上限5避免極端值）
            sc = s['wilson'] * max(s['avg_ret'], 0) * min(s['pf'], 5.0)
            combos.append({
                'fid': fid, 'eid': eid,
                'filter': flabel, 'exit': elabel,
                'full': s, 'front': s1, 'back': s2,
                'score': round(sc, 4),
                'n_picked': len(picked),
            })

    combos.sort(key=lambda x: -x['score'])

    # Console 輸出
    base_row = next((c for c in combos if c['fid'] == 'BASE' and c['eid'] == 'TP18_SL15'), None)
    if base_row:
        b = base_row['full']
        print(f'\n基準 BASE×TP18SL15：n={b["n"]}  勝率={b["win_rate"]:.0%}  '
              f'avg={b["avg_ret"]}%  PF={b["pf"]}  Wilson={b["wilson"]:.0%}')

    hdr = f'{"#":<3}{"過濾器":<26}{"出場":<14}{"n":>5}{"勝率":>6}{"Wil.":>6}{"avg%":>7}{"PF":>5}{"前WR":>6}{"後WR":>6}{"score":>7}'
    print(f'\n{hdr}')
    print('─' * len(hdr))
    for i, c in enumerate(combos[:25], 1):
        f1 = c['front']
        f2 = c['back']
        wr1 = f'{f1["win_rate"]:.0%}' if f1 else '  -'
        wr2 = f'{f2["win_rate"]:.0%}' if f2 else '  -'
        s   = c['full']
        print(f'{i:<3}{c["filter"]:<26}{c["eid"]:<14}'
              f'{s["n"]:>5}{s["win_rate"]:>6.0%}{s["wilson"]:>6.0%}'
              f'{s["avg_ret"]:>7.2f}{s["pf"]:>5.2f}{wr1:>6}{wr2:>6}{c["score"]:>7.3f}')

    # 找出前後段都優於 BASE 的組合
    base_wr = base_row['full']['wilson'] if base_row else 0
    robust = [c for c in combos
              if c['front'] and c['back']
              and c['front']['wilson'] > base_wr
              and c['back']['wilson']  > base_wr
              and c['full']['n'] >= args.min_n]
    print(f'\n兩段都優於 BASE 的穩健組合：{len(robust)} 個')
    for c in robust[:10]:
        s = c['full']
        print(f"  {c['filter']} × {c['eid']}: "
              f"勝率{s['win_rate']:.0%} avg{s['avg_ret']:+.1f}% "
              f"前{c['front']['win_rate']:.0%}/後{c['back']['win_rate']:.0%} n={s['n']}")

    out = {
        'generated_at':  pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        'signals_total': len(enriched),
        'wf_split':      mid,
        'min_n':         args.min_n,
        'top_combos':    combos[:50],
        'robust_combos': robust[:20],
    }
    dest = ROOT / 'docs' / 'combo_search.json'
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已輸出 {dest}')


if __name__ == '__main__':
    main()
