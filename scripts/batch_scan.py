"""
台股量化交易 - 範本 #3：多股批次掃描 + 走步前向交叉驗證
Batch Scanner with Walk-Forward Cross-Validation

策略：MA 黃金交叉 + RSI 雙重確認
驗證：時間序列 Walk-Forward CV（5 折）

用法：
    python strategy_templates/03_batch_scan_with_cv.py
"""

import twstock
from twstock import Stock
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import warnings
warnings.filterwarnings('ignore')


# ── 觀察清單（依族群分散） ───────────────────────────────────────────────────
WATCH_LIST = {
    # AI / 半導體先進製程 & 封裝
    '2330': '台積電',
    '3711': '日月光投控',
    '2303': '聯電',
    '6488': '環球晶',

    # AI 伺服器 ODM / 網通
    '2382': '廣達',
    '2357': '華碩',
    '3231': '緯創',
    '2345': '智邦',

    # 電力 / 散熱 / 基建
    '2308': '台達電',
    '1605': '華新',
    '3017': '奇鋐',

    # 金融（利率 + 殖利率防禦）
    '2881': '富邦金',
    '2882': '國泰金',

    # 傳產強勢 / 分散風險
    '1301': '台塑',
    '2912': '統一超',
    '9910': '豐泰',

    # 光通訊（CPO / 矽光子 / 光收發模組 / 砷化鎵）
    '3450': '聯鈞',
    '4979': '華星光',
    '3363': '上詮',
    '2455': '全新',
    '8086': '宏捷科',
    '3105': '穩懋',
}

# ── 策略參數 ─────────────────────────────────────────────────────────────────
MA_SHORT    = 5
MA_LONG     = 20
RSI_PERIOD  = 14
RSI_OVERSOLD   = 35   # 放寬到35，台股較少跌到30
RSI_OVERBOUGHT = 65
CV_FOLDS    = 3      # 折數減少，每折測試期更長（更多訊號機會）
DATA_DAYS   = 500    # 取 ~2年資料
INITIAL_CAPITAL = 100_000
TRANSACTION_COST = 0.001425  # 台股手續費+證交稅


# ── 計算工具函式 ──────────────────────────────────────────────────────────────

def sma(prices: list, window: int) -> list:
    result = [None] * (window - 1)
    for i in range(window - 1, len(prices)):
        result.append(sum(prices[i - window + 1:i + 1]) / window)
    return result


def calc_rsi(prices: list, period: int = 14) -> list:
    rsi = [None] * period
    for i in range(period, len(prices)):
        changes = [prices[j] - prices[j-1] for j in range(i - period + 1, i + 1)]
        gains = [c for c in changes if c > 0]
        losses = [abs(c) for c in changes if c < 0]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period if losses else 0
        rs = avg_gain / avg_loss if avg_loss else (100 if avg_gain else 0)
        rsi.append(100 - 100 / (1 + rs))
    return rsi


def generate_signals(prices, dates, short_ma, long_ma, rsi,
                     rsi_low=RSI_OVERSOLD, rsi_high=RSI_OVERBOUGHT,
                     initial_entry=False):
    """MA 黃金交叉 + RSI 雙重確認信號

    initial_entry=True: 測試期開頭若已在多頭排列 (MA5>MA20 且未超買)，
    視為策略已持倉，直接從第一個有效點進場，避免漏計強趨勢股。
    """
    signals = []

    if initial_entry:
        # 找第一個 MA/RSI 都有效的點，判斷是否直接進場
        for i in range(len(prices)):
            if any(v is None for v in [short_ma[i], long_ma[i], rsi[i]]):
                continue
            if short_ma[i] > long_ma[i] and rsi[i] < rsi_high:
                signals.append({'date': dates[i], 'price': prices[i], 'signal': 'BUY'})
            break  # 只在第一個有效點判斷一次

    for i in range(1, len(prices)):
        if any(v is None for v in [short_ma[i], short_ma[i-1],
                                    long_ma[i],  long_ma[i-1],
                                    rsi[i]]):
            continue

        ma_cross_up   = short_ma[i-1] <= long_ma[i-1] and short_ma[i] > long_ma[i]
        ma_cross_down = short_ma[i-1] >= long_ma[i-1] and short_ma[i] < long_ma[i]
        rsi_ok_buy    = rsi[i] < rsi_high
        rsi_ok_sell   = rsi[i] > rsi_low

        if ma_cross_up and rsi_ok_buy:
            signals.append({'date': dates[i], 'price': prices[i], 'signal': 'BUY'})
        elif ma_cross_down and rsi_ok_sell:
            signals.append({'date': dates[i], 'price': prices[i], 'signal': 'SELL'})
    return signals


def backtest(signals, initial_capital=INITIAL_CAPITAL, cost=TRANSACTION_COST):
    """簡單多空回測，回傳績效指標"""
    capital = initial_capital
    shares  = 0
    equity  = [initial_capital]
    wins    = 0
    trades  = 0
    buy_price = 0

    for sig in signals:
        price = sig['price']
        if sig['signal'] == 'BUY' and capital > 0:
            num = int(capital / price / 1000) * 1000
            if num == 0:
                continue
            capital -= num * price * (1 + cost)
            shares   = num
            buy_price = price
            trades  += 1
        elif sig['signal'] == 'SELL' and shares > 0:
            capital += shares * price * (1 - cost)
            if price > buy_price:
                wins += 1
            shares = 0
            trades += 1

        equity.append(capital + shares * price)

    final   = equity[-1]   # 已含 capital + 持股市值
    ret     = (final - initial_capital) / initial_capital
    eq_arr  = np.array(equity)
    peak    = np.maximum.accumulate(eq_arr)
    drawdown = np.max((peak - eq_arr) / np.where(peak == 0, 1, peak))

    rets    = np.diff(eq_arr) / eq_arr[:-1]
    sharpe  = (rets.mean() / rets.std() * np.sqrt(252)) if rets.std() > 0 else 0
    win_rate = wins / (trades // 2) if trades >= 2 else 0

    return {
        'return':    ret,
        'sharpe':    sharpe,
        'max_dd':    drawdown,
        'win_rate':  win_rate,
        'trades':    trades // 2,
    }


# ── Walk-Forward 交叉驗證 ─────────────────────────────────────────────────────

def walk_forward_cv(prices, dates, n_folds=CV_FOLDS):
    """
    時間序列走步前向交叉驗證。

    將資料切成 n_folds 段，每次以前段訓練、後段測試，
    確保未來資料不洩漏到訓練期（無 data leakage）。

    返回每折測試期績效的平均與標準差。
    """
    n = len(prices)
    fold_size = n // (n_folds + 1)
    results = []

    for fold in range(1, n_folds + 1):
        train_end = fold * fold_size
        test_end  = train_end + fold_size

        # 訓練期：用來決定參數是否穩定（本範本固定參數，可擴展）
        train_prices = prices[:train_end]
        train_dates  = dates[:train_end]

        # 測試期：評估真實績效
        test_prices = prices[train_end:test_end]
        test_dates  = dates[train_end:test_end]

        if len(test_prices) < MA_LONG + RSI_PERIOD:
            continue

        s_ma  = sma(test_prices, MA_SHORT)
        l_ma  = sma(test_prices, MA_LONG)
        rsi_v = calc_rsi(test_prices, RSI_PERIOD)

        sigs = generate_signals(test_prices, test_dates, s_ma, l_ma, rsi_v,
                               initial_entry=True)
        if not sigs:
            continue  # 無訊號的 fold 不納入統計

        # 強制在 fold 結束時平倉，確保每個有開倉的 fold 都能完整計算報酬
        sigs.append({'date': test_dates[-1], 'price': test_prices[-1], 'signal': 'SELL'})

        perf = backtest(sigs)
        if perf['trades'] == 0:
            continue  # 有訊號但完全沒成交（如只有 SELL 卻無持倉）

        perf['fold'] = fold
        results.append(perf)

    return results


# ── 單股完整分析 ──────────────────────────────────────────────────────────────

def analyze_stock(stock_id, name, days=DATA_DAYS):
    """取得資料、產生當前信號、執行 Walk-Forward CV"""
    try:
        stock = Stock(stock_id)
        # 回抓 ~14 個月資料，保證 CV 有足夠樣本
        start = datetime.now() - timedelta(days=int(days * 1.6))
        stock.fetch_from(start.year, start.month)
        prices = list(stock.price[-days:])
        dates  = list(stock.date[-days:])
    except Exception as e:
        return {'id': stock_id, 'name': name, 'error': str(e)}

    if len(prices) < MA_LONG + RSI_PERIOD + 10:
        return {'id': stock_id, 'name': name, 'error': '資料不足'}

    s_ma  = sma(prices, MA_SHORT)
    l_ma  = sma(prices, MA_LONG)
    rsi_v = calc_rsi(prices, RSI_PERIOD)

    # 當前狀態
    latest_price   = prices[-1]
    latest_short   = s_ma[-1]
    latest_long    = l_ma[-1]
    latest_rsi     = rsi_v[-1]

    # 當前信號（最後 5 根）
    recent_sigs = generate_signals(prices[-6:], dates[-6:],
                                   s_ma[-6:], l_ma[-6:], rsi_v[-6:])
    current_signal = recent_sigs[-1]['signal'] if recent_sigs else 'HOLD'

    # Walk-Forward CV
    cv_results = walk_forward_cv(prices, dates, CV_FOLDS)
    if cv_results:
        avg_ret    = np.mean([r['return']   for r in cv_results])
        avg_sharpe = np.mean([r['sharpe']   for r in cv_results])
        avg_dd     = np.mean([r['max_dd']   for r in cv_results])
        avg_wr     = np.mean([r['win_rate'] for r in cv_results])
        std_ret    = np.std( [r['return']   for r in cv_results])
    else:
        avg_ret = avg_sharpe = avg_dd = avg_wr = std_ret = 0

    return {
        'id':           stock_id,
        'name':         name,
        'price':        latest_price,
        'ma5':          round(latest_short, 2) if latest_short else None,
        'ma20':         round(latest_long,  2) if latest_long  else None,
        'rsi':          round(latest_rsi,   1) if latest_rsi   else None,
        'signal':       current_signal,
        'cv_return':    avg_ret,
        'cv_sharpe':    avg_sharpe,
        'cv_max_dd':    avg_dd,
        'cv_win_rate':  avg_wr,
        'cv_ret_std':   std_ret,      # 越小代表策略越穩定
        'cv_folds':     len(cv_results),
    }


# ── 批次掃描 ─────────────────────────────────────────────────────────────────

def batch_scan(watch_list: dict):
    print(f"\n{'='*70}")
    print(f"  台股批次掃描 + Walk-Forward 交叉驗證（{CV_FOLDS} 折）")
    print(f"  策略：MA({MA_SHORT}/{MA_LONG}) 黃金交叉 + RSI({RSI_PERIOD}) 雙重確認")
    print(f"  掃描時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*70}\n")

    all_results = []

    for stock_id, name in watch_list.items():
        print(f"  分析中... {stock_id} {name}", end='\r')
        result = analyze_stock(stock_id, name)
        all_results.append(result)

    # ── 結果表格 ──────────────────────────────────────────────────────────────
    ok  = [r for r in all_results if 'error' not in r]
    err = [r for r in all_results if 'error' in r]

    df = pd.DataFrame(ok)
    if df.empty:
        print("沒有可用資料")
        return

    df = df.sort_values('cv_sharpe', ascending=False)

    # 當前信號欄位美化
    signal_map = {'BUY': '★ 買入', 'SELL': '▼ 賣出', 'HOLD': '─ 觀望'}
    df['signal_label'] = df['signal'].map(signal_map)

    # ── 印出完整結果 ──────────────────────────────────────────────────────────
    print(f"\n{'─'*70}")
    print(f"{'代碼':<6}{'名稱':<10}{'現價':>7}{'RSI':>6}{'信號':<8}"
          f"{'CV報酬':>8}{'CV夏普':>8}{'CV勝率':>8}{'最大回撤':>9}{'穩定度':>8}")
    print(f"{'─'*70}")

    for _, r in df.iterrows():
        print(
            f"{r['id']:<6}{r['name']:<10}{r['price']:>7.1f}"
            f"{r['rsi']:>6.1f}{r['signal_label']:<8}"
            f"{r['cv_return']:>8.1%}{r['cv_sharpe']:>8.2f}"
            f"{r['cv_win_rate']:>8.1%}{r['cv_max_dd']:>9.1%}"
            f"{r['cv_ret_std']:>8.3f}"
        )

    print(f"{'─'*70}")
    print("  穩定度 = CV 報酬標準差（越小越穩）")

    # ── 推薦清單 ──────────────────────────────────────────────────────────────
    buy_candidates = df[
        (df['signal'] == 'BUY') &
        (df['cv_sharpe'] >= 0.3) &
        (df['cv_max_dd'] <= 0.20) &
        (df['cv_win_rate'] >= 0.40)
    ]

    print(f"\n{'='*70}")
    print(f"  近期買入候選（符合全部過濾條件）")
    print(f"{'='*70}")

    if buy_candidates.empty:
        print("  目前沒有同時滿足條件的標的，建議持續觀望。")

        # 給出最接近條件的前三名
        near = df[df['signal'] == 'BUY'].head(3)
        if not near.empty:
            print("\n  訊號為 BUY 但 CV 條件未全達標（可留意）：")
            for _, r in near.iterrows():
                print(f"  {r['id']} {r['name']}  夏普={r['cv_sharpe']:.2f}"
                      f"  勝率={r['cv_win_rate']:.1%}  最大回撤={r['cv_max_dd']:.1%}")
    else:
        for _, r in buy_candidates.iterrows():
            print(f"\n  ✅ {r['id']} {r['name']}")
            print(f"     現價={r['price']:.1f}  RSI={r['rsi']:.1f}")
            print(f"     CV 夏普={r['cv_sharpe']:.2f}  CV 勝率={r['cv_win_rate']:.1%}"
                  f"  最大回撤={r['cv_max_dd']:.1%}  穩定度={r['cv_ret_std']:.3f}")

    # ── 錯誤清單 ──────────────────────────────────────────────────────────────
    if err:
        print(f"\n  ⚠ 以下股票資料取得失敗：")
        for r in err:
            print(f"  {r['id']} {r['name']} → {r['error']}")

    print(f"\n{'='*70}\n")
    return df


# ── 主程式 ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    result_df = batch_scan(WATCH_LIST)

    # 儲存結果到 CSV
    if result_df is not None:
        out_path = 'scan_result.csv'
        result_df.to_csv(out_path, index=False, encoding='utf-8-sig')
        print(f"  結果已儲存至 {out_path}\n")
