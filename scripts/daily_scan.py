"""
每日收盤後掃描 - 7 大族群技術/基本/籌碼/消息面綜合
Daily Post-Market Sector Scan

輸出：
  - daily_reports/YYYYMMDD/summary.json  （Notion 上傳用）
  - daily_reports/YYYYMMDD/summary.md    （人類可讀）
  - daily_reports/YYYYMMDD/*.png         （族群比較圖 / 個股圖表）

用法：
  python strategy_templates/07_daily_scan.py
"""
import sys, os, json, math, time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

# 載入主引擎
_main = os.path.join(os.path.dirname(__file__), 'batch_scan.py')
with open(_main, encoding='utf-8') as f:
    code = f.read()
code = code.split("if __name__ == '__main__':")[0]
exec(code, globals())

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams

# 支援中文
rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'SimHei', 'Arial Unicode MS']
rcParams['axes.unicode_minus'] = False

# ── 7 大族群定義 ──────────────────────────────────────────────────────────────
SECTORS = {
    '光通訊': {
        '4979': '華星光', '3450': '聯鈞', '3665': '貿聯-KY',
        '3105': '穩懋', '8086': '宏捷科', '2455': '全新',
        '4906': '正文', '2345': '智邦',
    },
    '記憶體': {
        '2408': '南亞科', '2337': '旺宏', '2344': '華邦電',
        '3006': '晶豪科', '2451': '創見', '5289': '宜鼎',
        '3205': '十銓',
    },
    'AI伺服器': {
        '2317': '鴻海', '2382': '廣達', '3231': '緯創',
        '2376': '技嘉', '4938': '和碩', '2357': '華碩',
    },
    '封測': {
        '3711': '日月光投控', '2449': '京元電子', '6510': '精測',
        '2441': '超豐', '6257': '矽格', '6239': '力成',
    },
    '光學': {
        '3008': '大立光', '3406': '玉晶光',
    },
    'IC設計': {
        '2454': '聯發科', '3034': '聯詠', '4966': '譜瑞-KY',
        '4919': '新唐', '2388': '威盛',
    },
    '車用電子': {
        '3552': '同致', '1533': '車王電', '2243': '怡利電',
    },
    '綠能環保': {
        '5292': '華懋',
    },
    'CoPoS封裝': {
        '6789': '采鈺', '3535': '晶彩科', '3680': '家登',
        '6664': '群翊', '2467': '志聖', '7734': '印能',
        '5443': '均豪', '6640': '均華', '6187': '萬潤',
        '3131': '弘塑', '3583': '辛耘',
        '3711': '日月光投控', '2449': '京元電子', '6239': '力成',
    },
    '銅箔/CCL': {
        '8358': '金居', '2383': '台光電', '6213': '聯茂',
        '2368': '金像電', '8046': '南電',
    },
    'AI ASIC/IP': {
        '2330': '台積電', '3443': '創意', '3661': '世芯-KY',
        '3529': '力旺', '6643': 'M31',
    },
    'AI 散熱': {
        '3017': '奇鋐', '3324': '雙鴻', '3653': '健策',
        '8210': '勤誠', '8996': '高力',
    },
    'AI 電源': {
        '2308': '台達電', '2301': '光寶科', '6412': '群電',
    },
}


def _atr_stop_loss(highs, lows, closes, period=14, multiplier=2.0):
    """以 ATR(14) ×2 計算動態停損價（Wilder 平均）。

    Returns:
        (atr_value, stop_price) 或 (None, None) 資料不足時
    """
    if len(closes) < period + 1:
        return None, None
    trs = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        trs.append(tr)
    # Wilder smoothing: 第一個 ATR = SMA of first `period` TRs
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return round(atr, 2), round(closes[-1] - multiplier * atr, 1)


def fetch_price_stats(sid):
    """取近期價格資料，回傳 ret_20d + 估值目標價 + ATR 停損

    短期：布林上軌（技術壓力位）
    中期：P/E 均值回歸（相對估值；PE 不可用時改用 P/B）
    長期：40% P/E + 40% PEG + 20% 技術 加權合成（依指南結論）
    停損：ATR(14) × 2 動態停損
    """
    try:
        from twstock import Stock
        import statistics
        from datetime import date
        s = Stock(sid)
        st = datetime.now() - timedelta(days=120)
        s.fetch_from(st.year, st.month)
        p = list(s.price)
        if len(p) < 21:
            return None, None, None, None, None, None
        price = p[-1]
        ret_20d = (p[-1] - p[-21]) / p[-21]
        # ATR 停損（需要 high/low 資料）
        try:
            highs = list(s.high)
            lows = list(s.low)
            atr_val, stop_price = _atr_stop_loss(highs, lows, p)
        except Exception:
            atr_val, stop_price = None, None

        # 短期：布林上軌（技術壓力位）
        p20 = p[-20:]
        ma20 = sum(p20) / 20
        sigma = statistics.stdev(p20)
        t_short = round(ma20 + 2 * sigma, 1)

        # 中期 + 長期：基本面估值（P/E + PEG），FinMind 免費 api
        try:
            import pandas as _pd
            from FinMind.data import DataLoader as _DL
            _dl = _DL()
            one_yr_ago = (date.today() - timedelta(days=365)).strftime("%Y-%m-%d")
            per_df = _dl.taiwan_stock_per_pbr(stock_id=sid, start_date=one_yr_ago)

            if per_df.empty:
                raise ValueError("no per data")
            valid_pe = per_df[per_df["PER"] > 0]["PER"]
            valid_pb = per_df[per_df["PBR"] > 0]["PBR"]
            cur_pe = float(per_df.iloc[-1]["PER"])
            cur_pb = float(per_df.iloc[-1]["PBR"])
            med_pe = float(valid_pe.median()) if not valid_pe.empty else 0
            med_pb = float(valid_pb.median()) if not valid_pb.empty else 0

            # 中期：P/E 均值回歸（上下限 ±50%）
            if cur_pe > 0 and med_pe > 0:
                t_mid = round(price * max(0.85, min(med_pe / cur_pe, 1.5)), 1)
            elif cur_pb > 0 and med_pb > 0:
                t_mid = round(price * max(0.85, min(med_pb / cur_pb, 1.5)), 1)
            else:
                hi60 = max(p[-60:]) if len(p) >= 60 else max(p)
                t_mid = round(hi60 * 1.03, 1)

            # 長期：40% P/E + 40% PEG + 20% 技術
            pe_target = price * max(0.85, min(med_pe / cur_pe, 1.5)) if cur_pe > 0 and med_pe > 0 else price * 1.10
            peg_g = min(cur_pe, 30) / 100 if cur_pe > 0 else 0.10  # PEG=1 隱含成長率
            peg_target = price * (1 + peg_g)
            t_long = round(0.4 * pe_target + 0.4 * peg_target + 0.2 * t_short, 1)

        except Exception:
            # Fallback：純技術
            hi60 = max(p[-60:]) if len(p) >= 60 else max(p)
            t_mid = round(hi60 * 1.03, 1)
            daily_rets = [(p[i] - p[i-1]) / p[i-1] for i in range(1, len(p))]
            vol = statistics.stdev(daily_rets[-20:]) if len(daily_rets) >= 20 else 0.02
            t_long = round(price * (1 + vol * 15), 1)

        return ret_20d, t_short, t_mid, t_long, atr_val, stop_price
    except Exception:
        return None, None, None, None, None, None


def fetch_20d_return(sid):
    """向後相容的薄包裝"""
    ret, *_ = fetch_price_stats(sid)
    return ret


def fetch_market_overview():
    """抓取大盤數據"""
    try:
        from twstock import Stock
        overview = {}
        # 加權指數
        try:
            twii = Stock('^TWII')
            st = datetime.now() - timedelta(days=30)
            twii.fetch_from(st.year, st.month)
            if twii.price:
                cur = twii.price[-1]
                prev = twii.price[-2] if len(twii.price) > 1 else cur
                overview['加權指數'] = cur
                overview['漲跌幅'] = (cur - prev) / prev * 100
        except:
            overview['加權指數'] = None
            overview['漲跌幅'] = None
        return overview
    except Exception as e:
        return {'error': str(e)}


def fetch_news(stock_id, stock_name, days=3):
    """抓取個股最新新聞（鉅亨網 API，免費無需 token）"""
    import urllib.request, json as _json
    try:
        url = (f'https://api.cnyes.com/media/api/v1/newslist/category/tw_stock'
               f'?limit=5&stock_code={stock_id}')
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = _json.loads(r.read())
        items = data.get('items', {}).get('data', [])
        cutoff = datetime.now() - timedelta(days=days)
        result = []
        for it in items:
            import datetime as _dt
            pub_dt = _dt.datetime.fromtimestamp(it.get('publishAt', 0))
            if pub_dt < cutoff:
                continue
            result.append({
                'date': pub_dt.strftime('%Y-%m-%d'),
                'title': it.get('title', ''),
                'source': it.get('media', {}).get('name', '鉅亨網'),
            })
            if len(result) >= 3:
                break
        return result
    except Exception:
        return []


_CHIP_MAP = {
    'Foreign_Investor':    '外資',
    'Foreign_Dealer_Self': '外資',
    'Investment_Trust':    '投信',
    'Dealer_self':         '自營',
    'Dealer_Hedging':      '自營',
}


def fetch_chip_data(stock_id, days=5):
    """抓取三大法人近 N 日買賣超（FinMind）"""
    try:
        from FinMind.data import DataLoader
        dl = DataLoader()
        end = datetime.now().strftime('%Y-%m-%d')
        start = (datetime.now() - timedelta(days=days + 5)).strftime('%Y-%m-%d')
        df = dl.taiwan_stock_institutional_investors(
            stock_id=stock_id, start_date=start, end_date=end
        )
        if df.empty:
            return {}
        df = df.sort_values('date', ascending=False)
        latest_date = df['date'].iloc[0]
        day_df = df[df['date'] == latest_date]
        result = {'date': str(latest_date), '外資': 0, '投信': 0, '自營': 0}
        for _, row in day_df.iterrows():
            zh = _CHIP_MAP.get(str(row.get('name', '')))
            if zh:
                result[zh] += int(row.get('buy', 0) - row.get('sell', 0))
        result['合計'] = result['外資'] + result['投信'] + result['自營']
        return result
    except Exception:
        return {}


def scan_sector(sector_name, stocks):
    """掃描單一族群"""
    # 分點爬蟲模組（延遲匯入避免循環依賴與測試時拖慢啟動）
    try:
        from indicators.broker import fetch_broker_top15, main_force_score
    except Exception:
        fetch_broker_top15 = None
        main_force_score = None

    results = []
    for sid, name in stocks.items():
        try:
            r = analyze_stock(sid, name)
            ret_20d, t_short, t_mid, t_long, atr_val, stop_price = fetch_price_stats(sid)
            r['ret_20d'] = ret_20d
            r['target_short'] = t_short
            r['target_mid']   = t_mid
            r['target_long']  = t_long
            r['atr14']        = atr_val
            r['stop_loss']    = stop_price   # ATR(14) × 2 動態停損價
            r['news'] = fetch_news(sid, name, days=3)
            r['chip'] = fetch_chip_data(sid, days=5)

            # 分點資料：只對「值得關注」的個股抓取，避免過度頻繁請求
            #   條件：BUY 訊號 或 RSI 落於 50~75 趨勢中段 或 三大法人淨買 > 0
            chip_total = (r.get('chip') or {}).get('合計', 0)
            worth_chip = (
                r.get('signal') == 'BUY'
                or (50 <= (r.get('rsi') or 0) <= 75)
                or chip_total > 0
            )
            if worth_chip and fetch_broker_top15 is not None:
                br = fetch_broker_top15(sid, period='5')
                r['broker'] = {
                    'top_buyers': [
                        {'name': b[0], 'lots': b[1], 'pct': b[2]}
                        for b in br.get('top_buyers', [])[:5]
                    ],
                    'top_sellers': [
                        {'name': s[0], 'lots': s[1], 'pct': s[2]}
                        for s in br.get('top_sellers', [])[:5]
                    ],
                    'net_concentration': br.get('net_concentration', 0),
                    'source': br.get('source'),
                    'error': br.get('error'),
                }
                r['main_force'] = main_force_score(br)
            else:
                r['broker'] = None
                r['main_force'] = None

            results.append(r)
        except Exception as e:
            results.append({'id': sid, 'name': name, 'error': str(e)})
        time.sleep(0.5)
    return results


def generate_sector_chart(all_results, out_dir):
    """族群相對強弱圖"""
    sector_data = []
    for sector, results in all_results.items():
        ok = [r for r in results if 'error' not in r and r.get('ret_20d') is not None]
        if ok:
            avg_ret = np.mean([r['ret_20d'] for r in ok]) * 100
            avg_rsi = np.mean([r['rsi'] for r in ok])
            avg_sharpe = np.mean([r['cv_sharpe'] for r in ok])
            sector_data.append({
                'sector': sector, 'ret_20d': avg_ret,
                'rsi': avg_rsi, 'sharpe': avg_sharpe, 'count': len(ok)
            })

    if not sector_data:
        return None

    df = pd.DataFrame(sector_data).sort_values('ret_20d', ascending=True)

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    # 左：20 日報酬
    colors = ['#e74c3c' if x < 0 else '#27ae60' for x in df['ret_20d']]
    axes[0].barh(df['sector'], df['ret_20d'], color=colors)
    axes[0].set_xlabel('近 20 日平均報酬 (%)')
    axes[0].set_title('族群相對強弱')
    axes[0].axvline(0, color='black', linewidth=0.5)
    for i, v in enumerate(df['ret_20d']):
        axes[0].text(v, i, f' {v:+.1f}%', va='center',
                     ha='left' if v >= 0 else 'right')

    # 右：RSI 分布
    rsi_colors = ['#e74c3c' if r > 70 else '#f39c12' if r > 60 else '#27ae60' if 40 <= r <= 60 else '#3498db'
                  for r in df['rsi']]
    axes[1].barh(df['sector'], df['rsi'], color=rsi_colors)
    axes[1].set_xlabel('RSI (5)')
    axes[1].set_title('族群 RSI 熱度')
    axes[1].axvline(70, color='red', linestyle='--', linewidth=0.7, alpha=0.5)
    axes[1].axvline(30, color='green', linestyle='--', linewidth=0.7, alpha=0.5)
    axes[1].set_xlim(0, 100)

    plt.tight_layout()
    path = out_dir / '01_sector_strength.png'
    plt.savefig(path, dpi=100, bbox_inches='tight')
    plt.close()
    return str(path)


def run_daily_scan():
    today = datetime.now().strftime('%Y%m%d')
    out_dir = Path(f'daily_reports/{today}')
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*78}")
    print(f"  每日掃描 | {datetime.now():%Y-%m-%d %H:%M}")
    print(f"  輸出目錄：{out_dir}")
    print(f"{'='*78}\n")

    # 1. 大盤
    print("📊 抓取大盤數據...")
    market = fetch_market_overview()

    # 2. 掃描各族群
    all_results = {}
    for sector_name, stocks in SECTORS.items():
        print(f"\n🏭 掃描族群：{sector_name}（{len(stocks)} 檔）...")
        all_results[sector_name] = scan_sector(sector_name, stocks)

    # 3. 產生圖表
    print("\n📈 產生圖表...")
    chart_path = generate_sector_chart(all_results, out_dir)

    # 4. 整合摘要
    summary = build_summary(today, market, all_results, chart_path)

    # 5. 輸出 JSON + Markdown
    json_path = out_dir / 'summary.json'
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    md_path = out_dir / 'summary.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(summary_to_markdown(summary))

    print(f"\n✅ 完成")
    print(f"   JSON：{json_path}")
    print(f"   MD：  {md_path}")
    print(f"   圖表：{chart_path}")

    # Notion 上傳
    try:
        from notion_upload import upload_daily_report as notion_upload
        page_id = notion_upload(summary)
        print(f"   Notion：{page_id}")
    except Exception as e:
        print(f"   Notion 上傳失敗：{e}")

    # Google Sheets 上傳
    try:
        from gsheets_upload import upload_daily_report as sheets_upload
        sheets_upload(summary)
    except Exception as e:
        print(f"   Google Sheets 上傳失敗：{e}")

    # 寫入 GitHub Pages 靜態資料（含歷史每日 JSON）
    try:
        from build_docs import build_all as _build_docs
        _build_docs()
        print('  docs/ 歷史資料已更新')
    except Exception as e:
        docs_dir = Path(__file__).parent.parent / 'docs'
        docs_dir.mkdir(exist_ok=True)
        with open(docs_dir / 'daily.json', 'w', encoding='utf-8') as f:
            json.dump(build_daily_payload(summary), f, ensure_ascii=False, indent=2, default=str)
        print(f'  docs/daily.json 已更新（build_docs 失敗：{e}）')

    print(f"\n{'='*78}\n")
    return summary


def _nan_to_none(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


def build_daily_payload(summary):
    sectors = []
    chips = []
    stocks = []
    main_force = []   # 獨立陣列，給「主力觀察」分頁使用
    for sector, data in summary.get('sectors', {}).items():
        sectors.append({
            'sector': sector,
            'ret20': _nan_to_none(data.get('avg_ret_20d', '')),
            'rsi': _nan_to_none(data.get('avg_rsi', '')),
            'buy': data.get('buy_count', 0),
            'hot': data.get('hot_count', 0),
        })
        for st in data.get('stocks', []):
            chip = st.get('chip', {})
            stocks.append({
                'date': summary['date'],
                'sector': sector,
                'id': st['id'],
                'name': st['name'],
                'price': _nan_to_none(st.get('price', '')),
                'rsi': _nan_to_none(st.get('rsi', '')),
                'rsi10': _nan_to_none(st.get('rsi10', '')),
                'ret20': _nan_to_none(st.get('ret_20d', '')),
                'signal': st.get('signal', ''),
                'sharpe': _nan_to_none(st.get('cv_sharpe', '')),
                'foreign': chip.get('外資', ''),
                'trust': chip.get('投信', ''),
                'dealer': chip.get('自營', ''),
                'chipTotal': chip.get('合計', ''),
                'news': ' / '.join(n['title'] for n in st.get('news', [])[:2]),
            })
            # 主力觀察資料（含 ATR 停損、分點、主力分）— 獨立給新分頁用
            mf = st.get('main_force') or {}
            br = st.get('broker') or {}
            if (
                st.get('atr14') is not None
                or st.get('stop_loss') is not None
                or mf.get('score') is not None
                or br.get('source')
            ):
                main_force.append({
                    'id': st['id'],
                    'name': st['name'],
                    'sector': sector,
                    'price': _nan_to_none(st.get('price', '')),
                    'signal': st.get('signal', ''),
                    'atr14': st.get('atr14'),
                    'stopLoss': st.get('stop_loss'),
                    'mainForceScore': mf.get('score'),
                    'mainForceLabel': mf.get('label'),
                    'top1Broker': mf.get('top1_broker'),
                    'top1Lots': mf.get('top1_lots'),
                    'top5BuyLots': mf.get('top5_buy_lots'),
                    'concentration': mf.get('concentration'),
                    'brokerNet': br.get('net_concentration'),
                    'topBuyers': br.get('top_buyers') or [],
                    'topSellers': br.get('top_sellers') or [],
                    'brokerSource': br.get('source'),
                    'brokerError': br.get('error'),
                })
            if chip.get('合計', 0):
                chips.append({
                    'id': st['id'], 'name': st['name'], 'sector': sector,
                    'total': chip.get('合計', 0),
                    'foreign': chip.get('外資', 0),
                    'trust': chip.get('投信', 0),
                    'dealer': chip.get('自營', 0),
                })
    mkt = summary.get('market', {})
    # 主力觀察依分數排序（高→低），分數 None 排最後
    main_force_sorted = sorted(
        main_force,
        key=lambda x: (x.get('mainForceScore') is None, -(x.get('mainForceScore') or 0)),
    )
    return {
        'meta': {
            '掃描日期': summary.get('date', ''),
            '加權指數': mkt.get('加權指數', ''),
            '漲跌幅%': mkt.get('漲跌幅', ''),
            '強勢族群': ', '.join(summary.get('strong_sectors', [])),
            '弱勢族群': ', '.join(summary.get('weak_sectors', [])),
        },
        'sectors': sectors,
        'chips': sorted(chips, key=lambda x: x['total'], reverse=True),
        'stocks': stocks,
        'mainForce': main_force_sorted,
    }


def build_summary(date, market, all_results, chart_path):
    sectors_summary = {}
    all_strong = []  # 強勢股
    all_weak = []    # 弱勢股
    all_qualified = []  # 雙條件達標
    all_alerts = []  # 風險警示

    for sector, results in all_results.items():
        ok = [r for r in results if 'error' not in r]
        if not ok:
            continue
        df = pd.DataFrame(ok)

        avg_ret = df['ret_20d'].dropna().mean() * 100 if len(df['ret_20d'].dropna()) > 0 else 0
        avg_rsi = df['rsi'].mean()
        avg_sharpe = df['cv_sharpe'].mean()

        # 當日漲跌（從 analyze_stock 若有提供）
        buy_signals = df[df['signal'] == 'BUY']
        qualified = df[(df['cv_sharpe'] >= 0.3) & (df['cv_win_rate'] >= 0.4)]

        # 過熱個股
        hot = df[df['rsi'] > 70]

        sectors_summary[sector] = {
            'avg_ret_20d': round(avg_ret, 2),
            'avg_rsi': round(avg_rsi, 1),
            'avg_sharpe': round(avg_sharpe, 2),
            'stocks': [
                {
                    'id': r['id'], 'name': r['name'], 'price': r['price'],
                    'rsi': round(r['rsi'], 1),
                    'rsi10': round(r['rsi10'], 1) if r.get('rsi10') else None,
                    'ret_20d': round(r['ret_20d'] * 100, 1) if r.get('ret_20d') else None,
                    'signal': r['signal'],
                    'cv_sharpe': round(r['cv_sharpe'], 2),
                    'cv_win_rate': round(r['cv_win_rate'], 2),
                    'news': r.get('news', []),
                    'chip': r.get('chip', {}),
                    'target_short': r.get('target_short'),
                    'target_mid':   r.get('target_mid'),
                    'target_long':  r.get('target_long'),
                    'atr14':        r.get('atr14'),
                    'stop_loss':    r.get('stop_loss'),
                    'broker':       r.get('broker'),
                    'main_force':   r.get('main_force'),
                }
                for _, r in df.iterrows()
            ],
            'hot_count': len(hot),
            'buy_count': len(buy_signals),
            'qualified_count': len(qualified),
        }

        # 整體強弱判定
        if avg_ret > 3:
            all_strong.append(sector)
        elif avg_ret < -3:
            all_weak.append(sector)

        # 推薦名單
        final = df[(df['signal'] == 'BUY') & (df['cv_sharpe'] >= 0.3) &
                   (df['cv_win_rate'] >= 0.4) & (df['cv_max_dd'] <= 0.2)]
        for _, r in final.iterrows():
            all_qualified.append({
                'sector': sector, 'id': r['id'], 'name': r['name'],
                'price': r['price'], 'rsi': round(r['rsi'], 1),
                'cv_sharpe': round(r['cv_sharpe'], 2),
            })

        # 風險警示
        for _, r in hot.iterrows():
            all_alerts.append({
                'sector': sector, 'id': r['id'], 'name': r['name'],
                'type': 'RSI過熱', 'detail': f"RSI={r['rsi']:.1f}"
            })

    return {
        'date': date,
        'timestamp': datetime.now().isoformat(),
        'market': market,
        'sectors': sectors_summary,
        'strong_sectors': all_strong,
        'weak_sectors': all_weak,
        'qualified': all_qualified,
        'alerts': all_alerts,
        'chart_path': chart_path,
    }


def summary_to_markdown(s):
    md = [f"# 每日掃描 {s['date']}\n"]
    md.append(f"**時間**：{s['timestamp']}\n")

    if s['market'].get('加權指數'):
        md.append(f"\n## 大盤\n")
        md.append(f"- 加權指數：{s['market']['加權指數']:.2f}  "
                  f"({s['market']['漲跌幅']:+.2f}%)\n")

    md.append(f"\n## 族群強弱\n")
    md.append(f"- 🟢 強勢：{', '.join(s['strong_sectors']) or '無'}\n")
    md.append(f"- 🔴 弱勢：{', '.join(s['weak_sectors']) or '無'}\n")

    md.append(f"\n## 雙條件達標推薦（{len(s['qualified'])} 檔）\n")
    if s['qualified']:
        for q in s['qualified']:
            md.append(f"- [{q['sector']}] {q['id']} {q['name']}  "
                      f"價={q['price']}  RSI={q['rsi']}  夏普={q['cv_sharpe']}\n")
    else:
        md.append("- 無\n")

    md.append(f"\n## 風險警示\n")
    if s['alerts']:
        for a in s['alerts'][:10]:
            md.append(f"- [{a['sector']}] {a['id']} {a['name']} — {a['type']} ({a['detail']})\n")
    else:
        md.append("- 無\n")

    md.append(f"\n## 各族群明細\n")
    for sector, data in s['sectors'].items():
        md.append(f"\n### {sector}\n")
        md.append(f"- 平均 20 日報酬：{data['avg_ret_20d']:+.1f}%\n")
        md.append(f"- 平均 RSI：{data['avg_rsi']:.1f}\n")
        md.append(f"- BUY 訊號：{data['buy_count']} 檔 | CV達標：{data['qualified_count']} 檔 | RSI>70：{data['hot_count']} 檔\n")
        md.append(f"\n| 代碼 | 名稱 | 現價 | RSI | 20日% | 信號 | CV夏普 |\n")
        md.append(f"|---|---|---|---|---|---|---|\n")
        for st in data['stocks']:
            ret = f"{st['ret_20d']:+.1f}" if st['ret_20d'] is not None else 'N/A'
            md.append(f"| {st['id']} | {st['name']} | {st['price']} | "
                      f"{st['rsi']} | {ret} | {st['signal']} | {st['cv_sharpe']} |\n")

    return ''.join(md)


if __name__ == '__main__':
    run_daily_scan()
