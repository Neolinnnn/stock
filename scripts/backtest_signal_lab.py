# -*- coding: utf-8 -*-
"""
訊號規則實驗室（Signal Rule Lab）
==================================
比較「BUY 訊號如何產生」的多種規則變體，其餘閘門與出場完全沿用正式流程。

背景：現行 BUY 只在「MA5 上穿 MA20 的當根 ± 5 日視窗內」成立，且交叉當日
RSI5 必須 < 65。交叉當天 RSI 剛好過熱、之後卻一路沿 MA20 上攻的個股
（如 6271 同欣電 2026-08-10 交叉、RSI5=72.2 被擋），此後 MA5 未再跌破
MA20，就永遠拿不到 BUY。本 lab 量化兩類放寬方案的實際績效。

變體：
  A_BASE  現行規則（交叉視窗 5 日）
  B10/B15 延長交叉有效期至 10 / 15 日，且期間 MA5 未跌破 MA20
  C1      趨勢延續補發（交叉當日被 RSI 擋掉 → 首個 RSI5<65 且多頭排列日補發，一次性）
  C2      回檔再進場（多頭延續中，RSI5 由 ≥65 下穿 <65 且收盤>MA5>MA20）

兩條評估軌（皆沿用 daily_scan / position_tracker 的既有閘門）：
  qualified  雙條件達標推薦：BUY + CV 三閘門 + 乖離MA10≤2% + 族群強勢
  gate       HYBRID 進場閘門：BUY + 大盤>MA60 + 收盤>MA5>MA20>MA60 + 族群強勢 + 乖離

出場：HYBRID（PROFIT_TRIGGER/FLOOR/SL/TIMEOUT，與 position_tracker 同參數），
另跑 TP18/SL15 作為穩健性對照。進場價＝訊號日次一交易日收盤（與正式流程一致）。

無前視偏差：每個交易日只用 ≤ 當日的收盤資料重算指標、族群強弱與 CV。

用法：python scripts/backtest_signal_lab.py [--start 20240101] [--refresh]
輸出：docs/signal_lab.json + console 報告
"""
import argparse
import bisect
import json
import math
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).parent.parent
CACHE_DIR = ROOT / 'backtest_cache'
FETCH_START = '2022-01-01'      # 需 ~500 根暖身供 walk-forward CV 使用
CV_WINDOW = 500                 # 與 batch_scan.DATA_DAYS 一致

# HYBRID 出場參數（與 scripts/position_tracker.py 同值）
PROFIT_TRIGGER = 0.15
PROFIT_FLOOR = 0.07
PHASE1_SL = 0.15
PHASE2_TIMEOUT = 25

MAX_BIAS_MA10 = 2.0             # 與 daily_scan 同值
SECTOR_STRONG_RET = 3.0         # 族群 avg_ret_20d > 3% 視為強勢


# ── 載入 batch_scan 的原始函式（避免重寫造成邏輯漂移） ───────────────────────

def load_batch_scan() -> dict:
    """exec batch_scan.py 取得 sma / calc_rsi / generate_signals / walk_forward_cv。

    batch_scan 依賴 twstock 只為線上抓價，回測不需要，以空模組替身避開安裝需求。
    """
    if 'twstock' not in sys.modules:
        stub = types.ModuleType('twstock')
        stub.Stock = object
        stub.codes = {}
        sys.modules['twstock'] = stub
    src = (ROOT / 'scripts' / 'batch_scan.py').read_text(encoding='utf-8')
    src = src.split("if __name__ == '__main__':")[0]
    ns: dict = {}
    exec(compile(src, 'batch_scan.py', 'exec'), ns)
    return ns


BS = load_batch_scan()
sma = BS['sma']
calc_rsi = BS['calc_rsi']
generate_signals = BS['generate_signals']
walk_forward_cv = BS['walk_forward_cv']
RSI_HIGH = BS['RSI_OVERBOUGHT']   # 65


# ── 資料 ─────────────────────────────────────────────────────────────────────

def fetch_ohlcv(sid: str, refresh=False) -> list[dict]:
    """日 K，快取在 backtest_cache/{sid}_ohlcv_long.csv（較既有 _ohlcv.csv 長）。"""
    import csv
    cf = CACHE_DIR / f'{sid}_ohlcv_long.csv'
    if cf.exists() and not refresh:
        with cf.open(encoding='utf-8') as fh:
            return [{'date': r['date'], 'open': float(r['open']), 'high': float(r['high']),
                     'low': float(r['low']), 'close': float(r['close'])}
                    for r in csv.DictReader(fh)]
    from datafeed import make_dataloader
    df = make_dataloader().taiwan_stock_daily(stock_id=sid, start_date=FETCH_START)
    if df is None or df.empty:
        return []
    df = df.rename(columns={'max': 'high', 'min': 'low'})
    rows = [{'date': str(d).replace('-', '')[:8], 'open': float(o), 'high': float(h),
             'low': float(lo), 'close': float(c)}
            for d, o, h, lo, c in zip(df['date'], df['open'], df['high'],
                                      df['low'], df['close'])]
    CACHE_DIR.mkdir(exist_ok=True)
    with cf.open('w', newline='', encoding='utf-8') as fh:
        w = csv.writer(fh)
        w.writerow(['date', 'open', 'high', 'low', 'close', 'volume'])
        for r in rows:
            w.writerow([r['date'], r['open'], r['high'], r['low'], r['close'], 0])
    time.sleep(0.3)
    return rows


def _adjust_splits(close: list[float], ratio_floor=0.5) -> list[float]:
    """還原股票分割／減資：FinMind TaiwanStockPrice 為未還原價。

    單日跌幅超過 50%（如 2327 國巨 2025-08 一股換四股、6415 矽力-KY 2022-07
    面額分割）不可能是行情，視為公司行動，將該日之前的價格整段乘上比例。
    -33% 等級的真實崩盤（2025-04-07 關稅急殺）不會被誤判。
    """
    out = list(close)
    for i in range(len(out) - 1, 0, -1):
        r = out[i] / out[i - 1]
        if r < ratio_floor:
            for j in range(i):
                out[j] *= r
    return out


def _raw_cross_up(s, i: int) -> bool:
    """MA5 於第 i 根上穿 MA20。"""
    a5, a20, b5, b20 = s.ma5[i - 1], s.ma20[i - 1], s.ma5[i], s.ma20[i]
    return None not in (a5, a20, b5, b20) and a5 <= a20 and b5 > b20


class Series:
    """單檔個股的逐日指標，全部以「截至該日」的收盤價計算。"""

    def __init__(self, sid: str, name: str, sector: str, rows: list[dict]):
        self.id, self.name, self.sector = sid, name, sector
        # FinMind 停牌日偶爾回傳 close=0，會讓 CV 回測除以零，直接剔除
        rows = [r for r in rows if r['close'] and r['close'] > 0]
        self.dates = [r['date'] for r in rows]
        self.close = _adjust_splits([r['close'] for r in rows])
        self.idx = {d: i for i, d in enumerate(self.dates)}
        self.ma5 = sma(self.close, 5)
        self.ma10 = sma(self.close, 10)
        self.ma20 = sma(self.close, 20)
        self.ma60 = sma(self.close, 60)
        # calc_rsi 回傳長度 len+1（index i 對應 price i-1），取尾端對齊到各 bar
        r = calc_rsi(self.close, 5)
        self.rsi5 = [None] * (len(self.close) - (len(r) - 1)) + r[1:]
        self._cv: dict[int, tuple] = {}
        # 預算交叉點與「MA5 最後一次未站上 MA20 的位置」，讓查詢降為 O(1)/O(log n)
        self.crosses = [i for i in range(1, len(self.close)) if _raw_cross_up(self, i)]
        self.last_break, last = [], -1
        for i in range(len(self.close)):
            if self.ma5[i] is None or self.ma20[i] is None or self.ma5[i] <= self.ma20[i]:
                last = i
            self.last_break.append(last)

    def ret20(self, t: int):
        if t < 20 or self.close[t - 20] == 0:
            return None
        return (self.close[t] - self.close[t - 20]) / self.close[t - 20]

    def cv(self, t: int) -> tuple:
        """(sharpe, win_rate, max_dd)；與 analyze_stock 同樣取最近 CV_WINDOW 根。"""
        if t in self._cv:
            return self._cv[t]
        lo = max(0, t - CV_WINDOW + 1)
        res = walk_forward_cv(self.close[lo:t + 1], self.dates[lo:t + 1])
        if res:
            out = (sum(x['sharpe'] for x in res) / len(res),
                   sum(x['win_rate'] for x in res) / len(res),
                   sum(x['max_dd'] for x in res) / len(res))
        else:
            out = (0.0, 0.0, 0.0)
        self._cv[t] = out
        return out


# ── 訊號變體 ─────────────────────────────────────────────────────────────────

def _bull_since(s: Series, c: int, t: int) -> bool:
    """交叉日 c 之後 MA5 未跌破 MA20。"""
    return s.last_break[t] < c


def _last_cross(s: Series, t: int, max_back: int | None = None) -> int | None:
    i = bisect.bisect_right(s.crosses, t) - 1
    if i < 0:
        return None
    c = s.crosses[i]
    if max_back is not None and c < t - max_back + 1:
        return None
    return c


def sig_base(s: Series, t: int) -> bool:
    """A_BASE：完整重現 analyze_stock 的當前訊號判定（交叉視窗 5 日）。"""
    if t < 6:
        return False
    w = slice(t - 5, t + 1)
    sigs = generate_signals(s.close[w], s.dates[w], s.ma5[w], s.ma20[w], s.rsi5[w])
    if not sigs or sigs[-1]['signal'] != 'BUY':
        return False
    return s.ma5[t] is not None and s.ma20[t] is not None and s.ma5[t] > s.ma20[t]


def sig_b(s: Series, t: int, window: int) -> bool:
    """B：交叉視窗延長至 window 日，且交叉後 MA5 未跌破 MA20。"""
    c = _last_cross(s, t, window)
    if c is None or s.rsi5[c] is None or s.rsi5[c] >= RSI_HIGH:
        return False
    return _bull_since(s, c, t)


def sig_c1(s: Series, t: int) -> bool:
    """C1：交叉當日被 RSI 擋掉 → 首個 RSI5<65 且收盤>MA5>MA20 之日補發（一次性）。"""
    c = _last_cross(s, t)
    if c is None or s.rsi5[c] is None or s.rsi5[c] < RSI_HIGH:
        return False          # 沒被擋過就不需補發（走 A_BASE）
    if not _bull_since(s, c, t):
        return False

    def ok(j: int) -> bool:
        v = (s.rsi5[j], s.ma5[j], s.ma20[j])
        return (None not in v and s.rsi5[j] < RSI_HIGH
                and s.close[j] > s.ma5[j] > s.ma20[j])

    return ok(t) and not any(ok(j) for j in range(c + 1, t))


def sig_c2(s: Series, t: int) -> bool:
    """C2：多頭延續中，RSI5 由 ≥65 下穿 <65 且收盤>MA5>MA20 即再進場。"""
    c = _last_cross(s, t)
    if c is None or not _bull_since(s, c, t) or t == c:
        return False
    prev, cur = s.rsi5[t - 1], s.rsi5[t]
    if None in (prev, cur, s.ma5[t], s.ma20[t]) or not (prev >= RSI_HIGH > cur):
        return False
    return s.close[t] > s.ma5[t] > s.ma20[t]


def sig_d(s: Series, t: int) -> bool:
    """D：狀態型。不要求「近期有交叉」，只要多頭延續中且未過熱、站上 MA5。

    B/C 都仍綁在「交叉事件」上，遇到交叉當日被 RSI 擋掉、之後長期沿 MA20
    上攻的個股（6271）依然無解。D 改判「當下狀態」，真正的追高防線交給下游
    既有的乖離 MA10 ≤2% 閘門。
    """
    c = _last_cross(s, t)
    if c is None or not _bull_since(s, c, t):
        return False
    v = (s.rsi5[t], s.ma5[t], s.ma20[t])
    return not (None in v or s.rsi5[t] >= RSI_HIGH or s.close[t] <= s.ma5[t])


VARIANTS = {
    'A_BASE': ('現行規則（交叉視窗5日）', lambda s, t: sig_base(s, t)),
    'B10':    ('交叉視窗10日+多頭延續',   lambda s, t: sig_base(s, t) or sig_b(s, t, 10)),
    'B15':    ('交叉視窗15日+多頭延續',   lambda s, t: sig_base(s, t) or sig_b(s, t, 15)),
    'C1':     ('RSI冷卻後一次性補發',      lambda s, t: sig_base(s, t) or sig_c1(s, t)),
    'C2':     ('回檔再進場(RSI下穿65)',    lambda s, t: sig_base(s, t) or sig_c2(s, t)),
    'D':      ('狀態型：多頭延續+未過熱',   lambda s, t: sig_base(s, t) or sig_d(s, t)),
}


# ── 出場模擬 ─────────────────────────────────────────────────────────────────

def sim_hybrid(s: Series, t: int) -> dict | None:
    """HYBRID：訊號日 t → t+1 收盤進場，t+2 起逐日推進（同 position_tracker）。"""
    e = t + 1
    if e >= len(s.close):
        return None
    entry = s.close[e]
    trigger, floor, stop = entry * (1 + PROFIT_TRIGGER), entry * (1 + PROFIT_FLOOR), entry * (1 - PHASE1_SL)
    phase, hwm, flat = 1, entry, 0
    for u in range(e + 1, len(s.close)):
        p = s.close[u]
        if phase == 1:
            if p <= stop:
                return {'ret': p / entry - 1, 'days': u - e, 'why': 'SL', 'exit_i': u}
            if p >= trigger:
                phase, hwm, flat = 2, p, 0
            continue
        if p > hwm:
            hwm, flat = p, 0
        else:
            flat += 1
        if p <= floor:
            return {'ret': p / entry - 1, 'days': u - e, 'why': 'FLOOR', 'exit_i': u}
        if s.ma10[u] is not None and p < s.ma10[u]:
            return {'ret': p / entry - 1, 'days': u - e, 'why': 'MA10', 'exit_i': u}
        if flat >= PHASE2_TIMEOUT:
            return {'ret': p / entry - 1, 'days': u - e, 'why': 'TIME', 'exit_i': u}
    return None   # 尚未結算


def sim_tpsl(s: Series, t: int, tp=0.18, sl=0.15) -> dict | None:
    e = t + 1
    if e >= len(s.close):
        return None
    entry = s.close[e]
    for u in range(e + 1, len(s.close)):
        p = s.close[u]
        if p >= entry * (1 + tp):
            return {'ret': p / entry - 1, 'days': u - e, 'why': 'TP', 'exit_i': u}
        if p <= entry * (1 - sl):
            return {'ret': p / entry - 1, 'days': u - e, 'why': 'SL', 'exit_i': u}
        if u - e >= 60:
            return {'ret': p / entry - 1, 'days': u - e, 'why': 'MAX', 'exit_i': u}
    return None


EXITS = {'HYBRID': sim_hybrid, 'TP18SL15': sim_tpsl}


# ── 統計 ─────────────────────────────────────────────────────────────────────

def wilson_lb(wins: int, n: int, z=1.96) -> float:
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (centre - margin) / denom


def summarize(trades: list[dict]) -> dict:
    settled = [t for t in trades if t.get('ret') is not None]
    if not settled:
        return {'n': 0, 'n_open': len(trades)}
    rets = [t['ret'] for t in settled]
    wins = sum(1 for r in rets if r > 0)
    gains = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r <= 0)
    srt = sorted(rets)
    return {
        'n': len(settled),
        'n_open': len(trades) - len(settled),
        'win_rate': round(wins / len(settled) * 100, 1),
        'wilson_lb': round(wilson_lb(wins, len(settled)) * 100, 1),
        'avg_ret': round(sum(rets) / len(rets) * 100, 2),
        'median_ret': round(srt[len(srt) // 2] * 100, 2),
        'profit_factor': round(gains / losses, 2) if losses > 0 else 99.0,
        'total_ret': round(sum(rets) * 100, 1),
        'avg_days': round(sum(t['days'] for t in settled) / len(settled), 1),
        'worst': round(min(rets) * 100, 2),
        'best': round(max(rets) * 100, 2),
    }


# ── 主流程 ───────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20240101', help='回放起始日 YYYYMMDD')
    ap.add_argument('--refresh', action='store_true', help='強制重抓 FinMind 日K')
    args = ap.parse_args()

    universe = json.loads((ROOT / 'docs' / 'stocks_index.json').read_text(encoding='utf-8'))
    seen, stocks = set(), []
    print('載入日 K …')
    for u in universe:
        if u['id'] in seen:
            continue
        seen.add(u['id'])
        rows = fetch_ohlcv(u['id'], args.refresh)
        if len(rows) < 120:
            print(f"  略過 {u['id']} {u['name']}（資料僅 {len(rows)} 根）")
            continue
        stocks.append(Series(u['id'], u['name'], u['sector'], rows))
    taiex_rows = fetch_ohlcv('TAIEX', args.refresh)
    taiex_close = [r['close'] for r in taiex_rows]
    taiex_ma60 = sma(taiex_close, 60)
    taiex_bull = {r['date']: (taiex_ma60[i] is not None and taiex_close[i] > taiex_ma60[i])
                  for i, r in enumerate(taiex_rows)}
    print(f'  共 {len(stocks)} 檔')

    # 交易日軸 + 每日族群強弱（族群平均 20 日報酬 > 3%）
    all_dates = sorted({d for s in stocks for d in s.dates if d >= args.start})
    print(f'回放區間 {all_dates[0]} ~ {all_dates[-1]}（{len(all_dates)} 個交易日）')

    sector_strong: dict[str, set[str]] = {}
    for d in all_dates:
        acc: dict[str, list[float]] = {}
        for s in stocks:
            t = s.idx.get(d)
            if t is None:
                continue
            r = s.ret20(t)
            if r is not None:
                acc.setdefault(s.sector, []).append(r)
        sector_strong[d] = {sec for sec, v in acc.items()
                            if sum(v) / len(v) * 100 > SECTOR_STRONG_RET}

    results: dict[str, dict] = {}
    for key, (label, fn) in VARIANTS.items():
        raw_hits: list[tuple[str, Series, int]] = []      # (date, series, t)
        for d in all_dates:
            for s in stocks:
                t = s.idx.get(d)
                if t is None or t < 60:
                    continue
                if fn(s, t):
                    raw_hits.append((d, s, t))

        picks = {'qualified': [], 'gate': []}
        for d, s, t in raw_hits:
            sharpe, win, dd = s.cv(t)
            if sharpe < 0:                                  # analyze_stock：CV 夏普<0 降 HOLD
                continue
            ma5, ma10, ma20, ma60, c = s.ma5[t], s.ma10[t], s.ma20[t], s.ma60[t], s.close[t]
            if None in (ma5, ma10, ma20, ma60) or ma10 == 0:
                continue
            bias = (c - ma10) / ma10 * 100
            strong = s.sector in sector_strong[d]
            if sharpe >= 0.3 and win >= 0.4 and dd <= 0.2 and bias <= MAX_BIAS_MA10 and strong:
                picks['qualified'].append((d, s, t))
            if taiex_bull.get(d) and c > ma5 > ma20 > ma60 and strong and bias <= MAX_BIAS_MA10:
                picks['gate'].append((d, s, t))

        results[key] = {'label': label, 'raw_signals': len(raw_hits), 'tracks': {}}
        for track, hits in picks.items():
            for exit_name, sim in EXITS.items():
                trades, busy = [], {}       # busy[sid] = 出場前不得重複進場
                for d, s, t in sorted(hits, key=lambda x: (x[0], x[1].id)):
                    if busy.get(s.id, -1) >= t:
                        continue
                    r = sim(s, t)
                    busy[s.id] = r['exit_i'] if r else len(s.close)
                    trades.append({'date': d, 'id': s.id, 'name': s.name, 'sector': s.sector,
                                   'ret': r['ret'] if r else None,
                                   'days': r['days'] if r else None,
                                   'why': r['why'] if r else 'OPEN'})
                mid = all_dates[len(all_dates) // 2]
                results[key]['tracks'][f'{track}/{exit_name}'] = {
                    'all': summarize(trades),
                    'first_half': summarize([t for t in trades if t['date'] < mid]),
                    'second_half': summarize([t for t in trades if t['date'] >= mid]),
                    'trades': trades,
                }
        q = results[key]['tracks']['qualified/HYBRID']['all']
        g = results[key]['tracks']['gate/HYBRID']['all']
        print(f"  {key:8s} 原始訊號 {len(raw_hits):5d}｜qualified {q.get('n', 0):3d} 筆"
              f"｜gate {g.get('n', 0):3d} 筆")

    out = ROOT / 'docs' / 'signal_lab.json'
    out.write_text(json.dumps({
        'generated_at': time.strftime('%Y-%m-%d %H:%M'),
        'period': [all_dates[0], all_dates[-1]],
        'universe': len(stocks),
        'variants': {k: {'label': v['label'], 'raw_signals': v['raw_signals'],
                         # 交易明細只留正式流程對應的 gate/HYBRID 軌，其餘僅存統計
                         'tracks': {tk: (tv if tk == 'gate/HYBRID'
                                         else {kk: vv for kk, vv in tv.items() if kk != 'trades'})
                                    for tk, tv in v['tracks'].items()}}
                     for k, v in results.items()},
    }, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已寫出 {out}')
    return results


if __name__ == '__main__':
    main()
