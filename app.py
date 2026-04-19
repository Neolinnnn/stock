"""
台股族群掃描系統 — Web UI
Streamlit app：讀取 daily_reports/ 下的 JSON，呈現每日掃描 / 週報 / 個股查詢 / 歷史趨勢
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

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

    # 大盤
    mkt = summary.get("market", {})
    c1, c2, c3, c4 = st.columns(4)
    idx = mkt.get("加權指數")
    chg = mkt.get("漲跌幅")
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
            for s in data.get("stocks", []):
                chip = s.get("chip", {})
                chip_str = (
                    f"外{chip.get('外資',0):+,} 投{chip.get('投信',0):+,} 自{chip.get('自營',0):+,}"
                    if chip
                    else "—"
                )
                news = s.get("news", [])
                news_str = " ／ ".join(f"[{n['source']}]{n['title']}" for n in news[:2]) or "—"
                rows.append(
                    {
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
                )
            df = pd.DataFrame(rows)
            st.dataframe(df.set_index("代碼"), use_container_width=True)


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

            # 三大法人
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
                        n = r.get("name", "")
                        net = int(r.get("buy", 0) - r.get("sell", 0))
                        if "外資" in n:
                            row["外資"] = net
                        elif "投信" in n:
                            row["投信"] = net
                        elif "自營" in n:
                            row["自營"] = net
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


# ── 主程式 ───────────────────────────────────────────────────────────────────

def main():
    st.title("📊 台股族群掃描系統")

    tab1, tab2, tab3, tab4 = st.tabs(["今日掃描", "週報", "個股分析", "歷史趨勢"])
    with tab1:
        tab_daily()
    with tab2:
        tab_weekly()
    with tab3:
        tab_stock()
    with tab4:
        tab_history()


if __name__ == "__main__":
    main()
