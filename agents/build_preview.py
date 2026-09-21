"""
把 pipeline 產出的 analysis_<date>.json 渲染成自包含、資料驅動的 HTML 介面。

資料直接內嵌進 HTML（file:// 可直接開，免後端、免 CORS）。
介面：左欄族群排行 → 個股清單；右欄個股詳情（4 分析師 / 多空辯論 / 決策）。

用法：
    python agents/build_preview.py                 # 取最新 analysis_*.json → preview/
    python agents/build_preview.py --date 20260605
    python agents/build_preview.py --docs          # → docs/agents/（靜態網址）

輸出：
    preview/族群分析台_<date>.html   單檔自包含（走勢圖內嵌，file:// 可直接開）
    docs/agents/<date>.html          每日永久網址
    docs/agents/index.html           指向最新一日（內容同上）
    docs/agents/charts/<date>.json   走勢圖資料（非同步載入，減少首屏體積）
    docs/agents/dates.json           歷史日期索引
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "agents" / "output"
PREVIEW_DIR = ROOT / "preview"
DOCS_DIR = ROOT / "docs" / "agents"
_KEEP_DAYS = 90        # docs/agents 歷史頁保留天數（每天約 300KB HTML + 360KB 圖表）

_HTML = r"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>台股族群分析台 __DATE__</title>
<meta name="description" content="__DATE__ 台股族群多代理分析：18 族群逐檔技術／基本／籌碼面評分與部位建議。改寫自 TradingAgents。">
<meta name="theme-color" content="#0b0e14">
<meta property="og:type" content="website">
<meta property="og:title" content="台股族群分析台 __DATE__">
<meta property="og:description" content="多代理（技術／基本／總經／籌碼）逐檔評分與部位建議。">
<meta name="twitter:card" content="summary">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 64 64'><text y='52' font-size='52'>🤖</text></svg>">
<style>
:root{--bg:#0b0e14;--panel:#161a22;--panel2:#1b2029;--border:#272c37;--txt:#e8eaed;
--dim:#8b93a3;--accent:#3d8bfd;--accent2:#7c5cff;--bull:#2ecc71;--bear:#e74c3c;--warn:#f1b143;--chip:#21262d}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);line-height:1.6;-webkit-font-smoothing:antialiased;
font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang TC','Noto Sans TC','Microsoft JhengHei',sans-serif}
header{background:rgba(17,21,29,.92);backdrop-filter:blur(10px);border-bottom:1px solid var(--border);padding:14px 28px;position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.title{font-size:19px;font-weight:800;display:flex;align-items:center;gap:9px;
background:linear-gradient(90deg,var(--accent),var(--accent2));-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.title .dot{width:9px;height:9px;border-radius:50%;background:var(--bull);box-shadow:0 0 8px var(--bull)}
.hmeta{font-size:13px;color:var(--dim);margin-left:auto;display:flex;gap:18px;flex-wrap:wrap}
.hmeta b{color:var(--txt)}
.regime{padding:2px 10px;border-radius:20px;font-size:12px;font-weight:700}
.r-on{background:rgba(46,204,113,.15);color:var(--bull);border:1px solid var(--bull)}
.r-neu{background:rgba(241,177,67,.15);color:var(--warn);border:1px solid var(--warn)}
.r-off{background:rgba(231,76,60,.15);color:var(--bear);border:1px solid var(--bear)}
.layout{display:grid;grid-template-columns:235px 255px 1fr;gap:0;height:calc(100vh - 60px)}
.col{overflow-y:auto;border-right:1px solid var(--border);padding:12px}
.col h4{font-size:11px;color:var(--dim);letter-spacing:1px;margin:6px 8px 10px}
.row{padding:9px 11px;border-radius:9px;cursor:pointer;font-size:13px;margin-bottom:4px;border:1px solid transparent;transition:.15s}
.row .rtop{display:flex;justify-content:space-between;align-items:center;gap:8px}
.row:hover{background:var(--chip)}
.row.active{background:var(--panel2);border-color:var(--accent);box-shadow:0 2px 10px rgba(61,139,253,.18)}
.row .sc{font-weight:700;font-size:12px;padding:1px 7px;border-radius:5px;white-space:nowrap}
.row .scorebar{height:3px;border-radius:2px;background:#252b38;margin-top:6px;overflow:hidden}
.row .scorebar i{display:block;height:100%;border-radius:2px}
.pos{color:var(--bull)}.neg{color:var(--bear)}.neu{color:var(--warn)}
.detail{overflow-y:auto;padding:22px 26px}
.dhead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.dhead .nm{font-size:22px;font-weight:800}.dhead .id{color:var(--dim)}
.dhead .pr{font-size:18px;font-weight:700;margin-left:auto}
.action{font-size:15px;font-weight:800;padding:5px 16px;border-radius:9px}
.a-buy{background:rgba(46,204,113,.18);color:var(--bull);border:1px solid rgba(46,204,113,.45)}
.a-part{background:rgba(61,139,253,.18);color:var(--accent);border:1px solid rgba(61,139,253,.45)}
.a-wait{background:rgba(241,177,67,.18);color:var(--warn);border:1px solid rgba(241,177,67,.45)}
.a-cut{background:rgba(231,76,60,.18);color:var(--bear);border:1px solid rgba(231,76,60,.45)}
.sumtxt{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 16px;font-size:14px;color:#c9d1d9;margin:14px 0 18px}
.sec-h{font-size:13px;font-weight:700;margin:22px 0 12px;padding-left:10px;letter-spacing:1px;position:relative}
.sec-h::before{content:'';position:absolute;left:0;top:2px;bottom:2px;width:3px;border-radius:2px;background:linear-gradient(180deg,var(--accent),var(--accent2))}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.tab{background:var(--chip);border:1px solid var(--border);color:var(--dim);padding:7px 14px;border-radius:9px;cursor:pointer;font-size:13px;font-weight:600;transition:.15s}
.tab:hover{color:var(--txt)}
.tab.active{background:linear-gradient(135deg,var(--accent),var(--accent2));color:#fff;border-color:transparent;box-shadow:0 2px 10px rgba(61,139,253,.35)}
.acard{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:18px;display:none}
.acard.active{display:block}
.acard .ah{display:flex;align-items:center;gap:10px;margin-bottom:12px}
.acard .ah .v{font-size:12px;font-weight:700;padding:2px 10px;border-radius:6px;margin-left:auto}
.kv{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:9px;margin:4px 0}
.kv .c{background:var(--panel2);border-radius:8px;padding:9px 11px}
.kv .c .k{font-size:11px;color:var(--dim)}.kv .c .v{font-size:15px;font-weight:700}
.chart{background:var(--panel2);border-radius:8px;padding:10px 10px 8px;margin-bottom:12px}
.chart svg{display:block;width:100%;height:130px}
.chart .lg{font-size:11px;color:var(--dim);display:flex;gap:14px;margin-top:6px;align-items:center}
.chart .lg b{font-weight:700;color:var(--txt)}
.tr-table{width:100%;border-collapse:collapse;font-size:12.5px}
.tr-table td{padding:7px 9px;border-top:1px solid var(--border);vertical-align:top;color:#c9d1d9}
.tr-table tr:first-child td{border-top:none}
.glossary{font-size:11px;color:var(--dim);line-height:1.9;background:var(--panel);border:1px dashed var(--border);border-radius:8px;padding:8px 12px;margin-bottom:12px}
.glossary b{color:var(--accent)}
.stocklink{color:var(--accent);font-size:13px;font-weight:600;text-decoration:none;border:1px solid var(--accent);border-radius:7px;padding:3px 10px;margin-left:6px}
.stocklink:hover{background:rgba(88,166,255,.12)}
.debate{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:6px}
.side{border-radius:10px;padding:15px;border:1px solid var(--border)}
.side.bull{background:linear-gradient(160deg,rgba(63,185,80,.08),transparent)}
.side.bear{background:linear-gradient(160deg,rgba(248,81,73,.08),transparent)}
.side h3{font-size:14px;margin-bottom:8px}.side.bull h3{color:var(--bull)}.side.bear h3{color:var(--bear)}
.side ul{list-style:none;font-size:13px;color:#c9d1d9}
.side li{padding:4px 0 4px 16px;position:relative}.side li::before{content:"▸";position:absolute;left:0}
.side.bull li::before{color:var(--bull)}.side.bear li::before{color:var(--bear)}
.dec{background:linear-gradient(135deg,rgba(61,139,253,.08),var(--panel));border:1px solid rgba(61,139,253,.45);border-radius:12px;padding:18px;margin-top:8px;box-shadow:0 4px 18px rgba(0,0,0,.25)}
.dec .rg{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-top:6px}
.dec .rc{background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:11px}
.dec .rc .k{font-size:11px;color:var(--dim)}.dec .rc .v{font-size:16px;font-weight:700;margin-top:2px}
.flag{display:inline-block;font-size:11px;background:var(--chip);border-radius:5px;padding:2px 8px;margin:2px 4px 0 0}
.seccard{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:12px;margin-bottom:12px}
.seccard .st{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px}
.seccard .st b{font-size:14px}.seccard .vd{font-size:12px;font-weight:700;padding:1px 8px;border-radius:5px}
.seccard .mini{font-size:11px;color:var(--dim);margin-top:4px}
.seccard .mini .bp{color:var(--bull)}.seccard .mini .br{color:var(--bear)}
.news-list,.ev-list{display:flex;flex-direction:column;gap:8px}
.news-item,.ev-item{background:var(--panel);border:1px solid var(--border);border-radius:9px;padding:10px 12px;font-size:13px}
.news-item a,.ev-item a{color:var(--txt);text-decoration:none}.news-item a:hover,.ev-item a:hover{color:var(--accent);text-decoration:underline}
.news-meta{font-size:11px;color:var(--dim);margin-top:4px;display:flex;gap:10px;align-items:center}
.rec{font-size:10px;font-weight:700;padding:1px 7px;border-radius:10px;background:rgba(88,166,255,.15);color:var(--accent)}
.rec.today{background:rgba(63,185,80,.18);color:var(--bull)}
.ev-impact{font-size:10px;font-weight:700;padding:1px 7px;border-radius:5px}
.imp-bull{background:rgba(63,185,80,.15);color:var(--bull)}.imp-bear{background:rgba(248,81,73,.15);color:var(--bear)}.imp-neu{background:var(--chip);color:var(--dim)}
.note{font-size:11px;color:var(--dim);padding:14px 26px;border-top:1px solid var(--border)}
.note b{color:var(--warn)}
.hsearch{background:var(--chip);border:1px solid var(--border);color:var(--txt);border-radius:8px;
  padding:5px 10px;font-size:13px;width:180px;font-family:inherit}
.hsearch:focus{outline:none;border-color:var(--accent)}
.hsel{background:var(--chip);border:1px solid var(--border);color:var(--txt);border-radius:8px;
  padding:4px 8px;font-size:12px;font-family:inherit}
.filters{display:flex;gap:5px;flex-wrap:wrap;margin:0 8px 8px}
.fchip{font-size:11px;padding:3px 9px;border-radius:20px;background:var(--chip);border:1px solid var(--border);
  color:var(--dim);cursor:pointer;user-select:none}
.fchip.on{background:rgba(61,139,253,.18);border-color:var(--accent);color:var(--accent)}
.row:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.empty{color:var(--dim);font-size:13px;padding:24px;text-align:center}
.cov{font-size:10px;font-weight:700;padding:1px 7px;border-radius:5px;background:var(--chip);color:var(--dim)}
/* ── 手機 RWD：3 欄改為「族群橫向條 + 個股清單 + 全寬詳情」直向堆疊 ── */
@media (max-width:820px){
  header{padding:12px 16px;gap:10px}
  .title{font-size:17px}
  .hmeta{font-size:11px;gap:10px;width:100%;margin-left:0}
  .layout{display:block;height:auto}
  .col{border-right:none;border-bottom:1px solid var(--border);overflow-y:auto;padding:10px}
  /* 族群列改成可橫向滑動的籌碼條，節省垂直空間 */
  #col-sectors{display:flex;gap:8px;overflow-x:auto;overflow-y:hidden;
    -webkit-overflow-scrolling:touch;padding:10px}
  #col-sectors h4{display:none}
  #col-sectors .row{flex:0 0 auto;margin-bottom:0;gap:8px}
  /* 個股清單（含族群綜述卡）限高，確保下方詳情可觸及 */
  #col-stocks{max-height:38vh}
  .detail{overflow:visible;padding:16px 14px}
  .debate{grid-template-columns:1fr}
  .dhead{gap:8px}.dhead .nm{font-size:19px}.dhead .pr{margin-left:0}
  .stocklink{margin-left:0}
  .chart svg{height:120px}
  .chart .lg{flex-wrap:wrap;gap:8px}
  .kv{grid-template-columns:repeat(auto-fit,minmax(108px,1fr))}
  .dec .rg{grid-template-columns:repeat(auto-fit,minmax(108px,1fr))}
  .tr-table{font-size:11.5px}.tr-table td{padding:6px 6px}
  .note{padding:12px 16px}
}
</style></head><body>
<header>
  <div class="title"><span class="dot"></span>台股族群分析台</div>
  <input class="hsearch" id="h-search" list="stk-list" placeholder="搜尋代號／名稱…"
         aria-label="搜尋個股" autocomplete="off">
  <datalist id="stk-list"></datalist>
  <div class="hmeta">
    <span>日期 <select class="hsel" id="h-datesel" aria-label="切換日期"></select></span>
    <span>大盤 <b id="h-regime"></b></span>
    <span>族群 <b id="h-sec"></b>｜個股 <b id="h-cnt"></b></span>
  </div>
</header>
<div class="layout">
  <div class="col" id="col-sectors"><h4>族群排行</h4></div>
  <div class="col" id="col-stocks"><h4>個股（族群內排行）</h4></div>
  <div class="detail" id="detail"></div>
</div>
<div class="note"><b>※ 本頁為程式化研究輸出，非投資建議。</b>
所列進場區、停損、目標價與部位比例皆由規則運算產生，未考慮個人財務狀況，投資決策與風險請自行承擔。
評分／決策由 agents/analysts.py 產生；文字敘述可選 Gemini 改寫。
資料源：daily_reports（技術／籌碼）+ docs/fundamentals（月營收／EPS／毛利率）+ macro 模組（大盤 regime）。
分析基準日 <span id="n-date"></span>，頁面產生於 <span id="n-built"></span>。</div>
<script>
const DATA = __DATA__;
const CHART_URL = __CHART_URL__;   // null = 走勢圖已內嵌；字串 = 另外非同步載入
const DATES = __DATES__;
const BUILT_AT = "__BUILT_AT__";
let curSec=0, curStk=0, CURSTK=null, CHARTS=null, filterAction='';

// 指標簡寫 → 看得懂的中文名
const LABELS={
  ret_20d:'20日報酬', rsi:'RSI(14)', cv_sharpe:'回測夏普值', signal:'技術訊號',
  rev_month:'營收月份', rev_yoy:'月營收年增', rev_yoy_3m:'近三月年增均值',
  cum_yoy:'累計營收年增', eps_yoy:'最新季EPS年增', gross_margin:'毛利率',
  gm_delta:'毛利率季變動', news_count:'新聞則數',
  regime_score:'大盤情緒分數', regime:'大盤狀態', stock_news:'個股新聞數',
  '外資':'外資買賣超', '投信':'投信買賣超', '合計':'三大法人合計'};
const UNITS={ret_20d:'%', rev_yoy:'%', rev_yoy_3m:'%', cum_yoy:'%', eps_yoy:'%',
  gross_margin:'%', gm_delta:'pp', '外資':' 張', '投信':' 張', '合計':' 張'};
const COV_LABEL={financials:'財報資料', news:'僅新聞推估', none:'查無資料',
  indicators:'完整指標', basic:'基本價量', chip:'法人籌碼', regime:'大盤 regime'};
const LOTS=new Set(['外資','投信','合計']);
function fmtVal(k,v){
  if(v===null||v===undefined||v==='') return '—';
  if(typeof v==='number'){
    const n=LOTS.has(k)?Math.round(v).toLocaleString():v;
    return `${n}${UNITS[k]||''}`;
  }
  return v;
}
// 技術線圖（TradingAgents 技術分析師對應線）：布林帶 + 5/20/60MA + 收盤，純 SVG client 端畫
function sparkSVG(ch){
  if(!ch||!ch.close||ch.close.length<2) return '';
  const c=ch.close, n=c.length, W=560,H=150,P=8;
  const series=[ch.bb_upper,ch.bb_lower,ch.ma5,ch.ma20,ch.ma60].filter(a=>a&&a.length===n);
  let all=c.slice(); series.forEach(a=>all=all.concat(a.filter(x=>x!=null&&x>0)));
  const lo=Math.min(...all), hi=Math.max(...all), rng=(hi-lo)||1;
  const X=i=>P+i*(W-2*P)/(n-1);
  const Y=v=>P+(H-2*P)*(1-(v-lo)/rng);
  const ok=(a,i)=>a&&a[i]!=null&&a[i]>0;
  const path=a=>a?a.map((v,i)=>!ok(a,i)?'':`${(i&&ok(a,i-1))?'L':'M'}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(' '):'';
  let s=`<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">`;
  // 布林帶：上下軌間填色
  if(ch.bb_upper&&ch.bb_lower&&ch.bb_upper.length===n&&ch.bb_lower.length===n){
    let up=[],dn=[];
    for(let i=0;i<n;i++){ if(ok(ch.bb_upper,i))up.push(`${X(i).toFixed(1)},${Y(ch.bb_upper[i]).toFixed(1)}`); }
    for(let i=n-1;i>=0;i--){ if(ok(ch.bb_lower,i))dn.push(`${X(i).toFixed(1)},${Y(ch.bb_lower[i]).toFixed(1)}`); }
    if(up.length&&dn.length) s+=`<polygon points="${up.concat(dn).join(' ')}" fill="rgba(88,166,255,.07)" stroke="none"/>`;
    s+=`<path d="${path(ch.bb_upper)}" fill="none" stroke="rgba(88,166,255,.45)" stroke-width="1" stroke-dasharray="3,3"/>`;
    s+=`<path d="${path(ch.bb_lower)}" fill="none" stroke="rgba(88,166,255,.45)" stroke-width="1" stroke-dasharray="3,3"/>`;
  }
  if(ch.ma60&&ch.ma60.length===n) s+=`<path d="${path(ch.ma60)}" fill="none" stroke="#a371f7" stroke-width="1.2" opacity=".85"/>`;
  if(ch.ma20&&ch.ma20.length===n) s+=`<path d="${path(ch.ma20)}" fill="none" stroke="var(--warn)" stroke-width="1.2" opacity=".9"/>`;
  if(ch.ma5&&ch.ma5.length===n) s+=`<path d="${path(ch.ma5)}" fill="none" stroke="#39c5cf" stroke-width="1.2" opacity=".9"/>`;
  const upTrend=c[n-1]>=c[0], col=upTrend?'var(--bull)':'var(--bear)';
  s+=`<path d="${path(c)}" fill="none" stroke="${col}" stroke-width="2"/></svg>`;
  return s;
}
// 走勢圖資料：--docs 版本抽到外部 JSON 非同步載入（省掉首屏 ~65% 的傳輸量）
function chartOf(st){ return (st&&st.chart) || (CHARTS&&CHARTS[st.id]) || null; }
async function ensureCharts(){
  if(CHARTS||!CHART_URL) return;
  try{ const r=await fetch(CHART_URL); CHARTS=await r.json(); }
  catch(e){ CHARTS={}; }
}
function chartBlock(st){
  const ch0=chartOf(st);
  if(!ch0) return `<div class="chart" style="color:var(--dim);font-size:12px;text-align:center;padding:30px">${CHART_URL&&!CHARTS?'走勢圖載入中…':'（無走勢圖資料）'}</div>`;
  const ch=ch0, ds=ch.dates||[];
  const span=ds.length?`${ds[0]} ~ ${ds[ds.length-1]}`:'';
  return `<div class="chart">${sparkSVG(ch)}
    <div class="lg">
      <span style="color:${ch.close[ch.close.length-1]>=ch.close[0]?'var(--bull)':'var(--bear)'}">收盤 <b>$${st.price}</b></span>
      <span style="color:#39c5cf">— 5MA</span>
      <span style="color:var(--warn)">— 20MA</span>
      <span style="color:#a371f7">— 60MA</span>
      <span style="color:var(--accent)">┄ 布林帶</span>
      <span style="margin-left:auto">${span}（近 ${ch.close.length} 日）</span></div></div>`;
}
// TradingAgents 風格技術分析師：跨類別判讀表
function techReportTable(a){
  const rep=a.tech_report;
  if(!rep||!rep.length) return '';
  const bc={bullish:'imp-bull',bearish:'imp-bear'};
  const bl={bullish:'偏多',bearish:'偏空',neutral:'中性'};
  const rows=rep.map(r=>`<tr>
    <td style="color:var(--dim);white-space:nowrap">${r.category}</td>
    <td style="white-space:nowrap"><b>${r.indicator}</b></td>
    <td>${r.reading}</td>
    <td style="text-align:right"><span class="ev-impact ${bc[r.bias]||'imp-neu'}">${bl[r.bias]||'中性'}</span></td></tr>`).join('');
  return `<div style="margin-top:14px">
    <div style="font-size:12px;color:var(--accent);margin-bottom:6px">🧠 技術分析師判讀（改寫自 TradingAgents · 跨趨勢/動能/擺盪/波動）</div>
    <table class="tr-table">${rows}</table></div>`;
}

function scoreClass(v){return v>10?'pos':(v<-10?'neg':'neu')}
function regimeBadge(r){const m={risk_on:'r-on',mild_risk_on:'r-on',neutral:'r-neu',mild_risk_off:'r-off',risk_off:'r-off'};
  return `<span class="regime ${m[r.regime]||'r-neu'}">${r.regime} · ${r.regime_score}</span>`}
function actionClass(a){return a==='買進'?'a-buy':(a==='分批布局'?'a-part':(a==='觀望'?'a-wait':'a-cut'))}

function renderHeader(){
  document.getElementById('h-regime').innerHTML=regimeBadge(DATA.regime);
  document.getElementById('h-sec').textContent=DATA.sectors.length;
  document.getElementById('h-cnt').textContent=DATA.stock_count;
  document.getElementById('n-date').textContent=DATA.date;
  document.getElementById('n-built').textContent=BUILT_AT;

  // 日期切換：DATES 為歷史頁清單（新→舊）
  const sel=document.getElementById('h-datesel');
  const opts=(DATES&&DATES.length?DATES:[DATA.date]);
  sel.innerHTML=opts.map(d=>`<option value="${d}" ${d===DATA.date?'selected':''}>${d}</option>`).join('');
  sel.onchange=()=>{ location.href=`./${sel.value}.html${location.hash}`; };

  // 全站個股搜尋（代號或名稱）
  const all=[];
  DATA.sectors.forEach((s,si)=>s.stocks.forEach((st,ki)=>all.push({si,ki,st})));
  document.getElementById('stk-list').innerHTML=
    all.map(x=>`<option value="${x.st.id} ${x.st.name}">${DATA.sectors[x.si].name}</option>`).join('');
  const box=document.getElementById('h-search');
  const jump=()=>{
    const q=box.value.trim().toLowerCase();
    if(!q) return;
    const hit=all.find(x=>`${x.st.id} ${x.st.name}`.toLowerCase()===q)
           || all.find(x=>x.st.id.includes(q)||x.st.name.toLowerCase().includes(q));
    if(hit){ select(hit.si,hit.ki); box.blur(); }
  };
  box.addEventListener('change',jump);
  box.addEventListener('keydown',e=>{ if(e.key==='Enter') jump(); });
}
function scoreBar(v){
  const w=Math.min(Math.abs(v),100);
  const col=v>10?'var(--bull)':(v<-10?'var(--bear)':'var(--warn)');
  return `<div class="scorebar"><i style="width:${w}%;background:${col}"></i></div>`;
}
function renderSectors(){
  const el=document.getElementById('col-sectors');
  el.innerHTML='<h4>族群排行</h4>'+DATA.sectors.map((s,i)=>
    `<div class="row ${i===curSec?'active':''}" tabindex="0" role="button"
          aria-pressed="${i===curSec}" onclick="selSec(${i})">
       <div class="rtop"><span>${s.name}</span><span class="sc ${scoreClass(s.sector_score)}">${s.sector_score}</span></div>
       ${scoreBar(s.sector_score)}</div>`).join('');
}
function sectorCard(sec){
  const sy=sec.synthesis;
  const acts=Object.entries(sy.actions).map(([k,v])=>`${k}×${v}`).join('、');
  return `<div class="seccard">
    <div class="st"><b>${sec.name} 族群綜述</b>
      <span class="vd ${scoreClass(sec.sector_score)}" style="background:var(--chip)">${sec.verdict} · ${sec.sector_score}</span></div>
    <div class="mini">首選：${sy.top_picks.join('、')}</div>
    <div class="mini">行動分布：${acts}</div>
    <div class="mini"><span class="bp">🐂 ${sy.bull.join(' ')}</span></div>
    <div class="mini"><span class="br">🐻 ${sy.bear.join(' ')}</span></div>
  </div>`;
}
const ACTIONS=['買進','分批布局','觀望','減碼/避開'];
function renderStocks(){
  const sec=DATA.sectors[curSec];
  const el=document.getElementById('col-stocks');
  const chips=['全部'].concat(ACTIONS).map(a=>{
    const key=a==='全部'?'':a;
    return `<span class="fchip ${filterAction===key?'on':''}" onclick="setFilter('${key}')">${a}</span>`;
  }).join('');
  const rows=sec.stocks.map((st,i)=>({st,i}))
    .filter(x=>!filterAction||x.st.decision.action===filterAction)
    .map(({st,i})=>
    `<div class="row ${i===curStk?'active':''}" tabindex="0" role="button"
          aria-pressed="${i===curStk}" onclick="selStk(${i})">
       <div class="rtop"><span>${st.name} <span style="color:var(--dim)">${st.id}</span></span>
       <span class="sc ${scoreClass(st.decision.composite)}">${st.decision.composite}</span></div>
       ${scoreBar(st.decision.composite)}</div>`).join('');
  el.innerHTML=`<h4>${sec.name}（${sec.stocks.length} 檔）</h4>`+sectorCard(sec)
    +`<div class="filters">${chips}</div>`
    +(rows||'<div class="empty">此族群沒有符合篩選的個股</div>');
}
function setFilter(a){ filterAction=a; renderStocks(); }
function analystCard(key,label,a){
  const v=a.verdict, vc=scoreClass(a.score);
  const w=(CURSTK.decision.weights_used||{})[key];
  const wtxt=key==='macro'
    ? '<span class="cov">不計入個股加權（只調部位上限）</span>'
    : (w?`<span class="cov">加權 ${Math.round(w*100)}%</span>`
        :'<span class="cov">缺資料 · 已排除於加權外</span>');
  const cov=a.coverage?`<span class="cov">${COV_LABEL[a.coverage]||a.coverage}</span>`:'';
  const sig=Object.entries(a.signals).map(([k,val])=>
    `<div class="c"><div class="k">${LABELS[k]||k}</div><div class="v">${fmtVal(k,val)}</div></div>`).join('');
  const chart=key==='technical'?`<div id="chart-slot">${chartBlock(CURSTK)}</div>`:'';
  const tr=key==='technical'?techReportTable(a):'';
  return `<div class="acard ${key==='technical'?'active':''}" id="ac-${key}">
    <div class="ah"><b>${label}</b>${cov}${wtxt}
      <span class="v ${vc}" style="background:var(--chip)">${v} · ${a.score}</span></div>
    ${chart}<div class="kv">${sig}</div>${tr}</div>`;
}
function renderDetail(){
  const sec=DATA.sectors[curSec];
  if(!sec||!sec.stocks.length){
    document.getElementById('detail').innerHTML=
      '<div class="empty">本日無分析資料（daily_reports 可能尚未產出，或掃描結果為空）</div>';
    return;
  }
  const st=sec.stocks[curStk], d=st.decision, A=st.analysts;
  CURSTK=st;
  const labels={technical:'📈 技術面',fundamental:'💰 基本面',macro:'🌐 新聞總經',sentiment:'🔥 情緒籌碼'};
  const tabs=Object.keys(labels).map((k,i)=>
    `<div class="tab ${i===0?'active':''}" onclick="selTab('${k}',this)">${labels[k]}</div>`).join('');
  const cards=Object.keys(labels).map(k=>analystCard(k,labels[k],A[k])).join('');
  const bull=st.debate.bull.map(x=>`<li>${x}</li>`).join('');
  const bear=st.debate.bear.map(x=>`<li>${x}</li>`).join('');
  const flags=d.flags.map(f=>`<span class="flag">${f}</span>`).join('');
  // 個股新聞
  const newsHtml=(st.news&&st.news.length)?st.news.map(n=>{
    const rc=n.recency?`<span class="rec ${n.recency==='今日'?'today':''}">${n.recency}</span>`:'';
    const t=n.link?`<a href="${n.link}" target="_blank" rel="noopener">${n.title}</a>`:n.title;
    return `<div class="news-item">${t}<div class="news-meta">${rc}<span>${n.source}</span><span>${n.datetime||''}</span></div></div>`;
  }).join(''):'<div style="color:var(--dim);font-size:13px">無近期新聞</div>';
  document.getElementById('detail').innerHTML=`
    <div class="dhead"><span class="nm">${st.name}</span><span class="id">${st.id}</span>
      <a class="stocklink" href="../?stock=${st.id}" target="_blank" rel="noopener">完整個股分析 ↗</a>
      <span class="action ${actionClass(d.action)}">${d.action}</span>
      <span class="pr">$${st.price}</span></div>
    <div class="sumtxt">${st.summary_text}</div>
    <div class="sec-h">🎯 最終決策（交易員 → 風控 → 投組經理）</div>
    <div class="dec">
      <div style="font-size:13px;color:var(--dim)">綜合評分 <b style="color:var(--txt)">${d.composite}</b>｜信心度 <b style="color:var(--txt)">${d.confidence}%</b>
        <span style="margin-left:8px">＝ ${weightsText(d)}</span></div>
      <div class="rg">
        <div class="rc"><div class="k">建議行動</div><div class="v ${actionClass(d.action).replace('a-','').replace('buy','pos').replace('part','')}">${d.action}</div></div>
        <div class="rc"><div class="k">部位上限(regime)</div><div class="v">${d.exposure_pct}%</div></div>
        <div class="rc"><div class="k">進場區</div><div class="v">${d.entry_zone?d.entry_zone.join('–'):'-'}</div></div>
        <div class="rc"><div class="k">停損</div><div class="v neg">${d.stop_loss??'-'}</div></div>
        <div class="rc"><div class="k">短期目標</div><div class="v pos">${d.target_short??'-'}</div></div>
        <div class="rc"><div class="k">ATR14</div><div class="v">${d.atr14??'-'}</div></div>
      </div>
      <div style="margin-top:12px">風控旗標：${flags}</div>
    </div>
    ${renderEvents()}
    <div class="sec-h">🧑‍💼 分析師團隊</div>
    <div class="glossary">
      <b>指標說明</b>：20日報酬=近20交易日漲跌幅｜RSI(14)=相對強弱，&gt;70過熱、&lt;30超賣｜
      回測夏普值=每單位風險的報酬，越高越穩｜月營收年增=最新月營收 YoY（FinMind）｜
      毛利率季變動=最新季較上季增減，單位為百分點｜
      大盤情緒分數=國際指標+外電綜合(-100~+100)｜買賣超單位為「張」<br>
      <b>綜合評分</b>只用「有資料」的面向加權（缺料面向排除後重新歸一化，不當 0 分計），
      大盤總經不進個股加權，只調整部位上限與行動門檻。</div>
    <div class="tabs">${tabs}</div>${cards}
    <div class="sec-h">⚔️ 多空研究員辯論</div>
    <div class="debate">
      <div class="side bull"><h3>🐂 多方</h3><ul>${bull}</ul></div>
      <div class="side bear"><h3>🐻 空方</h3><ul>${bear}</ul></div></div>
    <div class="sec-h">📰 個股近期新聞</div>
    <div class="news-list">${newsHtml}</div>`;
  // 走勢圖若尚未載入，載完後只換掉圖表區塊（避免整頁重繪）
  ensureCharts().then(()=>{
    if(CURSTK!==st) return;
    const slot=document.getElementById('chart-slot');
    if(slot) slot.innerHTML=chartBlock(st);
  });
}
function weightsText(d){
  const w=d.weights_used||{};
  const nm={technical:'技術',fundamental:'基本',sentiment:'籌碼'};
  const parts=Object.keys(nm).filter(k=>w[k]).map(k=>`${nm[k]} ${Math.round(w[k]*100)}%`);
  return parts.length?parts.join(' + '):'無可用面向';
}
function renderEvents(){
  const head='<div class="sec-h">🌐 今日外電／總經事件（影響全市場）</div>';
  if(!DATA.events||!DATA.events.length)
    return head+'<div style="color:var(--dim);font-size:13px;padding:2px 0 6px">今日無近 48 小時重大外電（即時搜尋未抓到符合時效的事件）</div>';
  const items=DATA.events.map(e=>{
    const ic={bullish:'imp-bull',bearish:'imp-bear'}[e.impact]||'imp-neu';
    const lbl={bullish:'利多',bearish:'利空'}[e.impact]||'中性';
    const h=e.link?`<a href="${e.link}" target="_blank" rel="noopener">${e.headline}</a>`:e.headline;
    const rc=e.recency?`<span class="rec ${e.recency==='今日'?'today':''}">${e.recency}</span>`:'';
    return `<div class="ev-item">${h}
      <div class="news-meta">${rc}<span class="ev-impact ${ic}">${lbl}·強度${e.severity}</span>
      <span>${e.category}</span><span>${e.rationale}</span><span>${e.source||''}</span>${e.date?`<span>${e.date}</span>`:''}</div></div>`;
  }).join('');
  return head+`<div class="ev-list">${items}</div>`;
}
function selTab(k,el){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.acard').forEach(c=>c.classList.remove('active'));
  el.classList.add('active');document.getElementById('ac-'+k).classList.add('active')}
function selSec(i){select(i,0)}
function selStk(i){select(curSec,i)}

// ── 選取狀態 ⇄ 網址 hash（可分享、可重整、可上一頁）────────────────
function select(si,ki,skipHash){
  const sec=DATA.sectors[si]; if(!sec) return;
  curSec=si; curStk=Math.max(0,Math.min(ki,sec.stocks.length-1));
  renderSectors();renderStocks();renderDetail();
  if(!skipHash){
    const h=`#${encodeURIComponent(sec.name)}/${sec.stocks[curStk]?sec.stocks[curStk].id:''}`;
    if(location.hash!==h) history.replaceState(null,'',h);
  }
  document.querySelector('.detail').scrollTop=0;
}
function applyHash(){
  const raw=decodeURIComponent(location.hash.replace(/^#/,''));
  if(!raw) return false;
  const [secName,stkId]=raw.split('/');
  const si=DATA.sectors.findIndex(s=>s.name===secName);
  if(si<0) return false;
  const ki=Math.max(0,DATA.sectors[si].stocks.findIndex(s=>String(s.id)===stkId));
  select(si,ki,true);
  return true;
}
window.addEventListener('hashchange',applyHash);

// ── 鍵盤操作：↑↓ 換個股、←→ 換族群（輸入框中不攔截）──────────────
document.addEventListener('keydown',e=>{
  if(e.target.tagName==='INPUT'||e.target.tagName==='SELECT') return;
  const sec=DATA.sectors[curSec]; if(!sec) return;
  const move={ArrowDown:()=>select(curSec,curStk+1),
              ArrowUp:()=>select(curSec,curStk-1),
              ArrowRight:()=>select(Math.min(curSec+1,DATA.sectors.length-1),0),
              ArrowLeft:()=>select(Math.max(curSec-1,0),0)}[e.key];
  if(move){ e.preventDefault(); move(); }
  if(e.key==='/'){ e.preventDefault(); document.getElementById('h-search').focus(); }
});
document.addEventListener('keydown',e=>{
  if(e.key==='Enter'&&e.target.classList&&e.target.classList.contains('row')) e.target.click();
});

renderHeader();
if(!DATA.sectors.length){ renderDetail(); }
else if(!applyHash()){ select(0,0); }
</script></body></html>
"""


def _round_floats(obj, nd: int = 2):
    """遞迴把浮點數收斂到小數 nd 位。

    走勢圖與指標都是雙精度尾數（如 123.45000000000002），對顯示毫無意義，
    卻佔了 JSON 相當比例的體積。
    """
    if isinstance(obj, float):
        return round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round_floats(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v, nd) for v in obj]
    return obj


def _split_charts(data: dict) -> dict[str, dict]:
    """把每檔的 chart 從主資料抽出（就地移除），回傳 {股票代號: chart}。"""
    charts: dict[str, dict] = {}
    for sec in data.get("sectors", []):
        for st in sec.get("stocks", []):
            ch = st.pop("chart", None)
            if ch:
                charts[str(st["id"])] = ch
    return charts


def _prune(keep: int = _KEEP_DAYS) -> None:
    """只保留最近 keep 天的歷史頁與圖表，避免 repo 每年長 70MB+。"""
    pages = sorted(DOCS_DIR.glob("2*.html"), reverse=True)
    for p in pages[keep:]:
        p.unlink(missing_ok=True)
        (DOCS_DIR / "charts" / f"{p.stem}.json").unlink(missing_ok=True)


def _dates_index(latest: str) -> list[str]:
    """docs/agents/ 已存在的分析日期清單（新→舊），供頁面日期下拉使用。"""
    dates = {p.stem for p in DOCS_DIR.glob("2*.html")}
    dates.add(latest)
    return sorted(dates, reverse=True)


def _render(data: dict, chart_url: str | None, dates: list[str],
            back_link: bool) -> str:
    embedded = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = (_HTML
            .replace("__DATE__", data["date"])
            .replace("__CHART_URL__", json.dumps(chart_url))
            .replace("__DATES__", json.dumps(dates, ensure_ascii=False))
            .replace("__BUILT_AT__",
                     _dt.datetime.now().strftime("%Y-%m-%d %H:%M"))
            .replace("__DATA__", embedded))
    if back_link:
        html = html.replace(
            '<div class="title"><span class="dot"></span>台股族群分析台</div>',
            '<div class="title"><span class="dot"></span>台股族群分析台'
            '<a href="../" style="font-size:12px;color:var(--accent);text-decoration:none;'
            'margin-left:10px">← 返回主站</a></div>')
    return html


def build(date: str | None, to_docs: bool = False) -> Path:
    if date:
        src = OUT_DIR / f"analysis_{date}.json"
    else:
        cands = sorted(OUT_DIR.glob("analysis_*.json"))
        if not cands:
            raise FileNotFoundError("找不到 analysis_*.json，請先跑 pipeline.py")
        src = cands[-1]

    data = _round_floats(json.loads(src.read_text(encoding="utf-8")))
    d = data["date"]

    if not to_docs:
        # preview：單檔自包含（file:// 直接開，fetch 會被 CORS 擋，故圖表內嵌）
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        out = PREVIEW_DIR / f"族群分析台_{d}.html"
        out.write_text(_render(data, None, [d], back_link=False), encoding="utf-8")
        return out

    # 靜態網址版：每天一份永久網址 docs/agents/<date>.html，index.html 指向最新
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    charts = _split_charts(data)
    (DOCS_DIR / "charts").mkdir(exist_ok=True)
    (DOCS_DIR / "charts" / f"{d}.json").write_text(
        json.dumps(charts, ensure_ascii=False), encoding="utf-8")

    (DOCS_DIR / f"{d}.html").touch()   # 先建檔，讓 _prune/_dates_index 看得到今天
    _prune()
    dates = _dates_index(d)
    html = _render(data, f"./charts/{d}.json", dates, back_link=True)
    (DOCS_DIR / f"{d}.html").write_text(html, encoding="utf-8")
    (DOCS_DIR / "index.html").write_text(html, encoding="utf-8")
    (DOCS_DIR / "dates.json").write_text(
        json.dumps(dates, ensure_ascii=False), encoding="utf-8")
    return DOCS_DIR / "index.html"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定 analysis 日期 (YYYYMMDD)")
    ap.add_argument("--docs", action="store_true", help="輸出到 docs/agents/index.html（靜態網址）")
    args = ap.parse_args()
    out = build(args.date, to_docs=args.docs)
    print(f"→ 已產出介面：{out}")


if __name__ == "__main__":
    main()
