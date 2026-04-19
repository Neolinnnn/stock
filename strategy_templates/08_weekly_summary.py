"""
週報生成（週五 14:45）
彙整本週 5 個交易日的掃描結果，產生週變化圖 + Top Picks

用法：
  python strategy_templates/08_weekly_summary.py
"""
import sys, os, json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams

rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'SimHei', 'Arial Unicode MS']
rcParams['axes.unicode_minus'] = False


def run_weekly_summary():
    today = datetime.now()
    week_reports = []

    # 找過去 7 天的日報
    for i in range(7):
        d = today - timedelta(days=i)
        date_str = d.strftime('%Y%m%d')
        path = Path(f'daily_reports/{date_str}/summary.json')
        if path.exists():
            with open(path, encoding='utf-8') as f:
                week_reports.append(json.load(f))

    if len(week_reports) < 2:
        print(f"⚠ 週內日報不足（{len(week_reports)} 份），無法生成週報")
        return None

    week_reports.reverse()  # 由舊到新
    out_dir = Path(f'daily_reports/weekly_{today.strftime("%Y%m%d")}')
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*78}")
    print(f"  週報生成 | {today:%Y-%m-%d}")
    print(f"  涵蓋：{len(week_reports)} 個交易日")
    print(f"{'='*78}\n")

    # 各族群一週平均報酬變化
    sector_trend = {}
    for report in week_reports:
        for sector, data in report['sectors'].items():
            if sector not in sector_trend:
                sector_trend[sector] = []
            sector_trend[sector].append(data['avg_ret_20d'])

    # 繪製族群趨勢圖
    fig, ax = plt.subplots(figsize=(12, 6))
    dates = [r['date'] for r in week_reports]
    for sector, rets in sector_trend.items():
        ax.plot(dates, rets, marker='o', label=sector, linewidth=2)
    ax.set_title('本週各族群 20 日平均報酬變化')
    ax.set_ylabel('平均報酬 (%)')
    ax.axhline(0, color='gray', linewidth=0.5)
    ax.legend(loc='best', ncol=2)
    ax.grid(alpha=0.3)
    plt.xticks(rotation=30)
    plt.tight_layout()
    chart_path = out_dir / '01_weekly_sector_trend.png'
    plt.savefig(chart_path, dpi=100, bbox_inches='tight')
    plt.close()

    # 本週最強/最弱族群
    last = week_reports[-1]['sectors']
    first = week_reports[0]['sectors']
    changes = {}
    for sector in last:
        if sector in first:
            changes[sector] = last[sector]['avg_ret_20d'] - first[sector]['avg_ret_20d']
    sorted_changes = sorted(changes.items(), key=lambda x: x[1], reverse=True)

    # 本週累計 BUY 訊號次數
    buy_counts = {}
    for report in week_reports:
        for sector, data in report['sectors'].items():
            for st in data['stocks']:
                if st['signal'] == 'BUY':
                    key = f"{st['id']} {st['name']}"
                    buy_counts[key] = buy_counts.get(key, 0) + 1
    top_buys = sorted(buy_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    summary = {
        'week_ending': today.strftime('%Y-%m-%d'),
        'days_covered': len(week_reports),
        'sector_changes': [{'sector': s, 'change': round(c, 2)} for s, c in sorted_changes],
        'top_buys': [{'stock': k, 'buy_days': v} for k, v in top_buys],
        'chart_path': str(chart_path),
    }

    # 輸出
    json_path = out_dir / 'weekly.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    md = [f"# 週報 {today.strftime('%Y-%m-%d')}\n\n"]
    md.append(f"**涵蓋**：{len(week_reports)} 個交易日\n")
    md.append(f"\n## 族群一週變化（動能變化）\n")
    for s, c in sorted_changes:
        md.append(f"- {s}：{c:+.2f} pp\n")
    md.append(f"\n## 本週累計 BUY 訊號 Top 10\n")
    for k, v in top_buys:
        md.append(f"- {k} — {v} 次\n")

    md_path = out_dir / 'weekly.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(''.join(md))

    print(f"✅ 完成")
    print(f"   {json_path}")
    print(f"   {md_path}")
    print(f"   {chart_path}")

    # 寫入 GitHub Pages 靜態資料
    docs_dir = Path(__file__).parent.parent / 'docs'
    docs_dir.mkdir(exist_ok=True)
    with open(docs_dir / 'weekly.json', 'w', encoding='utf-8') as f:
        json.dump(build_weekly_payload(summary), f, ensure_ascii=False, indent=2, default=str)
    print('  docs/weekly.json 已更新')

    # Notion 上傳
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(__file__))
        from notion_upload import upload_weekly_report
        page_id = upload_weekly_report(summary)
        print(f"   Notion：{page_id}")
    except Exception as e:
        print(f"   Notion 上傳失敗：{e}")

    return summary


def build_weekly_payload(summary):
    return {
        'meta': {'date': summary.get('week_ending', ''), 'days': summary.get('days_covered', 0)},
        'changes': [{'sector': c['sector'], 'change': c['change']} for c in summary.get('sector_changes', [])],
        'buys': [{'stock': b['stock'], 'days': b['buy_days']} for b in summary.get('top_buys', [])],
    }


if __name__ == '__main__':
    run_weekly_summary()
