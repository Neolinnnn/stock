"""
歷史資料回補腳本
根據 FinMind 日 K 資料，為過去 N 個交易日重建 summary.json / summary.md。
已存在的日期會跳過（可用 --force 強制重寫）。

用法：
    python strategy_templates/backfill_history.py          # 補足前 20 個交易日
    python strategy_templates/backfill_history.py --days 30
    python strategy_templates/backfill_history.py --force
"""
import sys, os, json, argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import rcParams

sys.path.insert(0, os.path.dirname(__file__))
from finmind_client import get_dataloader

rcParams['font.sans-serif'] = ['Microsoft JhengHei', 'SimHei', 'Arial Unicode MS']
rcParams['axes.unicode_minus'] = False

# ── 族群定義（與 07_daily_scan.py 相同） ──────────────────────────────────────
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
}

ALL_STOCK_IDS = [sid for s in SECTORS.values() for sid in s]

MA_SHORT   = 5
MA_LONG    = 20
RSI_PERIOD = 14
CV_FOLDS   = 3


# ── 技術指標工具 ──────────────────────────────────────────────────────────────

def sma(prices, window):
    result = [None] * (window - 1)
    for i in range(window - 1, len(prices)):
        result.append(sum(prices[i - window + 1:i + 1]) / window)
    return result


def calc_rsi(prices, period=14):
    rsi = [None] * period
    for i in range(period, len(prices)):
        changes = [prices[j] - prices[j-1] for j in range(i - period + 1, i + 1)]
        gains  = [c for c in changes if c > 0]
        losses = [abs(c) for c in changes if c < 0]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period if losses else 0
        rs = avg_gain / avg_loss if avg_loss else (100 if avg_gain else 0)
        rsi.append(100 - 100 / (1 + rs))
    return rsi


def generate_signals(prices, short_ma, long_ma, rsi_vals,
                     rsi_low=35, rsi_high=65):
    signals = []
    for i in range(1, len(prices)):
        if any(v is None for v in [short_ma[i], short_ma[i-1],
                                    long_ma[i],  long_ma[i-1],
                                    rsi_vals[i]]):
            continue
        ma_cross_up   = short_ma[i-1] <= long_ma[i-1] and short_ma[i] > long_ma[i]
        ma_cross_down = short_ma[i-1] >= long_ma[i-1] and short_ma[i] < long_ma[i]
        if ma_cross_up and rsi_vals[i] < rsi_high:
            signals.append({'idx': i, 'signal': 'BUY'})
        elif ma_cross_down and rsi_vals[i] > rsi_low:
            signals.append({'idx': i, 'signal': 'SELL'})
    return signals


def backtest(signals, prices, initial=100_000, cost=0.001425):
    capital, shares, equity = initial, 0, [initial]
    wins, trades, buy_price = 0, 0, 0
    for s in signals:
        p = prices[s['idx']]
        if s['signal'] == 'BUY' and capital > 0:
            num = int(capital / p / 1000) * 1000
            if num == 0:
                continue
            capital -= num * p * (1 + cost)
            shares, buy_price, trades = num, p, trades + 1
        elif s['signal'] == 'SELL' and shares > 0:
            capital += shares * p * (1 - cost)
            if p > buy_price:
                wins += 1
            shares, trades = 0, trades + 1
        equity.append(capital + shares * p)
    eq = np.array(equity)
    peak = np.maximum.accumulate(eq)
    dd = np.max((peak - eq) / np.where(peak == 0, 1, peak))
    rets = np.diff(eq) / eq[:-1]
    sharpe = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    n_complete = trades // 2
    win_rate = wins / n_complete if n_complete > 0 else 0
    return {'sharpe': sharpe, 'max_dd': dd, 'win_rate': win_rate}


def walk_forward_cv(prices, n_folds=CV_FOLDS):
    n = len(prices)
    fold_size = n // (n_folds + 1)
    results = []
    for fold in range(1, n_folds + 1):
        train_end = fold * fold_size
        test_end  = train_end + fold_size
        tp = prices[train_end:test_end]
        if len(tp) < MA_LONG + RSI_PERIOD + 5:
            continue
        s_ma = sma(tp, MA_SHORT)
        l_ma = sma(tp, MA_LONG)
        r    = calc_rsi(tp, RSI_PERIOD)
        sigs = generate_signals(tp, s_ma, l_ma, r)
        if not sigs:
            results.append({'sharpe': 0, 'max_dd': 0, 'win_rate': 0})
        else:
            results.append(backtest(sigs, tp))
    return results


# ── 擷取股價快取 ──────────────────────────────────────────────────────────────

def fetch_all_prices(dl, start_date, end_date):
    """批次取得所有股票從 start_date 到 end_date 的日 K。
    回傳 dict: stock_id -> DataFrame (date, close)
    """
    print(f"  📥 下載日 K：{start_date} ~ {end_date}（{len(ALL_STOCK_IDS)} 檔）")
    cache = {}
    for sid in ALL_STOCK_IDS:
        try:
            df = dl.taiwan_stock_daily(
                stock_id=sid,
                start_date=start_date,
                end_date=end_date,
            )
            if df.empty:
                cache[sid] = pd.DataFrame(columns=['date', 'close'])
                continue
            df['date'] = pd.to_datetime(df['date']).dt.date
            df = df.sort_values('date')[['date', 'close']].reset_index(drop=True)
            cache[sid] = df
        except Exception as e:
            print(f"    ⚠️  {sid} 下載失敗：{e}")
            cache[sid] = pd.DataFrame(columns=['date', 'close'])
    return cache


def fetch_chip_history(dl, start_date, end_date):
    """批次取得三大法人資料。回傳 dict: (stock_id, date) -> {外資,投信,自營,合計}"""
    print(f"  📥 下載籌碼資料（三大法人）")
    chip_map = {}
    for sid in ALL_STOCK_IDS:
        try:
            df = dl.taiwan_stock_institutional_investors(
                stock_id=sid,
                start_date=start_date,
                end_date=end_date,
            )
            if df.empty:
                continue
            df['date'] = pd.to_datetime(df['date']).dt.date
            for date, grp in df.groupby('date'):
                rec = {'date': str(date)}
                for _, row in grp.iterrows():
                    nm = row.get('name', '')
                    net = int(row.get('buy', 0)) - int(row.get('sell', 0))
                    if '外資' in nm:
                        rec['外資'] = net
                    elif '投信' in nm:
                        rec['投信'] = net
                    elif '自營' in nm:
                        rec['自營'] = net
                rec['合計'] = rec.get('外資', 0) + rec.get('投信', 0) + rec.get('自營', 0)
                chip_map[(sid, date)] = rec
        except Exception as e:
            print(f"    ⚠️  {sid} 籌碼下載失敗：{e}")
    return chip_map


# ── 單日單股分析 ──────────────────────────────────────────────────────────────

def analyze_stock_on_date(sid, name, target_date, price_df, chip_map):
    """使用截至 target_date 的資料計算指標。"""
    df = price_df[price_df['date'] <= target_date].tail(500)
    if len(df) < MA_LONG + RSI_PERIOD + 10:
        return {'id': sid, 'name': name, 'error': '資料不足'}

    prices = df['close'].tolist()
    latest_price = prices[-1]

    s_ma   = sma(prices, MA_SHORT)
    l_ma   = sma(prices, MA_LONG)
    rsi_v  = calc_rsi(prices, RSI_PERIOD)

    latest_rsi = rsi_v[-1] if rsi_v[-1] is not None else 50.0

    # 當前信號（最後 6 根）
    recent_sigs = generate_signals(prices[-6:], s_ma[-6:], l_ma[-6:], rsi_v[-6:])
    current_signal = recent_sigs[-1]['signal'] if recent_sigs else 'HOLD'

    # 20 日報酬
    ret_20d = None
    if len(prices) >= 21:
        p20 = prices[-21]
        ret_20d = (latest_price - p20) / p20 if p20 else None

    # Walk-Forward CV
    cv_results = walk_forward_cv(prices)
    if cv_results:
        avg_sharpe = np.mean([r['sharpe']   for r in cv_results])
        avg_dd     = np.mean([r['max_dd']   for r in cv_results])
        avg_wr     = np.mean([r['win_rate'] for r in cv_results])
    else:
        avg_sharpe = avg_dd = avg_wr = 0.0

    # 籌碼
    chip = chip_map.get((sid, target_date), {})

    return {
        'id': sid, 'name': name,
        'price': latest_price,
        'rsi': round(latest_rsi, 1),
        'ret_20d': ret_20d,
        'signal': current_signal,
        'cv_sharpe': round(avg_sharpe, 2),
        'cv_win_rate': round(avg_wr, 2),
        'cv_max_dd': round(avg_dd, 2),
        'news': [],
        'chip': chip,
    }


# ── 族群圖表 ──────────────────────────────────────────────────────────────────

def generate_sector_chart(all_results, out_dir):
    sector_data = []
    for sector, results in all_results.items():
        ok = [r for r in results if 'error' not in r and r.get('ret_20d') is not None]
        if ok:
            sector_data.append({
                'sector': sector,
                'ret_20d': np.mean([r['ret_20d'] for r in ok]) * 100,
                'rsi': np.mean([r['rsi'] for r in ok]),
            })
    if not sector_data:
        return None

    df = pd.DataFrame(sector_data).sort_values('ret_20d', ascending=True)
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    colors = ['#e74c3c' if x < 0 else '#27ae60' for x in df['ret_20d']]
    axes[0].barh(df['sector'], df['ret_20d'], color=colors)
    axes[0].set_xlabel('近 20 日平均報酬 (%)')
    axes[0].set_title('族群相對強弱')
    axes[0].axvline(0, color='black', linewidth=0.5)
    for i, v in enumerate(df['ret_20d']):
        axes[0].text(v, i, f' {v:+.1f}%', va='center',
                     ha='left' if v >= 0 else 'right')

    rsi_colors = ['#e74c3c' if r > 70 else '#f39c12' if r > 60
                  else '#27ae60' if 40 <= r <= 60 else '#3498db'
                  for r in df['rsi']]
    axes[1].barh(df['sector'], df['rsi'], color=rsi_colors)
    axes[1].set_xlabel('RSI (14)')
    axes[1].set_title('族群 RSI 熱度')
    axes[1].axvline(70, color='red', linestyle='--', linewidth=0.7, alpha=0.5)
    axes[1].axvline(30, color='green', linestyle='--', linewidth=0.7, alpha=0.5)
    axes[1].set_xlim(0, 100)

    plt.tight_layout()
    path = out_dir / '01_sector_strength.png'
    plt.savefig(path, dpi=100, bbox_inches='tight')
    plt.close()
    return str(path)


# ── 彙整 summary ──────────────────────────────────────────────────────────────

def build_summary(date_str, all_results, chart_path):
    sectors_summary = {}
    all_strong, all_weak, all_qualified, all_alerts = [], [], [], []

    for sector, results in all_results.items():
        ok = [r for r in results if 'error' not in r]
        if not ok:
            continue
        df = pd.DataFrame(ok)

        rets = df['ret_20d'].dropna()
        avg_ret   = rets.mean() * 100 if len(rets) > 0 else 0
        avg_rsi   = df['rsi'].mean()
        avg_sharpe = df['cv_sharpe'].mean()

        buy_signals = df[df['signal'] == 'BUY']
        qualified   = df[(df['cv_sharpe'] >= 0.3) & (df['cv_win_rate'] >= 0.4)]
        hot         = df[df['rsi'] > 70]

        sectors_summary[sector] = {
            'avg_ret_20d': round(avg_ret, 2),
            'avg_rsi': round(avg_rsi, 1),
            'avg_sharpe': round(avg_sharpe, 2),
            'hot_count': len(hot),
            'buy_count': len(buy_signals),
            'qualified_count': len(qualified),
            'stocks': [
                {
                    'id': r['id'], 'name': r['name'],
                    'price': r['price'],
                    'rsi': r['rsi'],
                    'ret_20d': round(r['ret_20d'] * 100, 1) if r.get('ret_20d') is not None else None,
                    'signal': r['signal'],
                    'cv_sharpe': r['cv_sharpe'],
                    'cv_win_rate': r['cv_win_rate'],
                    'news': r.get('news', []),
                    'chip': r.get('chip', {}),
                }
                for _, r in df.iterrows()
            ],
        }

        if avg_ret > 3:
            all_strong.append(sector)
        elif avg_ret < -3:
            all_weak.append(sector)

        final = df[
            (df['signal'] == 'BUY') &
            (df['cv_sharpe'] >= 0.3) &
            (df['cv_win_rate'] >= 0.4) &
            (df['cv_max_dd'] <= 0.2)
        ]
        for _, r in final.iterrows():
            all_qualified.append({
                'sector': sector, 'id': r['id'], 'name': r['name'],
                'price': r['price'], 'rsi': r['rsi'], 'cv_sharpe': r['cv_sharpe'],
            })

        for _, r in hot.iterrows():
            all_alerts.append({
                'sector': sector, 'id': r['id'], 'name': r['name'],
                'type': 'RSI過熱', 'detail': f"RSI={r['rsi']:.1f}",
            })

    return {
        'date': date_str,
        'timestamp': datetime.now().isoformat(),
        'market': {'加權指數': None, '漲跌幅': None},
        'sectors': sectors_summary,
        'strong_sectors': all_strong,
        'weak_sectors': all_weak,
        'qualified': all_qualified,
        'alerts': all_alerts,
        'chart_path': chart_path,
    }


def summary_to_markdown(s):
    md = [f"# 每日掃描 {s['date']}\n",
          f"**時間**：{s['timestamp']}\n",
          f"\n## 族群強弱\n",
          f"- 🟢 強勢：{', '.join(s['strong_sectors']) or '無'}\n",
          f"- 🔴 弱勢：{', '.join(s['weak_sectors']) or '無'}\n",
          f"\n## 雙條件達標推薦（{len(s['qualified'])} 檔）\n"]
    for q in s['qualified']:
        md.append(f"- [{q['sector']}] {q['id']} {q['name']}  "
                  f"價={q['price']}  RSI={q['rsi']}  夏普={q['cv_sharpe']}\n")
    if not s['qualified']:
        md.append("- 無\n")
    md.append(f"\n## 風險警示\n")
    for a in s['alerts'][:10]:
        md.append(f"- [{a['sector']}] {a['id']} {a['name']} — {a['type']} ({a['detail']})\n")
    if not s['alerts']:
        md.append("- 無\n")
    md.append(f"\n## 各族群明細\n")
    for sector, data in s['sectors'].items():
        md.append(f"\n### {sector}\n")
        md.append(f"- 平均 20 日報酬：{data['avg_ret_20d']:+.1f}%\n")
        md.append(f"- 平均 RSI：{data['avg_rsi']:.1f}\n")
        md.append(f"- BUY 訊號：{data['buy_count']} 檔 | CV達標：{data['qualified_count']} 檔 | RSI>70：{data['hot_count']} 檔\n")
        md.append(f"\n| 代碼 | 名稱 | 現價 | RSI | 20日% | 信號 | CV夏普 |\n|---|---|---|---|---|---|---|\n")
        for st in data['stocks']:
            ret = f"{st['ret_20d']:+.1f}" if st['ret_20d'] is not None else 'N/A'
            md.append(f"| {st['id']} | {st['name']} | {st['price']} | "
                      f"{st['rsi']} | {ret} | {st['signal']} | {st['cv_sharpe']} |\n")
    return ''.join(md)


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=20, help='回補幾個交易日（預設 20）')
    parser.add_argument('--force', action='store_true', help='強制重寫已存在的日期')
    args = parser.parse_args()

    dl = get_dataloader()

    # 用 2330 取得近期交易日清單
    today = datetime.now().date()
    history_start = today - timedelta(days=int(args.days * 2))  # 保留足夠空間含假日
    ref_df = dl.taiwan_stock_daily(
        stock_id='2330',
        start_date=str(history_start),
        end_date=str(today),
    )
    ref_df['date'] = pd.to_datetime(ref_df['date']).dt.date
    trading_days = sorted(ref_df['date'].unique().tolist())

    # 取最近 N 個交易日（不含今天若今天尚未收盤）
    trading_days = [d for d in trading_days if d < today][-args.days:]
    print(f"\n需回補的交易日（{len(trading_days)} 天）：")
    for d in trading_days:
        status = "✅ 已存在" if (Path(f"daily_reports/{d.strftime('%Y%m%d')}/summary.json")).exists() else "🔄 待補"
        print(f"  {d}  {status}")

    # 過濾出需要補的日期
    target_days = [
        d for d in trading_days
        if args.force or not (Path(f"daily_reports/{d.strftime('%Y%m%d')}/summary.json")).exists()
    ]
    if not target_days:
        print("\n✅ 所有日期已存在，無需補充。加 --force 可強制重寫。")
        return

    print(f"\n開始補充 {len(target_days)} 個交易日...\n")

    # 計算需要的歷史起始日（ret_20d 需要再往前 30 個交易日 + CV 資料 500 天）
    fetch_start = (target_days[0] - timedelta(days=600)).strftime('%Y-%m-%d')
    fetch_end   = target_days[-1].strftime('%Y-%m-%d')

    # 批次下載資料
    price_cache = fetch_all_prices(dl, fetch_start, fetch_end)
    chip_cache  = fetch_chip_history(dl, (target_days[0] - timedelta(days=10)).strftime('%Y-%m-%d'), fetch_end)

    # 逐日處理
    for target_date in target_days:
        date_str = target_date.strftime('%Y%m%d')
        out_dir = Path(f'daily_reports/{date_str}')
        out_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n{'─'*60}")
        print(f"  📅  {target_date}  →  daily_reports/{date_str}/")

        all_results = {}
        for sector_name, stocks in SECTORS.items():
            sector_results = []
            for sid, name in stocks.items():
                pdf = price_cache.get(sid, pd.DataFrame())
                r = analyze_stock_on_date(sid, name, target_date, pdf, chip_cache)
                sector_results.append(r)
            all_results[sector_name] = sector_results
            ok = len([r for r in sector_results if 'error' not in r])
            print(f"    {sector_name}: {ok}/{len(stocks)} 檔 ✓")

        chart_path = generate_sector_chart(all_results, out_dir)
        summary = build_summary(date_str, all_results, chart_path)

        with open(out_dir / 'summary.json', 'w', encoding='utf-8') as f:
            json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
        with open(out_dir / 'summary.md', 'w', encoding='utf-8') as f:
            f.write(summary_to_markdown(summary))

        strong = ', '.join(summary['strong_sectors']) or '無'
        weak   = ', '.join(summary['weak_sectors']) or '無'
        print(f"    強勢：{strong}  弱勢：{weak}")
        print(f"    ✅  {out_dir}/summary.json 已寫入")

    print(f"\n{'='*60}")
    print(f"  回補完成！共 {len(target_days)} 個交易日。")
    print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
