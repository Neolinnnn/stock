"""
每日 Analyzer Framework 選股掃描，結果上傳 Notion。
標題格式：analyzer framework MMDD
由 GitHub Actions 每個交易日收盤後呼叫。
"""
import sys
import importlib.util
from pathlib import Path

# 07_analyzer_framework.py 檔名以數字開頭，無法直接 import
_fw_path = Path(__file__).parent / 'strategies' / '07_analyzer_framework.py'
_spec    = importlib.util.spec_from_file_location('analyzer_framework', _fw_path)
_fw      = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fw)

from notion_upload import upload_analyzer_daily


def main() -> None:
    print('\n' + '=' * 50)
    print('  Analyzer Framework 每日掃描')
    print('=' * 50)

    signals_a, signals_b, date_str = _fw.scan_daily()

    if not date_str:
        print('  ⚠️  無法取得交易日資料，跳過上傳')
        return

    print(f'  掃描日期：{date_str}')
    print(f'  規則A（純技術）觸發：{len(signals_a)} 檔')
    for sig in signals_a:
        b_mark = '✅' if any(s['stock_id'] == sig['stock_id'] for s in signals_b) else '○'
        print(f'    {b_mark} {sig["stock_id"]} {sig["stock_name"]}'
              f'（{sig.get("sector","")}）@ {sig["signal_close"]:.1f}')

    print(f'  規則B（+法人過濾）觸發：{len(signals_b)} 檔')

    page_id = upload_analyzer_daily(signals_a, signals_b, date_str)
    print(f'  ✅ 已上傳 Notion，頁面 ID：{page_id}')
    print('=' * 50 + '\n')


if __name__ == '__main__':
    main()
