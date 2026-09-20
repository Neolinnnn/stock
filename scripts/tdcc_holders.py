"""集保股權分散表：抓取、存檔、組出週頻時間序列。

改用集保（TDCC）open data 的原因
--------------------------------
FinMind 的 ``taiwan_stock_holding_shares_per`` 需要贊助等級帳號——免費帳號回
「Your level is free」、註冊帳號回「Your level is register」，兩者都拿不到資料。
而這份資料本來就是集保公開發布的，FinMind 只是轉賣，因此直接打集保的端點。

集保端點的特性與因應
--------------------
- 一次回**全市場**（約 4,000 檔）的 CSV，約 2.4 MB，免認證
- 但**只給最新一期**，沒有歷史

所以每抓到一個新的資料日期就存一份快照進 ``data/holders/``，卡 18 的時間序列
由累積的快照組出來。歷史深度會隨時間增加；剛接上時只有一期。

持股分級對照
------------
集保的 CSV 只給分級編號不給級距文字。1~15 級依序為 1-999、1,000-5,000、
5,001-10,000、10,001-15,000、15,001-20,000、20,001-30,000、30,001-40,000、
40,001-50,000、50,001-100,000、100,001-200,000、200,001-400,000、
400,001-600,000、600,001-800,000、800,001-1,000,000、1,000,001 以上（股），
16 為差異數調整、17 為合計。本模組只取 13/14/15，即 CLAUDE.md 定義的
600~800 張、800~1000 張、1000 張以上，分層互斥、不累積。
"""

from __future__ import annotations

import csv
import io
import json
import urllib.request
from pathlib import Path

# 集保 open data 的股權分散表。注意副檔名是 .ashx 不是 .aspx（.aspx 會回 404）
TDCC_URL = 'https://opendata.tdcc.com.tw/getOD.ashx?id=1-5'

# 分級編號 → 專案內的 key。與 build_docs 產出的 JSON 欄位、前端 HOLDER_LEVELS 對應
LEVELS = {
    '13': 'lv600_800',
    '14': 'lv800_1000',
    '15': 'lv1000_up',
}

SNAPSHOT_DIR = Path('data/holders')


def fetch(timeout: int = 120) -> tuple[str, dict]:
    """抓取最新一期的股權分散表。

    Returns:
        (資料日期 YYYYMMDD, {證券代號: {lv600_800: 比例, ...}})

    Raises:
        任何網路或解析錯誤；呼叫端自行決定是否中止。
    """
    with urllib.request.urlopen(TDCC_URL, timeout=timeout) as resp:
        raw = resp.read().decode('utf-8-sig', errors='replace')

    date = ''
    levels: dict = {}
    for row in csv.DictReader(io.StringIO(raw)):
        key = LEVELS.get((row.get('持股分級') or '').strip())
        if not key:
            continue
        date = (row.get('資料日期') or '').strip() or date
        sid = (row.get('證券代號') or '').strip()
        try:
            pct = round(float(row.get('占集保庫存數比例%')), 3)
        except (TypeError, ValueError):
            continue
        levels.setdefault(sid, {})[key] = pct

    if not date or not levels:
        raise ValueError(f'集保回應無法解析（{len(raw)} bytes）')
    return date, levels


def save_snapshot(date: str, levels: dict, keep_ids=None) -> tuple[Path, int]:
    """存一期快照。

    只留 keep_ids 內的個股：全市場約 4,000 檔存成 JSON 是 243 KB，一年 52 期
    會讓 repo 多出 12 MB；掃描清單的約 100 檔只有 6 KB。日後清單擴充時，
    新個股的歷史從加入當期起算。

    Returns:
        (快照路徑, 實際寫入的檔數)
    """
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    if keep_ids is not None:
        keep = set(keep_ids)
        levels = {k: v for k, v in levels.items() if k in keep}
    path = SNAPSHOT_DIR / f'{date}.json'
    path.write_text(
        json.dumps({'date': date, 'levels': levels},
                   ensure_ascii=False, separators=(',', ':')),
        encoding='utf-8',
    )
    return path, len(levels)


def update(keep_ids=None, timeout: int = 120) -> str:
    """抓取並存檔；該期已存在就不重抓寫入。

    掃描是每個交易日跑、集保是每週更新，所以大多數日子抓到的都是已存在的那期。

    Returns:
        本次的資料日期。
    """
    date, levels = fetch(timeout=timeout)
    path = SNAPSHOT_DIR / f'{date}.json'
    if path.exists():
        print(f'  [holders] {date} 快照已存在，略過寫入')
        return date
    _, stored = save_snapshot(date, levels, keep_ids)
    print(f'  [holders] 已存 {date} 快照（全市場 {len(levels)} 檔，'
          f'寫入 {stored} 檔）')
    return date


def _snapshots(weeks: int) -> list:
    """取最近 weeks 期的快照，由舊到新。"""
    if not SNAPSHOT_DIR.is_dir():
        return []
    files = sorted(p for p in SNAPSHOT_DIR.glob('*.json') if p.stem.isdigit())
    return files[-weeks:]


def load_series(sid: str, weeks: int = 12) -> dict:
    """組出單一個股的週頻時間序列，格式與前端 cardHolders 的預期一致。

    Returns:
        {'dates': [...], 'levels': {'lv600_800': [...], ...}}；
        無任何快照或該個股從未出現過時回傳 {}。
    """
    dates, series = [], {k: [] for k in LEVELS.values()}
    for path in _snapshots(weeks):
        try:
            snap = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError):
            continue
        row = (snap.get('levels') or {}).get(sid)
        if row is None:
            continue          # 該期沒有這檔（新上市或尚未納入清單），整期跳過
        dates.append(_fmt_date(snap.get('date') or path.stem))
        for key in series:
            series[key].append(row.get(key))

    if not dates:
        return {}
    return {'dates': dates, 'levels': series}


def _fmt_date(raw: str) -> str:
    """YYYYMMDD → YYYY-MM-DD；前端直接顯示這個字串。"""
    s = str(raw)
    return f'{s[:4]}-{s[4:6]}-{s[6:8]}' if len(s) == 8 and s.isdigit() else s
