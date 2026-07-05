from weekly_summary import compute_sector_metrics


def _report(sectors):
    return {'sectors': {name: {'avg_ret_20d': v, 'stocks': []} for name, v in sectors.items()}}


def test_compute_sector_metrics_change_and_level():
    week = [
        _report({'A': 1.0, 'B': 5.0}),   # 週一
        _report({'A': 4.0, 'B': 2.0}),   # 週五（最後一日 = level）
    ]
    metrics = compute_sector_metrics(week, prev_changes={})
    by = {m['sector']: m for m in metrics}
    assert by['A']['change'] == 3.0      # 4 - 1
    assert by['A']['level'] == 4.0       # 最後一日水位
    assert by['B']['change'] == -3.0     # 2 - 5
    assert by['B']['level'] == 2.0
    assert by['A']['prev_change'] is None


def test_compute_sector_metrics_sorted_by_change_desc():
    week = [_report({'A': 0.0, 'B': 0.0}), _report({'A': 1.0, 'B': 9.0})]
    metrics = compute_sector_metrics(week, prev_changes={})
    assert [m['sector'] for m in metrics] == ['B', 'A']


def test_compute_sector_metrics_prev_change_lookup():
    week = [_report({'A': 0.0}), _report({'A': 2.0})]
    metrics = compute_sector_metrics(week, prev_changes={'A': 5.0})
    assert metrics[0]['prev_change'] == 5.0


import json
from weekly_summary import load_prev_week_changes


def test_load_prev_week_changes_reads_latest_prior(tmp_path):
    # 建兩個舊週報資料夾，應讀較新的那個（且早於 today）
    (tmp_path / 'weekly_20260605').mkdir()
    (tmp_path / 'weekly_20260605' / 'weekly.json').write_text(
        json.dumps({'sector_changes': [{'sector': 'A', 'change': 5.0}]}), encoding='utf-8')
    (tmp_path / 'weekly_20260529').mkdir()
    (tmp_path / 'weekly_20260529' / 'weekly.json').write_text(
        json.dumps({'sector_changes': [{'sector': 'A', 'change': 1.0}]}), encoding='utf-8')

    prev = load_prev_week_changes('20260612', base_dir=tmp_path)
    assert prev == {'A': 5.0}


def test_load_prev_week_changes_none_when_absent(tmp_path):
    assert load_prev_week_changes('20260612', base_dir=tmp_path) == {}


from weekly_summary import build_narrative_context, generate_narrative


def test_build_narrative_context_picks_top_movers():
    metrics = [
        {'sector': 'A', 'change': 9.0, 'level': 10.0, 'prev_change': 1.0},
        {'sector': 'B', 'change': 3.0, 'level': 2.0, 'prev_change': None},
        {'sector': 'C', 'change': -8.0, 'level': -5.0, 'prev_change': 2.0},
    ]
    top_buys = [{'stock': '2368 金像電', 'buy_days': 5}]
    ctx = build_narrative_context(metrics, top_buys)
    assert ctx['accelerating'][0]['sector'] == 'A'
    assert ctx['decelerating'][0]['sector'] == 'C'
    assert ctx['top_buys'] == top_buys
    assert ctx['sector_metrics'] == metrics


class _FakeWriter:
    def __init__(self, raise_it=False):
        self.raise_it = raise_it
    def generate(self, task, context, **kw):
        if self.raise_it:
            raise RuntimeError('api down')
        return '本週輪動回顧…\n下週聚焦…'


def test_generate_narrative_returns_text():
    out = generate_narrative(_FakeWriter(), {'x': 1}, '20260612')
    assert '回顧' in out


def test_generate_narrative_fallbacks_to_empty_on_error():
    out = generate_narrative(_FakeWriter(raise_it=True), {'x': 1}, '20260612')
    assert out == ''


from weekly_summary import build_weekly_payload


def test_build_weekly_payload_carries_new_fields():
    summary = {
        'week_ending': '2026-06-12',
        'days_covered': 5,
        'sector_changes': [{'sector': 'A', 'change': 3.0, 'level': 4.0, 'prev_change': 1.0}],
        'top_buys': [{'stock': '2368 金像電', 'buy_days': 5}],
        'narrative': '本週輪動回顧…',
    }
    payload = build_weekly_payload(summary)
    assert payload['meta']['date'] == '2026-06-12'
    assert payload['changes'][0] == {'sector': 'A', 'change': 3.0, 'level': 4.0, 'prev_change': 1.0}
    assert payload['buys'][0] == {'stock': '2368 金像電', 'days': 5, 'tier': None, 'gate': False}
    assert payload['narrative'] == '本週輪動回顧…'


def test_build_weekly_payload_narrative_defaults_empty():
    summary = {'week_ending': '2026-06-12', 'days_covered': 5,
               'sector_changes': [], 'top_buys': []}
    assert build_weekly_payload(summary)['narrative'] == ''


# ── 新版週報：六節資料彙整 ────────────────────────────────────────────────────
from weekly_summary import (compute_market_week, build_rotation_matrix,
                            collect_week_signals, collect_positions_week,
                            collect_alerts_week, render_markdown)


def _full_report(date, sectors, market=None, positions=None, alerts=None):
    return {
        'date': date,
        'sectors': sectors,
        'market': market or {},
        'positions': positions or {},
        'alerts': alerts or [],
    }


def test_compute_market_week_compounds_daily_pct():
    week = [
        _full_report('d1', {}, market={'加權指數': 100.0, '漲跌幅': 1.0,
                                       '櫃買指數': 50.0, '櫃買漲跌幅': 2.0}),
        _full_report('d2', {}, market={'加權指數': 102.0, '漲跌幅': 1.0,
                                       '櫃買指數': 51.0, '櫃買漲跌幅': -1.0},
                     positions={'taiex_bull': True, 'taiex_ma60': 90.0}),
    ]
    m = compute_market_week(week)
    assert m['taiex_close'] == 102.0
    assert m['taiex_week_pct'] == 2.01          # (1.01*1.01-1)*100
    assert m['otc_week_pct'] == 0.98            # (1.02*0.99-1)*100
    assert m['taiex_bull'] is True
    assert m['taiex_ma60'] == 90.0


def test_compute_market_week_missing_fields():
    m = compute_market_week([{'sectors': {}}, {'sectors': {}}])
    assert m['taiex_close'] is None
    assert m['taiex_week_pct'] is None
    assert m['taiex_bull'] is None


def test_build_rotation_matrix_quadrants_and_v_turn():
    metrics = [
        {'sector': 'LEAD', 'change': 5.0, 'level': 10.0, 'prev_change': 1.0},
        {'sector': 'TURN', 'change': 8.0, 'level': -2.0, 'prev_change': -20.0},
        {'sector': 'COOL', 'change': -3.0, 'level': 4.0, 'prev_change': 2.0},
        {'sector': 'WEAK', 'change': -6.0, 'level': -8.0, 'prev_change': None},
    ]
    rot = build_rotation_matrix(metrics)
    assert [m['sector'] for m in rot['leading']] == ['LEAD']
    assert [m['sector'] for m in rot['turning']] == ['TURN']
    assert [m['sector'] for m in rot['cooling']] == ['COOL']
    assert [m['sector'] for m in rot['weak']] == ['WEAK']
    assert [m['sector'] for m in rot['v_turn']] == ['TURN']  # -20 → +8


def test_collect_week_signals_counts_tier_and_gate():
    def _stock(sid, name, signal, tier=None):
        return {'id': sid, 'name': name, 'signal': signal, 'chip_tier': tier}
    week = [
        _full_report('d1', {'S': {'stocks': [_stock('1101', '甲', 'BUY', 'weak')]}},
                     positions={'gate_buys': [{'id': '1101'}]}),
        _full_report('d2', {'S': {'stocks': [_stock('1101', '甲', 'BUY', 'strong'),
                                             _stock('2202', '乙', 'BUY')]}}),
    ]
    sig = collect_week_signals(week)
    assert sig[0] == {'stock': '1101 甲', 'buy_days': 2,
                      'chip_tier': 'strong', 'gate': True}   # tier 取最新
    assert sig[1] == {'stock': '2202 乙', 'buy_days': 1,
                      'chip_tier': None, 'gate': False}


def test_collect_positions_week_dedupes_and_pnl():
    hold = {'id': '1101', 'name': '甲', 'signal_date': 'd0',
            'entry_price': 100.0, 'phase': 2, 'days_since_high': 3}
    week = [
        _full_report('d1', {}, positions={'new_entries': [{'id': '1101', 'name': '甲'}]}),
        _full_report('d2', {'S': {'stocks': [{'id': '1101', 'price': 110.0, 'signal': 'HOLD',
                                              'chip_tier': None}]}},
                     positions={'holding': [hold, dict(hold)],  # 重複兩筆
                                'new_exits': [{'id': '2202', 'name': '乙',
                                               'return_pct': -5.0, 'exit_reason': 'SL'}]}),
    ]
    pw = collect_positions_week(week)
    assert len(pw['entries']) == 1 and pw['entries'][0]['date'] == 'd1'
    assert len(pw['exits']) == 1 and pw['exits'][0]['exit_reason'] == 'SL'
    assert len(pw['holding']) == 1                # 去重
    assert pw['holding'][0]['pnl_pct'] == 10.0    # (110-100)/100


def test_collect_alerts_week_accumulates_days():
    a = {'sector': 'S', 'id': '1101', 'name': '甲', 'type': 'RSI過熱'}
    a2 = {**a, 'sector': 'S2'}  # 同檔另一族群，同日不得重複計次
    week = [
        _full_report('d1', {}, alerts=[{**a, 'detail': 'RSI=71'}, {**a2, 'detail': 'RSI=71'}]),
        _full_report('d2', {}, alerts=[{**a, 'detail': 'RSI=75'},
                                       {'sector': 'S', 'id': '2202', 'name': '乙',
                                        'type': 'RSI過熱', 'detail': 'RSI=72'}]),
    ]
    out = collect_alerts_week(week)
    assert out[0]['days'] == 2 and out[0]['detail'] == 'RSI=75'  # 留最新
    assert out[1]['days'] == 1


def test_render_markdown_six_sections():
    summary = {
        'week_ending': '2026-07-03', 'days_covered': 5,
        'market': {'taiex_close': 46780.62, 'taiex_week_pct': 1.5,
                   'otc_close': 445.38, 'otc_week_pct': 2.0,
                   'taiex_bull': True, 'taiex_ma60': 42193.2},
        'rotation_matrix': {
            'leading': [{'sector': 'A', 'change': 5.0, 'level': 10.0}],
            'turning': [], 'cooling': [], 'weak': [],
            'v_turn': [{'sector': 'A', 'change': 5.0, 'prev_change': -3.0}]},
        'top_buys': [{'stock': '1101 甲', 'buy_days': 3, 'chip_tier': 'strong', 'gate': True}],
        'positions_week': {'entries': [], 'exits': [],
                           'holding': [{'id': '1101', 'name': '甲', 'pnl_pct': 7.7,
                                        'days_since_high': 0}]},
        'alerts_week': [{'id': '2345', 'name': '智邦', 'type': 'RSI過熱',
                         'days': 3, 'detail': 'RSI=76.8'}],
        'narrative': '本週輪動回顧…',
    }
    md = render_markdown(summary)
    for header in ('一、市場總覽', '二、族群輪動矩陣', '三、訊號榜',
                   '四、持倉週記', '五、風險警示', '六、AI 週評'):
        assert header in md
    assert '🟢 多頭' in md
    assert '強' in md and '✅' in md
    assert '甲（+7.7%、距高點 0 天）' in md
    assert 'RSI過熱 × 3 天' in md
