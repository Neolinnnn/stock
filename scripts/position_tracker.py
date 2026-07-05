# -*- coding: utf-8 -*-
"""
持倉追蹤 + HYBRID 出場（每日掃描呼叫）
========================================
依回測驗證的最終策略（閘門 × HYBRID），管理一份 positions.json 狀態檔。

進場閘門（訊號日須同時成立，由 daily_scan 傳入）：
  1. 大盤多頭：TAIEX 收盤 > MA60
  2. 個股多頭排列：收盤 > MA5 > MA20 > MA60
  3. analyze_stock 訊號為 BUY

HYBRID 三段式出場（每日對持倉檢查一次）：
  Phase 1（進場 → 達 +15%）
    - 停損：收盤 ≤ 進場×0.85
    - 觸發 Phase 2：收盤 ≥ 進場×1.15
  Phase 2（已賺 15%）
    - 跌破 MA10 → 出場
    - 利潤地板：收盤 ≤ 進場×1.07 → 出場
    - 時間停損：進入 Phase 2 後 25 日未創新高 → 出場

狀態機僅兩種狀態：pending_entry（次日開盤進場）/ holding。
本系統為「建議輸出」性質，出場以當日價記錄，實務於次一交易日開盤執行。

回測依據：backtest_final_strategy.py（勝率 63%、avg +8.6%、PF 2.83）
"""
import json
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
POSITIONS_FILE = ROOT / 'daily_reports' / 'positions.json'

# ── HYBRID 參數（與 regime_exit_analysis.py 一致）──────────────────────────────
PROFIT_TRIGGER = 0.15   # 達 +15% 進入 Phase 2
PROFIT_FLOOR   = 0.07   # Phase 2 利潤地板 +7%
PHASE1_SL      = 0.15   # Phase 1 停損 -15%
PHASE2_TIMEOUT = 25     # Phase 2 未創新高的時間停損（交易日）


# ── 進場閘門 ──────────────────────────────────────────────────────────────────

def passes_gate(stock: dict, taiex_bull: bool, *,
                sector_strong: bool = True,
                max_bias_ma10: float | None = None) -> bool:
    """判斷個股是否通過進場閘門。

    stock 需含：signal, price, ma5, ma20, ma60；檢查乖離時另需 ma10。
    taiex_bull：TAIEX 收盤 > MA60。
    sector_strong：個股所屬族群是否強勢（avg_ret>3）。daily_scan 依
        strong_sectors 傳入；REQUIRE_STRONG_SECTOR=False 時恆傳 True（不過濾）。
    max_bias_ma10：進場乖離 MA10 上限（%）；None 表示不檢查。

    族群強勢 + 乖離兩道閘門依 2025/1~2026/6 回測加入：套在現有閘門上，
    HYBRID 自適應出場勝率 63%→71%、PF 2.56→3.05（代價：訊號數大幅縮減）。
    預設參數不啟用新閘門，維持原行為；由呼叫端帶入啟用。
    """
    if not taiex_bull:
        return False
    if stock.get('signal') != 'BUY':
        return False
    c   = stock.get('price')
    ma5 = stock.get('ma5')
    ma20 = stock.get('ma20')
    ma60 = stock.get('ma60')
    if None in (c, ma5, ma20, ma60):
        return False
    # 多頭排列：收盤 > MA5 > MA20 > MA60
    if not (c > ma5 > ma20 > ma60):
        return False
    # 族群強勢閘門
    if not sector_strong:
        return False
    # 乖離率閘門：進場貼近 MA10，避免追高
    if max_bias_ma10 is not None:
        ma10 = stock.get('ma10')
        if ma10 is None or ma10 == 0:
            return False
        if (c - ma10) / ma10 * 100 > max_bias_ma10:
            return False
    return True


# ── 狀態檔讀寫 ────────────────────────────────────────────────────────────────

def load_positions() -> dict:
    if POSITIONS_FILE.exists():
        try:
            return json.loads(POSITIONS_FILE.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'updated': '', 'open': [], 'closed': []}


def save_positions(state: dict):
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSITIONS_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')


# ── 單一持倉的當日狀態推進 ────────────────────────────────────────────────────

def _step_holding(pos: dict, price: float, ma10: float | None) -> dict | None:
    """對 holding 部位推進一個交易日。
    回傳出場記錄（含 exit_reason/exit_price/return_pct）或 None（續抱）。
    會就地更新 pos 的 phase / high_watermark / days_since_high。
    """
    entry = pos['entry_price']
    trigger_price = entry * (1 + PROFIT_TRIGGER)
    floor_price   = entry * (1 + PROFIT_FLOOR)
    sl_price      = entry * (1 - PHASE1_SL)

    def _close(reason):
        return {
            'exit_price':  round(price, 2),
            'return_pct':  round((price - entry) / entry * 100, 2),
            'exit_reason': reason,
        }

    # ── Phase 1 ──
    if pos['phase'] == 1:
        if price <= sl_price:
            return _close('SL')
        if price >= trigger_price:
            pos['phase'] = 2
            pos['high_watermark'] = price
            pos['days_since_high'] = 0
        return None

    # ── Phase 2 ──
    if price > pos['high_watermark']:
        pos['high_watermark'] = price
        pos['days_since_high'] = 0
    else:
        pos['days_since_high'] = pos.get('days_since_high', 0) + 1

    if price <= floor_price:
        return _close('FLOOR')
    if ma10 is not None and price < ma10:
        return _close('MA10')
    if pos['days_since_high'] >= PHASE2_TIMEOUT:
        return _close('TIME')
    return None


# ── 每日更新主流程 ────────────────────────────────────────────────────────────

def update_positions(today: str, scan_lookup: dict, taiex_bull: bool,
                     gate_buys: list[dict]) -> dict:
    """每日掃描後呼叫，更新持倉狀態並回傳當日動作。

    參數：
      today        當日 YYYYMMDD
      scan_lookup  {stock_id: {price, ma5, ma10, ma20, ma60}}（今日最新）
      taiex_bull   TAIEX 收盤 > MA60
      gate_buys    今日通過閘門的新 BUY 清單 [{id, name, price, ...}]

    回傳 {'new_entries': [...], 'new_exits': [...], 'holding': [...], 'state': state}
    """
    state = load_positions()
    open_positions = state.get('open', [])
    held_ids = {p['id'] for p in open_positions}

    new_entries, new_exits = [], []
    still_open = []

    for pos in open_positions:
        sid = pos['id']
        info = scan_lookup.get(sid)

        # 1) pending_entry：以今日價作為次日開盤進場價，轉 holding
        if pos.get('status') == 'pending_entry':
            if info and info.get('price'):
                pos['entry_date']  = today
                pos['entry_price'] = round(info['price'], 2)
                pos['status']      = 'holding'
                pos['phase']       = 1
                pos['high_watermark'] = round(info['price'], 2)
                pos['days_since_high'] = 0
                new_entries.append({'id': sid, 'name': pos['name'],
                                    'entry_price': pos['entry_price']})
                still_open.append(pos)
            else:
                # 今日無價，保留 pending 等下次
                still_open.append(pos)
            continue

        # 2) holding：推進一個交易日
        if not info or not info.get('price'):
            still_open.append(pos)   # 今日無價，續抱不動
            continue

        exit_rec = _step_holding(pos, float(info['price']), info.get('ma10'))
        if exit_rec:
            closed = {**pos, 'exit_date': today, **exit_rec}
            state.setdefault('closed', []).append(closed)
            new_exits.append({'id': sid, 'name': pos['name'],
                              'exit_price': exit_rec['exit_price'],
                              'return_pct': exit_rec['return_pct'],
                              'exit_reason': exit_rec['exit_reason'],
                              'holding_days': _holding_days(pos.get('entry_date'), today)})
        else:
            still_open.append(pos)

    # 3) 新閘門 BUY → 加入 pending_entry（排除已持倉；同檔跨族群重複入選只計一次）
    for b in gate_buys:
        if b['id'] in held_ids:
            continue
        held_ids.add(b['id'])
        still_open.append({
            'id':     b['id'],
            'name':   b.get('name', b['id']),
            'signal_date':    today,
            'signal_price':   b.get('price'),
            'status':         'pending_entry',
            'phase':          1,
            'entry_date':     None,
            'entry_price':    None,
            'high_watermark': None,
            'days_since_high': 0,
        })
        new_entries.append({'id': b['id'], 'name': b.get('name', b['id']),
                            'status': 'pending_entry'})

    state['open'] = still_open
    state['updated'] = today
    save_positions(state)

    holding = [p for p in still_open if p.get('status') == 'holding']
    return {'new_entries': new_entries, 'new_exits': new_exits,
            'holding': holding, 'state': state}


def _holding_days(entry_date: str | None, today: str) -> int | None:
    """粗略持有曆日數（非交易日數，僅供顯示）。"""
    if not entry_date:
        return None
    try:
        d0 = datetime.strptime(entry_date, '%Y%m%d')
        d1 = datetime.strptime(today, '%Y%m%d')
        return (d1 - d0).days
    except Exception:
        return None
