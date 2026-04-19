# GitHub 股市研究模組 Top 10（台股優先）

**最後更新**：2026 年 4 月 14 日

---

## 📋 快速查閱表

| 排名 | 專案名稱 | 台股支援 | Stars | 語言 | 核心功能 | 難度 |
|:----:|---------|:--------:|:-----:|------|---------|:----:|
| **1** | **twstock** | ✅ | ~1.2k | Python | 股價擷取＋買賣點判斷 | ⭐ |
| **2** | **FinMind** | ✅ | ~1k | Python | 50+ 資料集全套（完整） | ⭐⭐ |
| **3** | 台灣股票即時爬蟲（JS） | ✅ | ~461 | JavaScript | 即時盤資料 | ⭐ |
| **4** | 股價擷取（Python） | ✅ | ~438 | Python | 即時盤＋時間判斷 | ⭐ |
| **5** | 上市上櫃爬蟲（v2） | ✅ | ~381 | Python | 上市＋上櫃爬蟲 | ⭐ |
| **6** | node-twstock | ✅ | 中等 | Node.js | 完整財務數據 API | ⭐⭐ |
| **7** | Taiwan Stock Knowledge Graph | ✅ | 學術向 | Python/Neo4j | 概念股＋知識圖譜 | ⭐⭐⭐ |
| **8** | 台股每日歷史資料庫 | ✅ | ~148 | Python | 自動更新＋備份 | ⭐ |
| **9** | Microsoft Qlib | 部分 | ~16k+ | Python | AI 量化研究平台 | ⭐⭐⭐ |
| **10** | AI4Finance FinRL | 部分 | ~10k+ | Python | 強化學習交易框架 | ⭐⭐⭐ |

---

## 🇹🇼 台股專屬模組（Top 8）

### #1 — mlouielu/twstock
- **GitHub**: https://github.com/mlouielu/twstock
- **⭐ Stars**: ~1,200 | **語言**: Python
- **更新狀況**: 積極維護 | **最後更新**: 2024年5月

**簡介**：
台股 GitHub 社群最高追蹤數的基礎套件，提供台灣股市股票價格擷取（含即時報價）。

**核心功能**：
- ✅ 股價擷取（含即時報價）
- ✅ 移動平均計算
- ✅ 乖離值計算
- ✅ 四大買賣點判斷（BestFourPoint）
- ✅ 全台證券代碼查詢
- ✅ 直接對接 TWSE 開放資料

**快速開始**：
```bash
git clone https://github.com/mlouielu/twstock
cd twstock
python -m pip install --user flit
flit install
```

**使用範例**：
```python
import twstock

# 擷取當前台積電股票資訊
twstock.realtime.get('2330')

# 計算五日均價
from twstock import Stock
stock = Stock('2330')
ma_p = stock.moving_average(stock.price, 5)

# 四大買賣點判斷
from twstock import BestFourPoint
bfp = BestFourPoint(stock)
bfp.best_four_point()
```

**適用場景**：
- ✅ 初學者快速入門
- ✅ 輕量級爬蟲需求
- ✅ 即時股價監控
- ✅ 技術面基礎分析

---

### #2 — FinMind/FinMind
- **GitHub**: https://github.com/FinMind/FinMind
- **⭐ Stars**: ~1,000 | **語言**: Python
- **更新狀況**: 積極維護 | **官網**: https://finmind.github.io/

**簡介**：
台股最完整的開源金融資料平台，提供超過 50 個金融資料集，每天自動更新。

**核心功能**：

#### 技術面
- 台股股價 daily、即時報價、歷史 tick
- PER、PBR（本益比、股價淨值比）
- 每 5 秒委託成交統計
- 加權指數、當日沖銷交易標的及成交量值

#### 基本面
- 綜合損益表
- 現金流量表
- 資產負債表
- 股利政策表
- 除權除息結果表
- 月營收

#### 籌碼面
- 外資持股
- 股權分散表（持股比例分佈）
- 融資融券
- 三大法人買賣
- 借券成交明細

#### 衍生性商品
- 期貨、選擇權 daily data
- 即時報價、交易明細
- 各商品法人買賣
- 各卷商每日交易

#### 國際市場
- 美股股價 daily、minute
- 美國債券殖利率
- 貨幣發行量（美國）
- 黃金價格、原油價格
- G8 央行利率、匯率

**快速開始**：
```bash
pip install finmind
```

**使用範例**：
```python
from FinMind.data import DataLoader

dl = DataLoader()

# 下載台股股價資料
stock_data = dl.taiwan_stock_daily(
    stock_id='2609',
    start_date='2018-01-01',
    end_date='2021-06-26'
)

# 下載三大法人資料
stock_data = dl.feature.add_kline_institutional_investors(stock_data)

# 下載融資券資料
stock_data = dl.feature.add_kline_margin_purchase_short_sale(stock_data)

# 繪製 K 線圖
from FinMind import plotting
plotting.kline(stock_data)
```

**適用場景**：
- ✅ 完整基本面分析
- ✅ 籌碼面研究
- ✅ 多維度策略開發
- ✅ 學位論文研究

**API 限制**：
- 免費用戶：300 次/小時
- 註冊驗證後：600 次/小時

---

### #3 — 台灣股票即時爬蟲（JavaScript 版）
- **GitHub**: 台灣 Topics 中高人氣專案
- **⭐ Stars**: ~461 | **語言**: JavaScript
- **特色**: 前端友善，輕量級

**簡介**：
支援 Taiwan Stock Exchange 即時資料擷取的前端爬蟲。

**核心功能**：
- 即時股價擷取
- 本益比、殖利率查詢
- 外資持股追蹤

**適用場景**：
- ✅ 網頁應用整合
- ✅ 即時盤資料展示
- ✅ 輕量級前端專案

---

### #4 — 台灣上市上櫃股票價格擷取（Python）
- **⭐ Stars**: ~438 | **語言**: Python

**簡介**：
含即時盤、台灣時間轉換、開休市判斷的台股爬蟲。

**核心功能**：
- ✅ 即時股價擷取
- ✅ 台灣時間自動轉換
- ✅ 開休市判斷
- ✅ 歷史股價查詢

**適用場景**：
- ✅ 自動化交易系統
- ✅ 盤中監控腳本
- ✅ 時區轉換場景

---

### #5 — 台灣上市上櫃股票爬蟲（第二版）
- **⭐ Stars**: ~381 | **語言**: Python

**簡介**：
完整的上市、上櫃雙交易所爬蟲。

**核心功能**：
- ✅ 上市股票爬蟲
- ✅ 上櫃股票爬蟲
- ✅ 完整資料覆蓋

**適用場景**：
- ✅ 中小型股票分析
- ✅ 上櫃公司研究

---

### #6 — chunkai1312/node-twstock
- **GitHub**: https://github.com/chunkai1312/node-twstock
- **⭐ Stars**: 中等 | **語言**: Node.js / TypeScript

**簡介**：
Node.js 版台股資料客戶端，功能完整的現代化實現。

**核心功能**：
- ✅ 即時報價（報價、委買/委賣）
- ✅ 歷史股價
- ✅ 三大法人買賣超
- ✅ 外資持股比例
- ✅ 融資融券餘額
- ✅ 本益比、殖利率、股價淨值比
- ✅ 股東結構（持股比例分佈）
- ✅ 每股盈餘（EPS）
- ✅ 月營收
- ✅ 除權除息資料

**快速開始**：
```bash
npm install node-twstock
```

**使用範例**：
```javascript
const twstock = require('node-twstock');

// 即時報價
twstock.stocks.quote({ symbol: '2330' })
  .then(data => console.log(data));

// 歷史股價
twstock.stocks.historical({ 
  date: '2023-01-30', 
  symbol: '2330' 
})
  .then(data => console.log(data));

// 三大法人買賣超
twstock.stocks.institutional({ 
  date: '2023-01-30', 
  symbol: '2330' 
})
  .then(data => console.log(data));
```

**適用場景**：
- ✅ 後端 Node.js 專案
- ✅ Electron 桌面應用
- ✅ 完整財務數據 API

---

### #7 — jojowither/Taiwan-Stock-Knowledge-Graph
- **GitHub**: https://github.com/jojowither/Taiwan-Stock-Knowledge-Graph
- **⭐ Stars**: 學術向 | **語言**: Python + Neo4j

**簡介**：
台股知識圖譜，用圖資料庫表示股票間的關係。

**核心概念**：
- **Stock 節點**：所有證券的名字、代號、屬性（股價、漲跌）
- **StockType 節點**：股票、ETF、權證等分類
- **Industry 節點**：產業類別（半導體業、航運業等）
- **Concept 節點**：概念股（如台積電的 5G、Apple 概念股）
- **Dealer 節點**：券商主力進出（隔日沖券商、國票等）
- **Board 節點**：董監持股、大股東關係

**資料來源**：
- twstock：證券名稱、代號、產業類別
- PChome 股市：概念股分類
- 永豐金證券：主力進出、董監持股

**適用場景**：
- ✅ 產業鏈關係分析
- ✅ 概念股投資研究
- ✅ 主力行為追蹤
- ✅ 圖論應用研究

---

### #8 — 台股每日歷史資料庫（自動更新）
- **⭐ Stars**: ~148 | **語言**: Python
- **特色**: 持續自動更新

**簡介**：
每天自動更新台股歷史資料庫，適合長期回測。

**核心功能**：
- ✅ 每日自動更新
- ✅ 歷史資料累積
- ✅ 資料庫持久化

**適用場景**：
- ✅ 長期策略回測
- ✅ 因子研究累積
- ✅ 歷史分析數據集

---

## 🌐 全球通用高 Stars 模組（Top 2）

### #9 — Microsoft/Qlib
- **GitHub**: https://github.com/microsoft/qlib
- **⭐ Stars**: ~16,000+ | **語言**: Python
- **維護方**: Microsoft AI4Science

**簡介**：
AI 導向的量化投資研究平台，支援多國市場（含台股）。

**核心特色**：
- ✅ **多種 ML 範式**：監督式學習、市場動態建模、強化學習
- ✅ **自動 R&D**：搭配 Microsoft RD-Agent
- ✅ **特徵工程**：自動特徵提取與選擇
- ✅ **回測引擎**：專業級績效評估
- ✅ **模型解釋**：SHAP values、特徵重要性分析

**適用場景**：
- ✅ 研究級量化策略
- ✅ ML 因子模型開發
- ✅ 機構投資研究
- ✅ 學位論文研究

**難度級別**：⭐⭐⭐ 進階

---

### #10 — AI4Finance-Foundation/FinRL
- **GitHub**: https://github.com/AI4Finance-Foundation/FinRL
- **⭐ Stars**: ~10,000+ | **語言**: Python
- **維護方**: AI4Finance Foundation

**簡介**：
深度強化學習自動交易框架，支援多資產類別。

**核心特色**：
- ✅ **DRL 策略**：DDPG、PPO、SAC 等
- ✅ **資產配置**：多資產組合優化
- ✅ **真實執行**：風險管理、訂單執行
- ✅ **績效分析**：Sharpe ratio、最大回撤等
- ✅ **數據整合**：Yahoo Finance、實盤資料

**適用場景**：
- ✅ 強化學習交易研究
- ✅ 自動資產配置
- ✅ 論文實驗框架
- ✅ 對標高頻策略

**難度級別**：⭐⭐⭐ 進階

---

## 💡 使用建議

### 🎯 按照使用場景選擇

#### 1. **初級：快速入門**
推薦組合：**twstock + FinMind**
- 20 分鐘上手
- 無需深度編程基礎
- 適合學習者、散戶

#### 2. **中級：系統化分析**
推薦組合：**FinMind + node-twstock + Jupyter**
- 完整的基本面、籌碼面、技術面
- 自建數據倉庫
- 適合學位論文、專業投資人

#### 3. **高級：機構級研究**
推薦組合：**Qlib + 自定義資料源 + ML Pipeline**
- 因子研究框架
- AI 驅動策略開發
- 適合對沖基金、量化團隊

#### 4. **高頻/強化學習**
推薦組合：**FinRL + 自定義執行層**
- 深度強化學習策略
- 自動資產配置
- 適合科技型基金

---

### 📚 學習路徑

```
第一週：twstock 基礎股價擷取
  ↓
第二週：FinMind 完整資料集整合
  ↓
第三週：自建 MongoDB/PostgreSQL 資料倉庫
  ↓
第四週：技術面 + 籌碼面 + 基本面聯動分析
  ↓
第五週～：進階選擇（Qlib 或 FinRL）
```

---

## 🔧 技術棧建議

### 最輕量配置
```
twstock + Jupyter + Matplotlib
└─ 適合快速驗證想法
```

### 標準配置
```
FinMind + PostgreSQL + Pandas + Plotly
└─ 適合中期研究
```

### 企業級配置
```
FinMind + MongoDB + Qlib + Apache Airflow
└─ 適合每日運營
```

---

## ⚠️ 注意事項

1. **資料正確性**：所有模組均基於公開資料，交易前應自行驗證
2. **API 限制**：FinMind 有請求數量限制，需根據使用量調整策略
3. **實盤交易**：建議先進行紙上交易（Paper Trading）驗證策略有效性
4. **風險管理**：任何策略都應配置止損、部位控管
5. **更新頻率**：關注 GitHub 專案更新，定期更新依賴套件

---

## 📖 相關資源

- **twstock GitHub**: https://github.com/mlouielu/twstock
- **FinMind 官網**: https://finmind.github.io/
- **node-twstock**: https://github.com/chunkai1312/node-twstock
- **Qlib GitHub**: https://github.com/microsoft/qlib
- **FinRL GitHub**: https://github.com/AI4Finance-Foundation/FinRL

---

**持續更新中**（最後更新：2026年4月14日）
