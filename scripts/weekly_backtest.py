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
from collections import Counter, defaultdict
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
    """以收盤價逐日判斷 TP/SL，最長持有至 prices 末端。prices[0] 為進場價。

    tp <= 0 表示不設停利（只留停損），sl <= 0 表示不設停損。
    """
    entry = prices[0]
    for k, px in enumerate(prices[1:], start=1):
        r = px / entry - 1
        if tp > 0 and r >= tp:
            return {'result': 'WIN', 'ret': r * 100, 'days': k}
        if sl > 0 and r <= -sl:
            return {'result': 'LOSS', 'ret': r * 100, 'days': k}
    r = prices[-1] / entry - 1
    return {'result': 'FLAT', 'ret': r * 100, 'days': len(prices) - 1}


def collect_signals(dates, panel, taiex, horizon, tp, sl, require_full=True, since=None):
    """逐日重建三層訊號，並計算每筆訊號的前瞻報酬與 TP/SL 結果。

    require_full=False 時保留前瞻期不足的訊號（用於近期個股明細），
    此時 retH 以最後一個可得交易日計，TP/SL 結果可能為 OPEN。
    """
    idx = {d: i for i, d in enumerate(dates)}
    by_date = defaultdict(list)
    for sid, series in panel.items():
        for d in series:
            by_date[d].append(series[d])

    signals = []
    for i, d in enumerate(dates):
        if require_full and i + horizon >= len(dates):
            break  # 前瞻期不足，不納入統計
        if since and d < since:
            continue
        strong = strong_sectors(by_date[d])
        fwd_dates = dates[i + 1:i + 1 + horizon]
        end_d = dates[min(i + horizon, len(dates) - 1)]
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
            fwd = [(x, panel[sid][x]['price']) for x in fwd_dates if x in panel[sid]]
            if require_full and len(fwd) < horizon:
                continue  # 個股在前瞻期內資料不完整
            if not fwd:
                continue
            prices = [rec['price']] + [px for _, px in fwd]
            sharpe = rec.get('sharpe')
            bias = bias_ma10(panel[sid], dates, i)
            tier = 1
            if isinstance(sharpe, (int, float)) and sharpe >= MIN_SHARPE:
                tier = 2
                if bias is not None and bias <= MAX_BIAS_MA10 and rec['sector'] in strong:
                    tier = 3
            sim = simulate(prices, tp, sl)
            if len(fwd) < horizon and sim['result'] == 'FLAT':
                sim['result'] = 'OPEN'   # 前瞻期未滿，尚未觸及 TP/SL
            signals.append({
                'date': d, 'id': sid, 'name': rec['name'], 'sector': rec['sector'],
                'tier': tier, 'price': rec['price'], 'rsi': rec.get('rsi'),
                'sharpe': sharpe, 'bias': None if bias is None else round(bias, 1),
                'ret5': (prices[5] / prices[0] - 1) * 100 if len(prices) > 5 else None,
                'ret10': (prices[10] / prices[0] - 1) * 100 if len(prices) > 10 else None,
                'retH': (prices[-1] / prices[0] - 1) * 100,
                'held': len(fwd), 'last_date': fwd[-1][0], 'last_price': fwd[-1][1],
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
    ap.add_argument('--tag', default='', help='輸出檔名後綴，用於保留不同參數的結果')
    ap.add_argument('--compare-exits', action='store_true',
                    help='額外輸出 L3 出場策略比較（純持有 / 各種 SL / TP+SL）')
    ap.add_argument('--detail-from', default='', help='YYYYMMDD；額外輸出該日之後的個股訊號明細')
    args = ap.parse_args()

    dates, panel, taiex = load_panel(args.start)
    signals = collect_signals(dates, panel, taiex, args.horizon, args.tp, args.sl)
    h = args.horizon

    weeks = defaultdict(list)
    for s in signals:
        weeks[week_of(s['date'])].append(s)

    out = [f'# 逐週走查回測（{dates[0]} → {dates[-1]}）', '',
           f"- 前瞻期：{h} 個交易日；"
           f"TP {args.tp:.0%} / SL {args.sl:.0%}（收盤價判斷，最長持有 {h} 日）"
           .replace('TP 0%', 'TP 不設').replace('SL 0%', 'SL 不設'),
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

    if args.compare_exits:
        # L3 出場策略比較：報酬與尾端風險（CVaR10 = 最差 10% 的平均）
        out += ['', '## L3 出場策略比較', '',
                '| 出場方式 | 平均報酬 | 中位數 | 勝率 | 標準差 | CVaR10 | 最差 | 虧損>15% | 平均持有日 |',
                '|---|---|---|---|---|---|---|---|---|']
        for label, e_tp, e_sl in (('純持有 %d 日' % h, 0.0, 0.0), ('SL 12% 不設 TP', 0.0, 0.12),
                                  ('SL 15% 不設 TP', 0.0, 0.15), ('SL 20% 不設 TP', 0.0, 0.20),
                                  ('TP 20% / SL 15%', 0.20, 0.15), ('TP 15% / SL 10%', 0.15, 0.10)):
            l3 = [x for x in collect_signals(dates, panel, taiex, h, e_tp, e_sl) if x['tier'] == 3]
            rets = sorted(x['sim_ret'] for x in l3)
            k = max(1, int(len(rets) * 0.1))
            out.append(
                f"| {label} | {statistics.mean(rets):+.2f}% | {statistics.median(rets):+.2f}% | "
                f"{sum(1 for x in rets if x > 0) / len(rets) * 100:.1f}% | {statistics.pstdev(rets):.2f}% | "
                f"{statistics.mean(rets[:k]):+.2f}% | {min(rets):+.2f}% | "
                f"{sum(1 for x in rets if x < -15)} | "
                f"{statistics.mean([x['sim_days'] for x in l3]):.1f} |")

    if args.detail_from:
        detail = collect_signals(dates, panel, taiex, h, args.tp, args.sl,
                                 require_full=False, since=args.detail_from)
        detail.sort(key=lambda r: (-r['retH']))
        out += ['', f'## 個股訊號明細（{args.detail_from} 起，含前瞻期未滿者）', '',
                '| 訊號日 | 股號 | 名稱 | 族群 | 層級 | 進場價 | 最新日 | 最新價 | 報酬 | 持有日 | TP/SL 結果 | RSI | sharpe | 乖離 |',
                '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
        for r in detail:
            bias_s = '—' if r['bias'] is None else f"{r['bias']:+.1f}%"
            out.append(
                f"| {r['date']} | {r['id']} | {r['name']} | {r['sector']} | L{r['tier']} | "
                f"{r['price']:.2f} | {r['last_date']} | {r['last_price']:.2f} | {r['retH']:+.2f}% | "
                f"{r['held']} | {r['sim_result']} ({r['sim_ret']:+.2f}%/{r['sim_days']}d) | "
                f"{r['rsi']} | {r['sharpe']} | {bias_s} |")
        # 個股彙總：同一檔多日重複觸發，合併看整體表現
        per_stock = defaultdict(list)
        for r in detail:
            per_stock[(r['id'], r['name'], r['sector'])].append(r)
        out += ['', f'### 個股彙總（{args.detail_from} 起）', '',
                '| 股號 | 名稱 | 族群 | 訊號數 | 平均報酬 | 最佳 | 最差 | 勝率 | 層級 | TP/SL 分布 | 訊號期間 |',
                '|---|---|---|---|---|---|---|---|---|---|---|']
        summary = sorted(per_stock.items(),
                         key=lambda kv: -statistics.mean([r['retH'] for r in kv[1]]))
        for (sid, name, sector), rs in summary:
            rets = [r['retH'] for r in rs]
            cnt = Counter(r['sim_result'] for r in rs)
            tiers_seen = sorted({r['tier'] for r in rs})
            out.append(
                f"| {sid} | {name} | {sector} | {len(rs)} | {statistics.mean(rets):+.2f}% | "
                f"{max(rets):+.2f}% | {min(rets):+.2f}% | "
                f"{sum(1 for x in rets if x > 0) / len(rets) * 100:.0f}% | "
                f"{'/'.join('L%d' % t for t in tiers_seen)} | "
                f"WIN {cnt['WIN']} / LOSS {cnt['LOSS']} / FLAT {cnt['FLAT']} / OPEN {cnt['OPEN']} | "
                f"{min(r['date'] for r in rs)[4:]}~{max(r['date'] for r in rs)[4:]} |")

        for label, sel in (('全部訊號', detail), ('L3 行動清單', [r for r in detail if r['tier'] == 3])):
            a = agg(sel)
            if a['n']:
                out += ['', f"**{label} 小結**：{a['n']} 筆，平均 {a['avg']:+.2f}%、中位數 {a['median']:+.2f}%、"
                            f"勝率 {a['win_rate']:.1f}%、最佳 {a['best']:+.2f}%、最差 {a['worst']:+.2f}%"]

    suffix = f'_{args.tag}' if args.tag else ''
    dest_md = ROOT / 'notes' / f'weekly_backtest_{dates[-1]}{suffix}.md'
    dest_md.write_text('\n'.join(out) + '\n', encoding='utf-8')
    (ROOT / 'notes' / f'weekly_backtest_{dates[-1]}{suffix}.json').write_text(
        json.dumps({'params': vars(args), 'weeks': weekly_json,
                    'overall': {k: agg(v) for k, v in tiers.items()}},
                   ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'已輸出：{dest_md}')


if __name__ == '__main__':
    main()
