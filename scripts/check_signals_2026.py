"""
2026 進場訊號全面檢查
=====================================
用「現有引擎」(indicators.stock_analyzer.analyze_stock) 對 daily_reports 內
2026 年所有 qualified 進場訊號逐筆評分，並以 MA10 出場法回測實際結果，
交叉檢視「模型評分 / 進場乖離率」與勝率的關係。

資料來源：daily_reports/*/summary.json（訊號）、backtest_cache/*_ohlcv.csv
          （價格，缺漏自 FinMind REST 補抓至 6/30）
用法：python scripts/check_signals_2026.py [--report 回測數據/xxx.md]
"""
import sys, json, csv, time, argparse
from pathlib import Path
from collections import defaultdict

import requests
import pandas as pd

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
from indicators.stock_analyzer import analyze_stock

CACHE = ROOT / 'backtest_cache'
HARD_STOP = -0.20          # 硬停損 -20%
FETCH_END = '2026-06-30'   # 補抓終點


def collect_signals(start: str = '20260101', end: str = '20261231') -> list[dict]:
    sigs = []
    for d in sorted((ROOT / 'daily_reports').iterdir()):
        if not d.is_dir() or not (start <= d.name <= end):
            continue
        sf = d / 'summary.json'
        if not sf.exists():
            continue
        data = json.loads(sf.read_text(encoding='utf-8'))
        strong = set(data.get('strong_sectors', []))
        weak = set(data.get('weak_sectors', []))
        for s in data.get('qualified', []):
            sec = s.get('sector')
            sigs.append({'date': d.name, 'id': str(s.get('id')), 'name': s.get('name'),
                         'sector': sec, 'sec_strong': sec in strong, 'sec_weak': sec in weak})
    return sigs


def load_prices(ids: list[str]) -> dict:
    """載入快取 OHLCV，缺漏自 FinMind REST 補抓至 FETCH_END。"""
    price = {}
    for sid in ids:
        rows = {}
        p = CACHE / f'{sid}_ohlcv.csv'
        if p.exists():
            with open(p) as f:
                for r in csv.DictReader(f):
                    rows[r['date']] = r
        last = max(rows.keys()) if rows else '20260101'
        if last < FETCH_END.replace('-', ''):
            try:
                resp = requests.get('https://api.finmindtrade.com/api/v4/data', params={
                    'dataset': 'TaiwanStockPrice', 'data_id': sid,
                    'start_date': f'{last[:4]}-{last[4:6]}-{last[6:]}',
                    'end_date': FETCH_END}, timeout=30)
                for row in resp.json().get('data', []):
                    dd = row['date'].replace('-', '')
                    rows[dd] = {'date': dd, 'open': str(row['open']), 'high': str(row['max']),
                                'low': str(row['min']), 'close': str(row['close']),
                                'volume': str(row['Trading_Volume'])}
                time.sleep(0.3)
            except Exception as e:
                print(f'  {sid} 補抓失敗：{e}')
        price[sid] = rows
    return price


def ma10_outcome(rows: dict, entry: str) -> tuple | None:
    """MA10 出場法：收盤跌破 MA10 次日出；硬停損 -20%。"""
    td = sorted(rows)
    if entry not in td:
        return None
    ep = float(rows[entry]['open'])
    if ep == 0:
        return None
    closes = {d: float(rows[d]['close']) for d in td}
    for d in td:
        if d <= entry:
            continue
        c = closes[d]
        i = td.index(d)
        ma = sum(closes[td[j]] for j in range(i - 9, i + 1)) / 10 if i >= 9 else None
        if c <= ep * (1 + HARD_STOP):
            return ('LOSS', (c - ep) / ep * 100, d)
        if ma and c < ma:
            return ('WIN' if c > ep else 'LOSS', (c - ep) / ep * 100, d)
    last = td[-1]
    return ('OPEN', (closes[last] - ep) / ep * 100, last)


def evaluate(sigs: list[dict], price: dict) -> list[dict]:
    out = []
    for s in sigs:
        rows = price[s['id']]
        sub = sorted(d for d in rows if d <= s['date'])[-90:]
        if len(sub) < 30:
            continue
        df = pd.DataFrame([{
            'date': d, 'open': float(rows[d]['open']), 'high': float(rows[d]['high']),
            'low': float(rows[d]['low']), 'close': float(rows[d]['close']),
            'volume': float(rows[d]['volume'])} for d in sub])
        try:
            a = analyze_stock(df, s['id'])
        except Exception:
            continue
        after = sorted(d for d in rows if d > s['date'])
        oc = ma10_outcome(rows, after[0]) if after else None
        out.append({
            'date': s['date'], 'id': s['id'], 'name': s['name'],
            'score': a.signal_score, 'bias10': round(a.bias_ma10, 1),
            'sec_strong': s['sec_strong'], 'sec_weak': s['sec_weak'],
            'result': oc[0] if oc else 'N/A',
            'ret': round(oc[1], 1) if oc else None,
        })
    return out


def summarize(rows: list[dict]) -> str:
    done = [x for x in rows if x['result'] in ('WIN', 'LOSS')]
    def block(grp):
        if not grp:
            return '0筆', '-', '-'
        w = sum(1 for x in grp if x['result'] == 'WIN')
        avg = sum(x['ret'] for x in grp) / len(grp)
        return f'{len(grp)}筆', f'{w/len(grp)*100:.0f}%', f'{avg:+.1f}%'

    L = []
    n, wr, avg = block(done)
    L.append(f'## 整體\n已結算 {n}，勝率 {wr}，平均報酬 {avg}（未出場 '
             f'{sum(1 for x in rows if x["result"]=="OPEN")} 筆）\n')

    L.append('## 依模型評分 signal_score')
    L.append('| 評分區間 | 筆數 | 勝率 | 平均報酬 |')
    L.append('|---|---|---|---|')
    for lo, hi, lab in [(60, 69, '60-69 買入'), (70, 74, '70-74'), (75, 999, '75+ 強力買入')]:
        n, wr, avg = block([x for x in done if lo <= x['score'] <= hi])
        L.append(f'| {lab} | {n} | {wr} | {avg} |')

    L.append('\n## 依進場乖離率 bias_ma10')
    L.append('| 乖離區間 | 筆數 | 勝率 | 平均報酬 |')
    L.append('|---|---|---|---|')
    for lo, hi, lab in [(-999, 0, '≤0% 均線下'), (0, 2, '0-2% 貼均線'),
                        (2, 4, '2-4%'), (4, 6, '4-6%'), (6, 999, '>6% 明顯追高')]:
        n, wr, avg = block([x for x in done if lo < x['bias10'] <= hi])
        L.append(f'| {lab} | {n} | {wr} | {avg} |')

    L.append('\n## 依訊號月份')
    L.append('| 月份 | 筆數 | 勝率 | 平均報酬 |')
    L.append('|---|---|---|---|')
    by_m = defaultdict(list)
    for x in done:
        by_m[x['date'][:6]].append(x)
    for m in sorted(by_m):
        n, wr, avg = block(by_m[m])
        L.append(f'| {m[:4]}/{m[4:6]} | {n} | {wr} | {avg} |')

    L.append('\n## 依族群 regime')
    L.append('| 族群狀態 | 筆數 | 勝率 | 平均報酬 |')
    L.append('|---|---|---|---|')
    for cond, lab in [(lambda x: x['sec_strong'], '強勢'),
                      (lambda x: not x['sec_strong'] and not x['sec_weak'], '中性'),
                      (lambda x: x['sec_weak'], '弱勢')]:
        n, wr, avg = block([x for x in done if cond(x)])
        L.append(f'| {lab} | {n} | {wr} | {avg} |')

    L.append('\n## 過濾規則模擬')
    L.append('| 規則 | 筆數 | 勝率 | 平均報酬 |')
    L.append('|---|---|---|---|')
    for cond, lab in [(lambda x: True, '現況（全收）'),
                      (lambda x: x['bias10'] <= 2, '乖離 MA10 ≤2%'),
                      (lambda x: x['bias10'] <= 0, '乖離 MA10 ≤0%'),
                      (lambda x: x['sec_strong'], '族群強勢（單獨）'),
                      (lambda x: x['bias10'] <= 2 and x['sec_strong'], '乖離 ≤2% + 族群強勢'),
                      (lambda x: x['bias10'] <= 0 and x['sec_strong'], '乖離 ≤0% + 族群強勢'),
                      (lambda x: x['bias10'] <= 2 and x['sec_weak'], '乖離 ≤2% + 族群弱勢')]:
        n, wr, avg = block([x for x in done if cond(x)])
        L.append(f'| {lab} | {n} | {wr} | {avg} |')
    return '\n'.join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--report', help='輸出 markdown 報告路徑')
    ap.add_argument('--start', default='20260101', help='訊號起始日 YYYYMMDD')
    ap.add_argument('--end', default='20261231', help='訊號終止日 YYYYMMDD')
    args = ap.parse_args()

    sigs = collect_signals(args.start, args.end)
    ids = sorted(set(s['id'] for s in sigs))
    print(f'訊號 {len(sigs)} 筆 / {len(ids)} 檔（{args.start}~{args.end}）')
    price = load_prices(ids)
    rows = evaluate(sigs, price)
    print(f'完成評分 {len(rows)} 筆\n')
    report = summarize(rows)
    print(report)
    if args.report:
        Path(args.report).write_text(report + '\n', encoding='utf-8')
        print(f'\n報告已寫入：{args.report}')


if __name__ == '__main__':
    main()
