# 週報分頁重新設計 — 綜合決策儀表板

**日期**：2026-06-13
**狀態**：設計已批准，待實作

## 問題

目前的週報分頁（[docs/index.html](../../index.html) `renderWeekly()`）只是兩張統計表 + 兩個數字卡：族群動能變化（pp）與本週累計 BUY Top 10。內容空泛，原因：

1. **無敘事層** — 專案架構規定 Gemini 生成 `weekly_report`，但 [build_weekly_payload](../../../scripts/weekly_summary.py) 沒把敘事寫進 `weekly.json`，前端也沒渲染。
2. **「動能變化 pp」只有差值、沒有水位** — 看不出族群是「現在強」還是「剛從谷底反彈」。
3. **BUY 排行無可行動性** — 只有次數，沒有今日是否仍 BUY、現價、RSI、法人籌碼。
4. **無 vs 上週連續性** — 週報的價值在連續觀察，但完全沒有對比。
5. **趨勢圖已生成卻沒顯示**（`01_weekly_sector_trend.png` 只存在 daily_reports）。
6. **每日 summary.json 的豐富欄位（RSI/cv_sharpe/chip/signal）未做週度彙整**。

核心：它在「報告統計」，不是「支援決策」。

## 目標

把週報分頁改成**三層綜合儀表板**：頂部敘事、中段族群輪動、底部行動清單。

## 關鍵發現（影響架構）

前端在啟動時已載入 `daily.json` 進 `DAILY_DATA`，內含每檔 `id/name/sector/signal/price/ret20/rsi/sharpe/chipTotal` 以及 `signalFlips`（買轉弱清單）。「買轉弱觀察」與「賣出訊號」已用 `action-card` 元件渲染。

→ **第三層的「今日狀態回填」在前端 join 即可完成，後端幾乎不用改。**

## 設計

### 第一層 · 本週敘事（後端 Gemini）

- [weekly_summary.py](../../../scripts/weekly_summary.py) `run_weekly_summary()` 收尾時呼叫 `GeminiWriter.generate("weekly_report", context)`。
- **context.data** 帶入：
  - `sector_changes`：每族群 `{sector, level, change}`（level = 最新 avg_ret_20d）
  - `accelerating` / `decelerating`：本週 change 最大正/負的前 3 族群
  - `top_buys`：BUY 排行（含次數）
  - `vs_last_week`：相對上週明顯轉強/轉弱的族群（若有上週資料）
- **context.extra** 指示：輸出兩段，標題分別為「本週輪動回顧」與「下週聚焦」，繁中、各 200 字內。
- 結果寫入 `weekly.json` 的 `narrative` 欄位（字串）。
- **錯誤處理**：Gemini 失敗時 `narrative` 設為空字串並印警告；前端 narrative 為空時**隱藏敘事卡**，不影響其餘兩層渲染。

### 第二層 · 族群輪動（後端補資料 + 前端象限）

- **payload 變更**：`build_weekly_payload` 的 `changes` 每項從 `{sector, change}` 擴充為 `{sector, change, level, prev_change}`。
  - `level` = 該族群最新一日 `avg_ret_20d`（取 `week_reports[-1]`）。
  - `prev_change` = 上週 `weekly.json` 同族群的 change（無則 null）。
- **象限分類**（前端依 level/change 正負）：

  | | change ≥ 0 | change < 0 |
  |---|---|---|
  | level ≥ 0 | 🟢 領漲（高檔續強） | 🟠 退燒（高檔回落） |
  | level < 0 | 🔵 接棒（落底翻揚） | 🔴 落後（弱勢加速） |

- **vs 上週箭頭**：`change - prev_change` 正→加速 ↑、負→減速 ↓、無上週資料→不顯示。
- **趨勢圖**：`run_weekly_summary` 把 `01_weekly_sector_trend.png` 另存一份到 `docs/weekly_sector_trend.png`；前端在第二層下方顯示。

### 第三層 · 行動清單（純前端 join）

- 將 `weekly.json` 的 `buys`（BUY 累計）對 `DAILY_DATA.stocks` 以股票 id join。
- 沿用既有 `action-card`／`action-grid` 元件，每張卡顯示：
  - 連續入選次數（`buy_days`）
  - **今日 signal 標記**：仍 BUY（綠）／轉 HOLD（黃）／轉 SELL（紅）
  - 現價、RSI（含 `rsiDot` 過熱提示）、法人 `chipTotal`
  - 所屬族群本週象限（從第二層分類帶入）
- **本週新冒出 vs 退燒**：
  - 新進＝本週 buys 有、上週 buys 無的個股（需上週 `weekly.json`）。
  - 退燒＝上週 buys 有、本週掉出 ＋ 直接複用 `DAILY_DATA.signalFlips`。

## 影響範圍

- **後端**：僅 [scripts/weekly_summary.py](../../../scripts/weekly_summary.py)
  - `run_weekly_summary`：讀上週 weekly.json、算 level/prev_change、呼叫 Gemini、複製趨勢圖到 docs/
  - `build_weekly_payload`：擴充 changes 欄位、加入 narrative
- **前端**：僅 [docs/index.html](../../index.html) `renderWeekly()` 重寫；新增象限分類與 action-card join 邏輯。
- **不動**：每日掃描流程、Notion 上傳、其他分頁。

## 驗證

1. 本機跑 `python scripts/weekly_summary.py`，確認 `docs/weekly.json` 含 `narrative` 與擴充後的 `changes`（有 level/prev_change），且 `docs/weekly_sector_trend.png` 產生。
2. 本機開 `docs/index.html`，切到週報分頁，三層皆正確渲染；故意把 narrative 清空，確認敘事卡隱藏而其餘正常。
3. 行動清單的「今日 signal」與 daily 分頁同一檔股票一致。

## 非目標（YAGNI）

- 不建持久化的週度狀態層（C 走向）；vs 上週只比對上一份 weekly.json。
- 象限位移（quadrant shift）對比要等連續兩週都有 `level` 後自然生效，本次不特別處理歷史回填。
