// ============================================================
// 每日掃描外部觸發器
// GitHub 的 schedule 事件實測延遲 5～9 小時，workflow_dispatch 則即時開跑，
// 故由 Apps Script 時間觸發器在交易日晚間呼叫 dispatch API 當主排程，
// daily_scan.yml 的 cron 只做備援（當日報告已存在即跳過）。
// 休市日判斷在 workflow 的 check job（auto=true 時套用），這裡只濾週末。
//
// 一次性設定：
//   1. GitHub → Settings → Developer settings → Fine-grained tokens → Generate new token
//      Repository access：Only select repositories → Neolinnnn/stock
//      Permissions：Actions → Read and write（其餘不給）
//   2. Apps Script 編輯器 → 專案設定 → 指令碼屬性 → 新增
//      GH_DISPATCH_TOKEN = <上一步的 token>
//   3. 編輯器上方選 installDailyScanTrigger → 執行（首次會要求授權外部連線與觸發器）
//
// 失敗處理：token 過期／被撤銷時 dispatchDailyScan 會拋錯，Apps Script 會寄失敗通知信；
// 期間 GitHub 備援排程仍會（延遲地）補跑。換新 token 只需改指令碼屬性，不必重裝觸發器。
// 手動執行 dispatchDailyScan 會真的觸發一次完整掃描（交易日），測試時請留意額度。
// ============================================================

const GH_DISPATCH_URL =
  'https://api.github.com/repos/Neolinnnn/stock/actions/workflows/daily_scan.yml/dispatches';

function dispatchDailyScan() {
  // 'u'：1＝週一 … 7＝週日（以台灣時間判斷，不受專案時區設定影響）
  const weekday = Number(Utilities.formatDate(new Date(), 'Asia/Taipei', 'u'));
  if (weekday > 5) return;

  const token = PropertiesService.getScriptProperties().getProperty('GH_DISPATCH_TOKEN');
  if (!token) throw new Error('指令碼屬性缺少 GH_DISPATCH_TOKEN');

  const res = UrlFetchApp.fetch(GH_DISPATCH_URL, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + token,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    // dispatch API 的 inputs 一律傳字串
    payload: JSON.stringify({ ref: 'main', inputs: { auto: 'true' } }),
    muteHttpExceptions: true,
  });
  const code = res.getResponseCode();
  // 成功為 204（要求回傳 run 資訊時為 200）；401＝token 失效，404＝token 無此 repo 權限
  if (code < 200 || code >= 300) {
    throw new Error('dispatch 失敗 HTTP ' + code + '：' + res.getContentText());
  }
}

// 安裝（或重裝）每日觸發器：台灣時間 20:30 前後 15 分鐘內執行。
// 不早於 20:00：FinMind 個股法人買賣表官方更新時間為 20:00，太早會拿到前一日籌碼。
function installDailyScanTrigger() {
  ScriptApp.getProjectTriggers()
    .filter((t) => t.getHandlerFunction() === 'dispatchDailyScan')
    .forEach((t) => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('dispatchDailyScan')
    .timeBased()
    .atHour(20)
    .nearMinute(30)
    .everyDays(1)
    .inTimezone('Asia/Taipei')
    .create();
}
