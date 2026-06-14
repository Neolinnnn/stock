"""台股價格／籌碼取數的統一資料層。

把原先散落在 daily_scan.py 等檔的「FinMind token 輪替 + 價格快取 +
yfinance 備援」整併於此，作為全專案唯一的取數入口，避免各檔各自重造。

對外 API：
    collect_tokens() / make_dataloader()   — FinMind DataLoader（自動帶 token）
    finmind_fetch(method, **kwargs)         — 呼叫 FinMind 方法，額度滿自動換 token
    load_price_cache(sid) / save_price_cache(...) — price_cache/{sid}.json 讀寫
    yf_history(sid, start, end)             — yfinance 備援（與 FinMind 同欄位）
    get_history(sid, data_days)             — 抓取/增量更新 K 棒，回傳 CachedStock
    CachedStock                              — twstock.Stock 的 duck-type
"""
import os
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

# 快取目錄：repo 根目錄下的 price_cache/（與 daily_scan 原行為一致）
PRICE_CACHE_DIR = Path(__file__).parent.parent / 'price_cache'

# FinMind token 輪替
_FINMIND_TOKENS: list = []
_finmind_token_idx = 0


def retry(fn, *args, max_retries: int = 3, base_delay: float = 3.0, **kwargs):
    """對 fn(*args, **kwargs) 執行最多 max_retries 次重試，
    每次等待 base_delay * 2^attempt 秒（指數退避）。"""
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries:
                raise
            wait = base_delay * (2 ** attempt)
            print(f'    [retry {attempt+1}/{max_retries}] {fn.__name__ if hasattr(fn,"__name__") else "call"} 失敗，{wait:.0f}s 後重試…（{e}）')
            time.sleep(wait)


def collect_tokens():
    global _FINMIND_TOKENS
    if _FINMIND_TOKENS:
        return
    tokens = []
    for key in ['FINMIND_TOKEN'] + [f'FINMIND_TOKEN_{i}' for i in range(1, 5)]:
        t = os.environ.get(key, '')
        if t and t not in tokens:
            tokens.append(t)
    _FINMIND_TOKENS = tokens
    if tokens:
        print(f'[FinMind] 載入 {len(tokens)} 組 token')


def make_dataloader():
    collect_tokens()
    from FinMind.data import DataLoader
    dl = DataLoader()
    if not _FINMIND_TOKENS:
        return dl
    token = _FINMIND_TOKENS[_finmind_token_idx % len(_FINMIND_TOKENS)]
    try:
        dl.login_by_token(api_token=token)
    except Exception:
        pass
    return dl


def finmind_fetch(method_name: str, **kwargs):
    """呼叫 FinMind DataLoader 方法；額度超限時自動輪替 token。"""
    global _finmind_token_idx
    collect_tokens()
    n = max(1, len(_FINMIND_TOKENS))
    last_err = None
    for attempt in range(n):
        dl = make_dataloader()
        method = getattr(dl, method_name)
        try:
            return retry(method, max_retries=2, base_delay=2.0, **kwargs)
        except Exception as e:
            last_err = e
            if ('upper limit' in str(e).lower() or 'quota' in str(e).lower()) and len(_FINMIND_TOKENS) > 1:
                _finmind_token_idx = (_finmind_token_idx + 1) % len(_FINMIND_TOKENS)
                print(f'  [FinMind] 額度已滿，切換至 token[{_finmind_token_idx}]')
                continue
            raise
    raise last_err


class CachedStock:
    """Duck-type for twstock.Stock backed by local price cache."""
    __slots__ = ('price', 'date', 'high', 'low', 'volume')

    def __init__(self, price, date, high, low, volume=None):
        self.price = price
        self.date = date
        self.high = high
        self.low = low
        self.volume = volume or []


def load_price_cache(sid: str):
    path = PRICE_CACHE_DIR / f'{sid}.json'
    if not path.exists():
        return None
    try:
        with open(path, encoding='utf-8') as f:
            d = json.load(f)
        dates = [datetime.strptime(x, '%Y-%m-%d').date() for x in d['dates']]
        return {
            'dates': dates,
            'prices': d['prices'],
            'highs': d.get('highs', []),
            'lows': d.get('lows', []),
            'volumes': d.get('volumes', []),
        }
    except Exception:
        return None


def save_price_cache(sid: str, dates, prices, highs, lows, volumes):
    PRICE_CACHE_DIR.mkdir(exist_ok=True)
    path = PRICE_CACHE_DIR / f'{sid}.json'
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({
            'sid': sid,
            'updated_at': datetime.now().strftime('%Y-%m-%d'),
            'dates': [d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
                      for d in dates],
            'prices': prices,
            'highs': highs,
            'lows': lows,
            'volumes': volumes,
        }, f, separators=(',', ':'))


def yf_history(sid: str, start: str, end: str):
    """yfinance 備援（FinMind 額度耗盡/失敗時用）：回傳與 FinMind 同欄位的 DataFrame。
    上市 .TW → 上櫃 .TWO 依序嘗試。yfinance 為軟依賴（macro/requirements.txt 已含）。"""
    try:
        import yfinance as yf
    except ImportError:
        return None
    import pandas as _pd
    end_plus = (datetime.strptime(end, '%Y-%m-%d') + timedelta(days=1)).strftime('%Y-%m-%d')
    for suffix in ('.TW', '.TWO'):
        try:
            h = yf.Ticker(sid + suffix).history(start=start, end=end_plus, auto_adjust=False)
        except Exception:
            continue
        if h is None or h.empty:
            continue
        df = _pd.DataFrame({
            'date': [d.strftime('%Y-%m-%d') for d in h.index],
            'close': h['Close'].values,
            'max': h['High'].values,
            'min': h['Low'].values,
            'Trading_Volume': h['Volume'].fillna(0).values,
        })
        print(f'    [{sid}] FinMind 失敗，改用 yfinance 備援（{sid}{suffix}，{len(df)} 筆）')
        return df
    return None


def get_history(sid, data_days: int):
    """抓取/更新 K 棒歷史，快取於 price_cache/{sid}.json。
    使用 FinMind taiwan_stock_daily（不受 TWSE IP 封鎖）；失敗時 yfinance 備援。
    非週一時只補快取末日後 7 天，每次掃描僅 ~88 次 FinMind 請求。

    data_days：所需最少天數（原 daily_scan 的 DATA_DAYS）。"""
    today = datetime.now()
    is_monday = today.weekday() == 0
    cached = None if is_monday else load_price_cache(sid)

    def _df_to_arrays(df):
        df = df.dropna(subset=['close']).sort_values('date').reset_index(drop=True)
        if df.empty:
            raise ValueError('close 全為 NaN')
        dates  = [datetime.strptime(str(d), '%Y-%m-%d').date() for d in df['date']]
        prices = [float(v) for v in df['close']]
        highs  = [float(v) for v in df['max']]
        lows   = [float(v) for v in df['min']]
        vols   = [int(v) for v in df['Trading_Volume']]
        return dates, prices, highs, lows, vols

    if cached is None or len(cached['prices']) < data_days:
        start = (today - timedelta(days=int(data_days * 1.6))).strftime('%Y-%m-%d')
        end = today.strftime('%Y-%m-%d')
        try:
            df = finmind_fetch('taiwan_stock_daily', stock_id=sid, start_date=start, end_date=end)
        except Exception:
            df = None
        if df is None or df.empty:
            df = yf_history(sid, start, end)
        if df is None or df.empty:
            raise ValueError(f'{sid} FinMind 與 yfinance 均無資料')
        dates, prices, highs, lows, vols = _df_to_arrays(df)
        save_price_cache(sid, dates, prices, highs, lows, vols)
        return CachedStock(prices, dates, highs, lows, vols)

    # 增量：只補快取末日後（重疊 5 天確保無缺口）
    last_date = cached['dates'][-1]
    fetch_start = (last_date - timedelta(days=5)).strftime('%Y-%m-%d')
    fetch_end = today.strftime('%Y-%m-%d')

    try:
        df = finmind_fetch('taiwan_stock_daily', stock_id=sid, start_date=fetch_start, end_date=fetch_end)
        if df is None or df.empty:
            raise ValueError(f'{sid} 增量抓取空資料')
        new_dates, new_prices, new_highs, new_lows, new_vols = _df_to_arrays(df)
    except Exception as e:
        df = yf_history(sid, fetch_start, fetch_end)
        if df is None or df.empty:
            print(f'    [{sid}] 增量抓取失敗（{e}），使用快取（{last_date}）')
            return CachedStock(cached['prices'], cached['dates'],
                               cached['highs'], cached['lows'], cached['volumes'])
        new_dates, new_prices, new_highs, new_lows, new_vols = _df_to_arrays(df)

    # 合併去重（以日期字串為鍵，新資料覆蓋舊資料）
    dm: dict = {}
    for i, d in enumerate(cached['dates']):
        dk = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
        dm[dk] = (
            cached['prices'][i],
            cached['highs'][i] if i < len(cached['highs']) else None,
            cached['lows'][i] if i < len(cached['lows']) else None,
            cached['volumes'][i] if i < len(cached['volumes']) else None,
        )
    for i, d in enumerate(new_dates):
        dk = d.strftime('%Y-%m-%d') if hasattr(d, 'strftime') else str(d)
        dm[dk] = (
            new_prices[i] if i < len(new_prices) else None,
            new_highs[i] if i < len(new_highs) else None,
            new_lows[i] if i < len(new_lows) else None,
            new_vols[i] if i < len(new_vols) else None,
        )

    keys = sorted(dk for dk, v in dm.items() if v[0] is not None)
    m_dates = [datetime.strptime(k, '%Y-%m-%d').date() for k in keys]
    m_prices = [dm[k][0] for k in keys]
    m_highs = [dm[k][1] for k in keys]
    m_lows = [dm[k][2] for k in keys]
    m_vols = [dm[k][3] for k in keys]

    save_price_cache(sid, m_dates, m_prices, m_highs, m_lows, m_vols)
    return CachedStock(m_prices, m_dates, m_highs, m_lows, m_vols)
