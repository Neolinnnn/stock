"""集保大戶持股資料的離線驗證工具。

用途：在能連線 FinMind 的環境跑一次，確認 build_docs._holders_aggregate()
對真實回應的解析正確。開發環境無法連線 FinMind 時，此腳本是唯一的驗證途徑。

用法：
    python scripts/verify_holders.py 2330
    python scripts/verify_holders.py 2330 --dump holders_raw.csv

檢查項目：
    1. DataLoader 是否存在 holding 相關方法（方法名猜錯會整個功能靜默失效）
    2. 回應欄位是否符合 _holders_aggregate 的預期
    3. 級距標籤的實際字串格式，以及三個大戶級距是否都抓得到
    4. 解析結果是否合理（比例總和、週變化量級）
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))


def _pad(text: str, width: int) -> str:
    """依顯示寬度補空白（中文字佔兩格），讓終端輸出的數字欄對齊。"""
    import unicodedata
    w = sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in text)
    return text + ' ' * max(0, width - w)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('stock_id', help='股票代碼，例如 2330')
    ap.add_argument('--weeks', type=int, default=12, help='取最近幾期（預設 12）')
    ap.add_argument('--dump', metavar='CSV', help='將原始回應存成 CSV 以供比對')
    args = ap.parse_args()

    from datafeed import make_dataloader
    import build_docs

    print(f'── 驗證 {args.stock_id} 的集保大戶持股資料 ' + '─' * 28)

    # ── 1. 方法是否存在 ──────────────────────────────────────────────────
    dl = make_dataloader()
    holding_methods = [m for m in dir(dl) if 'holding' in m.lower()]
    print('\n[1] DataLoader 的 holding 相關方法：')
    for m in holding_methods:
        print(f'      {m}')
    target = 'taiwan_stock_holding_shares_per'
    if target not in holding_methods:
        print(f'\n  ❌ 找不到 {target}')
        print('     build_docs.py 的取數方法名需改為上列其中之一。')
        return 1
    print(f'  ✅ {target} 存在')

    # ── 2. 實際取數 ──────────────────────────────────────────────────────
    from datetime import date, timedelta
    end = date.today().isoformat()
    start = (date.today() - timedelta(days=args.weeks * 7 + 21)).isoformat()
    print(f'\n[2] 取數 {start} ~ {end}')
    try:
        raw = getattr(dl, target)(stock_id=args.stock_id, start_date=start, end_date=end)
    except Exception as e:
        print(f'  ❌ 呼叫失敗：{e}')
        return 1
    if raw is None or raw.empty:
        print('  ❌ 回應為空。可能是免費方案不含此 dataset，或該期間無資料。')
        return 1
    print(f'  ✅ 取得 {len(raw)} 列')
    print(f'     欄位：{list(raw.columns)}')

    if args.dump:
        raw.to_csv(args.dump, index=False, encoding='utf-8-sig')
        print(f'     原始回應已存至 {args.dump}')

    # ── 3. 欄位與級距標籤 ────────────────────────────────────────────────
    print('\n[3] 欄位檢查：')
    for col in ('HoldingSharesLevel', 'percent'):
        ok = col in raw.columns
        print(f'  {"✅" if ok else "❌"} {col}' + ('' if ok else '  ← _holders_aggregate 需要此欄'))
    if 'HoldingSharesLevel' not in raw.columns or 'percent' not in raw.columns:
        print('\n  解析會回傳空 dict。請把上面的實際欄位名告知，以便調整。')
        return 1

    print('\n[4] 級距標籤實際格式與解析結果：')
    levels = list(dict.fromkeys(raw['HoldingSharesLevel'].astype(str)))
    hit = 0
    for lv in levels:
        lower = build_docs._level_lower_bound(lv)
        key = build_docs._HOLDER_LEVELS.get(lower)
        mark = '←  ' + key if key else ''
        if key:
            hit += 1
        print(f'      {_pad(repr(lv), 34)}下界={str(lower):<9}{mark}')
    print(f'\n  {"✅" if hit == 3 else "❌"} 三個大戶級距抓到 {hit}/3')
    if hit != 3:
        print('     集保級距切分可能與預期不同，請把上表告知以便調整 _HOLDER_LEVELS。')

    # ── 5. 彙整結果 ──────────────────────────────────────────────────────
    print('\n[5] _holders_aggregate() 輸出：')
    out = build_docs._holders_aggregate(raw, weeks=args.weeks)
    if not out:
        print('  ❌ 回傳空 dict')
        return 1
    print(f'  ✅ {len(out["dates"])} 期：{out["dates"][0]} ~ {out["dates"][-1]}')
    labels = {'lv1000_up': '1000 張以上', 'lv800_1000': '800~1000 張', 'lv600_800': '600~800 張'}
    for key, label in labels.items():
        vals = out['levels'].get(key, [])
        valid = [v for v in vals if v is not None]
        if not valid:
            print(f'      {_pad(label, 14)}無有效值 ⚠')
            continue
        diff = valid[-1] - valid[0]
        print(f'      {_pad(label, 14)}最新 {valid[-1]:7.3f}%   '
              f'期間變化 {diff:+7.3f} pp   缺值 {len(vals) - len(valid)} 期')

    # ── 6. 合理性 ────────────────────────────────────────────────────────
    print('\n[6] 合理性檢查：')
    last = [out['levels'][k][-1] for k in labels if out['levels'].get(k)]
    last = [v for v in last if v is not None]
    total = sum(last)
    checks = [
        ('三級距合計介於 0~100%', 0 <= total <= 100, f'實得 {total:.2f}%'),
        ('每期都有值（無整欄缺漏）',
         all(any(v is not None for v in out['levels'][k]) for k in labels), ''),
        ('期數與請求相符', len(out['dates']) <= args.weeks, f'{len(out["dates"])} 期'),
    ]
    bad = 0
    for name, ok, detail in checks:
        print(f'  {"✅" if ok else "❌"} {name}' + (f'  {detail}' if detail else ''))
        bad += not ok

    print('\n' + '─' * 60)
    if hit == 3 and not bad:
        print('全部通過。可直接跑 build_docs.py，holders 欄位會正常產出。')
        return 0
    print('有項目未通過，請將上方輸出回報以便調整解析邏輯。')
    return 1


if __name__ == '__main__':
    sys.exit(main())
