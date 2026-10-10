# -*- coding: utf-8 -*-
"""
進場策略實驗室（Entry Strategy Lab）
====================================
在「訊號日當天可得資訊」上測試多種進場過濾器，找出比現行基準更強的進場條件。
所有特徵計算嚴格使用 ≤ 訊號日的資料（無前視偏差），進場為訊號日次日開盤。

訊號來源：daily_reports/*/summary.json 的 qualified 清單
出場策略：TP18/SL15（現行最佳）、追蹤停損15%、MA10跌破、MA5跌破賣半+MA10跌破清倉
驗證：全期間統計 + 前後段 walk-forward 一致性 + 0050 同期超額報酬（見 benchmark.py）
過濾器：除自訂條件外，收錄論壇／GitHub／文獻分享的條件，出處見 FILTER_SOURCES

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
from benchmark import BENCH_ID, Benchmark, excess_stats
from datafeed import finmind_fetch
from price_adjust import adjust_df, fetch_events
from regime_exit_analysis import clean_ohlcv

CACHE_DIR = ROOT / 'backtest_cache'
CACHE_DIR.mkdir(exist_ok=True)

FETCH_START = '2024-10-01'   # 提前抓供 MA60 + RSI 暖身
MAX_HOLD_DAYS = 60
HARD_STOP = -0.20

def fetch_ohlcv(sid: str, refresh=False) -> pd.DataFrame:
    """日 K（含成交量），回傳含息還原價（見 price_adjust；TAIEX 為指數不還原）。
    快取存未還原原始價（build_backtest_docs 直接讀它當現價），事件另存 {sid}_events.json。"""
    cf = CACHE_DIR / f'{sid}_ohlcv.csv'
    if cf.exists() and not refresh:
        out = pd.read_csv(cf, dtype={'date': str})
    else:
        # finmind_fetch：額度用盡自動換 token（全部重抓約 430 次呼叫，單一 token 不一定夠）
        df = finmind_fetch('taiwan_stock_daily', stock_id=sid, start_date=FETCH_START)
        if df is None or df.empty:
            return pd.DataFrame()
        df = df.rename(columns={'max': 'high', 'min': 'low', 'Trading_Volume': 'volume'})
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
        out = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()
        out.to_csv(cf, index=False)
        time.sleep(0.3)
    if sid == 'TAIEX' or out.empty:
        return out
    return adjust_df(out, load_events(sid, out['date'].iloc[-1], refresh))


def load_events(sid: str, asof: str, refresh=False) -> list[dict]:
    """公司行動事件，快取於 {sid}_events.json；快取的查詢迄日早於價格最後一天時重抓，
    避免價格快取更新後漏掉新的除權息。"""
    cf = CACHE_DIR / f'{sid}_events.json'
    if cf.exists() and not refresh:
        cached = json.loads(cf.read_text(encoding='utf-8'))
        if cached['asof'] >= asof:
            return cached['events']
    events = fetch_events(sid, FETCH_START, f'{asof[:4]}-{asof[4:6]}-{asof[6:]}')
    cf.write_text(json.dumps({'asof': asof, 'events': events}), encoding='utf-8')
    return events


CHIP_COLS = ['net', 'foreign', 'trust', 'dealer']


def fetch_chip(sid: str, refresh=False) -> pd.DataFrame:
    """三大法人買賣超（股），本地快取。回傳 date, net（外資+投信）, foreign, trust, dealer。
    舊版快取只有 net，缺分法人欄位時自動重抓（投信、三大法人同買等條件需要）。"""
    cf = CACHE_DIR / f'{sid}_chip.csv'
    old = None
    if cf.exists() and not refresh:
        old = pd.read_csv(cf, dtype={'date': str})
        if set(CHIP_COLS) <= set(old.columns):
            return old
    try:
        df = finmind_fetch('taiwan_stock_institutional_investors', stock_id=sid, start_date=FETCH_START)
    except Exception as e:
        print(f'    {sid} 籌碼下載失敗：{e}')
        df = None
    if df is None or df.empty:
        if old is not None:
            # 舊版快取重抓失敗：保留 net 讓既有條件結果不變，分法人欄位補 NaN（判定為不成立）
            print(f'    {sid} 沿用舊版籌碼快取（僅 net），投信／三大法人條件本次不成立')
            return old.reindex(columns=['date'] + CHIP_COLS)
        return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y%m%d')
    df['diff'] = df['buy'] - df['sell']
    wide = df.pivot_table(index='date', columns='name', values='diff', aggfunc='sum', fill_value=0)

    def col(*names):
        return sum(wide[n] for n in names if n in wide.columns)

    out = pd.DataFrame({
        'date': wide.index,
        'net': col('Foreign_Investor', 'Investment_Trust'),    # 與舊版定義相同
        'foreign': col('Foreign_Investor'),
        'trust': col('Investment_Trust'),
        'dealer': col('Dealer_self', 'Dealer_Hedging'),
    }).reset_index(drop=True)
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

    # ── 外部來源條件用指標（見 FILTER_SOURCES）──
    df['ma3'] = c.rolling(3).mean()
    df['ma6'] = c.rolling(6).mean()
    std20 = c.rolling(20).std()
    df['bb_up'] = df['ma20'] + 2 * std20
    df['bb_bw'] = 4 * std20 / df['ma20']                      # 布林帶寬
    df['bb_bw_q20'] = df['bb_bw'].rolling(40).quantile(0.2)   # 近 40 日帶寬 20 分位
    df['range'] = df['high'] - df['low']
    df['obv'] = (c.diff().apply(lambda x: (x > 0) - (x < 0)) * df['volume']).cumsum()
    # RSI2（Connors）、RSI28（與 RSI14 交叉用），同 RSI14 的 Wilder 平滑
    for n in (2, 28):
        g = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
        ls = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
        df[f'rsi{n}'] = 100 - 100 / (1 + g / ls.replace(0, 1e-9))
    # ATR（Wilder）、ADX14 / ±DI
    tr = pd.concat([df['high'] - df['low'], (df['high'] - c.shift()).abs(),
                    (df['low'] - c.shift()).abs()], axis=1).max(axis=1)
    up, dn = df['high'].diff(), -df['low'].diff()
    atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean()
    df['plus_di'] = 100 * up.where((up > dn) & (up > 0), 0.0).ewm(alpha=1 / 14, adjust=False).mean() / atr14
    df['minus_di'] = 100 * dn.where((dn > up) & (dn > 0), 0.0).ewm(alpha=1 / 14, adjust=False).mean() / atr14
    dx = 100 * (df['plus_di'] - df['minus_di']).abs() / (df['plus_di'] + df['minus_di']).replace(0, 1e-9)
    df['adx'] = dx.ewm(alpha=1 / 14, adjust=False).mean()
    # Keltner 上軌：EMA10 + 2×ATR10
    df['kc_up'] = c.ewm(span=10, adjust=False).mean() + 2 * tr.ewm(alpha=1 / 10, adjust=False).mean()
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
    # ── 外部來源條件（出處見 FILTER_SOURCES）──
    p2 = df.iloc[-3]
    mas, pmas = (r['ma5'], r['ma10'], r['ma20']), (prev['ma5'], prev['ma10'], prev['ma20'])
    feat['tangle_break'] = bool((max(pmas) - min(pmas)) / prev['close'] <= 0.03 and c > max(mas))
    feat['big_red'] = bool(c >= r['open'] * 1.03)
    feat['ma3_6'] = bool(r['ma3'] > r['ma6'] and r['ma3'] > prev['ma3'] > p2['ma3'])
    feat['kd_cross_low'] = bool(prev['kd_k'] <= prev['kd_d'] and r['kd_k'] > r['kd_d'] and r['kd_k'] < 50)
    feat['bb_squeeze_break'] = bool(c > r['bb_up'] and prev['bb_bw'] <= prev['bb_bw_q20'])
    rng7 = df.iloc[-8:-1]['range']
    feat['nr7_break'] = bool(rng7.iloc[-1] <= rng7.min() and c > prev['high'])
    feat['gap_up'] = bool(r['low'] > prev['high'])
    feat['break55'] = bool(c >= df.iloc[-56:-1]['close'].max())
    feat['adx_trend'] = bool(r['adx'] >= 25 and r['plus_di'] > r['minus_di'])
    feat['obv_high'] = bool(r['obv'] > df.iloc[-21:-1]['obv'].max())
    feat['rsi2_pullback'] = bool(r['rsi2'] < 10 and c > r['ma60'])
    feat['kc_break'] = bool(c > r['kc_up'] and prev['close'] <= prev['kc_up'])
    feat['rsi_cross'] = bool(prev['rsi14'] <= prev['rsi28'] and r['rsi14'] > r['rsi28'])

    # 法人近 3 日淨買超；投信、三大法人條件（股數，與成交量同單位）
    feat.update(chip3=0, trust_big=False, trust3=False, inst3=False)
    if chip is not None and not chip.empty:
        c3 = chip[chip['date'] <= D].tail(3)
        if not c3.empty:
            feat['chip3'] = float(c3['net'].sum())
            today = c3.iloc[-1]
            feat['trust_big'] = bool(today['date'] == D and r['volume']
                                     and today['trust'] / r['volume'] >= 0.10)
            full = len(c3) == 3 and c3.iloc[-1]['date'] == D
            feat['trust3'] = bool(full and (c3['trust'] > 0).all())
            feat['inst3'] = bool(full and (c3[['foreign', 'trust', 'dealer']] > 0).all().all())
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


def sim_ma5_ma10(ohlcv, entry_date, entry_price):
    """收盤跌破 MA5 → 次日開盤賣一半；收盤跌破 MA10 → 次日開盤出清剩餘。
    兩條件同日成立則次日全數出清。ret 為兩半部位的平均報酬。"""
    df = ohlcv.copy()
    df['ma5'] = df['close'].rolling(5).mean()
    df['ma10'] = df['close'].rolling(10).mean()
    rows = df[df['date'] > entry_date]
    half_ret = None          # 已賣出那一半的報酬
    pend_half = pend_all = False
    for hold, (_, r) in enumerate(rows.iterrows(), 1):
        if pend_all:
            ret = r['open'] / entry_price - 1
            return {'ret': ret if half_ret is None else (half_ret + ret) / 2,
                    'days': hold, 'exit': r['date'], 'why': 'MA10'}
        if pend_half:
            half_ret, pend_half = r['open'] / entry_price - 1, False
        cl = r['close']
        rem = cl / entry_price - 1
        if cl <= entry_price * (1 + HARD_STOP) or hold >= MAX_HOLD_DAYS:
            return {'ret': rem if half_ret is None else (half_ret + rem) / 2, 'days': hold,
                    'exit': r['date'], 'why': 'HARD' if hold < MAX_HOLD_DAYS else 'MAX'}
        if not pd.isna(r['ma10']) and cl < r['ma10']:
            pend_all = True
        elif half_ret is None and not pd.isna(r['ma5']) and cl < r['ma5']:
            pend_half = True
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
    # 外部來源條件（2026-10 調研，出處見 FILTER_SOURCES）
    'TANGLE':      ('均線糾結後突破',         lambda f, s: f['tangle_break']),
    'BIGRED':      ('帶量長紅(實體≥3%+量比1.5)', lambda f, s: f['big_red'] and f['vol_ratio'] >= 1.5),
    'MA3_6':       ('三日均>六日均且連升',    lambda f, s: f['ma3_6']),
    'KDLOW':       ('KD 50以下金叉',          lambda f, s: f['kd_cross_low']),
    'BBSQZ':       ('布林擠壓後突破上軌',     lambda f, s: f['bb_squeeze_break']),
    'NR7':         ('NR7收斂後突破前高',      lambda f, s: f['nr7_break']),
    'GAPUP':       ('向上跳空缺口',           lambda f, s: f['gap_up']),
    'BREAK55':     ('創55日收盤新高',         lambda f, s: f['break55']),
    'ADX':         ('ADX≥25且+DI>−DI',        lambda f, s: f['adx_trend']),
    'OBVHIGH':     ('OBV創20日新高',          lambda f, s: f['obv_high']),
    'RSI2':        ('RSI2<10拉回(站上MA60)',  lambda f, s: f['rsi2_pullback']),
    'KELTNER':     ('Keltner通道突破',        lambda f, s: f['kc_break']),
    'RSICROSS':    ('RSI14上穿RSI28',         lambda f, s: f['rsi_cross']),
    'TRUST10':     ('投信買超≥成交量10%',     lambda f, s: f['trust_big']),
    'TRUST3':      ('投信連3日買超',          lambda f, s: f['trust3']),
    'INST3':       ('三大法人連3日同買',      lambda f, s: f['inst3']),
}

# 外部來源條件的出處；參數凡原文未給定者（糾結 3%、擠壓 20 分位等）為本專案設定
FILTER_SOURCES = {
    'TANGLE':   '台股論壇常見型態（朱家泓：5/10/20 日均線糾結後突破為起漲點）；前一日三線差距≤3%',
    'BIGRED':   '論壇「帶量長紅K」；twstock BestFourPoint「量大收紅」的加嚴版',
    'MA3_6':    'GitHub mlouielu/twstock BestFourPoint 買點3、4（三日均價連升、大於六日均價）',
    'KDLOW':    'CMoney／鉅亨 KD 教學、FinLab 因子範例：低檔（K<50）黃金交叉',
    'BBSQZ':    'TTM Squeeze／FinLab 布林通道台股回測：帶寬處近 40 日低檔後收盤突破上軌',
    'NR7':      'Toby Crabel 窄幅收斂：前一日為近 7 日振幅最小，今日收盤突破其高點',
    'GAPUP':    '缺口理論：今日最低 > 昨日最高（向上跳空未回補）',
    'BREAK55':  '海龜交易 55 日唐奇安突破；52 週新高效應（George & Hwang 2004，台股亦顯著）的短窗近似',
    'ADX':      'Wilder ADX 趨勢強度濾網（海龜類策略常搭配）',
    'OBVHIGH':  '量先價行：OBV 創 20 日新高',
    'RSI2':     'Larry Connors RSI(2) 拉回；原版為站上 MA200，受資料長度限制改 MA60',
    'KELTNER':  'FinLab 因子範例：收盤由下突破 EMA10 + 2×ATR10',
    'RSICROSS': 'FinLab 因子範例：RSI14 上穿 RSI28',
    'TRUST10':  'FinLab 因子範例：投信單日買超 ≥ 當日成交量 10%',
    'TRUST3':   '論壇「投信連買」作帳行情（FinLab 範例為連 2 日買超）',
    'INST3':    'FinLab 因子範例：外資、投信、自營商連 3 日同步買超',
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
        **excess_stats([(t['result']['ret'] * 100, t.get('bench')) for t in settled]),
    }


def picked_trades(picked: list[dict], ex: str) -> list[dict]:
    """summarize 的輸入：出場結果 + 同進出場日 0050 報酬。"""
    return [{'result': e['exits'][ex], 'bench': e['bench'][ex]} for e in picked]


def load_benchmark(refresh=False) -> Benchmark:
    """0050 日 K（同快取機制）；抓不到時回空 Benchmark，結果不含 0050 比較。"""
    try:
        df = fetch_ohlcv(BENCH_ID, refresh)
    except Exception as e:
        print(f'  ⚠️ {BENCH_ID} 下載失敗：{e}')
        df = pd.DataFrame()
    bars = {r['date']: {'open': r['open'], 'close': r['close']} for _, r in df.iterrows()}
    if not bars:
        print(f'  ⚠️ 無 {BENCH_ID} 股價，本次結果不含 0050 同期比較（回測標準 3 無法判定）')
    return Benchmark(bars)


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
        # 濾掉 0 價缺口列，否則持倉跨過該日會被假停損成 −100%
        ohlcv_map[sid] = compute_indicators(clean_ohlcv(df)) if not df.empty else df
        chip_map[sid] = fetch_chip(sid, args.refresh)
        print(f'  {sid}: {len(ohlcv_map[sid])} 天價格, {len(chip_map[sid])} 天籌碼')
    taiex = fetch_ohlcv('TAIEX', args.refresh)
    print(f'  TAIEX: {len(taiex)} 天')
    bench = load_benchmark(args.refresh)
    print(f'  {BENCH_ID}: {len(bench.days)} 天')

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
        exits = {
            'TP18SL15': sim_tpsl(ohlcv, entry_date, entry_price, 0.18, 0.15),
            'TRAIL15': sim_trailing(ohlcv, entry_date, entry_price, 0.15),
            'MA10': sim_ma10(ohlcv, entry_date, entry_price),
            'MA5HALF': sim_ma5_ma10(ohlcv, entry_date, entry_price),
        }
        enriched.append({
            'sig': sig, 'feat': feat,
            'entry_date': entry_date, 'entry_price': entry_price,
            'exits': exits,
            'bench': {ex: bench.ret_pct(entry_date, r['exit']) if r else None
                      for ex, r in exits.items()},
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
        if fid in FILTER_SOURCES:
            row['source'] = FILTER_SOURCES[fid]
        for ex in ['TP18SL15', 'TRAIL15', 'MA10', 'MA5HALF']:
            row['exits'][ex] = summarize(picked_trades(picked, ex))
            # walk-forward
            row['exits'][ex]['wf_front'] = summarize(
                picked_trades([e for e in picked if e['sig']['date'] <= mid], ex))
            row['exits'][ex]['wf_back'] = summarize(
                picked_trades([e for e in picked if e['sig']['date'] > mid], ex))
        results[fid] = row

    # 評分分層
    tier_stats = {}
    for lo, hi, tier in [(5, 7, 'A(5-7分)'), (3, 4, 'B(3-4分)'), (0, 2, 'C(0-2分)')]:
        picked = [e for e in enriched if lo <= e['score'] <= hi]
        tier_stats[tier] = {
            'picked': len(picked),
            'TP18SL15': summarize(picked_trades(picked, 'TP18SL15')),
            'TRAIL15': summarize(picked_trades(picked, 'TRAIL15')),
            'MA5HALF': summarize(picked_trades(picked, 'MA5HALF')),
        }

    # ── 報告 ──
    print('\n════════ 進場過濾器 × TP18SL15 ════════')
    print(f"{'過濾器':<24}{'樣本':>5}{'勝率':>7}{'Wilson下界':>10}{'平均%':>8}{'PF':>6}{'天數':>6}{'vs0050':>8}")
    base = results['BASE']['exits']['TP18SL15']
    for fid, row in sorted(results.items(), key=lambda kv: -(kv[1]['exits']['TP18SL15'].get('win_rate') or 0)):
        s = row['exits']['TP18SL15']
        if s.get('n', 0) == 0:
            continue
        # ★＝Wilson 下界勝過基準勝率，且（有 0050 資料時）超額報酬 > 0；walk-forward 見網頁判讀
        beat_bench = s.get('excess_return', 1) > 0
        mark = ' ★' if s['wilson_lb'] > (base.get('win_rate') or 0) and beat_bench else ''
        vs = f"{s['excess_return']:>+7.2f}" if 'excess_return' in s else f"{'—':>7}"
        print(f"{row['label']:<24}{s['n']:>5}{s['win_rate']:>7.0%}{s['wilson_lb']:>10.0%}"
              f"{s['avg_ret']:>8.2f}{s['profit_factor']:>6.2f}{s['avg_days']:>6.1f} {vs}{mark}")

    print('\n════════ 評分分層（7 項條件計分） ════════')
    for tier, st in tier_stats.items():
        s = st['TP18SL15']
        if s.get('n'):
            print(f"{tier}: 樣本={s['n']} 勝率={s['win_rate']:.0%} 平均={s['avg_ret']}% PF={s['profit_factor']}")

    out = {
        'generated_at': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M'),
        'period': {'start': dates[0], 'end': dates[-1], 'signals': len(enriched), 'wf_split': mid},
        # 0050 整段買進持有，僅供大盤背景；勝負以各過濾器的逐筆超額報酬（excess_return）為準
        'benchmark': bench.period(dates[0], taiex['date'].max()),
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
