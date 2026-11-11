"""還原歷史日 docs 頁面中被事後重算的 as-of 欄位。

背景
----
build_docs.build_all() 曾每天重建全部 docs/YYYYMMDD.json，而 qualified／dualFilterExcluded
的 trend_analysis（KD、乖離、動能評級）、product_mix 與 signalFlips 讀的是
docs/stocks/{id}.json 的「最新」資料——例如 2023-03-08 頁面顯示的是 2026-11-10 的 MA60
與買進建議。180f50f1 起歷史日改為沿用舊值，但沿用的已是被覆寫後的值，故須由 git 歷史還原。

還原規則
--------
日期 D 的正確版本 = 「下一份報告（D 之後最近的日期）首次 commit」的前一版中的 docs/D.json，
也就是 D 當晚產出、尚未被隔天重算的那份。下列情況視為從來沒有當日的值，移除這些欄位，
不保留用事後資料算出的值：
  - 那個時間點 docs/D.json 尚不存在（事後補建的歷史，與下一日在同一個 commit 加入）
  - docs/D.json 首次加入的 commit 晚於 D 超過 MAX_LAG_DAYS 天（補建）
最新一日不動（當日值本來就正確）。只改這三類欄位，其餘照舊。

休市日重複報告（HOLIDAY_DUPES）另由 commit 移除；signalFlips 中指向這些日期的
signal_date 改回前一交易日（休市報告即前一日資料的複本，訊號相同）。

用法：python scripts/restore_docs_asof.py [--dry-run]
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))

AS_OF_ITEM_KEYS = ('trend_analysis', 'product_mix')
ITEM_LISTS = ('qualified', 'dualFilterExcluded')
MAX_LAG_DAYS = 4
# 以前一交易日資料產出的休市日報告（FinMind 該日無任何 K 棒），由同一批 commit 移除
HOLIDAY_DUPES = {'20260501': '20260430', '20260619': '20260618', '20261023': '20261022'}


def _git(*args) -> str:
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


def first_add_commit(path: str) -> str | None:
    out = _git('log', '--format=%H', '--diff-filter=A', '--', path).split()
    return out[-1] if out else None


def commit_date(sha: str) -> datetime:
    return datetime.fromtimestamp(int(_git('show', '-s', '--format=%ct', sha)), timezone.utc)


def show(rev: str, path: str) -> dict | None:
    r = subprocess.run(['git', 'show', f'{rev}:{path}'], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def as_of_version(date: str, next_date: str) -> dict | None:
    """D 當晚產出的 docs/D.json；沒有當日版本時回 None。"""
    doc = f'docs/{date}.json'
    created = first_add_commit(doc)
    if created is None:
        return None
    lag = (commit_date(created).date() - datetime.strptime(date, '%Y%m%d').date()).days
    if lag > MAX_LAG_DAYS:
        return None
    nxt = first_add_commit(f'daily_reports/{next_date}/summary.json')
    if nxt is None:
        return None
    return show(f'{nxt}^', doc)


def apply_as_of(cur: dict, old: dict | None) -> dict:
    """把 cur 的 as-of 欄位換成 old 的值；old 為 None 或缺該欄位時移除。"""
    for key in ITEM_LISTS:
        prev = {o.get('id'): o for o in ((old or {}).get(key) or [])}
        for it in cur.get(key) or []:
            p = prev.get(it.get('id')) or {}
            for k in AS_OF_ITEM_KEYS:
                if k in p:
                    it[k] = p[k]
                else:
                    it.pop(k, None)
    flips = (old or {}).get('signalFlips') or []
    for f in flips:
        if f.get('signal_date') in HOLIDAY_DUPES:
            f['signal_date'] = HOLIDAY_DUPES[f['signal_date']]
    cur['signalFlips'] = flips
    return cur


def main(dry_run: bool = False) -> int:
    import build_docs
    base = ROOT / 'daily_reports'
    # 「下一日」以 git 歷史中存在過的報告日期計（含休市日），才找得到正確的時間點
    dates = sorted({p.split('/')[1] for p in _git('log', '--format=', '--name-only',
                                                     '--diff-filter=A', '--',
                                                     'daily_reports/*/summary.json').split()
                    if p.split('/')[1].isdigit()})
    restored = stripped = unchanged = 0
    for date, nxt in zip(dates, dates[1:]):
        path = ROOT / 'docs' / f'{date}.json'
        if date in HOLIDAY_DUPES or not path.exists() or not (base / date).is_dir():
            continue
        raw = path.read_text(encoding='utf-8')
        old = as_of_version(date, nxt)
        new = json.dumps(build_docs._sanitize(apply_as_of(json.loads(raw), old)),
                         ensure_ascii=False, separators=(',', ':'))
        if new == raw:
            unchanged += 1
            continue
        if old is None:
            stripped += 1
        else:
            restored += 1
        if not dry_run:
            path.write_text(new, encoding='utf-8')
    print(f'還原 {restored} 份、移除無當日值 {stripped} 份、本來就正確 {unchanged} 份'
          f'{"（dry-run，未寫入）" if dry_run else ""}')
    return 0


if __name__ == '__main__':
    sys.exit(main(dry_run='--dry-run' in sys.argv[1:]))
