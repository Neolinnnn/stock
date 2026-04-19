"""
單股深度分析：技術面 + 基本面 + 籌碼面 綜合驗證
Single Stock Deep Dive Analysis

用法：
    python strategy_templates/05_single_stock_deep_dive.py 4906
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

_here = os.path.dirname(__file__)
_main = os.path.join(_here, '03_batch_scan_with_cv.py')
with open(_main, encoding='utf-8') as f:
    code = f.read()
code = code.split("if __name__ == '__main__':")[0]
exec(code, globals())

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from twstock import Stock


def deep_dive(stock_id: str):
    print(f"\n{'='*70}")
    print(f"  單股深度分析  |  {stock_id}  |  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"{'='*70}\n")

    # ── 取 2 年資料 ────────────────────────────────────────────────────
    s = Stock(stock_id)
    start = datetime.now() - timedelta(days=800)
    s.fetch_from(start.year, start.month)

    prices = list(s.price)
    dates  = list(s.date)
    highs  = list(s.high)
    lows   = list(s.low)
    vols   = list(s.capacity)

    if len(prices) < 60:
        print(f"資料不足 ({len(prices)} 天)")
        return

    print(f"  📊 資料期間：{dates[0]:%Y-%m-%d} ~ {dates[-1]:%Y-%m-%d}  共 {len(prices)} 天\n")

    # ── 技術面 ──────────────────────────────────────────────────────────
    ma5   = sma(prices, 5)
    ma20  = sma(prices, 20)
    ma60  = sma(prices, 60)
    ma120 = sma(prices, 120)
    rsi_v = calc_rsi(prices, 14)

    cur    = prices[-1]
    hi_52w = max(prices[-250:]) if len(prices) >= 250 else max(prices)
    lo_52w = min(prices[-250:]) if len(prices) >= 250 else min(prices)
    pos    = (cur - lo_52w) / (hi_52w - lo_52w) * 100

    print(f"  【技術面】")
    print(f"  ─" * 34)
    print(f"  現價：        {cur:>8.2f}")
    print(f"  MA5 (週):     {ma5[-1]:>8.2f}   {'↑' if cur > ma5[-1] else '↓'}")
    print(f"  MA20 (月):    {ma20[-1]:>8.2f}   {'↑' if cur > ma20[-1] else '↓'}")
    print(f"  MA60 (季):    {ma60[-1]:>8.2f}   {'↑' if cur > ma60[-1] else '↓'}")
    print(f"  MA120 (半年): {ma120[-1]:>8.2f}   {'↑' if cur > ma120[-1] else '↓'}")
    print(f"  RSI(14):      {rsi_v[-1]:>8.1f}   "
          f"{'過熱' if rsi_v[-1] > 70 else ('超賣' if rsi_v[-1] < 30 else '中性')}")
    print(f"  52週高:       {hi_52w:>8.2f}")
    print(f"  52週低:       {lo_52w:>8.2f}")
    print(f"  位階:         {pos:>7.1f}%  (0=底部, 100=頂部)")

    # 均線多空排列
    mas = [ma5[-1], ma20[-1], ma60[-1], ma120[-1]]
    if mas == sorted(mas, reverse=True):
        arr = '多頭排列 ✅'
    elif mas == sorted(mas):
        arr = '空頭排列 ❌'
    else:
        arr = '糾結盤整 ⚠️'
    print(f"  均線結構:     {arr}")

    # 近期漲跌幅
    def pct(n):
        if len(prices) > n:
            return (prices[-1] - prices[-n-1]) / prices[-n-1] * 100
        return None
    print(f"  近5日 / 20日 / 60日 / 120日: "
          f"{pct(5):+.1f}% / {pct(20):+.1f}% / {pct(60):+.1f}% / {pct(120):+.1f}%")

    # ── 波動率 ──────────────────────────────────────────────────────────
    rets = np.diff(prices[-60:]) / np.array(prices[-60:-1])
    vol_ann = rets.std() * np.sqrt(252) * 100
    print(f"  年化波動率:   {vol_ann:>7.1f}%")

    # ── 成交量分析 ──────────────────────────────────────────────────────
    vol_ma20 = np.mean(vols[-20:])
    vol_ratio = vols[-1] / vol_ma20 if vol_ma20 else 0
    print(f"\n  【量能】")
    print(f"  ─" * 34)
    print(f"  今日量:       {vols[-1]/1000:>8.0f} 張")
    print(f"  20日均量:     {vol_ma20/1000:>8.0f} 張")
    print(f"  量比:         {vol_ratio:>8.2f}   "
          f"{'爆量' if vol_ratio > 2 else ('放量' if vol_ratio > 1.3 else '量縮' if vol_ratio < 0.7 else '正常')}")

    # ── Walk-Forward CV 驗證 ────────────────────────────────────────────
    cv = walk_forward_cv(prices, dates, 3)
    if cv:
        print(f"\n  【策略歷史驗證 (Walk-Forward CV, 3折)】")
        print(f"  ─" * 34)
        for r in cv:
            print(f"  第{r['fold']}折  報酬={r['return']:+.1%}  "
                  f"夏普={r['sharpe']:.2f}  勝率={r['win_rate']:.0%}  "
                  f"交易={r['trades']}次")
        avg_sh = np.mean([r['sharpe']  for r in cv])
        avg_wr = np.mean([r['win_rate'] for r in cv])
        avg_rt = np.mean([r['return']   for r in cv])
        verdict = '可用' if avg_sh >= 0.3 and avg_wr >= 0.4 else '不適用'
        print(f"  平均：    報酬={avg_rt:+.1%}  夏普={avg_sh:.2f}  勝率={avg_wr:.0%}  →  策略{verdict}")

    # ── 買點判斷 ─────────────────────────────────────────────────────────
    print(f"\n  【買點綜合判斷】")
    print(f"  ─" * 34)

    score = 0
    reasons = []

    # 均線結構
    if arr == '多頭排列 ✅':
        score += 2; reasons.append("✅ 均線多頭排列 (+2)")
    elif arr == '空頭排列 ❌':
        score -= 2; reasons.append("❌ 均線空頭排列 (-2)")
    else:
        reasons.append("⚠️ 均線盤整 (0)")

    # 位階
    if pos < 30:
        score += 2; reasons.append(f"✅ 位階低 {pos:.0f}% (+2)")
    elif pos < 60:
        score += 1; reasons.append(f"🟢 位階中偏低 {pos:.0f}% (+1)")
    elif pos > 85:
        score -= 2; reasons.append(f"❌ 位階過高 {pos:.0f}% (-2)")
    else:
        reasons.append(f"🟡 位階中偏高 {pos:.0f}% (0)")

    # RSI
    if rsi_v[-1] < 35:
        score += 2; reasons.append(f"✅ RSI 超賣 {rsi_v[-1]:.0f} (+2)")
    elif 40 <= rsi_v[-1] <= 60:
        score += 1; reasons.append(f"🟢 RSI 健康 {rsi_v[-1]:.0f} (+1)")
    elif rsi_v[-1] > 75:
        score -= 2; reasons.append(f"❌ RSI 極度過熱 {rsi_v[-1]:.0f} (-2)")
    elif rsi_v[-1] > 65:
        score -= 1; reasons.append(f"🟡 RSI 偏高 {rsi_v[-1]:.0f} (-1)")

    # 量能
    if vol_ratio > 1.5 and cur > ma20[-1]:
        score += 1; reasons.append(f"✅ 放量突破均線 (+1)")
    elif vol_ratio < 0.5:
        reasons.append(f"⚠️ 量能萎縮 (0)")

    # 波動率警告
    if vol_ann > 60:
        reasons.append(f"⚠️ 年化波動率 {vol_ann:.0f}% 偏高，留意部位控管")

    print("\n".join("  " + r for r in reasons))
    print(f"\n  綜合分數：{score}")

    if score >= 4:
        verdict = "🟢 強烈買進 — 現在可分批布局"
    elif score >= 2:
        verdict = "🟡 偏多 — 拉回至 MA20 附近可進場"
    elif score >= 0:
        verdict = "⚪ 中性 — 觀望等更好訊號"
    else:
        verdict = "🔴 偏空 — 暫不進場"
    print(f"  結論：{verdict}")

    # ── 具體建議價位 ────────────────────────────────────────────────────
    print(f"\n  【建議價位參考】")
    print(f"  ─" * 34)
    support1 = ma20[-1]
    support2 = ma60[-1]
    entry_hi = cur * 1.00
    entry_lo = min(support1, cur * 0.95)
    stop     = min(support2 * 0.97, cur * 0.93)
    target1  = cur * 1.08
    target2  = cur * 1.15

    print(f"  進場區間:    {entry_lo:.1f} ~ {entry_hi:.1f}")
    print(f"  停損參考:    {stop:.1f}  (MA60 下方或 -7%)")
    print(f"  第一目標:    {target1:.1f}  (+8%)")
    print(f"  第二目標:    {target2:.1f}  (+15%)")
    print(f"  MA20 支撐:   {support1:.1f}")
    print(f"  MA60 支撐:   {support2:.1f}")

    print(f"\n{'='*70}\n")


if __name__ == '__main__':
    sid = sys.argv[1] if len(sys.argv) > 1 else '4906'
    deep_dive(sid)
