"""
Notion 上傳模組 - 純文字段落頁面（非資料庫表格）
"""
import os
from pathlib import Path


PARENT_PAGE_ID = '346b36de-a4bc-8188-ab97-f684f47124fc'


def _load_env():
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def get_notion_client():
    _load_env()
    token = os.environ.get('NOTION_TOKEN')
    if not token:
        raise ValueError("NOTION_TOKEN 未設定")
    from notion_client import Client
    return Client(auth=token)


# ── 輔助：建立 block ──────────────────────────────────────────────────────────

def _h1(text): return {"object":"block","type":"heading_1","heading_1":{"rich_text":[{"type":"text","text":{"content":text}}]}}
def _h2(text): return {"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":text}}]}}
def _h3(text): return {"object":"block","type":"heading_3","heading_3":{"rich_text":[{"type":"text","text":{"content":text}}]}}
def _p(text):  return {"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":text[:2000]}}]}}
def _li(text): return {"object":"block","type":"bulleted_list_item","bulleted_list_item":{"rich_text":[{"type":"text","text":{"content":text[:2000]}}]}}
def _div():    return {"object":"block","type":"divider","divider":{}}


def _chip_str(chip: dict) -> str:
    if not chip:
        return '籌碼：無資料'
    parts = []
    for key in ('外資', '投信', '自營', '合計'):
        v = chip.get(key)
        if v is not None:
            sign = '+' if v >= 0 else ''
            parts.append(f"{key} {sign}{v:,}")
    date = chip.get('date', '')
    return f"籌碼({date})：{'  '.join(parts)}"


# ── 每日掃描 ──────────────────────────────────────────────────────────────────

def upload_daily_report(summary: dict) -> str:
    notion = get_notion_client()
    date_str = summary['date']
    title = f"📊 每日掃描 {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    strong = summary.get('strong_sectors', [])
    weak   = summary.get('weak_sectors', [])
    mkt    = summary.get('market', {})
    qualified = summary.get('qualified', [])
    alerts    = summary.get('alerts', [])

    blocks = []

    # ── 大盤 ──
    blocks.append(_h2('大盤概況'))
    if mkt.get('加權指數'):
        sign = '+' if mkt.get('漲跌幅', 0) >= 0 else ''
        blocks.append(_p(f"加權指數：{mkt['加權指數']:.0f}　漲跌幅：{sign}{mkt['漲跌幅']:.2f}%"))
    else:
        blocks.append(_p('加權指數：資料未取得'))

    # ── 族群強弱 ──
    blocks.append(_div())
    blocks.append(_h2('族群強弱'))
    blocks.append(_li(f"強勢：{'、'.join(strong) if strong else '無'}"))
    blocks.append(_li(f"弱勢：{'、'.join(weak) if weak else '無'}"))

    # ── 籌碼面摘要（三大法人合計買賣超排行） ──
    blocks.append(_div())
    blocks.append(_h2('籌碼面：三大法人'))
    chip_rows = []
    for sector, data in summary.get('sectors', {}).items():
        for st in data.get('stocks', []):
            chip = st.get('chip', {})
            total = chip.get('合計', 0)
            if total != 0:
                chip_rows.append((st['id'], st['name'], sector, chip))
    chip_rows.sort(key=lambda x: x[3].get('合計', 0), reverse=True)

    if chip_rows:
        blocks.append(_p('▲ 買超前段'))
        for sid, name, sector, chip in chip_rows[:5]:
            if chip.get('合計', 0) > 0:
                blocks.append(_li(f"{sid} {name}（{sector}）  {_chip_str(chip)}"))
        blocks.append(_p('▼ 賣超前段'))
        for sid, name, sector, chip in reversed(chip_rows[-5:]):
            if chip.get('合計', 0) < 0:
                blocks.append(_li(f"{sid} {name}（{sector}）  {_chip_str(chip)}"))
    else:
        blocks.append(_p('三大法人資料未取得'))

    # ── 推薦買進 ──
    blocks.append(_div())
    blocks.append(_h2(f'雙條件達標推薦（{len(qualified)} 檔）'))
    if qualified:
        for q in qualified:
            blocks.append(_li(
                f"{q['id']} {q['name']}（{q['sector']}）  "
                f"現價 {q['price']}  RSI {q['rsi']}  CV夏普 {q['cv_sharpe']}"
            ))
    else:
        blocks.append(_p('今日無符合雙條件個股'))

    # ── 風險警示 ──
    blocks.append(_div())
    blocks.append(_h2('風險警示'))
    if alerts:
        for a in alerts[:15]:
            blocks.append(_li(f"{a['id']} {a['name']}（{a['sector']}）— {a['type']} {a['detail']}"))
    else:
        blocks.append(_p('無'))

    # ── 各族群明細 ──
    blocks.append(_div())
    blocks.append(_h2('各族群明細'))

    for sector, data in summary.get('sectors', {}).items():
        blocks.append(_h3(
            f"{sector}　20日均報酬 {data['avg_ret_20d']:+.1f}%　"
            f"RSI {data['avg_rsi']:.1f}　BUY {data['buy_count']} 檔"
        ))
        for st in data.get('stocks', []):
            ret = f"{st['ret_20d']:+.1f}%" if st.get('ret_20d') is not None else 'N/A'
            line = (
                f"{st['id']} {st['name']}　現價 {st['price']}　"
                f"RSI {st['rsi']}　20d {ret}　{st['signal']}　CV夏普 {st['cv_sharpe']}"
            )
            chip = st.get('chip', {})
            if chip:
                line += f"\n  {_chip_str(chip)}"
            news = st.get('news', [])
            if news:
                line += '\n  ' + ' ／ '.join(f"[{n['source']}] {n['title']}" for n in news[:2])
            blocks.append(_li(line))

    # Notion 單次上限 100 blocks，分批上傳
    page = notion.pages.create(
        parent={"page_id": PARENT_PAGE_ID},
        properties={"title": {"title": [{"text": {"content": title}}]}},
        children=blocks[:100],
    )
    page_id = page['id']

    # 超過 100 blocks 的部分追加
    for i in range(100, len(blocks), 100):
        notion.blocks.children.append(page_id, children=blocks[i:i+100])

    return page_id


# ── 週報 ──────────────────────────────────────────────────────────────────────

def upload_weekly_report(summary: dict) -> str:
    notion = get_notion_client()
    week_ending = summary.get('week_ending', '')
    title = f"📅 週報 {week_ending}"
    days = summary.get('days_covered', 0)

    changes  = summary.get('sector_changes', [])
    top_buys = summary.get('top_buys', [])

    blocks = []

    blocks.append(_p(f"涵蓋 {days} 個交易日"))

    blocks.append(_div())
    blocks.append(_h2('族群一週動能變化'))
    for c in changes:
        arrow = '▲' if c['change'] > 0 else '▼' if c['change'] < 0 else '─'
        blocks.append(_li(f"{arrow} {c['sector']}：{c['change']:+.2f} pp"))

    blocks.append(_div())
    blocks.append(_h2('本週累計 BUY 訊號 Top 10'))
    for i, b in enumerate(top_buys, 1):
        blocks.append(_li(f"{i}. {b['stock']} — {b['buy_days']} 次"))

    page = notion.pages.create(
        parent={"page_id": PARENT_PAGE_ID},
        properties={"title": {"title": [{"text": {"content": title}}]}},
        children=blocks,
    )
    return page['id']
