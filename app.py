"""
台股族群掃描系統 — Web UI
Streamlit app：讀取 daily_reports/ 下的 JSON，呈現每日掃描 / 週報 / 個股查詢 / 歷史趨勢
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import math

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

@st.cache_data(ttl=3600)
def _calc_target(sid: str, price: float):
    """即時計算短/中/長期目標價（JSON 無值時 fallback）

    短期：布林上軌（技術壓力位）
    中期：P/E 均值回歸（免費 FinMind；不可用時改 P/B）
    長期：40% P/E + 40% PEG + 20% 技術 加權（依估值指南結論）
    """
    try:
        import statistics as _stat
        from twstock import Stock as _S
        from datetime import datetime as _dt2, timedelta as _td2, date as _date
        _s = _S(sid)
        _start = _dt2.now() - _td2(days=120)
        _s.fetch_from(_start.year, _start.month)
        _p = list(_s.price)
        if len(_p) < 20:
            return "—", "—", "—"
        _price = _p[-1]
        _p20 = _p[-20:]
        _ma20 = sum(_p20) / 20
        _sig = _stat.stdev(_p20)
        _t_short = round(_ma20 + 2 * _sig, 1)

        # 中期 + 長期：P/E 均值回歸 + PEG
        try:
            from FinMind.data import DataLoader as _DL
            _dl = _DL()
            _one_yr = (_date.today() - _td2(days=365)).strftime("%Y-%m-%d")
            _per = _dl.taiwan_stock_per_pbr(stock_id=sid, start_date=_one_yr)
            if _per.empty:
                raise ValueError
            _vpe = _per[_per["PER"] > 0]["PER"]
            _vpb = _per[_per["PBR"] > 0]["PBR"]
            _cpe = float(_per.iloc[-1]["PER"])
            _cpb = float(_per.iloc[-1]["PBR"])
            _mpe = float(_vpe.median()) if not _vpe.empty else 0
            _mpb = float(_vpb.median()) if not _vpb.empty else 0

            if _cpe > 0 and _mpe > 0:
                _t_mid = round(_price * max(0.85, min(_mpe / _cpe, 1.5)), 1)
            elif _cpb > 0 and _mpb > 0:
                _t_mid = round(_price * max(0.85, min(_mpb / _cpb, 1.5)), 1)
            else:
                _hi60 = max(_p[-60:]) if len(_p) >= 60 else max(_p)
                _t_mid = round(_hi60 * 1.03, 1)

            _pe_tgt = _price * max(0.85, min(_mpe / _cpe, 1.5)) if _cpe > 0 and _mpe > 0 else _price * 1.10
            _peg_g = min(_cpe, 30) / 100 if _cpe > 0 else 0.10
            _peg_tgt = _price * (1 + _peg_g)
            _t_long = round(0.4 * _pe_tgt + 0.4 * _peg_tgt + 0.2 * _t_short, 1)
        except Exception:
            _hi60 = max(_p[-60:]) if len(_p) >= 60 else max(_p)
            _t_mid = round(_hi60 * 1.03, 1)
            _dr = [(_p[i] - _p[i-1]) / _p[i-1] for i in range(1, len(_p))]
            _vol = _stat.stdev(_dr[-20:]) if len(_dr) >= 20 else 0.02
            _t_long = round(price * (1 + _vol * 15), 1)

        return _t_short, _t_mid, _t_long
    except Exception:
        return "—", "—", "—"


from concepts_data import (
    CONCEPTS,
    build_graph_edges,
    related_stocks,
    stock_name_lookup,
)
from qlib_factors import FACTOR_FUNCS, compute_all_factors, factor_description

# ── 頁面設定 ────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="台股族群掃描",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── 資料讀取 ────────────────────────────────────────────────────────────────

def load_latest_daily():
    base = Path("daily_reports")
    if not base.exists():
        return None, None
    dates = sorted(
        [d.name for d in base.iterdir() if d.is_dir() and d.name.isdigit() and len(d.name) == 8],
        reverse=True,
    )
    for date in dates:
        p = base / date / "summary.json"
        if p.exists():
            return date, json.loads(p.read_text(encoding="utf-8"))
    return None, None


def load_daily(date_str):
    p = Path(f"daily_reports/{date_str}/summary.json")
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_latest_weekly():
    base = Path("daily_reports")
    if not base.exists():
        return None, None
    weeklies = sorted(
        [d.name for d in base.iterdir() if d.is_dir() and d.name.startswith("weekly_")],
        reverse=True,
    )
    for w in weeklies:
        p = base / w / "weekly.json"
        if p.exists():
            return w, json.loads(p.read_text(encoding="utf-8"))
    return None, None


def all_daily_dates():
    base = Path("daily_reports")
    if not base.exists():
        return []
    return sorted(
        [d.name for d in base.iterdir() if d.is_dir() and d.name.isdigit() and len(d.name) == 8],
    )


# ── 色彩輔助 ────────────────────────────────────────────────────────────────

def signal_badge(signal):
    colors = {"BUY": "🟢", "SELL": "🔴", "HOLD": "🟡"}
    return colors.get(signal, "⚪") + " " + signal


def rsi_color(rsi):
    if rsi > 70:
        return "🔴"
    if rsi > 60:
        return "🟠"
    if rsi < 30:
        return "🟢"
    return "⚪"


# ── 族群強弱圖（Plotly） ────────────────────────────────────────────────────

def sector_chart(summary):
    sectors = summary.get("sectors", {})
    rows = []
    for name, data in sectors.items():
        rows.append(
            {
                "族群": name,
                "20日報酬%": data["avg_ret_20d"],
                "RSI": data["avg_rsi"],
                "BUY訊號": data["buy_count"],
            }
        )
    if not rows:
        return None, None
    df = pd.DataFrame(rows).sort_values("20日報酬%")

    fig_ret = px.bar(
        df,
        x="20日報酬%",
        y="族群",
        orientation="h",
        color="20日報酬%",
        color_continuous_scale=["#e74c3c", "#f39c12", "#27ae60"],
        title="族群 20 日平均報酬",
    )
    fig_ret.update_layout(
        coloraxis_showscale=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#fafafa",
        height=320,
        margin=dict(l=0, r=20, t=40, b=0),
    )
    fig_ret.add_vline(x=0, line_color="white", line_width=0.5)

    rsi_colors = [
        "#e74c3c" if r > 70 else "#f39c12" if r > 60 else "#27ae60" if r >= 40 else "#3498db"
        for r in df["RSI"]
    ]
    fig_rsi = go.Figure(
        go.Bar(x=df["RSI"], y=df["族群"], orientation="h", marker_color=rsi_colors)
    )
    fig_rsi.update_layout(
        title="族群 RSI 熱度",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#fafafa",
        height=320,
        margin=dict(l=0, r=20, t=40, b=0),
        xaxis=dict(range=[0, 100]),
    )
    fig_rsi.add_vline(x=70, line_color="red", line_dash="dash", line_width=0.8)
    fig_rsi.add_vline(x=30, line_color="green", line_dash="dash", line_width=0.8)

    return fig_ret, fig_rsi


# ── Tab 1：今日掃描 ─────────────────────────────────────────────────────────

def tab_daily():
    date_str, summary = load_latest_daily()
    if not summary:
        st.warning("尚無掃描資料，請先執行 07_daily_scan.py")
        return

    d = date_str
    st.subheader(f"📊 每日掃描　{d[:4]}-{d[4:6]}-{d[6:]}")

    # 大盤（加權指數：優先讀 JSON，讀不到就即時抓 FinMind）
    mkt = summary.get("market", {})
    c1, c2, c3, c4 = st.columns(4)
    idx = mkt.get("加權指數")
    chg = mkt.get("漲跌幅")
    if not idx:
        try:
            from FinMind.data import DataLoader as _DL
            from datetime import datetime as _dt, timedelta as _td
            _dl = _DL()
            for _offset in range(5):
                _d = (_dt.now() - _td(days=_offset)).strftime("%Y-%m-%d")
                _df = _dl.tse(_d)
                if not _df.empty:
                    idx = round(float(_df["TAIEX"].iloc[-1]), 0)
                    _prev = float(_df["TAIEX"].iloc[0])
                    chg = round((idx - _prev) / _prev * 100, 2) if _prev else None
                    break
        except Exception:
            pass
    c1.metric("加權指數", f"{idx:,.0f}" if idx else "—", f"{chg:+.2f}%" if chg else None)
    c2.metric("強勢族群", "、".join(summary.get("strong_sectors", [])) or "無")
    c3.metric("弱勢族群", "、".join(summary.get("weak_sectors", [])) or "無")
    c4.metric("雙條件推薦", f"{len(summary.get('qualified', []))} 檔")

    st.divider()

    # 族群圖
    fig_ret, fig_rsi = sector_chart(summary)
    if fig_ret:
        col1, col2 = st.columns(2)
        col1.plotly_chart(fig_ret, use_container_width=True)
        col2.plotly_chart(fig_rsi, use_container_width=True)

    st.divider()

    # 籌碼面摘要
    chip_rows = []
    for sector, data in summary.get("sectors", {}).items():
        for st_ in data.get("stocks", []):
            chip = st_.get("chip", {})
            total = chip.get("合計", 0)
            if total != 0:
                chip_rows.append(
                    {
                        "代碼": st_["id"],
                        "名稱": st_["name"],
                        "族群": sector,
                        "外資": chip.get("外資", 0),
                        "投信": chip.get("投信", 0),
                        "自營": chip.get("自營", 0),
                        "合計": total,
                    }
                )
    if chip_rows:
        df_chip = pd.DataFrame(chip_rows).sort_values("合計", ascending=False)
        st.markdown("#### 籌碼面：三大法人")
        col1, col2 = st.columns(2)
        top = df_chip[df_chip["合計"] > 0].head(5)
        bot = df_chip[df_chip["合計"] < 0].tail(5)
        if not top.empty:
            col1.markdown("**▲ 買超前段**")
            col1.dataframe(top.set_index("代碼"), use_container_width=True)
        if not bot.empty:
            col2.markdown("**▼ 賣超前段**")
            col2.dataframe(bot.set_index("代碼"), use_container_width=True)
        st.divider()

    # 風險警示
    alerts = summary.get("alerts", [])
    if alerts:
        with st.expander(f"⚠️ 風險警示（{len(alerts)} 筆）"):
            for a in alerts:
                st.write(f"**[{a['sector']}]** {a['id']} {a['name']} — {a['type']} {a['detail']}")

    # 各族群明細
    st.markdown("#### 各族群明細")
    for sector, data in summary.get("sectors", {}).items():
        label = (
            f"**{sector}**　20日 {data['avg_ret_20d']:+.1f}%　"
            f"RSI {data['avg_rsi']:.1f}　BUY {data['buy_count']} 檔"
        )
        with st.expander(label):
            rows = []
            buy_rows = []
            for s in data.get("stocks", []):
                chip = s.get("chip", {})
                chip_str = (
                    f"外{chip.get('外資',0):+,} 投{chip.get('投信',0):+,} 自{chip.get('自營',0):+,}"
                    if chip
                    else "—"
                )
                news = s.get("news", [])
                news_str = " ／ ".join(f"[{n['source']}]{n['title']}" for n in news[:2]) or "—"
                row = {
                    "代碼": s["id"],
                    "名稱": s["name"],
                    "現價": s["price"],
                    "RSI": s["rsi"],
                    "20日%": s.get("ret_20d"),
                    "訊號": s["signal"],
                    "CV夏普": s["cv_sharpe"],
                    "三大法人": chip_str,
                    "近期新聞": news_str,
                }
                rows.append(row)
                if s.get("signal") == "BUY":
                    buy_rows.append({
                        "代碼": s["id"],
                        "名稱": s["name"],
                        "現價": s["price"],
                        "短期目標(技術壓力)": s.get("target_short") or _calc_target(s["id"], s["price"])[0],
                        "中期目標(P/E回歸)": s.get("target_mid") or _calc_target(s["id"], s["price"])[1],
                        "長期目標(P/E+PEG加權)": s.get("target_long") or _calc_target(s["id"], s["price"])[2],
                    })
            df = pd.DataFrame(rows)
            st.dataframe(df.set_index("代碼"), use_container_width=True)
            if buy_rows:
                st.markdown("##### 📍 BUY 訊號目標價（技術推估，非投資建議）")
                df_buy = pd.DataFrame(buy_rows)
                st.dataframe(df_buy.set_index("代碼"), use_container_width=True)


# ── Tab 2：週報 ─────────────────────────────────────────────────────────────

def tab_weekly():
    wkey, summary = load_latest_weekly()
    if not summary:
        st.warning("尚無週報資料，請先執行 08_weekly_summary.py")
        return

    st.subheader(f"📅 週報　{summary.get('week_ending', '')}　（涵蓋 {summary.get('days_covered', 0)} 個交易日）")

    changes = summary.get("sector_changes", [])
    top_buys = summary.get("top_buys", [])

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("#### 族群動能變化")
        if changes:
            df_c = pd.DataFrame(changes)
            fig = px.bar(
                df_c,
                x="change",
                y="sector",
                orientation="h",
                color="change",
                color_continuous_scale=["#e74c3c", "#f39c12", "#27ae60"],
                labels={"change": "動能變化 (pp)", "sector": "族群"},
            )
            fig.update_layout(
                coloraxis_showscale=False,
                plot_bgcolor="rgba(0,0,0,0)",
                paper_bgcolor="rgba(0,0,0,0)",
                font_color="#fafafa",
                height=300,
                margin=dict(l=0, r=20, t=10, b=0),
            )
            fig.add_vline(x=0, line_color="white", line_width=0.5)
            st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.markdown("#### 本週累計 BUY 訊號 Top 10")
        if top_buys:
            df_b = pd.DataFrame(top_buys)
            df_b.columns = ["個股", "BUY次數"]
            st.dataframe(df_b.set_index("個股"), use_container_width=True)


# ── Tab 3：個股分析 ──────────────────────────────────────────────────────────

def tab_stock():
    st.subheader("🔍 個股即時分析")
    st.caption("輸入股票代碼，即時拉取技術 + 籌碼 + 基本面資料")

    col1, col2 = st.columns([1, 3])
    with col1:
        stock_id = st.text_input("股票代碼", placeholder="例：5292")
        run = st.button("開始分析", type="primary")

    if not run or not stock_id.strip():
        return

    sid = stock_id.strip()
    with st.spinner(f"分析 {sid} 中..."):
        try:
            # 技術面
            from twstock import Stock
            from datetime import datetime, timedelta

            s = Stock(sid)
            fetch_start = datetime.now() - timedelta(days=60)
            s.fetch_from(fetch_start.year, fetch_start.month)
            price = s.price[-1] if s.price else None
            ma5  = round(sum(s.price[-5:]) / 5, 1) if len(s.price) >= 5 else None
            ma20 = round(sum(s.price[-20:]) / 20, 1) if len(s.price) >= 20 else None
            ret20 = round((s.price[-1] - s.price[-21]) / s.price[-21] * 100, 2) if len(s.price) >= 21 else None

            # RSI
            import numpy as np
            prices = list(s.price)
            diffs = [prices[i] - prices[i-1] for i in range(1, len(prices))]
            gains = [max(d, 0) for d in diffs[-14:]]
            losses = [abs(min(d, 0)) for d in diffs[-14:]]
            avg_gain = sum(gains) / 14 if gains else 0
            avg_loss = sum(losses) / 14 if losses else 1
            rsi = round(100 - 100 / (1 + avg_gain / avg_loss), 1) if avg_loss else 50

            tech_ok = True
        except Exception as e:
            st.error(f"技術面資料取得失敗：{e}")
            tech_ok = False

        try:
            from FinMind.data import DataLoader
            dl = DataLoader()
            end = datetime.now().strftime("%Y-%m-%d")

            # 公司名稱
            info = dl.taiwan_stock_info()
            name_row = info[info["stock_id"] == sid]
            company_name = name_row["stock_name"].values[0] if not name_row.empty else sid
            industry = name_row["industry_category"].values[0] if not name_row.empty else "—"

            # EPS
            eps_df = dl.taiwan_stock_financial_statement(stock_id=sid, start_date="2023-01-01", end_date=end)
            eps_list = []
            if not eps_df.empty and "type" in eps_df.columns:
                eps_only = eps_df[eps_df["type"] == "EPS"].sort_values("date", ascending=False)
                for _, r in eps_only.head(8).iterrows():
                    eps_list.append({"季度": str(r["date"])[:10], "EPS": r["value"]})

            # 月營收
            rev_df = dl.taiwan_stock_month_revenue(stock_id=sid, start_date="2025-01-01", end_date=end)
            rev_list = []
            if not rev_df.empty:
                rev_df = rev_df.sort_values("date", ascending=False)
                for _, r in rev_df.head(12).iterrows():
                    rev_list.append({
                        "月份": str(r["date"])[:7],
                        "月營收(億)": round(r.get("revenue", 0) / 1e8, 2),
                        "YoY%": round(r.get("year_month_revenue_growth_ratio", 0), 1) if r.get("year_month_revenue_growth_ratio") else None,
                    })

            # 三大法人（FinMind 欄位為英文，需對應）
            _CHIP_MAP = {
                "Foreign_Investor": "外資", "Foreign_Dealer_Self": "外資",
                "Investment_Trust": "投信",
                "Dealer_self": "自營", "Dealer_Hedging": "自營",
            }
            chip_df = dl.taiwan_stock_institutional_investors(
                stock_id=sid,
                start_date=(datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d"),
                end_date=end,
            )
            chip_list = []
            if not chip_df.empty:
                chip_df = chip_df.sort_values("date", ascending=False)
                for date in chip_df["date"].unique()[:10]:
                    day = chip_df[chip_df["date"] == date]
                    row = {"日期": str(date), "外資": 0, "投信": 0, "自營": 0}
                    for _, r in day.iterrows():
                        zh = _CHIP_MAP.get(r.get("name", ""))
                        if zh:
                            row[zh] += int(r.get("buy", 0) - r.get("sell", 0))
                    row["合計"] = row["外資"] + row["投信"] + row["自營"]
                    chip_list.append(row)

            # 融資融券
            margin_df = dl.taiwan_stock_margin_purchase_short_sale(
                stock_id=sid,
                start_date=(datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d"),
                end_date=end,
            )
            margin_list = []
            if not margin_df.empty:
                margin_df = margin_df.sort_values("date", ascending=False)
                for _, r in margin_df.head(10).iterrows():
                    融資 = int(r.get("MarginPurchaseBuy", 0) - r.get("MarginPurchaseSell", 0) - r.get("MarginPurchaseCashRepayment", 0))
                    融券 = int(r.get("ShortSaleSell", 0) - r.get("ShortSaleBuy", 0) - r.get("ShortSaleStockRepayment", 0))
                    margin_list.append({"日期": str(r["date"]), "融資增減(張)": 融資, "融券增減(張)": 融券})

            fm_ok = True
        except Exception as e:
            st.error(f"FinMind 資料取得失敗：{e}")
            fm_ok = False

        # 個股新聞（鉅亨網，免費）
        news_list = []
        try:
            import urllib.request as _ureq, json as _json
            _url = (f"https://api.cnyes.com/media/api/v1/newslist/category/tw_stock"
                    f"?limit=5&stock_code={sid}")
            _req = _ureq.Request(_url, headers={"User-Agent": "Mozilla/5.0"})
            with _ureq.urlopen(_req, timeout=8) as _r:
                _d = _json.loads(_r.read())
            for it in _d.get("items", {}).get("data", [])[:5]:
                import datetime as _dt2
                pub = _dt2.datetime.fromtimestamp(it.get("publishAt", 0))
                news_list.append({
                    "日期": pub.strftime("%Y-%m-%d"),
                    "標題": it.get("title", ""),
                    "來源": it.get("media", {}).get("name", "鉅亨網"),
                })
        except Exception:
            pass

    if tech_ok:
        ttm_eps = sum(r["EPS"] for r in eps_list[:4]) if len(eps_list) >= 4 else None
        per = round(price / ttm_eps, 1) if ttm_eps and price else None

        st.markdown(f"## {company_name}（{sid}）　{industry}")
        st.divider()

        # 技術面
        st.markdown("### 技術面")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("現價", price)
        c2.metric("MA5", ma5, delta=round(price - ma5, 1) if ma5 else None)
        c3.metric("MA20", ma20, delta=round(price - ma20, 1) if ma20 else None)
        c4.metric("RSI(14)", rsi)
        c5.metric("20日報酬", f"{ret20:+.2f}%" if ret20 else "—")

        # 目標價（技術面推估）
        import statistics as _stat
        prices_list = list(s.price)
        if len(prices_list) >= 20:
            p20 = prices_list[-20:]
            _ma20 = sum(p20) / 20
            _sigma = _stat.stdev(p20)
            _t_short = round(_ma20 + 2 * _sigma, 1)
            _hi60 = max(prices_list[-60:]) if len(prices_list) >= 60 else max(prices_list)
            _t_mid = round(_hi60 * 1.03, 1)
            _daily_rets = [(prices_list[i] - prices_list[i-1]) / prices_list[i-1]
                           for i in range(1, len(prices_list))]
            _vol = _stat.stdev(_daily_rets[-20:]) if len(_daily_rets) >= 20 else 0.02
            _t_long = round(price * (1 + _vol * 15), 1)
            st.markdown("**📍 技術目標價（僅供參考，非投資建議）**")
            tc1, tc2, tc3 = st.columns(3)
            tc1.metric("短期（布林上軌）", f"{_t_short:,}", delta=f"{(_t_short/price-1)*100:+.1f}%")
            tc2.metric("中期（60日高×1.03）", f"{_t_mid:,}", delta=f"{(_t_mid/price-1)*100:+.1f}%")
            tc3.metric("長期（1σ × 15日）", f"{_t_long:,}", delta=f"{(_t_long/price-1)*100:+.1f}%")

        # 新聞
        if news_list:
            st.markdown("### 📰 近期新聞（鉅亨網）")
            for n in news_list:
                st.markdown(f"- `{n['日期']}` [{n['標題']}] — {n['來源']}")

        if fm_ok:
            st.divider()
            col1, col2 = st.columns(2)

            # 基本面
            with col1:
                st.markdown("### 基本面")
                if ttm_eps:
                    st.metric("TTM EPS", f"{ttm_eps:.2f} 元", help="近 4 季合計")
                if per:
                    st.metric("本益比 (PER)", f"{per}x")
                if eps_list:
                    st.markdown("**EPS 近 8 季**")
                    st.dataframe(pd.DataFrame(eps_list).set_index("季度"), use_container_width=True)
                if rev_list:
                    st.markdown("**月營收**")
                    df_rev = pd.DataFrame(rev_list)
                    fig_rev = px.bar(
                        df_rev.sort_values("月份"),
                        x="月份",
                        y="月營收(億)",
                        color_discrete_sequence=["#1f77b4"],
                    )
                    fig_rev.update_layout(
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font_color="#fafafa",
                        height=250,
                        margin=dict(l=0, r=0, t=10, b=0),
                    )
                    st.plotly_chart(fig_rev, use_container_width=True)

            # 籌碼面
            with col2:
                st.markdown("### 籌碼面")
                if chip_list:
                    st.markdown("**三大法人買賣超（近 10 日）**")
                    df_chip = pd.DataFrame(chip_list).set_index("日期")
                    st.dataframe(df_chip, use_container_width=True)
                    # 累計趨勢
                    df_chip_plot = pd.DataFrame(chip_list).sort_values("日期")
                    fig_chip = go.Figure()
                    for col in ["外資", "投信", "自營"]:
                        fig_chip.add_trace(go.Bar(name=col, x=df_chip_plot["日期"], y=df_chip_plot[col]))
                    fig_chip.update_layout(
                        barmode="relative",
                        plot_bgcolor="rgba(0,0,0,0)",
                        paper_bgcolor="rgba(0,0,0,0)",
                        font_color="#fafafa",
                        height=220,
                        margin=dict(l=0, r=0, t=10, b=0),
                        legend=dict(orientation="h", y=1.1),
                    )
                    st.plotly_chart(fig_chip, use_container_width=True)
                else:
                    st.info("法人資料無")

                if margin_list:
                    st.markdown("**融資融券增減（近 10 日）**")
                    df_mg = pd.DataFrame(margin_list).set_index("日期")
                    st.dataframe(df_mg, use_container_width=True)


# ── Tab 4：歷史趨勢 ──────────────────────────────────────────────────────────

def tab_history():
    st.subheader("📈 族群歷史趨勢")
    dates = all_daily_dates()
    if len(dates) < 2:
        st.info("需要至少 2 份日報才能顯示趨勢")
        return

    sector_trend: dict[str, list] = {}
    date_labels = []
    for d in dates[-20:]:
        data = load_daily(d)
        if not data:
            continue
        date_labels.append(f"{d[:4]}-{d[4:6]}-{d[6:]}")
        for sector, sdata in data.get("sectors", {}).items():
            sector_trend.setdefault(sector, []).append(sdata["avg_ret_20d"])

    fig = go.Figure()
    for sector, vals in sector_trend.items():
        fig.add_trace(go.Scatter(
            x=date_labels[-len(vals):],
            y=vals,
            mode="lines+markers",
            name=sector,
            line=dict(width=2),
        ))
    fig.add_hline(y=0, line_color="white", line_width=0.5)
    fig.update_layout(
        title="各族群 20 日平均報酬走勢",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font_color="#fafafa",
        height=450,
        legend=dict(orientation="h", y=-0.2),
        margin=dict(l=0, r=0, t=40, b=0),
        yaxis_title="20日報酬 (%)",
    )
    st.plotly_chart(fig, use_container_width=True)


# ── Tab 5：概念股關聯（Knowledge Graph 輕量版） ─────────────────────────────

def _graph_figure(nodes, edges):
    """用 plotly 畫概念股關聯網路圖（circular 佈局，中心點在原點）"""
    pos = {}
    concept_nodes = [n for n in nodes if n["type"] == "concept"]
    stock_nodes = [n for n in nodes if n["type"] == "stock"]
    center = [n for n in nodes if n["type"] == "center"][0]

    pos[center["id"]] = (0, 0)
    n_c = max(len(concept_nodes), 1)
    for i, n in enumerate(concept_nodes):
        ang = 2 * math.pi * i / n_c
        pos[n["id"]] = (math.cos(ang) * 1.0, math.sin(ang) * 1.0)

    # 股票節點依其所屬概念，繞在該概念旁邊
    concept_to_stocks: dict[str, list[str]] = {}
    for e in edges:
        if e["source"].startswith("C::"):
            concept_to_stocks.setdefault(e["source"], []).append(e["target"])
    for cid, sids in concept_to_stocks.items():
        cx, cy = pos[cid]
        k = len(sids)
        for j, sid in enumerate(sids):
            ang = 2 * math.pi * j / max(k, 1)
            pos[sid] = (cx + math.cos(ang) * 0.45, cy + math.sin(ang) * 0.45)

    # edges
    edge_x, edge_y = [], []
    for e in edges:
        x0, y0 = pos[e["source"]]
        x1, y1 = pos[e["target"]]
        edge_x += [x0, x1, None]
        edge_y += [y0, y1, None]

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=edge_x, y=edge_y, mode="lines",
                             line=dict(color="rgba(180,180,180,0.35)", width=1),
                             hoverinfo="none"))

    for typ, color, size in [("stock", "#3498db", 14), ("concept", "#e67e22", 22), ("center", "#27ae60", 32)]:
        xs, ys, labels = [], [], []
        for n in nodes:
            if n["type"] != typ:
                continue
            x, y = pos[n["id"]]
            xs.append(x); ys.append(y); labels.append(n["label"])
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="markers+text",
            marker=dict(color=color, size=size, line=dict(color="white", width=1)),
            text=labels, textposition="top center",
            textfont=dict(color="#fafafa", size=11),
            hovertext=labels, hoverinfo="text", name=typ,
        ))

    fig.update_layout(
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(visible=False),
        yaxis=dict(visible=False),
        height=560,
        margin=dict(l=0, r=0, t=10, b=0),
    )
    return fig


def tab_concepts():
    st.subheader("🕸️ 概念股 / 產業關聯")
    st.caption("靈感來源：Taiwan-Stock-Knowledge-Graph —— 用輕量對照表呈現同概念股關聯")

    names = stock_name_lookup()
    all_concepts = sorted(CONCEPTS.keys())

    mode = st.radio("檢視方式", ["以個股為中心", "瀏覽單一概念"], horizontal=True)

    if mode == "以個股為中心":
        default_ids = sorted(names.keys())
        sel = st.selectbox("選擇股票（已收錄於概念庫）", default_ids,
                           format_func=lambda sid: f"{sid} {names[sid]}")
        rel = related_stocks(sel)
        if not rel:
            st.info("此股票尚未分類到任何概念")
            return
        st.markdown(f"### {sel} {names[sel]} — 所屬 {len(rel)} 個概念")

        nodes, edges = build_graph_edges(sel)
        st.plotly_chart(_graph_figure(nodes, edges), use_container_width=True)

        st.markdown("#### 同概念個股清單")
        for concept, peers in rel.items():
            with st.expander(f"🏷️ {concept}（{len(peers)} 檔）"):
                df = pd.DataFrame(peers, columns=["代碼", "名稱"])
                st.dataframe(df.set_index("代碼"), use_container_width=True)

    else:
        concept = st.selectbox("選擇概念", all_concepts)
        stocks = CONCEPTS[concept]
        st.markdown(f"### {concept} — {len(stocks)} 檔成分")
        df = pd.DataFrame(stocks, columns=["代碼", "名稱"])
        st.dataframe(df.set_index("代碼"), use_container_width=True)
        st.caption("💡 可切回「以個股為中心」模式，查看任一檔的跨概念關聯")


# ── Tab 6：量化因子研究（Qlib 風格） ─────────────────────────────────────────

@st.cache_data(ttl=3600)
def _fetch_ohlcv(stock_id: str, days: int = 120):
    """用 FinMind 抓 OHLCV，回傳 pandas DataFrame"""
    from FinMind.data import DataLoader
    from datetime import datetime, timedelta
    dl = DataLoader()
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y-%m-%d")  # 預留日曆日
    df = dl.taiwan_stock_daily(stock_id=stock_id, start_date=start, end_date=end)
    if df.empty:
        return None
    df = df.sort_values("date").rename(columns={
        "max": "high", "min": "low", "Trading_Volume": "volume"
    })
    # FinMind 欄位: open, max, min, close, Trading_Volume, spread...
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.tail(days)


def tab_factors():
    st.subheader("🔬 量化因子研究（Qlib 風格）")
    st.caption("靈感來源：Microsoft Qlib Alpha158 —— 在不安裝 Qlib 的前提下自行實作關鍵 alpha 因子")

    c1, c2 = st.columns([1, 3])
    with c1:
        sid = st.text_input("股票代碼", placeholder="例：2330")
        run = st.button("計算因子", type="primary")

    if not run or not sid.strip():
        with st.expander("📚 因子說明"):
            for k, v in factor_description().items():
                st.markdown(f"- **{k}** — {v}")
        return

    sid = sid.strip()
    with st.spinner(f"抓取 {sid} 歷史資料並計算因子..."):
        df = _fetch_ohlcv(sid, days=120)
        if df is None or df.empty:
            st.error("取不到此股票的歷史資料")
            return
        factors = compute_all_factors(df)

    names = stock_name_lookup()
    display_name = names.get(sid, sid)
    st.markdown(f"### {sid} {display_name}")

    # 因子值表格
    rows = []
    for k, v in factors.items():
        if v is None:
            rows.append({"因子": k, "值": "—"})
        elif "RSI" in k or "VR" in k:
            rows.append({"因子": k, "值": f"{v:.2f}"})
        else:
            rows.append({"因子": k, "值": f"{v:.4f}"})
    df_f = pd.DataFrame(rows)

    col1, col2 = st.columns([1, 1])
    with col1:
        st.markdown("#### 因子值")
        st.dataframe(df_f.set_index("因子"), use_container_width=True)

    # 雷達圖（以一組合理的基準正規化到 [-1, 1]）
    def _norm(name: str, v: float | None) -> float:
        if v is None:
            return 0
        if "MOM" in name: return max(-1, min(1, v / 0.3))
        if "REV" in name: return max(-1, min(1, v / 0.15))
        if "VOL20" in name: return max(-1, min(1, 1 - v / 0.6))
        if "RSI" in name: return max(-1, min(1, (v - 50) / 50))
        if "BIAS" in name: return max(-1, min(1, v / 0.15))
        if "VR" in name: return max(-1, min(1, (v - 1) / 2))
        if "POS" in name: return max(-1, min(1, (v - 0.5) * 2))
        if "MA5/MA20" in name: return max(-1, min(1, v / 0.1))
        if "MDD" in name: return max(-1, min(1, 1 + v / 0.3))  # v 為負
        return 0

    with col2:
        st.markdown("#### 因子雷達圖（正規化）")
        labels = list(factors.keys())
        values = [_norm(k, v) for k, v in factors.items()]
        fig = go.Figure(go.Scatterpolar(
            r=values + [values[0]], theta=labels + [labels[0]],
            fill="toself", line=dict(color="#3498db"),
        ))
        fig.update_layout(
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                radialaxis=dict(range=[-1, 1], tickfont=dict(color="#fafafa")),
                angularaxis=dict(tickfont=dict(color="#fafafa")),
            ),
            paper_bgcolor="rgba(0,0,0,0)",
            font_color="#fafafa",
            height=380,
            margin=dict(l=40, r=40, t=20, b=20),
            showlegend=False,
        )
        st.plotly_chart(fig, use_container_width=True)

    # 全市場排名（用概念庫的股票當 universe）
    st.divider()
    st.markdown("#### 📊 在概念股 universe 中的排名")
    with st.spinner("計算全 universe 因子中..."):
        universe = sorted(stock_name_lookup().keys())
        if sid not in universe:
            universe = [sid] + universe
        rows = []
        for uid in universe[:40]:  # 取 40 檔避免太久
            d = _fetch_ohlcv(uid, days=120)
            if d is None or d.empty:
                continue
            f = compute_all_factors(d)
            row = {"代碼": uid, "名稱": names.get(uid, uid)}
            row.update({k: v for k, v in f.items()})
            rows.append(row)

    if rows:
        df_u = pd.DataFrame(rows).set_index("代碼")
        # 顯示 MOM20 / RSI / VR20 快速排名
        st.markdown("**Top 10：20日動能**")
        st.dataframe(df_u.nlargest(10, "MOM20（20日動能）")[
            ["名稱", "MOM20（20日動能）", "RSI14", "VR20（量比）"]
        ], use_container_width=True)

        st.markdown("**Top 10：爆量（VR20）**")
        st.dataframe(df_u.nlargest(10, "VR20（量比）")[
            ["名稱", "VR20（量比）", "MOM20（20日動能）", "POS20（20日位階）"]
        ], use_container_width=True)

    with st.expander("📚 因子說明"):
        for k, v in factor_description().items():
            st.markdown(f"- **{k}** — {v}")


# ── Tab 7：自選清單掃描 ───────────────────────────────────────────────────────

def _scan_one(sid: str):
    """掃描單一股票，回傳結果 dict（帶快取，1 小時 TTL）"""
    import statistics as _stat, urllib.request as _ur, json as _js
    from twstock import Stock as _S
    from FinMind.data import DataLoader as _DL
    from datetime import datetime as _dt, timedelta as _td

    result = {"代碼": sid, "名稱": sid, "現價": "—", "RSI": "—",
              "20日%": "—", "訊號": "—",
              "短期目標": "—", "中期目標": "—", "長期目標": "—",
              "三大法人(合計)": "—", "近期新聞": "—"}
    try:
        _s = _S(sid)
        _st = _dt.now() - _td(days=90)
        _s.fetch_from(_st.year, _st.month)
        _p = list(_s.price)
        if len(_p) < 21:
            return result
        price = _p[-1]
        # 技術指標
        _diffs = [_p[i] - _p[i-1] for i in range(1, len(_p))]
        _g = [max(d, 0) for d in _diffs[-14:]]
        _l = [abs(min(d, 0)) for d in _diffs[-14:]]
        _ag, _al = sum(_g)/14, sum(_l)/14
        rsi = round(100 - 100/(1 + _ag/_al), 1) if _al else 50
        ret20 = round((_p[-1] - _p[-21]) / _p[-21] * 100, 2) if len(_p) >= 21 else None
        ma5 = sum(_p[-5:]) / 5
        ma20 = sum(_p[-20:]) / 20
        signal = "BUY" if (ma5 > ma20 and rsi < 70) else ("SELL" if (ma5 < ma20 and rsi > 60) else "HOLD")
        # 短期目標：布林上軌
        _p20 = _p[-20:]
        _sig = _stat.stdev(_p20)
        t_short = round(ma20 + 2*_sig, 1)

        # 中期 + 長期：P/E 均值回歸 + PEG 加權（40%P/E + 40%PEG + 20%技術）
        try:
            from datetime import date as _date2
            _one_yr = (_date2.today() - _td(days=365)).strftime("%Y-%m-%d")
            _per = _DL().taiwan_stock_per_pbr(stock_id=sid, start_date=_one_yr)
            if _per.empty:
                raise ValueError
            _vpe = _per[_per["PER"] > 0]["PER"]
            _vpb = _per[_per["PBR"] > 0]["PBR"]
            _cpe = float(_per.iloc[-1]["PER"]); _cpb = float(_per.iloc[-1]["PBR"])
            _mpe = float(_vpe.median()) if not _vpe.empty else 0
            _mpb = float(_vpb.median()) if not _vpb.empty else 0
            if _cpe > 0 and _mpe > 0:
                t_mid = round(price * max(0.85, min(_mpe / _cpe, 1.5)), 1)
            elif _cpb > 0 and _mpb > 0:
                t_mid = round(price * max(0.85, min(_mpb / _cpb, 1.5)), 1)
            else:
                t_mid = round((max(_p[-60:]) if len(_p) >= 60 else max(_p)) * 1.03, 1)
            _pe_tgt = price * max(0.85, min(_mpe/_cpe, 1.5)) if _cpe > 0 and _mpe > 0 else price * 1.10
            _peg_g = min(_cpe, 30) / 100 if _cpe > 0 else 0.10
            t_long = round(0.4 * _pe_tgt + 0.4 * price * (1 + _peg_g) + 0.2 * t_short, 1)
        except Exception:
            _hi60 = max(_p[-60:]) if len(_p) >= 60 else max(_p)
            t_mid = round(_hi60 * 1.03, 1)
            _dr = [(_p[i]-_p[i-1])/_p[i-1] for i in range(1, len(_p))]
            _vol = _stat.stdev(_dr[-20:]) if len(_dr) >= 20 else 0.02
            t_long = round(price*(1 + _vol*15), 1)

        result.update({"現價": price, "RSI": rsi, "20日%": f"{ret20:+.1f}%" if ret20 else "—",
                        "訊號": signal, "短期目標": t_short, "中期目標": t_mid, "長期目標": t_long})
    except Exception:
        pass

    # 籌碼
    try:
        _dl = _DL()
        _end = _dt.now().strftime("%Y-%m-%d")
        _start = (_dt.now() - _td(days=10)).strftime("%Y-%m-%d")
        _chip = _dl.taiwan_stock_institutional_investors(stock_id=sid, start_date=_start, end_date=_end)
        if not _chip.empty:
            _CMAP = {"Foreign_Investor":"外資","Foreign_Dealer_Self":"外資",
                     "Investment_Trust":"投信","Dealer_self":"自營","Dealer_Hedging":"自營"}
            _chip = _chip.sort_values("date", ascending=False)
            _ld = _chip["date"].iloc[0]
            _day = _chip[_chip["date"]==_ld]
            _agg = {"外資":0,"投信":0,"自營":0}
            for _, _r in _day.iterrows():
                _zh = _CMAP.get(_r.get("name",""))
                if _zh:
                    _agg[_zh] += int(_r.get("buy",0) - _r.get("sell",0))
            _tot = sum(_agg.values())
            result["三大法人(合計)"] = f"外{_agg['外資']:+,} 投{_agg['投信']:+,} 自{_agg['自營']:+,} ={_tot:+,}"
    except Exception:
        pass

    # 新聞
    try:
        _url = f"https://api.cnyes.com/media/api/v1/newslist/category/tw_stock?limit=3&stock_code={sid}"
        _req = _ur.Request(_url, headers={"User-Agent": "Mozilla/5.0"})
        with _ur.urlopen(_req, timeout=6) as _r:
            _items = _js.loads(_r.read()).get("items", {}).get("data", [])
        if _items:
            result["近期新聞"] = _items[0].get("title", "")[:30]
    except Exception:
        pass

    # 公司名稱
    try:
        from FinMind.data import DataLoader as _DL2
        _info = _DL2().taiwan_stock_info()
        _row = _info[_info["stock_id"] == sid]
        if not _row.empty:
            result["名稱"] = _row["stock_name"].values[0]
    except Exception:
        pass

    return result


def tab_watchlist():
    st.subheader("📋 自選清單掃描")
    st.caption("貼入自己的股票代碼，即時掃描技術面 + 籌碼 + 新聞 + 估值目標價")

    default_stocks = "2330 2317 2382 2609 3711 2454 3008 2412"
    col1, col2 = st.columns([2, 1])
    with col1:
        raw = st.text_area(
            "股票代碼（空白或換行分隔，最多 20 檔）",
            placeholder="例：\n2330\n2317 2382\n2609 3711",
            height=130,
        )
    with col2:
        st.markdown("**快速範本**")
        if st.button("AI 供應鏈"):
            st.session_state["watchlist_preset"] = "2330 2317 2382 2376 3231 6669 3017"
        if st.button("光通訊族群"):
            st.session_state["watchlist_preset"] = "4979 3450 2455 4906 3105 8086 3665"
        if st.button("重電 / 電網"):
            st.session_state["watchlist_preset"] = "1503 1513 1514 1519 1526"

    preset = st.session_state.pop("watchlist_preset", None)
    if preset:
        raw = preset

    run = st.button("🔍 開始掃描", type="primary")
    if not run or not raw.strip():
        st.info("請輸入股票代碼後點選「開始掃描」")
        return

    import re
    sids = re.findall(r"\d{4,6}", raw)[:20]
    if not sids:
        st.warning("沒有偵測到有效代碼（4~6 位數字）")
        return

    st.markdown(f"掃描 **{len(sids)}** 檔：{', '.join(sids)}")
    results = []
    prog = st.progress(0, text="掃描中...")
    for i, sid in enumerate(sids):
        prog.progress((i + 1) / len(sids), text=f"掃描 {sid}...")
        results.append(_scan_one(sid))
    prog.empty()

    df = pd.DataFrame(results).set_index("代碼")
    # 訊號顏色標記
    def _color_signal(val):
        c = {"BUY": "color: #27ae60; font-weight: bold",
             "SELL": "color: #e74c3c; font-weight: bold",
             "HOLD": "color: #f39c12"}.get(val, "")
        return c

    styled = df.style.map(_color_signal, subset=["訊號"])
    st.dataframe(styled, use_container_width=True, height=min(60 + len(results)*38, 600))

    # BUY 清單摘要
    buy_df = df[df["訊號"] == "BUY"]
    if not buy_df.empty:
        st.success(f"✅ **BUY 訊號** {len(buy_df)} 檔：{', '.join(buy_df['名稱'].tolist())}")
        st.markdown("##### 📍 BUY 目標價（基本面估值加權，非投資建議）")
        st.dataframe(
            buy_df[["名稱", "現價", "短期目標", "中期目標", "長期目標"]],
            use_container_width=True,
        )

    st.download_button(
        "⬇️ 下載結果 CSV",
        df.reset_index().to_csv(index=False, encoding="utf-8-sig"),
        file_name=f"watchlist_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
    )


# ── 主程式 ───────────────────────────────────────────────────────────────────

def main():
    st.title("📊 台股族群掃描系統")

    tabs = st.tabs([
        "今日掃描", "週報", "個股分析", "歷史趨勢",
        "概念股關聯", "量化因子", "自選清單",
    ])
    with tabs[0]:
        tab_daily()
    with tabs[1]:
        tab_weekly()
    with tabs[2]:
        tab_stock()
    with tabs[3]:
        tab_history()
    with tabs[4]:
        tab_concepts()
    with tabs[5]:
        tab_factors()
    with tabs[6]:
        tab_watchlist()


if __name__ == "__main__":
    main()
