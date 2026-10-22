"""restore_cv_asof：只撤銷前視偏差 commit 的改寫，當日重跑與事後合法更正保留。"""
import json
import subprocess

import restore_cv_asof as r

ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't',
       'GIT_COMMITTER_EMAIL': 't@t', 'PATH': '/usr/bin:/bin'}


def _git(repo, *args, date=None):
    env = dict(ENV, **({'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date} if date else {}))
    return subprocess.run(['git', *args], cwd=repo, check=True, capture_output=True, text=True, env=env).stdout


def _commit(repo, msg, date):
    _git(repo, 'add', '-A')
    _git(repo, 'commit', '-qm', msg, date=date)
    return _git(repo, 'rev-parse', 'HEAD').strip()


def _write(repo, data, compact=False):
    p = repo / 'daily_reports' / '20260501' / 'summary.json'
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')) if compact
                 else json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return p


def _summary(tsmc_cv, tsmc_sig, mem_cv, qualified, name='台積電', chip=1):
    return {'sectors': {
        '晶圓': {'stocks': [{'id': '2330', 'name': name, 'cv_sharpe': tsmc_cv, 'cv_win_rate': 0.5,
                             'signal': tsmc_sig, 'chip': chip}]},
        '記憶體': {'stocks': [{'id': '2408', 'name': '南亞科', 'cv_sharpe': mem_cv, 'cv_win_rate': 0.5,
                               'signal': 'BUY'}]}},
        'qualified': qualified}


def test_reverts_only_lookahead_changes(tmp_path, monkeypatch, capsys):
    q = [{'sector': '晶圓', 'id': '2330', 'name': '舊名'}, {'sector': '記憶體', 'id': '2330', 'name': '舊名'}]
    _git(tmp_path, 'init', '-q')
    _write(tmp_path, _summary(0.2, 'HOLD', 0.1, []))
    _commit(tmp_path, 'scan', '2026-05-01T09:00:00')
    _write(tmp_path, _summary(0.4, 'BUY', 0.1, q))                       # 當日重跑：最終當日產出
    _commit(tmp_path, 'rerun', '2026-05-01T20:00:00')
    # 前視偏差改寫：CV 回填、BUY 降為 HOLD、清單少一筆；記憶體的 CV 也被覆寫
    _write(tmp_path, _summary(-0.2, 'HOLD', 0.9, [q[0]]), compact=True)
    lookahead = _commit(tmp_path, 'patch', '2026-05-10T09:00:00')
    # 之後的合法更正：股名、籌碼；記憶體 CV 又被當日重跑類的改動換掉（不應再還原）
    _write(tmp_path, _summary(-0.2, 'HOLD', 0.7, [{**q[0], 'name': '台積電'}], name='台積電(更正)', chip=2),
           compact=True)
    _commit(tmp_path, 'fix names', '2026-09-24T09:00:00')

    monkeypatch.setattr(r, 'ROOT', tmp_path)
    files, _ = r.restore(commits=(lookahead,))
    p = tmp_path / 'daily_reports' / '20260501' / 'summary.json'
    text = p.read_text(encoding='utf-8')
    got = json.loads(text)
    tsmc, mem = got['sectors']['晶圓']['stocks'][0], got['sectors']['記憶體']['stocks'][0]
    assert files == 1
    assert (tsmc['cv_sharpe'], tsmc['signal']) == (0.4, 'BUY')            # 還原為改寫前（當日重跑版）
    assert (tsmc['name'], tsmc['chip']) == ('台積電(更正)', 2)             # 事後合法更正保留
    assert mem['cv_sharpe'] == 0.7                                          # 改寫後又被改過：不動
    # 清單成員還原；同一檔兩個族群不合併；已存在條目沿用目前版本（保留股名更正）
    assert [(x['sector'], x['name']) for x in got['qualified']] == [('晶圓', '台積電'), ('記憶體', '舊名')]
    assert '\n' not in text                                                 # 保留原單行格式

    capsys.readouterr()
    assert r.restore(commits=(lookahead,)) == (0, 0)                        # 再跑一次不應再變動
