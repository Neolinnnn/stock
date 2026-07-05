# -*- coding: utf-8 -*-
"""
自我驗證迴圈：取代「每次改邏輯就用 prompt 問 Claude 對不對」的作法。
把三段核心策略邏輯的預期行為寫成案例表，跑一次就知道現在的程式碼是否仍符合預期：

  1. position_tracker.passes_gate  進場閘門
  2. batch_scan.analyze_stock      「過期 BUY」防護（見 CLAUDE 記憶 project-gotchas #3）
  3. position_tracker._step_holding  HYBRID 三段式出場

用法：
    python scripts/verify_logic.py

新增驗證對象時，比照現有區塊的格式加一組案例即可，不需要改跑法本身。
"""
import sys
import types
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from position_tracker import passes_gate, _step_holding
from batch_scan import analyze_stock

ALL_FAILURES = []


def check(section, name, actual, expected):
    ok = actual == expected
    print(f"[{'PASS' if ok else 'FAIL'}] ({section}) {name} -> got={actual} expected={expected}")
    if not ok:
        ALL_FAILURES.append(f"{section}: {name}")


# ── 1. passes_gate 進場閘門 ──────────────────────────────────────────────────

GATE_CASES = [
    ("大盤空頭 → 直接擋掉",
     dict(stock={"signal": "BUY", "price": 100, "ma5": 99, "ma20": 98, "ma60": 97},
          taiex_bull=False), False),
    ("訊號非 BUY → 擋掉",
     dict(stock={"signal": "HOLD", "price": 100, "ma5": 99, "ma20": 98, "ma60": 97},
          taiex_bull=True), False),
    ("多頭排列缺 ma60 → 擋掉（防 None 比較炸掉）",
     dict(stock={"signal": "BUY", "price": 100, "ma5": 99, "ma20": 98, "ma60": None},
          taiex_bull=True), False),
    ("多頭排列不成立（MA20 > MA5）→ 擋掉",
     dict(stock={"signal": "BUY", "price": 100, "ma5": 95, "ma20": 98, "ma60": 90},
          taiex_bull=True), False),
    ("全部條件成立 → 通過",
     dict(stock={"signal": "BUY", "price": 100, "ma5": 99, "ma20": 98, "ma60": 97},
          taiex_bull=True), True),
    ("族群不強勢且要求強勢 → 擋掉",
     dict(stock={"signal": "BUY", "price": 100, "ma5": 99, "ma20": 98, "ma60": 97},
          taiex_bull=True, sector_strong=False), False),
    ("乖離 MA10 超過上限 → 擋掉",
     dict(stock={"signal": "BUY", "price": 105, "ma5": 99, "ma20": 98, "ma60": 97, "ma10": 100},
          taiex_bull=True, max_bias_ma10=2.0), False),
    ("乖離 MA10 在上限內 → 通過",
     dict(stock={"signal": "BUY", "price": 101, "ma5": 99, "ma20": 98, "ma60": 97, "ma10": 100},
          taiex_bull=True, max_bias_ma10=2.0), True),
]

for name, kwargs, expected in GATE_CASES:
    check("passes_gate", name, passes_gate(**kwargs), expected)


# ── 2. analyze_stock 過期 BUY 防護 ────────────────────────────────────────────
# 情境：黃金交叉發生的當下 RSI5 尚未過熱、隔天立刻崩跌又觸發死叉（RSI5 轉超賣，
# SELL 因 rsi_ok_sell 條件被抑制）。修法前會殘留舊 BUY，修法後應降級為 HOLD。

def _build_history(days_flat, decline_days, decline_amt, rise_days, rise_amt, extra):
    """組出「盤整 → 緩跌 → 急拉 → extra」的價格序列，用來精準命中黃金交叉。"""
    base = [100.0] * days_flat
    decline = [round(100 - decline_amt * (i + 1) / decline_days, 2) for i in range(decline_days)]
    rise = [round(decline[-1] + rise_amt * (i + 1) / rise_days, 2) for i in range(rise_days)]
    prices = base + decline + rise + extra
    dates = [datetime.date(2026, 1, 1) + datetime.timedelta(days=i) for i in range(len(prices))]
    return types.SimpleNamespace(price=prices, date=dates), len(prices)


# 黃金交叉發生在「急拉後第一天」（MA 落後於價格），緊接著隔天崩跌即死叉
hist_expired_buy, n1 = _build_history(30, 8, 6.54, 5, 6.95, extra=[97.32, 90.74])
result_expired = analyze_stock('T_EXPIRED', 'expired', days=n1, hist=hist_expired_buy)
check("analyze_stock", "死叉後應降級為 HOLD（防過期 BUY）", result_expired.get('signal'), 'HOLD')

# 對照組：停在黃金交叉當下、不加入後續崩跌 → 結構仍多頭，應維持 BUY
hist_still_buy, n2 = _build_history(30, 8, 6.54, 5, 6.95, extra=[97.32])
result_still_buy = analyze_stock('T_STILL_BUY', 'still_buy', days=n2, hist=hist_still_buy)
check("analyze_stock", "無死叉時黃金交叉應維持 BUY（對照組）", result_still_buy.get('signal'), 'BUY')


# ── 3. HYBRID 三段式出場（_step_holding）────────────────────────────────────

def new_pos(entry=100.0):
    return {'phase': 1, 'entry_price': entry}

# Phase 1 停損 -15%
pos = new_pos()
check("HYBRID", "Phase1 觸及 -15% 停損 → SL",
      _step_holding(pos, 84.0, None), {'exit_price': 84.0, 'return_pct': -16.0, 'exit_reason': 'SL'})

# Phase 1 達 +15% → 轉 Phase 2（續抱，回傳 None）
pos = new_pos()
rec = _step_holding(pos, 116.0, None)
check("HYBRID", "Phase1 達 +15% → 進 Phase2 且續抱", (rec, pos['phase']), (None, 2))

# Phase 2 利潤地板 +7%
pos = {'phase': 2, 'entry_price': 100.0, 'high_watermark': 120.0, 'days_since_high': 0}
check("HYBRID", "Phase2 跌破 +7% 地板 → FLOOR",
      _step_holding(pos, 106.0, None), {'exit_price': 106.0, 'return_pct': 6.0, 'exit_reason': 'FLOOR'})

# Phase 2 跌破 MA10
pos = {'phase': 2, 'entry_price': 100.0, 'high_watermark': 120.0, 'days_since_high': 0}
check("HYBRID", "Phase2 跌破 MA10 → MA10",
      _step_holding(pos, 112.0, 115.0), {'exit_price': 112.0, 'return_pct': 12.0, 'exit_reason': 'MA10'})

# Phase 2 時間停損：連續 25 日未創新高
pos = {'phase': 2, 'entry_price': 100.0, 'high_watermark': 116.0, 'days_since_high': 0}
timeout_rec = None
for _day in range(25):
    timeout_rec = _step_holding(pos, 110.0, None)
    if timeout_rec:
        break
check("HYBRID", "Phase2 連續 25 日未創新高 → TIME",
      timeout_rec, {'exit_price': 110.0, 'return_pct': 10.0, 'exit_reason': 'TIME'})


# ── 總結 ─────────────────────────────────────────────────────────────────────
total = len(GATE_CASES) + 2 + 6
print(f"\n{total - len(ALL_FAILURES)}/{total} 通過")
if ALL_FAILURES:
    print("失敗項目：")
    for f in ALL_FAILURES:
        print(f"  - {f}")
sys.exit(1 if ALL_FAILURES else 0)
