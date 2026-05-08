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
