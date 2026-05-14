"""
將 daily_reports/YYYYMMDD/summary.json 轉換為 docs/ 靜態 JSON，
供 GitHub Pages 歷史查詢使用。

生成：
  docs/dates.json          — 可用日期清單（降冪）
  docs/YYYYMMDD.json       — 每日 payload（與 daily.json 同格式）
  docs/stocks/{id}.json    — 個股深度分析 JSON（OHLCV + 指標 + 籌碼）
  docs/stocks_index.json   — 個股搜尋索引

用法：
  python scripts/build_docs.py
"""
import json
import math
import os
import time
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(__file__))

try:
    import pandas as pd
    import numpy as np
    from scipy.signal import find_peaks
    _SCIPY_OK = True
except ImportError:
    _SCIPY_OK = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _nan_to_none(v):
    if isinstance(v, float) and math.isnan(v):
        return None
    return v


# ── FinMind helpers ─────────────────────────────────────────────────────────

def _get_dl():
    from FinMind.data import DataLoader
    dl = DataLoader()
    token = os.environ.get('FINMIND_TOKEN', '')
    if token:
        dl.login_by_token(api_token=token)
    return dl


# ── Technical indicator computation ─────────────────────────────────────────

def _compute_indicators(df: 'pd.DataFrame') -> 'pd.DataFrame':
    df = df.copy()
    df['ma20']    = df['close'].rolling(20).mean()
    std20         = df['close'].rolling(20).std()
    df['bb_upper'] = df['ma20'] + 2 * std20
    df['bb_lower'] = df['ma20'] - 2 * std20
    df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / (df['ma20'] + 1e-9)
    df['ma5']     = df['close'].rolling(5).mean()
    df['ma60']    = df['close'].rolling(60).mean()
    low9          = df['low'].rolling(9).min()
    high9         = df['high'].rolling(9).max()
    rsv           = (df['close'] - low9) / (high9 - low9 + 1e-9) * 100
    df['kd_k']    = rsv.ewm(com=2, adjust=False).mean()
    df['kd_d']    = df['kd_k'].ewm(com=2, adjust=False).mean()
    df['kd_j']    = 3 * df['kd_k'] - 2 * df['kd_d']
    ema12         = df['close'].ewm(span=12, adjust=False).mean()
    ema26         = df['close'].ewm(span=26, adjust=False).mean()
    df['macd']         = ema12 - ema26
    df['macd_signal']  = df['macd'].ewm(span=9, adjust=False).mean()
    df['macd_hist']    = df['macd'] - df['macd_signal']
    df['vol_ma20']     = df['volume'].rolling(20).mean()
    return df


def _round_list(series, decimals=2):
    result = []
    for v in series:
        if pd.isna(v):
            result.append(None)
        else:
            result.append(round(float(v), decimals))
    return result


def _int_list(series):
    result = []
    for v in series:
        if pd.isna(v):
            result.append(None)
        else:
            result.append(int(v))
    return result


def _technical_summary(df: 'pd.DataFrame') -> list:
    row  = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else row
    close = row['close']
    bb_range = row['bb_upper'] - row['bb_lower']
    bb_pos   = (close - row['bb_lower']) / (bb_range + 1e-9)
    items = []

    if row['ma5'] > row['ma20'] > row['ma60']:
        items.append({'label': '趨勢方向', 'value': '多頭趨勢', 'direction': 'up'})
    elif row['ma5'] < row['ma20'] < row['ma60']:
        items.append({'label': '趨勢方向', 'value': '空頭趨勢', 'direction': 'down'})
    else:
        items.append({'label': '趨勢方向', 'value': '盤整趨勢', 'direction': 'neutral'})

    if bb_pos > 0.85:
        items.append({'label': '價格位置', 'value': '高檔整理（貼近上軌）', 'direction': 'down'})
    elif bb_pos >= 0.5:
        items.append({'label': '價格位置', 'value': '中軌以上', 'direction': 'up'})
    elif bb_pos >= 0.15:
        items.append({'label': '價格位置', 'value': '中軌以下', 'direction': 'neutral'})
    else:
        items.append({'label': '價格位置', 'value': '低檔整理（貼近下軌）', 'direction': 'up'})

    if row['ma5'] > row['ma20'] > row['ma60']:
        items.append({'label': '均線排列', 'value': '多頭排列（5>20>60）', 'direction': 'up'})
    elif row['ma5'] < row['ma20'] < row['ma60']:
        items.append({'label': '均線排列', 'value': '空頭排列（5<20<60）', 'direction': 'down'})
    else:
        items.append({'label': '均線排列', 'value': '多空交錯', 'direction': 'neutral'})

    vol_ratio = row['volume'] / (row['vol_ma20'] + 1e-9)
    if vol_ratio > 1.5 and close > prev['close']:
        items.append({'label': '量價關係', 'value': '放量上漲，動能強勁', 'direction': 'up'})
    elif vol_ratio < 0.7:
        items.append({'label': '量價關係', 'value': '量縮整理，動能降溫', 'direction': 'neutral'})
    elif vol_ratio > 1.5 and close < prev['close']:
        items.append({'label': '量價關係', 'value': '放量下跌，注意風險', 'direction': 'down'})
    else:
        items.append({'label': '量價關係', 'value': '量能平穩', 'direction': 'neutral'})

    if bb_pos > 0.9:
        items.append({'label': '布林位置', 'value': '貼近上軌（過熱）', 'direction': 'down'})
    elif bb_pos < 0.1:
        items.append({'label': '布林位置', 'value': '貼近下軌（超跌）', 'direction': 'up'})
    else:
        items.append({'label': '布林位置', 'value': f'中軌區間（{bb_pos:.0%}）', 'direction': 'neutral'})

    prev5_width = df.iloc[-5]['bb_width'] if len(df) >= 5 else row['bb_width']
    if row['bb_width'] > prev5_width * 1.1:
        items.append({'label': '布林通道', 'value': '開口擴大（趨勢加速）', 'direction': 'up'})
    elif row['bb_width'] < prev5_width * 0.9:
        items.append({'label': '布林通道', 'value': '開口收斂（蓄勢待發）', 'direction': 'neutral'})
    else:
        items.append({'label': '布林通道', 'value': '開口平穩', 'direction': 'neutral'})

    if row['kd_k'] > 80:
        items.append({'label': 'KD狀態', 'value': '高檔鈍化', 'direction': 'down'})
    elif row['kd_k'] < 20:
        items.append({'label': 'KD狀態', 'value': '低檔鈍化', 'direction': 'up'})
    elif row['kd_k'] > row['kd_d'] and prev['kd_k'] <= prev['kd_d']:
        items.append({'label': 'KD狀態', 'value': '黃金交叉', 'direction': 'up'})
    elif row['kd_k'] < row['kd_d'] and prev['kd_k'] >= prev['kd_d']:
        items.append({'label': 'KD狀態', 'value': '死亡交叉', 'direction': 'down'})
    else:
        items.append({'label': 'KD狀態', 'value': f"K {row['kd_k']:.1f} / D {row['kd_d']:.1f}", 'direction': 'neutral'})

    up_count = sum(1 for i in items if i['direction'] == 'up')
    dn_count = sum(1 for i in items if i['direction'] == 'down')
    if up_count >= 5:
        items.append({'label': '綜合評估', 'value': '主升段延續', 'direction': 'up'})
    elif dn_count >= 5:
        items.append({'label': '綜合評估', 'value': '弱勢下跌', 'direction': 'down'})
    elif bb_pos > 0.8 and row['kd_k'] > 70:
        items.append({'label': '綜合評估', 'value': '高檔震盪出貨初期', 'direction': 'down'})
    elif bb_pos < 0.25 and row['kd_k'] < 35:
        items.append({'label': '綜合評估', 'value': '底部蓄積', 'direction': 'up'})
    else:
        items.append({'label': '綜合評估', 'value': '盤整觀望', 'direction': 'neutral'})
    return items


def _key_levels(df: 'pd.DataFrame') -> dict:
    row    = df.iloc[-1]
    high60 = df['close'].tail(60).max()
    r_lo   = round(row['bb_upper'] * 0.995 / 10) * 10
    r_hi   = round(row['bb_upper'] * 1.005 / 10) * 10
    bb_lo  = round(row['bb_lower'] * 0.99  / 100) * 100
    bb_hi  = round(row['bb_lower'] * 1.01  / 100) * 100
    return {
        'resistance': f'{r_lo:.0f} ～ {r_hi:.0f}',
        'pullback':   f'{row["ma20"] * 0.975:.0f} ～ {row["ma20"] * 1.025:.0f}',
        'support':    f'{bb_lo:.0f} ～ {bb_hi:.0f}',
        'breakdown':  round(float(row['ma20']), 0),
        'breakout':   round(float(high60), 0),
    }


def _detect_mj_signals(df: 'pd.DataFrame') -> list:
    """MJ強化版：J線零軸穿越 + MACD OSC 同向"""
    rows = []
    j   = df['kd_j'].values
    osc = df['macd_hist'].values
    dates = df['date'].values
    closes = df['close'].values
    for i in range(1, len(df)):
        prev_j, curr_j = j[i - 1], j[i]
        curr_osc = osc[i]
        if prev_j < 0 and curr_j >= 0 and curr_osc > 0:
            signal = 'LONG'
        elif prev_j > 0 and curr_j <= 0 and curr_osc < 0:
            signal = 'SHORT'
        else:
            continue
        rows.append({
            'date':     str(dates[i])[:10],
            'signal':   signal,
            'close':    round(float(closes[i]), 2),
            'kd_j':     round(float(curr_j), 2),
            'macd_osc': round(float(curr_osc), 4),
        })
    return rows


def _detect_patterns(df: 'pd.DataFrame') -> list:
    if not _SCIPY_OK:
        return []
    closes = df['close'].tail(60).values
    std    = closes.std()
    if std == 0:
        return []
    lows_idx,  _ = find_peaks(-closes, distance=5, prominence=std * 0.3)
    highs_idx, _ = find_peaks( closes, distance=5, prominence=std * 0.3)
    patterns = []
    last_date = df['date'].iloc[-1] if 'date' in df.columns else ''

    if len(lows_idx) >= 2:
        l1, l2 = lows_idx[-2], lows_idx[-1]
        if closes[l2] > closes[l1] * 0.97:
            neckline = closes[l1:l2].max()
            if closes[-1] > neckline:
                patterns.append({'label': 'W底（雙底）', 'desc': '已突破頸線，確認型態', 'date': last_date})
            else:
                patterns.append({'label': 'W底（雙底）候選', 'desc': '右低已形成，等待突破頸線', 'date': last_date})

    if len(highs_idx) >= 2:
        h1, h2 = highs_idx[-2], highs_idx[-1]
        if closes[h2] < closes[h1] * 1.03:
            neckline = closes[h1:h2].min()
            if closes[-1] < neckline:
                patterns.append({'label': 'M頭（雙頂）', 'desc': '已跌破頸線，確認型態', 'date': last_date})
            else:
                patterns.append({'label': 'M頭（雙頂）候選', 'desc': '右峰已形成，尚未跌破頸線', 'date': last_date})
    return patterns


def _chip_aggregate(df: 'pd.DataFrame') -> dict:
    """將 FinMind 三大法人 DataFrame 轉為前端需要的 dict 格式"""
    _MAP = {
        'Foreign_Investor':    '外資',
        'Foreign_Dealer_Self': '外資',
        'Investment_Trust':    '投信',
        'Dealer_self':         '自營',
        'Dealer_Hedging':      '自營',
    }
    if df is None or df.empty:
        return {}
    df = df.sort_values('date')
    dates = sorted(df['date'].unique())[-20:]
    rows = []
    for date in dates:
        day = df[df['date'] == date]
        agg = {'date': str(date)[:10], '外資': 0, '投信': 0, '自營': 0}
        for _, r in day.iterrows():
            zh = _MAP.get(str(r.get('name', '')))
            if zh:
                agg[zh] += (int(r.get('buy', 0)) - int(r.get('sell', 0))) // 1000
        agg['合計'] = agg['外資'] + agg['投信'] + agg['自營']
        rows.append(agg)
    return {
        'dates':   [r['date']  for r in rows],
        'foreign': [r['外資']  for r in rows],
        'trust':   [r['投信']  for r in rows],
        'dealer':  [r['自營']  for r in rows],
        'total':   [r['合計']  for r in rows],
    }


def _main_force_signal(chip: dict, df: 'pd.DataFrame') -> dict:
    if not chip or not chip.get('total'):
        return {'label': '觀望', 'color': '#95a5a6', 'desc': '資料不足'}
    recent5   = sum(chip['total'][-5:])
    cum10     = sum(chip['total'][-10:])
    close     = float(df['close'].iloc[-1])
    high60    = float(df['close'].tail(60).max())
    vol_ratio = float(df['volume'].iloc[-1]) / (float(df['volume'].tail(20).mean()) + 1e-9)
    if recent5 > 500 and cum10 > 0:
        return {'label': '吸籌期',   'color': '#27ae60', 'desc': '主力持續買超，籌碼集中'}
    if recent5 < -500 and close >= high60 * 0.9:
        return {'label': '出貨初期', 'color': '#e74c3c', 'desc': '主力開始調節，需留意短線風險'}
    if abs(recent5) < 500 and vol_ratio < 0.8:
        return {'label': '整理期',   'color': '#f39c12', 'desc': '量縮整理，等待方向'}
    return {'label': '觀望', 'color': '#95a5a6', 'desc': '籌碼中性，無明顯方向'}


def _simple_prediction(summary: list) -> dict:
    """根據技術面摘要用啟發法估算漲跌機率"""
    up  = sum(1 for s in summary if s['direction'] == 'up')
    dn  = sum(1 for s in summary if s['direction'] == 'down')
    n   = len(summary) or 1
    p_up  = round(0.35 + (up - dn) / n * 0.25, 2)
    p_dn  = round(0.35 - (up - dn) / n * 0.25, 2)
    p_up  = max(0.05, min(0.85, p_up))
    p_dn  = max(0.05, min(0.85, p_dn))
    p_sid = round(max(0.1, 1 - p_up - p_dn), 2)
    return {'up': p_up, 'sideways': p_sid, 'down': p_dn, 'accuracy': 0.55}


# ── Daily payload (unchanged) ────────────────────────────────────────────────

def build_daily_payload(summary):
    sectors, chips, stocks = [], [], []
    main_force = []
    for sector, data in summary.get('sectors', {}).items():
        sectors.append({
            'sector': sector,
            'ret20':  _nan_to_none(data.get('avg_ret_20d', '')),
            'rsi':    _nan_to_none(data.get('avg_rsi', '')),
            'buy':    data.get('buy_count', 0),
            'hot':    data.get('hot_count', 0),
        })
        for st in data.get('stocks', []):
            chip = st.get('chip', {})
            stocks.append({
                'date':      summary['date'],
                'sector':    sector,
                'id':        st['id'],
                'name':      st['name'],
                'price':     _nan_to_none(st.get('price', '')),
                'rsi':       _nan_to_none(st.get('rsi', '')),
                'rsi10':     _nan_to_none(st.get('rsi10', '')),
                'ret20':     _nan_to_none(st.get('ret_20d', '')),
                'signal':    st.get('signal', ''),
                'sharpe':    _nan_to_none(st.get('cv_sharpe', '')),
                'foreign':   chip.get('外資', ''),
                'trust':     chip.get('投信', ''),
                'dealer':    chip.get('自營', ''),
                'chipTotal': chip.get('合計', ''),
                'news': ' / '.join(
                    n['title'] for n in st.get('news', [])[:2]
                ),
            })
            # 主力觀察（ATR 停損 + 分點集中度 + 主力強度分）— 給新分頁專用
            mf = st.get('main_force') or {}
            br = st.get('broker') or {}
            if (
                st.get('atr14') is not None
                or st.get('stop_loss') is not None
                or mf.get('score') is not None
                or br.get('source')
            ):
                main_force.append({
                    'id': st['id'],
                    'name': st['name'],
                    'sector': sector,
                    'price': _nan_to_none(st.get('price', '')),
                    'signal': st.get('signal', ''),
                    'atr14': st.get('atr14'),
                    'stopLoss': st.get('stop_loss'),
                    'mainForceScore': mf.get('score'),
                    'mainForceLabel': mf.get('label'),
                    'top1Broker': mf.get('top1_broker'),
                    'top1Lots': mf.get('top1_lots'),
                    'top5BuyLots': mf.get('top5_buy_lots'),
                    'concentration': mf.get('concentration'),
                    'brokerNet': br.get('net_concentration'),
                    'topBuyers': br.get('top_buyers') or [],
                    'topSellers': br.get('top_sellers') or [],
                    'brokerSource': br.get('source'),
                    'brokerError': br.get('error'),
                })
            if chip.get('合計', 0):
                chips.append({
                    'id': st['id'], 'name': st['name'], 'sector': sector,
                    'total':   chip.get('合計', 0),
                    'foreign': chip.get('外資', 0),
                    'trust':   chip.get('投信', 0),
                    'dealer':  chip.get('自營', 0),
                })
    mkt = summary.get('market', {})
    qualified = [
        {
            'sector':    q.get('sector', ''),
            'id':        q.get('id', ''),
            'name':      q.get('name', ''),
            'price':     q.get('price', ''),
            'rsi':       q.get('rsi', ''),
            'cv_sharpe': q.get('cv_sharpe', ''),
        }
        for q in summary.get('qualified', [])
    ]
    main_force_sorted = sorted(
        main_force,
        key=lambda x: (x.get('mainForceScore') is None, -(x.get('mainForceScore') or 0)),
    )
    return {
        'meta': {
            '掃描日期':  summary.get('date', ''),
            '加權指數':  mkt.get('加權指數', ''),
            '漲跌幅%':   mkt.get('漲跌幅', ''),
            '強勢族群':  ', '.join(summary.get('strong_sectors', [])),
            '弱勢族群':  ', '.join(summary.get('weak_sectors', [])),
            '雙條件推薦': len(qualified),
        },
        'qualified': qualified,
        'sectors': sectors,
        'chips':   sorted(chips, key=lambda x: x['total'], reverse=True),
        'stocks':  stocks,
        'mainForce': main_force_sorted,
    }


# ── Rich stock pages ─────────────────────────────────────────────────────────

def build_stock_pages(date_dirs, docs_dir, keep_days=90):
    """
    為每檔個股產生 docs/stocks/{stock_id}.json（豐富格式），
    含 OHLCV、技術指標、籌碼、技術面摘要、型態偵測、關鍵價位。
    """
    if not _SCIPY_OK:
        print('[WARN] pandas/numpy/scipy 未安裝，跳過個股頁面生成')
        return

    stocks_dir = docs_dir / 'stocks'
    stocks_dir.mkdir(exist_ok=True)

    # 收集所有個股基本資訊
    stock_info_map: dict = {}
    for d in date_dirs[:keep_days]:
        summary = json.loads((d / 'summary.json').read_text(encoding='utf-8'))
        for sector, sdata in summary.get('sectors', {}).items():
            for st in sdata.get('stocks', []):
                sid = st['id']
                if sid not in stock_info_map:
                    stock_info_map[sid] = {
                        'id': sid, 'name': st['name'], 'sector': sector
                    }

    # 初始化 FinMind
    try:
        dl = _get_dl()
        finmind_ok = True
    except Exception as e:
        print(f'[WARN] FinMind 初始化失敗：{e}  → 跳過 OHLCV 抓取')
        finmind_ok = False

    from datetime import datetime, timedelta
    end_date   = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d')
    chip_start = (datetime.now() - timedelta(days=45)).strftime('%Y-%m-%d')

    ok_count = 0
    for sid, info in stock_info_map.items():
        try:
            _build_single_stock(
                sid, info, dl if finmind_ok else None,
                stocks_dir, start_date, end_date, chip_start,
            )
            ok_count += 1
            time.sleep(0.3)   # 避免 API 頻率限制
        except Exception as e:
            print(f'  [WARN] {sid} {info["name"]}: {e}')

    # stocks_index.json
    index = [{'id': v['id'], 'name': v['name'], 'sector': v['sector']}
             for v in stock_info_map.values()]
    (docs_dir / 'stocks_index.json').write_text(
        json.dumps(index, ensure_ascii=False, separators=(',', ':')),
        encoding='utf-8',
    )
    print(f'stocks/ 已更新：{ok_count}/{len(stock_info_map)} 檔個股（豐富格式）')


def _build_single_stock(sid, info, dl, stocks_dir, start_date, end_date, chip_start):
    from datetime import datetime

    # ── 抓 OHLCV ──────────────────────────────────────────────────────────────
    if dl is not None:
        df_raw = dl.taiwan_stock_daily(
            stock_id=sid, start_date=start_date, end_date=end_date
        )
        if df_raw.empty:
            print(f'  [SKIP] {sid} 無資料')
            return
        df_raw = df_raw.sort_values('date').rename(
            columns={'max': 'high', 'min': 'low', 'Trading_Volume': 'volume'}
        )
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df_raw[col] = pd.to_numeric(df_raw[col], errors='coerce')
        df_raw = df_raw.dropna(subset=['close']).reset_index(drop=True)
    else:
        print(f'  [SKIP] {sid} — 無 FinMind 連線')
        return

    # ── 計算指標 ──────────────────────────────────────────────────────────────
    if len(df_raw) < 26:
        return
    df = _compute_indicators(df_raw).reset_index(drop=True)

    # ── 抓籌碼 ────────────────────────────────────────────────────────────────
    chip_data = {}
    try:
        chip_raw = dl.taiwan_stock_institutional_investors(
            stock_id=sid, start_date=chip_start, end_date=end_date
        )
        chip_data = _chip_aggregate(chip_raw)
    except Exception:
        pass

    # ── 分析 ──────────────────────────────────────────────────────────────────
    df_valid = df.dropna(subset=['ma60']).reset_index(drop=True)
    if df_valid.empty:
        df_valid = df.dropna(subset=['ma20']).reset_index(drop=True)
    if df_valid.empty:
        return

    summary_items = _technical_summary(df_valid)
    levels        = _key_levels(df_valid)
    patterns      = _detect_patterns(df_valid)
    mj_signals    = _detect_mj_signals(df_valid)
    signal        = _main_force_signal(chip_data, df_valid)
    prediction    = _simple_prediction(summary_items)

    # ── 最後報價 ──────────────────────────────────────────────────────────────
    last   = df_raw.iloc[-1]
    prev   = df_raw.iloc[-2] if len(df_raw) >= 2 else last
    change = float(last['close']) - float(prev['close'])
    chg_pct = change / float(prev['close']) * 100 if prev['close'] else 0

    # ── 組裝 JSON ─────────────────────────────────────────────────────────────
    tail = df_raw.tail(90)   # 最多 90 根 K 棒給圖表
    df_ind = df.loc[df_raw.tail(90).index]

    payload = {
        'stock_id':     sid,
        'name':         info['name'],
        'industry':     info['sector'],
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M'),
        'quote': {
            'close':      round(float(last['close']), 2),
            'change':     round(change, 2),
            'change_pct': round(chg_pct, 2),
        },
        'ohlcv': {
            'date':   [str(d)[:10] for d in tail['date'].tolist()],
            'open':   _round_list(tail['open']),
            'high':   _round_list(tail['high']),
            'low':    _round_list(tail['low']),
            'close':  _round_list(tail['close']),
            'volume': _int_list(tail['volume']),
        },
        'indicators': {
            'bb_upper':    _round_list(df_ind['bb_upper']),
            'bb_lower':    _round_list(df_ind['bb_lower']),
            'bb_mid':      _round_list(df_ind['ma20']),
            'ma5':         _round_list(df_ind['ma5']),
            'ma20':        _round_list(df_ind['ma20']),
            'ma60':        _round_list(df_ind['ma60']),
            'macd':        _round_list(df_ind['macd']),
            'macd_signal': _round_list(df_ind['macd_signal']),
            'macd_hist':   _round_list(df_ind['macd_hist']),
            'kd_k':        _round_list(df_ind['kd_k']),
            'kd_d':        _round_list(df_ind['kd_d']),
            'kd_j':        _round_list(df_ind['kd_j']),
        },
        'chip':       chip_data,
        'mj_signals': mj_signals,
        'signal':     signal,
        'summary':  summary_items,
        'prediction': prediction,
        'patterns': patterns,
        'levels':   levels,
    }

    (stocks_dir / f'{sid}.json').write_text(
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        encoding='utf-8',
    )


# ── Main ─────────────────────────────────────────────────────────────────────

def build_all():
    base     = Path('daily_reports')
    docs_dir = Path('docs')
    docs_dir.mkdir(exist_ok=True)

    date_dirs = sorted(
        [d for d in base.iterdir()
         if d.is_dir() and d.name.isdigit() and len(d.name) == 8
         and (d / 'summary.json').exists()],
        key=lambda d: d.name,
        reverse=True,
    )

    if not date_dirs:
        print('找不到任何 daily_reports/YYYYMMDD/summary.json')
        return []

    dates = []
    for d in date_dirs:
        summary = json.loads((d / 'summary.json').read_text(encoding='utf-8'))
        payload = build_daily_payload(summary)
        out = docs_dir / f'{d.name}.json'
        out.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
            encoding='utf-8',
        )
        dates.append(d.name)

    (docs_dir / 'dates.json').write_text(
        json.dumps(dates, ensure_ascii=False), encoding='utf-8'
    )

    latest_summary = json.loads(
        (date_dirs[0] / 'summary.json').read_text(encoding='utf-8')
    )
    (docs_dir / 'daily.json').write_text(
        json.dumps(build_daily_payload(latest_summary),
                   ensure_ascii=False, indent=2, default=str),
        encoding='utf-8',
    )

    print(f'docs/ 已更新：{len(dates)} 個交易日  最新={dates[0]}')

    build_stock_pages(date_dirs, docs_dir)
    return dates


if __name__ == '__main__':
    build_all()
