"""daily_scan 的純函式測試（不打網路）。"""
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
ds = runpy.run_path(str(ROOT / 'scripts' / 'daily_scan.py'), run_name='daily_scan_test')


def test_chip_concentration_converts_lots_to_shares():
    # 法人近5日買超 2,000 張 = 2,000,000 股；5日成交 20,000,000 股 → 10%
    assert ds['chip_concentration'](2000, 20_000_000) == 10.0
    assert ds['chip_concentration'](-500, 10_000_000) == -5.0


def test_chip_concentration_missing_volume():
    assert ds['chip_concentration'](100, None) is None
    assert ds['chip_concentration'](100, 0) is None


def test_indicators_importable_from_scan(tmp_path):
    """模擬 CI 的 `python scripts/daily_scan.py`：sys.path[0] 只有 scripts/，且 cwd 不在 repo。

    在 pytest 行程內測不出來（pytest 已把 repo 根目錄放進 sys.path），故開子行程。
    """
    code = (
        "import runpy, sys\n"
        f"sys.path[0] = {str(ROOT / 'scripts')!r}\n"
        f"runpy.run_path({str(ROOT / 'scripts' / 'daily_scan.py')!r}, run_name='probe')\n"
        "from indicators.chip import chip_tier\n"
        "from indicators.broker import main_force_score\n"
    )
    r = subprocess.run([sys.executable, '-c', code], cwd=tmp_path,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-800:]


def _res(sid, price_date, chip_date):
    return {'id': sid, 'price_date': price_date,
            'chip': {'date': chip_date, '合計': 1} if chip_date else {}}


def test_freshness_rejects_previous_day_prices():
    # 20261023 案例：K 棒全停在前一日
    res = {'A': [_res(str(i), '2026-10-22', '2026-10-22') for i in range(10)]}
    ok, _ = ds['check_freshness']('2026-10-23', res)
    assert not ok


def test_freshness_rejects_stale_chips():
    # 20261104 案例：K 棒已更新、法人表尚未（77/105 停在前一日）
    res = {'A': [_res(str(i), '2026-11-04', '2026-11-04' if i < 3 else '2026-11-03')
                 for i in range(10)]}
    ok, msg = ds['check_freshness']('2026-11-04', res)
    assert not ok and '法人為當日 3/10' in msg


def test_freshness_tolerates_few_laggards_and_dedupes():
    stocks = [_res(str(i), '2026-11-05', '2026-11-05') for i in range(9)]
    stocks.append(_res('9', '2026-11-04', '2026-11-04'))   # 停牌一檔
    res = {'A': stocks, 'B': [stocks[0], {'id': 'x', 'error': 'boom'}]}
    ok, msg = ds['check_freshness']('2026-11-05', res)
    assert ok and '9/10' in msg


def test_drop_stale_chips_only_clears_old_dates():
    res = {'A': [_res('1', '2026-11-05', '2026-11-05'),
                 dict(_res('2', '2026-11-05', '2026-11-04'), chip_tier='strong')]}
    assert ds['drop_stale_chips']('2026-11-05', res) == 1
    assert res['A'][0]['chip'] and res['A'][1]['chip'] == {}
    assert res['A'][1]['chip_tier'] == 'neutral'


def _row(sid):
    return {'id': sid, 'name': sid, 'price': 100.0, 'rsi': 50.0, 'rsi10': 50.0,
            'ret_20d': 0.01, 'signal': 'HOLD', 'cv_sharpe': 0.1, 'cv_win_rate': 0.3,
            'cv_max_dd': 0.1, 'ma10': 100.0, 'chip': {}}


def test_coverage_counts_stocks_not_sectors():
    # 每族群都有一檔成功，舊算法（以族群計）會是 100%
    res = {'A': [_row('1'), {'id': '2', 'name': '2', 'error': 'x'}],
           'B': [_row('3'), {'id': '4', 'name': '4', 'error': 'x'}],
           'C': [_row('1')]}   # 跨族群重複只計一次
    s = ds['build_summary']('20261111', {}, res, None)
    assert s['scan_coverage'] == 50
    assert s['partial_sectors'] == {'A': 1, 'B': 1}


def test_coverage_zero_when_all_failed():
    res = {'A': [{'id': '1', 'name': '1', 'error': 'x'}]}
    assert ds['build_summary']('20261111', {}, res, None)['scan_coverage'] == 0


def test_chip_lots_truncate_toward_zero(monkeypatch):
    import pandas as pd
    df = pd.DataFrame([
        # 外資兩列合計 -500+(-400) = -900 股 → 0 張（舊算法逐列 //1000 得 -2）
        {'date': '2026-11-05', 'name': 'Foreign_Investor', 'buy': 0, 'sell': 500},
        {'date': '2026-11-05', 'name': 'Foreign_Dealer_Self', 'buy': 0, 'sell': 400},
        {'date': '2026-11-05', 'name': 'Investment_Trust', 'buy': 2500, 'sell': 0},
        {'date': '2026-11-05', 'name': 'Dealer_self', 'buy': 0, 'sell': 1500},
    ])
    # run_path 回傳的是 globals 副本，須改函式實際使用的 __globals__（否則會真的打 FinMind）
    monkeypatch.setitem(ds['fetch_chip_data'].__globals__, '_finmind_fetch',
                        lambda *a, **k: df)
    c = ds['fetch_chip_data']('2330')
    # 自營 -1500 股 → -1 張（向零），外資不會因兩列各自取整變成 -2
    assert (c['外資'], c['投信'], c['自營'], c['合計']) == (0, 2, -1, 1)


def test_summary_keeps_zero_and_drops_nan():
    import json
    import math
    a, b = _row('1'), _row('2')
    a['ret_20d'] = 0.0                       # 持平是有效值，不是缺值
    b['ret_20d'] = None                      # 經 DataFrame 變 NaN
    a['atr14'], b['atr14'] = 3.2, None
    s = ds['build_summary']('20261111', {}, {'A': [a, b]}, None)
    st = {x['id']: x for x in s['sectors']['A']['stocks']}
    assert st['1']['ret_20d'] == 0.0
    assert st['2']['ret_20d'] is None
    assert isinstance(st['2']['atr14'], float) and math.isnan(st['2']['atr14'])
    text = json.dumps(ds['_json_safe'](s))
    assert 'NaN' not in text


def test_title_mentions_disambiguates_prefix_collision():
    names = {'南亞', '南亞科', '台積電', '日月光投控'}
    m = ds['_title_mentions']
    assert not m('南亞科DRAM報價大漲', '1303', '南亞', names)
    assert m('南亞科DRAM報價大漲', '2408', '南亞科', names)
    assert m('南亞Q3獲利回升', '1303', '南亞', names)
    assert not m('南亞Q3獲利回升', '2408', '南亞科', names)
    assert m('日月光先進封裝擴產', '3711', '日月光投控', names)   # 無撞名仍用前 2 字
    assert m('2330 法說會', '2330', '台積電', names)


def test_news_time_is_taipei(monkeypatch):
    import json as _json
    ts = 1762790400   # 2025-11-10 16:00 UTC = 2025-11-11 00:00 台灣
    body = _json.dumps({'items': {'data': [
        {'publishAt': ts, 'title': '台積電 擴產', 'newsId': 1}]}}).encode()

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return body
    import urllib.request
    monkeypatch.setattr(urllib.request, 'urlopen', lambda *a, **k: R())
    real_dt = ds['datetime']

    class FakeDT(real_dt):
        @classmethod
        def now(cls, tz=None):
            return real_dt.fromtimestamp(ts + 3600, tz)
    monkeypatch.setitem(ds['fetch_news'].__globals__, 'datetime', FakeDT)
    news = ds['fetch_news']('2330', '台積電', days=3)
    assert news and news[0]['datetime'] == '2025-11-11 00:00'


def test_scan_cache_fetches_duplicate_stock_once(monkeypatch):
    g = ds['scan_sector'].__globals__
    calls = []

    def fake_analyze(sid, name, hist=None):
        calls.append(sid)
        return {'id': sid, 'name': name, 'signal': 'HOLD', 'rsi': 10}
    monkeypatch.setitem(g, 'analyze_stock', fake_analyze)
    monkeypatch.setitem(g, '_get_history', lambda sid: None)
    monkeypatch.setitem(g, 'fetch_price_stats', lambda sid, hist=None: (None,) * 6)
    monkeypatch.setitem(g, 'fetch_news', lambda *a, **k: [])
    monkeypatch.setitem(g, 'fetch_chip_data', lambda *a, **k: {})
    cache = {}
    a = ds['scan_sector']('封測', {'3711': '日月光投控', '2441': '超豐'}, cache=cache)
    b = ds['scan_sector']('CoPoS封裝', {'3711': '日月光投控'}, cache=cache)
    assert sorted(calls) == ['2441', '3711']
    assert b[0]['id'] == '3711' and b[0] is not a[0]
