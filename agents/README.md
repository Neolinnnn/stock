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
| 技術面分析師 | `analysts.technical` | summary.json（RSI/動能/Sharpe/signal）+ docs/stocks 指標 |
| 基本面分析師 | `analysts.fundamental` | `docs/fundamentals/<id>.json`（FinMind 月營收/EPS/毛利率） |
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

`technical 0.45 / sentiment 0.30 / fundamental 0.25`（見 `analysts.WEIGHTS`）。兩個關鍵規則：

1. **總經不進個股加權。** 大盤 regime 對當日所有個股是同一個常數，加進去只是替每檔加上同樣的
   偏移，沒有鑑別度卻壓縮其他面向的分數空間。總經改為只在 `decision()` 調整部位上限與行動門檻。
2. **缺資料的面向排除後重新歸一化**（`composite_score`）。`coverage == "none"` 代表「不知道」，
   不是「中性 0 分」——當 0 分計會把全體分數往 0 拉，決策系統性偏保守。
   每檔實際生效的權重記在 `decision.weights_used`，介面上會顯示。

## 靜態網址

| 路徑 | 內容 |
|------|------|
| `docs/agents/index.html` | 最新一日（主入口） |
| `docs/agents/<date>.html` | 每日永久網址，保留最近 90 天（`build_preview._KEEP_DAYS`） |
| `docs/agents/charts/<date>.json` | 走勢圖資料，前端非同步載入 |
| `docs/agents/dates.json` | 歷史日期索引，供頁面日期下拉 |

- 深層連結：`#<族群名>/<股票代號>`，可分享、重整、上一頁。
- 走勢圖抽成外部 JSON 後首屏 JSON 由 ~555KB 降到 ~204KB。
- preview 版（`不加 --docs`）仍為單檔自包含、圖表內嵌，`file://` 可直接開。
- 自動化：`.github/workflows/daily_scan.yml` 每日掃描後跑
  `pipeline.py --macro --gemini`（失敗自動降級）→ `build_preview.py --docs`，
  產物由既有 commit 步驟一併推上 GitHub Pages。

```bash
python agents/build_preview.py --docs          # 手動產生靜態頁
```

## LLM 覆核層（`llm_review.py`，獨立執行）

確定性主幹算完後，挑少數幾檔請 LLM 當魔鬼代言人。**不接入每日掃描**，
由 `.github/workflows/agents_review.yml` 手動觸發，產物不進 `docs/`。

```bash
python agents/llm_review.py                    # 最新一日，Top 10
python agents/llm_review.py --date 20260804 --top 15
python agents/llm_review.py --dry-run          # 不呼叫 API，只跑規則檢查
```

設計上的三條線：

- **LLM 不覆寫任何欄位。** 輸出獨立的 `review_<date>.json`／`.md`，`analysis_<date>.json`
  原封不動。回測基礎設施因此不受影響，也才能事後比對「LLM 的異議當時對不對」。
- **算術歸規則、判斷歸 LLM。** 風報比、停損距離、覆蓋率這些先由 `risk_reward()` 算好
  再餵進 prompt，不讓 LLM 自己算數字。
- **只送該送的。** 覆核名單＝綜合分數 Top-N ＋ 被規則判定 error 級的個股，
  一次呼叫涵蓋全部（約 12 檔／次），送出前剔除 chart 與新聞全文。

規則檢查分兩級，避免「例外變常態」：

| 級別 | 意義 | 處理 |
|------|------|------|
| `error` | 邏輯上說不通（如目標價 ≤ 進場價、單一面向卻報高信心） | 逐檔列出，強制納入覆核 |
| `warn` | 值得留意但普遍存在（如風報比偏低） | 由 `structural_summary()` 彙總成一則結論 |

分級是實測逼出來的：初版把風報比不足也當個案標記，20260804 一天就標了 56/102 檔。
限縮到「建議進場」的個股後仍有 21/22 檔不足——那是進場區固定 -3%、停損卻是 2×ATR
造成的結構性偏差，不是 21 個獨立問題，喊 21 次只會淹掉真正說不通的那 5 檔。

## 為什麼不整包導入 TradingAgents

評估過 `TauricResearch/TradingAgents`（Apache 2.0，可用），結論是只借角色分工概念：

1. **資料層不對盤**：其 `dataflows/` 是 Alpha Vantage / yfinance / FRED / Reddit /
   StockTwits / Polymarket，拿不到台股三大法人、分點與月營收——那正是本專案的資料優勢。
2. **規模不符**：`propagate()` 每檔跑完整 graph（4 分析師＋多空辯論＋交易員＋3 風控＋經理，
   約 12–15 次 LLM 呼叫）。102 檔／天 ≈ 1,300 次呼叫，且預設走付費模型。
3. **可回測性**：本專案的 `backtest_results_*.json` 成立前提是同輸入同輸出；
   LLM 決策無法用既有基礎設施回測。

## Gemini 文字生成

以**族群為單位批次呼叫**（`gemini_text.attach_summaries`）：18 次／日，而非逐檔 102 次，
免費額度才撐得住，Gemini 也才看得到同族群橫向比較。送出的記錄已剔除 chart 與新聞全文。
降級（無金鑰／HTTP 錯誤／回應無法解析）一律寫 stderr，CI log 看得到原因。

## 新聞時效與連結（news.py）

- 每則新聞標「今日 / 昨日 / N天前」時效 badge。
- 連結優先用爬蟲存的 `url`（`daily_scan.fetch_news` 已增強抓 cnyes `newsId`）；
  舊資料無 url 時退回 Google News 搜尋連結。
- 外電／總經事件來自 macro grounding，含來源連結，於介面頂部顯示（影響全市場）。
