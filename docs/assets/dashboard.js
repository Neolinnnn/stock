/* AI 個股戰情儀表板：指標計算、繪圖與卡片渲染。
   由 docs/stock_dashboard.html（全螢幕版）與個股分析分頁共用。
   對外只暴露 window.StockDashboard，資料載入由呼叫端負責。 */
(function () {
'use strict';

  // ════════════════════════════════════════════════════════════════
  //  工具函式
  // ════════════════════════════════════════════════════════════════
  // 查找範圍限縮在目前掛載的容器內，讓同一套程式碼可嵌進其他頁面。
  let _root = document;
  const $ = id => _root.querySelector('#' + id);
  const last = a => (a && a.length) ? a[a.length - 1] : null;
  const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
  const nz = v => (v === null || v === undefined || Number.isNaN(v)) ? 0 : v;
  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v))
    ? '—' : Number(v).toLocaleString('zh-TW', { minimumFractionDigits: d, maximumFractionDigits: d });
  const fmtInt = v => (v === null || v === undefined || Number.isNaN(v))
    ? '—' : Math.round(v).toLocaleString('zh-TW');

  /** 取陣列尾端 n 筆，濾掉 null。 */
  const tail = (arr, n) => (arr || []).slice(-n).filter(v => v !== null && v !== undefined);
  const sum = arr => arr.reduce((a, b) => a + b, 0);
  const mean = arr => arr.length ? sum(arr) / arr.length : 0;

  /** 簡單移動平均；資料不足的位置回傳 null，與後端 indicators 對齊。 */
  function sma(values, period) {
    const out = [];
    for (let i = 0; i < values.length; i++) {
      if (i < period - 1) { out.push(null); continue; }
      out.push(mean(values.slice(i - period + 1, i + 1)));
    }
    return out;
  }

  /** 母體標準差。 */
  function stdev(arr) {
    if (arr.length < 2) return 0;
    const m = mean(arr);
    return Math.sqrt(mean(arr.map(v => (v - m) ** 2)));
  }

    // 各 canvas 圖表的繪製高度（px）。主K線另由 .chartbox 的 CSS 高度決定。
  const CANVAS_H = 220;

  // 集保大戶級距，分層互斥，與 build_docs._HOLDER_LEVELS 對應
  const HOLDER_LEVELS = [
    { key: 'lv1000_up',  label: '1000 張以上', color: '#e74c3c' },
    { key: 'lv800_1000', label: '800~1000 張', color: '#f1b143' },
    { key: 'lv600_800',  label: '600~800 張',  color: '#4ea1f3' },
  ];

  // ════════════════════════════════════════════════════════════════
  //  衍生指標計算
  //  說明：以下指標後端 JSON 未提供，於前端由 ohlcv / chip 推導。
  //  凡屬「估算」而非實際揭露值者，UI 上標註 (估) 供使用者辨識。
  // ════════════════════════════════════════════════════════════════

  /**
   * 成交量加權平均價（VWAP）。
   * 以典型價 (H+L+C)/3 對成交量加權，作為主力平均持有成本的代理指標。
   */
  function vwap(o, period) {
    const n = o.close.length;
    const from = Math.max(0, n - period);
    let pv = 0, vv = 0;
    for (let i = from; i < n; i++) {
      const tp = (o.high[i] + o.low[i] + o.close[i]) / 3;
      pv += tp * o.volume[i];
      vv += o.volume[i];
    }
    return vv ? pv / vv : null;
  }

  /**
   * 價量分佈（Volume Profile）：把近 period 日的成交量依收盤價分配到 bins 個價格桶。
   * 用於「籌碼熱區圖」與「主力成本結構分布圖」。
   */
  function volumeProfile(o, period, bins) {
    const n = o.close.length;
    const from = Math.max(0, n - period);
    const px = o.close.slice(from), vol = o.volume.slice(from);
    const lo = Math.min(...px), hi = Math.max(...px);
    const span = (hi - lo) || 1;
    const buckets = Array.from({ length: bins }, (_, i) => ({
      lo: lo + span * i / bins,
      hi: lo + span * (i + 1) / bins,
      vol: 0
    }));
    px.forEach((p, i) => {
      const k = clamp(Math.floor((p - lo) / span * bins), 0, bins - 1);
      buckets[k].vol += vol[i];
    });
    const maxVol = Math.max(...buckets.map(b => b.vol)) || 1;
    buckets.forEach(b => { b.ratio = b.vol / maxVol; });
    return { buckets, lo, hi, total: sum(vol) };
  }

  /** 年化歷史波動率（%），以日報酬標準差 × √252。 */
  function annualVol(closes, period) {
    const px = tail(closes, period + 1);
    if (px.length < 3) return 0;
    const rets = [];
    for (let i = 1; i < px.length; i++) rets.push(Math.log(px[i] / px[i - 1]));
    return stdev(rets) * Math.sqrt(252) * 100;
  }

  /** RSI(14)，用於動能評分。 */
  function rsi(closes, period = 14) {
    const px = tail(closes, period + 1);
    if (px.length < period + 1) return 50;
    let up = 0, dn = 0;
    for (let i = 1; i < px.length; i++) {
      const d = px[i] - px[i - 1];
      if (d >= 0) up += d; else dn -= d;
    }
    if (dn === 0) return 100;
    const rs = (up / period) / (dn / period);
    return 100 - 100 / (1 + rs);
  }

  /**
   * 多維度評分：趨勢 / 動能 / 籌碼 / 量能 / 波動性，各 0~100。
   * 權重為經驗值，非回測最佳化結果。
   */
  function scoreDimensions(d) {
    const o = d.ohlcv, ind = d.indicators || {}, chip = d.chip || {};
    const c = last(o.close);
    const ma5 = last(ind.ma5), ma20 = last(ind.ma20), ma60 = last(ind.ma60);

    // 趨勢：收盤價相對三條均線的位置 + 均線多頭排列
    let trend = 0;
    [ma5, ma20, ma60].forEach(m => { if (m && c > m) trend += 20; });
    if (ma5 && ma20 && ma5 > ma20) trend += 20;
    if (ma20 && ma60 && ma20 > ma60) trend += 20;

    // 動能：RSI 直接映射 + MACD 柱狀體方向
    const r = rsi(o.close);
    const hist = last(ind.macd_hist);
    let momentum = clamp(r, 0, 100);
    if (hist !== null && hist !== undefined) momentum += hist > 0 ? 8 : -8;
    momentum = clamp(momentum, 0, 100);

    // 籌碼：近 20 日法人累計買賣超，換算成「相當於幾日均量」後評分。
    // 以 tanh 平滑壓縮而非線性截斷 —— 線性映射會讓半數個股頂到 0 或 100，
    // 使賣超 -1,000 張與 -9,000 張同為 0 分而失去鑑別度。
    const net20 = sum(tail(chip.total, 20));
    const avgLots = mean(tail(o.volume, 20)) / 1000;   // 股 → 張
    const chipRatio = avgLots ? net20 / avgLots : 0;   // 淨額相當於幾日均量
    const chipScore = 50 + 50 * Math.tanh(chipRatio / 2);

    // 量能：近 5 日均量 / 近 20 日均量
    const v5 = mean(tail(o.volume, 5)), v20 = mean(tail(o.volume, 20));
    const volScore = clamp(v20 ? (v5 / v20) * 50 : 50, 0, 100);

    // 波動性：年化波動率越低分數越高（此處高分代表「穩定」）
    const av = annualVol(o.close, 20);
    const volatility = clamp(100 - av, 0, 100);

    const total = Math.round(trend * 0.3 + momentum * 0.25 + chipScore * 0.2
      + volScore * 0.15 + volatility * 0.1);
    const grade = total >= 80 ? 'A' : total >= 65 ? 'B' : total >= 50 ? 'C' : 'D';

    return {
      trend: Math.round(trend), momentum: Math.round(momentum),
      chip: Math.round(chipScore), volume: Math.round(volScore),
      volatility: Math.round(volatility), total, grade,
      rsi: r, annualVol: av, net20, v5, v20
    };
  }

  /**
   * 風險指標。隔日沖比率無公開免費資料源，此處以「當日振幅 × 量能放大倍數」
   * 作為短線投機熱度的代理值，僅供相對比較。
   */
  function riskMetrics(d, sc) {
    const o = d.ohlcv;
    const n = o.close.length - 1;
    const amp = o.close[n] ? (o.high[n] - o.low[n]) / o.close[n] * 100 : 0;
    const volRatio = sc.v20 ? sc.v5 / sc.v20 : 1;

    const daytrade = clamp(amp * 8 + (volRatio - 1) * 40, 0, 100);   // 隔日沖熱度(估)
    const turnover = clamp(volRatio * 50, 0, 100);                    // 籌碼換手(估)
    const distRatio = sc.net20 / (mean(tail(o.volume, 20)) / 1000 || 1);
    const distribute = 50 - 50 * Math.tanh(distRatio / 2);   // 法人賣超越重，出貨壓力越高

    // 追價意願：近 5 日收盤在當日 K 棒的相對位置，收高代表買方願意追價
    const cp5 = [];
    for (let i = Math.max(0, n - 4); i <= n; i++) {
      const rng = o.high[i] - o.low[i];
      cp5.push(rng ? (o.close[i] - o.low[i]) / rng * 100 : 50);
    }
    const chase = clamp(mean(cp5), 0, 100);
    const volRisk = clamp(sc.annualVol, 0, 100);
    const intraday = clamp(amp * 10, 0, 100);

    // 綜合風險等級。不可只看 daytrade —— 那衡量的是短線投機熱度，
    // 一檔空頭走勢、法人連續賣超的弱勢股當日若平盤量縮，daytrade 會很低，
    // 卻被標成「低風險」而誤導。故併入波動、出貨壓力與趨勢弱勢一起評。
    const overall = mean([daytrade, volRisk, distribute, 100 - sc.trend]);
    const level = overall >= 60 ? '高' : overall >= 35 ? '中' : '低';
    return { amp, volRatio, daytrade, turnover, chase, distribute, volRisk,
      intraday, overall, level };
  }

  /** 多空能量：以近 20 日紅 K / 黑 K 的成交量佔比切分。 */
  function energySplit(o) {
    const n = o.close.length, from = Math.max(0, n - 20);
    let bull = 0, bear = 0;
    for (let i = from; i < n; i++) {
      (o.close[i] >= o.open[i] ? (bull += o.volume[i]) : (bear += o.volume[i]));
    }
    const t = bull + bear || 1;
    return { bull: bull / t * 100, bear: bear / t * 100, ratio: bear ? bull / bear : 0 };
  }

  // ════════════════════════════════════════════════════════════════
  //  繪圖元件
  // ════════════════════════════════════════════════════════════════

  /** 環形進度圖（SVG 字串）。 */
  function ringSVG(pct, color, label, size = 62) {
    const r = size / 2 - 5, cx = size / 2, circ = 2 * Math.PI * r;
    const off = circ * (1 - clamp(pct, 0, 100) / 100);
    return `<div class="ring">
      <svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}">
        <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="#0f1319" stroke-width="6"/>
        <circle cx="${cx}" cy="${cx}" r="${r}" fill="none" stroke="${color}" stroke-width="6"
          stroke-dasharray="${circ}" stroke-dashoffset="${off}" stroke-linecap="round"
          transform="rotate(-90 ${cx} ${cx})"/>
        <text x="${cx}" y="${cx + 4}" text-anchor="middle" fill="#e8eaed"
          font-size="14" font-weight="700">${Math.round(pct)}%</text>
      </svg>
      <div class="rl">${label}</div></div>`;
  }

  /** 水平比例條。 */
  function barRow(label, pct, color) {
    return `<div class="barrow"><span class="lab">${label}</span>
      <span class="bar"><i style="width:${clamp(pct, 0, 100)}%;background:${color}"></i></span>
      <span class="pct">${Math.round(pct)}%</span></div>`;
  }

  /** 多邊形雷達圖，畫在 canvas 上。axes = [{label, value 0~100}] */
  function drawRadar(canvas, axes, color) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 200, h = CANVAS_H;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const cx = w / 2, cy = h / 2 + 4, R = Math.min(w, h) / 2 - 26;
    const N = axes.length;
    const pt = (i, rr) => {
      const a = -Math.PI / 2 + i * 2 * Math.PI / N;
      return [cx + Math.cos(a) * rr, cy + Math.sin(a) * rr];
    };

    // 背景網格
    ctx.strokeStyle = '#272c37'; ctx.lineWidth = 1;
    [0.25, 0.5, 0.75, 1].forEach(f => {
      ctx.beginPath();
      for (let i = 0; i < N; i++) { const [x, y] = pt(i, R * f); i ? ctx.lineTo(x, y) : ctx.moveTo(x, y); }
      ctx.closePath(); ctx.stroke();
    });
    for (let i = 0; i < N; i++) {
      const [x, y] = pt(i, R);
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(x, y); ctx.stroke();
    }

    // 數值多邊形
    ctx.beginPath();
    axes.forEach((a, i) => {
      const [x, y] = pt(i, R * clamp(a.value, 0, 100) / 100);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.closePath();
    ctx.fillStyle = color + '33'; ctx.fill();
    ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.stroke();

    // 軸標籤
    ctx.fillStyle = '#8b93a3'; ctx.font = '10px sans-serif';
    ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
    axes.forEach((a, i) => {
      const [x, y] = pt(i, R + 14);
      ctx.fillText(a.label, x, y);
    });
  }

  /** 籌碼熱區圖：價格軸上的成交量分佈橫條。 */
  function drawHeatmap(canvas, profile, curPrice) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 200, h = CANVAS_H;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const bs = profile.buckets, bh = h / bs.length;
    const padL = 34, barW = w - padL - 6;
    bs.forEach((b, i) => {
      const y = h - (i + 1) * bh;
      // 熱度：低量藍、中量黃、高量紅
      const t = b.ratio;
      const col = t > 0.66 ? '#e74c3c' : t > 0.33 ? '#f1b143' : '#3d6ea8';
      ctx.fillStyle = col;
      ctx.globalAlpha = 0.35 + t * 0.65;
      ctx.fillRect(padL, y + 0.5, Math.max(2, barW * t), bh - 1);
      ctx.globalAlpha = 1;
    });

    // 價格刻度（上中下三點）
    ctx.fillStyle = '#8b93a3'; ctx.font = '9px sans-serif';
    ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    [[0, profile.hi], [h / 2, (profile.hi + profile.lo) / 2], [h - 6, profile.lo]]
      .forEach(([y, p]) => ctx.fillText(Math.round(p), 2, y + 4));

    // 現價線
    const span = profile.hi - profile.lo || 1;
    const cy = h - (curPrice - profile.lo) / span * h;
    if (cy >= 0 && cy <= h) {
      ctx.strokeStyle = '#e8eaed'; ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
      ctx.beginPath(); ctx.moveTo(padL, cy); ctx.lineTo(w, cy); ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  /** 主力成本結構分布：籌碼集中度的堆疊橫條。 */
  function drawCostStruct(canvas, profile, vwapPrice, curPrice) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 200, h = CANVAS_H;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    // 以「現價」為基準切分持有成本：成本低於現價者獲利，高於現價者套牢。
    // （基準取現價而非 VWAP —— 判斷賺賠的是市價，不是平均成本。）
    let profit = 0, near = 0, trapped = 0;
    profile.buckets.forEach(b => {
      const mid = (b.lo + b.hi) / 2;
      const dev = curPrice ? (mid - curPrice) / curPrice : 0;
      if (dev < -0.05) profit += b.vol;        // 成本低於現價 5% 以上 → 獲利
      else if (dev > 0.05) trapped += b.vol;   // 成本高於現價 5% 以上 → 套牢
      else near += b.vol;                      // 現價附近 → 成本區
    });
    const t = profit + near + trapped || 1;
    const segs = [
      { lab: '獲利區（成本低於現價 5%）', v: profit / t * 100, c: '#e74c3c' },
      { lab: '成本區（現價 ±5%）', v: near / t * 100, c: '#f1b143' },
      { lab: '套牢區（成本高於現價 5%）', v: trapped / t * 100, c: '#3d8bfd' },
    ];

    let y = 14;
    ctx.font = '10px sans-serif'; ctx.textBaseline = 'middle';
    segs.forEach(s => {
      ctx.fillStyle = '#8b93a3'; ctx.textAlign = 'left';
      ctx.fillText(s.lab, 2, y);
      ctx.fillStyle = '#0f1319';
      ctx.fillRect(2, y + 10, w - 4, 12);
      ctx.fillStyle = s.c;
      ctx.fillRect(2, y + 10, (w - 4) * s.v / 100, 12);
      ctx.fillStyle = '#e8eaed'; ctx.textAlign = 'right';
      ctx.fillText(s.v.toFixed(1) + '%', w - 6, y + 16);
      y += 42;
    });

    // 現價 vs VWAP 偏離
    const dev = vwapPrice ? (curPrice - vwapPrice) / vwapPrice * 100 : 0;
    ctx.textAlign = 'left'; ctx.fillStyle = dev >= 0 ? '#e74c3c' : '#2ecc71';
    ctx.font = '11px sans-serif';
    ctx.fillText(`現價偏離 VWAP ${dev >= 0 ? '+' : ''}${dev.toFixed(2)}%`, 2, y + 4);
  }

  /**
   * 大戶持股比例變化圖：三個級距各一條，週頻。
   *
   * 畫的是「相對首期的變化（百分點）」而非絕對比例 —— 1000 張以上常佔六成以上，
   * 另兩級距僅約 2%，同一個 Y 軸畫絕對值會被大的那條壓平，三條線全成直線。
   * 絕對比例改由右側數值面板呈現。
   */
  function drawHolders(canvas, holders) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 300, h = CANVAS_H;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    // 各級距轉為相對首個有效值的變化量
    const series = HOLDER_LEVELS.map(lv => {
      const vals = (holders.levels || {})[lv.key] || [];
      const base = vals.find(v => v !== null && v !== undefined);
      return {
        ...lv,
        deltas: vals.map(v => (v === null || v === undefined) ? null : v - base),
      };
    }).filter(s => s.deltas.some(v => v !== null));
    if (!series.length) return;

    const all = series.flatMap(s => s.deltas).filter(v => v !== null);
    const span = Math.max(Math.abs(Math.min(...all)), Math.abs(Math.max(...all)), 0.05);
    const lo = -span * 1.2, hi = span * 1.2;   // 以 0 為中心，正負對稱
    const n = holders.dates.length;
    const padL = 40, padR = 6, padT = 10, padB = 18;
    const x = i => padL + (w - padL - padR) * (n > 1 ? i / (n - 1) : 0.5);
    const y = v => padT + (h - padT - padB) * (1 - (v - lo) / (hi - lo));

    // 刻度；0 軸加深，正負一眼可分
    ctx.font = '9px sans-serif'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    [-span, -span / 2, 0, span / 2, span].forEach(v => {
      const yy = y(v), zero = Math.abs(v) < 1e-9;
      ctx.strokeStyle = zero ? '#3a4150' : '#1e232c';
      ctx.lineWidth = zero ? 1.2 : 1;
      ctx.beginPath(); ctx.moveTo(padL, yy); ctx.lineTo(w - padR, yy); ctx.stroke();
      ctx.fillStyle = zero ? '#aeb6c4' : '#8b93a3';
      ctx.fillText((v > 0 ? '+' : '') + v.toFixed(2), 2, yy);
    });

    // 折線；null 值斷線而非補零
    series.forEach(s => {
      ctx.strokeStyle = s.color; ctx.lineWidth = 1.8;
      ctx.beginPath();
      let drawing = false;
      s.deltas.forEach((v, i) => {
        if (v === null) { drawing = false; return; }
        drawing ? ctx.lineTo(x(i), y(v)) : ctx.moveTo(x(i), y(v));
        drawing = true;
      });
      ctx.stroke();
      const li = s.deltas.length - 1;
      if (s.deltas[li] !== null) {
        ctx.fillStyle = s.color;
        ctx.beginPath(); ctx.arc(x(li), y(s.deltas[li]), 3, 0, Math.PI * 2); ctx.fill();
      }
    });

    // X 軸端點日期與單位說明
    ctx.fillStyle = '#8b93a3'; ctx.textBaseline = 'bottom';
    ctx.textAlign = 'left';   ctx.fillText((holders.dates[0] || '').slice(5) + ' 起算', padL, h - 4);
    ctx.textAlign = 'right';  ctx.fillText((holders.dates[n - 1] || '').slice(5), w - padR, h - 4);
    ctx.textAlign = 'left';   ctx.textBaseline = 'top';
    ctx.fillText('累積變化 (pp)', padL + 2, 2);
  }


  /** 法人買賣超柱狀圖（近 20 日三大法人合計）。 */
  function drawChipBars(canvas, chip) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 200, h = CANVAS_H;
    canvas.width = w * dpr; canvas.height = h * dpr;
    canvas.style.height = h + 'px';
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    ctx.clearRect(0, 0, w, h);

    const vals = tail(chip.total, 20);
    if (!vals.length) return;
    const mx = Math.max(...vals.map(Math.abs)) || 1;
    const zero = h / 2, bw = w / vals.length;
    ctx.strokeStyle = '#272c37';
    ctx.beginPath(); ctx.moveTo(0, zero); ctx.lineTo(w, zero); ctx.stroke();

    vals.forEach((v, i) => {
      const bh = Math.abs(v) / mx * (h / 2 - 12);
      ctx.fillStyle = v >= 0 ? '#e74c3c' : '#2ecc71';
      ctx.fillRect(i * bw + 1, v >= 0 ? zero - bh : zero, bw - 2, bh);
    });

    // 累計線
    ctx.strokeStyle = '#f1b143'; ctx.lineWidth = 1.5;
    ctx.beginPath();
    let acc = 0;
    const accs = vals.map(v => (acc += v));
    const amx = Math.max(...accs.map(Math.abs)) || 1;
    accs.forEach((a, i) => {
      const x = i * bw + bw / 2, y = zero - a / amx * (h / 2 - 12);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  }

  // ════════════════════════════════════════════════════════════════
  //  卡片渲染
  // ════════════════════════════════════════════════════════════════
  let _mainChart = null;

  /** 報價列。成交筆數非 JSON 提供欄位，顯示為 —。 */
  function renderQuote(d) {
    const o = d.ohlcv, n = o.close.length - 1;
    const cp = nz(d.quote?.change_pct), cs = nz(d.quote?.change);
    const cls = cp > 0 ? 'up' : cp < 0 ? 'dn' : 'flat';
    const sgn = cp > 0 ? '+' : '';
    const cells = [
      ['今日收盤價', fmt(d.quote?.close, 2), cls],
      ['今日漲跌', `${sgn}${fmt(cs, 2)}`, cls],
      ['今日漲幅', `${sgn}${fmt(cp, 2)}%`, cls],
      ['成交量（張）', fmtInt(o.volume[n] / 1000), ''],
      ['成交筆數', '—', ''],
      ['開盤', fmt(o.open[n], 2), ''],
      ['最高', fmt(o.high[n], 2), ''],
      ['最低', fmt(o.low[n], 2), ''],
      ['最新交易日', o.date[n] || '—', ''],
      ['資料筆數', `${o.close.length} 日`, ''],
    ];
    $('quotebar').innerHTML = cells.map(([l, v, c]) =>
      `<div class="qcell"><div class="qlab">${l}</div>
       <div class="qval ${c}">${v}</div></div>`).join('');
  }

  /** 01 主K線圖：沿用 lightweight-charts，與站內其他頁面一致。 */
  function cardMainChart(d) {
    return `<div class="card c8"><h3><span class="no">01</span>主K線圖
      <span class="sub">K + MA5/10/20/60 + 量</span></h3>
      <div class="chartbox" id="kline"></div></div>`;
  }

  function mountMainChart(d) {
    const el = $('kline');
    // lightweight-charts 由 CDN 載入，離線或封鎖時降級為提示，不影響其餘卡片
    if (typeof LightweightCharts === 'undefined') {
      el.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;'
        + 'height:100%;color:var(--sub);font-size:12px;">⚠ 圖表元件載入失敗（CDN 無法連線）</div>';
      return;
    }
    if (_mainChart) { _mainChart.remove(); _mainChart = null; }
    _mainChart = LightweightCharts.createChart(el, {
      layout: { background: { color: '#161a22' }, textColor: '#8b93a3', fontSize: 10 },
      grid: { vertLines: { color: '#1e232c' }, horzLines: { color: '#1e232c' } },
      rightPriceScale: { borderColor: '#272c37' },
      timeScale: { borderColor: '#272c37' },
      crosshair: { mode: 0 },
      height: el.clientHeight,
    });

    const o = d.ohlcv;
    const candles = o.date.map((t, i) => ({
      time: t, open: o.open[i], high: o.high[i], low: o.low[i], close: o.close[i]
    }));
    _mainChart.addCandlestickSeries({
      upColor: '#e74c3c', downColor: '#2ecc71',
      borderUpColor: '#e74c3c', borderDownColor: '#2ecc71',
      wickUpColor: '#e74c3c', wickDownColor: '#2ecc71',
    }).setData(candles);

    // MA10 後端未提供，於此補算
    const ma10 = sma(o.close, 10);
    const lines = [
      [d.indicators?.ma5, '#f1b143', 'MA5'],
      [ma10, '#7c5cff', 'MA10'],
      [d.indicators?.ma20, '#4ea1f3', 'MA20'],
      [d.indicators?.ma60, '#8b93a3', 'MA60'],
    ];
    lines.forEach(([vals, color]) => {
      if (!vals) return;
      const s = _mainChart.addLineSeries({ color, lineWidth: 1, priceLineVisible: false });
      s.setData(o.date.map((t, i) => ({ time: t, value: vals[i] }))
        .filter(p => p.value !== null && p.value !== undefined));
    });

    const volSeries = _mainChart.addHistogramSeries({
      priceFormat: { type: 'volume' }, priceScaleId: '',
      scaleMargins: { top: 0.82, bottom: 0 },
    });
    volSeries.setData(o.date.map((t, i) => ({
      time: t, value: o.volume[i] / 1000,
      color: o.close[i] >= o.open[i] ? 'rgba(231,76,60,.45)' : 'rgba(46,204,113,.45)'
    })));
    _mainChart.timeScale().fitContent();
  }

  /** 02 AI 決策核心：彙整後端 summary + 前端衍生風險值。 */
  function cardDecision(d, sc, rk) {
    const s = Object.fromEntries((d.summary || []).map(x => [x.label, x]));
    const dirTag = x => !x ? '<span class="tag n">—</span>'
      : `<span class="tag ${x.direction === 'up' ? 'r' : x.direction === 'down' ? 'g' : 'y'}">${x.value}</span>`;
    const L = d.levels || {};
    const rows = [
      ['趨勢判斷', dirTag(s['趨勢方向'])],
      ['短線狀態', dirTag(s['KD狀態'])],
      ['主力行為', `<span class="tag ${d.signal?.label?.includes('進') ? 'r' : 'y'}">${d.signal?.label || '—'}</span>`],
      ['籌碼結構', `<span class="tag ${sc.chip >= 55 ? 'r' : sc.chip <= 45 ? 'g' : 'y'}">${sc.chip >= 55 ? '偏多' : sc.chip <= 45 ? '偏空' : '中性'}</span>`],
      ['隔日沖風險 <span class="est">(估)</span>', `<b>${Math.round(rk.daytrade)}%</b>`],
      ['籌碼健康度', `<b>${sc.chip} 分</b>`],
      ['支撐區', `<b>${L.support || '—'}</b>`],
      ['壓力區', `<b>${L.resistance || '—'}</b>`],
      ['風險等級', `<span class="tag ${rk.level === '高' ? 'r' : rk.level === '中' ? 'y' : 'g'}">${rk.level}</span>`],
    ];
    return `<div class="card c4"><h3><span class="no">02</span>AI 決策核心
      <span class="sub">AI DECISION CORE</span></h3>
      <div class="warn">⚠ ${d.signal?.label || 'AI CAUTION'}</div>
      ${rows.map(([k, v]) => `<div class="row"><span class="k">${k}</span><span class="v">${v}</span></div>`).join('')}
    </div>`;
  }

  /** 03 多維度評分雷達 */
  function cardRadar(sc) {
    return `<div class="card c4"><h3><span class="no">03</span>多維度評分
      <span class="sub">綜合 ${sc.total} / 100</span></h3>
      <canvas id="radar-score"></canvas>
      <div style="text-align:center;margin-top:4px;">
        <span class="tag ${sc.grade === 'A' ? 'r' : sc.grade === 'B' ? 'y' : 'n'}">
          評等 ${sc.grade}　總分 ${sc.total}/100</span></div>
    </div>`;
  }

  /** 04 籌碼熱區圖 */
  function cardHeatmap() {
    return `<div class="card c4"><h3><span class="no">04</span>AI 籌碼熱區圖
      <span class="sub">近 60 日價量分佈</span></h3>
      <canvas id="heatmap"></canvas>
      <div class="note">橫條長度為該價格區間累計成交量；虛線為現價。</div></div>`;
  }

  /** 05 風險管理雷達 */
  function cardRiskRadar(rk) {
    return `<div class="card c4"><h3><span class="no">05</span>風險管理雷達</h3>
      <canvas id="radar-risk"></canvas>
      <div class="row"><span class="k">綜合風險指數 <span class="est">(估)</span></span>
        <span class="v"><span class="tag ${rk.level === '高' ? 'r' : rk.level === '中' ? 'y' : 'g'}">${rk.level}</span>
        ${Math.round(rk.overall)}%</span></div>
      <div class="note">綜合隔日沖熱度、波動率、出貨壓力與趨勢弱勢四項。</div></div>`;
  }

  /** 06 AI 預測路徑 */
  function cardPrediction(d) {
    const p = d.prediction || {};
    return `<div class="card c4"><h3><span class="no">06</span>AI 預測路徑
      <span class="sub">後端模型輸出</span></h3>
      ${barRow('上漲', nz(p.up) * 100, '#e74c3c')}
      ${barRow('盤整', nz(p.sideways) * 100, '#f1b143')}
      ${barRow('下跌', nz(p.down) * 100, '#2ecc71')}
      <div class="row" style="margin-top:6px;"><span class="k">模型歷史準確率</span>
        <span class="v">${fmt(nz(p.accuracy) * 100, 1)}%</span></div>
      ${(d.patterns || []).map(pt =>
        `<div class="note">◆ <b>${pt.label}</b>：${pt.desc}</div>`).join('')}
    </div>`;
  }

  /** 07 主力成本結構分布 */
  function cardCostStruct(vw, cur) {
    return `<div class="card c4"><h3><span class="no">07</span>主力成本結構分布
      <span class="sub">20 日 VWAP ${fmt(vw, 2)}</span></h3>
      <canvas id="coststruct"></canvas></div>`;
  }

  /** 08 法人行為計量 */
  function cardChipFlow(d, sc) {
    const c = d.chip || {}, n = (c.dates || []).length;
    const rows = [];
    for (let i = n - 1; i >= Math.max(0, n - 4); i--) {
      rows.push(`<div class="row"><span class="k">${(c.dates[i] || '').slice(5)}</span>
        <span class="v" style="font-weight:400;font-size:11px;">
        外 <b class="${c.foreign[i] >= 0 ? 'up' : 'dn'}">${fmtInt(c.foreign[i])}</b> ·
        投 <b class="${c.trust[i] >= 0 ? 'up' : 'dn'}">${fmtInt(c.trust[i])}</b> ·
        自 <b class="${c.dealer[i] >= 0 ? 'up' : 'dn'}">${fmtInt(c.dealer[i])}</b></span></div>`);
    }
    const net5 = sum(tail(c.total, 5));
    return `<div class="card c4"><h3><span class="no">08</span>法人行為計量
      <span class="sub">三大法人買賣超（張）</span></h3>
      <canvas id="chipbars"></canvas>
      ${rows.join('')}
      <div class="row"><span class="k">近 20 日累計</span>
        <span class="v ${sc.net20 >= 0 ? 'up' : 'dn'}">${fmtInt(sc.net20)} 張</span></div>
      <div class="row"><span class="k">近 5 日累計</span>
        <span class="v ${net5 >= 0 ? 'up' : 'dn'}">${fmtInt(net5)} 張</span></div></div>`;
  }

  /** 09 隔日沖風險分析（全為估算值） */
  function cardDaytradeRisk(rk) {
    return `<div class="card c3"><h3><span class="no">09</span>隔日沖風險分析
      <span class="sub">估算值</span></h3>
      ${barRow('主力出貨壓力', rk.distribute, '#e74c3c')}
      ${barRow('籌碼換手率', rk.turnover, '#f1b143')}
      ${barRow('追價意願', rk.chase, '#4ea1f3')}
      ${barRow('隔日沖風險度', rk.daytrade, '#e74c3c')}
      ${barRow('日內波動率', rk.intraday, '#7c5cff')}
      <div class="note">⚠ 台股當沖／隔日沖明細無免費資料源，本卡以振幅與量能倍數推估，僅供相對比較。</div></div>`;
  }

  /** 10 AI 多空能量條 */
  function cardEnergy(en) {
    return `<div class="card c3"><h3><span class="no">10</span>AI 多空能量條
      <span class="sub">近 20 日量能歸屬</span></h3>
      ${barRow('多方量能', en.bull, '#e74c3c')}
      ${barRow('空方量能', en.bear, '#2ecc71')}
      <div class="row" style="margin-top:8px;"><span class="k">多空比</span>
        <span class="v ${en.ratio >= 1 ? 'up' : 'dn'}">${fmt(en.ratio, 2)} 倍${en.ratio >= 1 ? '多' : '空'}</span></div>
      <div class="note">以紅 K／黑 K 當日成交量加總切分。</div></div>`;
  }

  /** 11 健康度綜合評估 */
  function cardHealth(sc) {
    const items = [
      ['籌碼健康度', sc.chip, '#4ea1f3'],
      ['技術面健康', sc.trend, '#e74c3c'],
      ['資金動能度', sc.volume, '#f1b143'],
      ['波動穩定度', sc.volatility, '#2ecc71'],
      ['動能強度', sc.momentum, '#7c5cff'],
    ];
    const avg = Math.round(mean(items.map(i => i[1])));
    return `<div class="card c3"><h3><span class="no">11</span>健康度綜合評估</h3>
      <div class="rings">${items.map(([l, v, c]) => ringSVG(v, c, l)).join('')}</div>
      <div class="row" style="margin-top:8px;"><span class="k">總評</span>
        <span class="v">${avg >= 70 ? '良好' : avg >= 50 ? '普通' : '偏弱'}（平均 ${avg} 分）</span></div></div>`;
  }

  /** 12 主力動態信號燈 */
  function cardSignalLight(d, sc, rk) {
    const lights = [
      ['趨勢', sc.trend >= 60 ? 'g' : sc.trend >= 40 ? 'y' : 'r', sc.trend >= 60 ? '偏多延續' : sc.trend >= 40 ? '中性整理' : '偏空修正'],
      ['籌碼', sc.chip >= 55 ? 'g' : sc.chip >= 45 ? 'y' : 'r', sc.chip >= 55 ? '法人偏多' : sc.chip >= 45 ? '中性' : '法人偏空'],
      ['動能', sc.momentum >= 60 ? 'g' : sc.momentum >= 40 ? 'y' : 'r', `RSI ${fmt(sc.rsi, 0)}`],
      ['風險', rk.level === '低' ? 'g' : rk.level === '中' ? 'y' : 'r', `${rk.level}度風險`],
    ];
    const reds = lights.filter(l => l[1] === 'r').length;
    const cur = reds >= 2 ? ['r', '紅燈（觀望）'] : reds === 1 ? ['y', '黃燈（注意）'] : ['g', '綠燈（可續抱）'];
    return `<div class="card c3"><h3><span class="no">12</span>AI 主力動態信號燈</h3>
      ${lights.map(([k, c, t]) =>
        `<div class="row"><span class="k"><i class="dot ${c === 'g' ? '' : c}"></i>${k}</span>
         <span class="v">${t}</span></div>`).join('')}
      <div class="row" style="margin-top:6px;"><span class="k">目前燈號</span>
        <span class="v"><span class="tag ${cur[0]}">${cur[1]}</span></span></div></div>`;
  }

  /** 13 AI 信心維度 */
  function cardConfidence(d, sc) {
    const p = d.prediction || {};
    const completeness = (d.ohlcv?.close?.length >= 60 ? 100 : 60);
    const items = [
      ['模型準確率', nz(p.accuracy) * 100, '#4ea1f3'],
      ['資料完整度', completeness, '#2ecc71'],
      ['訊號穩定度', clamp(100 - Math.abs(50 - sc.momentum) * 2, 0, 100), '#f1b143'],
      ['策略適用度', clamp(sc.total, 0, 100), '#7c5cff'],
    ];
    const conf = Math.round(mean(items.map(i => i[1])));
    return `<div class="card c3"><h3><span class="no">13</span>AI 信心維度
      <span class="sub">AI CONFIDENCE ${conf}%</span></h3>
      ${items.map(([l, v, c]) => barRow(l, v, c)).join('')}
      <div class="note">資料截至 ${last(d.ohlcv?.date) || '—'}</div></div>`;
  }

  /** 14 籌碼異動摘要 */
  function cardChipSummary(d, sc) {
    const c = d.chip || {}, n = (c.dates || []).length - 1;
    const rows = [
      ['外資', c.foreign?.[n]],
      ['投信', c.trust?.[n]],
      ['自營商', c.dealer?.[n]],
      ['三大法人', c.total?.[n]],
    ];
    return `<div class="card c3"><h3><span class="no">14</span>籌碼異動摘要
      <span class="sub">${c.dates?.[n] || ''}</span></h3>
      ${rows.map(([k, v]) => `<div class="row"><span class="k">${k}</span>
        <span class="v ${nz(v) >= 0 ? 'up' : 'dn'}">${fmtInt(v)} 張</span></div>`).join('')}
      <div class="row"><span class="k">20 日籌碼偏向</span>
        <span class="v"><span class="tag ${sc.chip >= 55 ? 'r' : sc.chip <= 45 ? 'g' : 'y'}">
        ${sc.chip >= 55 ? '偏多買盤' : sc.chip <= 45 ? '偏空賣壓' : '中性'}</span></span></div></div>`;
  }

  /** 15 買賣力分佈（法人 vs 散戶，散戶為推估） */
  function cardPowerSplit(d, sc) {
    const c = d.chip || {}, o = d.ohlcv;
    // 法人主導度：近 5 日法人淨買賣超絕對值合計 / 同期成交量（張）。
    // 僅有淨額而無法人買賣總額，故此為流向強度，不等於實際成交佔比。
    const net5abs = sum(tail(c.total, 5).map(Math.abs));
    const vol5 = sum(tail(o.volume, 5)) / 1000;
    const instRatio = clamp(vol5 ? net5abs / vol5 * 100 : 0, 0, 100);
    const retailRatio = 100 - instRatio;
    const net5 = sum(tail(c.total, 5));
    return `<div class="card c4"><h3><span class="no">15</span>買賣力分佈
      <span class="sub">法人流向強度（推估）</span></h3>
      <div class="rings">
        ${ringSVG(instRatio, '#e74c3c', '法人主導度')}
        ${ringSVG(retailRatio, '#4ea1f3', '其他籌碼')}
        ${ringSVG(clamp(sc.volume, 0, 100), '#f1b143', '量能強度')}
      </div>
      <div class="row" style="margin-top:6px;"><span class="k">近 5 日法人淨額</span>
        <span class="v ${net5 >= 0 ? 'up' : 'dn'}">${fmtInt(net5)} 張</span></div>
      <div class="note">資料日 ${last(c.dates) || '—'}；法人淨額絕對值佔近 5 日成交量比重。
        免費資料源無法人買賣總額與散戶明細，故無法計算真實散戶佔比。</div></div>`;
  }

  /** 16 多空強度分佈 */
  function cardStrength(sc, en) {
    return `<div class="card c4"><h3><span class="no">16</span>多空強度分佈</h3>
      <div class="rings">
        ${ringSVG(en.bull, '#e74c3c', '多方強度')}
        ${ringSVG(en.bear, '#2ecc71', '空方強度')}
        ${ringSVG(sc.volume, '#f1b143', '量能強度')}
      </div>
      <div class="row" style="margin-top:8px;"><span class="k">信號強度</span>
        <span class="v">${sc.grade} 級（1~5 級，目前綜合 ${sc.total}）</span></div></div>`;
  }

  /**
   * 17 主力追蹤總評。
   * 註：依專案規範，報告內文應由 Gemini 產生；此處為前端規則模板，
   * 後續可由 gemini_writer.py 預產文字寫回 JSON 再讀取。
   */
  function cardVerdict(d, sc, rk, vw) {
    const cur = last(d.ohlcv.close);
    const dev = vw ? (cur - vw) / vw * 100 : 0;
    const verdict = sc.total >= 70 ? '偏多加碼' : sc.total >= 50 ? '調節減碼' : '保守觀望';
    const text = `經 ${d.ohlcv.close.length} 日行為綜合研判：法人近 20 日合計 `
      + `${fmtInt(sc.net20)} 張，收盤相對 20 日 VWAP ${dev >= 0 ? '+' : ''}${fmt(dev, 1)}%，`
      + `RSI ${fmt(sc.rsi, 0)}、年化波動率 ${fmt(sc.annualVol, 1)}%。`
      + `綜合評分 ${sc.total}/100（${sc.grade} 級），風險等級${rk.level}。`;
    return `<div class="card c4"><h3><span class="no">17</span>主力追蹤總評
      <span class="sub">規則引擎</span></h3>
      <div style="display:flex;align-items:center;gap:10px;">
        <span class="verdict">${verdict}</span>
        <span class="pill" style="margin-left:auto;">${rk.level}風險</span>
      </div>
      <div class="note" style="font-size:11px;line-height:1.7;">${text}</div></div>`;
  }

  /**
   * 18 大戶持股分布（集保股權分散表，週頻）。
   * 三個級距分層互斥，追蹤持股比例的週變化：比例上升代表籌碼向大戶集中。
   */
  function cardHolders(d) {
    const hd = d.holders;
    const has = hd && hd.dates && hd.dates.length;
    if (!has) {
      return `<div class="card c12"><h3><span class="no">18</span>大戶持股分布
        <span class="sub">集保股權分散表</span></h3>
        <div class="note" style="padding:14px 0;">
          尚無資料。此欄位需後端重跑 <code>build_docs.py</code> 取得集保股權分散表
          （<code>taiwan_stock_holding_shares_per</code>）後才會出現。</div></div>`;
    }

    const n = hd.dates.length;
    const rows = HOLDER_LEVELS.map(lv => {
      const vals = (hd.levels || {})[lv.key] || [];
      const cur = vals[n - 1], prev = vals[n - 2];
      const diff = (cur != null && prev != null) ? cur - prev : null;
      const dCls = diff == null ? 'flat' : diff > 0 ? 'up' : diff < 0 ? 'dn' : 'flat';
      const dTxt = diff == null ? '—'
        : `${diff > 0 ? '+' : ''}${diff.toFixed(3)} pp`;
      return `<div class="row">
        <span class="k"><i style="display:inline-block;width:9px;height:3px;
          background:${lv.color};border-radius:2px;"></i>${lv.label}</span>
        <span class="v">${cur == null ? '—' : cur.toFixed(2) + '%'}
          <span class="${dCls}" style="font-size:11px;margin-left:6px;">${dTxt}</span></span>
      </div>`;
    }).join('');

    // 三級距合計：600 張以上的整體集中度
    const sumAt = i => HOLDER_LEVELS.reduce((a, lv) => {
      const v = ((hd.levels || {})[lv.key] || [])[i];
      return a + (v == null ? 0 : v);
    }, 0);
    const tCur = sumAt(n - 1), tPrev = n > 1 ? sumAt(n - 2) : null;
    const tDiff = tPrev == null ? null : tCur - tPrev;
    const tCls = tDiff == null ? 'flat' : tDiff > 0 ? 'up' : tDiff < 0 ? 'dn' : 'flat';

    return `<div class="card c12"><h3><span class="no">18</span>大戶持股分布
      <span class="sub">集保股權分散表 · 週頻 · 截至 ${hd.dates[n - 1]}</span></h3>
      <div class="holderwrap">
        <div><canvas id="holders"></canvas></div>
        <div>
          ${rows}
          <div class="row" style="border-top:1px solid var(--border);margin-top:4px;padding-top:6px;">
            <span class="k">600 張以上合計</span>
            <span class="v">${tCur.toFixed(2)}%
              <span class="${tCls}" style="font-size:11px;margin-left:6px;">${
                tDiff == null ? '—' : (tDiff > 0 ? '+' : '') + tDiff.toFixed(3) + ' pp'}</span></span>
          </div>
          <div class="note">分層互斥，不累積。pp = 百分點。比例上升代表籌碼向該級距集中；
            集保每週五結算、次週初公布，與日 K 無法逐日對齊。</div>
        </div>
      </div></div>`;
  }


  /** 19 原始資料表（近 10 日） */
  function cardRawTable(d) {
    const o = d.ohlcv, c = d.chip || {}, n = o.close.length;
    let rows = '';
    for (let i = n - 1; i >= Math.max(0, n - 10); i--) {
      rows += `<tr>
        <td>${o.date[i]}</td><td>${fmt(o.open[i], 1)}</td><td>${fmt(o.high[i], 1)}</td>
        <td>${fmt(o.low[i], 1)}</td><td><b>${fmt(o.close[i], 1)}</b></td>
        <td>${fmtInt(o.volume[i] / 1000)}</td>
        <td class="${nz(c.total?.[i]) >= 0 ? 'up' : 'dn'}">${fmtInt(c.total?.[i])}</td></tr>`;
    }
    return `<div class="card c12"><h3><span class="no">19</span>原始資料表
      <span class="sub">近 10 個交易日</span></h3>
      <table style="width:100%;border-collapse:collapse;font-size:11px;">
        <thead><tr style="color:var(--sub);text-align:right;">
          <th style="text-align:left;">日期</th><th>開</th><th>高</th><th>低</th>
          <th>收</th><th>量(張)</th><th>法人(張)</th></tr></thead>
        <tbody style="text-align:right;">${rows}</tbody></table>
      <style>#grid td{padding:3px 4px;border-bottom:1px solid rgba(255,255,255,.04);}
        #grid td:first-child{text-align:left;color:var(--sub);}
        #grid th{padding:3px 4px;font-weight:500;}</style></div>`;
  }

  // ════════════════════════════════════════════════════════════════
  //  主流程
  // ════════════════════════════════════════════════════════════════
  function render(d) {
    const sc = scoreDimensions(d);
    const rk = riskMetrics(d, sc);
    const en = energySplit(d.ohlcv);
    const vw = vwap(d.ohlcv, 20);
    const cur = last(d.ohlcv.close);

    renderQuote(d);

    $('grid').innerHTML = [
      cardMainChart(d), cardDecision(d, sc, rk), cardRadar(sc), cardHeatmap(),
      cardRiskRadar(rk), cardPrediction(d), cardCostStruct(vw, cur),
      cardChipFlow(d, sc), cardDaytradeRisk(rk), cardEnergy(en), cardHealth(sc),
      cardSignalLight(d, sc, rk), cardConfidence(d, sc), cardChipSummary(d, sc),
      cardPowerSplit(d, sc), cardStrength(sc, en), cardVerdict(d, sc, rk, vw),
      cardHolders(d), cardRawTable(d),
    ].join('');

    const stEl = $('status');
    if (stEl) stEl.style.display = 'none';
    $('grid').style.display = 'grid';

    // canvas 需在插入 DOM 後才量得到寬度。
    // 各繪圖步驟獨立保護：單一元件失敗不應讓整頁報「找不到資料」。
    const safe = (label, fn) => { try { fn(); } catch (e) { console.warn('[draw] ' + label, e); } };
    safe('kline', () => mountMainChart(d));
    safe('radar-score', () => drawRadar($('radar-score'), [
      { label: '趨勢', value: sc.trend }, { label: '動能', value: sc.momentum },
      { label: '籌碼', value: sc.chip }, { label: '量能', value: sc.volume },
      { label: '穩定', value: sc.volatility },
    ], '#e74c3c'));
    safe('radar-risk', () => drawRadar($('radar-risk'), [
      { label: '波動風險', value: rk.volRisk }, { label: '隔日沖', value: rk.daytrade },
      { label: '換手風險', value: rk.turnover }, { label: '出貨壓力', value: rk.distribute },
      { label: '日內波動', value: rk.intraday },
    ], '#f1b143'));
    const vp = volumeProfile(d.ohlcv, 60, 24);
    safe('heatmap', () => drawHeatmap($('heatmap'), vp, cur));
    safe('coststruct', () => drawCostStruct($('coststruct'), vp, vw, cur));
    safe('chipbars', () => drawChipBars($('chipbars'), d.chip || {}));
    if (d.holders && d.holders.dates && d.holders.dates.length) {
      safe('holders', () => drawHolders($('holders'), d.holders));
    }
  }

  // ════════════════════════════════════════════════════════════════
  //  對外介面
  // ════════════════════════════════════════════════════════════════

  /**
   * 將儀表板渲染進指定容器。
   *
   * @param {HTMLElement} root 容器，內須含 [data-dash="quotebar"] 與 [data-dash="grid"]
   * @param {object} data      docs/stocks/<id>.json 的內容
   */
  function renderDashboard(root, data) {
    _root = root;
    const qb = root.querySelector('[data-dash="quotebar"]');
    const gd = root.querySelector('[data-dash="grid"]');
    if (!qb || !gd) throw new Error('容器需含 data-dash="quotebar" 與 data-dash="grid"');
    qb.id = 'quotebar';
    gd.id = 'grid';
    try {
      render(data);
    } finally {
      _root = document;   // 還原，避免影響宿主頁面其他查找
    }
  }

  /** 釋放主K線圖表實例。切換個股或離開分頁前呼叫。 */
  function destroyDashboard() {
    if (_mainChart) { _mainChart.remove(); _mainChart = null; }
  }

  window.StockDashboard = { render: renderDashboard, destroy: destroyDashboard };

})();
