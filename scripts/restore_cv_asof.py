# -*- coding: utf-8 -*-
"""
撤銷歷史報告中的前視偏差改寫（一次性修正）
==========================================
daily_reports/*/summary.json 是回測的訊號來源，內容必須是當日掃描產出。下列 commit 用「事後」
資料改寫了既有報告，回測因此讀到當天不可能知道的資訊：

  a5f7aaf34（2026-05-10）patch_cv_history.py 以執行當下的資料重算各股 cv_sharpe / cv_win_rate，
                          覆寫進當時全部 262 份歷史報告
  adc7426d6（2026-05-11）依覆寫後的 CV 把歷史 BUY 降為 HOLD（216 份報告）

2026-10 對照：以現行規則重建的行動清單在覆寫期勝率 75%，覆寫後的即時期僅 47%，與存檔清單一致。

做法：對這兩個 commit 動過的每份報告，比對「改寫前」（第一個改寫 commit 的父版本）、「改寫後」
（最後一個改寫 commit 的版本）與目前版本——只有「目前值仍等於改寫後的值」的欄位才還原為改寫前；
之後被合法改過的（當日重跑、股名更正等）一律保留。還原範圍：
  - sectors.*.stocks 的 cv_sharpe / cv_win_rate / signal（依族群＋代號比對）
  - qualified 行動清單的成員（成員仍等於改寫後時才還原；股名等欄位沿用目前條目）
其餘欄位與檔案格式（單行或 indent=2）不動。補建報告（2023-02～2026-04）的 CV 本來就是以截至
當日的資料計算（backfill_history.analyze_stock_on_date），只是舊版演算法常算出 0，屬當時規則，
不是前視偏差，無須處理。

需完整 git 歷史（淺層 clone 先 git fetch --unshallow）。可重複執行：已還原者不再變動。
用法：python scripts/restore_cv_asof.py [--dry-run]
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent

LOOKAHEAD_COMMITS = (
    'a5f7aaf34145f735ae46fe3c9924e93f185cfbab',   # CV 事後回填
    'adc7426d6084189267653ec667e3bb34974af586',   # 依回填 CV 將歷史 BUY 降為 HOLD
)
FIELDS = ('cv_sharpe', 'cv_win_rate', 'signal')


def git(*args) -> str:
    return subprocess.run(['git', *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout


def show(rev: str, rel: str) -> dict:
    return json.loads(git('show', f'{rev}:{rel}'))


def dump_like(data: dict, original: str) -> str:
    """依原檔格式序列化：單行（patch_cv_history 寫的）或 indent=2（daily_scan 寫的）。"""
    if '\n' in original.strip():
        return json.dumps(data, ensure_ascii=False, indent=2) + ('\n' if original.endswith('\n') else '')
    return json.dumps(data, ensure_ascii=False, separators=(',', ':'))


def field_map(summary: dict) -> dict:
    """(族群, 股票代號) -> 還原欄位值"""
    return {(sec, st['id']): tuple(st.get(f) for f in FIELDS)
            for sec, data in summary.get('sectors', {}).items() for st in data.get('stocks', [])}


def qkey(q: dict) -> tuple:
    """行動清單條目鍵：同一檔可能同時列在兩個族群"""
    return q.get('sector'), q.get('id')


def touched_files(commits) -> dict:
    """報告路徑 -> 依時間排序的改寫 commit 清單"""
    out = {}
    for c in sorted(commits, key=lambda c: int(git('show', '-s', '--format=%ct', c))):
        for rel in git('show', '--name-only', '--format=', c).split():
            if rel.startswith('daily_reports/') and rel.endswith('/summary.json'):
                out.setdefault(rel, []).append(c)
    return out


def restore(commits=LOOKAHEAD_COMMITS, dry_run=False) -> tuple[int, int]:
    total_files = total_values = 0
    for rel, cs in sorted(touched_files(commits).items()):
        sf = ROOT / rel
        if not sf.exists():
            continue
        before, after = show(f'{cs[0]}^', rel), show(cs[-1], rel)
        text = sf.read_text(encoding='utf-8')
        data = json.loads(text)
        if dump_like(data, text) != text:
            print(f'  ⚠️ {rel} 無法逐字重現原格式，略過（避免非預期的 diff）')
            continue
        b, a = field_map(before), field_map(after)
        changed = 0
        for sec, sec_data in data.get('sectors', {}).items():
            for st in sec_data.get('stocks', []):
                key = (sec, st['id'])
                if key not in b or key not in a:
                    continue
                for i, f in enumerate(FIELDS):
                    # 只還原「被改寫、且之後沒再被改過」的欄位
                    if a[key][i] != b[key][i] and st.get(f) == a[key][i]:
                        st[f] = b[key][i]
                        changed += 1
        cur_q = data.get('qualified', [])
        qb, qa = before.get('qualified', []), after.get('qualified', [])
        if [qkey(q) for q in qa] != [qkey(q) for q in qb] and [qkey(q) for q in cur_q] == [qkey(q) for q in qa]:
            by_key = {qkey(q): q for q in cur_q}
            data['qualified'] = [by_key.get(qkey(q), q) for q in qb]
            changed += 1
        if changed:
            total_files += 1
            total_values += changed
            print(f'  {rel.split("/")[1]}：還原 {changed} 處')
            if not dry_run:
                sf.write_text(dump_like(data, text), encoding='utf-8')
    return total_files, total_values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true', help='只列出會改動的報告，不寫檔')
    args = ap.parse_args()
    if git('rev-parse', '--is-shallow-repository').strip() == 'true':
        sys.exit('需要完整 git 歷史：請先 git fetch --unshallow')
    files, values = restore(dry_run=args.dry_run)
    print(f'{"（dry-run）" if args.dry_run else ""}共 {files} 份報告、{values} 處還原為改寫前的當日產出')


if __name__ == '__main__':
    main()
