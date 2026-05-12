# MACD+KDJ（MJ強化版）入場訊號圖形設計

**日期：** 2026-05-12  
**來源策略：** NotebookLM「熊熬 技術面」— MJ組合策略與獲利指南

---

## 策略規則（來源）

### 核心邏輯
將 KDJ 的 J 線（高靈敏度方向指標）疊加到 MACD 視窗，兩者互補：
- MACD 缺乏方向靈敏度，J 線彌補
- J 線無法判斷動能強弱，MACD OSC 補足

### 進場條件

| 方向 | J 線條件 | MACD OSC 條件 |
|------|---------|---------------|
| 做多（買進）| 前根 J < 0，當根 J ≥ 0（向上穿越零軸）| 當根 OSC > 0（正柱同步出現）|
| 做空（買跌）| 前根 J > 0，當根 J ≤ 0（向下穿越零軸）| 當根 OSC < 0（負柱同步出現）|

### 過濾假訊號
- **動能不足**：J 穿零但 OSC 符號不符合 → 不進場
- **指標延遲**：OSC 沒有在 J 穿零的同一根 K 棒出現 → 不進場（已包含在同根判斷中）

---

## 實作設計（方案 A）

### 檔案改動

#### `indicators/technical.py`

1. **`compute_indicators()`** 新增 J 線：
   ```python
   df["kd_j"] = 3 * df["kd_k"] - 2 * df["kd_d"]
   ```

2. **新增 `detect_mj_signals(df)`**：
   - 輸入：含 `kd_j`、`macd_osc`、`close`、`low`、`high`、`date` 的 DataFrame
   - 輸出：DataFrame，欄位 `[date, signal, close, kd_j, macd_osc]`
   - 邏輯：逐行比對前後 J 值與當根 OSC

#### `app.py` — `tab_stock()`

1. **KD 子圖（row 3）**：
   - 加入 J 線 trace（橘紅色 `#ff6b6b`，寬度 1.2）
   - 加入 y=0 水平參考線（零軸）

2. **K 線主圖（row 1）**：
   - 做多訊號 ▲：`go.Scatter` marker-triangle-up，綠色 `#27ae60`，標在 `low * 0.995`
   - 做空訊號 ▽：`go.Scatter` marker-triangle-down，紅色 `#e74c3c`，標在 `high * 1.005`

3. **圖下方新增 MJ 訊號摘要表格**：
   - 顯示近 60 日內觸發訊號
   - 欄位：日期 / 訊號方向 / 收盤價 / J 值 / OSC 值

---

## 改動邊界

- **不改動**：現有 K 線、BB、MA、成交量、MACD 顯示邏輯
- **不新增**：出場條件、停損邏輯（不在本次範圍）
- **不修改**：KD 的 K、D 線顯示（只新增 J）
