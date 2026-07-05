"""基本面功課：檢查清單評分規則測試（合成資料 → 預期燈號與分數）。"""
import pytest

from fundamental_homework import (
    build_checklist,
    check_revenue_yoy_streak, check_cum_yoy, check_revenue_mom,
    check_gross_margin_trend, check_eps_positive, check_eps_growth,
    check_pe_percentile, check_chip_net,
    _parse_gemini_json, _quant_brief,
)


# ── 個別規則 ──────────────────────────────────────────────────────────────────

def test_rev_yoy_green_when_3_months_positive():
    status, _ = check_revenue_yoy_streak({'yoy': [None, 5.0, 12.3, 8.1]})
    assert status == 'green'


def test_rev_yoy_yellow_when_only_latest_positive():
    status, _ = check_revenue_yoy_streak({'yoy': [-3.0, -1.0, 2.0]})
    assert status == 'yellow'


def test_rev_yoy_red_when_latest_negative():
    status, _ = check_revenue_yoy_streak({'yoy': [5.0, 3.0, -2.0]})
    assert status == 'red'


def test_rev_yoy_gray_when_missing():
    assert check_revenue_yoy_streak(None)[0] == 'gray'
    assert check_revenue_yoy_streak({'yoy': [None, None]})[0] == 'gray'


def test_cum_yoy_thresholds():
    assert check_cum_yoy({'cum_yoy': [15.0]})[0] == 'green'
    assert check_cum_yoy({'cum_yoy': [5.0]})[0] == 'yellow'
    assert check_cum_yoy({'cum_yoy': [-1.0]})[0] == 'red'


def test_mom_thresholds():
    assert check_revenue_mom({'mom': [3.0]})[0] == 'green'
    assert check_revenue_mom({'mom': [-2.0]})[0] == 'yellow'
    assert check_revenue_mom({'mom': [-9.0]})[0] == 'red'


def test_gross_margin_green_needs_two_consecutive_rises():
    assert check_gross_margin_trend({'gross_margin': [40.0, 42.0, 45.0]})[0] == 'green'
    assert check_gross_margin_trend({'gross_margin': [44.0, 42.0, 45.0]})[0] == 'yellow'
    assert check_gross_margin_trend({'gross_margin': [45.0, 42.0]})[0] == 'red'
    assert check_gross_margin_trend({'gross_margin': [45.0]})[0] == 'gray'


def test_eps_positive_states():
    assert check_eps_positive({'eps': [1.0, 1.2, 0.8, 1.5]})[0] == 'green'
    assert check_eps_positive({'eps': [-0.5, 1.2, -0.8, 1.5]})[0] == 'yellow'
    assert check_eps_positive({'eps': [1.0, 1.2, 0.8, -0.5]})[0] == 'red'


def test_eps_growth_compares_4q_sums_when_8q_available():
    growing = {'eps': [1, 1, 1, 1, 2, 2, 2, 2]}
    shrinking = {'eps': [2, 2, 2, 2, 1, 1, 1, 1]}
    assert check_eps_growth(growing)[0] == 'green'
    assert check_eps_growth(shrinking)[0] == 'red'


def test_eps_growth_falls_back_to_yoy_when_short():
    assert check_eps_growth({'eps': [1, 2], 'yoy': [None, 20.0]})[0] == 'yellow'
    assert check_eps_growth({'eps': [1, 2], 'yoy': [None, -20.0]})[0] == 'red'
    assert check_eps_growth({'eps': [1, 2], 'yoy': [None, None]})[0] == 'gray'


def test_pe_percentile_bands():
    assert check_pe_percentile({'per': 12.0, 'pe_percentile': 20.0})[0] == 'green'
    assert check_pe_percentile({'per': 18.0, 'pe_percentile': 50.0})[0] == 'yellow'
    assert check_pe_percentile({'per': 30.0, 'pe_percentile': 85.0})[0] == 'red'
    assert check_pe_percentile({'per': None, 'pe_percentile': None})[0] == 'gray'


def test_chip_net_states():
    assert check_chip_net({'net20': 500, 'net60': 1200})[0] == 'green'
    assert check_chip_net({'net20': -200, 'net60': 800})[0] == 'yellow'
    assert check_chip_net({'net20': -200, 'net60': -100})[0] == 'red'
    assert check_chip_net(None)[0] == 'gray'


# ── 綜合評分 ──────────────────────────────────────────────────────────────────

def _all_green_data():
    return {
        'revenue': {'yoy': [5.0, 12.0, 8.0], 'cum_yoy': [15.0], 'mom': [3.0]},
        'margins': {'gross_margin': [40.0, 42.0, 45.0]},
        'eps': {'eps': [1, 1, 1, 1, 2, 2, 2, 2], 'yoy': []},
        'valuation': {'per': 12.0, 'pe_percentile': 20.0},
        'chip': {'net20': 500, 'net60': 1200},
    }


def test_build_checklist_all_green_scores_100():
    items, score = build_checklist(_all_green_data())
    assert len(items) == 8
    assert all(i['status'] == 'green' for i in items)
    assert score == 100


def test_build_checklist_gray_excluded_from_denominator():
    data = _all_green_data()
    data['chip'] = None
    data['valuation'] = None
    items, score = build_checklist(data)
    grays = [i for i in items if i['status'] == 'gray']
    assert len(grays) == 2
    assert score == 100  # 剩下 6 條全綠


def test_build_checklist_all_missing_returns_none_score():
    items, score = build_checklist({})
    assert score is None
    assert all(i['status'] == 'gray' for i in items)


def test_build_checklist_mixed_score():
    data = _all_green_data()
    data['chip'] = {'net20': -200, 'net60': -100}      # red → 0
    data['valuation'] = {'per': 18.0, 'pe_percentile': 50.0}  # yellow → 0.5
    _, score = build_checklist(data)
    assert score == round((6 + 0.5) / 8 * 100)  # 81


# ── Gemini 輔助 ───────────────────────────────────────────────────────────────

def test_parse_gemini_json_strips_code_fence():
    text = '```json\n{"business": "測試", "risks": ["a"]}\n```'
    assert _parse_gemini_json(text)['business'] == '測試'


def test_parse_gemini_json_raises_without_json():
    with pytest.raises(ValueError):
        _parse_gemini_json('抱歉，我無法回答')


def test_quant_brief_includes_available_metrics():
    brief = _quant_brief(
        '2330', '台積電',
        revenue={'yoy': [5.0, 12.0, 8.0], 'cum_yoy': [15.0]},
        margins={'gross_margin': [50.0, 52.0]},
        eps={'eps': [8.0, 9.0]},
        valuation={'per': 20.0, 'pe_percentile': 45.0, 'pbr': 5.0},
        chip={'net20': 300},
        product_mix={'summary': '晶圓代工龍頭'},
    )
    assert brief['公司'] == '台積電（2330）'
    assert brief['月營收YoY近3月'] == [5.0, 12.0, 8.0]
    assert brief['法人20日淨買超(張)'] == 300
    assert brief['已知業務摘要'] == '晶圓代工龍頭'
