"""
基本面功課工具
針對單一個股產出：量化數據 + 檢查清單紅綠燈評分 + 籌碼補充 + Gemini grounding 質性研究。

用法：
    python scripts/fundamental_homework.py 2330            # 單檔
    python scripts/fundamental_homework.py 2330 3231 --force
    python scripts/fundamental_homework.py --auto --top 5  # 讀 docs/breakout.json 行動清單前 N 檔

輸出：
    docs/homework/{sid}.json   （單檔報告）
    docs/homework/index.json   （已完成功課清單）
    前端：docs/homework.html?sid=2330
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / '.env')
except ImportError:
    pass

DOCS_DIR = ROOT / 'docs'
HOMEWORK_DIR = DOCS_DIR / 'homework'
GENERATED_FMT = '%Y-%m-%d %H:%M'


# ── 檢查清單評分（純規則，可獨立測試） ─────────────────────────────────────────

def _last(vals, n):
    """取尾端 n 筆非 None 前的原始序列（保留 None 判斷資料缺漏）。"""
    return vals[-n:] if vals else []


def check_revenue_yoy_streak(revenue):
    """月營收 YoY：連 3 月正=green，最新正=yellow，否則 red。"""
    if not revenue or not revenue.get('yoy'):
        return 'gray', '無營收資料'
    yoy = [v for v in revenue['yoy'] if v is not None]
    if not yoy:
        return 'gray', '無 YoY 資料'
    if len(yoy) >= 3 and all(v > 0 for v in yoy[-3:]):
        return 'green', f'YoY 連 3 月為正（最新 {yoy[-1]:+.1f}%）'
    if yoy[-1] > 0:
        return 'yellow', f'最新 YoY {yoy[-1]:+.1f}%，但未連 3 月為正'
    return 'red', f'最新 YoY {yoy[-1]:+.1f}%'


def check_cum_yoy(revenue):
    """累計 YoY：>10%=green，>0=yellow，否則 red。"""
    if not revenue or not revenue.get('cum_yoy'):
        return 'gray', '無累計 YoY 資料'
    vals = [v for v in revenue['cum_yoy'] if v is not None]
    if not vals:
        return 'gray', '無累計 YoY 資料'
    v = vals[-1]
    if v > 10:
        return 'green', f'累計 YoY {v:+.1f}%'
    if v > 0:
        return 'yellow', f'累計 YoY {v:+.1f}%，成長偏緩'
    return 'red', f'累計 YoY {v:+.1f}%'


def check_revenue_mom(revenue):
    """月營收 MoM：>0=green，>=-5%=yellow，否則 red。"""
    if not revenue or not revenue.get('mom'):
        return 'gray', '無 MoM 資料'
    vals = [v for v in revenue['mom'] if v is not None]
    if not vals:
        return 'gray', '無 MoM 資料'
    v = vals[-1]
    if v > 0:
        return 'green', f'最新 MoM {v:+.1f}%'
    if v >= -5:
        return 'yellow', f'最新 MoM {v:+.1f}%，小幅回落'
    return 'red', f'最新 MoM {v:+.1f}%'


def check_gross_margin_trend(margins):
    """毛利率：連 2 季走升=green，最新一季走升=yellow，否則 red。"""
    if not margins or not margins.get('gross_margin'):
        return 'gray', '無毛利率資料'
    gm = [v for v in margins['gross_margin'] if v is not None]
    if len(gm) < 2:
        return 'gray', '毛利率資料不足 2 季'
    if len(gm) >= 3 and gm[-1] > gm[-2] > gm[-3]:
        return 'green', f'毛利率連 2 季走升（{gm[-3]:.1f}→{gm[-2]:.1f}→{gm[-1]:.1f}%）'
    if gm[-1] > gm[-2]:
        return 'yellow', f'毛利率最新一季走升（{gm[-2]:.1f}→{gm[-1]:.1f}%）'
    return 'red', f'毛利率走弱（{gm[-2]:.1f}→{gm[-1]:.1f}%）'


def check_eps_positive(eps):
    """EPS：近 4 季皆正=green，最新一季正=yellow，否則 red。"""
    if not eps or not eps.get('eps'):
        return 'gray', '無 EPS 資料'
    vals = [v for v in eps['eps'] if v is not None]
    if not vals:
        return 'gray', '無 EPS 資料'
    last4 = vals[-4:]
    if len(last4) == 4 and all(v > 0 for v in last4):
        return 'green', f'近 4 季 EPS 皆正（最新 {last4[-1]:.2f} 元）'
    if vals[-1] > 0:
        return 'yellow', f'最新一季 EPS {vals[-1]:.2f} 元，但近 4 季未全正'
    return 'red', f'最新一季 EPS {vals[-1]:.2f} 元'


def check_eps_growth(eps):
    """EPS 成長：近 4 季合計 > 前 4 季合計=green；不足 8 季時看最新 YoY。"""
    if not eps or not eps.get('eps'):
        return 'gray', '無 EPS 資料'
    vals = [v for v in eps['eps'] if v is not None]
    if len(vals) >= 8:
        recent, prior = sum(vals[-4:]), sum(vals[-8:-4])
        if recent > prior:
            return 'green', f'近 4 季 EPS 合計 {recent:.2f} > 前 4 季 {prior:.2f}'
        return 'red', f'近 4 季 EPS 合計 {recent:.2f} ≤ 前 4 季 {prior:.2f}'
    yoy = [v for v in (eps.get('yoy') or []) if v is not None]
    if yoy:
        if yoy[-1] > 0:
            return 'yellow', f'資料不足 8 季，最新 EPS YoY {yoy[-1]:+.1f}%'
        return 'red', f'最新 EPS YoY {yoy[-1]:+.1f}%'
    return 'gray', 'EPS 資料不足 8 季且無 YoY'


def check_pe_percentile(valuation):
    """PE 歷史百分位：<40=green，<60=yellow，否則 red。"""
    if not valuation or valuation.get('pe_percentile') is None:
        return 'gray', '無本益比資料'
    p = valuation['pe_percentile']
    per = valuation.get('per')
    label = f'PE {per:.1f} 倍，' if per else ''
    if p < 40:
        return 'green', f'{label}位於近 5 年第 {p:.0f} 百分位（偏低）'
    if p < 60:
        return 'yellow', f'{label}位於近 5 年第 {p:.0f} 百分位（中性）'
    return 'red', f'{label}位於近 5 年第 {p:.0f} 百分位（偏高）'


def check_chip_net(chip):
    """法人買賣超：20 日淨買超=green，60 日淨買超=yellow，否則 red。"""
    if not chip or chip.get('net20') is None:
        return 'gray', '無法人買賣超資料'
    net20, net60 = chip['net20'], chip.get('net60')
    if net20 > 0:
        return 'green', f'法人近 20 日淨買超 {net20:,} 張'
    if net60 is not None and net60 > 0:
        return 'yellow', f'20 日淨賣超 {net20:,} 張，但 60 日仍淨買超 {net60:,} 張'
    return 'red', f'法人近 20 日淨賣超 {net20:,} 張'


CHECKLIST_RULES = [
    ('rev_yoy_streak', '月營收 YoY 動能', 'revenue', check_revenue_yoy_streak),
    ('cum_yoy', '累計營收 YoY > 10%', 'revenue', check_cum_yoy),
    ('rev_mom', '月營收 MoM 為正', 'revenue', check_revenue_mom),
    ('gross_margin', '毛利率走升', 'margins', check_gross_margin_trend),
    ('eps_positive', 'EPS 近 4 季為正', 'eps', check_eps_positive),
    ('eps_growth', 'EPS 年度成長', 'eps', check_eps_growth),
    ('pe_percentile', '本益比歷史位階', 'valuation', check_pe_percentile),
    ('chip_net', '法人買賣超趨勢', 'chip', check_chip_net),
]

_STATUS_SCORE = {'green': 1.0, 'yellow': 0.5, 'red': 0.0}


def build_checklist(data):
    """
    data: {'revenue':…, 'margins':…, 'eps':…, 'valuation':…, 'chip':…}
    回傳 (items, score)；score = 非 gray 項的加權平均 ×100，全 gray 時為 None。
    """
    items = []
    got, total = 0.0, 0
    for key, label, field, fn in CHECKLIST_RULES:
        status, note = fn(data.get(field))
        items.append({'key': key, 'label': label, 'status': status, 'note': note})
        if status != 'gray':
            got += _STATUS_SCORE[status]
            total += 1
    score = round(got / total * 100) if total else None
    return items, score


# ── 資料抓取 ──────────────────────────────────────────────────────────────────

def fetch_quant(sid):
    """月營收 + EPS + 三率（重用 fundamentals_fetcher），失敗回傳 None 區塊。"""
    from fundamentals_fetcher import (
        parse_revenue, parse_financials,
        fetch_revenue_finmind, fetch_financials_finmind, _get_dl,
    )
    revenue = eps = margins = None
    errors = []
    try:
        dl = _get_dl()
    except Exception as e:
        return None, None, None, [f'FinMind 初始化失敗: {e}']
    try:
        revenue = parse_revenue(fetch_revenue_finmind(dl, sid))
    except Exception as e:
        errors.append(f'revenue: {e}')
    try:
        eps, margins = parse_financials(fetch_financials_finmind(dl, sid))
    except Exception as e:
        errors.append(f'eps_margins: {e}')
    return revenue, eps, margins, errors


def fetch_valuation(sid, years=5):
    """FinMind taiwan_stock_per_pbr → 現值 PE/PB 與近 5 年歷史百分位。"""
    from datafeed import finmind_fetch
    end = datetime.now()
    start = end - timedelta(days=365 * years)
    df = finmind_fetch(
        'taiwan_stock_per_pbr', stock_id=sid,
        start_date=start.strftime('%Y-%m-%d'), end_date=end.strftime('%Y-%m-%d'),
    )
    if df is None or df.empty:
        return None

    def _percentile(series):
        vals = [float(v) for v in series if v and float(v) > 0]
        if len(vals) < 60:  # 歷史太短沒有位階意義
            return None, None
        cur = vals[-1]
        rank = sum(1 for v in vals if v <= cur) / len(vals) * 100
        return round(cur, 2), round(rank, 1)

    df = df.sort_values('date')
    per, pe_pct = _percentile(df['PER'])
    pbr, pb_pct = _percentile(df['PBR'])
    dy = None
    if 'dividend_yield' in df.columns:
        dyv = [float(v) for v in df['dividend_yield'] if v is not None]
        dy = round(dyv[-1], 2) if dyv else None
    return {
        'per': per, 'pe_percentile': pe_pct,
        'pbr': pbr, 'pb_percentile': pb_pct,
        'dividend_yield': dy,
        'history_days': len(df),
    }


def fetch_chip(sid):
    """法人買賣超：近 20/60 日淨額（張）與每日明細。"""
    from datafeed import finmind_fetch
    from indicators.chip import aggregate_chip
    end = datetime.now()
    start = end - timedelta(days=120)
    df = finmind_fetch(
        'taiwan_stock_institutional_investors', stock_id=sid,
        start_date=start.strftime('%Y-%m-%d'), end_date=end.strftime('%Y-%m-%d'),
    )
    if df is None or df.empty:
        return None
    agg = aggregate_chip(df, days=60)
    if agg.empty:
        return None
    total = agg['合計'].tolist()
    return {
        'net20': int(sum(total[-20:])),
        'net60': int(sum(total)),
        'daily': [
            {'date': r['日期'], 'foreign': int(r['外資']),
             'trust': int(r['投信']), 'total': int(r['合計'])}
            for _, r in agg.tail(20).iterrows()
        ],
    }


# ── Gemini 質性研究 ───────────────────────────────────────────────────────────

def _quant_brief(sid, name, revenue, margins, eps, valuation, chip, product_mix):
    """整理給 Gemini 的量化摘要（讓敘述有數字依據）。"""
    brief = {'公司': f'{name}（{sid}）'}
    if revenue and revenue.get('yoy'):
        yoy = [v for v in revenue['yoy'] if v is not None]
        cum = [v for v in (revenue.get('cum_yoy') or []) if v is not None]
        if yoy:
            brief['月營收YoY近3月'] = yoy[-3:]
        if cum:
            brief['累計營收YoY'] = cum[-1]
    if margins and margins.get('gross_margin'):
        gm = [v for v in margins['gross_margin'] if v is not None]
        brief['毛利率近4季'] = gm[-4:]
    if eps and eps.get('eps'):
        ev = [v for v in eps['eps'] if v is not None]
        brief['EPS近4季'] = ev[-4:]
    if valuation:
        brief['本益比'] = valuation.get('per')
        brief['PE近5年百分位'] = valuation.get('pe_percentile')
        brief['股價淨值比'] = valuation.get('pbr')
    if chip:
        brief['法人20日淨買超(張)'] = chip['net20']
    if product_mix and product_mix.get('summary'):
        brief['已知業務摘要'] = product_mix['summary']
    return brief


def _parse_gemini_json(text):
    """去除可能的 code fence 後解析 JSON。"""
    t = text.strip()
    t = re.sub(r'^```(?:json)?\s*|\s*```$', '', t)
    m = re.search(r'\{.*\}', t, re.S)
    if not m:
        raise ValueError('回應中找不到 JSON')
    return json.loads(m.group(0))


def run_gemini(sid, name, quant_brief):
    """呼叫 Gemini grounding 做質性研究，失敗時回傳帶 error 的降級結果。"""
    from gemini_writer import GeminiWriter
    try:
        writer = GeminiWriter()
        text = writer.generate(
            'fundamental_homework',
            {'data': quant_brief, 'date': datetime.now().strftime('%Y-%m-%d')},
            use_grounding=True,
        )
        return _parse_gemini_json(text)
    except Exception as e:
        print(f'  [WARN] {sid} Gemini 質性研究失敗：{e}')
        return {'error': f'生成失敗：{e}'}


# ── 快取 / 輸出 ───────────────────────────────────────────────────────────────

def is_fresh(sid, max_days=7):
    path = HOMEWORK_DIR / f'{sid}.json'
    if not path.exists():
        return False
    try:
        d = json.loads(path.read_text(encoding='utf-8'))
        generated = datetime.strptime(d.get('generated_at', ''), GENERATED_FMT)
        return (datetime.now() - generated).days < max_days
    except Exception:
        return False


def load_product_mix(sid):
    """重用 enrich_product_mix 寫在 docs/fundamentals/{sid}.json 的 product_mix。"""
    path = DOCS_DIR / 'fundamentals' / f'{sid}.json'
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('product_mix')
    except Exception:
        return None


def update_index(entry):
    HOMEWORK_DIR.mkdir(parents=True, exist_ok=True)
    path = HOMEWORK_DIR / 'index.json'
    items = []
    if path.exists():
        try:
            items = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            items = []
    items = [i for i in items if i.get('stock_id') != entry['stock_id']]
    items.append(entry)
    items.sort(key=lambda i: i.get('generated_at', ''), reverse=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding='utf-8')


def lookup_name(sid):
    path = DOCS_DIR / 'stocks_index.json'
    if path.exists():
        try:
            for s in json.loads(path.read_text(encoding='utf-8')):
                if s.get('id') == sid:
                    return s.get('name', sid)
        except Exception:
            pass
    return sid


# ── 主流程 ────────────────────────────────────────────────────────────────────

def do_homework(sid, name=None, force=False, skip_gemini=False):
    name = name or lookup_name(sid)
    if not force and is_fresh(sid):
        print(f'[skip] {sid} {name}：7 天內已做過功課（--force 可重做）')
        return None

    print(f'[homework] {sid} {name} 開始…')

    revenue, eps, margins, errors = fetch_quant(sid)
    for e in errors:
        print(f'  [WARN] {e}')

    valuation = chip = None
    try:
        valuation = fetch_valuation(sid)
    except Exception as e:
        print(f'  [WARN] valuation: {e}')
    try:
        chip = fetch_chip(sid)
    except Exception as e:
        print(f'  [WARN] chip: {e}')

    checklist, score = build_checklist({
        'revenue': revenue, 'margins': margins, 'eps': eps,
        'valuation': valuation, 'chip': chip,
    })

    product_mix = load_product_mix(sid)
    if skip_gemini:
        narrative = {'error': '本次執行略過 Gemini（--skip-gemini）'}
    else:
        narrative = run_gemini(
            sid, name,
            _quant_brief(sid, name, revenue, margins, eps, valuation, chip, product_mix),
        )

    result = {
        'stock_id': sid,
        'name': name,
        'generated_at': datetime.now().strftime(GENERATED_FMT),
        'revenue': revenue,
        'eps': eps,
        'margins': margins,
        'valuation': valuation,
        'chip': chip,
        'product_mix': product_mix,
        'checklist': checklist,
        'score': score,
        'narrative': narrative,
        'fetch_errors': errors,
    }

    HOMEWORK_DIR.mkdir(parents=True, exist_ok=True)
    out = HOMEWORK_DIR / f'{sid}.json'
    out.write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    update_index({
        'stock_id': sid, 'name': name, 'score': score,
        'generated_at': result['generated_at'],
    })
    lights = ''.join(
        {'green': '🟢', 'yellow': '🟡', 'red': '🔴', 'gray': '⚪'}[i['status']]
        for i in checklist
    )
    print(f'[done] {sid} {name} 分數 {score} {lights} → {out}')
    return result


def auto_targets(top_n):
    """讀 docs/breakout.json 行動清單前 N 檔。"""
    path = DOCS_DIR / 'breakout.json'
    if not path.exists():
        print('[auto] docs/breakout.json 不存在')
        return []
    picks = json.loads(path.read_text(encoding='utf-8')).get('picks', [])
    targets = []
    for p in picks[:top_n]:
        sid = p.get('id') or p.get('stock_id')
        if sid:
            targets.append((str(sid), p.get('name')))
    if not targets:
        print('[auto] 今日行動清單無個股')
    return targets


def main():
    parser = argparse.ArgumentParser(description='基本面功課工具')
    parser.add_argument('stock_ids', nargs='*', help='個股代號，如 2330')
    parser.add_argument('--auto', action='store_true', help='讀當日行動清單自動做功課')
    parser.add_argument('--top', type=int, default=5, help='--auto 時取前 N 檔')
    parser.add_argument('--force', action='store_true', help='忽略 7 天快取重做')
    parser.add_argument('--skip-gemini', action='store_true', help='略過質性研究（省額度/測試）')
    args = parser.parse_args()

    targets = [(sid, None) for sid in args.stock_ids]
    if args.auto:
        targets += auto_targets(args.top)
    if not targets:
        parser.error('請指定個股代號或 --auto')

    for i, (sid, name) in enumerate(targets):
        if i:
            time.sleep(1)
        do_homework(sid, name=name, force=args.force, skip_gemini=args.skip_gemini)


if __name__ == '__main__':
    main()
