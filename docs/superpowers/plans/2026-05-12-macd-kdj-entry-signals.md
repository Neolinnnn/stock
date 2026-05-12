# MACD+KDJ（MJ強化版）入場訊號圖形 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在個股分析頁面新增 MJ 強化版入場訊號：KD 子圖加 J 線、K 線主圖加三角進場標記、圖下方顯示訊號摘要表。

**Architecture:** 在 `indicators/technical.py` 新增 J 線計算與 `detect_mj_signals()` 偵測函式，在 `app.py` 的 `tab_stock()` 消費結果並渲染為 Plotly 標記與 Streamlit 表格。J 線計算公式 `J = 3K − 2D`，進場條件為 J 穿越零軸且同根 K 棒 MACD OSC 符號一致。

**Tech Stack:** Python 3, pandas, plotly.graph_objects, streamlit

---

## 檔案對照

| 動作 | 路徑 | 責任 |
|------|------|------|
| Modify | `indicators/technical.py` | J 線計算、MJ 訊號偵測邏輯 |
| Modify | `app.py` | 渲染 J 線 trace、三角標記、訊號摘要表 |
| Create | `tests/test_mj_signals.py` | 訊號偵測邏輯的單元測試 |

---

## Task 1：新增 J 線計算

**Files:**
- Modify: `indicators/technical.py`（`compute_indicators` 函式，目前在第 12-54 行）

- [ ] **Step 1：在 `compute_indicators()` 中 KD 計算區塊後加入 J 線**

  找到 `indicators/technical.py` 第 29 行（`df["kd_d"] = ...`），在其後加入：

  ```python
  df["kd_j"] = 3 * df["kd_k"] - 2 * df["kd_d"]
  ```

  完整 KD 區塊修改後如下（第 24-30 行）：

  ```python
  # KD Stochastic (9, 3, 3)
  _low9  = df["low"].rolling(9).min()
  _high9 = df["high"].rolling(9).max()
  _rsv   = (df["close"] - _low9) / (_high9 - _low9 + 1e-9) * 100
  df["kd_k"] = _rsv.ewm(com=2, adjust=False).mean()   # 1/3 smoothing = com=2
  df["kd_d"] = df["kd_k"].ewm(com=2, adjust=False).mean()
  df["kd_j"] = 3 * df["kd_k"] - 2 * df["kd_d"]
  ```

- [ ] **Step 2：確認不影響現有欄位**

  執行 Python 確認欄位存在：

  ```bash
  cd "C:\Users\Neo\Documents\Claude\Projects\台股研究\.claude\worktrees\elated-franklin-7b8e48"
  python -c "
  import pandas as pd
  from indicators.technical import compute_indicators
  df = pd.DataFrame({
      'open': [10,11,12,13,14]*5, 'high': [11,12,13,14,15]*5,
      'low':  [9,10,11,12,13]*5,  'close': [10,11,12,13,14]*5,
      'volume': [1000]*25
  })
  out = compute_indicators(df).dropna()
  print('kd_j' in out.columns, out[['kd_k','kd_d','kd_j']].tail(3))
  "
  ```

  預期輸出：`True` 且顯示三欄數值

- [ ] **Step 3：Commit**

  ```bash
  git add indicators/technical.py
  git commit -m "feat: 新增 KDJ J 線計算（kd_j = 3K - 2D）"
  ```

---

## Task 2：新增 `detect_mj_signals()` 函式

**Files:**
- Modify: `indicators/technical.py`（在檔案末尾新增函式）
- Create: `tests/test_mj_signals.py`

- [ ] **Step 1：先寫失敗測試**

  建立 `tests/test_mj_signals.py`：

  ```python
  import pandas as pd
  import numpy as np
  import pytest
  from indicators.technical import compute_indicators, detect_mj_signals


  def _make_df(kd_j_vals, macd_osc_vals):
      """建立最小測試 DataFrame，直接指定 kd_j 與 macd_osc。"""
      n = len(kd_j_vals)
      df = pd.DataFrame({
          "date": pd.date_range("2025-01-01", periods=n).astype(str),
          "close": [100.0] * n,
          "high":  [102.0] * n,
          "low":   [98.0] * n,
          "kd_j":      kd_j_vals,
          "macd_osc":  macd_osc_vals,
      })
      return df


  def test_long_signal_detected():
      """J 從負穿正且 OSC 正值 → 做多訊號"""
      df = _make_df(
          kd_j_vals=  [-5.0, -2.0,  3.0, 10.0],
          macd_osc_vals=[ 0.1,  0.2,  0.5,  0.3],
      )
      signals = detect_mj_signals(df)
      assert len(signals) == 1
      assert signals.iloc[0]["signal"] == "LONG"
      assert signals.iloc[0]["date"] == "2025-01-03"


  def test_short_signal_detected():
      """J 從正穿負且 OSC 負值 → 做空訊號"""
      df = _make_df(
          kd_j_vals=  [10.0,  5.0, -2.0, -8.0],
          macd_osc_vals=[-0.1, -0.3, -0.5, -0.2],
      )
      signals = detect_mj_signals(df)
      assert len(signals) == 1
      assert signals.iloc[0]["signal"] == "SHORT"
      assert signals.iloc[0]["date"] == "2025-01-03"


  def test_no_signal_when_osc_wrong_direction():
      """J 向上穿零但 OSC 為負 → 動能不足，不進場"""
      df = _make_df(
          kd_j_vals=  [-5.0,  3.0],
          macd_osc_vals=[-0.3, -0.1],
      )
      signals = detect_mj_signals(df)
      assert len(signals) == 0


  def test_no_signal_when_no_crossover():
      """J 一直在零軸同側 → 無訊號"""
      df = _make_df(
          kd_j_vals=  [2.0, 5.0, 8.0],
          macd_osc_vals=[0.1, 0.2, 0.3],
      )
      signals = detect_mj_signals(df)
      assert len(signals) == 0


  def test_return_columns():
      """回傳 DataFrame 含必要欄位"""
      df = _make_df([-3.0, 4.0], [0.2, 0.5])
      signals = detect_mj_signals(df)
      for col in ["date", "signal", "close", "kd_j", "macd_osc"]:
          assert col in signals.columns
  ```

- [ ] **Step 2：執行測試確認失敗**

  ```bash
  cd "C:\Users\Neo\Documents\Claude\Projects\台股研究\.claude\worktrees\elated-franklin-7b8e48"
  python -m pytest tests/test_mj_signals.py -v
  ```

  預期：`ImportError: cannot import name 'detect_mj_signals'`

- [ ] **Step 3：在 `indicators/technical.py` 末尾新增函式**

  ```python
  # ── MJ 強化版入場訊號偵測 ────────────────────────────────────────────────────

  def detect_mj_signals(df: pd.DataFrame) -> pd.DataFrame:
      """
      依 MJ 強化版規則偵測做多 / 做空入場訊號。

      進場條件（兩者必須同時發生在同一根 K 棒）：
      - 做多（LONG）：J 線從零軸下方穿越至上方，且當根 MACD OSC > 0
      - 做空（SHORT）：J 線從零軸上方穿越至下方，且當根 MACD OSC < 0

      回傳含 [date, signal, close, kd_j, macd_osc] 的 DataFrame。
      """
      rows = []
      j   = df["kd_j"].values
      osc = df["macd_osc"].values

      for i in range(1, len(df)):
          prev_j, curr_j = j[i - 1], j[i]
          curr_osc = osc[i]

          if prev_j < 0 and curr_j >= 0 and curr_osc > 0:
              signal = "LONG"
          elif prev_j > 0 and curr_j <= 0 and curr_osc < 0:
              signal = "SHORT"
          else:
              continue

          rows.append({
              "date":     df["date"].iloc[i],
              "signal":   signal,
              "close":    df["close"].iloc[i],
              "kd_j":     round(curr_j, 2),
              "macd_osc": round(curr_osc, 4),
          })

      return pd.DataFrame(rows, columns=["date", "signal", "close", "kd_j", "macd_osc"])
  ```

- [ ] **Step 4：執行測試確認全部通過**

  ```bash
  python -m pytest tests/test_mj_signals.py -v
  ```

  預期：`5 passed`

- [ ] **Step 5：Commit**

  ```bash
  git add indicators/technical.py tests/test_mj_signals.py
  git commit -m "feat: 新增 detect_mj_signals() MJ強化版訊號偵測"
  ```

---

## Task 3：在 KD 子圖加入 J 線

**Files:**
- Modify: `app.py`（`tab_stock()` 函式，KD subplot 區段，約第 671-676 行）

- [ ] **Step 1：在 `tab_stock()` 中 import 新函式**

  找到 `app.py` 第 483 行：

  ```python
  from indicators.technical import compute_indicators, technical_summary, key_levels, detect_patterns
  ```

  改為：

  ```python
  from indicators.technical import compute_indicators, technical_summary, key_levels, detect_patterns, detect_mj_signals
  ```

- [ ] **Step 2：在訊號計算區塊新增 MJ 訊號**

  找到 `app.py` 約第 546-548 行（技術分析計算區塊）：

  ```python
  summary_items = technical_summary(df)
  levels        = key_levels(df)
  patterns      = detect_patterns(df)
  ```

  在其後加入：

  ```python
  mj_signals    = detect_mj_signals(df)
  ```

- [ ] **Step 3：在 KD 子圖加入 J 線 trace 與零軸**

  找到 `app.py` 約第 671-676 行（KD traces 區段）：

  ```python
  fig.add_trace(go.Scatter(x=df["date"], y=df["kd_k"], name="K",
                           line=dict(color="#3498db", width=1.5)), row=3, col=1)
  fig.add_trace(go.Scatter(x=df["date"], y=df["kd_d"], name="D",
                           line=dict(color="#e67e22", width=1.5)), row=3, col=1)
  fig.add_hline(y=80, line_dash="dash", line_color="rgba(220,50,50,0.4)", row=3, col=1)
  fig.add_hline(y=20, line_dash="dash", line_color="rgba(50,200,50,0.4)",  row=3, col=1)
  ```

  改為：

  ```python
  fig.add_trace(go.Scatter(x=df["date"], y=df["kd_k"], name="K",
                           line=dict(color="#3498db", width=1.5)), row=3, col=1)
  fig.add_trace(go.Scatter(x=df["date"], y=df["kd_d"], name="D",
                           line=dict(color="#e67e22", width=1.5)), row=3, col=1)
  fig.add_trace(go.Scatter(x=df["date"], y=df["kd_j"], name="J",
                           line=dict(color="#ff6b6b", width=1.2)), row=3, col=1)
  fig.add_hline(y=80, line_dash="dash", line_color="rgba(220,50,50,0.4)", row=3, col=1)
  fig.add_hline(y=20, line_dash="dash", line_color="rgba(50,200,50,0.4)",  row=3, col=1)
  fig.add_hline(y=0,  line_dash="dot",  line_color="rgba(200,200,200,0.5)", row=3, col=1)
  ```

- [ ] **Step 4：確認語法無誤**

  ```bash
  python -c "import app"
  ```

  預期：無 ImportError / SyntaxError

- [ ] **Step 5：Commit**

  ```bash
  git add app.py
  git commit -m "feat: KD 子圖新增 J 線（橘紅色）與零軸參考線"
  ```

---

## Task 4：在 K 線主圖加入 MJ 入場三角標記

**Files:**
- Modify: `app.py`（`tab_stock()` 函式，在 `fig.update_layout(...)` 之前）

- [ ] **Step 1：加入做多 / 做空三角標記 traces**

  找到 `app.py` 約第 686 行（`fig.update_layout(...)` 開始之前），在其前加入：

  ```python
  # ── MJ 入場訊號三角標記 ─────────────────────────────────────────────────────
  if not mj_signals.empty:
      _long_sig  = mj_signals[mj_signals["signal"] == "LONG"]
      _short_sig = mj_signals[mj_signals["signal"] == "SHORT"]

      # 做多訊號：綠色上三角，標在 K 棒 low 下方
      if not _long_sig.empty:
          _long_dates = _long_sig["date"].tolist()
          _long_lows  = df[df["date"].isin(_long_dates)]["low"] * 0.994
          fig.add_trace(go.Scatter(
              x=_long_dates, y=_long_lows.tolist(),
              mode="markers",
              marker=dict(symbol="triangle-up", color="#27ae60", size=12,
                          line=dict(color="white", width=1)),
              name="MJ做多",
              hovertemplate="做多入場<br>%{x}<br>收盤：%{customdata:.1f}",
              customdata=_long_sig["close"].tolist(),
          ), row=1, col=1)

      # 做空訊號：紅色下三角，標在 K 棒 high 上方
      if not _short_sig.empty:
          _short_dates = _short_sig["date"].tolist()
          _short_highs = df[df["date"].isin(_short_dates)]["high"] * 1.006
          fig.add_trace(go.Scatter(
              x=_short_dates, y=_short_highs.tolist(),
              mode="markers",
              marker=dict(symbol="triangle-down", color="#e74c3c", size=12,
                          line=dict(color="white", width=1)),
              name="MJ做空",
              hovertemplate="做空入場<br>%{x}<br>收盤：%{customdata:.1f}",
              customdata=_short_sig["close"].tolist(),
          ), row=1, col=1)
  ```

- [ ] **Step 2：確認語法無誤**

  ```bash
  python -c "import app"
  ```

  預期：無錯誤

- [ ] **Step 3：Commit**

  ```bash
  git add app.py
  git commit -m "feat: K 線主圖新增 MJ 入場三角標記（做多▲做空▽）"
  ```

---

## Task 5：在圖下方新增 MJ 訊號摘要表格

**Files:**
- Modify: `app.py`（`tab_stock()` 函式，主圖 `st.plotly_chart(...)` 之後）

- [ ] **Step 1：在主圖渲染後加入訊號摘要區塊**

  找到 `app.py` 約第 696 行：

  ```python
  st.plotly_chart(fig, use_container_width=True)

  st.divider()
  ```

  在 `st.divider()` 前插入：

  ```python
  # ── MJ 訊號摘要 ──────────────────────────────────────────────────────────────
  if not mj_signals.empty:
      st.markdown("#### 📍 MJ 強化版入場訊號（近期）")
      _mj_display = mj_signals.copy()
      _mj_display["方向"] = _mj_display["signal"].map({"LONG": "▲ 做多", "SHORT": "▽ 做空"})
      _mj_display = _mj_display.rename(columns={
          "date": "日期", "close": "收盤價", "kd_j": "J值", "macd_osc": "OSC值"
      })[["日期", "方向", "收盤價", "J值", "OSC值"]]

      def _color_signal_mj(val):
          if "做多" in str(val):
              return "color: #27ae60; font-weight: bold"
          if "做空" in str(val):
              return "color: #e74c3c; font-weight: bold"
          return ""

      st.dataframe(
          _mj_display.set_index("日期").style.map(_color_signal_mj, subset=["方向"]),
          use_container_width=True,
      )
      st.caption("MJ訊號：J線穿越零軸且 MACD OSC 同步確認。僅供技術參考，非投資建議。")
  else:
      st.info("近期無 MJ 入場訊號觸發")
  ```

- [ ] **Step 2：確認語法無誤**

  ```bash
  python -c "import app"
  ```

  預期：無錯誤

- [ ] **Step 3：Commit**

  ```bash
  git add app.py
  git commit -m "feat: 個股分析新增 MJ 訊號摘要表格"
  ```

---

## Task 6：整合驗收

- [ ] **Step 1：執行所有測試**

  ```bash
  cd "C:\Users\Neo\Documents\Claude\Projects\台股研究\.claude\worktrees\elated-franklin-7b8e48"
  python -m pytest tests/ -v
  ```

  預期：全部 PASS（含原有 `tests/test_backtest.py`）

- [ ] **Step 2：手動驗收（Streamlit）**

  ```bash
  streamlit run app.py
  ```

  進入「個股分析」頁，輸入 `2330` 或 `2317`，確認：
  - [ ] KD 子圖出現第三條橘紅色 J 線及 y=0 虛線
  - [ ] K 線主圖有綠色上三角 ▲ 或紅色下三角 ▽（若近期有訊號）
  - [ ] 圖下方出現「📍 MJ 強化版入場訊號」摘要表格或「近期無訊號」提示
  - [ ] Hover 三角標記可顯示日期與收盤價

- [ ] **Step 3：最終 commit**

  ```bash
  git add -A
  git commit -m "feat: 完整整合 MJ強化版入場訊號（J線 + 標記 + 摘要表）"
  ```
