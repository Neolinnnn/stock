"""
台股光通訊族群專屬掃描 + 相對強弱排名 + Walk-Forward CV
Optical Communication Sector Scanner
"""

import sys, os
sys.path.insert(0, os.path.dirname(__file__))

# 直接匯入主引擎
from importlib import import_module
m = import_module('03_batch_scan_with_cv') if False else None  # 檔名含數字不能 import

# 改直接 exec 主檔以重用函式
_here = os.path.dirname(__file__)
_main = os.path.join(_here, '03_batch_scan_with_cv.py')
with open(_main, encoding='utf-8') as f:
    code = f.read()
# 只取工具函式，不執行 main
code = code.split("if __name__ == '__main__':")[0]
exec(code, globals())

import pandas as pd
import numpy as np
from datetime import datetime


# ── 光通訊族群（依子類別分組） ─────────────────────────────────────────────
OPTICAL_COMM = {
    # 光收發模組 / CPO / 矽光子
    '4979': ('華星光',   '光模組'),
    '3450': ('聯鈞',     '光模組'),
    '3665': ('貿聯-KY',  '光模組'),
    '3363': ('上詮',     '光被動'),

    # 砷化鎵 / 磷化銦（光通訊晶片磊晶）
    '3105': ('穩懋',     '砷化鎵'),
    '8086': ('宏捷科',   '砷化鎵'),
    '2455': ('全新',     '砷化鎵'),
    '4991': ('環宇-KY',  '砷化鎵'),

    # 光纖 / 光纜
    '1605': ('華新',     '光纖纜'),

    # 矽光子 / 先進封裝
    '3034': ('聯詠',     '矽光子'),
    '6515': ('穎崴',     '矽光子測試'),
    '6271': ('同欣電',   '光封裝'),

    # 網通 / 資料中心交換器（光通訊下游）
    '2345': ('智邦',     '網通'),
    '3596': ('智易',     '網通'),
    '6285': ('啟碁',     '網通'),
}


def run_optical_scan():
    print(f"\n{'='*78}")
    print(f"  光通訊族群專屬掃描  |  {datetime.now():%Y-%m-%d %H:%M}")
    print(f"  策略：MA({MA_SHORT}/{MA_LONG}) + RSI({RSI_PERIOD}) 雙重確認")
    print(f"  驗證：Walk-Forward CV ({CV_FOLDS} 折，~500 日資料)")
    print(f"{'='*78}\n")

    rows = []
    for sid, (name, subsector) in OPTICAL_COMM.items():
        print(f"  分析中... {sid} {name} ({subsector})", end='\r')
        r = analyze_stock(sid, name)
        r['subsector'] = subsector
        rows.append(r)

    ok = [r for r in rows if 'error' not in r]
    err = [r for r in rows if 'error' in r]

    df = pd.DataFrame(ok)
    if df.empty:
        print("\n沒有可用資料")
        return

    # ── 相對強弱：近 20 日報酬排名 ─────────────────────────────────────────
    def recent_perf(sid):
        try:
            from twstock import Stock
            from datetime import datetime, timedelta
            s = Stock(sid)
            start = datetime.now() - timedelta(days=60)
            s.fetch_from(start.year, start.month)
            p = s.price
            if len(p) < 21:
                return None
            return (p[-1] - p[-21]) / p[-21]
        except Exception:
            return None

    df['ret_20d'] = df['id'].map(recent_perf)

    # ── 排序：依 CV 夏普 ────────────────────────────────────────────────────
    df = df.sort_values('cv_sharpe', ascending=False)

    signal_map = {'BUY': '★買', 'SELL': '▼賣', 'HOLD': '─持'}
    df['sig'] = df['signal'].map(signal_map)

    # ── 主表 ──────────────────────────────────────────────────────────────
    print(f"\n{'─'*78}")
    print(f"{'代碼':<6}{'名稱':<10}{'子類別':<10}"
          f"{'現價':>7}{'RSI':>6}{'20日%':>7}"
          f"{'信號':<5}{'CV夏普':>7}{'CV勝率':>7}{'CV回撤':>7}")
    print(f"{'─'*78}")
    for _, r in df.iterrows():
        ret20 = f"{r['ret_20d']*100:+.1f}" if r['ret_20d'] is not None else '  N/A'
        print(f"{r['id']:<6}{r['name']:<10}{r['subsector']:<10}"
              f"{r['price']:>7.1f}{r['rsi']:>6.1f}{ret20:>7}"
              f"{r['sig']:<5}{r['cv_sharpe']:>7.2f}"
              f"{r['cv_win_rate']:>7.1%}{r['cv_max_dd']:>7.1%}")
    print(f"{'─'*78}")

    # ── 子類別平均強度 ─────────────────────────────────────────────────────
    sub_avg = df.groupby('subsector').agg(
        平均RSI=('rsi', 'mean'),
        平均20日報酬=('ret_20d', 'mean'),
        平均CV夏普=('cv_sharpe', 'mean'),
        檔數=('id', 'count'),
    ).sort_values('平均20日報酬', ascending=False)

    print(f"\n  【子類別相對強弱】")
    print(f"  {'─'*64}")
    for sub, row in sub_avg.iterrows():
        ret = row['平均20日報酬']
        ret_str = f"{ret*100:+5.1f}%" if pd.notna(ret) else '  N/A'
        print(f"  {sub:<12} 檔數={int(row['檔數'])}  "
              f"平均RSI={row['平均RSI']:.1f}  "
              f"20日={ret_str}  "
              f"CV夏普={row['平均CV夏普']:.2f}")

    # ── 分析結論 ──────────────────────────────────────────────────────────
    print(f"\n  【重點分析】")
    print(f"  {'─'*64}")

    hot    = df[df['rsi'] > 70]
    cool   = df[df['rsi'] < 50]
    buy    = df[df['signal'] == 'BUY']
    qualified = df[(df['cv_sharpe'] >= 0.3) & (df['cv_win_rate'] >= 0.40)]

    print(f"  過熱區（RSI>70）：{len(hot)} 檔 — "
          f"{', '.join(hot['name'].tolist()) if len(hot) else '無'}")
    print(f"  冷卻區（RSI<50）：{len(cool)} 檔 — "
          f"{', '.join(cool['name'].tolist()) if len(cool) else '無'}")
    print(f"  當前買進訊號　  ：{len(buy)} 檔 — "
          f"{', '.join(buy['name'].tolist()) if len(buy) else '無'}")
    print(f"  CV 歷史達標　　 ：{len(qualified)} 檔 — "
          f"{', '.join(qualified['name'].tolist()) if len(qualified) else '無'}")

    # ── 最終推薦 ──────────────────────────────────────────────────────────
    final = df[
        (df['signal'] == 'BUY') &
        (df['cv_sharpe'] >= 0.3) &
        (df['cv_win_rate'] >= 0.40) &
        (df['cv_max_dd'] <= 0.20)
    ]

    print(f"\n  【今日推薦（雙條件達標）】")
    print(f"  {'─'*64}")
    if final.empty:
        print("  目前沒有光通訊族群同時通過訊號與 CV 條件的標的。")
        # 給最接近達標的
        close = df[df['signal'] == 'BUY'].head(3)
        if not close.empty:
            print("\n  有買進訊號但 CV 未達標（短線參考）：")
            for _, r in close.iterrows():
                print(f"    • {r['id']} {r['name']}  CV 夏普={r['cv_sharpe']:.2f}  勝率={r['cv_win_rate']:.1%}")
    else:
        for _, r in final.iterrows():
            print(f"  ✅ {r['id']} {r['name']} ({r['subsector']})")
            print(f"     現價 {r['price']:.1f}  RSI {r['rsi']:.1f}  "
                  f"CV夏普 {r['cv_sharpe']:.2f}  勝率 {r['cv_win_rate']:.1%}")

    if err:
        print(f"\n  ⚠ 資料失敗：{', '.join(r['id']+' '+r['name'] for r in err)}")

    print(f"\n{'='*78}\n")

    df.to_csv('optical_comm_scan.csv', index=False, encoding='utf-8-sig')
    print(f"  結果已儲存至 optical_comm_scan.csv\n")
    return df


if __name__ == '__main__':
    run_optical_scan()
