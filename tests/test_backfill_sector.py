"""backfill_sector：只新增補建族群，既有內容與每日掃描實際產出的區塊不動。"""
import json
from datetime import date, timedelta

import pandas as pd

import backfill_sector as bs

SECTOR = '新族群'
STOCKS = {'9999': '測試股'}


def _history(end=date(2026, 10, 30), n=700):
    """n 個工作日的合成 K 棒（緩漲＋鋸齒）與三大法人買賣超。"""
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    close = [50 + i * 0.05 + (i % 7) * 0.4 for i in range(n)]
    px = pd.DataFrame({'date': days, 'open': close, 'high': [c + 1 for c in close],
                       'low': [c - 1 for c in close], 'close': close, 'volume': [1_000_000] * n})
    inst = pd.DataFrame([{'date': x, 'name': 'Foreign_Investor', 'buy': 300_000, 'sell': 100_000}
                         for x in days])
    return {'9999': (px, inst)}


def _peer(live):
    st = {'id': '2330', 'name': '台積電', 'price': 100.0, 'rsi': 50.0, 'ret_20d': 1.0,
          'signal': 'HOLD', 'cv_sharpe': 1.0, 'cv_win_rate': 0.5, 'news': [], 'chip': {}}
    if live:
        st.update({'rsi10': 50.0, 'ma5': 100.0, 'ma10': 100.0, 'ma20': 100.0, 'ma60': 100.0,
                   'chip_tier': 'neutral'})
    return st


def _report(tmp_path, day, live, compact=False):
    summary = {'date': day, 'sectors': {'既有': {'avg_ret_20d': 1.0, 'stocks': [_peer(live)]}},
               'strong_sectors': [], 'weak_sectors': [], 'qualified': [{'id': '2330'}]}
    p = tmp_path / day / 'summary.json'
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps(summary, ensure_ascii=False, **({} if compact else {'indent': 2})),
                 encoding='utf-8')
    return p, summary


def test_live_report_gets_sector_with_peer_fields_only(tmp_path):
    p, before = _report(tmp_path, '20261030', live=True)
    assert bs.backfill_report(p, SECTOR, STOCKS, _history(), '2026-11-13') == 'patched'
    after = json.loads(p.read_text(encoding='utf-8'))
    entry = after['sectors'].pop(SECTOR)
    st = entry['stocks'][0]

    assert entry['backfilled'] == '2026-11-13'
    assert st['price'] == _history()['9999'][0]['close'].iloc[-1]      # 截至當日的收盤
    assert set(st) <= set(_peer(live=True))                             # 同日既有個股沒有的欄位不寫
    assert 'trade_value' not in st and st['ma60'] is not None
    assert st['chip']['外資'] == 200 and st['chip']['連買日'] == 5      # 20 萬股 → 200 張
    for key in ('strong_sectors', 'weak_sectors'):
        after[key] = [s for s in after[key] if s != SECTOR]
    assert after == before                                              # 其餘內容（含 qualified）不變


def test_legacy_report_uses_asof_data_and_legacy_fields(tmp_path):
    p, _ = _report(tmp_path, '20250602', live=False)
    data = _history()
    assert bs.backfill_report(p, SECTOR, STOCKS, data, '2026-11-13') == 'patched'
    st = json.loads(p.read_text(encoding='utf-8'))['sectors'][SECTOR]['stocks'][0]
    px = data['9999'][0]
    assert st['price'] == px[px['date'] <= date(2025, 6, 2)]['close'].iloc[-1]   # 不讀報告日之後的價格
    assert 'rsi10' not in st and 'ma5' not in st


def test_scanned_sector_is_never_overwritten(tmp_path):
    p, _ = _report(tmp_path, '20261030', live=True)
    data = _history()
    bs.backfill_report(p, SECTOR, STOCKS, data, '2026-11-13')
    assert bs.backfill_report(p, SECTOR, STOCKS, data, '2026-11-20') == 'exists'             # 預設不重算
    assert bs.backfill_report(p, SECTOR, STOCKS, data, '2026-11-20', force=True) == 'patched'
    # 每日掃描實際產出的區塊（無 backfilled 標記）連 --force 也不動
    assert bs.backfill_report(p, '既有', STOCKS, data, '2026-11-20', force=True) == 'exists'


def test_single_line_report_keeps_its_format(tmp_path):
    p, _ = _report(tmp_path, '20250602', live=False, compact=True)
    bs.backfill_report(p, SECTOR, STOCKS, _history(), '2026-11-13')
    assert '\n' not in p.read_text(encoding='utf-8')
