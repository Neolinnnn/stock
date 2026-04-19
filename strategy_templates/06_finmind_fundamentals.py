"""
FinMind 基本面 + 籌碼面抓取
Fundamentals & Institutional Investors Data

用法：
    python strategy_templates/06_finmind_fundamentals.py 4906
"""
import sys
from datetime import datetime, timedelta
from FinMind.data import DataLoader
import pandas as pd

pd.set_option('display.unicode.east_asian_width', True)
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 140)


def analyze(stock_id: str):
    print(f"\n{'='*78}")
    print(f"  FinMind 基本面 + 籌碼面綜合  |  {stock_id}  |  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*78}\n")

    dl = DataLoader()
    end   = datetime.now().strftime('%Y-%m-%d')
    start = (datetime.now() - timedelta(days=800)).strftime('%Y-%m-%d')
    start_short = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    # ── 1. 月營收 ─────────────────────────────────────────────────────
    print("【📅 月營收（近 12 個月）】")
    try:
        rev = dl.taiwan_stock_month_revenue(stock_id=stock_id,
                                            start_date=start, end_date=end)
        if not rev.empty:
            rev = rev.sort_values('date').tail(12).copy()
            rev['revenue_億'] = (rev['revenue'] / 1e8).round(2)
            rev['月增%'] = rev['revenue'].pct_change() * 100
            rev['年增%'] = rev['revenue_year_growth'] if 'revenue_year_growth' in rev.columns \
                          else rev['revenue'].pct_change(12) * 100
            print(rev[['date', 'revenue_億', '月增%', '年增%']].to_string(index=False))

            latest = rev.iloc[-1]
            print(f"\n  最新月營收：{latest['revenue_億']:.2f} 億  "
                  f"年增 {latest['年增%']:+.1f}%")
        else:
            print("  無資料")
    except Exception as e:
        print(f"  取得失敗：{e}")

    # ── 2. EPS / 財報 ─────────────────────────────────────────────────
    print("\n【💰 每股盈餘 EPS（近 8 季）】")
    try:
        fin = dl.taiwan_stock_financial_statement(stock_id=stock_id,
                                                  start_date=start, end_date=end)
        if not fin.empty:
            eps = fin[fin['type'].str.contains('EPS', case=False, na=False)]
            if eps.empty:
                # 部分版本欄位為 'EPS' 直接值
                eps = fin[fin['type'] == 'EPS']
            if not eps.empty:
                eps = eps.sort_values('date').tail(8).copy()
                eps = eps[['date', 'value']].rename(columns={'value': 'EPS'})
                print(eps.to_string(index=False))
                ttm = eps['EPS'].tail(4).sum()
                print(f"\n  近 4 季 EPS 合計 (TTM)：{ttm:.2f}")
            else:
                print("  找不到 EPS 類別，顯示原始前 5 列：")
                print(fin.head().to_string(index=False))
        else:
            print("  無資料")
    except Exception as e:
        print(f"  取得失敗：{e}")

    # ── 3. 三大法人買賣超（近 20 日）──────────────────────────────────
    print("\n【🏦 三大法人買賣超（近 20 交易日）】")
    try:
        ii = dl.taiwan_stock_institutional_investors(
            stock_id=stock_id, start_date=start_short, end_date=end)
        if not ii.empty:
            pivot = ii.pivot_table(index='date', columns='name',
                                    values='buy', aggfunc='sum', fill_value=0)
            sell  = ii.pivot_table(index='date', columns='name',
                                    values='sell', aggfunc='sum', fill_value=0)
            net = (pivot - sell)
            # 只留主要法人
            keep = [c for c in net.columns if any(k in c for k in
                    ['Foreign', '外資', 'Investment', '投信', 'Dealer', '自營'])]
            net = net[keep] / 1000  # 張
            net['合計'] = net.sum(axis=1)
            net = net.sort_index().tail(20).round(0).astype(int)
            print(net.to_string())

            total20 = net['合計'].sum()
            buy_days = (net['合計'] > 0).sum()
            print(f"\n  近 20 日合計買賣超：{total20:+,d} 張  "
                  f"|  買超天數 {buy_days}/20  "
                  f"|  {'籌碼偏多 ✅' if total20 > 0 else '籌碼偏空 ⚠️'}")
        else:
            print("  無資料")
    except Exception as e:
        print(f"  取得失敗：{e}")

    # ── 4. 外資持股比率 ────────────────────────────────────────────────
    print("\n【🌏 外資持股比率趨勢】")
    try:
        sh = dl.taiwan_stock_shareholding(
            stock_id=stock_id, start_date=start_short, end_date=end)
        if not sh.empty:
            sh = sh.sort_values('date').tail(10)
            cols = [c for c in sh.columns if c in
                    ['date', 'ForeignInvestmentSharesRatio',
                     'ForeignInvestmentRemainRatio']]
            print(sh[cols].to_string(index=False))
        else:
            print("  無資料")
    except Exception as e:
        print(f"  取得失敗：{e}")

    # ── 5. 融資融券 ────────────────────────────────────────────────────
    print("\n【📉 融資融券餘額（近 10 日）】")
    try:
        mg = dl.taiwan_stock_margin_purchase_short_sale(
            stock_id=stock_id, start_date=start_short, end_date=end)
        if not mg.empty:
            mg = mg.sort_values('date').tail(10)
            cols = [c for c in mg.columns if c in
                    ['date', 'MarginPurchaseTodayBalance',
                     'ShortSaleTodayBalance']]
            print(mg[cols].to_string(index=False))
        else:
            print("  無資料")
    except Exception as e:
        print(f"  取得失敗：{e}")

    # ── 6. PER / PBR ──────────────────────────────────────────────────
    print("\n【📊 本益比 / 股價淨值比 / 殖利率（近 5 日）】")
    try:
        pe = dl.taiwan_stock_per_pbr(
            stock_id=stock_id, start_date=start_short, end_date=end)
        if not pe.empty:
            print(pe.sort_values('date').tail(5).to_string(index=False))
        else:
            print("  無資料")
    except Exception as e:
        print(f"  取得失敗：{e}")

    print(f"\n{'='*78}\n")


if __name__ == '__main__':
    sid = sys.argv[1] if len(sys.argv) > 1 else '4906'
    analyze(sid)
