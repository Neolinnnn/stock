# 回測選股 Tab 整合 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `docs/index.html` 新增「⭐ 回測選股」Tab，整合 `backtest/results.json` Sharpe 前五策略持股，並在族群總覽個股名稱欄注入 `★回測` 金色徽章，點擊任何有回測標記的股票可彈出浮動提示卡。

**Architecture:** 純前端修改，只動 `docs/index.html`。在頁面啟動時非同步載入 `./backtest/results.json`，計算 `btMap`（stock_id → 出現次數與策略資訊），渲染時注入 Tab 內容與徽章。浮動提示卡為單例 DOM 元素，事件委派控制顯示與關閉。

**Tech Stack:** Vanilla JS（ES2020），HTML/CSS，已有 `fmt()`/`badge()`/`escHtml()` 等工具函式直接複用。

---

## 檔案對照

| 動作 | 路徑 |
|------|------|
| 修改 | `docs/index.html` |
| 刪除（完成後）| `mockup-backtest-panel.html` |

`index.html` 分成四個插入點：
1. **CSS 區段**（`</style>` 前）— 新增 `.bt-*` class
2. **HTML `<nav>` 區段**（`nav-watchlist` 之後）— 新增 Tab 連結
3. **HTML `<main>` 區段**（`page-watchlist` 之後）— 新增 page section 與 tooltip DOM
4. **JS 區段**（`</script>` 前）— 新增所有 JS 邏輯，並修改現有的 `setPage()` 與 `renderDaily()`

---

## Task 1：CSS — 新增 `.bt-*` 樣式

**Files:**
- Modify: `docs/index.html`（`</style>` 標籤前插入）

- [ ] **Step 1：在 `</style>` 前插入 CSS**

找到 `docs/index.html` 中的 `</style>` 標籤（約第 157 行），在其前面插入：

```css
  /* ── 回測選股 Tab ────────────────────────────────────────────────── */
  .bt-badge{display:inline-block;background:rgba(241,196,15,.15);color:#f1c40f;
    border:1px solid rgba(241,196,15,.4);border-radius:3px;padding:1px 5px;
    font-size:10px;font-weight:700;margin-left:4px;vertical-align:middle;cursor:pointer;}
  .bt-badge:hover{background:rgba(241,196,15,.3);}

  .bt-chip-grid{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px;}
  .bt-chip{background:var(--card);border:1px solid #3a3d55;border-radius:8px;
    padding:10px 14px;cursor:pointer;transition:.15s;min-width:100px;}
  .bt-chip:hover{border-color:#f1c40f;background:#1c1e2f;}
  .bt-chip .bc-id{font-size:11px;color:var(--sub);}
  .bt-chip .bc-name{font-size:14px;font-weight:700;margin:2px 0;}
  .bt-chip .bc-sector{font-size:11px;color:var(--blue);}
  .bt-chip .bc-count{font-size:10px;color:#f1c40f;margin-top:4px;}

  .bt-sub-tab-bar{display:flex;gap:4px;flex-wrap:wrap;margin-bottom:12px;
    border-bottom:1px solid var(--border);padding-bottom:8px;}
  .bt-sub-tab{background:transparent;border:1px solid var(--border);color:var(--sub);
    border-radius:6px;padding:5px 12px;font-size:12px;cursor:pointer;}
  .bt-sub-tab:hover{background:#1e2330;color:var(--text);}
  .bt-sub-tab.active{background:var(--accent);color:#fff;border-color:var(--accent);}

  #bt-tooltip{position:fixed;z-index:500;background:var(--card);
    border:1px solid #3a3d55;border-radius:10px;padding:14px 16px;
    min-width:220px;max-width:280px;box-shadow:0 8px 24px rgba(0,0,0,.5);
    display:none;font-size:13px;}
  #bt-tooltip.show{display:block;}
  #bt-tooltip .bt-tt-header{font-size:15px;font-weight:700;margin-bottom:2px;}
  #bt-tooltip .bt-tt-sub{font-size:11px;color:var(--sub);margin-bottom:10px;}
  #bt-tooltip .bt-tt-row{display:flex;justify-content:space-between;
    padding:4px 0;border-bottom:1px solid #1e2128;font-size:12px;}
  #bt-tooltip .bt-tt-row:last-child{border-bottom:none;}
  #bt-tooltip .bt-tt-lbl{color:var(--sub);}
  #bt-tooltip .bt-tt-strats{margin-top:8px;padding-top:8px;
    border-top:1px solid #1e2128;font-size:11px;color:var(--sub);line-height:1.7;}
  #bt-tooltip .bt-tt-strats strong{color:#f1c40f;}
```

- [ ] **Step 2：驗證 CSS 無語法錯誤**

在瀏覽器直接開啟 `docs/index.html`，打開 DevTools Console，確認無 CSS 解析錯誤。

- [ ] **Step 3：Commit**

```bash
git add docs/index.html
git commit -m "style: add bt-* CSS classes for backtest tab"
```

---

## Task 2：HTML 骨架 — Nav Tab + Page Section + Tooltip DOM

**Files:**
- Modify: `docs/index.html`（nav、main 兩處）

- [ ] **Step 1：在 nav 末端加 Tab 連結**

找到：
```html
    <a id="nav-backtest" href="./backtest.html">📊 策略回測</a>
```
在其前面插入：
```html
    <a id="nav-bt" onclick="setPage('bt')" style="color:#f1c40f;">⭐ 回測選股</a>
```

- [ ] **Step 2：在 main 末端加 page section 與 tooltip**

找到：
```html
  <div id="page-watchlist" class="page-section"></div>
```
在其後插入：
```html
  <div id="page-bt" class="page-section"></div>

  <!-- 回測浮動提示卡（單例） -->
  <div id="bt-tooltip">
    <div class="bt-tt-header" id="bt-tt-name"></div>
    <div class="bt-tt-sub" id="bt-tt-sub"></div>
    <div class="bt-tt-row"><span class="bt-tt-lbl">現價</span><span id="bt-tt-price"></span></div>
    <div class="bt-tt-row"><span class="bt-tt-lbl">RSI5</span><span id="bt-tt-rsi"></span></div>
    <div class="bt-tt-row"><span class="bt-tt-lbl">訊號</span><span id="bt-tt-signal"></span></div>
    <div class="bt-tt-row"><span class="bt-tt-lbl">CV夏普</span><span id="bt-tt-sharpe"></span></div>
    <div class="bt-tt-row"><span class="bt-tt-lbl">ATR停損</span><span id="bt-tt-stop"></span></div>
    <div class="bt-tt-strats"><strong id="bt-tt-count"></strong><br><span id="bt-tt-strat-names"></span></div>
  </div>
```

- [ ] **Step 3：驗證 DOM 結構**

在瀏覽器 DevTools 確認 `document.getElementById('page-bt')` 與 `document.getElementById('bt-tooltip')` 均不為 null。

- [ ] **Step 4：Commit**

```bash
git add docs/index.html
git commit -m "feat: add bt page-section, nav tab, and tooltip DOM"
```

---

## Task 3：JS — `setPage()` 更新（含 bt）

**Files:**
- Modify: `docs/index.html`（`setPage` 函式）

- [ ] **Step 1：更新 setPage()**

找到現有的 `setPage` 函式（約第 973 行）：

```js
function setPage(page){
  CUR_PAGE = page;
  ['daily','weekly','mainforce','stock','watchlist'].forEach(p=>{
    document.getElementById(`nav-${p}`).classList.toggle('active', p===page);
    document.getElementById(`page-${p}`).classList.toggle('active', p===page);
  });
  document.getElementById('date-picker-wrap').style.display =
    (page==='daily' || page==='mainforce') ? '' : 'none';
  if(page==='weekly')    renderWeekly();
  if(page==='watchlist') loadWatchlist();
  if(page==='stock')     initStockPage();
  if(page==='mainforce') renderMainForce(CACHE[CUR_DATE]);
}
```

**完整替換為：**

```js
function setPage(page){
  CUR_PAGE = page;
  ['daily','weekly','mainforce','stock','watchlist','bt'].forEach(p=>{
    const navEl = document.getElementById(`nav-${p}`);
    if(navEl) navEl.classList.toggle('active', p===page);
    const pageEl = document.getElementById(`page-${p}`);
    if(pageEl) pageEl.classList.toggle('active', p===page);
  });
  document.getElementById('date-picker-wrap').style.display =
    (page==='daily' || page==='mainforce') ? '' : 'none';
  if(page==='weekly')    renderWeekly();
  if(page==='watchlist') loadWatchlist();
  if(page==='stock')     initStockPage();
  if(page==='mainforce') renderMainForce(CACHE[CUR_DATE]);
  if(page==='bt')        renderBtPage();
  hideBtTooltip();
}
```

- [ ] **Step 2：驗證 Tab 可切換**

在瀏覽器點擊「⭐ 回測選股」Tab，確認：
- nav 連結高亮（金色 active 狀態）
- `page-bt` 出現（其餘 page section 隱藏）
- 日期選擇器隱藏（與 weekly 行為一致）
- Console 無錯誤（`renderBtPage` 尚未定義此時不打緊，後面 Task 5 補上）

- [ ] **Step 3：Commit**

```bash
git add docs/index.html
git commit -m "feat: wire bt tab into setPage routing"
```

---

## Task 4：JS — 載入 results.json 並建立 btMap

**Files:**
- Modify: `docs/index.html`（全域變數區 + init 函式 + 新增函式）

- [ ] **Step 1：在全域變數區加宣告**

找到：
```js
let WL_DATA = null;
```
在其後插入：
```js
let BT_RESULTS = null;
let BT_MAP = new Map(); // stock_id -> {count, strategies:[{id,label,sharpe}], name, sector}
```

- [ ] **Step 2：新增 loadBtResults() 函式**

在 `fetchJSON` 函式定義後插入：

```js
async function loadBtResults(){
  try{
    BT_RESULTS = await fetchJSON('./backtest/results.json?v=' + Math.floor(Date.now()/86400000));
    BT_MAP = buildBtMap(BT_RESULTS);
  }catch(e){
    console.warn('backtest/results.json 載入失敗：', e.message);
  }
}

function buildBtMap(results){
  const map = new Map();
  if(!results || !results.ranking || !results.variants) return map;
  const top5ids = results.ranking.slice(0,5).map(r=>r.id);
  top5ids.forEach(id=>{
    const variant = results.variants.find(v=>v.id===id);
    if(!variant || !variant.rebalances || !variant.rebalances.length) return;
    const lastRb = variant.rebalances[variant.rebalances.length-1];
    if(!lastRb.holdings) return;
    lastRb.holdings.forEach(h=>{
      if(!map.has(h.stock_id)){
        map.set(h.stock_id, {count:0, strategies:[], name:h.name, sector:h.sector});
      }
      const entry = map.get(h.stock_id);
      entry.count++;
      entry.strategies.push({id:variant.id, label:variant.label, sharpe:results.ranking.find(r=>r.id===id)?.sharpe||0});
    });
  });
  return map;
}
```

- [ ] **Step 3：在 init() 中並行載入 results.json**

找到 `init()` 函式內，`fetchJSON` 呼叫結束、`buildDateSelect()` 之前的位置。確切找到這一行：

```js
  fetchJSON(`./weekly.json?v=${bust}`).then(d=>{WEEKLY_DATA=d;}).catch(()=>{});
```

在其後插入：

```js
  loadBtResults();  // 非同步載入，不阻塞主流程
```

- [ ] **Step 4：驗證 btMap 有資料**

在瀏覽器 DevTools Console 執行：
```js
BT_MAP.size  // 應 > 0（約 9~27 筆，取決於策略重疊）
[...BT_MAP.entries()].slice(0,3)  // 應顯示 [['2454', {count:5, ...}], ...]
```

- [ ] **Step 5：Commit**

```bash
git add docs/index.html
git commit -m "feat: load backtest results and build btMap"
```

---

## Task 5：JS — 浮動提示卡

**Files:**
- Modify: `docs/index.html`（新增函式）

- [ ] **Step 1：新增 showBtTooltip() 與 hideBtTooltip()**

在 `buildBtMap` 函式後插入：

```js
function buildDailyMap(){
  const d = CACHE[CUR_DATE];
  const m = new Map();
  if(!d || !d.stocks) return m;
  d.stocks.forEach(s=> m.set(s.id, s));
  // mainForce 補充 stopLoss
  if(d.mainForce) d.mainForce.forEach(s=>{ if(m.has(s.id)) m.get(s.id).stopLoss = s.stopLoss; });
  return m;
}

function showBtTooltip(sid, event){
  const bt = BT_MAP.get(sid);
  if(!bt) return;
  const daily = buildDailyMap().get(sid);

  document.getElementById('bt-tt-name').textContent = `${bt.name}（${sid}）`;
  document.getElementById('bt-tt-sub').textContent  = bt.sector || '';
  document.getElementById('bt-tt-price').textContent   = daily ? fmt(daily.price,1)   : '—';
  document.getElementById('bt-tt-rsi').textContent     = daily ? fmt(daily.rsi,1)     : '—';
  document.getElementById('bt-tt-signal').innerHTML    = daily && daily.signal ? badge(daily.signal) : '—';
  document.getElementById('bt-tt-sharpe').textContent  = daily ? fmt(daily.sharpe,2)  : '—';
  document.getElementById('bt-tt-stop').textContent    = daily && daily.stopLoss!=null ? fmt(daily.stopLoss,1) : '—';
  document.getElementById('bt-tt-count').textContent   = `⭐ 出現 ${bt.count}/5 策略`;
  document.getElementById('bt-tt-strat-names').textContent = bt.strategies.map(s=>s.label).join('、');

  const tt = document.getElementById('bt-tooltip');
  tt.classList.add('show');

  // 定位：緊貼點擊處，自動偵測邊界
  const margin = 12;
  let x = event.clientX + margin;
  let y = event.clientY + margin;
  tt.style.left = '0'; tt.style.top = '0'; // 先顯示才能取得寬高
  const w = tt.offsetWidth, h = tt.offsetHeight;
  const vw = window.innerWidth, vh = window.innerHeight;
  if(x + w > vw - margin) x = event.clientX - w - margin;
  if(y + h > vh - margin) y = event.clientY - h - margin;
  tt.style.left = Math.max(margin, x) + 'px';
  tt.style.top  = Math.max(margin, y) + 'px';
}

function hideBtTooltip(){
  document.getElementById('bt-tooltip').classList.remove('show');
}
```

- [ ] **Step 2：在 document click 事件關閉 tooltip**

找到現有的 `document.addEventListener('click', ...)` 事件（約第 397 行，在 `initStockPage` 內）。
在 `init()` 函式末尾（`setPage('daily')` 之後）插入：

```js
  document.addEventListener('click', e=>{
    if(!e.target.closest('#bt-tooltip') && !e.target.closest('.bt-chip') && !e.target.closest('.bt-badge'))
      hideBtTooltip();
  });
```

- [ ] **Step 3：Commit**

```bash
git add docs/index.html
git commit -m "feat: add bt floating tooltip show/hide logic"
```

---

## Task 6：JS — ★回測 徽章注入 renderDaily

**Files:**
- Modify: `docs/index.html`（`renderDaily` 函式內）

- [ ] **Step 1：修改個股名稱欄**

找到 `renderDaily` 函式內的 `stks.map(s=>...)` 區塊，找到這一行（約第 1122 行）：

```js
          <td>${s.id}</td><td><b>${s.name}</b></td>
```

替換為：

```js
          <td>${s.id}</td><td><b>${escHtml(s.name)}</b>${BT_MAP.has(s.id)?`<span class="bt-badge" onclick="showBtTooltip('${escHtml(s.id)}',event);event.stopPropagation()">★回測</span>`:''}</td>
```

- [ ] **Step 2：驗證徽章顯示**

在瀏覽器切換到「今日掃描」Tab，展開任一族群（如「光通訊」），確認：
- `華星光`、`聯鈞` 後出現金色 `★回測` 小標籤（若在 btMap 中）
- 不在 btMap 中的個股名稱無徽章
- 點擊徽章彈出浮動提示卡，顯示正確資訊
- 點擊卡片外任意處關閉提示卡

- [ ] **Step 3：Commit**

```bash
git add docs/index.html
git commit -m "feat: inject bt-badge in sector stock table"
```

---

## Task 7：JS — renderBtPage()

**Files:**
- Modify: `docs/index.html`（新增函式）

- [ ] **Step 1：新增 renderBtPage()**

在 `hideBtTooltip()` 後插入完整函式：

```js
function renderBtPage(){
  const el = document.getElementById('page-bt');
  if(!BT_RESULTS){
    el.innerHTML = '<div class="error-box"><span>⏳</span>回測資料載入中，請稍後再切換此 Tab</div>';
    return;
  }
  if(!BT_RESULTS.ranking || !BT_RESULTS.variants){
    el.innerHTML = '<div class="error-box"><span>❌</span>回測資料格式異常</div>';
    return;
  }

  const top5ids = BT_RESULTS.ranking.slice(0,5).map(r=>r.id);
  const top5variants = top5ids.map(id=>BT_RESULTS.variants.find(v=>v.id===id)).filter(Boolean);
  const dailyMap = buildDailyMap();

  // ── 上層：交集精選 ────────────────────────────────────────────────
  const intersect = [...BT_MAP.entries()]
    .filter(([,v])=>v.count>=2)
    .sort((a,b)=>b[1].count-a[1].count)
    .slice(0,10);

  let html = `<div class="section-title">⭐ 交集精選（出現在 2+ 策略的個股）</div>`;

  if(!intersect.length){
    html += `<div class="card" style="margin-bottom:20px;color:var(--sub);font-size:13px;padding:16px;">目前五個策略持股無重疊，請查看下方各策略持倉</div>`;
  } else {
    html += `<div class="bt-chip-grid">`;
    intersect.forEach(([sid,bt])=>{
      const daily = dailyMap.get(sid);
      const sig = daily?.signal || '';
      html += `<div class="bt-chip" onclick="showBtTooltip('${escHtml(sid)}',event)">
        <div class="bc-id">${escHtml(sid)}</div>
        <div class="bc-name">${escHtml(bt.name)}</div>
        <div class="bc-sector">${escHtml(bt.sector)}</div>
        <div class="bc-count">★ 出現 ${bt.count}/5 策略</div>
        ${sig ? `<div style="margin-top:4px">${badge(sig)}</div>` : ''}
      </div>`;
    });
    html += `</div>`;
  }

  // ── 下層：各策略持倉子 Tab ─────────────────────────────────────────
  html += `<div class="section-title">各策略持倉明細</div>`;
  html += `<div class="bt-sub-tab-bar">`;
  top5variants.forEach((v,i)=>{
    const sharpe = BT_RESULTS.ranking.find(r=>r.id===v.id)?.sharpe?.toFixed(2) || '—';
    html += `<button class="bt-sub-tab${i===0?' active':''}" onclick="btSwitchTab(this,${i})">#${i+1} ${escHtml(v.label)}（${sharpe}）</button>`;
  });
  html += `</div>`;

  top5variants.forEach((v,i)=>{
    const lastRb = v.rebalances?.[v.rebalances.length-1];
    const rbDate = lastRb?.date ? fmtDate(lastRb.date) : '—';
    const holdings = lastRb?.holdings || [];
    html += `<div class="bt-sub-panel card" id="bt-panel-${i}" style="${i===0?'':'display:none'}">`;
    html += `<div style="font-size:11px;color:var(--sub);margin-bottom:8px;">持倉截至 ${rbDate}</div>`;
    if(!holdings.length){
      html += `<div style="color:var(--sub);font-size:13px;">無持倉資料</div>`;
    } else {
      html += `<div style="overflow-x:auto"><table>
        <tr><th>代碼</th><th>名稱</th><th>族群</th><th>權重</th><th>今日 RSI5</th><th>今日訊號</th></tr>
        ${holdings.map(h=>{
          const daily = dailyMap.get(h.stock_id);
          return `<tr onclick="showBtTooltip('${escHtml(h.stock_id)}',event)" style="cursor:pointer">
            <td>${escHtml(h.stock_id)}</td>
            <td><b>${escHtml(h.name)}</b>${BT_MAP.get(h.stock_id)?.count>=2?`<span class="bt-badge">★交集</span>`:''}</td>
            <td>${escHtml(h.sector)}</td>
            <td>${(h.weight*100).toFixed(1)}%</td>
            <td>${daily ? fmt(daily.rsi,1) : '—'}</td>
            <td>${daily && daily.signal ? badge(daily.signal) : '—'}</td>
          </tr>`;
        }).join('')}
      </table></div>`;
    }
    html += `</div>`;
  });

  el.innerHTML = html;
}

function btSwitchTab(btn, idx){
  document.querySelectorAll('.bt-sub-tab').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('.bt-sub-panel').forEach((p,i)=>{
    p.style.display = i===idx ? '' : 'none';
  });
}
```

- [ ] **Step 2：驗證 Tab 完整功能**

在瀏覽器點擊「⭐ 回測選股」Tab，確認：
1. 上方出現交集精選 Chip 群，每個 Chip 顯示代碼、名稱、族群、出現次數
2. 點擊 Chip 彈出浮動提示卡，顯示現價/RSI/訊號/夏普/停損/策略名稱
3. 下方顯示子 Tab 列，預設選中 `#1`
4. 切換子 Tab 正確顯示對應策略持倉表格
5. 持倉表格每行可點擊彈出浮動提示卡
6. 出現在 2+ 策略的個股在持倉表格內標 `★交集` 徽章

- [ ] **Step 3：Commit**

```bash
git add docs/index.html
git commit -m "feat: implement renderBtPage with intersection chips and sub-tabs"
```

---

## Task 8：Edge Case 驗證與清理

**Files:**
- Modify: `docs/index.html`（確認各邊界條件）
- Delete: `mockup-backtest-panel.html`

- [ ] **Step 1：測試 results.json 載入失敗情況**

在 DevTools → Network → 找到 `results.json` 請求，右鍵 Block request URL，重新整理頁面後切換到「回測選股」Tab。應顯示「回測資料載入中，請稍後再切換此 Tab」，不報 JS 錯誤。

Unblock 後重新整理，恢復正常。

- [ ] **Step 2：測試 BT_MAP 無交集情況**

在 DevTools Console 暫時覆寫：
```js
BT_MAP = new Map([['2454',{count:1,strategies:[],name:'聯發科',sector:'IC設計'}]]);
renderBtPage();
```
應顯示「目前五個策略持股無重疊…」說明文字，下方子 Tab 仍正常顯示。

恢復：`loadBtResults().then(()=>renderBtPage())`

- [ ] **Step 3：測試 daily 資料缺失**

對一個不在掃描清單的 stock_id（如 `9999`）呼叫：
```js
showBtTooltip('9999', {clientX:200, clientY:200});
```
應不顯示（因為 `BT_MAP.get('9999')` 為 undefined，函式直接 return）。

- [ ] **Step 4：手機寬度測試**

DevTools Device Toolbar 設為 375px，切換到「回測選股」Tab：
- Chip 卡片應換行顯示
- 子 Tab 列應可橫向捲動（`flex-wrap:wrap` 已處理）
- 浮動提示卡不超出視窗

- [ ] **Step 5：刪除 mockup 檔**

```bash
git rm mockup-backtest-panel.html
git add docs/index.html
git commit -m "chore: remove mockup file, finalize backtest tab"
```

---

## 完成標準

- [ ] 切換到「⭐ 回測選股」Tab 不報錯
- [ ] 交集精選 Chip 群顯示正確個股（可在 Console 對照 `[...BT_MAP.entries()].filter(([,v])=>v.count>=2)`）
- [ ] 子 Tab 切換正常，持倉表格欄位完整
- [ ] 浮動提示卡：點擊觸發、點擊外部關閉、不超出視窗邊界
- [ ] 族群總覽個股表格：btMap 內的個股名稱後有 `★回測` 金色標籤
- [ ] Console 無 JS 錯誤
