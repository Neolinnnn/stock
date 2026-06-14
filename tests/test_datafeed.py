"""datafeed 取數層的離線單元測試（不打網路）。

涵蓋：快取讀寫 round-trip、CachedStock、yfinance 備援欄位、token 輪替。
"""
import json

import pandas as pd
import pytest

import datafeed


@pytest.fixture(autouse=True)
def _tmp_cache(tmp_path, monkeypatch):
    """把快取目錄導向 tmp，避免污染真實 price_cache/。"""
    monkeypatch.setattr(datafeed, 'PRICE_CACHE_DIR', tmp_path)
    # 每個測試重置 token 狀態
    monkeypatch.setattr(datafeed, '_FINMIND_TOKENS', [], raising=False)
    monkeypatch.setattr(datafeed, '_finmind_token_idx', 0, raising=False)
    yield


def test_save_load_price_cache_roundtrip():
    from datetime import date
    dates = [date(2026, 1, 5), date(2026, 1, 6)]
    datafeed.save_price_cache('1234', dates, [10.0, 11.0], [10.5, 11.5], [9.5, 10.5], [100, 200])
    out = datafeed.load_price_cache('1234')
    assert out['dates'] == dates
    assert out['prices'] == [10.0, 11.0]
    assert out['highs'] == [10.5, 11.5]
    assert out['volumes'] == [100, 200]


def test_load_price_cache_missing_returns_none():
    assert datafeed.load_price_cache('0000') is None


def test_cached_stock_shape():
    s = datafeed.CachedStock([1.0], ['2026-01-05'], [1.1], [0.9])
    assert s.price == [1.0]
    assert s.volume == []   # 預設空 list


def test_yf_history_maps_columns(monkeypatch):
    """yfinance 回傳 OHLCV → 轉成 FinMind 同欄位 DataFrame。"""
    import types

    class _FakeHist:
        empty = False
        index = pd.to_datetime(['2026-01-05', '2026-01-06'])
        def __getitem__(self, k):
            return {
                'Close':  pd.Series([10.0, 11.0]),
                'High':   pd.Series([10.5, 11.5]),
                'Low':    pd.Series([9.5, 10.5]),
                'Volume': pd.Series([100, 200]),
            }[k]

    class _FakeTicker:
        def __init__(self, sym): pass
        def history(self, **kw): return _FakeHist()

    fake_yf = types.SimpleNamespace(Ticker=_FakeTicker)
    monkeypatch.setitem(__import__('sys').modules, 'yfinance', fake_yf)

    df = datafeed.yf_history('2330', '2026-01-05', '2026-01-06')
    assert list(df.columns) == ['date', 'close', 'max', 'min', 'Trading_Volume']
    assert df['close'].tolist() == [10.0, 11.0]
    assert df['date'].tolist() == ['2026-01-05', '2026-01-06']


def test_finmind_fetch_rotates_token_on_quota(monkeypatch):
    """第一組 token 額度滿 → 自動切第二組並成功。"""
    monkeypatch.setattr(datafeed, '_FINMIND_TOKENS', ['t1', 't2'], raising=False)
    calls = {'n': 0}

    class _FakeDL:
        def some_method(self, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise RuntimeError('Reached the upper limit of the API usage')
            return 'ok'

    monkeypatch.setattr(datafeed, 'make_dataloader', lambda: _FakeDL())
    # retry 內含 sleep，縮短等待
    monkeypatch.setattr(datafeed.time, 'sleep', lambda *a: None)

    assert datafeed.finmind_fetch('some_method') == 'ok'
    assert calls['n'] == 2   # 第一次失敗、換 token 後第二次成功
