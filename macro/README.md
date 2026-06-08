# macro — 宏觀情緒面濾網（獨立模組）

為既有「純技術面」掃描補上「由上而下」的市場 regime 濾網，解決**整體趨勢誤判**與**資訊落後**問題。

> ⚠️ 本模組與主專案**完全解耦**：輸出只寫到 `macro/output/`，不接 `daily_scan` / `build_docs` / `docs/`，**不影響 GitHub Pages 靜態網址**。

## 三階段架構

| 階段 | 檔案 | 內容 | 來源 | 分工 |
|------|------|------|------|------|
| 一 | `market_regime.py` | 國際指標 → `risk_score` (-100~+100) | yfinance：SOX/VIX/美債/DXY/台幣/S&P | Claude 邏輯 |
| 二 | `macro_events.py` | TWSE 重訊 + 川普/總經即時頭條 | MOPS API + **Gemini Google Search grounding** | Gemini 蒐集/判讀 |
| 三 | `regime_score.py` | 融合成最終 `regime_score` + 部位建議 | — | Claude 邏輯 |

階段二的國際情緒用 **Gemini 3.5-flash 內建即時搜尋**抓川普社群發言與總經頭條，這是純技術面拿不到、且能避免資訊落後的關鍵。

## 安裝

```bash
pip install -r macro/requirements.txt   # yfinance + beautifulsoup4（與主專案分開）
```

需要環境變數 `GEMINI_API_KEY`（KEY2，沿用 CLAUDE.md 規範）才能跑階段二的國際情緒。

## 使用

```bash
python macro/run_macro.py            # 完整三階段（需網路 + GEMINI_API_KEY）
python macro/run_macro.py --no-events  # 只跑階段一（國際指標）
python macro/run_macro.py --demo     # 離線自測，用假資料驗證流程
```

輸出：`macro/output/macro_YYYYMMDD.json`（完整）與 `.md`（人類可讀）。

各模組也可單獨執行驗證：`python macro/market_regime.py` 等。

## regime_score → 部位建議對照

| regime_score | regime | 建議曝險 | 基調 |
|---|---|---|---|
| ≥ 35 | risk_on | 90% | 積極 |
| 10 ~ 35 | mild_risk_on | 70% | 偏多 |
| -10 ~ 10 | neutral | 50% | 中性 |
| -35 ~ -10 | mild_risk_off | 30% | 保守 |
| ≤ -35 | risk_off | 10% | 避險 |

## 後續整合（目前刻意「不」做，以免影響靜態網址）

未來若要併入主流程，建議的接點：
1. 把 `regime_score` 寫進 `daily_scan` 目前空著的 `summary.json["market"]`。
2. 用 `regime_score` 調整族群評分與部位建議（regime 為避險時自動減碼）。
3. 讓 `gemini_writer` 的 `market_narrative` 引用 regime 結論。

## 設計備註

- **graceful degradation**：任一指標 / 來源 / 金鑰缺失時自動跳過並標記，整體不中斷（中性計分）。
- 權重（階段一 0.7 / 階段二 0.3）與評分規則皆寫死於程式，可回測、可稽核。
