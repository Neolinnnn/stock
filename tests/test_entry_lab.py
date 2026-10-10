"""backtest_entry_lab：分法人籌碼快取、外部來源條件的特徵定義、0050 超額統計。"""
import pandas as pd
import pytest

import backtest_entry_lab as lab


def _ohlcv(n=70, last=None):
    """n 根平盤 K 棒（收 100、振幅 2），最後一根可覆寫。"""
    days = [d.strftime('%Y%m%d') for d in pd.bdate_range('2026-01-01', periods=n)]
    rows = [{'date': d, 'open': 100.0, 'high': 101.0, 'low': 99.0, 'close': 100.0, 'volume': 1000.0}
            for d in days]
    if last:
        rows[-1].update(last)
    return lab.compute_indicators(pd.DataFrame(rows))


def _features(df, chip=None):
    taiex = df[['date', 'close']].copy()
    sig = {'date': df.iloc[-1]['date']}
    return lab.build_features(sig, df, chip, taiex)


@pytest.fixture
def fake_chip_api(monkeypatch, tmp_path):
    monkeypatch.setattr(lab, 'CACHE_DIR', tmp_path)
    rows = []
    for d, f, t, ds, dh in [('2026-01-01', 100, 50, 10, 5), ('2026-01-02', -30, 20, 0, -8)]:
        for name, v in [('Foreign_Investor', f), ('Investment_Trust', t),
                        ('Dealer_self', ds), ('Dealer_Hedging', dh), ('Foreign_Dealer_Self', 999)]:
            rows.append({'date': d, 'name': name, 'buy': max(v, 0), 'sell': max(-v, 0)})

    class DL:
        calls = 0

        def taiwan_stock_institutional_investors(self, stock_id, start_date):
            DL.calls += 1
            return pd.DataFrame(rows)

    monkeypatch.setattr(lab, 'get_dl', lambda: DL())
    monkeypatch.setattr(lab.time, 'sleep', lambda s: None)
    return DL, tmp_path


def test_fetch_chip_splits_institutions(fake_chip_api):
    df = lab.fetch_chip('2330')
    assert df.to_dict('records') == [
        {'date': '20260101', 'net': 150, 'foreign': 100, 'trust': 50, 'dealer': 15},
        {'date': '20260102', 'net': -10, 'foreign': -30, 'trust': 20, 'dealer': -8},
    ]   # net 維持舊定義：外資＋投信（不含外資自營商）


def test_fetch_chip_refetches_old_net_only_cache(fake_chip_api):
    DL, cache = fake_chip_api
    (cache / '2330_chip.csv').write_text('date,net\n20260101,1\n', encoding='utf-8')
    df = lab.fetch_chip('2330')
    assert DL.calls == 1 and set(lab.CHIP_COLS) <= set(df.columns)
    lab.fetch_chip('2330')   # 新格式快取命中，不再呼叫 API
    assert DL.calls == 1


def test_fetch_chip_falls_back_to_old_cache_when_refetch_fails(fake_chip_api, monkeypatch):
    _, cache = fake_chip_api
    (cache / '2330_chip.csv').write_text('date,net\n20260101,7\n', encoding='utf-8')

    class Down:
        def taiwan_stock_institutional_investors(self, **kw):
            raise ConnectionError('quota')

    monkeypatch.setattr(lab, 'get_dl', lambda: Down())
    df = lab.fetch_chip('2330')
    assert df['net'].tolist() == [7] and df['trust'].isna().all()   # 既有 net 條件不受影響


def test_chip_features():
    df = _ohlcv()
    d = list(df['date'])
    chip = pd.DataFrame({'date': d[-3:], 'net': [1, 1, 1], 'foreign': [5, 5, 5],
                         'trust': [10, 20, 150], 'dealer': [1, 1, 1]})
    f = _features(df, chip)
    assert f['trust_big'] and f['trust3'] and f['inst3']   # 投信 150 / 量 1000 = 15%
    chip.loc[0, 'dealer'] = -1
    chip.loc[2, 'trust'] = 50
    f = _features(df, chip)
    assert f['trust3'] and not f['inst3'] and not f['trust_big']
    # 訊號日當天無籌碼資料 → 不認定為連買
    f = _features(df, chip.iloc[:2])
    assert not f['trust3'] and not f['trust_big']


def test_gap_nr7_break_features():
    # 平盤後跳空大漲：低點 105 > 前高 101、實體 105.5→109（+3.3%）、創 55 日新高
    f = _features(_ohlcv(last={'open': 105.5, 'high': 110.0, 'low': 105.0, 'close': 109.0}))
    assert f['gap_up'] and f['break55'] and f['tangle_break'] and f['big_red']
    assert f['nr7_break']   # 平盤期振幅相同，前一日即為 7 日最小
    f = _features(_ohlcv())
    assert not (f['gap_up'] or f['nr7_break'] or f['tangle_break'] or f['big_red'])


def test_summarize_adds_0050_excess():
    trades = [{'result': {'ret': 0.10, 'days': 5}, 'bench': 2.0},
              {'result': {'ret': -0.05, 'days': 5}, 'bench': 1.0},
              {'result': None, 'bench': None}]
    s = lab.summarize(trades)
    assert (s['n'], s['excess_return'], s['beat_rate']) == (2, 1.0, 0.5)
    assert 'excess_return' not in lab.summarize([{'result': {'ret': 0.1, 'days': 1}}])
