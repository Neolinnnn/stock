"""
將 daily_reports/YYYYMMDD/summary.json 轉換為 docs/ 靜態 JSON，
供 GitHub Pages 歷史查詢使用。

生成：
  docs/dates.json         — 可用日期清單（降冪）
  docs/YYYYMMDD.json      — 每日 payload（與 daily.json 同格式）

用法：
  python strategy_templates/build_docs.py
"""
import json
from pathlib import Path
import sys, os

sys.path.insert(0, os.path.dirname(__file__))


def build_daily_payload(summary):
    """從 summary.json 轉換為前端 payload（與 07_daily_scan 同邏輯）"""
    sectors, chips, stocks = [], [], []
    for sector, data in summary.get('sectors', {}).items():
        sectors.append({
            'sector': sector,
            'ret20':  data.get('avg_ret_20d', ''),
            'rsi':    data.get('avg_rsi', ''),
            'buy':    data.get('buy_count', 0),
            'hot':    data.get('hot_count', 0),
        })
        for st in data.get('stocks', []):
            chip = st.get('chip', {})
            stocks.append({
                'date':      summary['date'],
                'sector':    sector,
                'id':        st['id'],
                'name':      st['name'],
                'price':     st.get('price', ''),
                'rsi':       st.get('rsi', ''),
                'ret20':     st.get('ret_20d', ''),
                'signal':    st.get('signal', ''),
                'sharpe':    st.get('cv_sharpe', ''),
                'foreign':   chip.get('外資', ''),
                'trust':     chip.get('投信', ''),
                'dealer':    chip.get('自營', ''),
                'chipTotal': chip.get('合計', ''),
                'news': ' / '.join(
                    n['title'] for n in st.get('news', [])[:2]
                ),
            })
            if chip.get('合計', 0):
                chips.append({
                    'id': st['id'], 'name': st['name'], 'sector': sector,
                    'total':   chip.get('合計', 0),
                    'foreign': chip.get('外資', 0),
                    'trust':   chip.get('投信', 0),
                    'dealer':  chip.get('自營', 0),
                })
    mkt = summary.get('market', {})
    qualified = [
        {
            'sector':    q.get('sector', ''),
            'id':        q.get('id', ''),
            'name':      q.get('name', ''),
            'price':     q.get('price', ''),
            'rsi':       q.get('rsi', ''),
            'cv_sharpe': q.get('cv_sharpe', ''),
        }
        for q in summary.get('qualified', [])
    ]
    return {
        'meta': {
            '掃描日期':  summary.get('date', ''),
            '加權指數':  mkt.get('加權指數', ''),
            '漲跌幅%':   mkt.get('漲跌幅', ''),
            '強勢族群':  ', '.join(summary.get('strong_sectors', [])),
            '弱勢族群':  ', '.join(summary.get('weak_sectors', [])),
            '雙條件推薦': len(qualified),
        },
        'qualified': qualified,
        'sectors': sectors,
        'chips':   sorted(chips, key=lambda x: x['total'], reverse=True),
        'stocks':  stocks,
    }


def build_all():
    base     = Path('daily_reports')
    docs_dir = Path('docs')
    docs_dir.mkdir(exist_ok=True)

    date_dirs = sorted(
        [d for d in base.iterdir()
         if d.is_dir() and d.name.isdigit() and len(d.name) == 8
         and (d / 'summary.json').exists()],
        key=lambda d: d.name,
        reverse=True,
    )

    if not date_dirs:
        print('找不到任何 daily_reports/YYYYMMDD/summary.json')
        return []

    dates = []
    for d in date_dirs:
        summary = json.loads((d / 'summary.json').read_text(encoding='utf-8'))
        payload = build_daily_payload(summary)

        out = docs_dir / f"{d.name}.json"
        out.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
            encoding='utf-8',
        )
        dates.append(d.name)

    # dates.json
    (docs_dir / 'dates.json').write_text(
        json.dumps(dates, ensure_ascii=False),
        encoding='utf-8',
    )

    # 同步更新 daily.json = 最新一天
    latest_summary = json.loads(
        (date_dirs[0] / 'summary.json').read_text(encoding='utf-8')
    )
    (docs_dir / 'daily.json').write_text(
        json.dumps(build_daily_payload(latest_summary),
                   ensure_ascii=False, indent=2, default=str),
        encoding='utf-8',
    )

    print(f'docs/ 已更新：{len(dates)} 個交易日  最新={dates[0]}')
    return dates


if __name__ == '__main__':
    build_all()
