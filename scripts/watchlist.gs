/**
 * 台股自選清單 — Google Apps Script Web App backend
 * Setup: replace SPREADSHEET_ID below with your Google Sheets ID.
 * Deploy: Extensions → Apps Script → Deploy → New deployment
 *         Type: Web app | Execute as: Me | Who has access: Anyone
 *
 * 另需設定 GitHub token（供網頁觸發 Actions 用，避免 token 落在瀏覽器）：
 *   專案設定 → 指令碼屬性 → 新增 GH_TOKEN = <fine-grained PAT>
 *   權限範圍：Neolinnnn/stock 的 Actions: Read & Write
 */

const SPREADSHEET_ID = '1Nkcun6gcxMZk-6gqYGJJVSRuT_Q_hJvR-7WX6AE8cxM';
const SHEET_NAME     = 'watchlist';
// Pages 以 docs/ 為發佈根目錄，網址不含 /docs
const PAGES_BASE     = 'https://neolinnnn.github.io/stock';
const MAX_STOCKS     = 50;

// 允許由網頁觸發的 workflow 白名單。此端點對外公開（Who has access: Anyone），
// 不加白名單等於把任意 workflow 的觸發權開放給知道網址的人。
const GH_REPO           = 'Neolinnnn/stock';
const ALLOWED_WORKFLOWS = ['daily_scan.yml', 'scan_one.yml', 'homework_one.yml'];
const DISPATCH_MIN_GAP_SEC = 60;   // 同一 workflow 兩次觸發的最小間隔

function doGet(e) {
  try {
    const action = (e && e.parameter && e.parameter.action) || 'list';
    if (action === 'list') return handleList();
    return jsonResp({ error: 'unknown_action' });
  } catch (err) {
    return jsonResp({ error: err.message || 'server_error' });
  }
}

function doPost(e) {
  try {
    let body;
    try { body = JSON.parse(e.postData.contents); } catch { return jsonResp({ error: 'invalid_json' }); }
    if (body.action === 'add')      return handleAdd(body.id);
    if (body.action === 'remove')   return handleRemove(body.id);
    if (body.action === 'dispatch') return handleDispatch(body.workflow, body.inputs);
    return jsonResp({ error: 'unknown_action' });
  } catch (err) {
    return jsonResp({ error: err.message || 'server_error' });
  }
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

/**
 * 代網頁觸發 GitHub Actions。token 只存在這裡的指令碼屬性，不進瀏覽器。
 * neolinnnn.github.io 是所有 Pages 專案共用的單一 origin，token 若放在
 * localStorage，同源任何頁面（含第三方 CDN 腳本）都讀得到，而 workflow
 * 執行時帶著 9 組 repo secrets。
 */
function handleDispatch(workflow, inputs) {
  if (ALLOWED_WORKFLOWS.indexOf(workflow) === -1) return jsonResp({ error: 'workflow_not_allowed' });

  const token = PropertiesService.getScriptProperties().getProperty('GH_TOKEN');
  if (!token) return jsonResp({ error: 'token_not_configured' });

  // 節流：此端點公開，避免被反覆呼叫而燒掉 Actions 額度
  const cache = CacheService.getScriptCache();
  const key   = 'dispatch_' + workflow;
  if (cache.get(key)) return jsonResp({ error: 'rate_limited' });

  const payload = { ref: 'main' };
  if (inputs && Object.keys(inputs).length) payload.inputs = inputs;

  const res = UrlFetchApp.fetch(
    `https://api.github.com/repos/${GH_REPO}/actions/workflows/${workflow}/dispatches`,
    {
      method: 'post',
      contentType: 'application/json',
      headers: { Accept: 'application/vnd.github+json', Authorization: `Bearer ${token}`,
                 'X-GitHub-Api-Version': '2022-11-28' },
      payload: JSON.stringify(payload),
      muteHttpExceptions: true,
    }
  );

  const code = res.getResponseCode();
  if (code === 204) {
    cache.put(key, '1', DISPATCH_MIN_GAP_SEC);
    return jsonResp({ ok: true });
  }
  return jsonResp({ error: 'dispatch_failed', status: code,
                    detail: String(res.getContentText()).slice(0, 200) });
}

// ── Helpers ────────────────────────────────────────────────────────────────

function getSheet() {
  const sheet = SpreadsheetApp.openById(SPREADSHEET_ID).getSheetByName(SHEET_NAME);
  if (!sheet) throw new Error('sheet_not_found');
  return sheet;
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
      const changeStr = (item.Change || '').trim();
      const change    = parseFloat(changeStr);
      const prev      = (closing !== null && !isNaN(change)) ? closing - change : null;
      const pct       = (prev !== null && prev !== 0) ? +((change / prev) * 100).toFixed(2) : '';
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
    if (!dates || !dates.length || typeof dates[0] !== 'string') return {};

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

const ALERT_EMAIL = 'neo_lin@gemteks.com';

function sendAlertEmail() {
  const rows = getRows();
  if (!rows.length) return;

  const scan = fetchLatestScan();

  const alertIds = rows.filter(({ id }) => {
    const s = scan[id] || {};
    return s.signal && s.signal !== '' && s.signal !== '—';
  });

  if (!alertIds.length) return;

  const twse = fetchTWSE();

  const alerts = alertIds.map(({ id }) => {
    const s = scan[id] || {};
    const t = twse[id] || {};
    return { id, name: t.name || s.name || id, signal: s.signal, rsi: s.rsi, ret20: s.ret20, chipTotal: s.chipTotal };
  });

  const today   = Utilities.formatDate(new Date(), 'Asia/Taipei', 'yyyy-MM-dd');
  const subject = `[台股自選] ${alerts.length} 檔出現訊號 ${today}`;
  const body    = '自選清單中以下股票今日出現訊號：\n\n' +
    alerts.map(a =>
      `• ${a.id} ${a.name} — 訊號 ${a.signal}，RSI ${a.rsi || '—'}，20日 ${a.ret20 != null && a.ret20 !== '' ? Number(a.ret20).toFixed(1) + '%' : '—'}，法人合計 ${a.chipTotal || '—'} 張`
    ).join('\n');

  GmailApp.sendEmail(ALERT_EMAIL, subject, body);
}
