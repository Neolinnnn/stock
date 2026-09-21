"""集保大戶持股資料的驗證工具。

用途：確認集保（TDCC）open data 的端點、欄位與分級對照仍如預期，以及
tdcc_holders 的解析與快照累積結果合理。集保端點免認證，任何環境都能跑。

用法：
    python scripts/verify_holders.py 2330
    python scripts/verify_holders.py 2330 --dump holders_raw.csv
    python scripts/verify_holders.py 2330 --save   # 順便存一期快照

檢查項目：
    1. 端點可連線、回應是 CSV 而非錯誤頁
    2. 欄位名稱符合預期（集保改欄位名會讓解析靜默失效）
    3. 分級 13/14/15 是否都在，且量級符合「級數越大持股越多」
    4. 指定個股的三個大戶級距解析結果是否合理
    5. 既有快照能否組出時間序列
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

LABELS = {'lv1000_up': '1000 張以上',
          'lv800_1000': '800~1000 張',
          'lv600_800': '600~800 張'}
EXPECTED_COLS = ['資料日期', '證券代號', '持股分級', '人數', '股數', '占集保庫存數比例%']


def _pad(text: str, width: int) -> str:
    """依顯示寬度補空白（中文字佔兩格），讓終端輸出的數字欄對齊。"""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)
    return text + ' ' * max(0, width - w)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('stock_id', help='股票代碼，例如 2330')
    ap.add_argument('--weeks', type=int, default=12, help='時間序列取最近幾期（預設 12）')
    ap.add_argument('--dump', metavar='CSV', help='將原始回應存成 CSV 以供比對')
    ap.add_argument('--save', action='store_true', help='將這一期存成快照')
    args = ap.parse_args()

    import csv
    import io
    import urllib.request
    import tdcc_holders

    sid = args.stock_id.strip()
    print(f'── 驗證 {sid} 的集保大戶持股資料 ' + '─' * 28)
    print(f'   來源：{tdcc_holders.TDCC_URL}')

    # ── 1. 端點 ──────────────────────────────────────────────────────────
    print('\n[1] 連線與回應格式：')
    try:
        with urllib.request.urlopen(tdcc_holders.TDCC_URL, timeout=120) as resp:
            raw = resp.read().decode('utf-8-sig', errors='replace')
    except Exception as e:
        print(f'  ❌ 連線失敗：{e}')
        return 1
    print(f'  ✅ 取得 {len(raw) / 1024 / 1024:.2f} MB')
    if raw.lstrip().startswith('<'):
        print('  ❌ 回應是 HTML 不是 CSV（端點可能改版，或被中間層擋下）')
        print('     開頭：', raw[:120].replace('\n', ' '))
        return 1
    if args.dump:
        Path(args.dump).write_text(raw, encoding='utf-8')
        print(f'  ✅ 原始回應已存至 {args.dump}')

    # ── 2. 欄位 ──────────────────────────────────────────────────────────
    print('\n[2] 欄位名稱：')
    reader = csv.DictReader(io.StringIO(raw))
    cols = reader.fieldnames or []
    bad = 0
    for col in EXPECTED_COLS:
        ok = col in cols
        bad += 0 if ok else 1
        print(f'  {"✅" if ok else "❌"} {col}' + ('' if ok else '  ← 解析需要此欄'))
    if bad:
        print('     實際欄位：', cols)
        return 1

    # ── 3. 分級 ──────────────────────────────────────────────────────────
    print('\n[3] 持股分級 13/14/15（600~800 張 / 800~1000 張 / 1000 張以上）：')
    rows = [r for r in reader if (r.get('證券代號') or '').strip() == sid]
    if not rows:
        print(f'  ❌ 找不到 {sid}，該檔可能未上市櫃或代號有誤')
        return 1
    by_level = {(r.get('持股分級') or '').strip(): r for r in rows}
    print(f'  {sid} 共 {len(rows)} 個分級，資料日期 {rows[0].get("資料日期")}')
    shares = []
    for lv in ('13', '14', '15'):
        r = by_level.get(lv)
        if not r:
            print(f'  ❌ 缺少分級 {lv}')
            bad += 1
            continue
        n = int(r['人數'])
        sh = int(r['股數'])
        shares.append(sh / max(n, 1))
        print(f'  ✅ 分級 {lv}：{n:>9,} 人　{sh:>18,} 股　{r["占集保庫存數比例%"]:>7}%')
    # 級數越大代表持股越多，人均持股必須遞增；不成立代表分級對照錯了
    if len(shares) == 3 and not (shares[0] < shares[1] < shares[2]):
        print('  ❌ 人均持股未隨分級遞增，LEVELS 對照可能已失效')
        bad += 1
    elif len(shares) == 3:
        print('  ✅ 人均持股隨分級遞增，分級對照合理')

    # ── 4. 解析 ──────────────────────────────────────────────────────────
    print('\n[4] tdcc_holders.fetch() 解析結果：')
    date, levels = tdcc_holders.fetch()
    row = levels.get(sid)
    if not row:
        print(f'  ❌ 解析後找不到 {sid}')
        return 1
    print(f'  ✅ 資料日期 {date}，全市場 {len(levels):,} 檔')
    total = 0.0
    for key, label in LABELS.items():
        v = row.get(key)
        if v is None:
            print(f'      {_pad(label, 14)}無值 ⚠')
            bad += 1
            continue
        total += v
        print(f'      {_pad(label, 14)}{v:7.3f}%')
    print(f'      {_pad("600 張以上合計", 14)}{total:7.3f}%')
    if not 0 <= total <= 100:
        print('  ❌ 合計超出 0~100%')
        bad += 1

    if args.save:
        path, stored = tdcc_holders.save_snapshot(date, levels, [sid])
        print(f'  ✅ 快照已存至 {path}（{stored} 檔）')

    # ── 5. 時間序列 ──────────────────────────────────────────────────────
    print('\n[5] 由既有快照組出的時間序列：')
    series = tdcc_holders.load_series(sid, weeks=args.weeks)
    if not series:
        print('  ⚠ 目前沒有任何快照，時間序列為空（首次接上時的正常狀態）')
    else:
        d = series['dates']
        print(f'  ✅ {len(d)} 期：{d[0]} ~ {d[-1]}')
        for key, label in LABELS.items():
            vals = [v for v in series['levels'].get(key, []) if v is not None]
            if not vals:
                print(f'      {_pad(label, 14)}無有效值 ⚠')
                continue
            print(f'      {_pad(label, 14)}最新 {vals[-1]:7.3f}%   '
                  f'期間變化 {vals[-1] - vals[0]:+7.3f} pp')
        if len(d) < 2:
            print('  ⚠ 只有一期，卡 18 的折線圖要累積到兩期以上才畫得出變化')

    print('\n' + ('✅ 全部通過' if not bad else f'❌ {bad} 項未通過'))
    return 1 if bad else 0


if __name__ == '__main__':
    raise SystemExit(main())
