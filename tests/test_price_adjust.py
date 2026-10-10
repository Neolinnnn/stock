"""price_adjust：公司行動事件解析、向後還原、全市場表共用與錯誤處理。"""
import math

import pandas as pd
import pytest

import price_adjust as pa

DAYS = ['20250801', '20250804', '20250805', '20250806']


def test_dividend_scales_only_days_before_ex_date():
    pm, vm = pa.multipliers(DAYS, [{'date': '20250805', 'factor': 0.97, 'shares': False}])
    assert pm == [0.97, 0.97, 1.0, 1.0]   # 除息日當天視為事件後
    assert vm == [1.0] * 4                 # 除息不改股數


def test_split_adjusts_volume_and_events_compound():
    events = [{'date': '20250804', 'factor': 0.25, 'shares': True},
              {'date': '20250806', 'factor': 0.98, 'shares': False}]
    pm, vm = pa.multipliers(DAYS, events)
    assert [round(x, 4) for x in pm] == [0.245, 0.98, 0.98, 1.0]
    assert vm == [4.0, 1.0, 1.0, 1.0]


def test_events_after_last_day_are_ignored():
    pm, _ = pa.multipliers(DAYS, [{'date': '20250901', 'factor': 0.9, 'shares': False}])
    assert pm == [1.0] * 4   # 最新價維持實際收盤


def test_parse_events_skips_invalid_rows_and_normalises_dates():
    df = pd.DataFrame({'date': ['2025-08-05', pd.Timestamp('2025-08-06'), '2025-08-07', '2025-08-08'],
                       'before_price': [100, 50, 0, None], 'reference_price': [97, 49, 10, 10]})
    ev = pa.parse_events(df, 'taiwan_stock_dividend_result', 'before_price', 'reference_price', False)
    assert [(e['date'], round(e['factor'], 2)) for e in ev] == [('20250805', 0.97), ('20250806', 0.98)]


def test_parse_events_raises_on_schema_change():
    df = pd.DataFrame({'date': ['2025-08-05'], 'before_price': [100]})
    with pytest.raises(ValueError, match='reference_price'):
        pa.parse_events(df, 'taiwan_stock_dividend_result', 'before_price', 'reference_price', False)


def test_dedupe_merges_same_event_from_two_tables():
    ev = pa.dedupe([{'date': '20250825', 'factor': 0.25, 'shares': False},
                    {'date': '20250825', 'factor': 0.25, 'shares': True},
                    {'date': '20250801', 'factor': 0.97, 'shares': False}])
    assert ev == [{'date': '20250801', 'factor': 0.97, 'shares': False},
                  {'date': '20250825', 'factor': 0.25, 'shares': True}]


def test_2327_par_value_change_removes_fake_crash():
    # 國巨 2025-08-13 收 546，停牌後 08-25 以約四分之一價恢復買賣（面額變更）
    df = pd.DataFrame({'date': ['20250813', '20250825'], 'open': [540.0, 142.0], 'high': [550.0, 145.0],
                       'low': [535.0, 140.0], 'close': [546.0, 143.0], 'volume': [1000.0, 4100.0]})
    adj = pa.adjust_df(df, [{'date': '20250825', 'factor': 136.5 / 546, 'shares': True}])
    assert math.isclose(adj['close'][0], 136.5)
    assert adj['close'][1] / adj['close'][0] - 1 > 0      # 真實漲幅，不再是 −73%
    assert math.isclose(adj['volume'][0], 4000.0)          # 舊股數換算成新股數
    assert df['close'][0] == 546.0                         # 不改原資料


def test_adjust_bars_keeps_non_price_fields():
    bars = {'20250804': {'open': 100.0, 'close': 100.0, 'note': 'x'}, '20250805': {'open': 97.0, 'close': 98.0}}
    adj = pa.adjust_bars(bars, [{'date': '20250805', 'factor': 0.97, 'shares': False}])
    assert adj['20250804'] == {'open': 97.0, 'close': 97.0, 'note': 'x'}
    assert adj['20250805'] == bars['20250805']
    assert pa.adjust_bars(bars, []) is bars


@pytest.fixture
def fake_api(monkeypatch):
    calls = []
    tables = {
        'taiwan_stock_dividend_result': pd.DataFrame(
            {'date': ['2025-07-10'], 'before_price': [100.0], 'reference_price': [96.0]}),
        'taiwan_stock_capital_reduction_reference_price': pd.DataFrame(),
        'taiwan_stock_split_price': pd.DataFrame(
            {'date': ['2025-06-18', '2025-06-18'], 'stock_id': ['0050', '0052'],
             'before_price': [188.65, 200.0], 'after_price': [47.16, 28.57]}),
        'taiwan_stock_par_value_change': pd.DataFrame(
            {'date': ['2025-08-25'], 'stock_id': ['2327'], 'before_close': [546.0], 'after_ref_close': [136.5]}),
    }

    def fetch(method, **kw):
        calls.append((method, kw.get('stock_id')))
        return tables[method]

    monkeypatch.setattr(pa, 'finmind_fetch', fetch)
    monkeypatch.setattr(pa, '_market_cache', {})
    return calls


def test_fetch_events_filters_market_tables_and_shares_them(fake_api):
    ev = pa.fetch_events('0050', '2024-10-01', '2026-10-01')
    assert [(e['date'], round(e['factor'], 2), e['shares']) for e in ev] == [
        ('20250618', 0.25, True), ('20250710', 0.96, False)]
    pa.fetch_events('2327', '2024-10-01', '2026-10-01')
    market = [c for c in fake_api if c[1] is None]
    assert len(market) == 2   # 全市場兩張表各只查一次


def test_fetch_events_skips_tier_locked_table_but_raises_transient(fake_api, monkeypatch):
    def locked(method, **kw):
        if method == 'taiwan_stock_capital_reduction_reference_price':
            raise Exception('Your level is register')
        return pd.DataFrame()
    monkeypatch.setattr(pa, 'finmind_fetch', locked)
    assert pa.fetch_events('2330', 'a', 'b') == []

    def down(method, **kw):
        raise ConnectionError('timeout')
    monkeypatch.setattr(pa, 'finmind_fetch', down)
    with pytest.raises(ConnectionError):
        pa.fetch_events('2330', 'a', 'b')
