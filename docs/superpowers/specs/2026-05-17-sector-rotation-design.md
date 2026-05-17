# 族群輪動回測功能設計

**日期**：2026-05-17
**狀態**：已核准，待實作

---

## 背景

主站目前有每日族群掃描訊號（`docs/{YYYYMMDD}.json` 的 `sectors[]`），但無法驗證「跟著訊號操作」的歷史績效。本 spec 設計一個離線批次回測腳本與獨立呈現頁面，把 13 個月的 sector 訊號資料變成可驗證的策略回測結果。

---

## 系統架構

```
[weekly_summary workflow (週五觸發)]
       │
       ├─ scripts/weekly_summary.py（現有）
       │
       └─ scripts/sector_rotation_backtest.py（新增）
             │
             ├─ 讀 docs/{YYYYMMDD}.json × 全部歷史
             │     └─ 抽出每日 sectors[] + stocks[]
             │
             ├─ FinMind 補資料（cache 到 data/cache/）
             │     ├─ TaiwanStockPrice（個股調整收盤價，所有曾入選股）
             │     ├─ TaiwanStockPrice（加權指數 TAIEX）
             │     └─ 7 日 skip 邏輯；再跑只 patch 新日期
             │
             ├─ run_backtest()
             │     ├─ 訊號矩陣：ret20 / rsi / hot / buy（已在 daily JSON）
             │     ├─ 4 種族群選法 × 2 種頻率(W/M) × 2 種選股法 = 16 variants
             │     ├─ 每 variant 算：equity curve / sharpe / mdd / win rate / turnover
             │     └─ 對比 TAIEX 與等權 67 檔基準
             │
             └─ 輸出 docs/backtest/results.json

[新增頁面 docs/backtest.html]
       └─ fetch results.json
             ├─ 表格：16 variants 排行（Sharpe / 年化 / MDD / 勝率）
             ├─ Plotly：所選 variant 的 equity curve vs TAIEX/等權基準
             ├─ 表格：歷次 rebalance 持股
             └─ 連結回主站
```

**架構決策**：
- 資料來源混合：sector 訊號用既有 `docs/{date}.json`（不重新計算）；個股價格從 FinMind 補齊
- 本地 cache 在 `data/cache/`：首次跑 ~5 分鐘抓全部歷史，之後只 patch 新日期
- 解耦：回測腳本可單獨跑，不影響 daily_scan
- 靜態輸出：`docs/backtest/results.json` 一次寫滿 16 個 variants，前端只負責呈現

---

## 策略變體（16 variants）

### 三個維度

| 維度 | 選項數 | 選項 |
|------|--------|------|
| 族群選法 | 4 | `ret20` / `rsi` / `hot` / `composite`（0.5·ret20_norm + 0.3·hot_norm + 0.2·buy_norm） |
| 族群內選股 | 2 | `ret20_individual`（個股 20 日報酬，取自 `stocks[].ret20`）/ `chip_concentration`（三大法人淨買超合計，取自 `stocks[].chipTotal`） |
| 換股頻率 | 2 | `weekly`（每週一收盤後）/ `monthly`（每月首交易日） |

→ 4 × 2 × 2 = 16 variants
命名格式：`{sector_rule}_{frequency}_{stock_rule}`（例：`composite_W_chip`）

### 持股規則

- **固定組合**：Top-3 族群 × 族群內 Top-3 個股 = **9 檔等權**
- **進場**：訊號日 T 收盤後決定 → T+1 收盤價成交（避免 look-ahead）
- **無流動性過濾**：以 67 檔追蹤池為母體
- **不放空 / 不加槓桿**：純多單

### 交易成本

每次換股扣除：
- 賣出：手續費 0.1425% + 證交稅 0.3%
- 買入：手續費 0.1425%
- 總來回 **0.585%**（換出股票賣 + 換入股票買）

### 績效指標

| 指標 | 算法 |
|------|------|
| CAGR | (終值/初值)^(252/N) - 1 |
| 年化波動 | daily_returns.std() × √252 |
| Sharpe | (CAGR - 0.01) / 年化波動，1% 當無風險利率 |
| MDD | 最大連續回撤 % |
| 勝率 | 賺錢的 rebalance 期數 / 總期數 |
| 平均單期報酬 | 算術平均 |
| 換手率 | 平均每期換掉的檔數 / 9 |

### 基準對照

- **TAIEX** 加權指數 buy & hold（FinMind `TAIEX`）
- **等權 67** = 等權持有全部追蹤股 buy & hold

---

## JSON Schema

**路徑**：`docs/backtest/results.json`

```json
{
  "generated_at": "2026-05-17 11:30",
  "period": {
    "start": "20250415",
    "end":   "20260515",
    "trading_days": 260
  },
  "config": {
    "portfolio_size": 9,
    "sectors_picked": 3,
    "stocks_per_sector": 3,
    "cost_per_turn": 0.00585,
    "rf_rate": 0.01
  },
  "benchmarks": {
    "TAIEX": {
      "equity": [1.0, 1.002, 0.998, "..."],
      "dates":  ["20250415", "20250416", "..."],
      "cagr": 0.082, "vol": 0.18, "sharpe": 0.39, "mdd": -0.21
    },
    "EqualWeight67": { "...同上..." }
  },
  "variants": [
    {
      "id": "composite_W_chip",
      "label": "複合分數 / 週調 / 籌碼選股",
      "sector_rule": "composite",
      "frequency":   "weekly",
      "stock_rule":  "chip_concentration",
      "metrics": {
        "cagr": 0.247, "vol": 0.22, "sharpe": 1.08,
        "mdd": -0.18, "win_rate": 0.62, "turnover": 0.55,
        "avg_period_return": 0.0048
      },
      "equity": [1.0, 1.003, 1.011, "..."],
      "dates":  ["20250415", "..."],
      "rebalances": [
        {
          "date": "20250421",
          "sectors": ["記憶體", "IC設計", "AI伺服器"],
          "holdings": [
            {"stock_id":"2330","name":"台積電","sector":"IC設計","weight":0.111}
          ]
        }
      ]
    }
  ],
  "ranking": [
    {"id": "composite_W_chip", "sharpe": 1.08},
    {"id": "ret20_M_ret20",    "sharpe": 0.94}
  ]
}
```

**檔案大小估算**：16 variants × 260 日 × 8 bytes ≈ 35 KB + rebalances ≈ 50 KB

---

## 前端 UI（`docs/backtest.html`）

```
┌──────────────────────────────────────────────────────────────┐
│  📊 族群輪動回測  (← 回主站)                                  │
│  資料期間 2025-04-15 ~ 2026-05-15 · 共 260 個交易日            │
├──────────────────────────────────────────────────────────────┤
│  ▼ 排行榜（依 Sharpe 排序）                                    │
│  ┌────┬─────────────────────┬──────┬──────┬──────┬───────┐│
│  │排名│ 策略名稱             │ 年化 │Sharpe│ MDD  │ 勝率  ││
│  ├────┼─────────────────────┼──────┼──────┼──────┼───────┤│
│  │ 1  │ 複合分數/週調/籌碼  │+24.7%│ 1.08 │-18% │ 62%  ││← 點 row 切換
│  │ 2  │ ret20/月調/ret20    │+18.3%│ 0.94 │-22% │ 58%  ││
│  │ ...│ ...                 │ ...  │ ...  │ ... │ ...   ││
│  │ B  │ TAIEX 加權指數      │+8.2% │ 0.39 │-21% │ —    ││← 基準
│  │ B  │ 等權持有67檔        │+12.1%│ 0.55 │-19% │ —    ││
│  └────┴─────────────────────┴──────┴──────┴──────┴───────┘│
├──────────────────────────────────────────────────────────────┤
│  ▼ 權益曲線：所選 variant vs 基準（Plotly 折線）               │
│  [策略] [TAIEX] [等權67]  ← 圖例可點切換                       │
├──────────────────────────────────────────────────────────────┤
│  ▼ 持股歷史（時間軸）                                          │
│  ┌──────────────────────────────────────────────┐           │
│  │ 2025-04-21  Top 族群: 記憶體 IC設計 AI伺服器  │           │
│  │   ├─ 記憶體：南亞科 華邦電 旺宏                │           │
│  │   ├─ IC設計：台積電 聯發科 瑞昱                │           │
│  │   └─ AI伺服器：廣達 緯創 神達                  │           │
│  └──────────────────────────────────────────────┘           │
└──────────────────────────────────────────────────────────────┘
```

**互動**：
- 排行表點某 row → 下方 equity chart + 持股歷史切到該 variant
- 預設選中 Sharpe 最高的 variant
- 基準固定顯示 TAIEX 與等權，便於對比
- 配色：沿用 `_PLY` 常數（深色主題）

**進入方式**：
- 主站 `docs/index.html` 右上角加「策略回測」連結
- 反向：`backtest.html` 左上「← 回主站」

---

## 邊界條件與假設

### 資料缺口處理

| 情況 | 處理方式 |
|------|----------|
| 某天 sector 訊號缺失（節日 / 掃描失敗） | 跳過該日 rebalance，沿用上期持股 |
| 某檔個股無價格資料（新上市 / FinMind 缺資料） | 該檔以等權重內其他股代位，不報錯 |
| 族群內可選股不足 3 檔 | 有幾檔買幾檔，權重重新平均 |
| 歷史早期僅有 12 個族群 | 從第一個有完整訊號的日期開始回測 |
| TAIEX 資料缺失 | 顯示 N/A，不阻擋其他結果 |

### 回測假設（明確記錄於 UI 注腳）

- 訊號 T 日收盤產生 → T+1 收盤價成交（避免 look-ahead bias）
- 不滑價：用收盤價成交（小型股不適用，標註於 UI）
- 不做 stop-loss / take-profit：純機械換股
- 股利不計：用調整後收盤價（FinMind 提供）
- 初始本金 100 萬：純數字基準，不影響比較

### 風險警示

頁面底部固定顯示：
> 「過去績效不代表未來，回測未計入滑價、流動性、停損機制，僅供研究參考」

---

## Workflow 整合

```yaml
# .github/workflows/daily_scan.yml 在 Run weekly summary 之後新增

- name: Run sector rotation backtest (Fridays only)
  if: github.event.schedule == '45 7 * * 5'
  continue-on-error: true
  env:
    FINMIND_TOKEN: ${{ secrets.FINMIND_TOKEN }}
  run: python scripts/sector_rotation_backtest.py
```

**重複執行策略**：
- 週五 weekly_summary 後執行（每週一次）
- 7 日 skip：若 `results.json` 的 `generated_at` 在 7 日內，僅 patch 新增日期、不重算
- 本地 cache `data/cache/prices/` 內含個股價格 CSV，跨次執行重複利用

---

## 不在本 spec 範圍

- 即時下單（純回測）
- 機器學習因子（用既有訊號）
- 多空策略 / 槓桿 / 期權
- 自訂 variant UI（先寫死 16 組，未來再開）
- Walk-forward / IS-OOS 切割（用全歷史，未來開）
- Live monitor / Telegram 通知
- 個股下方鑽取（點持股看詳情）

---

## 後續可擴充方向（記錄供未來 spec 用）

1. **個股因子加權**：在 chip_concentration 之外加入基本面（YoY、毛利率）作為選股訊號
2. **動態 N/K**：允許在 UI 上調整族群數與每族群股數
3. **停損機制**：個股單檔虧損超過 X% 強制移除
4. **Walk-forward 切割**：用前 70% 訓練、後 30% 驗證，更可信的績效
5. **Telegram 推播**：每週五自動發本週 Top variant 持股名單
