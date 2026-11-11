"""分點來源解析與來源切換（不打網路）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from indicators import broker  # noqa: E402

FUBON_HTML = """<table><tr><td>買超</td><td>賣超</td></tr>
<TR><TD>買超券商</TD><TD>買進</TD><TD>賣出</TD><TD>買超</TD><TD>佔成交比重</TD>
<TD>賣超券商</TD><TD>買進</TD><TD>賣出</TD><TD>賣超</TD><TD>佔成交比重</TD></TR>
<TR><TD><a href="x">富邦證券</a></TD><TD>2,338</TD><TD>385</TD><TD>1,953</TD><TD>5.36%</TD>
<TD><a href="y">凱基-台北</a></TD><TD>1,035</TD><TD>6,150</TD><TD>5,115</TD><TD>14.04%</TD></tr>
<TR><TD>摩根大通</TD><TD>3,978</TD><TD>2,118</TD><TD>1,860</TD><TD>5.11%</TD>
<TD>美商高盛</TD><TD>2,264</TD><TD>3,195</TD><TD>931</TD><TD>2.56%</TD></tr>
</table>買超或賣超成本，為上述家數合計"""


def test_parse_fubon():
    buyers, sellers = broker._parse_fubon(FUBON_HTML)
    assert [b['name'] for b in buyers] == ['富邦證券', '摩根大通']
    assert buyers[0] == {'name': '富邦證券', 'buy': 2338, 'sell': 385, 'net': 1953, 'avg_price': None}
    assert [s['net'] for s in sellers] == [-5115, -931]   # 賣超轉為負數，由大到小賣


def test_falls_back_to_histock_and_reports_all_errors(monkeypatch):
    broker._CACHE.clear()
    calls = []

    def fake_get(url, timeout=8, encoding='utf-8'):
        calls.append(url)
        raise OSError('HTTP Error 403: Forbidden')
    monkeypatch.setattr(broker, '_http_get', fake_get)
    r = broker.fetch_broker_top15('9999')
    assert r['source'] is None and 'fubon fetch_failed' in r['error'] and 'histock fetch_failed' in r['error']
    assert 'fubon' in calls[0] and 'histock' in calls[1]


def test_fubon_primary(monkeypatch):
    broker._CACHE.clear()
    monkeypatch.setattr(broker, '_http_get', lambda url, timeout=8, encoding='utf-8': FUBON_HTML)
    monkeypatch.setattr(broker.time, 'sleep', lambda s: None)
    r = broker.fetch_broker_top15('2330')
    assert r['source'] == 'fubon' and r['net_concentration'] == 1953 + 1860 - 5115 - 931
    assert broker.main_force_score(r)['score'] is not None
