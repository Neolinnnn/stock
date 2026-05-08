# 台股族群掃描系統 — 開發規範

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

- **預設模型**：`gemini-2.5-flash`（穩定、免費）
- **備用模型**：`gemini-3.1-flash-lite-preview`（最新，KEY2 限定）
- Pro 系列需付費，**不使用**

---

## 環境變數

```
GEMINI_API_KEY=# your api key  # KEY2，支援最多模型
GEMINI_API_KEY_1=# your api key
GEMINI_API_KEY_2=# your api key
```

---

## 專案結構

```
台股研究/
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
