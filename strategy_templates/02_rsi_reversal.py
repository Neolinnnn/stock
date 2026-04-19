"""
台股量化交易 - 範本 #2：RSI 反轉策略
RSI Reversal Strategy

簡介：
相對強度指標 (RSI) 是動量指標，用於衡量股價上升和下降的幅度。
- 當 RSI < 30 時，股票超賣，可能形成反轉買入信號
- 當 RSI > 70 時，股票超買，可能形成反轉賣出信號

難度：初級
推薦用於：搭配均線策略使用、防止假突破

使用倉庫：twstock + FinMind
"""

import twstock
from twstock import Stock
import pandas as pd
import numpy as np


class RSIStrategy:
    """RSI 反轉策略"""

    def __init__(self, stock_id, rsi_period=14, overbought=70, oversold=30):
        """
        初始化

        Args:
            stock_id: 股票代碼
            rsi_period: RSI 計算週期
            overbought: 超買線（預設 70）
            oversold: 超賣線（預設 30）
        """
        self.stock_id = stock_id
        self.rsi_period = rsi_period
        self.overbought = overbought
        self.oversold = oversold
        self.stock = Stock(stock_id)
        self.signals = []

    def fetch_data(self, days=100):
        """取得股票資料"""
        self.stock.update()
        self.prices = self.stock.price[-days:]
        self.dates = self.stock.date[-days:]

        if len(self.prices) < self.rsi_period:
            raise ValueError(f"資料不足，需要至少 {self.rsi_period} 天")

    def calculate_rsi(self):
        """計算相對強度指標 (RSI)"""
        self.rsi = []

        for i in range(len(self.prices)):
            if i < self.rsi_period:
                self.rsi.append(None)
            else:
                # 計算變化
                changes = [self.prices[j] - self.prices[j-1] for j in range(i-self.rsi_period+1, i+1)]

                # 計算平均收益和平均虧損
                gains = [c for c in changes if c > 0]
                losses = [abs(c) for c in changes if c < 0]

                avg_gain = sum(gains) / self.rsi_period
                avg_loss = sum(losses) / self.rsi_period if losses else 0

                # 計算相對強度
                if avg_loss == 0:
                    rs = 100 if avg_gain > 0 else 0
                else:
                    rs = avg_gain / avg_loss

                # 計算 RSI
                rsi_value = 100 - (100 / (1 + rs))
                self.rsi.append(rsi_value)

    def generate_signals(self):
        """生成交易信號"""
        self.signals = []

        for i in range(1, len(self.rsi)):
            if self.rsi[i] is None or self.rsi[i-1] is None:
                continue

            # 超賣反轉買入
            if self.rsi[i-1] <= self.oversold and self.rsi[i] > self.oversold:
                self.signals.append({
                    'date': self.dates[i],
                    'price': self.prices[i],
                    'signal': '買入',
                    'rsi': self.rsi[i],
                    'reason': f'RSI 超賣反轉 ({self.rsi[i]:.1f})'
                })

            # 超買反轉賣出
            elif self.rsi[i-1] >= self.overbought and self.rsi[i] < self.overbought:
                self.signals.append({
                    'date': self.dates[i],
                    'price': self.prices[i],
                    'signal': '賣出',
                    'rsi': self.rsi[i],
                    'reason': f'RSI 超買反轉 ({self.rsi[i]:.1f})'
                })

    def backtest(self, initial_capital=100000, transaction_cost=0.001):
        """回測策略"""
        if not self.signals:
            return {'error': '沒有交易信號'}

        capital = initial_capital
        shares = 0
        trades = []

        for signal in self.signals:
            price = signal['price']

            if signal['signal'] == '買入' and capital > 0:
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
                    'rsi': signal['rsi'],
                    'reason': signal['reason']
                })

            elif signal['signal'] == '賣出' and shares > 0:
                revenue = shares * price * (1 - transaction_cost)
                cost = trades[-1].get('shares', 0) * trades[-1].get('price', 0) * (1 + transaction_cost)
                pnl = revenue - cost

                capital += revenue

                trades.append({
                    'date': signal['date'],
                    'action': '賣出',
                    'price': price,
                    'shares': shares,
                    'rsi': signal['rsi'],
                    'pnl': pnl,
                    'pnl_pct': (pnl / cost * 100) if cost > 0 else 0,
                    'reason': signal['reason']
                })

                shares = 0

        return {
            'total_return': f"{((capital - initial_capital) / initial_capital):.2%}",
            'final_capital': capital,
            'total_trades': len(trades),
            'trades': trades
        }

    def run(self, days=100, backtest=True):
        """完整運行流程"""
        print(f"\n{'='*60}")
        print(f"RSI 反轉策略 - {self.stock_id}")
        print(f"{'='*60}")

        try:
            print(f"\n📊 取得股票資料...")
            self.fetch_data(days)
            print(f"✓ 成功取得 {len(self.prices)} 天的資料")

            print(f"\n📈 計算 RSI 指標...")
            self.calculate_rsi()
            print(f"✓ RSI 已計算 (週期={self.rsi_period})")

            print(f"\n🔔 生成交易信號...")
            self.generate_signals()
            print(f"✓ 生成 {len(self.signals)} 個交易信號")

            print(f"\n最近 5 個信號：")
            for sig in self.signals[-5:]:
                print(f"  {sig['date']} - {sig['signal']} @ {sig['price']:.2f} (RSI: {sig['rsi']:.1f})")

            if backtest and self.signals:
                print(f"\n📉 執行回測...")
                results = self.backtest()

                print(f"\n【回測結果】")
                print(f"  總報酬率: {results['total_return']}")
                print(f"  最終資本: ${results['final_capital']:,.0f}")
                print(f"  交易次數: {results['total_trades']}")

                return results

        except Exception as e:
            print(f"\n❌ 錯誤: {e}")
            return None


if __name__ == "__main__":
    # 測試
    strategy = RSIStrategy('2330')
    results = strategy.run(days=100)

    # 顯示詳細交易記錄
    if results and 'trades' in results:
        print(f"\n【交易詳情】")
        for trade in results['trades']:
            if 'pnl' in trade:
                print(f"  {trade['date']} - {trade['action']} {trade['shares']}股 @ {trade['price']:.2f} | 損益: {trade['pnl']:.0f} ({trade['pnl_pct']:.1f}%)")
            else:
                print(f"  {trade['date']} - {trade['action']} {trade['shares']}股 @ {trade['price']:.2f}")
