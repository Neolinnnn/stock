"""
週報生成（當週最後一個交易日，由 daily_scan workflow 觸發）
彙整本週各交易日的掃描結果，產生七節式週報：
  一、市場總覽　二、族群輪動矩陣　三、訊號榜　四、本月行動清單與漲幅追蹤
  五、持倉週記　六、風險警示　七、AI 週評（Gemini）

用法：
  python scripts/weekly_summary.py
"""
import sys, os, json, math
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

from cjk_font import setup_cjk_font
setup_cjk_font()

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


def collect_month_actions(today_str, base_dir=Path('daily_reports')):
    """本月（1 日至 today）每日行動清單（qualified）彙整與入榜後漲幅追蹤。

    入榜價＝首次入榜日收盤；最新價＝最近一份日報收盤；最高漲幅取入榜後各日收盤最高點。
    股價取自日報各族群個股的 price，不打 API。回傳依首次入榜日排序的 list[dict]。
    """
    days = sorted(d for d in base_dir.iterdir()
                  if d.is_dir() and d.name.isdigit() and d.name.startswith(today_str[:6])
                  and d.name <= today_str and (d / 'summary.json').exists())
    track = {}
    for d in days:
        rep = json.loads((d / 'summary.json').read_text(encoding='utf-8'))
        for q in rep.get('qualified', []):
            t = track.setdefault(q['id'], {
                'id': q['id'], 'name': q['name'], 'sector': q.get('sector', ''),
                'first_date': d.name, 'entry_price': q.get('price'),
                'days_listed': 0, 'last_price': None, 'max_price': None,
            })
            t['days_listed'] += 1
            t['last_listed'] = d.name
        prices = {st['id']: st.get('price') for sec in rep['sectors'].values() for st in sec['stocks']}
        for t in track.values():
            p = prices.get(t['id'])
            if isinstance(p, (int, float)) and p > 0:
                t['last_price'] = p
                t['max_price'] = max(t['max_price'] or p, p)
    out = []
    for t in track.values():
        e = t['entry_price']
        ok = isinstance(e, (int, float)) and e > 0
        t['ret_pct'] = round((t['last_price'] / e - 1) * 100, 2) if ok and t['last_price'] else None
        t['max_ret_pct'] = round((t['max_price'] / e - 1) * 100, 2) if ok and t['max_price'] else None
        out.append(t)
    return sorted(out, key=lambda t: (t['first_date'], t['id']))


def month_actions_stats(actions):
    """本月行動清單摘要：檔數、上漲檔數、平均漲幅（無報價者不計）。"""
    rets = [a['ret_pct'] for a in actions if a.get('ret_pct') is not None]
    return {
        'count': len(actions),
        'up': sum(r > 0 for r in rets),
        'avg_ret_pct': round(sum(rets) / len(rets), 2) if rets else None,
    }


def build_narrative_context(sector_metrics, top_buys, market=None,
                            rotation=None, positions_week=None, alerts_week=None,
                            month_actions=None):
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
    if month_actions is not None:
        ctx['month_actions'] = month_actions
        ctx['month_actions_stats'] = month_actions_stats(month_actions)
    return ctx


def generate_narrative(writer, context_data, date_str):
    """呼叫 Gemini 生成三段週報敘事；任何失敗回傳空字串（前端會隱藏敘事卡）。"""
    extra = ('請輸出四段，第一段標題「本週輪動回顧」描述族群強弱輪動，'
             '第二段標題「本月行動清單追蹤」根據 month_actions 與 month_actions_stats '
             '說明本月入榜個股的整體表現（上漲檔數、平均漲幅），點名漲幅最大與回落最多者，'
             '若 month_actions 為空則說明本月尚無入榜個股，'
             '第三段標題「下週聚焦」點出值得追蹤的族群與個股，'
             '第四段標題「風險提醒」根據 alerts_week 與 rotation_matrix.cooling '
             '提示過熱與轉弱風險，繁體中文、各 120 字內（模板總長 500 字內）。'
             '直接輸出四段內容，不要任何開場白、前言或結語。')
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
    """由 summary dict 產生七節式 weekly.md 內容。"""
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

    # 四、本月行動清單與漲幅追蹤
    acts = summary.get('month_actions') or []
    md.append("\n## 四、本月行動清單與漲幅追蹤\n")
    if acts:
        st = month_actions_stats(acts)
        avg = f"{st['avg_ret_pct']:+.2f}%" if st['avg_ret_pct'] is not None else '—'
        md.append(f"本月共 {st['count']} 檔入榜，{st['up']} 檔上漲，平均漲幅 {avg}"
                  "（入榜價＝首次入榜日收盤）\n\n")
        md.append("| 股票 | 族群 | 首次入榜 | 入榜價 | 最新價 | 漲幅 | 入榜後最高 | 入榜天數 |\n"
                  "|------|------|---------|-------|-------|------|-----------|---------|\n")
        pct = lambda v: f"{v:+.2f}%" if v is not None else '—'   # noqa: E731
        for a in acts:
            fd = a['first_date']
            md.append(f"| {a['id']} {a['name']} | {a['sector']} | {fd[4:6]}/{fd[6:]} | "
                      f"{a['entry_price'] if a['entry_price'] is not None else '—'} | "
                      f"{a['last_price'] if a['last_price'] is not None else '—'} | "
                      f"{pct(a['ret_pct'])} | {pct(a['max_ret_pct'])} | {a['days_listed']} |\n")
    else:
        md.append("- 本月尚無個股入榜\n")

    # 五、持倉週記
    pw = summary.get('positions_week') or {}
    md.append("\n## 五、持倉週記\n")
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

    # 六、風險警示
    alerts = summary.get('alerts_week', [])
    md.append("\n## 六、風險警示（週內累計）\n")
    if alerts:
        for a in alerts:
            md.append(f"- {a['id']} {a['name']}：{a['type']} × {a['days']} 天（{a['detail']}）\n")
    else:
        md.append("- 無\n")

    # 七、AI 週評
    if summary.get('narrative'):
        md.append("\n## 七、AI 週評\n\n")
        md.append(summary['narrative'] + "\n")

    return ''.join(md)


# 與網頁深色主題一致的色票（docs 前端 :root 變數）
_CARD = '#161a22'    # 卡片底色
_TEXT = '#e8eaed'    # 主要文字
_SUB = '#8b93a3'     # 次要文字
_BORDER = '#272c37'  # 格線／邊框
_UP, _DOWN, _NEUTRAL = '#2ecc71', '#e74c3c', '#4a5162'  # 綠漲／紅跌／中性


def draw_sector_small_multiples(sector_trend, dates, out_path):
    """
    小倍數分面圖：每個族群一格獨立走勢，取代原本 18 條線疊在一起的義大利麵圖。
    - 依「本週淨變化」(末值−首值) 由大到小排序，輪動最強者置左上
    - 漲綠跌紅，標題標示族群名與淨變化
    - 共用 y 軸，各族群報酬量級可直接比較
    """
    # 對齊每個族群到日期軸：(x索引, 值) 只取有資料的日子
    series = []
    for sector, vals in sector_trend.items():
        pts = [(i, v) for i, v in enumerate(vals) if v is not None]
        if len(pts) >= 2:
            series.append((sector, pts))
    series.sort(key=lambda kv: kv[1][-1][1] - kv[1][0][1], reverse=True)  # 依淨變化排序
    n = len(series)

    if n == 0:
        fig, ax = plt.subplots(figsize=(8, 3), facecolor=_CARD)
        ax.set_facecolor(_CARD)
        ax.text(0.5, 0.5, '本週資料不足', ha='center', va='center', fontsize=14, color=_SUB)
        ax.axis('off')
        fig.savefig(out_path, dpi=100, bbox_inches='tight', facecolor=_CARD)
        plt.close(fig)
        return

    ncols = 4 if n > 6 else min(n, 3)
    nrows = math.ceil(n / ncols)

    all_vals = [v for _, pts in series for _, v in pts]
    lo, hi = min(all_vals), max(all_vals)
    pad = max((hi - lo) * 0.15, 1.0)
    ylim = (lo - pad, hi + pad)

    last_i = len(dates) - 1
    xlabels = [f'{d[4:6]}/{d[6:8]}' for d in dates]  # MM/DD

    fig, axes = plt.subplots(nrows, ncols, figsize=(ncols * 2.7, nrows * 1.95),
                             sharex=True, sharey=True, facecolor=_CARD)
    axes = list(axes.flatten()) if n > 1 else [axes]

    for i, (sector, pts) in enumerate(series):
        ax = axes[i]
        ax.set_facecolor(_CARD)
        xs = [p[0] for p in pts]
        vals = [p[1] for p in pts]
        delta = vals[-1] - vals[0]
        color = _UP if delta >= 0 else _DOWN
        ax.plot(xs, vals, color=color, linewidth=2, marker='o', markersize=3.5,
                markerfacecolor=color, markeredgecolor=_CARD, markeredgewidth=0.6,
                zorder=3)
        ax.fill_between(xs, vals, 0, color=color, alpha=0.13, zorder=1)
        ax.axhline(0, color=_NEUTRAL, linewidth=0.7, zorder=0)
        ax.set_title(f'{sector}  {delta:+.1f}', fontsize=10, color=color, pad=3)
        # 末值標籤
        ax.annotate(f'{vals[-1]:+.0f}', (xs[-1], vals[-1]), fontsize=8, color=color,
                    ha='left', va='center', xytext=(3, 0), textcoords='offset points')
        ax.set_ylim(ylim)
        ax.set_xlim(-0.3, last_i + 0.9)  # 右側留白給末值標籤
        ax.set_xticks([0, last_i])
        ax.set_xticklabels([xlabels[0], xlabels[-1]])
        ax.tick_params(labelsize=7, length=2, colors=_SUB)
        ax.grid(axis='y', color=_BORDER, alpha=0.6, linewidth=0.6)
        for side, spine in ax.spines.items():
            spine.set_visible(side in ('left', 'bottom'))
            spine.set_color(_BORDER)

    for j in range(n, len(axes)):
        axes[j].axis('off')

    fig.suptitle('本週各族群 20 日平均報酬走勢（依本週變化排序 · 綠漲紅跌 · 單位 %）',
                 fontsize=13, y=1.0, color=_TEXT)
    fig.supylabel('20 日平均報酬 (%)', fontsize=10, color=_SUB)
    fig.tight_layout(rect=[0.01, 0, 1, 0.98])
    fig.savefig(out_path, dpi=100, bbox_inches='tight', facecolor=_CARD)
    plt.close(fig)


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

    # 各族群一週平均報酬變化（對齊到日期軸，缺日補 None——族群可能週中新增/整併）
    sector_trend = {}
    for i, report in enumerate(week_reports):
        for sector, data in report['sectors'].items():
            sector_trend.setdefault(sector, [None] * len(week_reports))[i] = data['avg_ret_20d']

    dates = [r['date'] for r in week_reports]
    chart_path = out_dir / '01_weekly_sector_trend.png'
    draw_sector_small_multiples(sector_trend, dates, chart_path)

    # 各節資料彙整
    prev_changes = load_prev_week_changes(today.strftime('%Y%m%d'))
    sector_metrics = compute_sector_metrics(week_reports, prev_changes)
    market = compute_market_week(week_reports)
    rotation = build_rotation_matrix(sector_metrics)
    top_buys = collect_week_signals(week_reports)
    positions_week = collect_positions_week(week_reports)
    alerts_week = collect_alerts_week(week_reports)
    month_actions = collect_month_actions(today.strftime('%Y%m%d'))

    summary = {
        'week_ending': today.strftime('%Y-%m-%d'),
        'days_covered': len(week_reports),
        'market': market,
        'sector_changes': sector_metrics,
        'rotation_matrix': rotation,
        'top_buys': top_buys,
        'positions_week': positions_week,
        'alerts_week': alerts_week,
        'month_actions': month_actions,
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
            positions_week=positions_week, alerts_week=alerts_week,
            month_actions=month_actions)
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
        'monthActions': summary.get('month_actions', []),
        'narrative': summary.get('narrative', ''),
    }


if __name__ == '__main__':
    run_weekly_summary()
