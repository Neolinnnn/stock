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
let curSec=0, curStk=0;

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
function renderStocks(){
  const sec=DATA.sectors[curSec];
  const el=document.getElementById('col-stocks');
  el.innerHTML=`<h4>${sec.name}（${sec.stocks.length} 檔）</h4>`+sec.stocks.map((st,i)=>
    `<div class="row ${i===curStk?'active':''}" onclick="selStk(${i})">
       <span>${st.name} <span style="color:var(--dim)">${st.id}</span></span>
       <span class="sc ${scoreClass(st.decision.composite)}">${st.decision.composite}</span></div>`).join('');
}
function analystCard(key,label,a){
  const v=a.verdict, vc=scoreClass(a.score);
  const sig=Object.entries(a.signals).map(([k,val])=>
    `<div class="c"><div class="k">${k}</div><div class="v">${val}</div></div>`).join('');
  return `<div class="acard ${key==='technical'?'active':''}" id="ac-${key}">
    <div class="ah"><b>${label}</b><span class="v ${vc}" style="background:var(--chip)">${v} · ${a.score}</span></div>
    <div class="kv">${sig}</div></div>`;
}
function renderDetail(){
  const st=DATA.sectors[curSec].stocks[curStk], d=st.decision, A=st.analysts;
  const labels={technical:'📈 技術面',fundamental:'💰 基本面',macro:'🌐 新聞總經',sentiment:'🔥 情緒籌碼'};
  const tabs=Object.keys(labels).map((k,i)=>
    `<div class="tab ${i===0?'active':''}" onclick="selTab('${k}',this)">${labels[k]}</div>`).join('');
  const cards=Object.keys(labels).map(k=>analystCard(k,labels[k],A[k])).join('');
  const bull=st.debate.bull.map(x=>`<li>${x}</li>`).join('');
  const bear=st.debate.bear.map(x=>`<li>${x}</li>`).join('');
  const flags=d.flags.map(f=>`<span class="flag">${f}</span>`).join('');
  document.getElementById('detail').innerHTML=`
    <div class="dhead"><span class="nm">${st.name}</span><span class="id">${st.id}</span>
      <span class="action ${actionClass(d.action)}">${d.action}</span>
      <span class="pr">$${st.price}</span></div>
    <div class="sumtxt">${st.summary_text}</div>
    <div class="sec-h">① 分析師團隊</div>
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
    </div>`;
}
function selTab(k,el){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  document.querySelectorAll('.acard').forEach(c=>c.classList.remove('active'));
  el.classList.add('active');document.getElementById('ac-'+k).classList.add('active')}
function selSec(i){curSec=i;curStk=0;renderSectors();renderStocks();renderDetail()}
function selStk(i){curStk=i;renderStocks();renderDetail()}
renderHeader();renderSectors();renderStocks();renderDetail();
</script></body></html>
"""


def build(date: str | None) -> Path:
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

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    out = PREVIEW_DIR / f"族群分析台_{data['date']}.html"
    out.write_text(html, encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="指定 analysis 日期 (YYYYMMDD)")
    args = ap.parse_args()
    out = build(args.date)
    print(f"→ 已產出介面：{out}")


if __name__ == "__main__":
    main()
