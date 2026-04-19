"""
Google Sheets 上傳模組
將每日掃描結果寫入 Google Sheets，供 GAS Web App 讀取
"""
import os
import json
from pathlib import Path
from datetime import datetime


def _get_creds():
    """取得 Google 認證（Service Account JSON）"""
    import google.auth
    from google.oauth2.service_account import Credentials

    scopes = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive',
    ]
    key_path = Path(__file__).parent.parent / 'google_service_account.json'
    if key_path.exists():
        return Credentials.from_service_account_file(str(key_path), scopes=scopes)

    # 從環境變數讀（GitHub Actions）
    key_json = os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')
    if key_json:
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write(key_json)
            tmp = f.name
        creds = Credentials.from_service_account_file(tmp, scopes=scopes)
        os.unlink(tmp)
        return creds

    raise ValueError("找不到 Google Service Account 認證")


def get_client():
    import gspread
    return gspread.authorize(_get_creds())


def _sheet_id():
    env = Path(__file__).parent.parent / '.env'
    if env.exists():
        for line in env.read_text(encoding='utf-8').splitlines():
            if line.startswith('GOOGLE_SHEET_ID='):
                return line.split('=', 1)[1].strip()
    return os.environ.get('GOOGLE_SHEET_ID', '')


# ── 每日掃描上傳 ──────────────────────────────────────────────────────────────

def upload_daily_report(summary: dict):
    """將每日掃描結果寫入 Sheets 的「每日掃描」分頁"""
    gc = get_client()
    sh = gc.open_by_key(_sheet_id())

    try:
        ws = sh.worksheet('每日掃描')
    except Exception:
        ws = sh.add_worksheet('每日掃描', rows=2000, cols=20)

    date_str = summary['date']
    date_fmt = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    # 清除並重寫標題（首次執行時）
    existing = ws.get_all_values()
    if not existing:
        ws.append_row(['日期', '族群', '代碼', '名稱', '現價', 'RSI',
                       '20日%', '訊號', 'CV夏普', '外資', '投信', '自營', '法人合計', '新聞'])

    rows = []
    for sector, data in summary.get('sectors', {}).items():
        for st in data.get('stocks', []):
            chip = st.get('chip', {})
            news = ' / '.join(n['title'] for n in st.get('news', [])[:2])
            rows.append([
                date_fmt,
                sector,
                st['id'],
                st['name'],
                st.get('price', ''),
                st.get('rsi', ''),
                st.get('ret_20d', ''),
                st.get('signal', ''),
                st.get('cv_sharpe', ''),
                chip.get('外資', ''),
                chip.get('投信', ''),
                chip.get('自營', ''),
                chip.get('合計', ''),
                news,
            ])

    if rows:
        ws.append_rows(rows)

    # 更新「最新摘要」分頁
    _update_summary_sheet(sh, summary, date_fmt)
    print(f"  Google Sheets 每日掃描已寫入（{len(rows)} 筆）")


def _update_summary_sheet(sh, summary, date_fmt):
    """更新「最新摘要」分頁（GAS 主要讀取這裡）"""
    try:
        ws = sh.worksheet('最新摘要')
        ws.clear()
    except Exception:
        ws = sh.add_worksheet('最新摘要', rows=200, cols=10)

    mkt = summary.get('market', {})
    strong = ', '.join(summary.get('strong_sectors', []))
    weak   = ', '.join(summary.get('weak_sectors', []))

    meta = [
        ['掃描日期', date_fmt],
        ['加權指數', mkt.get('加權指數', '')],
        ['漲跌幅%', mkt.get('漲跌幅', '')],
        ['強勢族群', strong],
        ['弱勢族群', weak],
        ['推薦買進', len(summary.get('qualified', []))],
        ['風險警示', len(summary.get('alerts', []))],
    ]
    ws.update('A1', meta)

    # 族群摘要
    ws.update('A10', [['族群', '20日%', 'RSI', 'BUY數', 'RSI>70數']])
    sector_rows = []
    for sector, data in summary.get('sectors', {}).items():
        sector_rows.append([
            sector,
            data['avg_ret_20d'],
            data['avg_rsi'],
            data['buy_count'],
            data['hot_count'],
        ])
    ws.update('A11', sector_rows)

    # 籌碼排行
    chip_rows = []
    for sector, data in summary.get('sectors', {}).items():
        for st in data.get('stocks', []):
            chip = st.get('chip', {})
            total = chip.get('合計', 0)
            if total != 0:
                chip_rows.append([
                    st['id'], st['name'], sector, total,
                    chip.get('外資', 0), chip.get('投信', 0), chip.get('自營', 0)
                ])
    chip_rows.sort(key=lambda x: x[3], reverse=True)

    start = 11 + len(sector_rows) + 3
    ws.update(f'A{start}', [['代碼', '名稱', '族群', '法人合計', '外資', '投信', '自營']])
    if chip_rows:
        ws.update(f'A{start+1}', chip_rows)


# ── 週報上傳 ──────────────────────────────────────────────────────────────────

def upload_weekly_report(summary: dict):
    """將週報寫入「週報」分頁"""
    gc = get_client()
    sh = gc.open_by_key(_sheet_id())

    try:
        ws = sh.worksheet('週報')
    except Exception:
        ws = sh.add_worksheet('週報', rows=500, cols=10)

    week = summary.get('week_ending', '')
    ws.clear()
    ws.update('A1', [['週報日期', week], ['涵蓋交易日', summary.get('days_covered', 0)]])
    ws.update('A4', [['族群', '動能變化(pp)']])
    changes = [[c['sector'], c['change']] for c in summary.get('sector_changes', [])]
    if changes:
        ws.update('A5', changes)

    start = 5 + len(changes) + 2
    ws.update(f'A{start}', [['個股', 'BUY次數']])
    buys = [[b['stock'], b['buy_days']] for b in summary.get('top_buys', [])]
    if buys:
        ws.update(f'A{start+1}', buys)

    print(f"  Google Sheets 週報已寫入")
