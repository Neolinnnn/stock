"""
修補歷史 summary.json 中的 cv_sharpe / cv_win_rate。

因原始回測邏輯有缺陷（零交易 fold 塞 0、資金不足買不到高價股等），
導致大量 cv_sharpe = 0。此腳本以修正後的 batch_scan 重跑所有股票，
再 patch 至每個 daily_reports/YYYYMMDD/summary.json。

用法：
    python scripts/patch_cv_history.py
"""
import json, sys, os, time
from pathlib import Path

# ── 載入 batch_scan（含修正後的 analyze_stock）────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
_main = os.path.join(os.path.dirname(__file__), 'batch_scan.py')
with open(_main, encoding='utf-8') as f:
    code = f.read()
exec(code.split("if __name__ == '__main__':")[0], globals())

# ── 收集所有股票 ──────────────────────────────────────────────────────────────
base = Path('daily_reports')
stock_map = {}   # id -> name

for d in base.iterdir():
    sj = d / 'summary.json'
    if not sj.exists():
        continue
    try:
        data = json.loads(sj.read_text(encoding='utf-8'))
        for sector_data in data.get('sectors', {}).values():
            for st in sector_data.get('stocks', []):
                stock_map[st['id']] = st['name']
    except Exception:
        pass

print(f'共 {len(stock_map)} 支股票，開始重算 CV 指標...\n')

# ── 逐支股票跑 analyze_stock ──────────────────────────────────────────────────
cv_cache = {}   # id -> {cv_sharpe, cv_win_rate}
errors   = []

for i, (sid, name) in enumerate(sorted(stock_map.items()), 1):
    print(f'  [{i:2d}/{len(stock_map)}] {sid} {name}', end=' ... ', flush=True)
    try:
        r = analyze_stock(sid, name)
        cv_cache[sid] = {
            'cv_sharpe':  round(r.get('cv_sharpe',  0) or 0, 2),
            'cv_win_rate': round(r.get('cv_win_rate', 0) or 0, 4),
        }
        print(f"夏普={cv_cache[sid]['cv_sharpe']:+.2f}  勝率={cv_cache[sid]['cv_win_rate']:.1%}")
    except Exception as e:
        cv_cache[sid] = {'cv_sharpe': 0, 'cv_win_rate': 0}
        errors.append(f'{sid} {name}: {e}')
        print(f'ERROR: {e}')
    time.sleep(0.3)

# ── Patch 所有 summary.json ───────────────────────────────────────────────────
patched = 0
for d in sorted(base.iterdir()):
    sj = d / 'summary.json'
    if not sj.exists():
        continue
    try:
        data = json.loads(sj.read_text(encoding='utf-8'))
        changed = False
        for sector_data in data.get('sectors', {}).values():
            for st in sector_data.get('stocks', []):
                sid = st['id']
                if sid in cv_cache:
                    st['cv_sharpe']  = cv_cache[sid]['cv_sharpe']
                    st['cv_win_rate'] = cv_cache[sid]['cv_win_rate']
                    changed = True
        if changed:
            sj.write_text(
                json.dumps(data, ensure_ascii=False, separators=(',', ':')),
                encoding='utf-8',
            )
            patched += 1
    except Exception as e:
        print(f'  patch {d.name} 失敗: {e}')

print(f'\n✅ 已更新 {patched} 個 summary.json')

if errors:
    print(f'\n⚠ 以下股票 analyze_stock 失敗（cv 保留 0）：')
    for e in errors:
        print(f'  {e}')

# ── 重建 docs/ ────────────────────────────────────────────────────────────────
print('\n重建 docs/ ...')
import importlib.util, importlib
spec = importlib.util.spec_from_file_location(
    'build_docs',
    os.path.join(os.path.dirname(__file__), 'build_docs.py'),
)
bd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bd)
bd.build_all()
print('完成。')
