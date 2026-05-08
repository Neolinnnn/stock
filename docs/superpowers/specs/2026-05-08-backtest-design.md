# 回測系統設計規格 — 停利/停損 9 組合勝率分析

**日期**：2026-05-08  
**狀態**：已核准，待實作

---

## 目標

對現有族群掃描系統的每日 BUY 訊號進行回測，以 9 種停利/停損組合評估策略勝率，結果上傳 Notion。

---

## 參數定義

| 維度 | 值 |
|------|-----|
| 停利（TP） | 10%、12%、15% |
| 停損（SL） | 5%、10%、12% |
| 組合數 | 3 × 3 = 9 |
| 資料範圍 | 2026-03-01 ~ 最新掃描日 |
| 進場條件 | 當日 signal == "BUY"，隔交易日開盤價買入 |
| 投資上限 | 單次最高 3,000 元；同一個股累計持倉不超過 10,000 元 |
| 未實現倉位 | 到資料末端尚未觸發 TP/SL 者，排除在勝率統計之外 |

---

## 系統架構

### 新檔案
- `scripts/backtest.py`：主程式（回測引擎 + 結果存檔 + Notion 上傳）

### 修改檔案
- `scripts/notion_upload.py`：新增 `upload_backtest_results(results: dict) -> str`

### 輸出
- `backtest_results.json`：本地結果（9 組統計 + 每筆交易明細）
- Notion 頁面：3×3 勝率矩陣 + 各組詳細指標

---

## 回測流程

### Step 1：資料補齊
呼叫 `backfill_history.py` 邏輯（或直接 import），補足 2026-03-01 ~ 2026-03-18 尚未存在的交易日 summary.json。

### Step 2：批次抓開盤價
使用 FinMind `taiwan_stock_daily` 抓所有標的從 2026-03-01 至今的 OHLC，快取為 `dict[stock_id][date] = {open, close}`。

### Step 3：收集 BUY 訊號
讀所有 `daily_reports/*/summary.json`，按日期排序，掃描所有族群下 signal == "BUY" 的個股，產生訊號清單：
```
[{date, stock_id, stock_name, signal_price(close)}, ...]
```

### Step 4：回測引擎
對每筆訊號，取隔交易日的 open price 作為進場價。

持倉管理（per stock_id）：
- 若個股累計持倉 ≥ 10,000 元，跳過
- 本次投入 = min(3,000, 10,000 − 累計持倉)
- 若投入 < 100 元，跳過

出場判斷（逐日迭代 close price）：
- close ≥ entry × (1 + TP) → WIN，記錄出場日、報酬
- close ≤ entry × (1 − SL) → LOSS，記錄出場日、報酬
- 到末端未出場 → OPEN（排除勝率統計）

對每組 (TP, SL) 獨立執行以上邏輯（共 9 次）。

### Step 5：統計
每組計算：
- `total`：已出場交易數（WIN + LOSS）
- `wins`：WIN 筆數
- `losses`：LOSS 筆數
- `win_rate`：wins / total
- `avg_return`：已出場交易平均報酬（%）
- `avg_holding_days`：平均持有天數
- `open_count`：未實現筆數

### Step 6：存檔
```json
{
  "generated_at": "2026-05-08T...",
  "date_range": {"start": "20260301", "end": "20260507"},
  "combinations": {
    "TP10_SL5": { "win_rate": 0.62, "total": 45, ... , "trades": [...] },
    ...
  }
}
```

### Step 7：Notion 上傳
頁面標題：`📈 回測結果 TP×SL 勝率矩陣 2026-03-01~`

內容：
1. 3×3 勝率矩陣（paragraph 模擬表格）
2. 各組詳細指標（heading_3 + bullet）
3. 附記：資料範圍、未實現筆數說明

---

## Notion 表格格式（文字模擬）

```
        SL 5%      SL 10%     SL 12%
TP 10%  62% (45)   58% (45)   55% (45)
TP 12%  65% (42)   61% (42)   59% (42)
TP 15%  70% (38)   67% (38)   64% (38)
```
括號內為已出場交易數。

---

## 邊界條件

- 若隔日開盤價資料缺失，跳過該訊號並 log 警告
- 同一個股同一天出現多次 BUY（不同族群重複），只處理一次
- 回測不扣手續費（簡化模型，可後續加入）
