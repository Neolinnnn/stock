import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

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
                        'stock_name': '台積電', 'signal_close': 100.0}
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
