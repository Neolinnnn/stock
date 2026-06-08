# agents — 台股族群／個股多代理分析後端

改寫自 [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents) 的多代理流程，套用到本專案，**族群下鑽到各股**。

> ⚠️ 與 `docs/` 靜態網址產出**完全解耦**。輸出寫到 `agents/output/`、介面寫到 `preview/`，皆已 gitignore。

## 流程（對應 TradingAgents）

```
daily_reports/<date>/summary.json  ─┐
macro 模組 regime_score            ─┴─→ 分析師團隊(4) → 多空辯論 → 交易員 → 風控 → 投組經理
                                                            │
                                              agents/output/analysis_<date>.json
                                                            │
                                              preview/族群分析台_<date>.html
```

| 角色 | 實作 | 資料源 |
|------|------|--------|
| 技術面分析師 | `analysts.technical` | summary.json（RSI/動能/Sharpe/signal） |
| 基本面分析師 | `analysts.fundamental` | 新聞標題抽營收年增 |
| 新聞總經分析師 | `analysts.macro` | macro 模組 `regime_score` |
| 情緒/籌碼分析師 | `analysts.sentiment` | 法人買賣超 + 新聞聲量 |
| 多空研究員 | `analysts.researchers` | 由分析師訊號生成多/空論點 |
| 交易員/風控/投組經理 | `analysts.decision` | 綜合分數 × regime → 行動/部位/停損 |

## 分工（遵循 CLAUDE.md）

- **評分、辯論、決策規則** → `analysts.py`（Claude 程式邏輯，確定性、可回測）
- **文字敘述** → `gemini_text.py`（預設模板；加 `--gemini` 才呼叫 Gemini 改寫）

## 用法

```bash
# 1) 跑分析（離線即可，吃既有 daily_reports）
python agents/pipeline.py --date 20260605
python agents/pipeline.py --gemini          # 啟用 Gemini 文字（需 GEMINI_API_KEY）
python agents/pipeline.py --macro           # 串接 macro 即時 regime（需網路）

# 2) 產生資料驅動介面
python agents/build_preview.py --date 20260605
# → preview/族群分析台_20260605.html（瀏覽器直接開）
```

## 綜合分數權重

`technical 0.35 / sentiment 0.25 / fundamental 0.20 / macro 0.20`（技術面為主，補上其餘三面向），可於 `analysts.WEIGHTS` 調整。

## 靜態網址整合（已實作：方案 1＋3）

- **方案 1（獨立新頁）**：`build_preview.py --docs` 輸出到 `docs/agents/index.html`（每日覆寫為最新），
  並於主站 `docs/index.html` 導覽列加「🤖 多代理分析」連結。不影響既有頁面網址。
- **方案 3（自動化）**：`.github/workflows/daily_scan.yml` 於每日掃描後自動執行
  `pipeline.py --macro --gemini`（失敗自動降級）→ `build_preview.py --docs`，
  產物由既有 commit 步驟一併推上 GitHub Pages。

```bash
python agents/build_preview.py --docs          # 手動產生靜態頁
```

## 新聞時效與連結（news.py）

- 每則新聞標「今日 / 昨日 / N天前」時效 badge。
- 連結優先用爬蟲存的 `url`（`daily_scan.fetch_news` 已增強抓 cnyes `newsId`）；
  舊資料無 url 時退回 Google News 搜尋連結。
- 外電／總經事件來自 macro grounding，含來源連結，於介面頂部顯示（影響全市場）。
