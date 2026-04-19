"""
測試範例 #1：基礎資料取得
Basic Data Fetching Examples

演示如何使用 twstock 和 FinMind 取得台股資料
"""

print("\n" + "="*60)
print("台股資料取得測試")
print("="*60)

# ============================================================================
# 範例 1：使用 twstock 取得即時股價
# ============================================================================

print("\n【範例 1】使用 twstock 取得即時股價")
print("-" * 60)

try:
    import twstock

    # 取得台積電 (2330) 的即時報價
    stock_id = '2330'
    print(f"\n取得 {stock_id} (台積電) 的即時報價...")

    realtime = twstock.realtime.get(stock_id)

    if realtime['success']:
        info = realtime['data']
        print(f"\n✓ 取得成功！")
        print(f"  股票代碼: {info['code']}")
        print(f"  股票名稱: {info['name']}")
        print(f"  最新價格: {info['last_trade']['price']}")
        print(f"  最高價: {info['highest_price']}")
        print(f"  最低價: {info['lowest_price']}")
        print(f"  成交量: {info['trade_volume']} 張")
        print(f"  時間: {info['last_trade']['timestamp']}")
    else:
        print(f"❌ 取得失敗")

except ImportError:
    print("❌ twstock 未安裝，請執行: pip install twstock")
except Exception as e:
    print(f"❌ 錯誤: {e}")

# ============================================================================
# 範例 2：使用 twstock 取得歷史股價
# ============================================================================

print("\n\n【範例 2】使用 twstock 取得歷史股價")
print("-" * 60)

try:
    from twstock import Stock

    stock = Stock('2454')  # 聯發科
    stock.update()

    print(f"\n✓ 成功取得聯發科 (2454) 的股價資料")
    print(f"  資料筆數: {len(stock.price)}")
    print(f"\n最近 10 日股價:")
    print(f"{'日期':<12} {'收盤價':>8}")
    print("-" * 20)

    for date, price in zip(stock.date[-10:], stock.price[-10:]):
        print(f"{date}   {price:>8.2f}")

    # 計算技術指標
    print(f"\n技術面分析:")
    ma5 = stock.moving_average(stock.price, 5)
    ma20 = stock.moving_average(stock.price, 20)
    print(f"  5日均線: {ma5[-1]:.2f}")
    print(f"  20日均線: {ma20[-1]:.2f}")
    print(f"  當前股價: {stock.price[-1]:.2f}")

    if ma5[-1] > ma20[-1]:
        print(f"  📈 短期走勢強於長期")
    else:
        print(f"  📉 短期走勢弱於長期")

except Exception as e:
    print(f"❌ 錯誤: {e}")

# ============================================================================
# 範例 3：使用 FinMind 取得完整資料集
# ============================================================================

print("\n\n【範例 3】使用 FinMind 取得完整資料集")
print("-" * 60)

try:
    from FinMind.data import DataLoader
    import pandas as pd

    dl = DataLoader()

    print(f"\n下載台灣股票日線資料...")
    stock_data = dl.taiwan_stock_daily(
        stock_id='2609',  # 陽明海運
        start_date='2026-04-01',
        end_date='2026-04-18'
    )

    if isinstance(stock_data, pd.DataFrame) and len(stock_data) > 0:
        print(f"\n✓ 成功取得資料")
        print(f"  資料筆數: {len(stock_data)}")
        print(f"  欄位: {list(stock_data.columns)}")

        print(f"\n資料預覽:")
        print(stock_data)

        # 基本統計
        print(f"\n基本統計:")
        print(f"  平均收盤價: {stock_data['close'].mean():.2f}")
        print(f"  最高價: {stock_data['close'].max():.2f}")
        print(f"  最低價: {stock_data['close'].min():.2f}")
        print(f"  平均成交量: {stock_data['volume'].mean():.0f}")
    else:
        print(f"❌ 沒有取得到資料")

except ImportError:
    print("❌ FinMind 未安裝，請執行: pip install finmind")
except Exception as e:
    print(f"❌ 錯誤: {e}")

# ============================================================================
# 範例 4：多支股票批次取得
# ============================================================================

print("\n\n【範例 4】多支股票批次取得")
print("-" * 60)

try:
    import twstock

    # 台灣電子股代表
    stocks = [
        ('2330', '台積電'),
        ('2454', '聯發科'),
        ('2412', '中華電'),
        ('2317', '鴻海'),
    ]

    print(f"\n取得電子股即時價格...")
    print(f"{'代碼':<6} {'名稱':<8} {'價格':>8} {'漲跌':>8}")
    print("-" * 35)

    for code, name in stocks:
        try:
            realtime = twstock.realtime.get(code)
            if realtime['success']:
                price = realtime['data']['last_trade']['price']
                change = realtime['data']['last_trade']['change']
                print(f"{code}   {name:<8} {price:>8.2f} {change:>+8.2f}")
            else:
                print(f"{code}   {name:<8} {'N/A':>8}")
        except:
            print(f"{code}   {name:<8} {'錯誤':>8}")

except Exception as e:
    print(f"❌ 錯誤: {e}")

# ============================================================================
# 範例 5：計算報酬率
# ============================================================================

print("\n\n【範例 5】計算股票報酬率")
print("-" * 60)

try:
    from twstock import Stock
    from datetime import datetime, timedelta

    stocks_to_check = [
        ('2330', '台積電'),
        ('2454', '聯發科'),
        ('0050', '台灣50 ETF'),
    ]

    for code, name in stocks_to_check:
        stock = Stock(code)
        stock.update()

        if len(stock.price) >= 20:
            price_20_days_ago = stock.price[-20]
            current_price = stock.price[-1]
            return_pct = ((current_price - price_20_days_ago) / price_20_days_ago) * 100

            print(f"\n{name} ({code})")
            print(f"  20 日前價格: {price_20_days_ago:.2f}")
            print(f"  當前價格: {current_price:.2f}")
            print(f"  20 日報酬率: {return_pct:+.2f}%")

except Exception as e:
    print(f"❌ 錯誤: {e}")

print("\n" + "="*60)
print("✨ 測試完成！")
print("="*60 + "\n")
