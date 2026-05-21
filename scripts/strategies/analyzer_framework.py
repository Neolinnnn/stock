# scripts/strategies/07_analyzer_framework.py
"""
Analyzer Framework 回測
MA多頭排列 + 偏離度 + 縮量回踩進場規則，一年份台股回測。

用法：
  python scripts/strategies/07_analyzer_framework.py
  python scripts/strategies/07_analyzer_framework.py --days 365
  python scripts/strategies/07_analyzer_framework.py --no-institutional
"""
import sys, os, json, argparse, math
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta, date as dt_date

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest import (
    simulate_position_v2,
    calc_stats,
    calc_next_trading_day,
    get_sorted_trading_days,
)
from finmind_client import get_dataloader

# ── 股票池 ────────────────────────────────────────────────────────────────────
SECTORS = {
    '光通訊': {
        '4979': '華星光', '3450': '聯鈞', '3665': '貿聯-KY',
        '3105': '穩懋', '8086': '宏捷科', '2455': '全新',
        '4906': '正文', '2345': '智邦',
    },
    '記憶體': {
        '2408': '南亞科', '2337': '旺宏', '2344': '華邦電',
        '3006': '晶豪科', '2451': '創見', '5289': '宜鼎',
        '3205': '十銓', '3260': '威剛', '6770': '力積電', '8299': '群聯',
    },
    'AI伺服器': {
        '2317': '鴻海', '2382': '廣達', '3231': '緯創',
        '2376': '技嘉', '4938': '和碩', '2357': '華碩',
    },
    '封測': {
        '3711': '日月光投控', '2449': '京元電子', '6510': '精測',
        '2441': '超豐', '6257': '矽格', '6239': '力成',
        '8150': '南茂', '6147': '頎邦', '3264': '欣銓', '2369': '菱生',
    },
    '光學': {'3008': '大立光', '3406': '玉晶光'},
    'IC設計': {
        '2454': '聯發科', '3034': '聯詠', '4966': '譜瑞-KY',
        '4919': '新唐', '2388': '威盛', '2379': '瑞昱',
        '6415': '矽力-KY', '5269': '祥碩', '2458': '義隆', '3058': '聯陽',
    },
    '車用電子': {'3552': '同致', '1533': '車王電', '2243': '怡利電'},
    '被動元件': {'2327': '國巨', '2492': '華新科', '2456': '奇力新'},
}

STOCK_NAME    = {sid: name for sec in SECTORS.values() for sid, name in sec.items()}
ALL_STOCK_IDS = list(STOCK_NAME.keys())

TP_LIST = [0.15, 0.18, 0.20]
SL_LIST = [0.10, 0.12, 0.15]


# ── 指標計算 ──────────────────────────────────────────────────────────────────

def compute_ma_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """新增 ma5、ma20、vol_ma20 欄位並回傳新 DataFrame。"""
    df = df.copy()
    df['ma5']      = df['close'].rolling(5).mean()
    df['ma20']     = df['close'].rolling(20).mean()
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    return df


# ── 訊號產生 ──────────────────────────────────────────────────────────────────

def generate_signals_a(
    stock_id: str,
    df: pd.DataFrame,
    stock_name: str = '',
) -> list[dict]:
    """
    規則A：4 條件全中才發訊。
      1. MA5 > MA20
      2. (close - MA5) / MA5 < 0.05
      3. volume < vol_ma20 * 0.8
      4. MA5 * 0.97 <= close <= MA5 * 1.03

    df 需已含 ma5、ma20、vol_ma20（先呼叫 compute_ma_indicators）。
    回傳格式與 backtest.py load_buy_signals() 相容。
    """
    signals = []
    for _, row in df.iterrows():
        ma5      = row.get('ma5')
        ma20     = row.get('ma20')
        vol_ma20 = row.get('vol_ma20')
        close    = row['close']
        volume   = row['volume']

        if pd.isna(ma5) or pd.isna(ma20) or pd.isna(vol_ma20) or ma5 == 0:
            continue

        c1 = ma5 > ma20
        c2 = (close - ma5) / ma5 < 0.05
        c3 = volume < vol_ma20 * 0.8
        c4 = ma5 * 0.97 <= close <= ma5 * 1.03

        if c1 and c2 and c3 and c4:
            date_str = str(row['date']).replace('-', '')
            signals.append({
                'date':         date_str,
                'stock_id':     stock_id,
                'stock_name':   stock_name,
                'signal_close': float(close),
                'amount':       3000.0,
            })
    return signals


def generate_signals_b(
    stock_id: str,
    df: pd.DataFrame,
    inst_df: pd.DataFrame,
    stock_name: str = '',
) -> list[dict]:
    """
    規則B：規則A + 三大法人過濾。
      條件5：(外資買進股 - 外資賣出股) + (投信買進股 - 投信賣出股) > 0

    inst_df 欄位：date（YYYY-MM-DD）、name、buy（股）、sell（股）
    inst_df 為空時直接回傳規則A 結果。
    """
    signals_a = generate_signals_a(stock_id, df, stock_name)
    if not signals_a or inst_df.empty:
        return signals_a

    filt = inst_df[inst_df['name'].isin(['外資及陸資', '投信'])].copy()
    filt['net'] = filt['buy'] - filt['sell']
    daily_net = filt.groupby('date')['net'].sum()
    net_by_date = {str(d).replace('-', ''): float(v) for d, v in daily_net.items()}

    return [s for s in signals_a if net_by_date.get(s['date'], 0) > 0]
