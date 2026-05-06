import pandas as pd

_CHIP_MAP = {
    "Foreign_Investor":    "外資",
    "Foreign_Dealer_Self": "外資",
    "Investment_Trust":    "投信",
    "Dealer_self":         "自營",
    "Dealer_Hedging":      "自營",
}


def aggregate_chip(df: pd.DataFrame, days: int = 10) -> pd.DataFrame:
    """
    聚合三大法人買賣數據，計算每日淨買（張），並計算滾動累計。

    Args:
        df: FinMind 三大法人原始 DataFrame，需包含 'date', 'name', 'buy', 'sell' 欄位
        days: 滾動窗口天數，用於計算累計欄位（預設10天）

    Returns:
        包含 '日期', '外資', '投信', '自營', '合計', '10日累計' 的 DataFrame
    """
    if df.empty:
        return pd.DataFrame(columns=["日期", "外資", "投信", "自營", "合計", "10日累計"])

    df = df.sort_values("date")
    rows = []
    recent_dates = sorted(df["date"].unique())[-days:]
    for date in recent_dates:
        day = df[df["date"] == date]
        agg = {"日期": str(date)[:10], "外資": 0, "投信": 0, "自營": 0}
        for _, r in day.iterrows():
            zh = _CHIP_MAP.get(str(r.get("name", "")))
            if zh:
                # 買賣單位為股，轉換為張（1張=1000股）
                agg[zh] += (int(r.get("buy", 0)) - int(r.get("sell", 0))) // 1000
        agg["合計"] = agg["外資"] + agg["投信"] + agg["自營"]
        rows.append(agg)

    result = pd.DataFrame(rows)
    result["10日累計"] = result["合計"].rolling(window=10, min_periods=1).sum()
    return result


def main_force_signal(chip_df: pd.DataFrame, df_price: pd.DataFrame) -> dict:
    """
    根據三大法人籌碼變化及股價走勢，判斷主力動向信號。

    Args:
        chip_df: aggregate_chip() 輸出的聚合籌碼 DataFrame
        df_price: 包含 'close', 'volume', 'vol_ma20' 欄位的價格 DataFrame

    Returns:
        含 'label'（信號名稱）、'color'（視覺顏色碼）、'desc'（說明文字）的字典
    """
    if chip_df.empty or df_price.empty:
        return {"label": "觀望", "color": "#95a5a6", "desc": "資料不足"}

    recent5 = chip_df.tail(5)["合計"].sum()
    cum10   = chip_df["10日累計"].iloc[-1]
    close   = df_price["close"].iloc[-1]
    high60  = df_price["close"].tail(60).max()
    vol_ratio = df_price["volume"].iloc[-1] / (df_price["volume"].tail(20).mean() + 1e-9)

    if recent5 > 500 and cum10 > 0:
        return {"label": "吸籌期",   "color": "#27ae60", "desc": "主力持續買超，籌碼集中"}
    if recent5 < -500 and close >= high60 * 0.9:
        return {"label": "出貨初期", "color": "#e74c3c", "desc": "主力開始調節，需留意短線風險"}
    if abs(recent5) < 500 and vol_ratio < 0.8:
        return {"label": "整理期",   "color": "#f39c12", "desc": "量縮整理，等待方向"}
    return {"label": "觀望", "color": "#95a5a6", "desc": "籌碼中性，無明顯方向"}
