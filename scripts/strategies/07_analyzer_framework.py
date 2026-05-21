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


# ── 資料抓取 ──────────────────────────────────────────────────────────────────

def fetch_ohlc(dl, stock_ids: list[str], start: str, end: str) -> dict:
    """
    回傳 {stock_id: {date_str(YYYYMMDD): {'open','high','low','close','volume'}}}
    start/end 格式：'YYYY-MM-DD'
    FinMind 欄名：max=high, min=low, Trading_Volume=volume
    """
    price_data: dict[str, dict] = {}
    for sid in stock_ids:
        try:
            raw = dl.taiwan_stock_daily(stock_id=sid, start_date=start, end_date=end)
            if raw is None or raw.empty:
                price_data[sid] = {}
                continue
            raw['_date'] = pd.to_datetime(raw['date']).dt.strftime('%Y%m%d')
            price_data[sid] = {
                row['_date']: {
                    'open':   float(row['open']),
                    'high':   float(row.get('max',  row['open'])),
                    'low':    float(row.get('min',  row['close'])),
                    'close':  float(row['close']),
                    'volume': float(row.get('Trading_Volume', 0)),
                }
                for _, row in raw.iterrows()
            }
        except Exception as e:
            print(f'    ⚠️  {sid} OHLC 失敗：{e}')
            price_data[sid] = {}
    return price_data


def fetch_institutional(dl, stock_ids: list[str], start: str, end: str) -> dict:
    """
    回傳 {stock_id: DataFrame}，df 含 date(YYYY-MM-DD), name, buy, sell
    """
    result: dict[str, pd.DataFrame] = {}
    for sid in stock_ids:
        try:
            df = dl.taiwan_stock_institutional_investors(
                stock_id=sid, start_date=start, end_date=end
            )
            result[sid] = df if (df is not None and not df.empty) else pd.DataFrame()
        except Exception as e:
            print(f'    ⚠️  {sid} 法人資料失敗：{e}')
            result[sid] = pd.DataFrame()
    return result


# ── 訊號收集 ──────────────────────────────────────────────────────────────────

def collect_all_signals(
    price_data: dict,
    inst_data: dict | None = None,
    cutoff: str = '',
) -> tuple[list[dict], list[dict]]:
    """
    對所有個股產生規則A、規則B 訊號。
    cutoff：YYYYMMDD，只保留 >= cutoff 的訊號（排除 MA 預熱期）。
    回傳 (signals_a, signals_b)
    """
    signals_a, signals_b = [], []
    seen_a: set[tuple] = set()
    seen_b: set[tuple] = set()

    for sid, daily_ohlc in price_data.items():
        if not daily_ohlc:
            continue
        name = STOCK_NAME.get(sid, sid)

        rows = [{'date': d, **v} for d, v in sorted(daily_ohlc.items())]
        df = pd.DataFrame(rows)
        df['date'] = df['date'].apply(lambda x: f'{x[:4]}-{x[4:6]}-{x[6:]}')
        df = df.sort_values('date').reset_index(drop=True)
        df = compute_ma_indicators(df)

        inst_df = (inst_data or {}).get(sid, pd.DataFrame())

        for sig in generate_signals_a(sid, df, name):
            if cutoff and sig['date'] < cutoff:
                continue
            k = (sig['date'], sid)
            if k not in seen_a:
                seen_a.add(k)
                signals_a.append(sig)

        for sig in generate_signals_b(sid, df, inst_df, name):
            if cutoff and sig['date'] < cutoff:
                continue
            k = (sig['date'], sid)
            if k not in seen_b:
                seen_b.add(k)
                signals_b.append(sig)

    return signals_a, signals_b


# ── 回測執行 ──────────────────────────────────────────────────────────────────

def run_all_combos(
    signals: list[dict],
    price_data: dict,
    trading_days: list[str],
) -> dict:
    """執行 9 組 TP×SL，回傳 {key: {'stats':..., 'trades':[...]}}"""
    combos: dict[str, dict] = {}
    for tp in TP_LIST:
        for sl in SL_LIST:
            key = f'TP{int(tp*100)}_SL{int(sl*100)}'
            trades = []
            for sig in signals:
                sid        = sig['stock_id']
                entry_date = calc_next_trading_day(sig['date'], trading_days)
                if entry_date is None:
                    continue
                stock_px   = price_data.get(sid, {})
                entry_info = stock_px.get(entry_date)
                if not entry_info:
                    continue
                ep = entry_info['open']
                if ep <= 0 or math.isnan(ep):
                    continue
                ohlc = {d: v for d, v in stock_px.items() if d > entry_date}
                trade = simulate_position_v2(
                    entry_date=entry_date,
                    entry_price=ep,
                    amount=sig.get('amount', 3000.0),
                    ohlc_prices=ohlc,
                    trading_days=[d for d in trading_days if d >= entry_date],
                    tp=tp,
                    sl=sl,
                )
                trade['stock_id']    = sid
                trade['stock_name']  = sig['stock_name']
                trade['signal_date'] = sig['date']
                trade['tp']          = tp
                trade['sl']          = sl
                trades.append(trade)
            combos[key] = {'stats': calc_stats(trades), 'trades': trades}
    return combos


# ── 輸出 ──────────────────────────────────────────────────────────────────────

def print_combos(label: str, combos: dict) -> None:
    print(f'\n【{label}】')
    print(f'  {"組合":<12} {"勝率":>7}  {"交易數":>5}  {"未結":>4}  {"avg報酬":>8}  {"avg持有":>7}')
    for key, res in sorted(combos.items()):
        s = res['stats']
        print(
            f'  {key:<12} {s["win_rate"]:>7.1%}  {s["total"]:>5}  '
            f'{s["open_count"]:>4}  {s["avg_return"]:>+8.2f}%  '
            f'{s["avg_holding_days"]:>6.1f}天'
        )


def print_comparison(
    signals_a: list[dict],
    signals_b: list[dict],
    combos_a: dict,
    combos_b: dict,
) -> None:
    def best(combos: dict) -> tuple[str, dict]:
        return max(combos.items(), key=lambda kv: kv[1]['stats']['win_rate'])

    key_a, res_a = best(combos_a)
    key_b, res_b = best(combos_b)
    sa, sb = res_a['stats'], res_b['stats']

    na, nb = len(signals_a), len(signals_b)
    reduction = (na - nb) / na if na else 0.0
    wr_diff   = sb['win_rate'] - sa['win_rate']
    ret_diff  = sb['avg_return'] - sa['avg_return']

    print('\n【對比摘要】')
    print(f'  規則A 訊號數：{na}  規則B 訊號數：{nb}  '
          f'法人過濾減少 {reduction:.0%}')
    print(f'  最佳組合(A)：{key_a}  '
          f'勝率 {sa["win_rate"]:.1%}  avg報酬 {sa["avg_return"]:+.2f}%')
    print(f'  最佳組合(B)：{key_b}  '
          f'勝率 {sb["win_rate"]:.1%}  avg報酬 {sb["avg_return"]:+.2f}%')
    print(f'  法人過濾效果：勝率 {wr_diff:+.1%}，avg報酬 {ret_diff:+.2f}%')


# ── 主程式 ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description='Analyzer Framework 回測')
    parser.add_argument('--days', type=int, default=365,
                        help='回測天數（預設 365，額外 +60 天預熱 MA）')
    parser.add_argument('--no-institutional', action='store_true',
                        help='跳過三大法人資料（只跑規則A）')
    args = parser.parse_args()

    end_date    = dt_date.today()
    fetch_start = end_date - timedelta(days=args.days + 60)
    bt_cutoff   = end_date - timedelta(days=args.days)
    start_str   = fetch_start.strftime('%Y-%m-%d')
    end_str     = end_date.strftime('%Y-%m-%d')
    cutoff_ym   = bt_cutoff.strftime('%Y%m%d')
    end_ym      = end_date.strftime('%Y%m%d')

    print(f'\n{"="*60}')
    print(f'  Analyzer Framework 回測  {cutoff_ym} ~ {end_ym}')
    print(f'  規則A：MA多頭排列 + 偏離度<5% + 縮量回踩±3%')
    print(f'  規則B：規則A + 三大法人買超')
    print(f'{"="*60}')

    dl = get_dataloader()

    print(f'\n【Step 1】抓取 OHLCV（{len(ALL_STOCK_IDS)} 檔，{start_str} ~ {end_str}）')
    price_data   = fetch_ohlc(dl, ALL_STOCK_IDS, start_str, end_str)
    trading_days = get_sorted_trading_days(price_data)
    trading_days_bt = [d for d in trading_days if d >= cutoff_ym]
    print(f'  回測交易日：{len(trading_days_bt)} 天')

    inst_data = None
    if not args.no_institutional:
        print(f'\n【Step 2】抓取三大法人買賣超')
        inst_data = fetch_institutional(dl, ALL_STOCK_IDS, start_str, end_str)

    print(f'\n【Step 3】產生訊號')
    signals_a, signals_b = collect_all_signals(price_data, inst_data, cutoff=cutoff_ym)
    print(f'  規則A：{len(signals_a)} 筆  規則B：{len(signals_b)} 筆')

    if not signals_a:
        print('  ❌ 無訊號，終止')
        return

    print(f'\n【Step 4】執行 9 組 TP×SL 回測')
    combos_a = run_all_combos(signals_a, price_data, trading_days_bt)
    combos_b = run_all_combos(signals_b, price_data, trading_days_bt)

    print_combos('規則 A：純技術（MA排列 + 偏離度 + 縮量回踩）', combos_a)
    print_combos('規則 B：加三大法人過濾', combos_b)
    print_comparison(signals_a, signals_b, combos_a, combos_b)

    out = {
        'generated_at': datetime.now().isoformat(),
        'date_range':   {'start': cutoff_ym, 'end': end_ym},
        'rule_a': {
            'total_signals': len(signals_a),
            'combinations': {
                k: {'stats': v['stats'], 'trades': v['trades']}
                for k, v in combos_a.items()
            },
        },
        'rule_b': {
            'total_signals': len(signals_b),
            'combinations': {
                k: {'stats': v['stats'], 'trades': v['trades']}
                for k, v in combos_b.items()
            },
        },
    }
    out_path = Path('backtest_analyzer_results.json')
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2, default=str),
        encoding='utf-8',
    )
    print(f'\n  ✅ 結果已存至 {out_path}')
    print(f'\n{"="*60}\n')


if __name__ == '__main__':
    main()
