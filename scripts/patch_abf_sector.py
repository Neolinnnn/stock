"""
把 ABF載板 族群資料補入 2026 年所有現有 daily_reports/YYYYMMDD/summary.json。

做法：
  1. 對已有相關族群（小摩AI供應鏈、銅箔/CCL）的日期：直接抽取股票資料
  2. 已有 ABF載板 的日期：跳過
  3. 資料不足的日期：跳過（需 FinMind API 才能補完）

用法：
    python scripts/patch_abf_sector.py
    python scripts/patch_abf_sector.py --force   # 重寫已有 ABF載板 的日期
"""
import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ROOT / 'daily_reports'

ABF_STOCKS = {
    '3037': '欣興',
    '4958': '臻鼎-KY',
    '8046': '南電',
    '3189': '景碩',
}

# 來源族群優先順序（越前面優先）
SOURCE_SECTORS = ['小摩AI供應鏈', 'ABF載板', '銅箔/CCL', 'PCB 載板', 'PCB 硬板',
                  'HBM / 記憶體', 'CoWoS / 先進封裝']


def _extract_abf_stocks(summary: dict) -> list:
    """從已有的族群資料中抽取 ABF 4 檔的個股記錄。"""
    found: dict[str, dict] = {}
    for sector in SOURCE_SECTORS:
        data = summary.get('sectors', {}).get(sector, {})
        for st in data.get('stocks', []):
            sid = st.get('id')
            if sid in ABF_STOCKS and sid not in found:
                entry = dict(st)
                entry['name'] = ABF_STOCKS[sid]  # 統一用正式名稱
                found[sid] = entry
    return list(found.values())


def _build_abf_sector(stocks: list) -> dict:
    """根據個股清單計算族群摘要統計。"""
    ok = [s for s in stocks if s.get('rsi') is not None]
    rets = [s['ret_20d'] for s in ok if s.get('ret_20d') is not None]
    rsis = [s['rsi'] for s in ok if s.get('rsi') is not None]
    sharpes = [s.get('cv_sharpe', 0) or 0 for s in ok]

    avg_ret = (sum(rets) / len(rets)) if rets else 0
    avg_rsi = (sum(rsis) / len(rsis)) if rsis else 50.0
    avg_sharpe = (sum(sharpes) / len(sharpes)) if sharpes else 0.0

    buy_count = sum(1 for s in ok if s.get('signal') == 'BUY')
    hot_count = sum(1 for s in ok if (s.get('rsi') or 0) > 70)
    qualified_count = sum(
        1 for s in ok
        if (s.get('cv_sharpe') or 0) >= 0.3 and (s.get('cv_win_rate') or 0) >= 0.4
    )

    return {
        'avg_ret_20d': round(avg_ret, 2),
        'avg_rsi': round(avg_rsi, 1),
        'avg_sharpe': round(avg_sharpe, 2),
        'hot_count': hot_count,
        'buy_count': buy_count,
        'qualified_count': qualified_count,
        'stocks': stocks,
    }


def patch_one(report_dir: Path, force: bool = False) -> str:
    """回傳狀態：'patched' / 'skipped_exists' / 'skipped_no_data'"""
    summary_path = report_dir / 'summary.json'
    summary = json.loads(summary_path.read_text(encoding='utf-8'))

    if 'ABF載板' in summary.get('sectors', {}) and not force:
        return 'skipped_exists'

    stocks = _extract_abf_stocks(summary)
    if not stocks:
        return 'skipped_no_data'

    sector_entry = _build_abf_sector(stocks)
    summary.setdefault('sectors', {})['ABF載板'] = sector_entry

    # 更新 strong/weak sectors
    avg_ret = sector_entry['avg_ret_20d']
    strong = summary.get('strong_sectors', [])
    weak = summary.get('weak_sectors', [])
    if avg_ret > 3 and 'ABF載板' not in strong:
        summary['strong_sectors'] = strong + ['ABF載板']
    elif avg_ret < -3 and 'ABF載板' not in weak:
        summary['weak_sectors'] = weak + ['ABF載板']

    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8',
    )
    return 'patched'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='重寫已有 ABF載板 的日期')
    args = parser.parse_args()

    date_dirs = sorted([
        d for d in REPORTS_DIR.iterdir()
        if d.is_dir() and d.name.startswith('2026') and len(d.name) == 8
        and (d / 'summary.json').exists()
    ])

    patched, skipped_exists, skipped_no_data = 0, 0, 0
    for d in date_dirs:
        status = patch_one(d, force=args.force)
        if status == 'patched':
            patched += 1
            print(f'  ✅ {d.name} ABF載板 已補入')
        elif status == 'skipped_exists':
            skipped_exists += 1
        else:
            skipped_no_data += 1

    print(f'\n補入完成：{patched} 個日期已補，{skipped_exists} 個已有，{skipped_no_data} 個資料不足（需 FinMind API）')


if __name__ == '__main__':
    main()
