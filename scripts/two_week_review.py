"""
兩週回顧：彙整近兩週每日掃描 (daily_reports/YYYYMMDD/summary.json) 的個股股價表現，
並追蹤「雙篩選命中但未進入行動清單」的標的後續走勢。

輸出：
  notes/two_week_review_<結束日>.md

用法：
  python scripts/two_week_review.py                 # 取最近 10 個掃描日
  python scripts/two_week_review.py --days 10
"""
import argparse
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS = ROOT / 'daily_reports'


def load_dates(days: int) -> list[str]:
    """取最近 N 個有 summary.json 的掃描日（升冪）。"""
    dates = sorted(
        d.name for d in REPORTS.iterdir()
        if d.is_dir() and d.name.isdigit() and (d / 'summary.json').exists()
    )
    return dates[-days:]


def build_panel(dates: list[str]) -> tuple[dict, dict, list]:
    """回傳 (panel, meta, dual_hits)。panel: {股號: {日期: 個股 record}}。"""
    panel: dict[str, dict] = {}
    meta: dict[str, tuple[str, str]] = {}
    dual_hits: list[dict] = []
    for d in dates:
        data = json.loads((REPORTS / d / 'summary.json').read_text(encoding='utf-8'))
        for sector, sec_data in data['sectors'].items():
            for st in sec_data['stocks']:
                panel.setdefault(st['id'], {})[d] = st
                meta[st['id']] = (st['name'], sector)
        for hit in data.get('dual_filter', []):
            dual_hits.append({**hit, 'date': d})
    return panel, meta, dual_hits


def stock_stats(series: dict, dates: list[str]) -> dict | None:
    """計算單檔個股期間內的價格統計；資料不足 2 日則回傳 None。"""
    avail = [d for d in dates if d in series]
    if len(avail) < 2:
        return None
    prices = [series[d]['price'] for d in avail]
    daily = [(prices[i] / prices[i - 1] - 1) * 100 for i in range(1, len(prices))]
    # 期間最大回落：以逐日的歷史高點為基準
    mdd = min((prices[i] / max(prices[:i + 1]) - 1) * 100 for i in range(len(prices)))
    last = series[avail[-1]]
    return {
        'first_date': avail[0], 'last_date': avail[-1],
        'p0': prices[0], 'p1': prices[-1],
        'ret': (prices[-1] / prices[0] - 1) * 100,
        'high': max(prices), 'low': min(prices),
        'mdd': mdd,
        'vol': statistics.pstdev(daily) if len(daily) > 1 else 0.0,
        'pos_from_high': (prices[-1] / max(prices) - 1) * 100,
        'rsi': last.get('rsi'), 'ret_20d': last.get('ret_20d'),
        'cv_sharpe': last.get('cv_sharpe'), 'signal': last.get('signal'),
        'ma10': last.get('ma10'), 'ma20': last.get('ma20'), 'ma60': last.get('ma60'),
    }


def reject_reason(hit: dict) -> str:
    """雙篩選命中但未進入行動清單的原因（乖離閘門 / 族群強勢閘門）。以最近一次命中為準。"""
    reasons = []
    if not hit.get('passes_bias'):
        reasons.append(f"乖離 {hit['bias_ma10']}% > 2%")
    if not hit.get('sector_strong'):
        reasons.append('族群非強勢')
    return ' + '.join(reasons) if reasons else '—（已進入行動清單）'


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=10, help='回顧的掃描日數（預設 10 ≈ 兩週）')
    args = ap.parse_args()

    dates = load_dates(args.days)
    panel, meta, dual_hits = build_panel(dates)
    d0, d1 = dates[0], dates[-1]

    m0 = json.loads((REPORTS / d0 / 'summary.json').read_text(encoding='utf-8'))['market']
    m1 = json.loads((REPORTS / d1 / 'summary.json').read_text(encoding='utf-8'))['market']

    rows = []
    for sid, series in panel.items():
        s = stock_stats(series, dates)
        if s:
            rows.append({'id': sid, 'name': meta[sid][0], 'sector': meta[sid][1], **s})
    rows.sort(key=lambda r: -r['ret'])

    # 族群兩週均漲（同時出現在首尾兩日的個股才納入）
    sec_rows = []
    for sector in {r['sector'] for r in rows}:
        rets = [r['ret'] for r in rows if r['sector'] == sector
                and r['first_date'] == d0 and r['last_date'] == d1]
        if rets:
            sec_rows.append((statistics.mean(rets), sector, len(rets)))
    sec_rows.sort(reverse=True)

    out = [f'# 兩週股價回顧（{d0} → {d1}，{len(dates)} 個交易日）', '']
    out.append(f"- 加權指數：{m0['加權指數']:.2f} → {m1['加權指數']:.2f}"
               f"（{(m1['加權指數'] / m0['加權指數'] - 1) * 100:+.2f}%）")
    out.append(f"- 櫃買指數：{m0['櫃買指數']:.2f} → {m1['櫃買指數']:.2f}"
               f"（{(m1['櫃買指數'] / m0['櫃買指數'] - 1) * 100:+.2f}%）")
    all_ret = [r['ret'] for r in rows]
    out.append(f"- 掃描池 {len(rows)} 檔：中位數 {statistics.median(all_ret):+.2f}%、"
               f"平均 {statistics.mean(all_ret):+.2f}%、"
               f"上漲 {sum(1 for x in all_ret if x > 0)} 檔 / 下跌 {sum(1 for x in all_ret if x < 0)} 檔")
    out += ['', '## 雙篩選命中追蹤（含未進入行動清單者）', '',
            '| 首次命中 | 股號 | 名稱 | 族群 | 命中價 | 最新價 | 命中後報酬 | 兩週區間 | 最大回落 | 命中次數 | 曾進榜 | 最新一次未進榜原因 |',
            '|---|---|---|---|---|---|---|---|---|---|---|---|']
    seen: dict[str, dict] = {}
    for hit in dual_hits:
        seen.setdefault(hit['id'], hit)
    for sid, first in sorted(seen.items(), key=lambda kv: kv[1]['date']):
        hits = [h for h in dual_hits if h['id'] == sid]
        s = next(r for r in rows if r['id'] == sid)
        fwd = (s['p1'] / first['price'] - 1) * 100
        passed = any(h['passes_all'] for h in hits)
        out.append(
            f"| {first['date']} | {sid} | {first['name']} | {first['sector']} | "
            f"{first['price']:.2f} | {s['p1']:.2f} | {fwd:+.2f}% | "
            f"{s['low']:.2f}~{s['high']:.2f} | {s['mdd']:.2f}% | {len(hits)} | "
            f"{'是' if passed else '否'} | {reject_reason(hits[-1])} |")

    out += ['', '## 族群兩週表現', '', '| 族群 | 檔數 | 兩週均漲 |', '|---|---|---|']
    out += [f'| {sec} | {n} | {m:+.2f}% |' for m, sec, n in sec_rows]

    def table(title, subset):
        block = ['', f'## {title}', '',
                 '| 股號 | 名稱 | 族群 | 起始價 | 最新價 | 兩週報酬 | 最大回落 | 日波動 | RSI | 訊號 |',
                 '|---|---|---|---|---|---|---|---|---|---|']
        for r in subset:
            block.append(
                f"| {r['id']} | {r['name']} | {r['sector']} | {r['p0']:.2f} | {r['p1']:.2f} | "
                f"{r['ret']:+.2f}% | {r['mdd']:.2f}% | {r['vol']:.2f}% | {r['rsi']} | {r['signal']} |")
        return block

    out += table('兩週漲幅前 20', rows[:20])
    out += table('兩週跌幅前 15', rows[-15:][::-1])

    dest = ROOT / 'notes' / f'two_week_review_{d1}.md'
    dest.write_text('\n'.join(out) + '\n', encoding='utf-8')
    print(f'已輸出：{dest}')


if __name__ == '__main__':
    main()
