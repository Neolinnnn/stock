"""
台股量化交易 - 範本 #1：移動平均線交叉策略
Moving Average Crossover Strategy

簡介：
經典的技術分析策略，當短期均線上穿長期均線時買入，
當短期均線下穿長期均線時賣出。

難度：初級
推薦用於：快速驗證策略框架、學習回測邏輯

使用倉庫：twstock
"""

import twstock
from twstock import Stock
import pandas as pd
from datetime import datetime, timedelta
import json

class MAStrategy:
    """移動平均線交叉策略"""

    def __init__(self, stock_id, short_window=5, long_window=20):
        """
        初始化

        Args:
            stock_id: 股票代碼（如 '2330'）
            short_window: 短期均線天數（預設 5 日）
            long_window: 長期均線天數（預設 20 日）
        """
        self.stock_id = stock_id
        self.short_window = short_window
        self.long_window = long_window
        self.stock = Stock(stock_id)
        self.signals = []

    def fetch_data(self, days=100):
        """
        取得股票資料

        Args:
            days: 要取得的天數
        """
        self.stock.update()
        self.prices = self.stock.price[-days:]
        self.dates = self.stock.date[-days:]

        if len(self.prices) < self.long_window:
            raise ValueError(f"資料不足，需要至少 {self.long_window} 天")

    def calculate_ma(self):
        """計算移動平均線"""
        self.short_ma = self._sma(self.prices, self.short_window)
        self.long_ma = self._sma(self.prices, self.long_window)

    @staticmethod
    def _sma(prices, window):
        """簡單移動平均 (SMA)"""
        sma = []
        for i in range(len(prices)):
            if i < window - 1:
                sma.append(None)
            else:
                sma.append(sum(prices[i-window+1:i+1]) / window)
        return sma

    def generate_signals(self):
        """生成交易信號"""
        self.signals = []

        for i in range(1, len(self.short_ma)):
            if self.short_ma[i] is None or self.long_ma[i] is None:
                continue

            # 判斷信號
            if self.short_ma[i-1] <= self.long_ma[i-1] and self.short_ma[i] > self.long_ma[i]:
                # 短期均線上穿長期均線 → 買入
                self.signals.append({
                    'date': self.dates[i],
                    'price': self.prices[i],
                    'signal': '買入',
                    'short_ma': self.short_ma[i],
                    'long_ma': self.long_ma[i]
                })

            elif self.short_ma[i-1] >= self.long_ma[i-1] and self.short_ma[i] < self.long_ma[i]:
                # 短期均線下穿長期均線 → 賣出
                self.signals.append({
                    'date': self.dates[i],
                    'price': self.prices[i],
                    'signal': '賣出',
                    'short_ma': self.short_ma[i],
                    'long_ma': self.long_ma[i]
                })

    def backtest(self, initial_capital=100000, transaction_cost=0.001):
        """
        回測策略

        Args:
            initial_capital: 初始資本
            transaction_cost: 交易手續費（預設 0.1%）

        Returns:
            回測結果字典
        """
        if not self.signals:
            return {'error': '沒有交易信號'}

        capital = initial_capital
        shares = 0
        trades = []
        equity_curve = [initial_capital]

        for signal in self.signals:
            price = signal['price']

            if signal['signal'] == '買入' and capital > 0:
                # 計算可買數量（每手 1000 股）
                num_shares = int(capital / price / 1000) * 1000
                if num_shares == 0:
                    continue

                cost = num_shares * price * (1 + transaction_cost)
                if cost > capital:
                    continue

                shares = num_shares
                capital -= cost

                trades.append({
                    'date': signal['date'],
                    'action': '買入',
                    'price': price,
                    'shares': num_shares,
                    'cost': cost
                })

            elif signal['signal'] == '賣出' and shares > 0:
                # 賣出全部持股
                revenue = shares * price * (1 - transaction_cost)
                pnl = revenue - trades[-1]['cost']

                capital += revenue

                trades.append({
                    'date': signal['date'],
                    'action': '賣出',
                    'price': price,
                    'shares': shares,
                    'revenue': revenue,
                    'pnl': pnl,
                    'pnl_pct': (pnl / trades[-1]['cost']) * 100
                })

                shares = 0

            equity_curve.append(capital + shares * price)

        # 計算績效指標
        total_return = (capital - initial_capital) / initial_capital
        max_equity = max(equity_curve)
        min_equity = min(equity_curve)
        max_drawdown = (max_equity - min_equity) / max_equity if max_equity > 0 else 0

        # 計算 Sharpe 比率（假設無風險利率為 0）
        returns = [equity_curve[i] - equity_curve[i-1] for i in range(1, len(equity_curve))]
        sharpe = (sum(returns) / len(returns)) / (max(1, sum([(r - sum(returns)/len(returns))**2 for r in returns]) ** 0.5)) if returns else 0

        return {
            'total_return': f"{total_return:.2%}",
            'final_capital': capital,
            'total_trades': len(trades),
            'winning_trades': sum(1 for t in trades if 'pnl' in t and t['pnl'] > 0),
            'max_drawdown': f"{max_drawdown:.2%}",
            'sharpe_ratio': f"{sharpe:.2f}",
            'equity_curve': equity_curve,
            'trades': trades
        }

    def run(self, days=100, backtest=True):
        """
        完整運行流程

        Args:
            days: 資料天數
            backtest: 是否進行回測
        """
        print(f"\n{'='*60}")
        print(f"移動平均線交叉策略 - {self.stock_id}")
        print(f"{'='*60}")

        try:
            # 取得資料
            print(f"\n📊 取得股票資料...")
            self.fetch_data(days)
            print(f"✓ 成功取得 {len(self.prices)} 天的資料")

            # 計算均線
            print(f"\n📈 計算均線指標...")
            self.calculate_ma()
            print(f"✓ {self.short_window}日均線和{self.long_window}日均線已計算")

            # 生成信號
            print(f"\n🔔 生成交易信號...")
            self.generate_signals()
            print(f"✓ 生成 {len(self.signals)} 個交易信號")

            # 顯示最近信號
            print(f"\n最近 5 個信號：")
            for sig in self.signals[-5:]:
                print(f"  {sig['date']} - {sig['signal']} @ {sig['price']:.2f}")

            # 回測
            if backtest and self.signals:
                print(f"\n📉 執行回測...")
                results = self.backtest()

                print(f"\n【回測結果】")
                print(f"  總報酬率: {results['total_return']}")
                print(f"  最終資本: ${results['final_capital']:,.0f}")
                print(f"  交易次數: {results['total_trades']}")
                print(f"  勝率: {results['winning_trades']}/{results['total_trades']}")
                print(f"  最大回撤: {results['max_drawdown']}")
                print(f"  Sharpe 比率: {results['sharpe_ratio']}")

                return results

        except Exception as e:
            print(f"\n❌ 錯誤: {e}")
            return None


# ============================================================================
# 使用範例
# ============================================================================

if __name__ == "__main__":

    # 測試 1：台積電 (2330)
    print("\n【測試 1】台積電 (2330) - 預設參數")
    strategy1 = MAStrategy('2330')
    results1 = strategy1.run(days=100, backtest=True)

    # 測試 2：聯發科 (2454) - 自訂參數
    print("\n\n【測試 2】聯發科 (2454) - 自訂參數")
    strategy2 = MAStrategy('2454', short_window=3, long_window=10)
    results2 = strategy2.run(days=100, backtest=True)

    # 測試 3：鴻海 (2317)
    print("\n\n【測試 3】鴻海 (2317)")
    strategy3 = MAStrategy('2317')
    results3 = strategy3.run(days=100, backtest=True)

    print("\n\n" + "="*60)
    print("✨ 策略測試完成！")
    print("="*60)
