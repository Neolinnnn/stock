# 設計文件：回測選股 Tab 整合至每日掃描頁面

**日期**：2026-05-19  
**狀態**：已核准  
**修改範圍**：`docs/index.html`（純前端，無後端改動）

---

## 目標

將回測策略排行（Sharpe 前五）的最新持股，整合進每日掃描頁面（`index.html`），讓使用者在瀏覽族群掃描的同時能看到哪些個股有回測依據支撐。

---

## 資料來源

| 檔案 | 用途 |
|------|------|
| `docs/backtest/results.json` | 策略排行 + 各策略最新 rebalance 持股。由 `scripts/backtest.py` 產生，非每日更新。 |
| `docs/YYYYMMDD.json` | 每日掃描個股資料（price, rsi, signal, cv_sharpe, stop_loss 等）。由每日自動掃描產生。 |

兩者均已存在，不需新增資料管道。

---

## 架構

純 client-side JavaScript，在 `index.html` 現有的 `init()` / 資料載入流程中加入 `results.json` 的載入，並在 DOM 渲染完成後注入徽章與 Tab 內容。

```
頁面載入
  ├── fetch(YYYYMMDD.json)   → dailyMap: stock_id → {price, rsi, signal, sharpe, stopLoss, sector}
  └── fetch(backtest/results.json) → 取 ranking[:5] 的 variant
          └── 對每個 variant 取 rebalances[-1].holdings
                  └── 彙整 → btMap: stock_id → {count, strategies[], sector, name}

渲染
  ├── renderBtTab()     → 「回測選股」Tab 頁內容
  ├── injectBtBadges()  → 族群總覽表格注入 ★回測 徽章
  └── setupTooltip()    → 浮動提示卡事件綁定
```

---

## 功能規格

### 1. Nav Tab

- 在現有 `nav` 列末端新增：`<a data-page="bt">⭐ 回測選股</a>`
- 樣式與其他 Tab 一致，但文字顏色用金色（`var(--yellow)` 或自定 `#f1c40f`）

### 2. 「回測選股」Tab 頁面（`page-section` id=`bt`）

#### 2a. 上層：交集精選

- **資料**：btMap 中 count ≥ 2 的個股，依 count 降序，最多顯示 10 支
- **呈現**：每支股票一張 Chip 卡片，內容：
  - 代碼、名稱（大）
  - 族群（小字，藍色）
  - `出現 N/5 策略`（金色）
  - 今日訊號 badge（BUY/HOLD/SELL，若 dailyMap 有資料）
- **互動**：點擊 → 觸發浮動提示卡（見第 4 節）
- **無交集時**：顯示「目前五個策略持股無重疊，請查看各策略持倉」

#### 2b. 下層：各策略持倉子 Tab

- **子 Tab 列**：`#1`、`#2`、`#3`、`#4`、`#5`，標題格式：`#N 策略名稱（Sharpe X.XX）`
- **每個子 Tab 顯示**：該策略最新 rebalance 的持股表格

  | 欄位 | 來源 |
  |------|------|
  | 代碼 | holdings.stock_id |
  | 名稱 | holdings.name |
  | 族群 | holdings.sector |
  | 權重 | holdings.weight（顯示為 %） |
  | 今日 RSI | dailyMap（無資料顯示 —） |
  | 今日訊號 | dailyMap（badge，無資料顯示 —） |

- rebalance 日期顯示在子 Tab 上方：`持倉截至 YYYY-MM-DD`
- 表格行可點擊觸發浮動提示卡

### 3. ★回測 徽章（族群總覽）

- 在族群明細表格的「名稱」欄 render 時，若 `stock_id` 存在於 btMap，在名稱後附加：
  ```html
  <span class="bt-badge" data-sid="XXXX">★回測</span>
  ```
- 樣式：金色小標籤（`background: rgba(241,196,15,.15); color: #f1c40f; border: 1px solid rgba(241,196,15,.4)`）
- 點擊此徽章 → 觸發浮動提示卡

### 4. 浮動提示卡（Stock Tooltip）

- **觸發**：點擊 Chip 卡片、子 Tab 表格行、★回測 徽章
- **定位**：`position: fixed`，預設出現在點擊位置附近，自動偵測是否超出視窗邊緣並調整
- **關閉**：點擊卡片外任意處（`document` click 事件，stopPropagation 保護卡片本身）
- **內容**：

  ```
  ┌──────────────────────────────┐
  │  2454 聯發科          [IC設計] │
  ├──────────────────────────────┤
  │  現價    1,050               │
  │  RSI5    53.9                │
  │  訊號    HOLD                │
  │  CV夏普  -0.11               │
  │  ATR停損 980（若有）          │
  ├──────────────────────────────┤
  │  ⭐ 出現 5/5 策略            │
  │  策略：ret20/週/籌碼 … (列出名稱) │
  └──────────────────────────────┘
  ```

- **無每日資料時**：個股欄位顯示「— 今日無掃描資料」

---

## 樣式規範

沿用 `index.html` 現有 CSS 變數（`--card`、`--border`、`--text`、`--sub`、`--green`、`--red`、`--yellow`、`--accent`）。

新增 CSS class：

| Class | 用途 |
|-------|------|
| `.bt-badge` | ★回測 金色徽章 |
| `.bt-chip` | 交集精選 Chip 卡片 |
| `.bt-tooltip` | 浮動提示卡容器 |
| `.bt-sub-tab-bar` | 子 Tab 列 |
| `.bt-sub-tab` | 子 Tab 按鈕 |
| `.bt-sub-tab.active` | 選中狀態 |

---

## 邊界條件

| 情況 | 處理方式 |
|------|---------|
| `results.json` 載入失敗 | 不顯示 Tab 和徽章，靜默失敗（console.warn） |
| 某策略無 rebalances 資料 | 子 Tab 顯示「無持倉資料」 |
| 股票不在今日掃描 JSON | 浮動卡片對應欄位顯示 `—` |
| btMap 無 count ≥ 2 的股票 | 上層顯示說明文字，仍正常顯示下層子 Tab |
| 視窗寬度 < 768px | 子 Tab 列可橫向捲動；Chip 換行顯示 |

---

## 不在範圍內

- 後端腳本（`batch_scan.py`、`build_docs.py`）不修改
- `backtest.html` 不修改
- 回測執行頻率不改變（仍由使用者手動觸發）
- 不新增任何 API endpoint

---

## 檔案變更清單

| 檔案 | 類型 |
|------|------|
| `docs/index.html` | 修改（新增 CSS + JS + HTML 結構） |
| `mockup-backtest-panel.html` | 可在實作完成後刪除 |
