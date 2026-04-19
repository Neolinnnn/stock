// ============================================================
// 台股族群掃描 - Google Apps Script Web App
// 部署方式：擴充功能 → Apps Script → 部署 → 新增部署 → 類型選 Web 應用程式
//           執行身份：我 / 存取權限：所有人
// ============================================================

const SHEET_ID = '1NzrQlsW8vQLPPius47Enc-4Kit55PYjuQ8IOiyLLl8c';

function doGet(e) {
  const page = e.parameter.page || 'daily';
  const html = HtmlService.createTemplateFromFile('index');
  html.page = page;
  html.data = JSON.stringify(getData(page));
  return html.evaluate()
    .setTitle('台股族群掃描')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

function getData(page) {
  const ss = SpreadsheetApp.openById(SHEET_ID);

  if (page === 'weekly') {
    return getWeeklyData(ss);
  }
  return getDailyData(ss);
}

function getDailyData(ss) {
  // 最新摘要
  const summaryWs = ss.getSheetByName('最新摘要');
  const summaryVals = summaryWs ? summaryWs.getDataRange().getValues() : [];

  const meta = {};
  const sectors = [];
  const chips = [];
  let chipStart = -1;

  summaryVals.forEach((row, i) => {
    const key = row[0];
    if (i < 7) {
      meta[key] = row[1];
    } else if (row[0] === '族群') {
      // skip header
    } else if (row[0] === '代碼') {
      chipStart = i + 1;
    } else if (chipStart === -1 && row[0] && row[0] !== '族群' && i >= 10) {
      sectors.push({ sector: row[0], ret20: row[1], rsi: row[2], buy: row[3], hot: row[4] });
    } else if (chipStart > 0 && i >= chipStart && row[0]) {
      chips.push({ id: row[0], name: row[1], sector: row[2], total: row[3], foreign: row[4], trust: row[5], dealer: row[6] });
    }
  });

  // 個股明細（全部）
  const detailWs = ss.getSheetByName('每日掃描');
  const detailVals = detailWs ? detailWs.getDataRange().getValues() : [];
  const latestDate = meta['掃描日期'] || '';
  const stocks = detailVals
    .slice(1)
    .filter(r => r[0] === latestDate)
    .map(r => ({
      date: r[0], sector: r[1], id: r[2], name: r[3],
      price: r[4], rsi: r[5], ret20: r[6], signal: r[7],
      sharpe: r[8], foreign: r[9], trust: r[10], dealer: r[11],
      chipTotal: r[12], news: r[13]
    }));

  return { meta, sectors, chips, stocks };
}

function getWeeklyData(ss) {
  const ws = ss.getSheetByName('週報');
  if (!ws) return { meta: {}, changes: [], buys: [] };
  const vals = ws.getDataRange().getValues();
  const meta = { date: vals[0]?.[1], days: vals[1]?.[1] };
  const changes = [], buys = [];
  let buyStart = -1;
  vals.forEach((row, i) => {
    if (row[0] === '族群') return;
    if (row[0] === '個股') { buyStart = i + 1; return; }
    if (buyStart === -1 && i >= 4 && row[0]) changes.push({ sector: row[0], change: row[1] });
    if (buyStart > 0 && i >= buyStart && row[0]) buys.push({ stock: row[0], days: row[1] });
  });
  return { meta, changes, buys };
}
