# -*- coding: utf-8 -*-
"""
大戶籌碼掃描（集保股權分散表）
==============================
仿 aistockmap 的「大戶持股比例」功能：追蹤掃描池個股的
千張大戶（>1000張）、中實戶（>400張）、散戶（<100張）持股比例週變化。

資料來源（免費、免 token）：
  TDCC 集保戶股權分散表 open data（每週六更新上週五資料）
  https://opendata.tdcc.com.tw/getOD.ashx?id=1-5
  一次只提供最新一週 → 每週執行累積歷史到 docs/whales.json。

歷史回補（選用）：FinMind taiwan_stock_holding_shares_per
  免費帳號層級不開放此 dataset，回補失敗會自動跳過，不影響週更。

持股分級（TDCC level 代碼，單位=股）：
  1:1-999 … 9:50,001-100,000 … 12:400,001-600,000 …
  15:1,000,001以上   16:差異數調整   17:合計
  千張大戶 = level 15；>400張 = level 12-15；散戶(<100張) = level 1-9

輸出：docs/whales.json（歷史 + 排行榜，前端 docs/whales.html 使用）
用法：python scripts/whale_scan.py [--backfill]
"""
import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / 'docs' / 'whales.json'
TDCC_URL = 'https://opendata.tdcc.com.tw/getOD.ashx?id=1-5'
BACKFILL_START = '2024-07-01'  # 回補起點（約兩年）
MAX_WEEKS = 160                # 歷史保留上限（約三年）


def load_universe() -> dict:
    """掃描池：docs/stocks_index.json ∪ docs/stocks/*.json → {id: (name, sector)}"""
    uni = {}
    idx = json.loads((ROOT / 'docs' / 'stocks_index.json').read_text(encoding='utf-8'))
    for s in idx:
        uni[s['id']] = (s['name'], s.get('sector', ''))
    for p in (ROOT / 'docs' / 'stocks').glob('*.json'):
        if p.stem not in uni:
            try:
                d = json.loads(p.read_text(encoding='utf-8'))
                uni[p.stem] = (d.get('name', p.stem), d.get('industry', ''))
            except Exception:
                pass
    return uni


def fetch_tdcc(ids: set) -> tuple[str, dict]:
    """下載 TDCC 最新一週股權分散表 → (週日期 yyyymmdd, {sid: metrics})"""
    print('[TDCC] 下載集保股權分散表…')
    r = requests.get(TDCC_URL, timeout=120,
                     headers={'User-Agent': 'Mozilla/5.0'})
    r.raise_for_status()
    text = r.content.decode('utf-8-sig')
    week = ''
    levels = {}  # sid -> {level(int): pct, 'holders': int}
    for row in csv.reader(io.StringIO(text)):
        if len(row) < 6 or row[0] == '資料日期':
            continue
        sid = row[1].strip()
        if sid not in ids:
            continue
        week = row[0].strip()
        try:
            lv = int(row[2])
            people = int(row[3])
            pct = float(row[5])
        except ValueError:
            continue
        d = levels.setdefault(sid, {})
        d[lv] = pct
        if lv == 17:
            d['holders'] = people
    result = {sid: _metrics_from_levels(d) for sid, d in levels.items()}
    print(f'[TDCC] 週別 {week}，取得 {len(result)} 檔')
    return week, result


def _metrics_from_levels(d: dict) -> dict:
    return {
        'big1000': round(d.get(15, 0.0), 2),
        'big400': round(sum(d.get(lv, 0.0) for lv in (12, 13, 14, 15)), 2),
        'retail': round(sum(d.get(lv, 0.0) for lv in range(1, 10)), 2),
        'holders': d.get('holders', 0),
    }


# ── FinMind 歷史回補（免費層級不支援時自動跳過） ──────────────────────────────

def _bucket_lower_bound(level: str) -> int | None:
    """FinMind HoldingSharesLevel 字串 → 分級下限股數；total/調整回傳 None"""
    s = level.replace(',', '').replace(' ', '')
    low = s.lower()
    if 'total' in low or '合計' in s:
        return None
    if 'more' in low or '以上' in s:
        return 1000001
    try:
        return int(s.split('-')[0])
    except ValueError:
        return None


def backfill_finmind(history: dict, uni: dict):
    """用 FinMind 股權分散表回補歷史；dataset 無權限時整批跳過。"""
    from datafeed import finmind_fetch
    need = [sid for sid in uni if len(history.get(sid, {}).get('weeks', [])) < 8]
    if not need:
        print('[回補] 歷史已足夠，跳過')
        return
    print(f'[回補] 嘗試 FinMind 回補 {len(need)} 檔…')
    for i, sid in enumerate(need):
        try:
            df = finmind_fetch('taiwan_stock_holding_shares_per',
                               stock_id=sid, start_date=BACKFILL_START)
        except Exception as e:
            if 'level' in str(e).lower() or '400' in str(e):
                print(f'[回補] FinMind 帳號層級不支援此 dataset，跳過回補（{e}）')
                return
            print(f'  [{sid}] 回補失敗：{e}')
            continue
        if df is None or df.empty:
            continue
        weekly = {}  # date(yyyymmdd) -> {level_low: percent, holders}
        for _, row in df.iterrows():
            lb = _bucket_lower_bound(str(row['HoldingSharesLevel']))
            date = str(row['date']).replace('-', '')[:8]
            w = weekly.setdefault(date, {})
            if lb is None:
                if 'total' in str(row['HoldingSharesLevel']).lower():
                    w['holders'] = int(row.get('people', 0) or 0)
                continue
            w[lb] = w.get(lb, 0.0) + float(row['percent'])
        rec = history.setdefault(sid, {'weeks': [], 'big1000': [], 'big400': [],
                                       'retail': [], 'holders': []})
        for date in sorted(weekly):
            if date in rec['weeks']:
                continue
            w = weekly[date]
            _insert_week(rec, date, {
                'big1000': round(w.get(1000001, 0.0), 2),
                'big400': round(sum(v for lb, v in w.items()
                                    if isinstance(lb, int) and lb >= 400001), 2),
                'retail': round(sum(v for lb, v in w.items()
                                    if isinstance(lb, int) and lb < 100001), 2),
                'holders': w.get('holders', 0),
            })
        if (i + 1) % 20 == 0:
            print(f'  [回補] 進度 {i + 1}/{len(need)}')
    print('[回補] 完成')


def _insert_week(rec: dict, week: str, m: dict):
    """依週日期排序插入一筆（已存在則覆蓋）。"""
    if week in rec['weeks']:
        i = rec['weeks'].index(week)
    else:
        i = len([w for w in rec['weeks'] if w < week])
        rec['weeks'].insert(i, week)
        for k in ('big1000', 'big400', 'retail', 'holders'):
            rec[k].insert(i, None)
    for k in ('big1000', 'big400', 'retail', 'holders'):
        rec[k][i] = m[k]


def build_rankings(history: dict, uni: dict) -> dict:
    """千張大戶週增減排行（需 ≥2 週）＋最高持股排行。"""
    rows = []
    for sid, rec in history.items():
        if sid not in uni or not rec['weeks']:
            continue
        name, sector = uni[sid]
        b = rec['big1000']
        row = {'id': sid, 'name': name, 'sector': sector,
               'big1000': b[-1], 'big400': rec['big400'][-1],
               'retail': rec['retail'][-1], 'holders': rec['holders'][-1],
               'chg_w1': None, 'chg_w4': None}
        if len(b) >= 2 and b[-2] is not None:
            row['chg_w1'] = round(b[-1] - b[-2], 2)
        if len(b) >= 5 and b[-5] is not None:
            row['chg_w4'] = round(b[-1] - b[-5], 2)
        rows.append(row)
    has_chg = [r for r in rows if r['chg_w1'] is not None]
    return {
        'up_w1': sorted(has_chg, key=lambda r: -r['chg_w1'])[:10],
        'down_w1': sorted(has_chg, key=lambda r: r['chg_w1'])[:10],
        'top_big1000': sorted(rows, key=lambda r: -(r['big1000'] or 0))[:10],
    }


def main():
    uni = load_universe()
    history = {}
    if OUT_PATH.exists():
        history = json.loads(OUT_PATH.read_text(encoding='utf-8')).get('stocks', {})

    week, metrics = fetch_tdcc(set(uni))
    for sid, m in metrics.items():
        rec = history.setdefault(sid, {'weeks': [], 'big1000': [], 'big400': [],
                                       'retail': [], 'holders': []})
        _insert_week(rec, week, m)

    for sid, rec in history.items():
        if sid in uni:
            rec['name'], rec['sector'] = uni[sid]

    if '--backfill' in sys.argv:
        try:
            backfill_finmind(history, uni)
        except Exception as e:
            print(f'[回補] 例外，跳過：{e}')

    # 修剪過長歷史
    for rec in history.values():
        if len(rec['weeks']) > MAX_WEEKS:
            cut = len(rec['weeks']) - MAX_WEEKS
            for k in ('weeks', 'big1000', 'big400', 'retail', 'holders'):
                rec[k] = rec[k][cut:]

    latest = max((rec['weeks'][-1] for rec in history.values() if rec['weeks']),
                 default='')
    out = {
        'updated': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'latest_week': latest,
        'source': 'TDCC 集保戶股權分散表（每週六更新）',
        'stocks': history,
        'rankings': build_rankings(history, uni),
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False), encoding='utf-8')
    n_weeks = max((len(r['weeks']) for r in history.values()), default=0)
    print(f'[輸出] {OUT_PATH}（{len(history)} 檔，最多 {n_weeks} 週，最新週 {latest}）')


if __name__ == '__main__':
    main()
