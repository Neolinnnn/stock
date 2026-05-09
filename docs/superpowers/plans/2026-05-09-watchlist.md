# 自選清單 (Watchlist) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a shared watchlist tab to the GitHub Pages static site, backed by Google Sheets + Google Apps Script, showing real-time prices (TWSE API) and scan signals (daily JSON), with daily email alerts.

**Architecture:** Google Apps Script Web App acts as the REST backend — it reads/writes a Google Sheets spreadsheet, fetches TWSE Open API for prices, and merges with GitHub Pages daily JSON for signals. `docs/index.html` calls this single endpoint for CRUD; a time-triggered Apps Script function sends Gmail alerts daily.

**Tech Stack:** Google Sheets, Google Apps Script, TWSE Open API (`openapi.twse.com.tw`), GitHub Pages (vanilla JS), Gmail

---

## File Structure

| File | Action | Purpose |
|---|---|---|
| `scripts/watchlist.gs` | **Create** | Apps Script source to copy-paste into Google Apps Script editor |
| `docs/index.html` | **Modify** | Add 自選清單 tab (nav link, page div, CSS, JS) and [＋] buttons in 今日掃描 table |

---

## Task 1: Google Sheets Setup (Manual)

**Files:** None (browser-only setup)

- [ ] **Step 1: Create a Google Sheet**

  Go to [sheets.google.com](https://sheets.google.com) → New blank spreadsheet → rename it to `台股自選清單`.

- [ ] **Step 2: Create the watchlist sheet**

  Rename the default `工作表1` tab to `watchlist`.

- [ ] **Step 3: Add headers**

  In cell A1 type `stock_id`, in cell B1 type `added_at`.

- [ ] **Step 4: Copy the Spreadsheet ID**

  From the URL `https://docs.google.com/spreadsheets/d/SPREADSHEET_ID/edit`, copy the long ID string. Save it — you'll need it in Task 2.

---

## Task 2: Google Apps Script — Web App (GET + POST)

**Files:**
- Create: `scripts/watchlist.gs`

- [ ] **Step 1: Create the Apps Script file**

  Create `scripts/watchlist.gs` with this content (replace `YOUR_SPREADSHEET_ID` with the ID from Task 1 Step 4):

  ```javascript
  const SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID';
  const SHEET_NAME     = 'watchlist';
  const PAGES_BASE     = 'https://neolinnnn.github.io/stock/docs';
  const MAX_STOCKS     = 50;

  function doGet(e) {
    const action = (e && e.parameter && e.parameter.action) || 'list';
    if (action === 'list') return handleList();
    return jsonResp({ error: 'unknown_action' });
  }

  function doPost(e) {
    let body;
    try { body = JSON.parse(e.postData.contents); } catch { return jsonResp({ error: 'invalid_json' }); }
    if (body.action === 'add')    return handleAdd(body.id);
    if (body.action === 'remove') return handleRemove(body.id);
    return jsonResp({ error: 'unknown_action' });
  }

  function handleList() {
    const rows = getRows();
    if (!rows.length) return jsonResp({ stocks: [], updated_at: new Date().toISOString() });

    const twse = fetchTWSE();
    const scan = fetchLatestScan();

    const stocks = rows.map(({ id, added_at }) => {
      const t = twse[id] || {};
      const s = scan[id]  || {};
      return {
        id,
        name:       t.name      || s.name  || id,
        added_at,
        price:      t.price      !== undefined ? t.price      : '',
        change_pct: t.change_pct !== undefined ? t.change_pct : '',
        rsi:        s.rsi       || '',
        ret20:      s.ret20     || '',
        signal:     s.signal    || '',
        chip_total: s.chipTotal || '',
      };
    });

    return jsonResp({ stocks, updated_at: new Date().toISOString() });
  }

  function handleAdd(id) {
    if (!id) return jsonResp({ error: 'missing_id' });
    id = String(id).trim();
    const rows = getRows();
    if (rows.length >= MAX_STOCKS)           return jsonResp({ error: 'limit_reached' });
    if (rows.find(r => r.id === id))         return jsonResp({ error: 'duplicate' });

    const twse = fetchTWSE();
    if (!twse[id]) return jsonResp({ error: 'not_found' });

    getSheet().appendRow([id, new Date().toISOString().slice(0, 10)]);
    return jsonResp({ ok: true, id, name: twse[id].name });
  }

  function handleRemove(id) {
    if (!id) return jsonResp({ error: 'missing_id' });
    id = String(id).trim();
    const sheet = getSheet();
    const data  = sheet.getDataRange().getValues();
    for (let i = data.length - 1; i >= 1; i--) {
      if (String(data[i][0]) === id) {
        sheet.deleteRow(i + 1);
        return jsonResp({ ok: true });
      }
    }
    return jsonResp({ error: 'not_found' });
  }

  // ── Helpers ────────────────────────────────────────────────────────────────

  function getSheet() {
    return SpreadsheetApp.openById(SPREADSHEET_ID).getSheetByName(SHEET_NAME);
  }

  function getRows() {
    const data = getSheet().getDataRange().getValues();
    return data.slice(1).filter(r => r[0]).map(r => ({ id: String(r[0]), added_at: String(r[1]) }));
  }

  function fetchTWSE() {
    try {
      const res = UrlFetchApp.fetch(
        'https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL',
        { muteHttpExceptions: true }
      );
      if (res.getResponseCode() !== 200) return {};
      const arr = JSON.parse(res.getContentText());
      const map = {};
      arr.forEach(item => {
        const id      = item.Code;
        const closing = parseFloat((item.ClosingPrice || '').replace(/,/g, '')) || null;
        const change  = parseFloat((item.Change || '').replace(/[^0-9.\-]/g, '')) || 0;
        const prev    = closing !== null ? closing - change : null;
        const pct     = (prev && prev !== 0) ? +((change / prev) * 100).toFixed(2) : '';
        map[id] = { name: item.Name || id, price: closing, change_pct: pct };
      });
      return map;
    } catch { return {}; }
  }

  function fetchLatestScan() {
    try {
      const dRes = UrlFetchApp.fetch(`${PAGES_BASE}/dates.json`, { muteHttpExceptions: true });
      if (dRes.getResponseCode() !== 200) return {};
      const dates = JSON.parse(dRes.getContentText());
      if (!dates || !dates.length) return {};

      const sRes = UrlFetchApp.fetch(`${PAGES_BASE}/${dates[0]}.json`, { muteHttpExceptions: true });
      if (sRes.getResponseCode() !== 200) return {};
      const data = JSON.parse(sRes.getContentText());

      const map = {};
      (data.stocks || []).forEach(s => {
        map[s.id] = { name: s.name, rsi: s.rsi, ret20: s.ret20, signal: s.signal, chipTotal: s.chipTotal };
      });
      return map;
    } catch { return {}; }
  }

  function jsonResp(obj) {
    return ContentService
      .createTextOutput(JSON.stringify(obj))
      .setMimeType(ContentService.MimeType.JSON);
  }
  ```

- [ ] **Step 2: Open Apps Script editor**

  Go to [script.google.com](https://script.google.com) → New project → rename project to `台股自選清單`.

- [ ] **Step 3: Paste the code**

  Delete the default `function myFunction(){}`, paste the contents of `scripts/watchlist.gs`.

- [ ] **Step 4: Manual test — handleList (empty)**

  In the editor toolbar, select function `handleList` → Run.
  Expected: Logger shows `{"stocks":[],"updated_at":"..."}` (no error).

- [ ] **Step 5: Add a test row in Google Sheets**

  In `watchlist` sheet, add row: A2=`2330`, B2=`2026-05-09`.

- [ ] **Step 6: Manual test — handleList with data**

  Run `handleList` again.
  Expected: returns object with `stocks` array containing one entry for 2330 with price and name populated.

- [ ] **Step 7: Manual test — handleAdd**

  In editor, create a temporary test function, run it:
  ```javascript
  function testAdd() {
    const result = handleAdd('2317');
    Logger.log(JSON.stringify(result));
  }
  ```
  Expected: Logger shows `{"ok":true,"id":"2317","name":"鴻海"}` (or similar). Sheet now has row for 2317.

- [ ] **Step 8: Manual test — handleRemove**

  ```javascript
  function testRemove() {
    const result = handleRemove('2317');
    Logger.log(JSON.stringify(result));
  }
  ```
  Expected: `{"ok":true}`. 2317 row removed from sheet.

- [ ] **Step 9: Deploy as Web App**

  Click **Deploy → New deployment** → Type: **Web app**.
  - Execute as: **Me**
  - Who has access: **Anyone**
  
  Click **Deploy** → copy the Web App URL (looks like `https://script.google.com/macros/s/AKfycb.../exec`). Save this URL.

- [ ] **Step 10: Test deployed endpoint**

  In a browser, open: `<YOUR_WEB_APP_URL>?action=list`
  Expected: JSON response with `stocks` array.

- [ ] **Step 11: Commit**

  ```bash
  git add scripts/watchlist.gs
  git commit -m "feat: add Apps Script watchlist backend"
  ```

---

## Task 3: Google Apps Script — Email Alert Trigger

**Files:**
- Modify: `scripts/watchlist.gs` (add `sendAlertEmail` function + setup instructions)

- [ ] **Step 1: Add the alert function**

  Append this to `scripts/watchlist.gs`:

  ```javascript
  const ALERT_EMAIL = 'neo_lin@gemteks.com';

  function sendAlertEmail() {
    const rows = getRows();
    if (!rows.length) return;

    const scan = fetchLatestScan();
    const twse = fetchTWSE();

    const alerts = rows
      .filter(({ id }) => {
        const s = scan[id] || {};
        return s.signal && s.signal !== '' && s.signal !== '—';
      })
      .map(({ id }) => {
        const s = scan[id] || {};
        const t = twse[id] || {};
        return { id, name: t.name || s.name || id, signal: s.signal, rsi: s.rsi, ret20: s.ret20, chipTotal: s.chipTotal };
      });

    if (!alerts.length) return;

    const today   = new Date().toISOString().slice(0, 10);
    const subject = `[台股自選] ${alerts.length} 檔出現訊號 ${today}`;
    const body    = '自選清單中以下股票今日出現訊號：\n\n' +
      alerts.map(a =>
        `• ${a.id} ${a.name} — 訊號 ${a.signal}，RSI ${a.rsi || '—'}，20日 ${a.ret20 !== '' ? a.ret20 + '%' : '—'}，法人合計 ${a.chipTotal || '—'} 張`
      ).join('\n');

    GmailApp.sendEmail(ALERT_EMAIL, subject, body);
  }
  ```

- [ ] **Step 2: Manual test — sendAlertEmail**

  Run `sendAlertEmail` in the editor.
  Expected: If 2330 has a signal today, an email arrives at neo_lin@gemteks.com. If no signals, function returns without sending.

- [ ] **Step 3: Set up time trigger**

  In the Apps Script editor → **Triggers** (alarm clock icon) → **Add Trigger**:
  - Function: `sendAlertEmail`
  - Event source: **Time-driven**
  - Type: **Day timer**
  - Time: **9am to 10am** (Taiwan time, UTC+8 = 01:00–02:00 UTC)

  Click Save.

- [ ] **Step 4: Re-deploy to pick up the new function**

  **Deploy → Manage deployments** → Edit (pencil) → Version: **New version** → Deploy.

- [ ] **Step 5: Commit**

  ```bash
  git add scripts/watchlist.gs
  git commit -m "feat: add email alert trigger to Apps Script"
  ```

---

## Task 4: GitHub Pages — Add 自選清單 Tab & CSS

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Add nav link**

  In `docs/index.html`, find:
  ```html
      <a id="nav-weekly" onclick="setPage('weekly')">週報</a>
  ```
  Replace with:
  ```html
      <a id="nav-weekly"   onclick="setPage('weekly')">週報</a>
      <a id="nav-watchlist" onclick="setPage('watchlist')">⭐ 自選清單</a>
  ```

- [ ] **Step 2: Add page div**

  Find:
  ```html
    <div id="page-daily"  class="page-section"></div>
    <div id="page-weekly" class="page-section"></div>
  ```
  Replace with:
  ```html
    <div id="page-daily"     class="page-section"></div>
    <div id="page-weekly"    class="page-section"></div>
    <div id="page-watchlist" class="page-section"></div>
  ```

- [ ] **Step 3: Add CSS for watchlist**

  Inside `<style>`, before the closing `</style>`, add:
  ```css
  .wl-add-bar{display:flex;gap:8px;margin-bottom:16px;align-items:center;}
  .wl-add-bar input{background:#13161d;color:var(--text);border:1px solid var(--border);border-radius:6px;padding:6px 10px;font-size:13px;width:140px;}
  .wl-add-bar input::placeholder{color:var(--sub);}
  .btn-primary{background:var(--accent);color:#fff;border:none;border-radius:6px;padding:6px 14px;font-size:13px;cursor:pointer;}
  .btn-primary:hover{opacity:.85;}
  .btn-remove{background:transparent;border:1px solid var(--border);color:var(--sub);border-radius:4px;padding:2px 7px;font-size:11px;cursor:pointer;}
  .btn-remove:hover{border-color:var(--red);color:var(--red);}
  .toast{position:fixed;bottom:24px;right:24px;background:#2d3139;color:var(--text);border:1px solid var(--border);border-radius:8px;padding:10px 18px;font-size:13px;z-index:999;opacity:0;transition:opacity .3s;}
  .toast.show{opacity:1;}
  .btn-add-inline{background:transparent;border:1px solid var(--accent);color:var(--accent);border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;margin-left:4px;}
  .btn-add-inline:hover{background:var(--accent);color:#fff;}
  ```

- [ ] **Step 4: Update setPage() to handle watchlist tab**

  Find:
  ```javascript
  function setPage(page){
    CUR_PAGE = page;
    ['daily','weekly'].forEach(p=>{
      document.getElementById(`nav-${p}`).classList.toggle('active', p===page);
      document.getElementById(`page-${p}`).classList.toggle('active', p===page);
    });
    document.getElementById('date-picker-wrap').style.display = page==='daily'?'':'none';
    if(page==='weekly') renderWeekly();
  }
  ```
  Replace with:
  ```javascript
  function setPage(page){
    CUR_PAGE = page;
    ['daily','weekly','watchlist'].forEach(p=>{
      document.getElementById(`nav-${p}`).classList.toggle('active', p===page);
      document.getElementById(`page-${p}`).classList.toggle('active', p===page);
    });
    document.getElementById('date-picker-wrap').style.display = page==='daily'?'':'none';
    if(page==='weekly')    renderWeekly();
    if(page==='watchlist') loadWatchlist();
  }
  ```

- [ ] **Step 5: Add Apps Script URL constant and toast helper**

  In the `<script>` block, after the `let WEEKLY_DATA = null;` line, add:
  ```javascript
  const APPS_SCRIPT_URL = 'PASTE_YOUR_WEB_APP_URL_HERE';
  let WL_DATA = null;

  function showToast(msg) {
    let t = document.getElementById('toast');
    if (!t) { t = document.createElement('div'); t.id='toast'; t.className='toast'; document.body.appendChild(t); }
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 3000);
  }
  ```

- [ ] **Step 6: Verify in browser (no data yet)**

  Open `docs/index.html` locally (via `python -m http.server 8766 --directory docs` then visit `http://localhost:8766`).
  Click "⭐ 自選清單" tab.
  Expected: Tab becomes active, page div visible (empty for now). No JS errors in console.

- [ ] **Step 7: Commit**

  ```bash
  git add docs/index.html
  git commit -m "feat: add watchlist tab skeleton to GitHub Pages"
  ```

---

## Task 5: GitHub Pages — Watchlist Fetch & Render

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Add loadWatchlist() function**

  In `<script>`, after `showToast()`, add:
  ```javascript
  async function loadWatchlist(silent=false) {
    if (!APPS_SCRIPT_URL || APPS_SCRIPT_URL === 'PASTE_YOUR_WEB_APP_URL_HERE') {
      document.getElementById('page-watchlist').innerHTML =
        '<div class="error-box"><span>⚙️</span>請先設定 APPS_SCRIPT_URL</div>';
      return;
    }
    if (!silent) document.getElementById('page-watchlist').innerHTML = '<div id="loading">載入中…</div>';
    try {
      const data = await fetchJSON(`${APPS_SCRIPT_URL}?action=list`);
      WL_DATA = data;
      renderWatchlist(data);
    } catch(e) {
      document.getElementById('page-watchlist').innerHTML =
        '<div class="error-box"><span>❌</span>載入失敗，請重試</div>';
    }
  }
  ```

- [ ] **Step 2: Add renderWatchlist() function**

  After `loadWatchlist()`, add:
  ```javascript
  function renderWatchlist(data) {
    const stocks = (data && data.stocks) || [];
    const updated = data && data.updated_at
      ? new Date(data.updated_at).toLocaleString('zh-TW', {timeZone:'Asia/Taipei'})
      : '';

    let html = `
    <div class="wl-add-bar">
      <input id="wl-input" type="text" placeholder="輸入代碼 例：2330" maxlength="6"
        onkeydown="if(event.key==='Enter')wlAdd()">
      <button class="btn-primary" onclick="wlAdd()">＋ 新增</button>
      <span id="wl-msg" style="font-size:12px;color:var(--sub)"></span>
    </div>`;

    if (!stocks.length) {
      html += `<div class="error-box" style="margin-top:0"><span>📋</span>尚無自選股票，請輸入代碼新增</div>`;
    } else {
      html += `<div class="card" style="overflow-x:auto"><table>
        <tr>
          <th>代碼</th><th>名稱</th><th>現價</th><th>漲跌幅</th>
          <th>RSI</th><th>20日%</th><th>訊號</th><th>法人合計</th><th>操作</th>
        </tr>
        ${stocks.sort((a,b)=>{
          const sa = a.signal && a.signal!=='' ? 0 : 1;
          const sb = b.signal && b.signal!=='' ? 0 : 1;
          return sa - sb || a.id.localeCompare(b.id);
        }).map(s => `<tr>
          <td><b>${s.id}</b></td>
          <td>${s.name}</td>
          <td>${s.price!==''&&s.price!==null ? fmt(s.price,1) : '—'}</td>
          <td class="${Number(s.change_pct)>=0?'up':'dn'}">${s.change_pct!==''&&s.change_pct!==null ? sign(Number(s.change_pct))+fmt(s.change_pct,2)+'%' : '—'}</td>
          <td>${s.rsi!==''&&s.rsi!==null ? `<span class="rsi-dot ${rsiDot(Number(s.rsi))}"></span>${fmt(s.rsi,1)}` : '—'}</td>
          <td class="${signColor(Number(s.ret20))}">${s.ret20!==''&&s.ret20!==null ? sign(Number(s.ret20))+fmt(s.ret20)+'%' : '—'}</td>
          <td>${s.signal ? badge(s.signal) : '—'}</td>
          <td>${chip(s.chip_total)}</td>
          <td><button class="btn-remove" onclick="wlRemove('${s.id}')">✕</button></td>
        </tr>`).join('')}
      </table></div>`;
    }

    html += `<div style="margin-top:10px;font-size:11px;color:var(--sub)">
      最後更新：${updated}&nbsp;&nbsp;
      <button class="date-nav" onclick="loadWatchlist()">↻ 重新整理</button>
    </div>`;

    document.getElementById('page-watchlist').innerHTML = html;
  }
  ```

- [ ] **Step 3: Add wlAdd() and wlRemove() functions**

  After `renderWatchlist()`, add:
  ```javascript
  async function wlAdd(idOverride) {
    const id = (idOverride || document.getElementById('wl-input')?.value || '').trim().toUpperCase();
    if (!id) return;
    const msg = document.getElementById('wl-msg');
    if (msg) msg.textContent = '新增中…';
    try {
      const res = await fetch(APPS_SCRIPT_URL, {
        method: 'POST',
        body: JSON.stringify({ action: 'add', id }),
      });
      const data = await res.json();
      if (data.ok) {
        showToast(`✓ ${id} ${data.name} 已加入自選清單`);
        if (document.getElementById('wl-input')) document.getElementById('wl-input').value = '';
        loadWatchlist(true);
      } else {
        const msgs = { duplicate:'已在清單中', limit_reached:'自選清單已達 50 檔上限', not_found:'代碼無效，請確認股票代碼' };
        showToast(msgs[data.error] || `新增失敗：${data.error}`);
      }
    } catch { showToast('新增失敗，請重試'); }
    if (msg) msg.textContent = '';
  }

  async function wlRemove(id) {
    try {
      const res = await fetch(APPS_SCRIPT_URL, {
        method: 'POST',
        body: JSON.stringify({ action: 'remove', id }),
      });
      const data = await res.json();
      if (data.ok) { showToast(`${id} 已從自選清單移除`); loadWatchlist(true); }
      else showToast(`移除失敗：${data.error}`);
    } catch { showToast('移除失敗，請重試'); }
  }
  ```

- [ ] **Step 4: Test watchlist add/remove in browser**

  Replace `PASTE_YOUR_WEB_APP_URL_HERE` with the actual Apps Script URL (from Task 2 Step 9).

  Open `http://localhost:8766`, click ⭐ 自選清單.
  - Type `2330` → click ＋ 新增 → expect toast "✓ 2330 台積電 已加入自選清單", table refreshes
  - Click ✕ on the row → expect toast "2330 已從自選清單移除", row disappears
  - Type `9999` → expect toast "代碼無效，請確認股票代碼"

- [ ] **Step 5: Commit**

  ```bash
  git add docs/index.html
  git commit -m "feat: add watchlist fetch, render, add/remove to GitHub Pages"
  ```

---

## Task 6: GitHub Pages — Add [＋] Buttons to 今日掃描 Table

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Add [＋] column header to 各族群個股明細 table**

  Find (around line 311):
  ```javascript
        <tr><th>代碼</th><th>名稱</th><th>現價</th><th>RSI</th><th>20日%</th><th>訊號</th><th>CV夏普</th><th>法人合計</th><th>短期目標</th><th>中期目標</th><th>長期目標</th></tr>
  ```
  Replace with:
  ```javascript
        <tr><th>代碼</th><th>名稱</th><th>現價</th><th>RSI</th><th>20日%</th><th>訊號</th><th>CV夏普</th><th>法人合計</th><th>短期目標</th><th>中期目標</th><th>長期目標</th><th></th></tr>
  ```

- [ ] **Step 2: Add [＋] button to each stock row**

  Find (around line 323):
  ```javascript
          <td class="tp">${s.target_long!=null&&s.target_long!==''?fmt(s.target_long,1):'—'}</td>
        </tr>`).join('')}
  ```
  Replace with:
  ```javascript
          <td class="tp">${s.target_long!=null&&s.target_long!==''?fmt(s.target_long,1):'—'}</td>
          <td><button class="btn-add-inline" onclick="wlAdd('${s.id}')" title="加入自選清單">＋</button></td>
        </tr>`).join('')}
  ```

- [ ] **Step 3: Test [＋] button in browser**

  Open `http://localhost:8766`, click 今日掃描.
  Expand any sector → click [＋] on a stock row.
  Expected: toast "✓ XXXX 股票名稱 已加入自選清單". Switch to ⭐ 自選清單 tab — stock appears.

- [ ] **Step 4: Commit**

  ```bash
  git add docs/index.html
  git commit -m "feat: add watchlist shortcut buttons to scan table"
  ```

---

## Task 7: Deploy & Final Verification

**Files:** None (config already wired in Task 5 Step 4)

- [ ] **Step 1: Push to GitHub**

  ```bash
  git push origin main
  ```

- [ ] **Step 2: Wait for GitHub Pages to deploy**

  GitHub Actions deploys Pages within ~2 minutes. Check `https://github.com/Neolinnnn/stock/actions` for Pages build completion.

- [ ] **Step 3: Verify on live site**

  Open `https://neolinnnn.github.io/stock/`:
  - Click ⭐ 自選清單 → table loads with any saved stocks
  - Add a new stock → toast appears, table refreshes
  - Remove a stock → row disappears
  - Click ＋ in 今日掃描 → toast confirms add

- [ ] **Step 4: Verify email alert (optional — wait for next business day)**

  The time trigger fires at 09:00 Taiwan time. After that, check neo_lin@gemteks.com for a `[台股自選]` subject email if any watchlist stocks have signals.

  Alternatively: run `sendAlertEmail` manually in the Apps Script editor to verify immediately.

- [ ] **Step 5: Commit Apps Script URL to a config comment (not secret)**

  Since the Apps Script URL is "anyone" access and not a secret, document it in `docs/index.html` as the `APPS_SCRIPT_URL` constant so future deployments don't need to re-look it up.
