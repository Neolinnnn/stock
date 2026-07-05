"""
週報生成（週五 14:45）
彙整本週 5 個交易日的掃描結果，產生六節式週報：
  一、市場總覽　二、族群輪動矩陣　三、訊號榜　四、持倉週記
  五、風險警示　六、AI 週評（Gemini）

用法：
  python scripts/weekly_summary.py
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

TIER_LABEL = {'strong': '強', 'neutral': '中', 'weak': '弱'}


def compute_sector_metrics(week_reports, prev_changes):
    """由舊到新排序的 week_reports，計算每族群本週變化、最新水位、上週變化。

    回傳依 change 由大到小排序的 list[dict]。
    """
    first = week_reports[0]['sectors']
    last = week_reports[-1]['sectors']
    metrics = []
    for sector, data in last.items():
        if sector not in first:
            continue
        level = data['avg_ret_20d']
        change = level - first[sector]['avg_ret_20d']
        metrics.append({
            'sector': sector,
            'change': round(change, 2),
            'level': round(level, 2),
            'prev_change': prev_changes.get(sector),
        })
    metrics.sort(key=lambda m: m['change'], reverse=True)
    return metrics


def load_prev_week_changes(today_str, base_dir=Path('daily_reports')):
    """找早於 today_str 的最近一份 weekly_*/weekly.json，回傳 {sector: change}。"""
    base_dir = Path(base_dir)
    candidates = []
    for p in base_dir.glob('weekly_*/weekly.json'):
        tag = p.parent.name.replace('weekly_', '')
        if tag.isdigit() and tag < today_str:
            candidates.append((tag, p))
    if not candidates:
        return {}
    _, latest = max(candidates, key=lambda x: x[0])
    with open(latest, encoding='utf-8') as f:
        data = json.load(f)
    return {c['sector']: c['change'] for c in data.get('sector_changes', [])}


def compute_market_week(week_reports):
    """彙整一週大盤：週漲跌（各日漲跌幅複利）、收盤、多空閘門狀態。

    market 欄位缺漏時回傳對應 None；positions 缺漏時閘門欄位為 None。
    """
    def _compound(key):
        acc, seen = 1.0, False
        for r in week_reports:
            pct = (r.get('market') or {}).get(key)
            if pct is None:
                continue
            acc *= 1 + pct / 100
            seen = True
        return round((acc - 1) * 100, 2) if seen else None

    last_market = week_reports[-1].get('market') or {}
    last_pos = week_reports[-1].get('positions') or {}
    return {
        'taiex_close': last_market.get('加權指數'),
        'taiex_week_pct': _compound('漲跌幅'),
        'otc_close': last_market.get('櫃買指數'),
        'otc_week_pct': _compound('櫃買漲跌幅'),
        'taiex_bull': last_pos.get('taiex_bull'),
        'taiex_ma60': last_pos.get('taiex_ma60'),
    }


def build_rotation_matrix(sector_metrics, v_turn_top=3):
    """依水位×動能分四象限（分界與前端 sectorQuadrant 一致：>=0）。

    v_turn：上週動能為負、本週翻正的族群，取擺動幅度最大前 v_turn_top 名。
    """
    matrix = {'leading': [], 'turning': [], 'cooling': [], 'weak': []}
    for m in sector_metrics:
        if m['level'] >= 0 and m['change'] >= 0:
            matrix['leading'].append(m)
        elif m['level'] < 0 and m['change'] >= 0:
            matrix['turning'].append(m)
        elif m['level'] >= 0 and m['change'] < 0:
            matrix['cooling'].append(m)
        else:
            matrix['weak'].append(m)
    v_turns = [m for m in sector_metrics
               if m.get('prev_change') is not None
               and m['prev_change'] < 0 and m['change'] > 0]
    v_turns.sort(key=lambda m: m['change'] - m['prev_change'], reverse=True)
    matrix['v_turn'] = v_turns[:v_turn_top]
    return matrix


def collect_week_signals(week_reports, top_n=10):
    """週累計 BUY 次數榜，附最近一次非空 chip_tier 與是否曾過閘門。

    回傳 [{'stock','buy_days','chip_tier','gate'}]，依次數由大到小。
    """
    buy_counts, tiers = {}, {}
    gate_ids = set()
    for report in week_reports:  # 由舊到新，tier 自然留下最新值
        for data in report['sectors'].values():
            for st in data['stocks']:
                if st.get('signal') == 'BUY':
                    key = f"{st['id']} {st['name']}"
                    buy_counts[key] = buy_counts.get(key, 0) + 1
                if st.get('chip_tier') is not None:
                    tiers[st['id']] = st['chip_tier']
        for b in (report.get('positions') or {}).get('gate_buys', []):
            gate_ids.add(b['id'])
    ranked = sorted(buy_counts.items(), key=lambda x: x[1], reverse=True)[:top_n]
    return [{
        'stock': k,
        'buy_days': v,
        'chip_tier': tiers.get(k.split(' ')[0]),
        'gate': k.split(' ')[0] in gate_ids,
    } for k, v in ranked]


def collect_positions_week(week_reports):
    """彙整一週持倉動態：新進場、出場、期末持倉（含未實現損益）。"""
    entries, exits = [], []
    seen_e, seen_x = set(), set()
    for report in week_reports:
        pos = report.get('positions') or {}
        day = report.get('date', '')
        for e in pos.get('new_entries', []):
            k = (e['id'], day)
            if k not in seen_e:
                seen_e.add(k)
                entries.append({**e, 'date': day})
        for x in pos.get('new_exits', []):
            k = (x['id'], day)
            if k not in seen_x:
                seen_x.add(k)
                exits.append({**x, 'date': day})

    # 期末持倉：最後一日 holding（去重）＋ 以當日收盤估未實現損益
    last = week_reports[-1]
    price_by_id = {}
    for data in last['sectors'].values():
        for st in data['stocks']:
            price_by_id[st['id']] = st.get('price')
    holding, seen_h = [], set()
    for p in (last.get('positions') or {}).get('holding', []):
        k = (p['id'], p.get('signal_date'))
        if k in seen_h:
            continue
        seen_h.add(k)
        price = price_by_id.get(p['id'])
        entry = p.get('entry_price')
        pnl = round((price - entry) / entry * 100, 2) if price and entry else None
        holding.append({
            'id': p['id'], 'name': p['name'],
            'entry_price': entry, 'price': price, 'pnl_pct': pnl,
            'phase': p.get('phase'),
            'days_since_high': p.get('days_since_high'),
        })
    return {'entries': entries, 'exits': exits, 'holding': holding}


def collect_alerts_week(week_reports, top_n=10):
    """週內警示累計：同檔同類型計次，依次數由大到小取前 top_n。"""
    counts = {}
    for report in week_reports:
        daily_seen = set()  # 同檔跨族群同日重複警示只計一次
        for a in report.get('alerts', []):
            k = (a['id'], a['name'], a['type'])
            if k in daily_seen:
                continue
            daily_seen.add(k)
            if k not in counts:
                counts[k] = {'id': a['id'], 'name': a['name'],
                             'type': a['type'], 'days': 0, 'detail': ''}
            counts[k]['days'] += 1
            counts[k]['detail'] = a.get('detail', '')  # 留最新一日
    ranked = sorted(counts.values(), key=lambda x: x['days'], reverse=True)
    return ranked[:top_n]


def build_narrative_context(sector_metrics, top_buys, market=None,
                            rotation=None, positions_week=None, alerts_week=None):
    """組裝給 Gemini 的 weekly_report context.data。"""
    ctx = {
        'sector_metrics': sector_metrics,
        'accelerating': sector_metrics[:3],
        'decelerating': sorted(sector_metrics, key=lambda m: m['change'])[:3],
        'top_buys': top_buys,
    }
    if market is not None:
        ctx['market'] = market
    if rotation is not None:
        ctx['rotation_matrix'] = rotation
    if positions_week is not None:
        ctx['positions_week'] = positions_week
    if alerts_week is not None:
        ctx['alerts_week'] = alerts_week
    return ctx


def generate_narrative(writer, context_data, date_str):
    """呼叫 Gemini 生成三段週報敘事；任何失敗回傳空字串（前端會隱藏敘事卡）。"""
    extra = ('請輸出三段，第一段標題「本週輪動回顧」描述族群強弱輪動，'
             '第二段標題「下週聚焦」點出值得追蹤的族群與個股，'
             '第三段標題「風險提醒」根據 alerts_week 與 rotation_matrix.cooling '
             '提示過熱與轉弱風險，繁體中文、各 200 字內。'
             '直接輸出三段內容，不要任何開場白、前言或結語。')
    try:
        return writer.generate(
            task='weekly_report',
            context={'date': date_str, 'data': context_data, 'extra': extra},
        )
    except Exception as e:
        print(f'   ⚠ Gemini 週報敘事生成失敗，略過：{e}')
        return ''


def _fmt_sector(m):
    return f"{m['sector']}（{m['change']:+.2f} pp / 水位 {m['level']:+.2f}）"


def render_markdown(summary):
    """由 summary dict 產生六節式 weekly.md 內容。"""
    md = [f"# 週報 {summary['week_ending']}（涵蓋 {summary['days_covered']} 個交易日）\n"]

    # 一、市場總覽
    mkt = summary.get('market') or {}
    md.append("\n## 一、市場總覽\n")
    md.append("| 指標 | 收盤 | 本週變化 |\n|------|------|---------|\n")
    if mkt.get('taiex_close') is not None:
        pct = mkt.get('taiex_week_pct')
        md.append(f"| 加權指數 | {mkt['taiex_close']:,.2f} | "
                  f"{pct:+.2f}% |\n" if pct is not None else
                  f"| 加權指數 | {mkt['taiex_close']:,.2f} | — |\n")
    if mkt.get('otc_close') is not None:
        pct = mkt.get('otc_week_pct')
        md.append(f"| 櫃買指數 | {mkt['otc_close']:,.2f} | "
                  f"{pct:+.2f}% |\n" if pct is not None else
                  f"| 櫃買指數 | {mkt['otc_close']:,.2f} | — |\n")
    if mkt.get('taiex_bull') is not None:
        gate = '🟢 多頭' if mkt['taiex_bull'] else '🔴 空頭'
        ma60 = f"（指數 {'>' if mkt['taiex_bull'] else '<'} 60MA {mkt['taiex_ma60']:,.0f}）" \
            if mkt.get('taiex_ma60') else ''
        md.append(f"| 多空閘門 | {gate}{ma60} | — |\n")

    # 二、族群輪動矩陣
    rot = summary.get('rotation_matrix') or {}
    md.append("\n## 二、族群輪動矩陣（水位 × 本週動能）\n")
    for key, title in (('leading', '🔥 領漲續強'), ('turning', '🌱 落底轉強'),
                       ('cooling', '⚠️ 高檔轉弱'), ('weak', '❄️ 弱勢整理')):
        items = rot.get(key, [])
        if items:
            md.append(f"- **{title}**：{'、'.join(_fmt_sector(m) for m in items)}\n")
    if rot.get('v_turn'):
        md.append("- **動能 V 轉**（上週負、本週翻正）："
                  + '、'.join(f"{m['sector']} {m['prev_change']:+.1f}→{m['change']:+.1f}"
                              for m in rot['v_turn']) + "\n")

    # 三、訊號榜
    md.append("\n## 三、訊號榜（本週累計 BUY × 籌碼分層）\n")
    md.append("| 股票 | BUY 次數 | 籌碼 | 過閘門 |\n|------|---------|------|--------|\n")
    for b in summary.get('top_buys', []):
        tier = TIER_LABEL.get(b.get('chip_tier'), '—')
        gate = '✅' if b.get('gate') else '—'
        md.append(f"| {b['stock']} | {b['buy_days']} | {tier} | {gate} |\n")

    # 四、持倉週記
    pw = summary.get('positions_week') or {}
    md.append("\n## 四、持倉週記\n")
    ent = pw.get('entries', [])
    ext = pw.get('exits', [])
    ent_txt = '、'.join(f"{e['id']} {e['name']}" for e in ent) or '無'
    md.append(f"- 本週新進場：{ent_txt}\n")
    if ext:
        md.append("- 本週出場：" + '、'.join(
            f"{x['id']} {x['name']}（{x.get('return_pct', 0):+.1f}%・{x.get('exit_reason', '')}）"
            for x in ext) + "\n")
    else:
        md.append("- 本週出場：無\n")
    hold = pw.get('holding', [])
    if hold:
        md.append("- 持有中：" + '、'.join(
            f"{h['name']}（{h['pnl_pct']:+.1f}%、距高點 {h['days_since_high']} 天）"
            if h.get('pnl_pct') is not None else f"{h['name']}（無今日報價）"
            for h in hold) + "\n")
    else:
        md.append("- 持有中：無\n")

    # 五、風險警示
    alerts = summary.get('alerts_week', [])
    md.append("\n## 五、風險警示（週內累計）\n")
    if alerts:
        for a in alerts:
            md.append(f"- {a['id']} {a['name']}：{a['type']} × {a['days']} 天（{a['detail']}）\n")
    else:
        md.append("- 無\n")

    # 六、AI 週評
    if summary.get('narrative'):
        md.append("\n## 六、AI 週評\n\n")
        md.append(summary['narrative'] + "\n")

    return ''.join(md)


def run_weekly_summary(as_of=None, upload_notion=True):
    today = as_of or datetime.now()
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

    # 各節資料彙整
    prev_changes = load_prev_week_changes(today.strftime('%Y%m%d'))
    sector_metrics = compute_sector_metrics(week_reports, prev_changes)
    market = compute_market_week(week_reports)
    rotation = build_rotation_matrix(sector_metrics)
    top_buys = collect_week_signals(week_reports)
    positions_week = collect_positions_week(week_reports)
    alerts_week = collect_alerts_week(week_reports)

    summary = {
        'week_ending': today.strftime('%Y-%m-%d'),
        'days_covered': len(week_reports),
        'market': market,
        'sector_changes': sector_metrics,
        'rotation_matrix': rotation,
        'top_buys': top_buys,
        'positions_week': positions_week,
        'alerts_week': alerts_week,
        'chart_path': str(chart_path),
        'narrative': '',
    }

    # AI 週評（Gemini；失敗安全略過）
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).parent.parent / '.env')
        except ImportError:
            pass
        from gemini_writer import GeminiWriter
        narrative_ctx = build_narrative_context(
            sector_metrics, top_buys, market=market, rotation=rotation,
            positions_week=positions_week, alerts_week=alerts_week)
        summary['narrative'] = generate_narrative(
            GeminiWriter(), narrative_ctx, today.strftime('%Y-%m-%d'))
    except Exception as e:
        print(f'   ⚠ 敘事模組載入失敗，略過：{e}')
        summary['narrative'] = ''

    # 輸出
    json_path = out_dir / 'weekly.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    md_path = out_dir / 'weekly.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(render_markdown(summary))

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

    import shutil
    shutil.copy(chart_path, docs_dir / 'weekly_sector_trend.png')
    print('  docs/weekly_sector_trend.png 已更新')

    # Notion 上傳
    if upload_notion:
        try:
            from notion_upload import upload_weekly_report
            page_id = upload_weekly_report(summary)
            print(f"   Notion：{page_id}")
        except Exception as e:
            print(f"   Notion 上傳失敗：{e}")

    return summary


def build_weekly_payload(summary):
    return {
        'meta': {'date': summary.get('week_ending', ''), 'days': summary.get('days_covered', 0)},
        'market': summary.get('market'),
        'changes': [
            {
                'sector': c['sector'],
                'change': c['change'],
                'level': c.get('level'),
                'prev_change': c.get('prev_change'),
            }
            for c in summary.get('sector_changes', [])
        ],
        'buys': [
            {
                'stock': b['stock'],
                'days': b['buy_days'],
                'tier': b.get('chip_tier'),
                'gate': b.get('gate', False),
            }
            for b in summary.get('top_buys', [])
        ],
        'positions': summary.get('positions_week'),
        'alerts': summary.get('alerts_week', []),
        'narrative': summary.get('narrative', ''),
    }


if __name__ == '__main__':
    run_weekly_summary()
