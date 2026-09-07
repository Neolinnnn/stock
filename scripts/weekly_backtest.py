"""
逐週走查回測（walk-forward）：以 docs/YYYYMMDD.json 的歷史每日面板重建三層篩選訊號，
逐週統計訊號的前瞻報酬、勝率與相對大盤超額，並模擬 TP/SL 出場。

三層篩選（重建自 scripts/daily_scan.py 的邏輯）：
  L1 訊號層      signal == BUY
  L2 雙篩選層    L1 + sharpe >= MIN_SHARPE
  L3 行動清單層  L2 + 乖離(MA10) <= MAX_BIAS_MA10 且所屬族群強勢（族群 ret20 均值 > 3）

註：歷史面板缺 cv_win_rate / cv_max_dd 兩個欄位，L2 僅以 sharpe 近似，
    故 L2 為實際雙篩選的「超集合」（略寬鬆）；L3 的乖離與族群強勢由面板重算，
    已對 20260904 驗證與線上結果一致。

輸出：
  notes/weekly_backtest_<結束日>.md
  notes/weekly_backtest_<結束日>.json

用法：
  python scripts/weekly_backtest.py [--start 20230223] [--horizon 20] [--tp 0.15] [--sl 0.10]
"""
import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCS = ROOT / 'docs'

MIN_SHARPE = 0.3      # 對齊 daily_scan.py 的 cv_sharpe 門檻
MAX_BIAS_MA10 = 2.0   # 對齊 daily_scan.py 的乖離閘門
STRONG_SECTOR_RET20 = 3.0


def load_panel(start: str):
    """讀取 docs 每日面板，回傳 (dates, panel, taiex)。panel: {股號: {日期: rec}}。"""
    files = sorted(p for p in DOCS.glob('2*.json') if p.stem.isdigit() and p.stem >= start)
    dates, panel, taiex = [], defaultdict(dict), {}
    for p in files:
        d = p.stem
        data = json.loads(p.read_text(encoding='utf-8'))
        stocks = data.get('stocks') or []
        if not stocks:
            continue
        dates.append(d)
        taiex[d] = data.get('meta', {}).get('加權指數')
        for s in stocks:
            panel[s['id']][d] = s
    return dates, panel, taiex


def strong_sectors(recs: list[dict]) -> set[str]:
    """族群強勢判定：族群內個股 ret20 均值 > 3%。"""
    buckets = defaultdict(list)
    for s in recs:
        if isinstance(s.get('ret20'), (int, float)):
            buckets[s['sector']].append(s['ret20'])
    return {k for k, v in buckets.items() if statistics.mean(v) > STRONG_SECTOR_RET20}


def bias_ma10(panel_id: dict, dates: list[str], i: int) -> float | None:
    """以面板收盤價重算 (price - MA10) / MA10 * 100；不足 10 筆回傳 None。"""
    window = [panel_id[d]['price'] for d in dates[max(0, i - 9):i + 1] if d in panel_id]
    if len(window) < 10:
        return None
    ma10 = sum(window) / len(window)
    return (window[-1] - ma10) / ma10 * 100 if ma10 else None


def simulate(prices: list[float], tp: float, sl: float) -> dict:
    """以收盤價逐日判斷 TP/SL，最長持有至 prices 末端。prices[0] 為進場價。"""
    entry = prices[0]
    for k, px in enumerate(prices[1:], start=1):
        r = px / entry - 1
        if r >= tp:
            return {'result': 'WIN', 'ret': r * 100, 'days': k}
        if r <= -sl:
            return {'result': 'LOSS', 'ret': r * 100, 'days': k}
    r = prices[-1] / entry - 1
    return {'result': 'FLAT', 'ret': r * 100, 'days': len(prices) - 1}


def collect_signals(dates, panel, taiex, horizon, tp, sl):
    """逐日重建三層訊號，並計算每筆訊號的前瞻報酬與 TP/SL 結果。"""
    idx = {d: i for i, d in enumerate(dates)}
    by_date = defaultdict(list)
    for sid, series in panel.items():
        for d in series:
            by_date[d].append(series[d])

    signals = []
    for i, d in enumerate(dates):
        if i + horizon >= len(dates):
            break  # 前瞻期不足，不納入統計
        strong = strong_sectors(by_date[d])
        fwd_dates = dates[i + 1:i + 1 + horizon]
        end_d = dates[i + horizon]
        # 基準：當日掃描池等權前瞻報酬（歷史 meta 多數缺加權指數，改用掃描池自身為基準）
        pool = [(panel[r['id']][end_d]['price'] / r['price'] - 1) * 100
                for r in by_date[d] if end_d in panel[r['id']] and r['price']]
        bench = statistics.mean(pool) if pool else None
        taiex_ret = ((taiex[end_d] / taiex[d] - 1) * 100
                     if taiex.get(d) and taiex.get(end_d) else None)
        for rec in by_date[d]:
            if rec.get('signal') != 'BUY':
                continue
            sid = rec['id']
            prices = [rec['price']] + [panel[sid][x]['price'] for x in fwd_dates if x in panel[sid]]
            if len(prices) < horizon + 1:
                continue  # 個股在前瞻期內資料不完整
            sharpe = rec.get('sharpe')
            bias = bias_ma10(panel[sid], dates, i)
            tier = 1
            if isinstance(sharpe, (int, float)) and sharpe >= MIN_SHARPE:
                tier = 2
                if bias is not None and bias <= MAX_BIAS_MA10 and rec['sector'] in strong:
                    tier = 3
            sim = simulate(prices, tp, sl)
            signals.append({
                'date': d, 'id': sid, 'name': rec['name'], 'sector': rec['sector'],
                'tier': tier, 'price': rec['price'], 'rsi': rec.get('rsi'),
                'sharpe': sharpe, 'bias': None if bias is None else round(bias, 1),
                'ret5': (prices[5] / prices[0] - 1) * 100 if len(prices) > 5 else None,
                'ret10': (prices[10] / prices[0] - 1) * 100 if len(prices) > 10 else None,
                'retH': (prices[horizon] / prices[0] - 1) * 100,
                'bench': bench, 'taiex': taiex_ret,
                'sim_result': sim['result'], 'sim_ret': sim['ret'], 'sim_days': sim['days'],
            })
    return signals


def agg(rows: list[dict], key='retH') -> dict:
    """一組訊號的績效彙總。"""
    vals = [r[key] for r in rows if r[key] is not None]
    if not vals:
        return {'n': 0}
    exc = [r[key] - r['bench'] for r in rows if r[key] is not None and r['bench'] is not None]
    wins = [r for r in rows if r['sim_result'] == 'WIN']
    loss = [r for r in rows if r['sim_result'] == 'LOSS']
    return {
        'n': len(vals),
        'avg': statistics.mean(vals),
        'median': statistics.median(vals),
        'win_rate': sum(1 for v in vals if v > 0) / len(vals) * 100,
        'excess': statistics.mean(exc) if exc else None,
        'best': max(vals), 'worst': min(vals),
        'tp_hit': len(wins) / len(rows) * 100,
        'sl_hit': len(loss) / len(rows) * 100,
        'sim_avg': statistics.mean([r['sim_ret'] for r in rows]),
    }


def week_of(date_str: str) -> str:
    y, w, _ = datetime.strptime(date_str, '%Y%m%d').isocalendar()
    return f'{y}-W{w:02d}'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--start', default='20230223')
    ap.add_argument('--horizon', type=int, default=20, help='前瞻交易日數（預設 20 ≈ 一個月）')
    ap.add_argument('--tp', type=float, default=0.15)
    ap.add_argument('--sl', type=float, default=0.10)
    args = ap.parse_args()

    dates, panel, taiex = load_panel(args.start)
    signals = collect_signals(dates, panel, taiex, args.horizon, args.tp, args.sl)
    h = args.horizon

    weeks = defaultdict(list)
    for s in signals:
        weeks[week_of(s['date'])].append(s)

    out = [f'# 逐週走查回測（{dates[0]} → {dates[-1]}）', '',
           f'- 前瞻期：{h} 個交易日；TP {args.tp:.0%} / SL {args.sl:.0%}（收盤價判斷，最長持有 {h} 日）',
           f'- 訊號樣本：{len(signals)} 筆（前瞻期不足的最後 {h} 個交易日不納入）',
           f'- 基準：當日掃描池等權 {h} 日報酬（歷史 meta 多數無加權指數）\n- L1 = BUY；L2 = L1 + sharpe ≥ {MIN_SHARPE}；L3 = L2 + 乖離 ≤ {MAX_BIAS_MA10}% 且族群強勢', '']

    out += ['## 各層總體績效', '',
            f'| 層級 | 訊號數 | 平均{h}日 | 中位數 | 勝率 | 超額(對掃描池) | TP先觸 | SL先觸 | TP/SL模擬報酬 |',
            '|---|---|---|---|---|---|---|---|---|']
    tiers = {'L1 訊號 (BUY)': [s for s in signals if s['tier'] >= 1],
             'L2 雙篩選': [s for s in signals if s['tier'] >= 2],
             'L3 行動清單': [s for s in signals if s['tier'] == 3]}
    for label, rows in tiers.items():
        a = agg(rows)
        if not a['n']:
            continue
        out.append(f"| {label} | {a['n']} | {a['avg']:+.2f}% | {a['median']:+.2f}% | {a['win_rate']:.1f}% | "
                   f"{a['excess']:+.2f}% | {a['tp_hit']:.1f}% | {a['sl_hit']:.1f}% | {a['sim_avg']:+.2f}% |")

    def fmt(a, field, pct=True):
        """彙總欄位格式化；無樣本或無值以 — 呈現。"""
        if not a.get('n') or a.get(field) is None:
            return '—'
        return f"{a[field]:+.2f}%" if pct else f"{a[field]:.1f}%"

    out += ['', '## 持有天期比較（各層平均報酬 / 勝率）', '',
            '| 層級 | 5 日 | 10 日 | %d 日 |' % h, '|---|---|---|---|']
    for label, rows in tiers.items():
        cells = []
        for key in ('ret5', 'ret10', 'retH'):
            a = agg(rows, key)
            cells.append('—' if not a['n'] else f"{a['avg']:+.2f}% / {a['win_rate']:.0f}%")
        out.append(f"| {label} | " + ' | '.join(cells) + ' |')

    out += ['', '## 分年績效', '',
            '| 年度 | L1 數 | L1 平均 | L2 數 | L2 平均 | L3 數 | L3 平均 | L3 勝率 | L3 超額 |',
            '|---|---|---|---|---|---|---|---|---|']
    for year in sorted({s['date'][:4] for s in signals}):
        ys = [s for s in signals if s['date'][:4] == year]
        a1, a2, a3 = agg(ys), agg([s for s in ys if s['tier'] >= 2]), agg([s for s in ys if s['tier'] == 3])
        out.append(f"| {year} | {a1['n']} | {fmt(a1,'avg')} | {a2['n']} | {fmt(a2,'avg')} | "
                   f"{a3['n']} | {fmt(a3,'avg')} | {fmt(a3,'win_rate',False)} | {fmt(a3,'excess')} |")

    out += ['', '## 逐週明細', '',
            f'| 週別 | 起訖 | L1 | L2 | L3 | L1 平均{h}日 | L2 平均 | L3 平均 | L3 勝率 | 掃描池同期 | L3 超額 |',
            '|---|---|---|---|---|---|---|---|---|---|---|']

    weekly_json = []
    for wk in sorted(weeks):
        rows = weeks[wk]
        ds = sorted({r['date'] for r in rows})
        a1 = agg(rows)
        a2 = agg([r for r in rows if r['tier'] >= 2])
        a3 = agg([r for r in rows if r['tier'] == 3])
        benches = [r['bench'] for r in rows if r['bench'] is not None]
        bench = statistics.mean(benches) if benches else None
        out.append(f"| {wk} | {ds[0][4:]}~{ds[-1][4:]} | {a1['n']} | {a2['n']} | {a3['n']} | "
                   f"{fmt(a1,'avg')} | {fmt(a2,'avg')} | {fmt(a3,'avg')} | "
                   f"{fmt(a3,'win_rate',False)} | {bench:+.2f}% | {fmt(a3,'excess')} |"
                   if bench is not None else
                   f"| {wk} | {ds[0][4:]}~{ds[-1][4:]} | {a1['n']} | {a2['n']} | {a3['n']} | "
                   f"{fmt(a1,'avg')} | {fmt(a2,'avg')} | {fmt(a3,'avg')} | {fmt(a3,'win_rate',False)} | — | — |")
        weekly_json.append({'week': wk, 'start': ds[0], 'end': ds[-1],
                            'L1': a1, 'L2': a2, 'L3': a3, 'bench': bench})

    dest_md = ROOT / 'notes' / f'weekly_backtest_{dates[-1]}.md'
    dest_md.write_text('\n'.join(out) + '\n', encoding='utf-8')
    (ROOT / 'notes' / f'weekly_backtest_{dates[-1]}.json').write_text(
        json.dumps({'params': vars(args), 'weeks': weekly_json,
                    'overall': {k: agg(v) for k, v in tiers.items()}},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'已輸出：{dest_md}')


if __name__ == '__main__':
    main()
