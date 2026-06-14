# -*- coding: utf-8 -*-
"""
領頭羊突破每日掃描（第二訊號源）
================================
與雙篩選互補的 bottom-up 訊號：抓「自己先動、籌碼同向」的領頭羊。
條件與回測一致（scripts/backtest_breakout_lab.py，回測 n=183、TP18SL15 勝率 65%、
追蹤停損15% 平均 +15%、PF 3.2）：

  0. TAIEX > MA60   1. 收盤創20日新高   2. 量比 ≥ 1.5
  3. 法人5日淨買超 > 0   4. >MA20、MA20上揚、乖離 ≤ 10%

資料來源：price_cache/（每日掃描後最新）；籌碼只查通過技術條件的候選股。
輸出：docs/breakout.json
用法：python scripts/breakout_scan.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

from datafeed import finmind_fetch as _finmind_fetch  # 統一取數層（token 輪替）

PRICE_CACHE = ROOT / 'price_cache'
VOL_RATIO_MIN = 1.5
DIST_MA20_MAX = 0.10
CHIP_DAYS = 5


def _load_cache(sid: str) -> dict | None:
    p = PRICE_CACHE / f'{sid}.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _taiex_bull() -> bool:
    start = (datetime.now() - timedelta(days=120)).strftime('%Y-%m-%d')
    df = _finmind_fetch('taiwan_stock_daily', stock_id='TAIEX', start_date=start)
    if df is None or len(df) < 60:
        return True  # 抓不到時不阻擋（保守放行，由其他條件把關）
    closes = list(df['close'])
    return closes[-1] > sum(closes[-60:]) / 60


def _chip5(sid: str) -> float:
    start = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')
    try:
        df = _finmind_fetch('taiwan_stock_institutional_investors',
                            stock_id=sid, start_date=start)
        if df is None or df.empty:
            return 0.0
        df = df[df['name'].isin(['Foreign_Investor', 'Investment_Trust'])]
        df = df.assign(net=df['buy'] - df['sell'])
        daily = df.groupby('date')['net'].sum()
        return float(daily.tail(CHIP_DAYS).sum())
    except Exception:
        return 0.0


def _tech_pass(d: dict) -> dict | None:
    """條件 1/2/4（純 price_cache，不打 API）。回傳特徵或 None。"""
    closes = d.get('prices', [])
    vols = d.get('volumes', [])
    if len(closes) < 60 or len(vols) < 21 or not vols[-1]:
        return None
    c = closes[-1]
    # 1. 突破 20 日收盤新高
    if c < max(closes[-21:-1]):
        return None
    # 2. 量比
    vma20 = sum(vols[-20:]) / 20
    if not vma20 or vols[-1] / vma20 < VOL_RATIO_MIN:
        return None
    # 4. 結構
    ma20 = sum(closes[-20:]) / 20
    ma20_prev = sum(closes[-25:-5]) / 20
    if c <= ma20 or ma20 <= ma20_prev:
        return None
    dist = (c - ma20) / ma20
    if dist > DIST_MA20_MAX:
        return None
    return {
        'close': round(c, 2),
        'vol_ratio': round(vols[-1] / vma20, 2),
        'dist_ma20': round(dist * 100, 2),
        'ret20': round((c / closes[-21] - 1) * 100, 2) if len(closes) >= 21 else None,
        'last_date': d.get('dates', [''])[-1],
    }


def main():
    # 股名對照
    names = {}
    try:
        idx = json.loads((ROOT / 'docs' / 'stocks_index.json').read_text(encoding='utf-8'))
        names = {s['id']: s.get('name', s['id']) for s in idx}
    except Exception:
        pass

    bull = _taiex_bull()
    print(f'[breakout_scan] 大盤多頭開關：{"開" if bull else "關（今日不出訊號）"}')

    picks = []
    if bull:
        sids = sorted(p.stem for p in PRICE_CACHE.glob('*.json'))
        candidates = []
        for sid in sids:
            d = _load_cache(sid)
            if not d:
                continue
            feat = _tech_pass(d)
            if feat:
                candidates.append({'id': sid, **feat})
        print(f'[breakout_scan] 技術條件通過 {len(candidates)} 檔，查籌碼…')
        for cand in candidates:
            chip5 = _chip5(cand['id'])
            if chip5 > 0:
                cand['chip5'] = chip5
                cand['name'] = names.get(cand['id'], cand['id'])
                picks.append(cand)
        picks.sort(key=lambda x: -(x.get('ret20') or 0))

    out = {
        'date': datetime.now().strftime('%Y%m%d'),
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'taiex_bull': bull,
        'picks': picks,
        'backtest_ref': {
            'n': 183, 'tp18sl15_win': 0.65, 'trail15_avg': 15.05,
            'trail15_pf': 3.22, 'period': '2025/01~2026/06',
        },
    }
    (ROOT / 'docs' / 'breakout.json').write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'[breakout_scan] 今日領頭羊 {len(picks)} 檔 → docs/breakout.json')
    for p in picks:
        print(f"  {p['id']} {p.get('name','')} 量比{p['vol_ratio']} 法人5日{p['chip5']:+,.0f} 乖離{p['dist_ma20']}%")


if __name__ == '__main__':
    main()
