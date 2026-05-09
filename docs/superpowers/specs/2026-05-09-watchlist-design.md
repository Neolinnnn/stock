# 自選清單 (Watchlist) Design Spec

## Goal

Add a shared watchlist tab to the GitHub Pages static site, backed by Google Sheets + Google Apps Script, showing combined real-time price data (TWSE API) and scan signals (daily JSON), with daily Email alerts when signals appear.

## Architecture

Google Apps Script deployed as a Web App acts as the REST backend. It reads/writes a Google Sheets spreadsheet (stock ID list), fetches TWSE Open API for current-day prices, and merges with the existing `docs/YYYYMMDD.json` scan data. GitHub Pages JavaScript calls this single Apps Script endpoint for all CRUD and display operations. A daily time-triggered function in Apps Script checks signals and sends Email via Gmail.

**Tech Stack:** Google Sheets, Google Apps Script, TWSE Open API (`openapi.twse.com.tw`), GitHub Pages (vanilla JS), Gmail

---

## Data Storage

**Google Sheets — one sheet named `watchlist`:**

| Column A (`stock_id`) | Column B (`added_at`) |
|---|---|
| `2330` | `2026-05-09` |
| `2317` | `2026-05-09` |

- Max 50 entries (enforced by Apps Script)
- Deduplication enforced on add

---

## Apps Script Web App Endpoints

**Deployment:** Execute as "Me", access "Anyone" (no auth, shared watchlist).

### GET `?action=list`

Returns merged payload:
```json
{
  "stocks": [
    {
      "id": "2330",
      "name": "台積電",
      "added_at": "2026-05-09",
      "price": 985,
      "change_pct": 1.2,
      "rsi": 62,
      "ret20": 8.3,
      "signal": "買超",
      "chip_total": 5234
    }
  ],
  "updated_at": "2026-05-09T15:30:00"
}
```

Server-side logic:
1. Read all rows from Google Sheets
2. Fetch `https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL` for prices
3. Fetch `https://neolinnnn.github.io/stock/docs/YYYYMMDD.json` (latest date from `dates.json`) for signals
4. Merge by stock ID, return combined array

### POST `{action: "add", id: "2330"}`

- Check duplicate → return `{error: "duplicate"}` if exists
- Check 50-stock limit → return `{error: "limit_reached"}` if exceeded
- Validate ID exists in TWSE data → return `{error: "not_found"}` if invalid
- Append row to sheet
- Return `{ok: true, id: "2330", name: "台積電"}`

### POST `{action: "remove", id: "2330"}`

- Find and delete matching row
- Return `{ok: true}`

---

## Email Alert (Apps Script Time Trigger)

- Triggered daily at **09:00 Taiwan time** (01:00 UTC) — after scan data is published
- Reads watchlist, loads latest `YYYYMMDD.json`
- For each stock with non-empty `signal` field: collect into alert list
- If alert list non-empty: send Gmail to `neo_lin@gemteks.com`

**Email format:**
```
Subject: [台股自選] N 檔出現訊號 2026-05-09

自選清單中以下股票今日出現訊號：

• 2330 台積電 — 外資買超 5,234 張，RSI 62，20日+8.3%
• 2317 鴻海 — 投信買超 1,200 張，RSI 55，20日+3.1%
```

- If watchlist is empty: skip, no email sent
- If no signals today: skip, no email sent
- If daily JSON not yet published (holiday): skip

---

## GitHub Pages UI

### New Tab: `⭐ 自選清單` (4th tab)

```
新增股票： [輸入代碼 2330____] [＋ 新增]

┌──────┬──────┬──────┬──────┬─────┬───────┬────────┬──────┐
│ 代碼 │ 名稱 │ 現價 │漲跌幅│ RSI │ 20日% │  訊號  │ 操作 │
├──────┼──────┼──────┼──────┼─────┼───────┼────────┼──────┤
│ 2330 │台積電│  985 │+1.2% │  62 │ +8.3% │🔴 買超 │  [✕] │
│ 2317 │ 鴻海 │  210 │-0.5% │  48 │ +2.1% │   —    │  [✕] │
└──────┴──────┴──────┴──────┴─────┴───────┴────────┴──────┘

最後更新：2026-05-09 15:30  [↻ 重新整理]
```

- Table sorted by signal (signalled stocks first), then by stock ID
- 訊號 cell highlighted red for buy signals
- `[✕]` triggers POST remove, refreshes table
- Loading spinner shown while fetching
- Empty state: "尚無自選股票，請新增"

### 今日掃描 Tab Addition

Individual stock table gains a `[＋]` button in the rightmost column. Clicking POSTs to Apps Script and shows a toast notification:

- Success: "✓ 2330 已加入自選清單"
- Duplicate: "2330 已在自選清單中"
- Limit: "自選清單已達 50 檔上限"

---

## Error Handling

| Situation | Behavior |
|---|---|
| TWSE API returns no data (holiday/after-hours) | Show `—` for price/change columns |
| Invalid stock ID | Apps Script returns `{error:"not_found"}`, frontend shows "代碼無效" |
| Apps Script timeout (>30s) | Frontend shows "載入失敗，請重試" with retry button |
| Empty watchlist | Show "尚無自選股票，請新增" |
| Duplicate add | Return `{error:"duplicate"}`, show toast "已在清單中" |
| 50-stock limit exceeded | Return `{error:"limit_reached"}`, show toast |
| No daily JSON (holiday) | Signal column shows `—`, price still displayed |
| Email trigger, no signals | Skip silently |

---

## Implementation Sequence

1. **Google Sheets setup** — Create sheet with headers
2. **Apps Script** — Web App with GET list + POST add/remove + email trigger
3. **GitHub Pages** — Add 自選清單 tab with fetch/render/CRUD logic
4. **GitHub Pages** — Add `[＋]` buttons to 今日掃描 stock table
5. **Deploy & test** — Deploy Apps Script, update `APPS_SCRIPT_URL` constant in `index.html`
