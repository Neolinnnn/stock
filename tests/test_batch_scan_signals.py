"""analyze_stock 的 SELL 訊號對稱過濾（MA5/MA20 趨勢 + CV 夏普值）測試。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))
import batch_scan
from batch_scan import analyze_stock


class _FakeHist:
    """模擬 twstock Stock 物件，僅需 .price / .date 供 analyze_stock 讀取。"""

    def __init__(self, prices):
        self.price = prices
        self.date = list(range(len(prices)))


def _bearish_prices(n=60):
    """末段下跌：最新 MA5 < MA20（趨勢確實轉空）。"""
    return [100.0 + i * 0.5 for i in range(n - 20)] + \
           [100.0 + (n - 20) * 0.5 - i for i in range(20)]


def _bullish_prices(n=60):
    """末段仍上漲：最新 MA5 > MA20（趨勢仍偏多）。"""
    return [100.0 + i * 0.5 for i in range(n)]


def _cv_result(sharpe):
    return [{'return': 0.0, 'sharpe': sharpe, 'max_dd': 0.0, 'win_rate': 0.0, 'trades': 1}]


def _run(monkeypatch, prices, signal, sharpe):
    monkeypatch.setattr(batch_scan, 'generate_signals',
                        lambda *a, **kw: [{'date': 0, 'price': prices[-1], 'signal': signal}])
    monkeypatch.setattr(batch_scan, 'walk_forward_cv',
                        lambda *a, **kw: _cv_result(sharpe))
    hist = _FakeHist(prices)
    return analyze_stock('0000', 'test', days=len(prices), hist=hist)


def test_sell_kept_when_bearish_and_sharpe_negative(monkeypatch):
    """趨勢確實轉空（MA5<=MA20）+ 夏普為負 → SELL 維持。"""
    result = _run(monkeypatch, _bearish_prices(), 'SELL', sharpe=-0.5)
    assert result['signal'] == 'SELL'


def test_sell_downgraded_when_still_bullish(monkeypatch):
    """死叉觸發但最新 MA5>MA20（趨勢仍偏多）→ SELL 降為 HOLD。"""
    result = _run(monkeypatch, _bullish_prices(), 'SELL', sharpe=-0.5)
    assert result['signal'] == 'HOLD'


def test_sell_downgraded_when_sharpe_positive(monkeypatch):
    """趨勢轉空但 CV 夏普為正（策略仍賺錢）→ SELL 降為 HOLD。"""
    result = _run(monkeypatch, _bearish_prices(), 'SELL', sharpe=0.5)
    assert result['signal'] == 'HOLD'


def test_buy_kept_when_bullish_and_sharpe_positive(monkeypatch):
    """既有 BUY 邏輯迴歸測試：多頭排列 + 夏普為正 → BUY 維持。"""
    result = _run(monkeypatch, _bullish_prices(), 'BUY', sharpe=0.5)
    assert result['signal'] == 'BUY'


def test_buy_downgraded_when_bearish(monkeypatch):
    """既有 BUY 邏輯迴歸測試：非多頭排列（MA5<=MA20）→ BUY 降為 HOLD。"""
    result = _run(monkeypatch, _bearish_prices(), 'BUY', sharpe=0.5)
    assert result['signal'] == 'HOLD'


def test_buy_downgraded_when_sharpe_negative(monkeypatch):
    """既有 BUY 邏輯迴歸測試：夏普為負 → BUY 降為 HOLD。"""
    result = _run(monkeypatch, _bullish_prices(), 'BUY', sharpe=-0.5)
    assert result['signal'] == 'HOLD'
