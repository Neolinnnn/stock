# -*- coding: utf-8 -*-
"""
進場閘門出手頻率延伸（Gate Frequency Extension）
================================================
`backtest_gate_variants.py` 的回測終點被價格快取綁住（需要次日開盤價才能進場、
需要日後 OHLC 才能模擬出場）。但「某道閘門在某天會不會放行」只需要訊號日當天的
資料，而 daily_reports/*/summary.json 已經存了所需的全部欄位：

    price, ma5, ma10, ma20, ma60, atr14   → 多頭排列與各種乖離定義
    sectors[*].avg_ret_20d, strong_sectors → 族群強勢與族群排名
    market.加權指數                        → TAIEX 收盤，可推 MA20/MA60/斜率
    全體個股 price vs ma20                 → 市場廣度

因此在拿不到未來價格的環境，仍可把**出手頻率**算到最新交易日，回答
「放寬門檻能不能解決連續空手」這一題。**勝率與報酬不在本腳本範圍**，
那需要進場後的價格，請用 backtest_gate_variants.py --refresh。

動能閘門（A1/A2）需要成交量與 MACD，summary.json 未收錄，故不納入。

用法：python scripts/gate_frequency_extend.py [--since 20260626]
輸出：docs/gate_frequency.json + console 報告
"""
import argparse
import csv
import json
from pathlib import Path

ROOT = Path(__file__).parent.parent
REPORTS = ROOT / 'daily_reports'

# 與 backtest_gate_variants.py 對齊；MA60 需 60 個交易日暖身
SIGNAL_START = '20250102'


def load_days() -> list[dict]:
    """讀出所有日報，按日期排序。缺 market 或 sectors 的殘缺日直接跳過。"""
    days = []
    for d in sorted(REPORTS.iterdir()):
        if not d.is_dir() or len(d.name) != 8 or not d.name.isdigit():
            continue
        sf = d / 'summary.json'
        if not sf.exists():
            continue
        try:
            data = json.loads(sf.read_text(encoding='utf-8'))
        except json.JSONDecodeError:
            print(f'  略過 {d.name}：summary.json 解析失敗')
            continue
        if not data.get('sectors') or not data.get('market'):
            continue
        days.append({'date': d.name, 'data': data})
    return days


def taiex_closes(days: list[dict]) -> dict:
    """TAIEX 收盤序列：backtest_cache 打底 + daily_reports 往後接。

    daily_reports 的 market.加權指數 只有 2026/06/11 起才有值（更早是 None），
    單靠它湊不出 MA60 暖身；backtest_cache/TAIEX_ohlcv.csv 涵蓋 2024/10 起，
    兩者在 20260611 的收盤一致，可直接銜接。缺快取時只能從日報有值處起算。
    """
    closes = {}
    cf = ROOT / 'backtest_cache' / 'TAIEX_ohlcv.csv'
    if cf.exists():
        with cf.open(encoding='utf-8') as fh:
            for r in csv.DictReader(fh):
                try:
                    c = float(r['close'])
                except (TypeError, ValueError):
                    continue
                if c > 0:
                    closes[r['date']] = c
    else:
        print('  ⚠ 找不到 backtest_cache/TAIEX_ohlcv.csv，大盤閘門的暖身期會縮短')
    for day in days:
        c = (day['data'].get('market') or {}).get('加權指數')
        if isinstance(c, (int, float)) and c > 0:
            closes[day['date']] = c
    return closes


def taiex_series(days: list[dict]) -> dict:
    """大盤閘門的三個判斷。

    MA20/MA60 以「已收盤日」為窗口右端，與 build_features 一致；斜率取 MA20
    相對 5 日前（backtest_gate_variants 用 t_ma20[-1] > t_ma20[-6]）。
    """
    closes = taiex_closes(days)
    dates = sorted(closes)
    seq = [closes[d] for d in dates]
    out = {}
    for i, d in enumerate(dates):
        if i < 59:
            continue
        c = seq[i]
        ma20 = sum(seq[i - 19:i + 1]) / 20
        ma60 = sum(seq[i - 59:i + 1]) / 60
        ma20_prev = sum(seq[i - 24:i - 4]) / 20
        out[d] = {
            'taiex_ma60':  c > ma60,
            'taiex_ma20':  c > ma20,
            'taiex_slope': ma20 > ma20_prev,
        }
    return out


def day_stocks(data: dict):
    """攤平當日所有個股，附上族群名稱。"""
    for sec_name, sec in (data.get('sectors') or {}).items():
        for st in (sec.get('stocks') or []):
            yield sec_name, sec, st


def breadth_of(data: dict) -> float:
    """當日掃描池中「收盤站上 MA20」的家數比。不足 20 檔視為中性 0.5。"""
    up = tot = 0
    for _, _, st in day_stocks(data):
        p, m = st.get('price'), st.get('ma20')
        if not isinstance(p, (int, float)) or not isinstance(m, (int, float)) or m <= 0:
            continue
        tot += 1
        up += p > m
    return up / tot if tot >= 20 else 0.5


def features(st: dict) -> dict | None:
    """個股當日特徵。任一必要欄位缺失就回 None（保守剔除，與線上閘門一致）。"""
    p, ma5, ma10 = st.get('price'), st.get('ma5'), st.get('ma10')
    ma20, ma60, atr = st.get('ma20'), st.get('ma60'), st.get('atr14')
    if not all(isinstance(v, (int, float)) for v in (p, ma5, ma10, ma20, ma60)):
        return None
    if ma10 <= 0 or ma20 <= 0:
        return None
    return {
        'ma_stack':  p > ma5 > ma20 > ma60,
        'bias_ma10': (p - ma10) / ma10,
        'bias_ma20': (p - ma20) / ma20,
        'bias_atr':  (p - ma10) / atr if isinstance(atr, (int, float)) and atr > 0 else 99.0,
    }


# 與 backtest_gate_variants.py 的閘門定義逐條對應
BIAS_GATES = {
    'B0': ('乖離不限',         lambda f: True),
    'B1': ('MA10 ≤2%（原）',   lambda f: f['bias_ma10'] <= 0.02),
    'B2': ('MA20 ≤8%',         lambda f: f['bias_ma20'] <= 0.08),
    'B3': ('MA20 ≤5%（現）',   lambda f: f['bias_ma20'] <= 0.05),
    'B4': ('ATR ≤1.0',         lambda f: f['bias_atr'] <= 1.0),
    'B5': ('ATR ≤1.5',         lambda f: f['bias_atr'] <= 1.5),
    'B6': ('ATR ≤2.0',         lambda f: f['bias_atr'] <= 2.0),
}
MARKET_GATES = {
    'M0': ('大盤不限',          lambda m, b: True),
    'M1': ('TAIEX >MA60（現）', lambda m, b: m['taiex_ma60']),
    'M2': ('TAIEX >MA20',       lambda m, b: m['taiex_ma20']),
    'M3': ('MA20 斜率>0',       lambda m, b: m['taiex_slope']),
    'M4': ('廣度 >50%',         lambda m, b: b > 0.5),
}
SECTOR_GATES = {
    'S0': ('族群不限',          lambda s: True),
    'S1': ('avg_ret>3（現）',   lambda s: s['strong']),
    'S2': ('族群排名前 1/3',    lambda s: s['pct'] <= 1 / 3),
    'S3': ('族群排名前 1/2',    lambda s: s['pct'] <= 0.5),
}


def collect(days: list[dict], taiex: dict) -> list[dict]:
    """逐日展開所有 BUY 訊號，附上閘門判斷所需的全部特徵。"""
    rows = []
    for day in days:
        date, data = day['date'], day['data']
        if date < SIGNAL_START or date not in taiex:
            continue
        sectors = data.get('sectors') or {}
        strong = set(data.get('strong_sectors') or [])
        rets = {n: (s.get('avg_ret_20d') or 0) for n, s in sectors.items()}
        order = sorted(rets, key=lambda k: rets[k], reverse=True)
        rank = {n: i + 1 for i, n in enumerate(order)}
        n_sec = len(order) or 1
        b = breadth_of(data)
        seen = set()
        for sec_name, _sec, st in day_stocks(data):
            sid = st.get('id')
            if st.get('signal') != 'BUY' or not sid or sid in seen:
                continue
            seen.add(sid)
            f = features(st)
            if f is None:
                continue
            rows.append({
                'date': date, 'id': sid, 'feat': f,
                'sec': {'strong': sec_name in strong, 'pct': rank[sec_name] / n_sec},
                'mkt': taiex[date], 'breadth': b,
            })
    return rows


def count(rows: list[dict], bias: str, market: str, sector: str) -> list[dict]:
    bf, mf, sf = BIAS_GATES[bias][1], MARKET_GATES[market][1], SECTOR_GATES[sector][1]
    return [r for r in rows
            if r['feat']['ma_stack'] and bf(r['feat'])
            and mf(r['mkt'], r['breadth']) and sf(r['sec'])]


def stats(hits: list[dict], all_dates: list[str]) -> dict:
    """出手筆數、每月頻率，以及最長連續無進場交易日數。"""
    n_days = len(all_dates)
    hit_days = {r['date'] for r in hits}
    longest = cur = 0
    for d in all_dates:
        cur = 0 if d in hit_days else cur + 1
        longest = max(longest, cur)
    return {
        'n':          len(hits),
        'days':       n_days,
        'entry_days': len(hit_days),
        'per_mo':     round(len(hits) / (n_days / 20.5), 1) if n_days else 0.0,
        'max_drought': longest,
    }


def main(since: str | None):
    days = load_days()
    taiex = taiex_series(days)
    rows = collect(days, taiex)
    if since:
        rows = [r for r in rows if r['date'] >= since]
    dates = sorted({r['date'] for r in rows})
    if not dates:
        print('無可用訊號日：daily_reports 的個股需含 ma5/ma10/ma20/ma60 才能判斷閘門')
        return
    print(f'涵蓋 {dates[0]}~{dates[-1]}，{len(dates)} 個交易日，BUY 訊號 {len(rows)} 筆')
    print('（daily_reports 自 20260617 起才寫入 MA 欄位，更早的日子無法回推閘門）\n')

    # 其餘兩道固定在現行值（B3 + M1 + S1），才看得出單獨調整那一道的效果
    cases = (
        [('乖離', f'{k} {v[0]}', k, 'M1', 'S1') for k, v in BIAS_GATES.items()] +
        [('大盤', f'{k} {v[0]}', 'B3', k, 'S1') for k, v in MARKET_GATES.items()] +
        [('族群', f'{k} {v[0]}', 'B3', 'M1', k) for k, v in SECTOR_GATES.items()]
    )

    out = {
        'generated_at': __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M'),
        'scope': '僅出手頻率；勝率與報酬需 backtest_gate_variants.py --refresh',
        'note': '乖離變體固定 M1+S1；大盤／族群變體固定 B3（現行乖離門檻）',
        'period': {'start': dates[0], 'end': dates[-1],
                   'signal_days': len(dates), 'signals': len(rows)},
        'variants': {},
    }

    hdr = (f'{"":<6}{"變體":<20}{"出手":>6}{"筆/月":>8}'
           f'{"有進場日":>9}{"最長空手":>9}')
    last_group = None
    print(hdr)
    print('─' * 62)
    for group, label, b, m, s in cases:
        if group != last_group:
            print(f'{group}')
            last_group = group
        st = stats(count(rows, b, m, s), dates)
        out['variants'][label] = st
        print(f'{"":<6}{label:<20}{st["n"]:>6}{st["per_mo"]:>8.1f}'
              f'{st["entry_days"]:>9}{st["max_drought"]:>9}')

    dest = ROOT / 'docs' / 'gate_frequency.json'
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'\n已輸出 {dest}')


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', default=None,
                    help='只算此日之後（預設不限，由 daily_reports 的欄位可用性決定）')
    main(ap.parse_args().since)
