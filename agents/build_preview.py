"""
把 pipeline 產出的 analysis_<date>.json 渲染成自包含、資料驅動的 HTML 介面。

資料直接內嵌進 HTML（file:// 可直接開，免後端、免 CORS）。
介面：左欄族群排行 → 個股清單；右欄個股詳情（4 分析師 / 多空辯論 / 決策）。

用法：
    python agents/build_preview.py                 # 取最新 analysis_*.json
    python agents/build_preview.py --date 20260605
輸出：preview/族群分析台_<date>.html（不接入 docs/，不影響靜態網址）
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "agents" / "output"
PREVIEW_DIR = ROOT / "preview"
DOCS_DIR = ROOT / "docs" / "agents"

_HTML = r"""<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>台股族群分析台 __DATE__</title>
<style>
:root{--bg:#0d1117;--panel:#161b22;--panel2:#1c2333;--border:#2d3748;--txt:#e6edf3;
--dim:#8b949e;--accent:#58a6ff;--bull:#3fb950;--bear:#f85149;--warn:#d29922;--chip:#21262d}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--txt);font-family:"Microsoft JhengHei","PingFang TC","Noto Sans TC",sans-serif;line-height:1.6}
header{background:linear-gradient(135deg,#161b22,#1c2333);border-bottom:1px solid var(--border);padding:16px 28px;position:sticky;top:0;z-index:10;display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.title{font-size:20px;font-weight:700;display:flex;align-items:center;gap:9px}
.title .dot{width:9px;height:9px;border-radius:50%;background:var(--bull);box-shadow:0 0 8px var(--bull)}
.hmeta{font-size:13px;color:var(--dim);margin-left:auto;display:flex;gap:18px;flex-wrap:wrap}
.hmeta b{color:var(--txt)}
.regime{padding:2px 10px;border-radius:20px;font-size:12px;font-weight:700}
.r-on{background:rgba(63,185,80,.15);color:var(--bull);border:1px solid var(--bull)}
.r-neu{background:rgba(210,153,34,.15);color:var(--warn);border:1px solid var(--warn)}
.r-off{background:rgba(248,81,73,.15);color:var(--bear);border:1px solid var(--bear)}
.layout{display:grid;grid-template-columns:230px 250px 1fr;gap:0;height:calc(100vh - 60px)}
.col{overflow-y:auto;border-right:1px solid var(--border);padding:12px}
.col h4{font-size:11px;color:var(--dim);letter-spacing:1px;margin:6px 8px 10px}
.row{padding:9px 11px;border-radius:8px;cursor:pointer;display:flex;justify-content:space-between;align-items:center;font-size:13px;margin-bottom:3px;border:1px solid transparent}
.row:hover{background:var(--chip)}
.row.active{background:var(--panel2);border-color:var(--accent)}
.row .sc{font-weight:700;font-size:12px;padding:1px 7px;border-radius:5px}
.pos{color:var(--bull)}.neg{color:var(--bear)}.neu{color:var(--warn)}
.detail{overflow-y:auto;padding:22px 26px}
.dhead{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:6px}
.dhead .nm{font-size:22px;font-weight:800}.dhead .id{color:var(--dim)}
.dhead .pr{font-size:18px;font-weight:700;margin-left:auto}
.action{font-size:14px;font-weight:800;padding:4px 14px;border-radius:8px}
.a-buy{background:rgba(63,185,80,.18);color:var(--bull)}
.a-part{background:rgba(88,166,255,.18);color:var(--accent)}
.a-wait{background:rgba(210,153,34,.18);color:var(--warn)}
.a-cut{background:rgba(248,81,73,.18);color:var(--bear)}
.sumtxt{background:var(--panel);border:1px solid var(--border);border-radius:10px;padding:14px 16px;font-size:14px;color:#c9d1d9;margin:14px 0 20px}
.sec-h{font-size:13px;color:var(--accent);margin:22px 0 12px;border-left:3px solid var(--accent);padding-left:9px;letter-spacing:1px}
.tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}
.tab{background:var(--chip);border:1px solid var(--border);color:var(--dim);padding:7px 14px;border-radius:8px;cursor:pointer;font-size:13px;font-weight:600}
.tab.active{background:var(--accent);color:#0d1117;border-color:var(--accent)}
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
.dec{background:linear-gradient(135deg,var(--panel2),var(--panel));border:1px solid var(--accent);border-radius:12px;padding:18px;margin-top:8px}
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
</style></head><body>
<header>
  <div class="title"><span class="dot"></span>台股族群分析台</div>
  <div class="hmeta">
    <span>日期 <b id="h-date"></b></span>
    <span>大盤 <b id="h-regime"></b></span>
    <span>族群 <b id="h-sec"></b>｜個股 <b id="h-cnt"></b></span>
    <span style="color:var(--dim)">改寫自 TradingAgents</span>
  </div>
</header>
<div class="layout">
  <div class="col" id="col-sectors"><h4>族群排行</h4></div>
  <div class="col" id="col-stocks"><h4>個股（族群內排行）</h4></div>
  <div class="detail" id="detail"></div>
</div>
<div class="note">※ 評分／決策由 Claude 程式邏輯（agents/analysts.py）產生；文字敘述可選 Gemini。資料源：daily_reports + macro 模組。本檔在 preview/，不影響靜態網址。</div>
<script>
const DATA = __DATA__;
let curSec=0, curStk=0, CURSTK=null;

// 指標簡寫 → 看得懂的中文名（解決「max_yoy_pct 是什麼」）
const LABELS={
  ret_20d:'20日報酬', rsi:'RSI(14)', cv_sharpe:'回測夏普值', signal:'技術訊號',
  max_yoy_pct:'營收年增率(最高)', news_count:'新聞則數',
  regime_score:'大盤情緒分數', regime:'大盤狀態', stock_news:'個股新聞數',
  '外資':'外資買賣超', '投信':'投信買賣超', '合計':'三大法人合計'};
const UNITS={ret_20d:'%', max_yoy_pct:'%', '外資':' 張', '投信':' 張', '合計':' 張'};
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
function chartBlock(st){
  if(!st||!st.chart) return '<div class="chart" style="color:var(--dim);font-size:12px;text-align:center;padding:30px">（無走勢圖資料）</div>';
  const ch=st.chart, ds=ch.dates||[];
  const span=ds.length?`${ds[0]} ~ ${ds[ds.length-1]}`:'';
  return `<div class="chart">${sparkSVG(ch)}
    <div class="lg">
      <span style="color:${st.chart.close[st.chart.close.length-1]>=st.chart.close[0]?'var(--bull)':'var(--bear)'}">收盤 <b>$${st.price}</b></span>
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
  document.getElementById('h-date').textContent=DATA.date;
  document.getElementById('h-regime').innerHTML=regimeBadge(DATA.regime);
  document.getElementById('h-sec').textContent=DATA.sectors.length;
  document.getElementById('h-cnt').textContent=DATA.stock_count;
}
function renderSectors(){
  const el=document.getElementById('col-sectors');
  el.innerHTML='<h4>族群排行</h4>'+DATA.sectors.map((s,i)=>
    `<div class="row ${i===curSec?'active':''}" onclick="selSec(${i})">
       <span>${s.name}</span><span class="sc ${scoreClass(s.sector_score)}">${s.sector_score}</span></div>`).join('');
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
function renderStocks(){
  const sec=DATA.sectors[curSec];
  const el=document.getElementById('col-stocks');
  el.innerHTML=`<h4>${sec.name}（${sec.stocks.length} 檔）</h4>`+sectorCard(sec)+sec.stocks.map((st,i)=>
    `<div class="row ${i===curStk?'active':''}" onclick="selStk(${i})">
       <span>${st.name} <span style="color:var(--dim)">${st.id}</span></span>
       <span class="sc ${scoreClass(st.decision.composite)}">${st.decision.composite}</span></div>`).join('');
}
function analystCard(key,label,a){
  const v=a.verdict, vc=scoreClass(a.score);
  const sig=Object.entries(a.signals).map(([k,val])=>
    `<div class="c"><div class="k">${LABELS[k]||k}</div><div class="v">${fmtVal(k,val)}</div></div>`).join('');
  const chart=key==='technical'?chartBlock(CURSTK):'';
  const tr=key==='technical'?techReportTable(a):'';
  return `<div class="acard ${key==='technical'?'active':''}" id="ac-${key}">
    <div class="ah"><b>${label}</b><span class="v ${vc}" style="background:var(--chip)">${v} · ${a.score}</span></div>
    ${chart}<div class="kv">${sig}</div>${tr}</div>`;
}
function renderDetail(){
  const st=DATA.sectors[curSec].stocks[curStk], d=st.decision, A=st.analysts;
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
    ${renderEvents()}
    <div class="sec-h">① 分析師團隊</div>
    <div class="glossary">
      <b>指標說明</b>：20日報酬=近20交易日漲跌幅｜RSI(14)=相對強弱，&gt;70過熱、&lt;30超賣｜
      回測夏普值=每單位風險的報酬，越高越穩｜營收年增率=最新月營收 YoY｜
      大盤情緒分數=國際指標+外電綜合(-100~+100)｜買賣超單位為「張」</div>
    <div class="tabs">${tabs}</div>${cards}
    <div class="sec-h">② 多空研究員辯論</div>
    <div class="debate">
      <div class="side bull"><h3>🐂 多方</h3><ul>${bull}</ul></div>
      <div class="side bear"><h3>🐻 空方</h3><ul>${bear}</ul></div></div>
    <div class="sec-h">③④⑤ 交易員 → 風控 → 投組經理</div>
    <div class="dec">
      <div style="font-size:13px;color:var(--dim)">綜合評分 ${d.composite}｜信心度 ${d.confidence}%</div>
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
    <div class="sec-h">⑥ 個股近期新聞</div>
    <div class="news-list">${newsHtml}</div>`;
}
function renderEvents(){
  if(!DATA.events||!DATA.events.length) return '';
  const items=DATA.events.map(e=>{
    const ic={bullish:'imp-bull',bearish:'imp-bear'}[e.impact]||'imp-neu';
    const lbl={bullish:'利多',bearish:'利空'}[e.impact]||'中性';
    const h=e.link?`<a href="${e.link}" target="_blank" rel="noopener">${e.headline}</a>`:e.headline;
    return `<div class="ev-item">${h}
      <div class="news-meta"><span class="ev-impact ${ic}">${lbl}·強度${e.severity}</span>
      <span>${e.category}</span><span>${e.rationale}</span><span>${e.source||''}</span></div></div>`;
  }).join('');
  return `<div class="sec-h">🌐 今日外電／總經事件（影響全市場）</div><div class="ev-list">${items}</div>`;
}
function selTab(k,el){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.acard').forEach(c=>c.classList.remove('active'));
  el.classList.add('active');document.getElementById('ac-'+k).classList.add('active')}
function selSec(i){curSec=i;curStk=0;renderSectors();renderStocks();renderDetail()}
function selStk(i){curStk=i;renderStocks();renderDetail()}
renderHeader();renderSectors();renderStocks();renderDetail();
</script></body></html>
"""


def build(date: str | None, to_docs: bool = False) -> Path:
    if date:
        src = OUT_DIR / f"analysis_{date}.json"
    else:
        cands = sorted(OUT_DIR.glob("analysis_*.json"))
        if not cands:
            raise FileNotFoundError("找不到 analysis_*.json，請先跑 pipeline.py")
        src = cands[-1]

    data = json.loads(src.read_text(encoding="utf-8"))
    embedded = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    html = _HTML.replace("__DATE__", data["date"]).replace("__DATA__", embedded)

    if to_docs:
        # 靜態網址版：固定檔名 docs/agents/index.html（每日覆寫為最新），加返回連結
        html = html.replace(
            '<div class="title"><span class="dot"></span>台股族群分析台</div>',
            '<div class="title"><span class="dot"></span>台股族群分析台'
            '<a href="../" style="font-size:12px;color:var(--accent);text-decoration:none;'
            'margin-left:10px">← 返回主站</a></div>')
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        out = DOCS_DIR / "index.html"
    else:
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        out = PREVIEW_DIR / f"族群分析台_{data['date']}.html"
    out.write_text(html, encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定 analysis 日期 (YYYYMMDD)")
    ap.add_argument("--docs", action="store_true", help="輸出到 docs/agents/index.html（靜態網址）")
    args = ap.parse_args()
    out = build(args.date, to_docs=args.docs)
    print(f"→ 已產出介面：{out}")


if __name__ == "__main__":
    main()
