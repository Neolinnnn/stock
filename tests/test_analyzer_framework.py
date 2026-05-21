# tests/test_analyzer_framework.py
import sys, os
import importlib.util
import pandas as pd
import pytest

_mod_path = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'strategies', '07_analyzer_framework.py')
_spec = importlib.util.spec_from_file_location('analyzer_framework', _mod_path)
_mod  = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

compute_ma_indicators = _mod.compute_ma_indicators
generate_signals_a    = _mod.generate_signals_a
generate_signals_b    = _mod.generate_signals_b


def _make_uptrend_df(n: int = 60) -> pd.DataFrame:
    """建立 n 根遞增收盤的合成日線，volume 固定 1000。"""
    dates = pd.bdate_range('2025-01-01', periods=n)
    prices = [100.0 + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        'date':   dates.strftime('%Y-%m-%d'),
        'open':   prices,
        'high':   [p * 1.01 for p in prices],
        'low':    [p * 0.99 for p in prices],
        'close':  prices,
        'volume': [1000] * n,
    })


def _make_downtrend_df(n: int = 60) -> pd.DataFrame:
    """建立 n 根遞減收盤的合成日線。"""
    dates = pd.bdate_range('2025-01-01', periods=n)
    prices = [130.0 - i * 0.5 for i in range(n)]
    return pd.DataFrame({
        'date':   dates.strftime('%Y-%m-%d'),
        'open':   prices,
        'high':   [p * 1.01 for p in prices],
        'low':    [p * 0.99 for p in prices],
        'close':  prices,
        'volume': [1000] * n,
    })


# ── compute_ma_indicators ────────────────────────────────────────────────────

def test_compute_ma_indicators_adds_columns():
    df = compute_ma_indicators(_make_uptrend_df())
    assert 'ma5'      in df.columns
    assert 'ma20'     in df.columns
    assert 'vol_ma20' in df.columns


def test_compute_ma_indicators_ma5_gt_ma20_in_uptrend():
    df = compute_ma_indicators(_make_uptrend_df(60))
    last = df.iloc[-1]
    assert last['ma5'] > last['ma20']


def test_compute_ma_indicators_ma5_lt_ma20_in_downtrend():
    df = compute_ma_indicators(_make_downtrend_df(60))
    last = df.iloc[-1]
    assert last['ma5'] < last['ma20']


# ── generate_signals_a ───────────────────────────────────────────────────────

def test_generate_signals_a_no_signal_when_bearish():
    """空頭排列（MA5 < MA20）→ 不產生任何訊號。"""
    df = compute_ma_indicators(_make_downtrend_df())
    sigs = generate_signals_a('9999', df, '空頭股')
    assert len(sigs) == 0


def test_generate_signals_a_produces_signal_when_all_conditions_met():
    """手動讓最後一行滿足 4 條件，確認產生訊號。"""
    df = compute_ma_indicators(_make_uptrend_df(60))
    last = df.index[-1]
    ma5      = df.at[last, 'ma5']
    vol_ma20 = df.at[last, 'vol_ma20']
    # c2 & c4：close 在 MA5 +1%（< 5% 且在 ±3% 區間）
    df.at[last, 'close']  = ma5 * 1.01
    # c3：縮量
    df.at[last, 'volume'] = vol_ma20 * 0.5

    sigs = generate_signals_a('1234', df, '多頭股')
    last_date = df.iloc[-1]['date'].replace('-', '')
    assert any(s['date'] == last_date for s in sigs), '應在最後一天產生訊號'


def test_generate_signals_a_no_signal_when_bias_exceeds_5pct():
    """偏離度 > 5% → 不進場。"""
    df = compute_ma_indicators(_make_uptrend_df(60))
    last = df.index[-1]
    ma5 = df.at[last, 'ma5']
    df.at[last, 'close'] = ma5 * 1.10   # 偏離 10%
    sigs = generate_signals_a('1234', df, '追高股')
    last_date = df.iloc[-1]['date'].replace('-', '')
    assert not any(s['date'] == last_date for s in sigs)


def test_generate_signals_a_no_signal_when_volume_high():
    """量比 >= 0.8 → 不進場（非縮量）。"""
    df = compute_ma_indicators(_make_uptrend_df(60))
    last = df.index[-1]
    ma5      = df.at[last, 'ma5']
    vol_ma20 = df.at[last, 'vol_ma20']
    df.at[last, 'close']  = ma5 * 1.01
    df.at[last, 'volume'] = vol_ma20 * 1.2  # 放量，非縮量
    sigs = generate_signals_a('1234', df, '放量股')
    last_date = df.iloc[-1]['date'].replace('-', '')
    assert not any(s['date'] == last_date for s in sigs)


def test_generate_signals_a_signal_format():
    """驗證回傳的 dict 有必要欄位。"""
    df = compute_ma_indicators(_make_uptrend_df(60))
    last = df.index[-1]
    ma5      = df.at[last, 'ma5']
    vol_ma20 = df.at[last, 'vol_ma20']
    df.at[last, 'close']  = ma5 * 1.01
    df.at[last, 'volume'] = vol_ma20 * 0.5
    sigs = generate_signals_a('1234', df, '多頭股')
    assert len(sigs) > 0
    s = sigs[-1]
    for key in ('date', 'stock_id', 'stock_name', 'signal_close', 'amount'):
        assert key in s, f'缺少欄位：{key}'
    assert s['stock_id'] == '1234'
    assert s['stock_name'] == '多頭股'
    assert len(s['date']) == 8 and s['date'].isdigit()


# ── generate_signals_b ───────────────────────────────────────────────────────

def test_generate_signals_b_filters_when_net_sell():
    """規則A通過但法人賣超 → 規則B過濾掉。"""
    df = compute_ma_indicators(_make_uptrend_df(60))
    last = df.index[-1]
    ma5      = df.at[last, 'ma5']
    vol_ma20 = df.at[last, 'vol_ma20']
    df.at[last, 'close']  = ma5 * 1.01
    df.at[last, 'volume'] = vol_ma20 * 0.5
    last_date_iso = df.iloc[-1]['date']   # YYYY-MM-DD

    inst_df = pd.DataFrame([
        {'date': last_date_iso, 'name': '外資及陸資', 'buy': 0,   'sell': 5000},
        {'date': last_date_iso, 'name': '投信',       'buy': 0,   'sell': 1000},
    ])
    sigs_a = generate_signals_a('1234', df, '多頭股')
    sigs_b = generate_signals_b('1234', df, inst_df, '多頭股')
    assert len(sigs_b) < len(sigs_a), '法人賣超應被過濾'


def test_generate_signals_b_keeps_when_net_buy():
    """規則A通過且法人買超 → 規則B保留。"""
    df = compute_ma_indicators(_make_uptrend_df(60))
    last = df.index[-1]
    ma5      = df.at[last, 'ma5']
    vol_ma20 = df.at[last, 'vol_ma20']
    df.at[last, 'close']  = ma5 * 1.01
    df.at[last, 'volume'] = vol_ma20 * 0.5
    last_date_iso = df.iloc[-1]['date']

    inst_df = pd.DataFrame([
        {'date': last_date_iso, 'name': '外資及陸資', 'buy': 8000, 'sell': 2000},
        {'date': last_date_iso, 'name': '投信',       'buy': 1000, 'sell': 0},
    ])
    sigs_a = generate_signals_a('1234', df, '多頭股')
    sigs_b = generate_signals_b('1234', df, inst_df, '多頭股')
    assert len(sigs_b) == len(sigs_a), '法人買超應保留所有訊號'


def test_generate_signals_b_empty_inst_equals_rule_a():
    """inst_df 為空時，規則B 結果等同規則A。"""
    df = compute_ma_indicators(_make_uptrend_df(60))
    sigs_a = generate_signals_a('1234', df, '多頭股')
    sigs_b = generate_signals_b('1234', df, pd.DataFrame(), '多頭股')
    assert len(sigs_b) == len(sigs_a)
