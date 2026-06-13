# 週報分頁重新設計 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把週報分頁從統計傾印改成三層綜合決策儀表板（敘事／族群輪動象限／行動清單）。

**Architecture:** 後端只動 `scripts/weekly_summary.py` —— 抽出可測的純函式計算 level/prev_change/narrative context，再把結果寫進 `weekly.json`；前端只重寫 `docs/index.html` 的 `renderWeekly()`，第三層在瀏覽器把週報 buys 與已載入的 `DAILY_DATA` join。

**Tech Stack:** Python（pytest）、Gemini API（`gemini_writer.GeminiWriter`）、純 JS（無框架，瀏覽器手動驗證）。

**Spec:** [docs/superpowers/specs/2026-06-13-weekly-dashboard-redesign.md](../specs/2026-06-13-weekly-dashboard-redesign.md)

---

## File Structure

- `scripts/weekly_summary.py`（修改）— 新增三個純函式 + 在 `run_weekly_summary` 串接、複製趨勢圖、呼叫 Gemini。
- `tests/test_weekly_summary.py`（新增）— 純函式的單元測試。
- `docs/index.html`（修改）— 重寫 `renderWeekly()`，新增象限分類與 action-card join。

後端純邏輯與其 I/O 副作用分離：`compute_sector_metrics` / `load_prev_week_changes` / `build_narrative_context` 為純函式可測；`run_weekly_summary` 負責串接與副作用。

---

## Task 1：族群指標計算（level + change + prev_change）

把現有 `run_weekly_summary` 內聯的 change 計算抽成純函式，並加上 level 與 prev_change。

**Files:**
- Modify: `scripts/weekly_summary.py`
- Test: `tests/test_weekly_summary.py`（新增）

- [ ] **Step 1：寫失敗測試**

建立 `tests/test_weekly_summary.py`：

```python
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from weekly_summary import compute_sector_metrics


def _report(sectors):
    return {'sectors': {name: {'avg_ret_20d': v, 'stocks': []} for name, v in sectors.items()}}


def test_compute_sector_metrics_change_and_level():
    week = [
        _report({'A': 1.0, 'B': 5.0}),   # 週一
        _report({'A': 4.0, 'B': 2.0}),   # 週五（最後一日 = level）
    ]
    metrics = compute_sector_metrics(week, prev_changes={})
    by = {m['sector']: m for m in metrics}
    assert by['A']['change'] == 3.0      # 4 - 1
    assert by['A']['level'] == 4.0       # 最後一日水位
    assert by['B']['change'] == -3.0     # 2 - 5
    assert by['B']['level'] == 2.0
    assert by['A']['prev_change'] is None


def test_compute_sector_metrics_sorted_by_change_desc():
    week = [_report({'A': 0.0, 'B': 0.0}), _report({'A': 1.0, 'B': 9.0})]
    metrics = compute_sector_metrics(week, prev_changes={})
    assert [m['sector'] for m in metrics] == ['B', 'A']


def test_compute_sector_metrics_prev_change_lookup():
    week = [_report({'A': 0.0}), _report({'A': 2.0})]
    metrics = compute_sector_metrics(week, prev_changes={'A': 5.0})
    assert metrics[0]['prev_change'] == 5.0
```

- [ ] **Step 2：跑測試確認失敗**

Run: `python -m pytest tests/test_weekly_summary.py -v`
Expected: FAIL — `ImportError: cannot import name 'compute_sector_metrics'`

- [ ] **Step 3：實作 compute_sector_metrics**

在 `scripts/weekly_summary.py` 的 `import` 之後、`run_weekly_summary` 之前加入：

```python
def compute_sector_metrics(week_reports, prev_changes):
    """由舊到新排序的 week_reports，計算每族群本週變化、最新水位、上週變化。

    回傳依 change 由大到小排序的 list[dict]。
    """
    first = week_reports[0]['sectors']
    last = week_reports[-1]['sectors']
    metrics = []
    for sector, data in last.items():
        if sector not in first:
            continue
        level = data['avg_ret_20d']
        change = level - first[sector]['avg_ret_20d']
        metrics.append({
            'sector': sector,
            'change': round(change, 2),
            'level': round(level, 2),
            'prev_change': prev_changes.get(sector),
        })
    metrics.sort(key=lambda m: m['change'], reverse=True)
    return metrics
```

- [ ] **Step 4：跑測試確認通過**

Run: `python -m pytest tests/test_weekly_summary.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5：commit**

```bash
git add scripts/weekly_summary.py tests/test_weekly_summary.py
git commit -m "feat: 週報 compute_sector_metrics — 加入 level 與 prev_change"
```

---

## Task 2：讀取上週 weekly.json 的族群變化

**Files:**
- Modify: `scripts/weekly_summary.py`
- Test: `tests/test_weekly_summary.py`

- [ ] **Step 1：寫失敗測試**

在 `tests/test_weekly_summary.py` 末尾加入：

```python
import json
from weekly_summary import load_prev_week_changes


def test_load_prev_week_changes_reads_latest_prior(tmp_path):
    # 建兩個舊週報資料夾，應讀較新的那個（且早於 today）
    (tmp_path / 'weekly_20260605').mkdir()
    (tmp_path / 'weekly_20260605' / 'weekly.json').write_text(
        json.dumps({'sector_changes': [{'sector': 'A', 'change': 5.0}]}), encoding='utf-8')
    (tmp_path / 'weekly_20260529').mkdir()
    (tmp_path / 'weekly_20260529' / 'weekly.json').write_text(
        json.dumps({'sector_changes': [{'sector': 'A', 'change': 1.0}]}), encoding='utf-8')

    prev = load_prev_week_changes('20260612', base_dir=tmp_path)
    assert prev == {'A': 5.0}


def test_load_prev_week_changes_none_when_absent(tmp_path):
    assert load_prev_week_changes('20260612', base_dir=tmp_path) == {}
```

- [ ] **Step 2：跑測試確認失敗**

Run: `python -m pytest tests/test_weekly_summary.py -v`
Expected: FAIL — `cannot import name 'load_prev_week_changes'`

- [ ] **Step 3：實作 load_prev_week_changes**

在 `compute_sector_metrics` 後加入：

```python
def load_prev_week_changes(today_str, base_dir=Path('daily_reports')):
    """找早於 today_str 的最近一份 weekly_*/weekly.json，回傳 {sector: change}。"""
    base_dir = Path(base_dir)
    candidates = []
    for p in base_dir.glob('weekly_*/weekly.json'):
        tag = p.parent.name.replace('weekly_', '')
        if tag.isdigit() and tag < today_str:
            candidates.append((tag, p))
    if not candidates:
        return {}
    _, latest = max(candidates, key=lambda x: x[0])
    with open(latest, encoding='utf-8') as f:
        data = json.load(f)
    return {c['sector']: c['change'] for c in data.get('sector_changes', [])}
```

- [ ] **Step 4：跑測試確認通過**

Run: `python -m pytest tests/test_weekly_summary.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5：commit**

```bash
git add scripts/weekly_summary.py tests/test_weekly_summary.py
git commit -m "feat: 週報 load_prev_week_changes — 讀上週族群變化供 vs上週對比"
```

---

## Task 3：敘事 context 組裝 + Gemini 呼叫含 fallback

**Files:**
- Modify: `scripts/weekly_summary.py`
- Test: `tests/test_weekly_summary.py`

- [ ] **Step 1：寫失敗測試**

在 `tests/test_weekly_summary.py` 末尾加入：

```python
from weekly_summary import build_narrative_context, generate_narrative


def test_build_narrative_context_picks_top_movers():
    metrics = [
        {'sector': 'A', 'change': 9.0, 'level': 10.0, 'prev_change': 1.0},
        {'sector': 'B', 'change': 3.0, 'level': 2.0, 'prev_change': None},
        {'sector': 'C', 'change': -8.0, 'level': -5.0, 'prev_change': 2.0},
    ]
    top_buys = [{'stock': '2368 金像電', 'buy_days': 5}]
    ctx = build_narrative_context(metrics, top_buys)
    assert ctx['accelerating'][0]['sector'] == 'A'
    assert ctx['decelerating'][0]['sector'] == 'C'
    assert ctx['top_buys'] == top_buys
    assert ctx['sector_metrics'] == metrics


class _FakeWriter:
    def __init__(self, raise_it=False):
        self.raise_it = raise_it
    def generate(self, task, context, **kw):
        if self.raise_it:
            raise RuntimeError('api down')
        return '本週輪動回顧…\n下週聚焦…'


def test_generate_narrative_returns_text():
    out = generate_narrative(_FakeWriter(), {'x': 1}, '20260612')
    assert '回顧' in out


def test_generate_narrative_fallbacks_to_empty_on_error():
    out = generate_narrative(_FakeWriter(raise_it=True), {'x': 1}, '20260612')
    assert out == ''
```

- [ ] **Step 2：跑測試確認失敗**

Run: `python -m pytest tests/test_weekly_summary.py -v`
Expected: FAIL — `cannot import name 'build_narrative_context'`

- [ ] **Step 3：實作兩個函式**

在 `load_prev_week_changes` 後加入：

```python
def build_narrative_context(sector_metrics, top_buys):
    """組裝給 Gemini 的 weekly_report context.data。"""
    return {
        'sector_metrics': sector_metrics,
        'accelerating': sector_metrics[:3],
        'decelerating': sorted(sector_metrics, key=lambda m: m['change'])[:3],
        'top_buys': top_buys,
    }


def generate_narrative(writer, context_data, date_str):
    """呼叫 Gemini 生成兩段週報敘事；任何失敗回傳空字串（前端會隱藏敘事卡）。"""
    extra = ('請輸出兩段，第一段標題「本週輪動回顧」描述族群強弱輪動，'
             '第二段標題「下週聚焦」點出值得追蹤的族群與個股，繁體中文、各 200 字內。')
    try:
        return writer.generate(
            task='weekly_report',
            context={'date': date_str, 'data': context_data, 'extra': extra},
        )
    except Exception as e:
        print(f'   ⚠ Gemini 週報敘事生成失敗，略過：{e}')
        return ''
```

註：`gemini_writer.GeminiWriter.generate(task, context, use_grounding=False)` 期望 `context` 含 `data`/`date`/`extra` 鍵（見 PROMPTS 的 `weekly_report` 模板）。

- [ ] **Step 4：跑測試確認通過**

Run: `python -m pytest tests/test_weekly_summary.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5：commit**

```bash
git add scripts/weekly_summary.py tests/test_weekly_summary.py
git commit -m "feat: 週報敘事 context 組裝與 Gemini 呼叫（失敗安全 fallback）"
```

---

## Task 4：擴充 build_weekly_payload（level/prev_change/narrative）

**Files:**
- Modify: `scripts/weekly_summary.py`
- Test: `tests/test_weekly_summary.py`

- [ ] **Step 1：寫失敗測試**

在 `tests/test_weekly_summary.py` 末尾加入：

```python
from weekly_summary import build_weekly_payload


def test_build_weekly_payload_carries_new_fields():
    summary = {
        'week_ending': '2026-06-12',
        'days_covered': 5,
        'sector_changes': [{'sector': 'A', 'change': 3.0, 'level': 4.0, 'prev_change': 1.0}],
        'top_buys': [{'stock': '2368 金像電', 'buy_days': 5}],
        'narrative': '本週輪動回顧…',
    }
    payload = build_weekly_payload(summary)
    assert payload['meta']['date'] == '2026-06-12'
    assert payload['changes'][0] == {'sector': 'A', 'change': 3.0, 'level': 4.0, 'prev_change': 1.0}
    assert payload['buys'][0] == {'stock': '2368 金像電', 'days': 5}
    assert payload['narrative'] == '本週輪動回顧…'


def test_build_weekly_payload_narrative_defaults_empty():
    summary = {'week_ending': '2026-06-12', 'days_covered': 5,
               'sector_changes': [], 'top_buys': []}
    assert build_weekly_payload(summary)['narrative'] == ''
```

- [ ] **Step 2：跑測試確認失敗**

Run: `python -m pytest tests/test_weekly_summary.py -v`
Expected: FAIL — `KeyError: 'level'`（現有 build_weekly_payload 只帶 sector/change）

- [ ] **Step 3：改寫 build_weekly_payload**

把現有 `build_weekly_payload`（約在檔案 143-148 行）整個取代為：

```python
def build_weekly_payload(summary):
    return {
        'meta': {'date': summary.get('week_ending', ''), 'days': summary.get('days_covered', 0)},
        'changes': [
            {
                'sector': c['sector'],
                'change': c['change'],
                'level': c.get('level'),
                'prev_change': c.get('prev_change'),
            }
            for c in summary.get('sector_changes', [])
        ],
        'buys': [{'stock': b['stock'], 'days': b['buy_days']} for b in summary.get('top_buys', [])],
        'narrative': summary.get('narrative', ''),
    }
```

- [ ] **Step 4：跑測試確認通過**

Run: `python -m pytest tests/test_weekly_summary.py -v`
Expected: PASS（10 passed）

- [ ] **Step 5：commit**

```bash
git add scripts/weekly_summary.py tests/test_weekly_summary.py
git commit -m "feat: build_weekly_payload 帶出 level/prev_change/narrative"
```

---

## Task 5：串接 run_weekly_summary（用新函式 + 複製趨勢圖 + 生成敘事）

把 `run_weekly_summary` 內聯的 change/buy 計算換成新函式，並補上 prev_changes、level、narrative、複製趨勢圖到 docs/。無單元測試（I/O 副作用），以本機實跑驗證。

**Files:**
- Modify: `scripts/weekly_summary.py`

- [ ] **Step 1：替換內聯計算為新函式**

在 `run_weekly_summary` 中，把現有「本週最強/最弱族群」區塊（建立 `changes`、`sorted_changes` 的那段，約 73-80 行）整段取代為：

```python
    # 本週族群變化（含水位與 vs 上週）
    prev_changes = load_prev_week_changes(today.strftime('%Y%m%d'))
    sector_metrics = compute_sector_metrics(week_reports, prev_changes)
```

- [ ] **Step 2：更新 summary dict 的 sector_changes**

把 `summary = {...}` 內的 `'sector_changes'` 一行改為使用 `sector_metrics`（已含 sector/change/level/prev_change）：

```python
        'sector_changes': sector_metrics,
```

並把 `'chart_path'` 之後、`}` 之前補上 narrative（generate 在 Step 4 補；此處先放欄位）：

```python
        'narrative': '',
```

- [ ] **Step 3：更新 markdown 輸出迴圈**

原 md 迴圈 `for s, c in sorted_changes:` 改為走 `sector_metrics`：

```python
    for m in sector_metrics:
        md.append(f"- {m['sector']}：{m['change']:+.2f} pp（水位 {m['level']:+.2f}）\n")
```

- [ ] **Step 4：生成敘事並複製趨勢圖到 docs/**

在 `summary = {...}` 建立之後、寫出 json 之前，加入敘事生成；並在「寫入 GitHub Pages 靜態資料」區塊中複製趨勢圖。

敘事生成（緊接 summary 建立後）：

```python
    # 第一層敘事（Gemini；失敗安全略過）
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
        from gemini_writer import GeminiWriter
        narrative_ctx = build_narrative_context(sector_metrics, summary['top_buys'])
        summary['narrative'] = generate_narrative(
            GeminiWriter(), narrative_ctx, today.strftime('%Y-%m-%d'))
    except Exception as e:
        print(f'   ⚠ 敘事模組載入失敗，略過：{e}')
        summary['narrative'] = ''
```

複製趨勢圖（在 `docs_dir` 建立後、寫 weekly.json 旁）：

```python
    import shutil
    shutil.copy(chart_path, docs_dir / 'weekly_sector_trend.png')
    print('  docs/weekly_sector_trend.png 已更新')
```

- [ ] **Step 5：本機實跑驗證**

Run: `python scripts/weekly_summary.py`
Expected:
- console 印出 `docs/weekly.json 已更新`、`docs/weekly_sector_trend.png 已更新`
- 開啟 `docs/weekly.json` 確認：`changes[0]` 含 `level` 與 `prev_change`；`narrative` 為非空字串（若 Gemini 額度正常）或空字串（失敗 fallback，且程式未中斷）。

驗證指令（檢查 payload 欄位）：

```bash
python -c "import json;d=json.load(open('docs/weekly.json',encoding='utf-8'));print('narrative_len',len(d['narrative']));print('change0',d['changes'][0])"
```
Expected: 印出 `change0` 含 `level`/`prev_change` 鍵；不報錯。

- [ ] **Step 6：commit**

```bash
git add scripts/weekly_summary.py docs/weekly.json docs/weekly_sector_trend.png
git commit -m "feat: weekly_summary 串接 level/vs上週/敘事 + 複製趨勢圖到 docs"
```

---

## Task 6：前端 — 第一層敘事卡

重寫 `renderWeekly()` 起手，先加敘事卡（narrative 為空則隱藏）。前端無 JS 測試框架，以瀏覽器驗證。

**Files:**
- Modify: `docs/index.html`（`renderWeekly()`，約 2690-2724 行）

- [ ] **Step 1：在 renderWeekly 解構 narrative 並插入敘事卡**

把現有 `const {meta,changes,buys}=WEEKLY_DATA;` 改為：

```javascript
  const {meta,changes,buys,narrative}=WEEKLY_DATA;
```

把原本從 `let html=\`` 起、一路到開啟 `<div class="grid-2">` 結束的整段（含頂部數字卡 grid 與那行 `<div class="grid-2">`）整段取代為——先宣告 `let html=''`、依條件補敘事卡、再接數字卡 grid，**並移除 `<div class="grid-2">`**（新版三層改為垂直滿版堆疊，不再並排）：

```javascript
  let html='';
  if(narrative && narrative.trim()){
    html+=`<div class="card" style="margin-bottom:14px;border-left:3px solid var(--accent,#4a9eff)">
      <div class="card-title">📋 本週敘事</div>
      <div style="white-space:pre-wrap;line-height:1.7;font-size:13px;margin-top:6px">${escHtml(narrative)}</div>
    </div>`;
  }
  html+=`
  <div class="grid-4" style="grid-template-columns:repeat(2,1fr)">
    <div class="card"><div class="card-title">週報日期</div><div class="card-value" style="font-size:18px">${meta.date||'—'}</div></div>
    <div class="card"><div class="card-title">涵蓋交易日</div><div class="card-value">${meta.days||'—'} 日</div></div>
  </div>`;
```

- [ ] **Step 2：瀏覽器驗證**

在瀏覽器開 `docs/index.html` → 週報分頁。
Expected：若 `weekly.json` 的 narrative 非空，頂部出現「📋 本週敘事」卡且兩段文字換行正常；把 weekly.json 的 narrative 手動改成 `""` 重整，敘事卡消失、其餘正常。

- [ ] **Step 3：commit**

```bash
git add docs/index.html
git commit -m "feat: 週報第一層 — Gemini 敘事卡（空則隱藏）"
```

---

## Task 7：前端 — 第二層族群輪動象限 + vs上週 + 趨勢圖

把原「族群動能變化」表升級為含象限標籤、水位、vs上週箭頭的表，並在下方顯示趨勢圖。

**Files:**
- Modify: `docs/index.html`（`renderWeekly()`）

- [ ] **Step 1：加入象限分類 helper**

在 `renderWeekly()` 函式上方（`// ── 週報` 註解之後）加入：

```javascript
function sectorQuadrant(level, change){
  const L=Number(level)||0, C=Number(change)||0;
  if(L>=0 && C>=0) return {label:'領漲',sub:'高檔續強',color:'var(--green)'};
  if(L<0  && C>=0) return {label:'接棒',sub:'落底翻揚',color:'#4a9eff'};
  if(L>=0 && C<0)  return {label:'退燒',sub:'高檔回落',color:'var(--yellow)'};
  return {label:'落後',sub:'弱勢加速',color:'var(--red)'};
}
```

- [ ] **Step 2：改寫族群動能變化表（滿版）**

把現有「族群動能變化」表整段（從 `html+=\`<div><div class="section-title">族群動能變化</div>...\`` 起、含 `const maxC=...`、`changes.forEach(...)`，到 `html+=\`</table></div></div>\`;` 為止）整段取代為下列滿版區塊（不再用外層欄位 `<div>` 包覆，`maxC`/bar 計算一併移除）：

```javascript
  html+=`<div class="section-title">族群輪動（水位 × 本週動能）</div><div class="card"><table>
    <tr><th>族群</th><th>狀態</th><th>水位</th><th>本週變化</th><th>vs 上週</th></tr>`;
  changes.forEach(c=>{
    const v=Number(c.change)||0, lvl=c.level, q=sectorQuadrant(lvl,v);
    let vsLast='—';
    if(c.prev_change!=null){
      const d=v-Number(c.prev_change);
      vsLast = d>0 ? `<span class="up">↑ 加速</span>` : d<0 ? `<span class="dn">↓ 減速</span>` : '持平';
    }
    html+=`<tr>
      <td>${c.sector}</td>
      <td><span class="badge" style="background:${q.color};color:#111">${q.label}</span> <span style="color:var(--sub);font-size:11px">${q.sub}</span></td>
      <td class="${signColor(lvl)}">${lvl!=null?sign(lvl)+fmt(lvl,2):'—'}</td>
      <td class="${signColor(v)}">${sign(v)}${fmt(v,2)} pp</td>
      <td>${vsLast}</td>
    </tr>`;
  });
  html+=`</table>
    <img src="./weekly_sector_trend.png?v=${Date.now()}" alt="族群趨勢" style="width:100%;margin-top:10px;border-radius:6px" onerror="this.style.display='none'">
  </div>`;
```

- [ ] **Step 3：瀏覽器驗證**

開 `docs/index.html` → 週報分頁。
Expected：族群表每列顯示「狀態（領漲/接棒/退燒/落後 帶色塊）＋水位＋本週變化 pp＋vs上週箭頭」；若有上週資料顯示 ↑加速/↓減速，無則「—」；表下方顯示 `weekly_sector_trend.png` 趨勢圖（檔案不存在時自動隱藏，不出現破圖）。

- [ ] **Step 4：commit**

```bash
git add docs/index.html
git commit -m "feat: 週報第二層 — 族群輪動象限 + vs上週 + 趨勢圖"
```

---

## Task 8：前端 — 第三層行動清單（join DAILY_DATA + 新進/退燒）

把「本週累計 BUY Top 10」表升級為 action-card：join 今日狀態，並加「本週新進 vs 退燒」。

**Files:**
- Modify: `docs/index.html`（`renderWeekly()`）

- [ ] **Step 1：建 daily 查表 + 改寫 BUY 區塊為 action-card**

把現有「本週累計 BUY Top 10」表整段（從 `html+=\`<div><div class="section-title">本週累計 BUY Top 10</div>...\`` 起、含 `buys.forEach(...)`，到結尾 `html+=\`</table></div></div></div>\`;` 為止；注意原結尾的最後一個 `</div>` 是關閉 Task 6 已移除的 `grid-2`，因此**不要保留**）整段取代為：

```javascript
  // 第三層 · 行動清單：週報 buys join 今日 daily 狀態
  const dailyById={};
  (DAILY_DATA && DAILY_DATA.stocks ? DAILY_DATA.stocks : []).forEach(s=>{dailyById[String(s.id)]=s;});
  const quadBySector={};
  changes.forEach(c=>{quadBySector[c.sector]=sectorQuadrant(c.level,c.change);});
  html+=`<div class="section-title">本週行動清單（BUY 累計 × 今日狀態）</div>
    <div style="font-size:11px;color:var(--sub);margin:-6px 0 10px;">連續入選次數越高代表本週持續強勢；「今日」欄位顯示最新掃描訊號是否仍 BUY</div>
    <div class="action-grid">`;
  buys.forEach(b=>{
    const id=String(b.stock).split(' ')[0];
    const nm=String(b.stock).split(' ').slice(1).join(' ');
    const d=dailyById[id]||{};
    const sig=d.signal||'—';
    const sigColor=sig==='BUY'?'var(--green)':sig==='SELL'?'var(--red)':'var(--yellow)';
    const sigText=sig==='BUY'?'今日仍 BUY':sig==='SELL'?'今日轉 SELL':sig==='HOLD'?'今日轉 HOLD':'今日無資料';
    const rsi=Number(d.rsi||0), chipN=Number(d.chipTotal||0);
    const q=d.sector?quadBySector[d.sector]:null;
    html+=`<div class="action-card" style="border-left:3px solid ${sigColor}">
      <div class="ac-head">
        <span class="ac-id" style="cursor:pointer" onclick="goToStock('${escHtml(id)}')">${escHtml(id)}</span>
        <span class="ac-name" style="cursor:pointer;text-decoration:underline;text-underline-offset:3px" onclick="goToStock('${escHtml(id)}')">${escHtml(nm)}</span>
        ${d.sector?`<span class="ac-sector">${escHtml(d.sector)}${q?'・'+q.label:''}</span>`:''}
        <span class="ac-sig" style="color:${sigColor}">${sigText}</span>
      </div>
      <div class="ac-prices">
        <div class="ac-price-item"><span class="ac-price-lbl">本週入選</span><span class="ac-price-val" style="font-weight:600">${b.days} 次</span></div>
        <div class="ac-price-item"><span class="ac-price-lbl">現價</span><span class="ac-price-val">${d.price!=null?fmt(d.price,1):'—'}</span></div>
        <div class="ac-price-item"><span class="ac-price-lbl">RSI5</span><span class="ac-price-val" style="font-weight:600">${d.rsi!=null?`<span class="rsi-dot ${rsiDot(rsi)}"></span>`+fmt(rsi,1):'—'}</span></div>
        <div class="ac-price-item"><span class="ac-price-lbl">法人合計</span><span class="ac-price-val ${chipN>=0?'chip-pos':'chip-neg'}" style="font-weight:600">${d.chipTotal!=null?sign(chipN)+chipN.toLocaleString():'—'}</span></div>
      </div>
    </div>`;
  });
  html+=`</div>`;
```

- [ ] **Step 2：加「本週新進 vs 退燒」區塊**

緊接 Step 1 的 `html+=\`</div>\`;` 之後加入：

```javascript
  // 新進 vs 退燒
  const flips=(DAILY_DATA && DAILY_DATA.signalFlips)?DAILY_DATA.signalFlips:[];
  if(flips.length){
    html+=`<div class="section-title" style="color:var(--yellow)">🔄 本週退燒觀察（${flips.length} 檔）</div>
      <div style="font-size:11px;color:var(--sub);margin:-6px 0 10px;">近期曾入選、今日掉出清單 — 持有者注意，詳見每日分頁</div>
      <div class="action-grid">`;
    flips.forEach(f=>{
      const bc=f.status==='回檔測試'?'var(--yellow)':'var(--red)';
      html+=`<div class="action-card" style="border-left:3px solid ${bc}">
        <div class="ac-head">
          <span class="ac-id" style="cursor:pointer" onclick="goToStock('${escHtml(f.id)}')">${escHtml(f.id)}</span>
          <span class="ac-name" style="cursor:pointer" onclick="goToStock('${escHtml(f.id)}')">${escHtml(f.name)}</span>
          <span class="ac-sector">${escHtml(f.sector||'')}</span>
          <span class="ac-sig" style="color:${bc}">${escHtml(f.status)}</span>
        </div>
        <div class="ac-prices">
          <div class="ac-price-item"><span class="ac-price-lbl">現價</span><span class="ac-price-val">${fmt(f.price,1)}</span></div>
          <div class="ac-price-item"><span class="ac-price-lbl">距訊號</span><span class="ac-price-val ${Number(f.chg_pct)>=0?'up':'dn'}">${f.chg_pct!=null?sign(Number(f.chg_pct))+f.chg_pct+'%':'—'}</span></div>
        </div>
      </div>`;
    });
    html+=`</div>`;
  }
```

註：原本 `renderWeekly` 結尾的 `html+=\`...</div></div></div>\`;`（關閉 grid-2 的那行）已被 Step 1 取代，確認最終 `el.innerHTML=html;` 前的 html 標籤是平衡的（grid-2 在 Step 1 開頭已關閉）。

- [ ] **Step 3：瀏覽器驗證**

開 `docs/index.html` → 週報分頁。
Expected：
- 行動清單以 action-card 呈現，每張顯示本週入選次數、今日 signal 標記（仍 BUY 綠／轉 HOLD 黃／轉 SELL 紅）、現價、RSI（含過熱點）、法人合計、所屬族群＋象限。
- 點 id/名稱可跳到個股分析（`goToStock`）。
- 若有 signalFlips，底部出現「本週退燒觀察」卡。
- 整頁無破版、console 無 error。

- [ ] **Step 4：commit**

```bash
git add docs/index.html
git commit -m "feat: 週報第三層 — 行動清單 join 今日狀態 + 退燒觀察"
```

---

## 收尾驗證（全部任務完成後）

- [ ] 跑後端測試：`python -m pytest tests/test_weekly_summary.py -v` → 全 PASS（10）。
- [ ] 實跑：`python scripts/weekly_summary.py` → 無中斷，`docs/weekly.json` 與 `docs/weekly_sector_trend.png` 更新。
- [ ] 瀏覽器開 `docs/index.html` 週報分頁，三層皆正確渲染；故意把 weekly.json 的 narrative 設空，敘事卡隱藏而其餘正常。
- [ ] 確認 daily 分頁同一檔股票的 signal 與週報行動清單一致。
