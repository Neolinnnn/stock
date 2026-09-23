from backtest import calc_next_trading_day, calc_stats, simulate_position

TRADING_DAYS = ['20260301', '20260302', '20260303', '20260304', '20260305']

def test_next_trading_day_normal():
    assert calc_next_trading_day('20260301', TRADING_DAYS) == '20260302'

def test_next_trading_day_last():
    # 最後一天沒有下一日
    assert calc_next_trading_day('20260305', TRADING_DAYS) is None

def test_next_trading_day_not_in_list():
    # 訊號日不在交易日清單（例如補假），取最近下一個
    assert calc_next_trading_day('20260301', ['20260303', '20260305']) == '20260303'

def test_simulate_position_tp_hit():
    # 停利 10%，第 3 天漲到 entry * 1.11 → WIN
    prices = {
        '20260302': 100.0,
        '20260303': 105.0,
        '20260304': 112.0,   # 漲超 10%
        '20260305': 95.0,
    }
    result = simulate_position(
        entry_date='20260302', entry_price=100.0, amount=3000,
        prices=prices, trading_days=['20260302','20260303','20260304','20260305'],
        tp=0.10, sl=0.05
    )
    assert result['result'] == 'WIN'
    assert result['exit_date'] == '20260304'
    assert abs(result['return_pct'] - 12.0) < 0.1

def test_simulate_position_sl_hit():
    # 停損 5%，第 2 天跌到 entry * 0.94 → LOSS
    prices = {
        '20260302': 100.0,
        '20260303': 94.0,    # 跌超 5%
        '20260304': 88.0,
    }
    result = simulate_position(
        entry_date='20260302', entry_price=100.0, amount=3000,
        prices=prices, trading_days=['20260302','20260303','20260304'],
        tp=0.10, sl=0.05
    )
    assert result['result'] == 'LOSS'
    assert result['exit_date'] == '20260303'

def test_simulate_position_open():
    # 兩天都沒觸發 → OPEN
    prices = {
        '20260302': 100.0,
        '20260303': 103.0,
    }
    result = simulate_position(
        entry_date='20260302', entry_price=100.0, amount=3000,
        prices=prices, trading_days=['20260302','20260303'],
        tp=0.10, sl=0.05
    )
    assert result['result'] == 'OPEN'

def test_calc_stats_basic():
    trades = [
        {'result': 'WIN',  'return_pct': 10.0, 'holding_days': 5},
        {'result': 'WIN',  'return_pct': 12.0, 'holding_days': 3},
        {'result': 'LOSS', 'return_pct': -5.0, 'holding_days': 2},
        {'result': 'OPEN', 'return_pct': None, 'holding_days': None},
    ]
    stats = calc_stats(trades)
    assert stats['total'] == 3       # OPEN 不計
    assert stats['wins'] == 2
    assert stats['losses'] == 1
    assert abs(stats['win_rate'] - 2/3) < 0.001
    assert abs(stats['avg_return'] - (10+12-5)/3) < 0.001
    assert stats['open_count'] == 1

from backtest import load_buy_signals, apply_position_limits
import tempfile, json, os

def _make_summary(date_str, signals):
    """建立最小 summary.json fixture"""
    sectors = {}
    for sid, name, sig in signals:
        sectors.setdefault('測試族群', {'stocks': []})
        sectors['測試族群']['stocks'].append({
            'id': sid, 'name': name, 'price': 100.0,
            'signal': sig, 'rsi': 50, 'ret_20d': 5.0,
            'cv_sharpe': 0, 'cv_win_rate': 0, 'news': [], 'chip': {}
        })
    return {'date': date_str, 'sectors': sectors}

def test_load_buy_signals_basic(tmp_path):
    # 建立兩天的 summary.json
    for date_str, signals in [
        ('20260301', [('2330', '台積電', 'BUY'), ('2317', '鴻海', 'HOLD')]),
        ('20260302', [('2330', '台積電', 'HOLD'), ('2317', '鴻海', 'BUY')]),
    ]:
        d = tmp_path / date_str
        d.mkdir()
        (d / 'summary.json').write_text(
            json.dumps(_make_summary(date_str, signals)), encoding='utf-8')

    sigs = load_buy_signals(str(tmp_path), start_date='20260301')
    assert len(sigs) == 2
    assert sigs[0] == {'date': '20260301', 'stock_id': '2330',
                        'stock_name': '台積電', 'signal_close': 100.0,
                        'cv_sharpe': 0, 'cv_win_rate': 0}
    assert sigs[1]['stock_id'] == '2317'

def test_load_buy_signals_dedup(tmp_path):
    # 同一個股同一天出現在兩個族群，只算一次
    d = tmp_path / '20260301'
    d.mkdir()
    sectors = {
        'A': {'stocks': [{'id': '2330', 'name': '台積電', 'price': 100.0,
                          'signal': 'BUY', 'rsi': 50, 'ret_20d': 0,
                          'cv_sharpe': 0, 'cv_win_rate': 0, 'news': [], 'chip': {}}]},
        'B': {'stocks': [{'id': '2330', 'name': '台積電', 'price': 100.0,
                          'signal': 'BUY', 'rsi': 50, 'ret_20d': 0,
                          'cv_sharpe': 0, 'cv_win_rate': 0, 'news': [], 'chip': {}}]},
    }
    (d / 'summary.json').write_text(
        json.dumps({'date': '20260301', 'sectors': sectors}), encoding='utf-8')

    sigs = load_buy_signals(str(tmp_path), start_date='20260301')
    assert len(sigs) == 1   # 去重

def test_apply_position_limits_basic():
    signals = [
        {'date': '20260301', 'stock_id': '2330', 'stock_name': '台積電', 'signal_close': 100.0},
        {'date': '20260302', 'stock_id': '2330', 'stock_name': '台積電', 'signal_close': 100.0},
        {'date': '20260303', 'stock_id': '2330', 'stock_name': '台積電', 'signal_close': 100.0},
        {'date': '20260304', 'stock_id': '2330', 'stock_name': '台積電', 'signal_close': 100.0},
    ]
    # 每次 3000，累積上限 10000 → 最多 3 次 (3000+3000+3000=9000 < 10000, 第4次只能投 1000)
    result = apply_position_limits(signals, per_trade=3000, max_per_stock=10000)
    assert len(result) == 4
    assert result[0]['amount'] == 3000
    assert result[1]['amount'] == 3000
    assert result[2]['amount'] == 3000
    assert result[3]['amount'] == 1000   # 只剩 1000 可投

from backtest import run_backtest_combo, run_all_backtests

def test_run_backtest_combo_integration():
    signals = [
        {'date': '20260301', 'stock_id': '2330', 'stock_name': '台積電', 'amount': 3000},
        {'date': '20260302', 'stock_id': '2317', 'stock_name': '鴻海',   'amount': 3000},
    ]
    price_data = {
        '2330': {
            '20260302': {'open': 100.0, 'close': 100.0, 'high': 100.0, 'low': 100.0},
            # prev_close=106; (111-106)/106 ≈ 4.7% < 5% → state=SELL_OPEN (not PULLBACK)
            '20260303': {'open': 100.0, 'close': 106.0, 'high': 106.0, 'low': 100.0},
            '20260304': {'open': 100.0, 'close': 111.0, 'high': 111.0, 'low': 100.0},  # TP10% → SELL_OPEN
            '20260305': {'open': 112.0, 'close': 112.0, 'high': 112.0, 'low': 111.0},  # sell at open → WIN
        },
        '2317': {
            '20260303': {'open': 200.0, 'close': 200.0, 'high': 200.0, 'low': 200.0},
            '20260304': {'open': 200.0, 'close': 188.0, 'high': 200.0, 'low': 188.0},  # SL5% → LOSS
        },
    }
    trading_days = ['20260301','20260302','20260303','20260304','20260305']
    result = run_backtest_combo(signals, price_data, trading_days, tp=0.10, sl=0.05)
    assert result['stats']['total'] == 2
    assert result['stats']['wins'] == 1
    assert result['stats']['losses'] == 1
    assert result['stats']['win_rate'] == 0.5

def test_run_all_backtests_keys():
    signals = [
        {'date': '20260301', 'stock_id': '2330', 'stock_name': '台積電', 'amount': 3000},
    ]
    price_data = {
        '2330': {'20260302': {'open': 100.0, 'close': 115.0}},  # TP hit for all TP configs
    }
    trading_days = ['20260301', '20260302']
    result = run_all_backtests(signals, price_data, trading_days)
    assert set(result.keys()) == {
        'TP15_SL10','TP15_SL12','TP15_SL15',
        'TP18_SL10','TP18_SL12','TP18_SL15',
        'TP20_SL10','TP20_SL12','TP20_SL15',
        'MA5_MA10',
    }


def _ma_prices():
    # 10 天 100 暖身 → 進場 → 漲到 120 → 跌破 MA5（賣半）→ 跌破 MA10（清倉）
    closes = [100] * 10 + [105, 110, 115, 120, 118, 112, 108, 100, 95]
    days = [f'2026{i:04d}' for i in range(1, len(closes) + 1)]
    return days, {d: {'open': c, 'close': c} for d, c in zip(days, closes)}


def test_simulate_position_ma_half_then_all():
    from backtest import simulate_position_ma
    days, px = _ma_prices()
    r = simulate_position_ma('20260010', 100.0, 3000, px, days)
    # 半倉於 d17 開盤 108 賣出，剩餘於 d18 開盤 100 出清 → 均價 104、+4%
    assert (r['result'], r['exit_date'], r['exit_price'], r['return_pct']) == ('WIN', '20260018', 104.0, 4.0)


def test_simulate_position_ma_half_sold_still_open():
    from backtest import simulate_position_ma
    days, px = _ma_prices()
    r = simulate_position_ma('20260010', 100.0, 3000, px, days[:17])
    assert r['result'] == 'OPEN' and r['exit_state'] == 'HALF_SOLD'


def test_filter_bias_ma10():
    from backtest import filter_bias_ma10
    mk = lambda last: {f'202601{i:02d}': {'close': 100.0 if i < 11 else last} for i in range(1, 12)}
    px = {'A': mk(101.0), 'B': mk(110.0),                      # 乖離 ~0.9% 留、~9% 剔除
          'C': {f'202601{i:02d}': {'close': 100.0} for i in range(1, 6)}}  # MA10 資料不足剔除
    out = filter_bias_ma10([{'stock_id': k, 'date': '20260111'} for k in 'ABC'], px)
    assert [s['stock_id'] for s in out] == ['A']
