"""一次重跑回測頁的 18 份 backtest_results*.json（資料更新到今天），股價共用。

每週最後一個交易日由 daily_scan workflow 呼叫，之後 build_backtest_docs.py
彙整成 docs/backtest_summary*.json。各檔參數與歷來手動重跑一致：
  1m 附 --compare；6m/9m/12m 為固定停利停損（--fixed），其 _trailing 版為追蹤停損；
  _al 為只用今日行動清單進場（--action-list）。

安全閥：
- 任一訊號股抓不到股價即中止。backtest.fetch_price_data 失敗時回空資料，
  照跑會把「開盤價缺失而被跳過」的殘缺結果發佈上網站（FinMind 額度用完時最常見）。
- 18 份全數成功才覆寫，避免新舊結果混雜。

用法：python scripts/backtest_all.py
"""
import shutil
import sys
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'scripts'))
import backtest  # noqa: E402

# (輸出檔名, 回溯月數, 額外旗標)
CONFIGS = [
    ('backtest_results', 1, ['--compare']),
    ('backtest_results_3m', 3, []),
    ('backtest_results_6m', 6, ['--fixed']),
    ('backtest_results_9m', 9, ['--fixed']),
    ('backtest_results_12m', 12, ['--fixed']),
    ('backtest_results_36m', 36, []),
    ('backtest_results_6m_trailing', 6, []),
    ('backtest_results_9m_trailing', 9, []),
    ('backtest_results_12m_trailing', 12, []),
]
CONFIGS += [(f'{n}_al', m, ['--action-list'] + [f for f in fl if f == '--fixed'])
            for n, m, fl in CONFIGS]


def start_of(months: int, today: date) -> str:
    return (pd.Timestamp(today) - pd.DateOffset(months=months)).strftime('%Y%m%d')


def main() -> int:
    today = date.today()
    earliest = min(start_of(m, today) for _, m, _ in CONFIGS)
    fetch_from = (pd.Timestamp(earliest) - pd.Timedelta(days=30)).strftime('%Y-%m-%d')
    cache: dict = {}
    fetch = backtest.fetch_price_data

    def fetch_shared(dl, stock_ids, start, end):
        missing = [s for s in stock_ids if s not in cache]
        if missing:
            cache.update(fetch(dl, missing, fetch_from, end))
        empty = [s for s in stock_ids if not cache.get(s)]
        if empty:
            raise RuntimeError(f'股價抓取失敗 {len(empty)} 檔：{empty[:10]}，中止且不覆寫回測結果')
        lo, hi = start.replace('-', ''), end.replace('-', '')
        return {s: {d: v for d, v in cache[s].items() if lo <= d <= hi} for s in stock_ids}

    backtest.fetch_price_data = fetch_shared
    tmp = Path(tempfile.mkdtemp(prefix='backtest_all_'))
    for name, months, flags in CONFIGS:
        print(f'\n##### {name}（近 {months} 個月）', flush=True)
        sys.argv = ['backtest.py', '--no-backfill', '--no-notion',
                    '--start', start_of(months, today), '--output', str(tmp / f'{name}.json')] + flags
        backtest.main()
        if not (tmp / f'{name}.json').exists():
            print(f'❌ {name} 未產出結果（無訊號？），中止且不覆寫', file=sys.stderr)
            return 1
    for name, _, _ in CONFIGS:
        shutil.move(tmp / f'{name}.json', ROOT / f'{name}.json')
    print(f'\n✅ {len(CONFIGS)} 份回測已更新至 {today:%Y-%m-%d}（{len(cache)} 檔股價）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
