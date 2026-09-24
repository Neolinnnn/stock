"""程式碼中寫死的「代號→名稱」必須與證交所／櫃買官方簡稱一致。

曾發生：3058 標成聯陽（實為立德）、2243 標成怡利電（實為宏旭-KY）、
3205 標成十銓（實為佰研）——掃描抓的是另一家公司的股價，網站卻顯示成目標公司。
名稱對不上代號，就代表至少有一邊寫錯。

官方名單：data/radar_cache/history.json（rotation_radar --collect 每日由
TWSE/TPEx openapi 更新，含上市上櫃全部個股）。只用標準庫，可直接 python 執行。
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAIR_RE = [
    re.compile(r"""['"](\d{4,6}[A-Z]?)['"]\s*:\s*['"]([^'"\n]{1,15})['"]"""),     # '2330': '台積電'
    re.compile(r"""\(\s*['"](\d{4,6}[A-Z]?)['"]\s*,\s*['"]([^'"\n]{1,15})['"]"""),  # ("2330", "台積電")
]
SKIP_DIRS = {'tests', 'test_examples', '.git', '.claude', 'node_modules'}


def official_names() -> dict:
    stocks = json.loads((ROOT / 'data' / 'radar_cache' / 'history.json')
                        .read_text(encoding='utf-8'))['stocks']
    # 官方簡稱的 * 為交易所註記（如「國巨*」），不屬名稱本身
    return {sid: v['name'].replace('*', '') for sid, v in stocks.items()}


def code_pairs():
    """(檔案:行號, 代號, 名稱)：掃描所有 .py 中的代號→名稱對照。"""
    for p in sorted(ROOT.rglob('*.py')):
        if SKIP_DIRS & set(p.relative_to(ROOT).parts):
            continue
        for i, line in enumerate(p.read_text(encoding='utf-8').splitlines(), 1):
            for rx in PAIR_RE:
                for m in rx.finditer(line):
                    if re.search(r'[一-鿿]', m.group(2)) or '-KY' in m.group(2):
                        yield f'{p.relative_to(ROOT)}:{i}', m.group(1), m.group(2)


def test_code_names_match_official():
    off = official_names()
    errors = []
    for loc, sid, name in code_pairs():
        if sid not in off:
            errors.append(f'{loc}  {sid} {name}：官方名單查無此代號（下市或代號錯誤）')
        elif off[sid] != name.replace('*', ''):
            errors.append(f'{loc}  {sid} 寫成「{name}」，官方為「{off[sid]}」')
    assert not errors, '代號與名稱不符：\n' + '\n'.join(errors)


def test_scanner_finds_pairs():
    """防止正則失效而空轉通過：至少要掃到主掃描的族群名單。"""
    locs = {loc.split(':')[0] for loc, _, _ in code_pairs()}
    assert str(Path('scripts/daily_scan.py')) in locs


if __name__ == '__main__':
    test_scanner_finds_pairs()
    test_code_names_match_official()
    print('OK：程式碼中的代號與名稱皆與官方一致')
