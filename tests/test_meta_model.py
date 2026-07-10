# -*- coding: utf-8 -*-
"""Meta-labeling 模組測試：特徵組裝（meta_features）與 HYBRID 結算（build_signal_dataset）。"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from model.meta_features import FEATURES, build_features
from build_signal_dataset import settle_hybrid


# ── build_features ────────────────────────────────────────────────────────────

def test_features_keys_complete():
    """輸出鍵必須恰為 FEATURES（順序無關），確保訓練/預測欄位對齊。"""
    out = build_features({})
    assert set(out) == set(FEATURES)


def test_features_missing_all_nan():
    """空輸入 → 全 NaN（不拋錯，影子模式容錯）。"""
    out = build_features({})
    assert all(isinstance(v, float) and math.isnan(v) for v in out.values())


def test_ma_bull_alignment():
    stock = {'price': 105, 'ma5': 104, 'ma10': 103, 'ma20': 100, 'ma60': 95}
    assert build_features(stock)['ma_bull'] == 1.0
    stock['ma20'] = 106  # 破壞排列
    assert build_features(stock)['ma_bull'] == 0.0
    stock['ma60'] = None  # MA60 缺 → NaN
    assert math.isnan(build_features(stock)['ma_bull'])


def test_bias_and_chip_dir():
    out = build_features({'price': 102, 'ma10': 100, 'chip': {'合計': -500}})
    assert abs(out['bias_ma10'] - 2.0) < 1e-9
    assert out['chip_dir'] == -1.0


# ── settle_hybrid ─────────────────────────────────────────────────────────────

def _no_ma10(closes):
    return [None] * len(closes)


def test_settle_phase1_stop_loss():
    """進場 100，跌到 85 以下 → SL 出場。"""
    closes = [100, 100, 95, 84]
    i, reason, ret = settle_hybrid(closes, _no_ma10(closes), 1)
    assert (i, reason) == (3, 'SL')
    assert abs(ret - (-16.0)) < 1e-9


def test_settle_phase2_ma10_break():
    """達 +15% 進 Phase 2，之後收盤跌破 MA10 → MA10 出場（報酬 > 地板 7%）。"""
    closes = [100, 100, 116, 118, 112]
    ma10s = [None, None, None, None, 113]  # 最後一日跌破 MA10
    i, reason, ret = settle_hybrid(closes, ma10s, 1)
    assert (i, reason) == (4, 'MA10')
    assert abs(ret - 12.0) < 1e-9


def test_settle_phase2_profit_floor():
    """Phase 2 後跌回 +7% 地板以下 → FLOOR 出場（地板優先於 MA10 檢查）。"""
    closes = [100, 100, 116, 106]
    i, reason, ret = settle_hybrid(closes, _no_ma10(closes), 1)
    assert (i, reason) == (3, 'FLOOR')


def test_settle_phase2_timeout():
    """Phase 2 連續 25 日未創新高 → TIME 出場。"""
    closes = [100, 100, 116] + [110] * 30
    i, reason, ret = settle_hybrid(closes, _no_ma10(closes), 1)
    assert reason == 'TIME'
    assert i == 2 + 25  # 觸發日索引 2，其後第 25 日


def test_settle_open_when_no_exit():
    """始終在 Phase 1 區間震盪 → 未結案。"""
    closes = [100, 100, 105, 95, 100]
    i, reason, ret = settle_hybrid(closes, _no_ma10(closes), 1)
    assert (i, reason, ret) == (None, 'OPEN', None)
