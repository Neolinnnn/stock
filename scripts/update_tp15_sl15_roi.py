"""
為 Notion 回測頁面新增「含未實現倉位投報率（TP 15% / SL 15%）」區塊。
用法：python scripts/update_tp15_sl15_roi.py
"""
import sys, os, json
from pathlib import Path

PAGE_ID = '35db36dea4bc814292b6f94aad04eaac'  # Notion 回測頁面

# ── 工具 ──────────────────────────────────────────────────────────────────────

def _load_env():
    env_path = Path(__file__).parent.parent / '.env'
    if env_path.exists():
        for line in env_path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())

def _h2(text):
    return {"object":"block","type":"heading_2",
            "heading_2":{"rich_text":[{"type":"text","text":{"content":text}}]}}

def _h3(text):
    return {"object":"block","type":"heading_3",
            "heading_3":{"rich_text":[{"type":"text","text":{"content":text}}]}}

def _p(text):
    return {"object":"block","type":"paragraph",
            "paragraph":{"rich_text":[{"type":"text","text":{"content":text[:2000]}}]}}

def _li(text):
    return {"object":"block","type":"bulleted_list_item",
            "bulleted_list_item":{"rich_text":[{"type":"text","text":{"content":text[:2000]}}]}}

def _div():
    return {"object":"block","type":"divider","divider":{}}

def _table_row(cells):
    return {"object":"block","type":"table_row",
            "table_row":{"cells":[[{"type":"text","text":{"content":c}}] for c in cells]}}

def _table(rows, has_col_header=True, has_row_header=True):
    width = max(len(r) for r in rows)
    return {"object":"block","type":"table",
            "table":{"table_width":width,"has_column_header":has_col_header,
                     "has_row_header":has_row_header,
                     "children":[_table_row(r) for r in rows]}}


# ── 計算 ──────────────────────────────────────────────────────────────────────

# 最新收盤價（來自 Notion 頁面 2026/05/11 資料）
LATEST_PRICES = {
    '2382': 343.5,   # 廣達
    '6239': 225.5,   # 力成
    '2441': 92.4,    # 超豐
    '2357': 686.0,   # 華碩
    '2451': 343.5,   # 創見
    '2337': 159.5,   # 旺宏
}
PRICE_DATE = '2026/05/11'


def calc_inclusive_roi(backtest_path='backtest_results.json'):
    """
    計算 TP15/SL15 含未實現倉位的綜合投報率。
    回傳 dict with closed_stats, open_stats, combined_stats, open_trades.
    """
    results = json.loads(Path(backtest_path).read_text(encoding='utf-8'))
    trades = results['combinations']['TP15_SL15']['trades']

    closed = [t for t in trades if t['result'] != 'OPEN']
    opens  = [t for t in trades if t['result'] == 'OPEN']

    # 計算未實現倉位的當前損益
    open_details = []
    for t in sorted(opens, key=lambda x: x.get('signal_date', '')):
        sid = t['stock_id']
        ep  = t['entry_price']
        cur = LATEST_PRICES.get(sid)
        if cur and ep:
            roi = round((cur - ep) / ep * 100, 2)
        else:
            roi = None
        open_details.append({**t, 'current_price': cur, 'unrealized_pct': roi})

    open_rois = [d['unrealized_pct'] for d in open_details if d['unrealized_pct'] is not None]

    avg_closed  = sum(t['return_pct'] for t in closed) / len(closed) if closed else 0
    avg_open    = sum(open_rois) / len(open_rois) if open_rois else 0

    total_sum   = sum(t['return_pct'] for t in closed) + sum(open_rois)
    total_count = len(closed) + len(open_rois)
    avg_combined = total_sum / total_count if total_count else 0

    # 含未實現的勝率（未實現正報酬算贏）
    open_wins = sum(1 for r in open_rois if r > 0)
    total_wins = len([t for t in closed if t['result'] == 'WIN']) + open_wins
    win_rate_incl = total_wins / total_count if total_count else 0

    return {
        'closed_count': len(closed),
        'open_count': len(open_rois),
        'total_count': total_count,
        'avg_closed_roi': round(avg_closed, 2),
        'avg_open_roi': round(avg_open, 2),
        'avg_combined_roi': round(avg_combined, 2),
        'win_rate_closed': round(len([t for t in closed if t['result'] == 'WIN']) / len(closed), 4) if closed else 0,
        'win_rate_incl': round(win_rate_incl, 4),
        'open_details': open_details,
    }


# ── 建立 Notion blocks ────────────────────────────────────────────────────────

def build_blocks(stats: dict) -> list:
    blocks = []
    blocks.append(_div())
    blocks.append(_h2("📊 含未實現倉位投報率（TP 15% / SL 15%）"))
    blocks.append(_p(f"以 {PRICE_DATE} 收盤價計算未實現損益，所有 {stats['total_count']} 筆訊號納入統計。"))

    # 摘要表格
    summary_rows = [
        ["項目", "已出場", "未實現倉位", "全部合計（含未實現）"],
        ["筆數",
         str(stats['closed_count']),
         str(stats['open_count']),
         str(stats['total_count'])],
        ["平均投報率",
         f"{stats['avg_closed_roi']:+.2f}%",
         f"{stats['avg_open_roi']:+.2f}%",
         f"{stats['avg_combined_roi']:+.2f}%"],
        ["勝率（正報酬）",
         f"{stats['win_rate_closed']:.1%}",
         "—",
         f"{stats['win_rate_incl']:.1%}"],
    ]
    blocks.append(_table(summary_rows, has_row_header=True))

    # 未實現倉位明細
    blocks.append(_h3(f"未實現倉位明細（{PRICE_DATE} 收盤，{stats['open_count']} 筆）"))
    detail_rows = [["訊號日", "進場日", "代號", "名稱", "進場價", "現價", "損益%", "狀態"]]
    for d in stats['open_details']:
        roi = d.get('unrealized_pct')
        roi_str = f"{roi:+.1f}%" if roi is not None else "N/A"
        sign_flag = "✅" if (roi is not None and roi > 0) else ("❌" if (roi is not None and roi < 0) else "─")
        detail_rows.append([
            d.get('signal_date', ''),
            d.get('entry_date', ''),
            d.get('stock_id', ''),
            d.get('stock_name', ''),
            str(d.get('entry_price', '')),
            str(d.get('current_price', 'N/A')),
            f"{sign_flag} {roi_str}",
            d.get('exit_state', 'HOLDING'),
        ])
    blocks.append(_table(detail_rows))

    return blocks


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    _load_env()
    token = os.environ.get('NOTION_TOKEN')
    if not token:
        print("❌ NOTION_TOKEN 未設定")
        sys.exit(1)

    from notion_client import Client
    notion = Client(auth=token)

    print("【計算】含未實現倉位投報率（TP 15% / SL 15%）...")
    stats = calc_inclusive_roi()

    print(f"  已出場 {stats['closed_count']} 筆：平均 {stats['avg_closed_roi']:+.2f}%")
    print(f"  未實現 {stats['open_count']} 筆：平均 {stats['avg_open_roi']:+.2f}%")
    print(f"  全部合計 {stats['total_count']} 筆：平均 {stats['avg_combined_roi']:+.2f}%")
    print(f"  含未實現勝率：{stats['win_rate_incl']:.1%}")

    blocks = build_blocks(stats)
    print(f"\n【上傳】新增 {len(blocks)} 個 blocks 到 Notion 頁面 {PAGE_ID}...")
    notion.blocks.children.append(PAGE_ID, children=blocks)
    print("  ✅ 完成！")
    print(f"  🔗 https://www.notion.so/{PAGE_ID.replace('-', '')}")


if __name__ == '__main__':
    main()
