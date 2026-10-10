"""0050 同期績效基準：每筆回測交易對照「同一持有區間改買 0050」的報酬。

回測標準之一：策略須勝過 0050 同期（excess_return > 0）。逐筆對照而非拿整段
買進持有報酬比，因為策略平均報酬是「每筆持有 N 天」的報酬，與「整段期間持有」
的報酬不同尺度，直接比較會失真。

定義：
  同期報酬 = 0050 出場日收盤 / 0050 進場日開盤 − 1（百分比）
  超額報酬 = 策略該筆報酬 − 同期報酬（百分點），取已結算交易平均
  - 進出場日與策略交易相同。策略以開盤出場者（追蹤停損、均線出場）0050 仍取
    出場日收盤，差半個交易日，ETF 單日波動小，影響可忽略
  - 兩邊皆為價格報酬、未含股利（個股回測亦未還原權息），維持同一基礎
  - 0050 停牌日（如 2025-06-11~17 分割停止買賣）無 K 棒：進場取之後第一個交易日
    開盤、出場取之前最後一個交易日收盤；整段落在停牌期間則無法對照，回 None
"""
from bisect import bisect_left, bisect_right

BENCH_ID = '0050'

# 台股漲跌幅限制 10%：開盤價較前一日收盤跌逾三分之一，只可能是股票分割
SPLIT_GAP = 1.5
PRICE_KEYS = ('open', 'high', 'low', 'close')


def _tradable(bar: dict) -> bool:
    """開收盤皆為正數（停牌日 0 值、NaN 皆排除；NaN > 0 為 False）。"""
    return (bar.get('open') or 0) > 0 and (bar.get('close') or 0) > 0


def adjust_splits(bars: dict[str, dict]) -> dict[str, dict]:
    """還原股票分割，回傳新 dict（不改原資料）。

    bars：{YYYYMMDD: {'open','close',...}}，FinMind 日 K 為未還原價，
    0050 於 2025-06 一拆四，不還原會在分割日出現 −75% 的假跌幅。
    分割日之前的價格除以分割比例（取最接近的整數）；成交量等非價格欄位不動。
    跳空只在有效 K 棒之間比對，停牌日的 0 值不會讓分割漏判。
    """
    days = sorted(bars)
    valid = [d for d in days if _tradable(bars[d])]
    splits = {d: round(bars[p]['close'] / bars[d]['open'])
              for p, d in zip(valid, valid[1:])
              if bars[p]['close'] / bars[d]['open'] >= SPLIT_GAP}
    out: dict[str, dict] = {}
    factor = 1.0
    for d in reversed(days):   # 由新到舊，跨過分割日後更早的價格除以比例
        out[d] = {k: (v / factor if k in PRICE_KEYS else v) for k, v in bars[d].items()}
        factor *= splits.get(d, 1)
    return out


class Benchmark:
    """0050 已還原分割的日 K，提供同期報酬查詢。"""

    def __init__(self, bars: dict[str, dict]):
        self.bars = adjust_splits(bars)
        self.days = sorted(d for d, v in self.bars.items() if _tradable(v))

    def _open_on_or_after(self, d: str) -> tuple[str, float] | None:
        i = bisect_left(self.days, d)
        if i == len(self.days):
            return None
        return self.days[i], self.bars[self.days[i]]['open']

    def _close_on_or_before(self, d: str) -> tuple[str, float] | None:
        i = bisect_right(self.days, d)
        if i == 0:
            return None
        return self.days[i - 1], self.bars[self.days[i - 1]]['close']

    def ret_pct(self, entry_date: str, exit_date: str) -> float | None:
        """entry_date 開盤買進、exit_date 收盤賣出 0050 的報酬（%）；無法對照回 None。"""
        if not entry_date or not exit_date:
            return None
        a = self._open_on_or_after(entry_date)
        b = self._close_on_or_before(exit_date)
        if a is None or b is None or a[0] > b[0]:
            return None
        return round((b[1] / a[1] - 1) * 100, 2)

    def period(self, start: str, end: str) -> dict | None:
        """整段買進持有（start 後首日開盤 → end 前末日收盤），供頁面標示大盤背景。"""
        a = self._open_on_or_after(start)
        b = self._close_on_or_before(end)
        if a is None or b is None or a[0] > b[0]:
            return None
        return {'id': BENCH_ID, 'start': a[0], 'end': b[0],
                'return_pct': round((b[1] / a[1] - 1) * 100, 2)}


def excess_stats(pairs: list[tuple[float, float | None]]) -> dict:
    """pairs：[(策略報酬%, 0050 同期報酬% 或 None)]，None 者略過不計。

    回傳 {'bench_n', 'bench_avg_return', 'excess_return', 'beat_rate'}；無可對照者回空 dict。
    """
    valid = [(s, b) for s, b in pairs if s is not None and b is not None]
    if not valid:
        return {}
    n = len(valid)
    return {
        'bench_n': n,
        'bench_avg_return': round(sum(b for _, b in valid) / n, 2),
        'excess_return': round(sum(s - b for s, b in valid) / n, 2),
        'beat_rate': round(sum(1 for s, b in valid if s > b) / n, 4),
    }
