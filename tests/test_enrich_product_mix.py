"""產銷組合：出處校正與舊格式判定測試。"""
import json
from datetime import date

import enrich_product_mix as epm


# ── _normalize_source ─────────────────────────────────────────────────────────

def test_disclosed_source_kept():
    pm = {'source_type': 'prospectus', 'source': '超穎電子港股招股書'}
    epm._normalize_source(pm)
    assert pm['source_type'] == 'prospectus'


def test_unknown_source_type_downgraded():
    pm = {'source_type': 'guess', 'source': '某文件'}
    epm._normalize_source(pm)
    assert pm['source_type'] == 'estimate'


def test_missing_source_type_downgraded():
    pm = {'source': '某文件'}
    epm._normalize_source(pm)
    assert pm['source_type'] == 'estimate'


def test_disclosed_without_citation_downgraded():
    """自稱年報卻沒附出處：降為估計，避免估計值冒充揭露值。"""
    for source in ('', '   ', None):
        pm = {'source_type': 'annual_report', 'source': source}
        epm._normalize_source(pm)
        assert pm['source_type'] == 'estimate'


def test_estimate_without_citation_stays_estimate():
    pm = {'source_type': 'estimate'}
    epm._normalize_source(pm)
    assert pm['source_type'] == 'estimate'


# ── _is_stale ─────────────────────────────────────────────────────────────────

def test_legacy_format_is_stale_even_if_fresh():
    pm = {'product_lines': [], 'updated_at': date.today().isoformat()}
    assert epm._is_stale(pm)


def test_new_format_fresh_not_stale():
    pm = {'source_type': 'estimate', 'updated_at': date.today().isoformat()}
    assert not epm._is_stale(pm)


def test_new_format_old_date_is_stale():
    pm = {'source_type': 'prospectus', 'updated_at': '2020-01-01'}
    assert epm._is_stale(pm)


# ── get_sids_missing_pm ───────────────────────────────────────────────────────

def test_missing_includes_legacy_and_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(epm, 'FUND_DIR', tmp_path)
    files = {
        '1111': {'name': '無資料'},
        '2222': {'name': '舊格式', 'product_mix': {'product_lines': []}},
        '3333': {'name': '新格式', 'product_mix': {'source_type': 'estimate'}},
    }
    for sid, d in files.items():
        (tmp_path / f'{sid}.json').write_text(json.dumps(d, ensure_ascii=False), encoding='utf-8')

    assert epm.get_sids_missing_pm() == [('1111', '無資料'), ('2222', '舊格式')]


# ── enrich_one：揭露值不被估計值覆寫 ─────────────────────────────────────────

class _FakeWriter:
    def __init__(self, reply: dict):
        self.reply = json.dumps(reply, ensure_ascii=False)

    def generate(self, **kwargs):
        return self.reply


_LINES = [{'name': 'A', 'share_pct': 60}, {'name': 'B', 'share_pct': 40}]


def _write_fund(tmp_path, pm):
    (tmp_path / '9999.json').write_text(
        json.dumps({'name': '測試', 'product_mix': pm}, ensure_ascii=False), encoding='utf-8')


def _read_pm(tmp_path):
    return json.loads((tmp_path / '9999.json').read_text(encoding='utf-8'))['product_mix']


def test_estimate_does_not_overwrite_disclosed(tmp_path, monkeypatch):
    monkeypatch.setattr(epm, 'FUND_DIR', tmp_path)
    _write_fund(tmp_path, {'product_lines': _LINES, 'source_type': 'prospectus',
                           'source': '招股書', 'updated_at': '2020-01-01'})
    writer = _FakeWriter({'product_lines': _LINES, 'source_type': 'estimate'})
    assert epm.enrich_one('9999', '測試', writer, force=True) is False
    assert _read_pm(tmp_path)['source_type'] == 'prospectus'


def test_disclosed_overwrites_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(epm, 'FUND_DIR', tmp_path)
    _write_fund(tmp_path, {'product_lines': _LINES, 'updated_at': '2020-01-01'})
    writer = _FakeWriter({'product_lines': _LINES, 'source_type': 'annual_report',
                          'source': '2025 年報'})
    assert epm.enrich_one('9999', '測試', writer) is True
    assert _read_pm(tmp_path)['source_type'] == 'annual_report'


def test_estimate_overwrites_legacy(tmp_path, monkeypatch):
    """舊格式本來就是未標出處的估計，換成有標記的估計值仍是改善。"""
    monkeypatch.setattr(epm, 'FUND_DIR', tmp_path)
    _write_fund(tmp_path, {'product_lines': _LINES, 'updated_at': '2020-01-01'})
    writer = _FakeWriter({'product_lines': _LINES})
    assert epm.enrich_one('9999', '測試', writer) is True
    assert _read_pm(tmp_path)['source_type'] == 'estimate'
