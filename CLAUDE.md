# 台股族群掃描系統 — 開發規範

## 語言規範

- **所有回覆請使用繁體中文**（程式碼、指令、技術術語除外）

---

## AI 分工架構

本專案採用雙模型協作：

| 角色 | 模型 | 負責範疇 |
|------|------|----------|
| **文字生成** | Gemini API (KEY2) | 每日摘要、週報、個股分析、市場敘述等所有 TEXT 輸出 |
| **程式邏輯** | Claude | 架構設計、程式碼撰寫、邏輯驗證、資料流規劃 |

### 原則
- Gemini 處理**高 token 消耗**的文字任務（1M context window，免費額度大）
- Claude 專注**思考型**工作：架構決策、邏輯驗證、pipeline 設計
- 兩者不共享記憶，每次呼叫 Gemini 需透過 JSON 傳入必要背景

---

## Gemini 呼叫介面規範

所有呼叫 Gemini 的任務統一使用 `gemini_writer.py`，傳入標準 JSON payload：

```python
from gemini_writer import GeminiWriter

writer = GeminiWriter()
result = writer.generate(task="daily_summary", context={...})
```

### Task 類型

| task | 說明 |
|------|------|
| `daily_summary` | 每日族群掃描摘要 |
| `weekly_report` | 週報敘述 |
| `stock_analysis` | 個股深度分析 |
| `market_narrative` | 大盤市場敘述 |

### Context JSON 格式

```json
{
  "task": "daily_summary",
  "date": "20260419",
  "data": { ... },
  "extra": "額外指示（選填）"
}
```

---

## 使用模型

- **預設模型**：`gemini-2.5-flash`（實在的最新 GA、免費額度、原生 Google Search grounding）
- **備用模型**：`gemini-2.0-flash`（穩定，高量低成本）
- Pro 系列需付費，**不使用**

### Groq 備援

Gemini 仍是預設供應商。Groq 僅在 Gemini 所有 Key 都耗盡或持續失敗時接手，
不主動取代。模型由 `GROQ_MODEL` 環境變數指定（Groq 的模型上下架頻繁，
使用前請以 `GET https://api.groq.com/openai/v1/models` 確認當下可用者）。

**以下任務永不退回 Groq**，因其提示詞要求搜尋最新法說會、年報與新聞稿，
依賴 Gemini 的 Google Search grounding；Groq 無內建搜尋，只會拿訓練截止前
的舊資料編出看似合理的數字：

| task | 呼叫者 |
|------|--------|
| `product_mix` | `scripts/enrich_product_mix.py`、`scripts/scan_one_stock.py` |
| `fundamental_homework` | `scripts/fundamental_homework.py` |

清單定義於 `gemini_writer.GROUNDING_REQUIRED_TASKS`，由 `generate()` 強制套用，
呼叫端無法以 `allow_fallback=True` 繞過。

`GeminiWriter` 只要任一供應商有 Key 即可建構：Gemini Key 暫時缺失或被撤銷時，
不需 grounding 的任務仍走得到 Groq 備援；需 grounding 的任務則在 `generate()`
階段正確失敗，不會拿舊資料充數。兩邊皆無 Key 才拋 `ValueError`。

---

## 呼叫層結構

所有 LLM 請求都經由 `gemini_writer.py`，不得自行組 HTTP 請求：

```
call_llm()      分派層：先 Gemini，失敗且允許時退 Groq
├── call_gemini()   Gemini HTTP 出口（多 Key 輪替、429 換 Key、503 重試）
└── call_groq()     Groq HTTP 出口（OpenAI 相容，多 Key 輪替、429 換 Key）
```

- `GeminiWriter.generate()` — 走 `PROMPTS` 模板的任務，失敗時拋出例外
- `agents/gemini_text._call_gemini()` — 族群敘述，失敗時回 `None` 由呼叫端
  降級為確定性模板，pipeline 不中斷

---

## 環境變數

```
GEMINI_API_KEY=# your api key  # KEY2，支援最多模型
GEMINI_API_KEY_1=# your api key
GEMINI_API_KEY_2=# your api key

GROQ_API_KEY=# your api key    # 選填；未設定則無備援，行為同以往
GROQ_MODEL=# 選填，預設 llama-3.3-70b-versatile
```

Key 收集規則：依序讀 `<PREFIX>`、`<PREFIX>_1`、`<PREFIX>_2`…，**序號中斷即停止**
（設了 `_1` 未設 `_2`，則 `_3` 不會被讀取）。

---

## 大戶定義

「大戶」一律指集保股權分散表（TDCC）的持股張數級距。

**儲存為原始互斥區間，顯示為累計門檻**——集保給的是互斥區間，但判讀大戶
動向時要看的是「持股 N 張以上的人共握有多少」，故前端顯示前累加：

| 顯示（累計門檻） | JSON key | 集保原始互斥區間（股） | 累計方式 |
|-----------------|----------|---------------------|---------|
| 1000 張以上 | `lv1000_up` | 1,000,001 以上 | 自身 |
| 800 張以上 | `lv800_1000` | 800,001 ~ 1,000,000 | ＋1000 張以上 |
| 600 張以上 | `lv600_800` | 600,001 ~ 800,000 | ＋800 張以上 |

- JSON、快照與 `tdcc_holders` 一律存**原始互斥值**，累加只在前端
  （`dashboard.js` 的 `cumulativeHolders()`）進行，兩種視角都算得出來
- 三個累計值**不可相加**：600 張以上已涵蓋另外兩層
- 例（2330 / 2026-09-18）：互斥值 84.70 / 0.76 / 0.94
  → 累計顯示 1000 張以上 84.70%、800 張以上 85.46%、600 張以上 86.40%

- 資料源：集保 open data `https://opendata.tdcc.com.tw/getOD.ashx?id=1-5`
  （免認證。FinMind 的 `taiwan_stock_holding_shares_per` 需贊助等級帳號，
  register 等級會被擋，且該資料集本來就是轉賣這份公開資料）
- 追蹤指標：各級距**持股比例的週變化**（百分點）
- **週頻**：集保每週五結算、次週初公布，與日 K 無法逐日對齊
- 集保 CSV 只給分級編號不給級距文字，13/14/15 依序對應上表三個級距，
  對照表見 `scripts/tdcc_holders.LEVELS` 的 docstring
- 集保端點**只給最新一期**，故每期存一份快照於 `data/holders/YYYYMMDD.json`，
  時間序列由累積的快照組出（`tdcc_holders.load_series`）。歷史深度隨時間增加
- 驗證工具：`python scripts/verify_holders.py 2330`，或 Actions 的
  「驗證集保大戶資料」workflow（免 token，任何環境都能跑）

與三大法人買賣超的區別：法人是**流量**（這幾日買賣多少），大戶是**存量**
（目前握有多少），兩者可互相印證但不可混用。

---

## 專案結構

```
stock_research/
├── app.py                 # Streamlit UI
├── gemini_writer.py       # Gemini 文字生成模組（Claude 不直接生成文字）
├── daily_reports/         # 每日掃描 JSON 輸出
├── docs/                  # GitHub Pages 靜態頁面
├── gas/                   # Google Apps Script
└── .github/workflows/     # CI/CD 自動掃描
```

---

## 開發邊界說明

- `docstring` / 程式碼說明文件 → **Claude 撰寫**（屬於程式邏輯說明）
- 報告內文、市場分析、個股敘述 → **Gemini 生成**
- 架構圖、流程規劃、資料結構設計 → **Claude 負責**

---

## 網頁上線流程

**改動 `docs/` 底下的頁面後，先在本機驗證，再開 PR 合併。**

- 本機預覽：`python -m http.server 8766 --directory docs`，瀏覽器開 `http://localhost:8766`
- 驗證範圍：改到的按鈕實際點過、圖表有正常渲染、console 無錯誤
- 確認無誤後開 PR，合併到 main 即由 GitHub Pages 部署

> 早期用 `docs/preview.html`（index.html 的線上沙盒副本）做測試版，
> 已於 2026-07-26 廢除：兩檔需手動保持同步，容易發生只改其中一檔、
> 另一檔把修正蓋回去的情形。改為本機驗證。

# AI Behavior Guidelines (by Andrej Karpathy)

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.