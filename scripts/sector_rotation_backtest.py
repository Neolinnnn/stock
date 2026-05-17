"""族群輪動回測：讀 docs/{date}.json，跑 16 個策略 variant，輸出 results.json"""
from __future__ import annotations
import json
from pathlib import Path


def load_daily_signals(docs_dir: Path) -> dict[str, dict]:
    """讀取 docs/{YYYYMMDD}.json，回傳 {date: {sectors, stocks}}，依日期升序"""
    result: dict[str, dict] = {}
    for f in sorted(docs_dir.glob('[0-9]*.json')):
        if len(f.stem) != 8 or not f.stem.isdigit():
            continue
        data = json.loads(f.read_text(encoding='utf-8'))
        result[f.stem] = {
            'sectors': data.get('sectors', []),
            'stocks':  data.get('stocks',  []),
        }
    return result
