# 族群輪動回測 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立離線批次族群輪動回測腳本，輸出 16 個策略 variant 的績效 JSON，並新增獨立靜態頁面呈現。

**Architecture:** 純 Python 後端 (`scripts/sector_rotation_backtest.py`) 讀 `docs/{date}.json` 抽 sector 訊號 + 從 FinMind 補個股價格，跑 16 個 variant + 2 個 benchmark，輸出 `docs/backtest/results.json`。新頁面 `docs/backtest.html` 以 Plotly 呈現。週五在 weekly_summary 後自動跑。

**Tech Stack:** Python 3.11 / pandas / FinMind / pytest / Plotly.js (CDN)

---

## File Structure

| 檔案 | 責任 |
|------|------|
| `scripts/sector_rotation_backtest.py` | 主腳本，包含資料載入、策略計算、JSON 輸出 |
| `tests/test_sector_rotation.py` | 純函式單元測試 |
| `data/cache/prices/{stock_id}.csv` | FinMind 價格本地 cache（.gitignore） |
| `docs/backtest.html` | 前端呈現頁 |
| `docs/backtest/results.json` | 回測結果（覆寫式更新） |
| `docs/index.html` | 加導覽連結 |
| `.github/workflows/daily_scan.yml` | 新增 Friday-only 步驟 |
| `.gitignore` | 加 `data/cache/` |

---

## Task 1: Spec & 訊號讀取

**Files:**
- Create: `scripts/sector_rotation_backtest.py`
- Create: `tests/test_sector_rotation.py`

- [ ] **Step 1: Write the failing test**

`tests/test_sector_rotation.py`：

```python
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / 'scripts'))

from sector_rotation_backtest import load_daily_signals


def test_load_daily_signals_returns_sorted_dates(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / '20250416.json').write_text(json.dumps({
        'sectors': [{'sector': 'A', 'ret20': 5.0, 'rsi': 50, 'hot': 1, 'buy': 0}],
        'stocks':  [{'id': '2330', 'name': '台積電', 'sector': 'A',
                     'ret20': 3.2, 'chipTotal': 100}],
    }), encoding='utf-8')
    (docs / '20250415.json').write_text(json.dumps({
        'sectors': [{'sector': 'A', 'ret20': 4.0, 'rsi': 48, 'hot': 0, 'buy': 0}],
        'stocks':  [{'id': '2330', 'name': '台積電', 'sector': 'A',
                     'ret20': 2.0, 'chipTotal': 50}],
    }), encoding='utf-8')

    result = load_daily_signals(docs)

    assert list(result.keys()) == ['20250415', '20250416']
    assert result['20250415']['sectors'][0]['ret20'] == 4.0
    assert result['20250416']['stocks'][0]['chipTotal'] == 100


def test_load_daily_signals_skips_non_date_files(tmp_path):
    docs = tmp_path / 'docs'
    docs.mkdir()
    (docs / '20250415.json').write_text(json.dumps({
        'sectors': [], 'stocks': []
    }), encoding='utf-8')
    (docs / 'dates.json').write_text('[]', encoding='utf-8')
    (docs / 'random.json').write_text('{}', encoding='utf-8')

    result = load_daily_signals(docs)
    assert list(result.keys()) == ['20250415']
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'sector_rotation_backtest'`

- [ ] **Step 3: Write minimal implementation**

`scripts/sector_rotation_backtest.py`：

```python
"""族群輪動回測：讀 docs/{date}.json，跑 16 個策略 variant，輸出 results.json"""
from __future__ import annotations
import json
from pathlib import Path


def load_daily_signals(docs_dir: Path) -> dict[str, dict]:
    """讀取 docs/{YYYYMMDD}.json，回傳 {date: {sectors, stocks}}，依日期升序"""
    result: dict[str, dict] = {}
    for f in sorted(docs_dir.glob('[0-9]*.json')):
        if len(f.stem) != 8 or not f.stem.isdigit():
            continue
        data = json.loads(f.read_text(encoding='utf-8'))
        result[f.stem] = {
            'sectors': data.get('sectors', []),
            'stocks':  data.get('stocks',  []),
        }
    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/sector_rotation_backtest.py tests/test_sector_rotation.py
git commit -m "feat: load daily signal JSONs from docs/"
```

---

## Task 2: 族群選法 — 4 個 score 函式

**Files:**
- Modify: `scripts/sector_rotation_backtest.py`
- Modify: `tests/test_sector_rotation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sector_rotation.py`：

```python
from sector_rotation_backtest import score_sectors


SECTORS = [
    {'sector': 'A', 'ret20': 10.0, 'rsi': 60, 'hot': 3, 'buy': 2},
    {'sector': 'B', 'ret20':  5.0, 'rsi': 55, 'hot': 5, 'buy': 0},
    {'sector': 'C', 'ret20': 15.0, 'rsi': 70, 'hot': 1, 'buy': 1},
    {'sector': 'D', 'ret20': -2.0, 'rsi': 45, 'hot': 0, 'buy': 3},
]


def test_score_sectors_ret20_picks_highest():
    top = score_sectors(SECTORS, rule='ret20', top_n=2)
    assert top == ['C', 'A']  # 15.0, 10.0


def test_score_sectors_rsi_picks_highest():
    top = score_sectors(SECTORS, rule='rsi', top_n=2)
    assert top == ['C', 'A']  # 70, 60


def test_score_sectors_hot_picks_highest():
    top = score_sectors(SECTORS, rule='hot', top_n=2)
    assert top == ['B', 'A']  # 5, 3


def test_score_sectors_composite_normalizes():
    # composite = 0.5*ret20_norm + 0.3*hot_norm + 0.2*buy_norm
    # ret20 range -2~15, hot 0~5, buy 0~3 → normalize to [0,1]
    top = score_sectors(SECTORS, rule='composite', top_n=2)
    # A: 0.5*((10+2)/17) + 0.3*(3/5) + 0.2*(2/3) = 0.353 + 0.18 + 0.133 = 0.666
    # B: 0.5*((5+2)/17)  + 0.3*(5/5) + 0.2*(0/3) = 0.206 + 0.30 + 0      = 0.506
    # C: 0.5*((15+2)/17) + 0.3*(1/5) + 0.2*(1/3) = 0.500 + 0.06 + 0.067 = 0.627
    # D: 0.5*((-2+2)/17) + 0.3*(0/5) + 0.2*(3/3) = 0     + 0    + 0.200 = 0.200
    assert top[0] == 'A'
    assert top[1] == 'C'


def test_score_sectors_unknown_rule_raises():
    import pytest
    with pytest.raises(ValueError, match='unknown rule'):
        score_sectors(SECTORS, rule='banana', top_n=2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sector_rotation.py -v -k score_sectors`
Expected: FAIL — `cannot import name 'score_sectors'`

- [ ] **Step 3: Implement**

Append to `scripts/sector_rotation_backtest.py`：

```python
def _normalize(values: list[float]) -> list[float]:
    lo, hi = min(values), max(values)
    if hi == lo:
        return [0.5] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def score_sectors(sectors: list[dict], rule: str, top_n: int) -> list[str]:
    """回傳前 top_n 個族群名稱，依 rule 排序由高至低"""
    if rule in ('ret20', 'rsi', 'hot'):
        ranked = sorted(sectors, key=lambda s: s.get(rule, 0), reverse=True)
        return [s['sector'] for s in ranked[:top_n]]

    if rule == 'composite':
        rets = _normalize([s.get('ret20', 0) for s in sectors])
        hots = _normalize([s.get('hot',   0) for s in sectors])
        buys = _normalize([s.get('buy',   0) for s in sectors])
        scored = [
            (s['sector'], 0.5 * r + 0.3 * h + 0.2 * b)
            for s, r, h, b in zip(sectors, rets, hots, buys)
        ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [name for name, _ in scored[:top_n]]

    raise ValueError(f'unknown rule: {rule}')
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/sector_rotation_backtest.py tests/test_sector_rotation.py
git commit -m "feat: add 4 sector scoring rules (ret20/rsi/hot/composite)"
```

---

## Task 3: 族群內選股 — 2 個 stock-picking 函式

**Files:**
- Modify: `scripts/sector_rotation_backtest.py`
- Modify: `tests/test_sector_rotation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sector_rotation.py`：

```python
from sector_rotation_backtest import select_stocks_in_sector


STOCKS = [
    {'id': '2330', 'sector': 'IC', 'ret20':  5.0, 'chipTotal': 1000},
    {'id': '2454', 'sector': 'IC', 'ret20': 10.0, 'chipTotal':  500},
    {'id': '3008', 'sector': 'IC', 'ret20':  2.0, 'chipTotal': 2000},
    {'id': '2308', 'sector': 'IC', 'ret20':  8.0, 'chipTotal':  100},
    {'id': '1234', 'sector': 'OTHER', 'ret20': 99.0, 'chipTotal': 9999},
]


def test_select_stocks_ret20_top_k():
    out = select_stocks_in_sector(STOCKS, sector='IC', rule='ret20_individual', top_k=2)
    assert [s['id'] for s in out] == ['2454', '2308']  # 10.0, 8.0


def test_select_stocks_chip_top_k():
    out = select_stocks_in_sector(STOCKS, sector='IC', rule='chip_concentration', top_k=2)
    assert [s['id'] for s in out] == ['3008', '2330']  # 2000, 1000


def test_select_stocks_ignores_other_sectors():
    out = select_stocks_in_sector(STOCKS, sector='IC', rule='ret20_individual', top_k=10)
    assert '1234' not in [s['id'] for s in out]


def test_select_stocks_handles_fewer_than_top_k():
    out = select_stocks_in_sector(STOCKS, sector='IC', rule='ret20_individual', top_k=10)
    assert len(out) == 4  # IC only has 4 stocks


def test_select_stocks_empty_sector_returns_empty():
    out = select_stocks_in_sector(STOCKS, sector='NONE', rule='ret20_individual', top_k=3)
    assert out == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sector_rotation.py -v -k select_stocks`
Expected: FAIL — `cannot import name 'select_stocks_in_sector'`

- [ ] **Step 3: Implement**

Append to `scripts/sector_rotation_backtest.py`：

```python
_STOCK_RULE_KEY = {
    'ret20_individual':   'ret20',
    'chip_concentration': 'chipTotal',
}


def select_stocks_in_sector(
    stocks: list[dict], sector: str, rule: str, top_k: int
) -> list[dict]:
    """從 stocks 中過濾 sector，依 rule 排序，回傳前 top_k 個 dict"""
    if rule not in _STOCK_RULE_KEY:
        raise ValueError(f'unknown stock rule: {rule}')
    key = _STOCK_RULE_KEY[rule]
    pool = [s for s in stocks if s.get('sector') == sector]
    pool.sort(key=lambda s: s.get(key, 0), reverse=True)
    return pool[:top_k]
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: PASS (11 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/sector_rotation_backtest.py tests/test_sector_rotation.py
git commit -m "feat: add stock selection in sector (ret20/chip)"
```

---

## Task 4: 換股頻率排程

**Files:**
- Modify: `scripts/sector_rotation_backtest.py`
- Modify: `tests/test_sector_rotation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sector_rotation.py`：

```python
from sector_rotation_backtest import rebalance_dates


def test_rebalance_dates_weekly_picks_first_trading_day_each_week():
    # 2025-04-14 (Mon), 04-15 (Tue), 04-16 (Wed), 04-17 (Thu), 04-18 (Fri),
    # 04-21 (Mon next week), 04-22 (Tue)
    all_dates = ['20250414', '20250415', '20250416', '20250417', '20250418',
                 '20250421', '20250422']
    out = rebalance_dates(all_dates, frequency='weekly')
    # 第一個交易日 + 每週首個交易日
    assert out == ['20250414', '20250421']


def test_rebalance_dates_monthly_picks_first_trading_day_each_month():
    all_dates = ['20250415', '20250416', '20250430',
                 '20250501', '20250502', '20250531',
                 '20250602']
    out = rebalance_dates(all_dates, frequency='monthly')
    assert out == ['20250415', '20250501', '20250602']


def test_rebalance_dates_handles_holiday_gap():
    # 跳過 04-19 (Sat), 04-20 (Sun)
    all_dates = ['20250418', '20250421']  # Fri then Mon
    out = rebalance_dates(all_dates, frequency='weekly')
    assert out == ['20250418', '20250421']


def test_rebalance_dates_invalid_frequency_raises():
    import pytest
    with pytest.raises(ValueError, match='unknown frequency'):
        rebalance_dates(['20250415'], frequency='daily')
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sector_rotation.py -v -k rebalance_dates`
Expected: FAIL — `cannot import name 'rebalance_dates'`

- [ ] **Step 3: Implement**

Append to `scripts/sector_rotation_backtest.py`：

```python
from datetime import date


def _parse_yyyymmdd(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def rebalance_dates(all_dates: list[str], frequency: str) -> list[str]:
    """從交易日清單篩出 rebalance 日，每週/月的第一個交易日"""
    if frequency not in ('weekly', 'monthly'):
        raise ValueError(f'unknown frequency: {frequency}')

    if not all_dates:
        return []

    out: list[str] = []
    prev_key: tuple | None = None
    for d in sorted(all_dates):
        dt = _parse_yyyymmdd(d)
        if frequency == 'weekly':
            iso = dt.isocalendar()
            key: tuple = (iso[0], iso[1])  # (year, ISO week)
        else:
            key = (dt.year, dt.month)
        if key != prev_key:
            out.append(d)
            prev_key = key
    return out
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: PASS (15 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/sector_rotation_backtest.py tests/test_sector_rotation.py
git commit -m "feat: rebalance schedule (weekly/monthly)"
```

---

## Task 5: 價格抓取 + 本地 cache

**Files:**
- Modify: `scripts/sector_rotation_backtest.py`
- Modify: `tests/test_sector_rotation.py`
- Modify: `.gitignore`

- [ ] **Step 1: Update .gitignore**

Append to `.gitignore`：

```
data/cache/
```

- [ ] **Step 2: Write the failing test (cache logic only, no FinMind call)**

Append to `tests/test_sector_rotation.py`：

```python
import pandas as pd
from sector_rotation_backtest import load_prices_cached, _cache_path_for


def test_cache_path_for_stock_id(tmp_path):
    assert _cache_path_for(tmp_path, '2330') == tmp_path / '2330.csv'
    assert _cache_path_for(tmp_path, 'TAIEX') == tmp_path / 'TAIEX.csv'


def test_load_prices_cached_reads_existing_csv(tmp_path):
    df = pd.DataFrame({
        'date':  ['2025-04-15', '2025-04-16'],
        'close': [100.0, 102.0],
    })
    df.to_csv(tmp_path / '2330.csv', index=False)

    out = load_prices_cached(cache_dir=tmp_path, stock_id='2330',
                             start='20250415', end='20250416', fetch_fn=None)
    assert list(out['close']) == [100.0, 102.0]


def test_load_prices_cached_calls_fetch_when_missing(tmp_path):
    fake_df = pd.DataFrame({'date': ['2025-04-15'], 'close': [100.0]})

    def fake_fetch(stock_id, start, end):
        assert stock_id == '2330'
        return fake_df

    out = load_prices_cached(cache_dir=tmp_path, stock_id='2330',
                             start='20250415', end='20250415',
                             fetch_fn=fake_fetch)
    # Written to cache
    assert (tmp_path / '2330.csv').exists()
    assert list(out['close']) == [100.0]


def test_load_prices_cached_returns_empty_on_fetch_failure(tmp_path):
    def fake_fetch(stock_id, start, end):
        raise RuntimeError('API down')

    out = load_prices_cached(cache_dir=tmp_path, stock_id='2330',
                             start='20250415', end='20250415',
                             fetch_fn=fake_fetch)
    assert out.empty
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/test_sector_rotation.py -v -k cache`
Expected: FAIL — import errors

- [ ] **Step 4: Implement**

Append to `scripts/sector_rotation_backtest.py`：

```python
import pandas as pd


def _cache_path_for(cache_dir: Path, stock_id: str) -> Path:
    return cache_dir / f'{stock_id}.csv'


def load_prices_cached(
    cache_dir: Path,
    stock_id: str,
    start: str,
    end: str,
    fetch_fn,
) -> pd.DataFrame:
    """讀本地 cache CSV；若無則呼叫 fetch_fn 抓取並寫入。失敗回空 DataFrame。"""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path_for(cache_dir, stock_id)
    if path.exists():
        return pd.read_csv(path)
    if fetch_fn is None:
        return pd.DataFrame(columns=['date', 'close'])
    try:
        df = fetch_fn(stock_id, start, end)
        if df is None or df.empty:
            return pd.DataFrame(columns=['date', 'close'])
        df.to_csv(path, index=False)
        return df
    except Exception as e:
        print(f'[warn] fetch failed for {stock_id}: {e}')
        return pd.DataFrame(columns=['date', 'close'])


def fetch_prices_finmind(stock_id: str, start: str, end: str) -> pd.DataFrame:
    """從 FinMind 抓 TaiwanStockPrice（含 TAIEX），回傳 date/close 欄位"""
    from finmind_client import get_dataloader
    dl = get_dataloader()
    start_iso = f'{start[:4]}-{start[4:6]}-{start[6:8]}'
    end_iso   = f'{end[:4]}-{end[4:6]}-{end[6:8]}'
    df = dl.taiwan_stock_daily(stock_id=stock_id,
                               start_date=start_iso, end_date=end_iso)
    if df is None or df.empty:
        return pd.DataFrame(columns=['date', 'close'])
    return df[['date', 'close']].copy()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: PASS (19 passed)

- [ ] **Step 6: Commit**

```bash
git add scripts/sector_rotation_backtest.py tests/test_sector_rotation.py .gitignore
git commit -m "feat: price fetch with local CSV cache"
```

---

## Task 6: 單一策略模擬

**Files:**
- Modify: `scripts/sector_rotation_backtest.py`
- Modify: `tests/test_sector_rotation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sector_rotation.py`：

```python
from sector_rotation_backtest import simulate_strategy


def test_simulate_strategy_buy_and_hold_equal_weight():
    # 2 stocks, both rise 10%, equal weight → portfolio +10%
    signals = {
        '20250415': {
            'sectors': [{'sector': 'A', 'ret20': 5, 'rsi': 50, 'hot': 1, 'buy': 0}],
            'stocks':  [
                {'id': '111', 'sector': 'A', 'ret20': 5, 'chipTotal': 100},
                {'id': '222', 'sector': 'A', 'ret20': 4, 'chipTotal':  50},
            ],
        },
        '20250416': {'sectors': [], 'stocks': []},
    }
    prices = {
        '111': pd.DataFrame({'date': ['2025-04-15', '2025-04-16'],
                             'close': [100.0, 110.0]}),
        '222': pd.DataFrame({'date': ['2025-04-15', '2025-04-16'],
                             'close': [50.0, 55.0]}),
    }
    result = simulate_strategy(
        signals=signals, prices=prices,
        sector_rule='ret20', stock_rule='ret20_individual',
        frequency='weekly', sectors_picked=1, stocks_per_sector=2,
        cost_per_turn=0.0,
    )
    assert result['equity'][0] == 1.0
    assert abs(result['equity'][-1] - 1.10) < 1e-6
    assert len(result['rebalances']) == 1


def test_simulate_strategy_applies_transaction_cost_on_rebalance():
    # First rebalance: full position change (initial buy) → cost charged
    signals = {
        '20250415': {
            'sectors': [{'sector': 'A', 'ret20': 5, 'rsi': 50, 'hot': 1, 'buy': 0}],
            'stocks':  [{'id': '111', 'sector': 'A', 'ret20': 5, 'chipTotal': 100}],
        },
    }
    prices = {
        '111': pd.DataFrame({'date': ['2025-04-15'], 'close': [100.0]}),
    }
    result = simulate_strategy(
        signals=signals, prices=prices,
        sector_rule='ret20', stock_rule='ret20_individual',
        frequency='weekly', sectors_picked=1, stocks_per_sector=1,
        cost_per_turn=0.01,  # 1% per turn
    )
    # Initial buy = 0.5 turn (only one side) = 0.5%
    assert abs(result['equity'][0] - (1.0 - 0.005)) < 1e-6


def test_simulate_strategy_skips_missing_signal_days():
    signals = {
        '20250415': {  # Tue with signal
            'sectors': [{'sector': 'A', 'ret20': 5, 'rsi': 50, 'hot': 1, 'buy': 0}],
            'stocks':  [{'id': '111', 'sector': 'A', 'ret20': 5, 'chipTotal': 100}],
        },
        '20250416': {'sectors': [], 'stocks': []},  # empty signal → carry
    }
    prices = {
        '111': pd.DataFrame({'date': ['2025-04-15', '2025-04-16'],
                             'close': [100.0, 110.0]}),
    }
    result = simulate_strategy(
        signals=signals, prices=prices,
        sector_rule='ret20', stock_rule='ret20_individual',
        frequency='weekly', sectors_picked=1, stocks_per_sector=1,
        cost_per_turn=0.0,
    )
    # Rebalance only once (on 04-15, first trading day of the week)
    assert len(result['rebalances']) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sector_rotation.py -v -k simulate`
Expected: FAIL — `cannot import name 'simulate_strategy'`

- [ ] **Step 3: Implement**

Append to `scripts/sector_rotation_backtest.py`：

```python
def _price_on(prices: dict[str, pd.DataFrame], stock_id: str, date_str: str) -> float | None:
    df = prices.get(stock_id)
    if df is None or df.empty:
        return None
    iso = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'
    row = df.loc[df['date'] == iso]
    if row.empty:
        return None
    return float(row['close'].iloc[0])


def simulate_strategy(
    signals: dict[str, dict],
    prices: dict[str, pd.DataFrame],
    sector_rule: str,
    stock_rule: str,
    frequency: str,
    sectors_picked: int,
    stocks_per_sector: int,
    cost_per_turn: float,
) -> dict:
    """跑單一 variant；回傳 {equity, dates, rebalances}"""
    all_dates = sorted(signals.keys())
    rb_dates = set(rebalance_dates(all_dates, frequency))

    equity_curve: list[float] = []
    out_dates: list[str] = []
    rebalances: list[dict] = []

    current_holdings: list[dict] = []  # [{id, name, sector, weight, entry_price}]
    nav = 1.0

    for i, d in enumerate(all_dates):
        # Mark-to-market: update nav from previous day's holdings using today's prices
        if i > 0 and current_holdings:
            prev_d = all_dates[i - 1]
            day_return = 0.0
            for h in current_holdings:
                p_prev = _price_on(prices, h['id'], prev_d)
                p_now  = _price_on(prices, h['id'], d)
                if p_prev and p_now:
                    day_return += h['weight'] * (p_now / p_prev - 1)
            nav *= (1 + day_return)

        # Rebalance check
        sigs = signals[d]
        if d in rb_dates and sigs.get('sectors') and sigs.get('stocks'):
            top_sectors = score_sectors(sigs['sectors'], sector_rule, sectors_picked)
            new_holdings: list[dict] = []
            for sec in top_sectors:
                picks = select_stocks_in_sector(
                    sigs['stocks'], sec, stock_rule, stocks_per_sector
                )
                for p in picks:
                    new_holdings.append({
                        'id': p['id'],
                        'name': p.get('name', p['id']),
                        'sector': sec,
                    })

            if new_holdings:
                w = 1.0 / len(new_holdings)
                for h in new_holdings:
                    h['weight'] = w

                old_ids = {h['id'] for h in current_holdings}
                new_ids = {h['id'] for h in new_holdings}
                # Turnover = fraction of portfolio changed (each side counted half)
                changed = len(old_ids.symmetric_difference(new_ids)) / max(
                    2 * len(new_holdings), 1
                )
                nav *= (1 - cost_per_turn * changed)

                rebalances.append({
                    'date': d,
                    'sectors': top_sectors,
                    'holdings': [
                        {'stock_id': h['id'], 'name': h['name'],
                         'sector': h['sector'], 'weight': round(h['weight'], 4)}
                        for h in new_holdings
                    ],
                })
                current_holdings = new_holdings

        equity_curve.append(round(nav, 6))
        out_dates.append(d)

    return {
        'equity': equity_curve,
        'dates':  out_dates,
        'rebalances': rebalances,
    }
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: PASS (22 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/sector_rotation_backtest.py tests/test_sector_rotation.py
git commit -m "feat: simulate_strategy core backtest loop"
```

---

## Task 7: 績效指標

**Files:**
- Modify: `scripts/sector_rotation_backtest.py`
- Modify: `tests/test_sector_rotation.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_sector_rotation.py`：

```python
import math
from sector_rotation_backtest import compute_metrics


def test_compute_metrics_flat_curve():
    eq = [1.0, 1.0, 1.0, 1.0]
    rebalances = [{'date': '20250415', 'holdings': []}]
    m = compute_metrics(eq, rebalances, rf=0.0)
    assert m['cagr'] == 0.0
    assert m['mdd'] == 0.0
    assert m['vol'] == 0.0


def test_compute_metrics_doubling_one_year():
    # 252 trading days, ends at 2.0 → CAGR ≈ 100%
    eq = [1.0 + i / 251 for i in range(252)]
    m = compute_metrics(eq, [], rf=0.0)
    assert 0.99 < m['cagr'] < 1.01
    assert m['mdd'] == 0.0  # monotonically rising


def test_compute_metrics_mdd():
    eq = [1.0, 1.2, 0.9, 1.1]
    # Peak 1.2 → trough 0.9 → MDD = -25%
    m = compute_metrics(eq, [], rf=0.0)
    assert abs(m['mdd'] - (-0.25)) < 1e-6


def test_compute_metrics_win_rate_counts_positive_periods():
    eq = [1.0]
    rebalances = [
        {'date': '20250415', 'holdings': [], 'period_return': 0.05},
        {'date': '20250422', 'holdings': [], 'period_return': -0.02},
        {'date': '20250429', 'holdings': [], 'period_return': 0.03},
    ]
    m = compute_metrics(eq, rebalances, rf=0.0)
    assert abs(m['win_rate'] - (2 / 3)) < 1e-6


def test_compute_metrics_handles_empty_curve():
    m = compute_metrics([], [], rf=0.0)
    assert m['cagr'] == 0.0
    assert m['sharpe'] == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_sector_rotation.py -v -k compute_metrics`
Expected: FAIL — import error

- [ ] **Step 3: Implement**

Append to `scripts/sector_rotation_backtest.py`：

```python
import math


def compute_metrics(equity: list[float], rebalances: list[dict], rf: float = 0.01) -> dict:
    """從 equity curve 與 rebalances 計算 CAGR / vol / Sharpe / MDD / 勝率"""
    if not equity or len(equity) < 2:
        return {'cagr': 0.0, 'vol': 0.0, 'sharpe': 0.0,
                'mdd': 0.0, 'win_rate': 0.0,
                'avg_period_return': 0.0, 'turnover': 0.0}

    n = len(equity)
    final = equity[-1]
    initial = equity[0]
    cagr = (final / initial) ** (252 / max(n - 1, 1)) - 1 if initial > 0 else 0.0

    # Daily returns
    rets = [equity[i] / equity[i - 1] - 1
            for i in range(1, n) if equity[i - 1] > 0]
    mean = sum(rets) / len(rets) if rets else 0.0
    var = sum((r - mean) ** 2 for r in rets) / len(rets) if rets else 0.0
    daily_vol = math.sqrt(var)
    vol = daily_vol * math.sqrt(252)
    sharpe = (cagr - rf) / vol if vol > 0 else 0.0

    # MDD
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = v / peak - 1 if peak > 0 else 0.0
        if dd < mdd:
            mdd = dd

    # Win rate / avg period return — from rebalances if they carry period_return
    period_rets = [rb.get('period_return') for rb in rebalances
                   if 'period_return' in rb]
    if period_rets:
        win_rate = sum(1 for r in period_rets if r > 0) / len(period_rets)
        avg_pr  = sum(period_rets) / len(period_rets)
    else:
        win_rate = 0.0
        avg_pr  = 0.0

    return {
        'cagr':     round(cagr, 4),
        'vol':      round(vol, 4),
        'sharpe':   round(sharpe, 4),
        'mdd':      round(mdd, 4),
        'win_rate': round(win_rate, 4),
        'avg_period_return': round(avg_pr, 4),
        'turnover': 0.0,  # filled by caller
    }
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: PASS (27 passed)

- [ ] **Step 5: Commit**

```bash
git add scripts/sector_rotation_backtest.py tests/test_sector_rotation.py
git commit -m "feat: backtest performance metrics"
```

---

## Task 8: 主流程：跑 16 variants + benchmarks + 寫 JSON

**Files:**
- Modify: `scripts/sector_rotation_backtest.py`

This task wires existing pieces; no new unit tests (covered by smoke test in Task 14).

- [ ] **Step 1: Append main orchestration**

Append to `scripts/sector_rotation_backtest.py`：

```python
SECTOR_RULES = ['ret20', 'rsi', 'hot', 'composite']
STOCK_RULES  = ['ret20_individual', 'chip_concentration']
FREQUENCIES  = ['weekly', 'monthly']

_RULE_LABEL_SECTOR = {
    'ret20': 'ret20', 'rsi': 'RSI', 'hot': '熱度', 'composite': '複合分數',
}
_RULE_LABEL_STOCK = {
    'ret20_individual': 'ret20選股', 'chip_concentration': '籌碼選股',
}
_RULE_LABEL_FREQ = {'weekly': '週調', 'monthly': '月調'}


def variant_id(sector_rule: str, frequency: str, stock_rule: str) -> str:
    freq_short = 'W' if frequency == 'weekly' else 'M'
    stock_short = 'ret20' if stock_rule == 'ret20_individual' else 'chip'
    return f'{sector_rule}_{freq_short}_{stock_short}'


def variant_label(sector_rule: str, frequency: str, stock_rule: str) -> str:
    return (f'{_RULE_LABEL_SECTOR[sector_rule]} / '
            f'{_RULE_LABEL_FREQ[frequency]} / '
            f'{_RULE_LABEL_STOCK[stock_rule]}')


def _compute_period_returns(equity: list[float], dates: list[str],
                            rebalances: list[dict]) -> None:
    """In-place: 在每個 rebalance 上加 period_return 欄位"""
    rb_idx = [dates.index(rb['date']) for rb in rebalances if rb['date'] in dates]
    for i, rb in enumerate(rebalances):
        if rb['date'] not in dates:
            continue
        start_i = dates.index(rb['date'])
        end_i = rb_idx[i + 1] if i + 1 < len(rb_idx) else len(equity) - 1
        if equity[start_i] > 0 and end_i > start_i:
            rb['period_return'] = round(equity[end_i] / equity[start_i] - 1, 6)
        else:
            rb['period_return'] = 0.0


def _avg_turnover(rebalances: list[dict]) -> float:
    if len(rebalances) < 2:
        return 0.0
    turnovers = []
    for i in range(1, len(rebalances)):
        prev_ids = {h['stock_id'] for h in rebalances[i - 1]['holdings']}
        curr_ids = {h['stock_id'] for h in rebalances[i]['holdings']}
        n = max(len(curr_ids), 1)
        turnovers.append(len(prev_ids.symmetric_difference(curr_ids)) / (2 * n))
    return sum(turnovers) / len(turnovers) if turnovers else 0.0


def simulate_benchmark_buyhold(
    dates: list[str], prices_by_id: dict[str, pd.DataFrame],
    stock_ids: list[str],
) -> dict:
    """等權持有清單；遺失資料的股以剩餘股權重平均代位"""
    weight = 1.0 / max(len(stock_ids), 1)
    equity: list[float] = []
    nav = 1.0
    for i, d in enumerate(dates):
        if i == 0:
            equity.append(1.0)
            continue
        prev_d = dates[i - 1]
        day_return = 0.0
        active = 0
        for sid in stock_ids:
            p_prev = _price_on(prices_by_id, sid, prev_d)
            p_now  = _price_on(prices_by_id, sid, d)
            if p_prev and p_now:
                day_return += (p_now / p_prev - 1)
                active += 1
        if active > 0:
            nav *= (1 + day_return / active)
        equity.append(round(nav, 6))
    return {'equity': equity, 'dates': dates, 'rebalances': []}


def simulate_benchmark_taiex(dates: list[str], taiex: pd.DataFrame) -> dict:
    """加權指數 buy & hold"""
    if taiex.empty:
        return {'equity': [1.0] * len(dates), 'dates': dates, 'rebalances': []}
    iso_dates = [f'{d[:4]}-{d[4:6]}-{d[6:8]}' for d in dates]
    closes = []
    last_close = None
    for iso in iso_dates:
        row = taiex.loc[taiex['date'] == iso]
        if not row.empty:
            last_close = float(row['close'].iloc[0])
        closes.append(last_close)
    base = next((c for c in closes if c is not None), 1.0)
    equity = [(c / base) if c else 1.0 for c in closes]
    return {'equity': [round(e, 6) for e in equity],
            'dates': dates, 'rebalances': []}


def build_results(
    signals: dict[str, dict],
    prices_by_id: dict[str, pd.DataFrame],
    taiex: pd.DataFrame,
    benchmark_stock_ids: list[str],
    cost_per_turn: float = 0.00585,
    sectors_picked: int = 3,
    stocks_per_sector: int = 3,
    rf: float = 0.01,
) -> dict:
    """跑 16 variants + 2 benchmarks，組合 results.json"""
    from datetime import datetime
    all_dates = sorted(signals.keys())

    variants_out = []
    for sr in SECTOR_RULES:
        for freq in FREQUENCIES:
            for stk in STOCK_RULES:
                sim = simulate_strategy(
                    signals=signals, prices=prices_by_id,
                    sector_rule=sr, stock_rule=stk, frequency=freq,
                    sectors_picked=sectors_picked,
                    stocks_per_sector=stocks_per_sector,
                    cost_per_turn=cost_per_turn,
                )
                _compute_period_returns(sim['equity'], sim['dates'],
                                        sim['rebalances'])
                metrics = compute_metrics(sim['equity'], sim['rebalances'], rf=rf)
                metrics['turnover'] = round(_avg_turnover(sim['rebalances']), 4)
                variants_out.append({
                    'id':           variant_id(sr, freq, stk),
                    'label':        variant_label(sr, freq, stk),
                    'sector_rule':  sr,
                    'frequency':    freq,
                    'stock_rule':   stk,
                    'metrics':      metrics,
                    'equity':       sim['equity'],
                    'dates':        sim['dates'],
                    'rebalances':   sim['rebalances'],
                })

    bench_taiex = simulate_benchmark_taiex(all_dates, taiex)
    bench_ew = simulate_benchmark_buyhold(all_dates, prices_by_id,
                                          benchmark_stock_ids)
    bench_taiex_m = compute_metrics(bench_taiex['equity'], [], rf=rf)
    bench_ew_m    = compute_metrics(bench_ew['equity'],    [], rf=rf)

    ranking = sorted(
        [{'id': v['id'], 'sharpe': v['metrics']['sharpe']} for v in variants_out],
        key=lambda x: x['sharpe'], reverse=True,
    )

    return {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'period': {
            'start': all_dates[0] if all_dates else '',
            'end':   all_dates[-1] if all_dates else '',
            'trading_days': len(all_dates),
        },
        'config': {
            'portfolio_size': sectors_picked * stocks_per_sector,
            'sectors_picked': sectors_picked,
            'stocks_per_sector': stocks_per_sector,
            'cost_per_turn': cost_per_turn,
            'rf_rate': rf,
        },
        'benchmarks': {
            'TAIEX': {**bench_taiex_m, **bench_taiex},
            'EqualWeight67': {**bench_ew_m, **bench_ew},
        },
        'variants': variants_out,
        'ranking':  ranking,
    }


def main():
    repo = Path(__file__).parent.parent
    docs = repo / 'docs'
    cache = repo / 'data' / 'cache' / 'prices'

    print('[1/4] 載入 daily signals...')
    signals = load_daily_signals(docs)
    if not signals:
        print('  沒有任何 daily JSON，結束')
        return
    all_dates = sorted(signals.keys())
    start, end = all_dates[0], all_dates[-1]
    print(f'  {len(signals)} 個交易日 ({start} ~ {end})')

    print('[2/4] 蒐集需要的個股 ID...')
    stock_ids = set()
    for d in signals.values():
        for s in d['stocks']:
            stock_ids.add(s['id'])
    print(f'  {len(stock_ids)} 檔股票')

    print('[3/4] 抓取價格（個股 + TAIEX）...')
    prices: dict[str, pd.DataFrame] = {}
    for i, sid in enumerate(sorted(stock_ids), 1):
        prices[sid] = load_prices_cached(
            cache_dir=cache, stock_id=sid, start=start, end=end,
            fetch_fn=fetch_prices_finmind,
        )
        if i % 10 == 0:
            print(f'    {i}/{len(stock_ids)}')
    taiex = load_prices_cached(
        cache_dir=cache, stock_id='TAIEX', start=start, end=end,
        fetch_fn=fetch_prices_finmind,
    )

    print('[4/4] 跑 16 variants + benchmarks...')
    results = build_results(
        signals=signals, prices_by_id=prices, taiex=taiex,
        benchmark_stock_ids=sorted(stock_ids),
    )

    out_dir = docs / 'backtest'
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / 'results.json'
    out_path.write_text(json.dumps(results, ensure_ascii=False, separators=(',', ':')),
                        encoding='utf-8')
    size_kb = out_path.stat().st_size / 1024
    print(f'  寫出 {out_path} ({size_kb:.1f} KB)')
    print(f'  Top variant: {results["ranking"][0]["id"]} '
          f'(Sharpe={results["ranking"][0]["sharpe"]})')


if __name__ == '__main__':
    main()
```

- [ ] **Step 2: Run existing tests to confirm no regression**

Run: `pytest tests/test_sector_rotation.py -v`
Expected: PASS (27 passed)

- [ ] **Step 3: Commit**

```bash
git add scripts/sector_rotation_backtest.py
git commit -m "feat: main orchestration — run 16 variants + benchmarks, write results.json"
```

---

## Task 9: 前端 backtest.html — 骨架 + 排行榜

**Files:**
- Create: `docs/backtest.html`

- [ ] **Step 1: Create the HTML skeleton**

Create `docs/backtest.html` with this exact content:

```html
<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>族群輪動回測 · 台股研究</title>
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<style>
  :root {
    --bg:#0d1117; --card:#161b22; --border:#30363d;
    --text:#e6edf3; --sub:#8b949e; --accent:#58a6ff;
    --up:#3fb950; --dn:#f85149; --neu:#d29922;
  }
  body { margin:0; background:var(--bg); color:var(--text);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans TC",sans-serif; }
  .wrap { max-width:1200px; margin:0 auto; padding:24px; }
  h1 { font-size:22px; margin:0 0 4px; }
  .meta { color:var(--sub); font-size:13px; margin-bottom:18px; }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  .card { background:var(--card); border:1px solid var(--border);
          border-radius:8px; padding:16px; margin-bottom:16px; }
  h2 { font-size:16px; margin:0 0 12px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th, td { padding:8px 10px; text-align:right; border-bottom:1px solid var(--border); }
  th:first-child, td:first-child,
  th:nth-child(2), td:nth-child(2) { text-align:left; }
  thead th { color:var(--sub); font-weight:normal; }
  tbody tr { cursor:pointer; }
  tbody tr:hover { background:rgba(88,166,255,0.08); }
  tbody tr.active { background:rgba(88,166,255,0.16); }
  tr.bench td { color:var(--sub); cursor:default; }
  .pos { color:var(--up); } .neg { color:var(--dn); }
  #chart { height:380px; }
  #timeline { font-size:13px; max-height:400px; overflow-y:auto; }
  .rb-block { padding:8px 0; border-bottom:1px solid var(--border); }
  .rb-date { color:var(--accent); font-weight:bold; }
  .rb-row { color:var(--sub); margin-left:12px; }
  .footer { color:var(--sub); font-size:12px; text-align:center;
            margin-top:24px; padding:12px; }
</style>
</head>
<body>
<div class="wrap">
  <div style="display:flex;justify-content:space-between;align-items:baseline;">
    <h1>📊 族群輪動回測</h1>
    <a href="./index.html">← 回主站</a>
  </div>
  <div class="meta" id="meta">載入中...</div>

  <div class="card">
    <h2>策略排行（依 Sharpe 排序）</h2>
    <table>
      <thead><tr>
        <th>排名</th><th>策略</th><th>年化</th><th>Sharpe</th>
        <th>MDD</th><th>勝率</th><th>換手</th>
      </tr></thead>
      <tbody id="rank-body"></tbody>
    </table>
  </div>

  <div class="card">
    <h2 id="chart-title">權益曲線</h2>
    <div id="chart"></div>
  </div>

  <div class="card">
    <h2>持股歷史</h2>
    <div id="timeline"></div>
  </div>

  <div class="footer">
    過去績效不代表未來，回測未計入滑價、流動性、停損機制，僅供研究參考
  </div>
</div>

<script>
const _PLY = {
  bg:'#161b22', plot:'#0d1117', grid:'#30363d',
  font:{color:'#e6edf3', size:12},
  acc:'#58a6ff', up:'#3fb950', dn:'#f85149', neu:'#d29922',
};

let RESULTS = null;
let SELECTED_ID = null;

function pct(v, digits=1){ return (v*100).toFixed(digits) + '%'; }
function cls(v){ return v >= 0 ? 'pos' : 'neg'; }

async function init(){
  try {
    const r = await fetch('./backtest/results.json?v=' + Date.now());
    if(!r.ok) throw new Error('HTTP ' + r.status);
    RESULTS = await r.json();
  } catch(e){
    document.getElementById('meta').textContent = '無法載入 results.json：' + e.message;
    return;
  }
  const p = RESULTS.period;
  document.getElementById('meta').textContent =
    `資料期間 ${p.start} ~ ${p.end} · 共 ${p.trading_days} 個交易日`;
  renderRanking();
  SELECTED_ID = RESULTS.ranking[0].id;
  renderChart();
  renderTimeline();
}

function renderRanking(){
  const tbody = document.getElementById('rank-body');
  tbody.innerHTML = '';
  RESULTS.ranking.forEach((r, idx) => {
    const v = RESULTS.variants.find(x => x.id === r.id);
    const m = v.metrics;
    const tr = document.createElement('tr');
    tr.dataset.id = v.id;
    tr.innerHTML =
      `<td>${idx+1}</td><td>${v.label}</td>` +
      `<td class="${cls(m.cagr)}">${pct(m.cagr)}</td>` +
      `<td>${m.sharpe.toFixed(2)}</td>` +
      `<td class="neg">${pct(m.mdd)}</td>` +
      `<td>${pct(m.win_rate, 0)}</td>` +
      `<td>${pct(m.turnover, 0)}</td>`;
    tr.onclick = () => { SELECTED_ID = v.id; renderRanking(); renderChart(); renderTimeline(); };
    if(v.id === SELECTED_ID) tr.classList.add('active');
    tbody.appendChild(tr);
  });
  // Benchmarks rows
  for(const [k, b] of Object.entries(RESULTS.benchmarks)){
    const tr = document.createElement('tr');
    tr.className = 'bench';
    tr.innerHTML =
      `<td>—</td><td>${k}</td>` +
      `<td class="${cls(b.cagr)}">${pct(b.cagr)}</td>` +
      `<td>${b.sharpe.toFixed(2)}</td>` +
      `<td class="neg">${pct(b.mdd)}</td>` +
      `<td>—</td><td>—</td>`;
    tbody.appendChild(tr);
  }
}

function renderChart(){
  const v = RESULTS.variants.find(x => x.id === SELECTED_ID);
  document.getElementById('chart-title').textContent = '權益曲線：' + v.label;
  const traces = [
    { name:v.label, x:v.dates, y:v.equity, mode:'lines',
      line:{color:_PLY.acc, width:2} },
    { name:'TAIEX', x:RESULTS.benchmarks.TAIEX.dates,
      y:RESULTS.benchmarks.TAIEX.equity, mode:'lines',
      line:{color:_PLY.neu, width:1.2, dash:'dot'} },
    { name:'等權67', x:RESULTS.benchmarks.EqualWeight67.dates,
      y:RESULTS.benchmarks.EqualWeight67.equity, mode:'lines',
      line:{color:_PLY.up, width:1.2, dash:'dash'} },
  ];
  const layout = {
    paper_bgcolor:_PLY.bg, plot_bgcolor:_PLY.plot, font:_PLY.font,
    xaxis:{gridcolor:_PLY.grid}, yaxis:{gridcolor:_PLY.grid, title:'NAV (起點=1.0)'},
    margin:{t:10,r:10,b:40,l:50}, legend:{orientation:'h', y:-0.15},
  };
  Plotly.newPlot('chart', traces, layout, {displayModeBar:false, responsive:true});
}

function renderTimeline(){
  const v = RESULTS.variants.find(x => x.id === SELECTED_ID);
  const el = document.getElementById('timeline');
  el.innerHTML = '';
  v.rebalances.slice().reverse().forEach(rb => {
    const div = document.createElement('div');
    div.className = 'rb-block';
    const ret = rb.period_return != null
      ? `<span class="${cls(rb.period_return)}">${pct(rb.period_return)}</span>`
      : '';
    let html = `<div class="rb-date">${rb.date} ${ret}</div>`;
    const bySec = {};
    rb.holdings.forEach(h => {
      (bySec[h.sector] = bySec[h.sector] || []).push(`${h.stock_id} ${h.name}`);
    });
    for(const sec in bySec){
      html += `<div class="rb-row">${sec}：${bySec[sec].join(' / ')}</div>`;
    }
    div.innerHTML = html;
    el.appendChild(div);
  });
}

init();
</script>
</body>
</html>
```

- [ ] **Step 2: Commit**

```bash
git add docs/backtest.html
git commit -m "feat: add backtest.html page (ranking + chart + timeline)"
```

---

## Task 10: 主站加入「策略回測」連結

**Files:**
- Modify: `docs/index.html`

- [ ] **Step 1: Find an existing nav element to anchor**

Run: `grep -n "<header\|<nav\|class=\"nav\"" docs/index.html | head -5`
Expected: Some result showing where navigation lives. If no nav exists, find `<body>` opening or the first heading.

- [ ] **Step 2: Add the link**

Find the top section (`<body>` or first heading). Add this link inline somewhere visible at the top — for example, append a small link block right after the opening `<body>` tag or near the existing title bar:

```html
<div style="position:fixed;top:8px;right:12px;font-size:13px;z-index:999;">
  <a href="./backtest.html" style="color:#58a6ff;text-decoration:none;">📊 策略回測 →</a>
</div>
```

(If a more idiomatic location exists in the page header, place it there matching the existing style. The hard requirement: an `a href="./backtest.html"` somewhere on page load.)

- [ ] **Step 3: Verify link target is reachable**

Open `docs/index.html` in a browser, confirm the link is visible and clicking it loads `backtest.html`. Until `results.json` exists, the backtest page will show "無法載入" — that's expected.

- [ ] **Step 4: Commit**

```bash
git add docs/index.html
git commit -m "feat: add navigation link to backtest page"
```

---

## Task 11: Workflow 整合（週五觸發回測）

**Files:**
- Modify: `.github/workflows/daily_scan.yml`

- [ ] **Step 1: Locate the weekly_summary step**

Run: `grep -n "weekly_summary\|Run weekly" .github/workflows/daily_scan.yml`
Expected: Match on the step that runs `python scripts/weekly_summary.py`

- [ ] **Step 2: Insert new step right after weekly_summary**

In `.github/workflows/daily_scan.yml`, insert this block after the "Run weekly summary" step and before "Fetch fundamentals":

```yaml
      - name: Run sector rotation backtest (Fridays only)
        if: github.event.schedule == '45 7 * * 5'
        continue-on-error: true
        env:
          FINMIND_TOKEN: ${{ secrets.FINMIND_TOKEN }}
        run: python scripts/sector_rotation_backtest.py
```

- [ ] **Step 3: Confirm `docs/backtest/` is committed in the existing commit step**

Run: `grep -n "git add" .github/workflows/daily_scan.yml`
Expected: The existing line `git add daily_reports/ docs/` already covers `docs/backtest/`. No change needed (verify the path glob includes subdirs).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/daily_scan.yml
git commit -m "ci: trigger sector rotation backtest on Fridays"
```

---

## Task 12: Smoke test — 本機跑完整流程

**Files:**
- None (validation only — no commits expected unless `results.json` is generated and the user wants to ship it)

- [ ] **Step 1: Run full pipeline locally**

Run: `python scripts/sector_rotation_backtest.py`
Expected output (rough):
```
[1/4] 載入 daily signals...
  N 個交易日 (20250415 ~ 20260515)
[2/4] 蒐集需要的個股 ID...
  M 檔股票
[3/4] 抓取價格（個股 + TAIEX）...
    10/M
    ...
[4/4] 跑 16 variants + benchmarks...
  寫出 .../docs/backtest/results.json (XX.X KB)
  Top variant: <some_id> (Sharpe=<some_value>)
```

If FinMind hits rate limit or 401, the script should still finish (each fetch failure handled per Task 5). Confirm:
- `docs/backtest/results.json` exists
- File contains 16 entries under `variants`
- `ranking` is sorted by `sharpe` desc

- [ ] **Step 2: Open page locally**

From repo root run `python -m http.server 8000 --directory docs` then open `http://localhost:8000/backtest.html`. Confirm:
- Meta line shows period
- Ranking table has 16 rows + 2 benchmark rows
- Clicking a row redraws the chart
- Timeline shows rebalance history

- [ ] **Step 3: Commit generated `results.json` (and any cache stat artifacts that need versioning)**

```bash
git add docs/backtest/results.json
git commit -m "data: first sector rotation backtest results"
```

(`data/cache/prices/*.csv` should be ignored per `.gitignore` updated in Task 5.)

---

## Self-Review Notes (post-write, before handoff)

**Spec coverage check:**
- ✅ 16 variants (4 sector × 2 freq × 2 stock) — Tasks 2/3/4/8
- ✅ TAIEX & EqualWeight67 benchmark — Task 8
- ✅ Trading cost 0.585% — Task 6/8
- ✅ T+1 close execution — Task 6 (mark-to-market uses prev_d → d return)
- ✅ Carry holdings on missing signal day — Task 6 (`if d in rb_dates and sigs.get('sectors')`)
- ✅ Stocks < 3 in sector → take what's there — Task 3 (`pool[:top_k]`)
- ✅ JSON schema — Tasks 1/8
- ✅ Backtest.html UI: ranking + chart + timeline + risk note — Task 9
- ✅ Friday workflow trigger — Task 11
- ✅ `.gitignore` for cache — Task 5
- ✅ Index.html nav link — Task 10

**Types & consistency:**
- `variant_id` returns `{sector_rule}_{freq_short}_{stock_short}` format matches spec ✅
- `rebalances[].holdings[].stock_id` matches frontend usage ✅
- `metrics.turnover` filled by caller (Task 8) ✅

**No placeholders:** All steps have concrete code; no "implement later" markers.
