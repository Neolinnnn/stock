/**
 * 台股自選清單 — Google Apps Script Web App backend
 * Setup: replace SPREADSHEET_ID below with your Google Sheets ID.
 * Deploy: Extensions → Apps Script → Deploy → New deployment
 *         Type: Web app | Execute as: Me | Who has access: Anyone
 */

const SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID';
const SHEET_NAME     = 'watchlist';
const PAGES_BASE     = 'https://neolinnnn.github.io/stock/docs';
const MAX_STOCKS     = 50;

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
    if (body.action === 'add')    return handleAdd(body.id);
    if (body.action === 'remove') return handleRemove(body.id);
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
