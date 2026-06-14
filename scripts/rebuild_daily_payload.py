"""
重建最新交易日的 docs payload（daily.json + YYYYMMDD.json）。
供 CI 在 enrich_product_mix 之後呼叫，把新抓的產銷組合帶進行動清單。

用法：python scripts/rebuild_daily_payload.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import build_docs


def main():
    base = ROOT / 'daily_reports'
    dirs = sorted(
        [d for d in base.iterdir()
         if d.is_dir() and d.name.isdigit() and len(d.name) == 8
         and (d / 'summary.json').exists()],
        key=lambda d: d.name, reverse=True,
    )
    if not dirs:
        print('找不到 daily_reports')
        return
    latest = dirs[0]
    summary = json.loads((latest / 'summary.json').read_text(encoding='utf-8'))
    payload = build_docs.build_daily_payload(summary)
    (ROOT / 'docs' / f'{latest.name}.json').write_text(
        json.dumps(build_docs._sanitize(payload), ensure_ascii=False, separators=(',', ':')),
        encoding='utf-8')
    (ROOT / 'docs' / 'daily.json').write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8')
    n_pm = sum(1 for q in payload.get('qualified', []) if q.get('product_mix'))
    print(f'{latest.name} payload 已重建：qualified {len(payload.get("qualified", []))} 檔，'
          f'{n_pm} 檔含產銷組合')


if __name__ == '__main__':
    main()
