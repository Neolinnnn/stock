# 台股量化交易完整開發套件

> 一套完整的台灣股市量化交易框架，包含 6 個核心開源倉庫、策略範本、回測框架和實戰指南。
>
> **最後更新**：2026年4月18日

---

## 📁 資料夾結構

```
台股研究/
├── 🚀 01_快速開始指南.md          ← 從這裡開始！
├── 📥 download_repos.sh            ← 自動下載所有GitHub倉庫的腳本
├── 📖 README.md                    ← 本文件
├── 📊 GitHub_Stock_Research_Top10_TW.md  ← 詳細的倉庫介紹
│
├── 📦 github-repos/                ← 克隆的GitHub倉庫目錄
│   ├── twstock/                    ← 基礎股價擷取
│   ├── FinMind/                    ← 完整資料集平台
│   ├── node-twstock/               ← Node.js 版本
│   ├── Taiwan-Stock-Knowledge-Graph/  ← 知識圖譜
│   ├── qlib/                       ← 量化研究平台
│   └── FinRL/                      ← 強化學習交易
│
├── 🎯 strategy_templates/          ← 交易策略範本
│   ├── 01_moving_average_crossover.py   ← 移動平均線交叉
│   └── 02_rsi_reversal.py              ← RSI 反轉策略
│
└── 🧪 test_examples/               ← 測試和範例程式
    ├── 01_basic_data_fetch.py      ← 基本資料取得
    └── (更多範例開發中...)
```

---

## 🎯 **快速開始（3 分鐘）**

### 第一步：下載所有倉庫

```bash
# 進入台股研究資料夾
cd /path/to/台股研究

# 執行下載腳本
bash download_repos.sh
```

腳本會自動下載並解壓所有 6 個倉庫到 `github-repos/` 目錄。

### 第二步：設置 Python 環境

```bash
# 建立虛擬環境
python3 -m venv twstock-env
source twstock-env/bin/activate  # Linux/Mac

# 安裝核心套件
pip install twstock finmind pandas numpy matplotlib seaborn jupyter
```

### 第三步：運行測試程式

```bash
# 測試基本資料取得
python test_examples/01_basic_data_fetch.py

# 測試策略 #1：移動平均線交叉
python strategy_templates/01_moving_average_crossover.py

# 測試策略 #2：RSI 反轉
python strategy_templates/02_rsi_reversal.py
```

---

## 📚 **核心倉庫簡介**

### 🥇 **Top 3 推薦組合**

#### **初級（1 週內上手）**
```
twstock + FinMind
```
- **目標**：快速驗證想法、學習基礎分析
- **時間**：20 分鐘上手
- **推薦度**：⭐⭐⭐⭐⭐

**重點：**
- `twstock`：即時股價、技術指標、買賣點判斷
- `FinMind`：完整資料集（技術面、籌碼面、基本面）

**快速範例：**
```python
import twstock
from FinMind.data import DataLoader

# 即時股價
price = twstock.realtime.get('2330')

# 完整資料
dl = DataLoader()
data = dl.taiwan_stock_daily(
    stock_id='2330',
    start_date='2026-04-01',
    end_date='2026-04-18'
)
```

---

#### **中級（2-4 週）**
```
FinMind + node-twstock + 自建資料庫
```
- **目標**：系統化分析、策略開發
- **推薦度**：⭐⭐⭐⭐

**重點：**
- 整合基本面、籌碼面、技術面
- Node.js 或 Python 並行開發
- 建立 PostgreSQL/MongoDB 資料倉庫
- 回測框架

---

#### **高級（1 個月以上）**
```
Qlib + FinRL + Taiwan-Stock-Knowledge-Graph
```
- **目標**：機構級研究、AI 策略開發
- **推薦度**：⭐⭐⭐

**重點：**
- **Qlib**：因子研究、機器學習模型
- **FinRL**：強化學習交易
- **Knowledge Graph**：概念股分析

---

## 🧭 **學習路徑（建議 4 週）**

### **第 1 週：基礎入門**

目標：快速掌握股價取得、技術指標、簡單策略

| 天數 | 任務 | 使用工具 |
|------|------|---------|
| Day 1-2 | 學習 twstock API，取得即時/歷史股價 | `twstock` |
| Day 3 | 計算技術指標（MA、RSI、MACD） | `twstock` + `pandas` |
| Day 4-5 | 開發第一個簡單策略（MA交叉）| `strategy_templates/01_...` |
| Day 6-7 | 回測框架，評估績效 | `pandas` + `numpy` |

**目標程式：**
```python
from twstock import Stock

stock = Stock('2330')
stock.update()

# 計算 5 日均線和 20 日均線
ma5 = stock.moving_average(stock.price, 5)
ma20 = stock.moving_average(stock.price, 20)

# 生成信號
if ma5[-1] > ma20[-1]:
    print("買入信號")
else:
    print("賣出信號")
```

---

### **第 2 週：多維度分析**

目標：整合籌碼面、基本面、技術面

| 任務 | 內容 |
|------|------|
| 籌碼面 | 三大法人、外資持股、融資融券 |
| 基本面 | EPS、PER、PBR、月營收 |
| 技術面 | 加入 RSI、MACD、布林帶 |

**使用 FinMind：**
```python
from FinMind.data import DataLoader

dl = DataLoader()
stock_data = dl.taiwan_stock_daily(stock_id='2330')

# 加入籌碼面
stock_data = dl.feature.add_kline_institutional_investors(stock_data)

# 加入融資融券
stock_data = dl.feature.add_kline_margin_purchase_short_sale(stock_data)
```

---

### **第 3 週：回測 & 優化**

目標：建立完整的回測框架

| 任務 | 重點 |
|------|------|
| 歷史資料倉庫 | 建立 PostgreSQL/MongoDB 數據庫 |
| 回測引擎 | Sharpe ratio、最大回撤、勝率計算 |
| 參數優化 | 網格搜索、貝葉斯優化 |

---

### **第 4 週：進階方向選擇**

選擇一個方向深化：

**A. 機器學習因子模型（推薦）**
```
使用 Qlib 進行因子研究
→ XGBoost/LightGBM 預測模型
→ 自動特徵工程
```

**B. 強化學習自動交易**
```
使用 FinRL 開發 DRL 策略
→ DDPG、PPO、SAC 演算法
→ 風險管理模組
```

**C. 概念股投資分析**
```
使用 Taiwan-Stock-Knowledge-Graph
→ 知識圖譜分析
→ 產業鏈投資機會
```

---

## 🚀 **開發建議**

### 開發環境設置

```bash
# 推薦配置
python3 -m venv twstock-env
source twstock-env/bin/activate

# 核心套件
pip install \
  twstock \
  finmind \
  pandas \
  numpy \
  scikit-learn \
  xgboost \
  matplotlib \
  seaborn \
  plotly \
  jupyter

# 資料庫（選擇一個）
pip install sqlalchemy psycopg2-binary  # PostgreSQL
# 或
pip install pymongo  # MongoDB

# 進階套件（根據選擇）
pip install qlib          # 量化研究
pip install finrl         # 強化學習
pip install py2neo        # 知識圖譜
```

### IDE 推薦

- **快速開發**：Jupyter Notebook / JupyterLab
- **正式開發**：VS Code + Python Extension
- **深度開發**：PyCharm Professional

### 版本控制

```bash
# 初始化 Git
git init
git add .
git commit -m "Initial commit: Taiwan stock research framework"

# 推送到 GitHub（可選）
git remote add origin https://github.com/YOUR_USERNAME/twstock-research.git
git push -u origin main
```

---

## 📊 **實戰檢查清單**

在上線交易前，確認以下事項：

- [ ] 策略已在歷史資料上回測 ≥ 3 個月
- [ ] Sharpe ratio ≥ 0.5（初級要求）
- [ ] 最大回撤 ≤ 20%
- [ ] 勝率 ≥ 40%
- [ ] 紙上交易驗證 ≥ 1 個月
- [ ] 風險管理：止損、部位控管、單日虧損限制
- [ ] 監控系統：即時警報、績效追蹤
- [ ] 合規檢查：符合證交所規範

---

## 🔗 **快速連結**

| 資源 | 連結 |
|------|------|
| twstock GitHub | https://github.com/mlouielu/twstock |
| FinMind 官網 | https://finmind.github.io/ |
| node-twstock | https://github.com/chunkai1312/node-twstock |
| Taiwan Stock KG | https://github.com/jojowither/Taiwan-Stock-Knowledge-Graph |
| Microsoft Qlib | https://github.com/microsoft/qlib |
| AI4Finance FinRL | https://github.com/AI4Finance-Foundation/FinRL |

---

## 💡 **常見問題**

### Q1：我應該從哪裡開始？
A：從 `01_快速開始指南.md` 開始，按照第一週的任務進行。

### Q2：我沒有程式基礎，可以用嗎？
A：可以，但建議先學習基礎 Python。推薦資源：
- Codecademy Python 課程
- 台灣大學開放課程（Python）

### Q3：我想要自動化交易，應該用什麼？
A：FinRL + 自定義執行層，或使用 Qlib 開發 ML 模型。

### Q4：資料來源可靠嗎？
A：twstock 和 FinMind 都使用官方公開資料，但交易前建議驗證。

### Q5：我能用這個賺錢嗎？
A：這是工具和框架，成敗取決於你的策略、市場環境和風險管理。

---

## 📝 **貢獻和改進**

這個框架持續更新中。如果你有建議或發現問題：

1. 在本資料夾建立 `issues/` 目錄
2. 記錄問題、改進建議、新策略範本
3. 我會定期整合社群反饋

---

## ⚖️ **免責聲明**

- 所有策略均為教育目的
- 過去績效不代表未來表現
- 量化交易涉及風險，請務必做好風險管理
- 交易前請諮詢財務顧問
- 確保你的交易活動符合台灣證交所規範

---

## 🎓 **學習資源推薦**

| 主題 | 資源 |
|------|------|
| Python 基礎 | Codecademy、官方教程 |
| 技術分析 | ChartSchool、TradingView 教學 |
| 量化交易 | "量化交易"（作者：Narang）|
| 機器學習 | Coursera、Fast.ai |
| 強化學習 | OpenAI Spinning Up、Sutton & Barto |

---

## 📞 **聯絡與支援**

- **報告 Bug**：在 `issues/` 目錄創建文件
- **建議新策略**：提交 `strategy_templates/` 新文件
- **交流討論**：使用本資料夾的 `discussions/` 目錄

---

## 📜 **授權**

本框架文件和範本由社群創建，遵循 MIT 授權。
各倉庫遵循其原始授權（通常為 MIT 或 Apache 2.0）。

---

**祝你交易順利！** 🚀

最後更新：2026年4月18日
維護者：Neo（neo_lin@gemteks.com）

