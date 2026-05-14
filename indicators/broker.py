"""
TWSE 分點籌碼擷取模組

資料來源：histock.tw 公開頁面（免費、免 token、不需 CAPTCHA）
URL: https://histock.tw/stock/branch.aspx?no={stock_id}
顯示前 15 大買超 / 前 15 大賣超分點（含 買張/賣張/淨超/均價）

主要 API：
    fetch_broker_top15(stock_id) -> dict
        return: {
            'top_buyers':  [{'name', 'buy', 'sell', 'net', 'avg_price'}, ...],
            'top_sellers': [{'name', 'buy', 'sell', 'net', 'avg_price'}, ...],
            'net_concentration': int,        # top15_buy 張 - top15_sell 張
            'source': 'histock' | None,
            'error': str | None,
        }
    main_force_score(broker_data) -> dict
        計算主力強度評分（0~100）與標籤

設計原則：
- 失敗時不丟例外，回傳 error 欄位
- 6 秒 timeout，避免 daily_scan 卡死
- 同 process 內快取（避免重複請求）
"""
from __future__ import annotations

import re
import ssl
import time
import urllib.request
from typing import Optional

_CACHE: dict[str, dict] = {}

# SSL context（CI/Windows 上系統憑證鏈可能不完整）
_SSL_CTX = ssl.create_default_context()
try:
    import certifi  # type: ignore
    _SSL_CTX.load_verify_locations(certifi.where())
except Exception:
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE


def _http_get(url: str, timeout: int = 8) -> Optional[str]:
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
                ),
                "Accept-Language": "zh-TW,zh;q=0.9",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            raw = resp.read()
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def _to_int(s: str) -> Optional[int]:
    try:
        return int(s.replace(",", "").strip())
    except Exception:
        return None


def _parse_histock(html: str) -> tuple[list, list]:
    """
    解析 histock.tw branch.aspx 頁面。

    頁面是左右兩欄表格：
      左：賣超 top 15  欄位序: 券商名稱 | 買張 | 賣張 | 賣超(負) | 均價
      右：買超 top 15  欄位序: 券商名稱 | 買張 | 賣張 | 買超(正) | 均價

    每一列共 10 欄（左 5 + 右 5）。
    """
    table_match = re.search(r"<table[^>]*>(.*?)</table>", html, re.DOTALL)
    if not table_match:
        return [], []
    table_html = table_match.group(1)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL)

    buyers: list[dict] = []
    sellers: list[dict] = []

    for tr in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", tr, re.DOTALL)
        clean = [re.sub(r"<[^>]+>", "", c).replace("\xa0", " ").strip() for c in cells]
        # 跳過表頭與短列
        if len(clean) < 10:
            continue
        if "券商" in clean[0] or "名稱" in clean[0]:
            continue

        # 左半（賣超）
        sell_name = clean[0]
        sell_buy = _to_int(clean[1])
        sell_sell = _to_int(clean[2])
        sell_net = _to_int(clean[3])
        sell_avg = clean[4]

        # 右半（買超）
        buy_name = clean[5]
        buy_buy = _to_int(clean[6])
        buy_sell = _to_int(clean[7])
        buy_net = _to_int(clean[8])
        buy_avg = clean[9]

        if sell_name and sell_net is not None and sell_net < 0:
            sellers.append({
                "name": sell_name,
                "buy": sell_buy or 0,
                "sell": sell_sell or 0,
                "net": sell_net,
                "avg_price": _safe_float(sell_avg),
            })
        if buy_name and buy_net is not None and buy_net > 0:
            buyers.append({
                "name": buy_name,
                "buy": buy_buy or 0,
                "sell": buy_sell or 0,
                "net": buy_net,
                "avg_price": _safe_float(buy_avg),
            })

    buyers.sort(key=lambda x: x["net"], reverse=True)
    sellers.sort(key=lambda x: x["net"])
    return buyers[:15], sellers[:15]


def _safe_float(s: str) -> Optional[float]:
    try:
        return float(re.sub(r"[^\d\.\-]", "", s))
    except Exception:
        return None


def fetch_broker_top15(stock_id: str) -> dict:
    """抓取個股近期主力券商 top15 買賣超（histock.tw 預設窗口）"""
    if stock_id in _CACHE:
        return _CACHE[stock_id]

    result: dict = {
        "top_buyers": [],
        "top_sellers": [],
        "net_concentration": 0,
        "source": None,
        "error": None,
    }

    url = f"https://histock.tw/stock/branch.aspx?no={stock_id}"
    html = _http_get(url, timeout=8)
    if html is None:
        result["error"] = "fetch_failed"
        _CACHE[stock_id] = result
        return result

    try:
        buyers, sellers = _parse_histock(html)
    except Exception as e:
        result["error"] = f"parse_failed: {e}"
        _CACHE[stock_id] = result
        return result

    if not buyers and not sellers:
        result["error"] = "no_data"
        _CACHE[stock_id] = result
        return result

    result["top_buyers"] = buyers
    result["top_sellers"] = sellers
    result["net_concentration"] = (
        sum(b["net"] for b in buyers) + sum(s["net"] for s in sellers)
    )
    result["source"] = "histock"

    _CACHE[stock_id] = result
    time.sleep(0.6)  # 禮貌延遲
    return result


def main_force_score(broker_data: dict) -> dict:
    """
    依 top15 分點資料計算主力強度（0~100）。

    分數構成：
      40% 淨買超張數     net 在 ±5000 張之間線性對應 0~100
      35% 前 5 大集中度  top5_buy / 全 top15_buy
      25% 單一主力主導性  top1_buy / 全 top15_buy
    """
    buyers = broker_data.get("top_buyers", [])
    sellers = broker_data.get("top_sellers", [])
    if not buyers and not sellers:
        return {
            "score": None,
            "label": "資料不足",
            "reason": broker_data.get("error", ""),
        }

    total_buy = sum(b["net"] for b in buyers) or 1
    top5_buy = sum(b["net"] for b in buyers[:5])
    top1_buy = buyers[0]["net"] if buyers else 0
    net = broker_data.get("net_concentration", 0)

    concentration = top5_buy / total_buy
    dominance = top1_buy / total_buy

    score_net = (min(max(net, -5000), 5000) / 5000) * 50 + 50
    score_conc = concentration * 100
    score_dom = dominance * 100
    score = round(0.4 * score_net + 0.35 * score_conc + 0.25 * score_dom, 1)

    if score >= 70:
        label = "主力強力吸籌"
    elif score >= 55:
        label = "主力買盤偏多"
    elif score >= 45:
        label = "籌碼中性"
    elif score >= 30:
        label = "主力出貨偏多"
    else:
        label = "主力大幅出脫"

    return {
        "score": score,
        "label": label,
        "net_lots": net,
        "top5_buy_lots": top5_buy,
        "top1_broker": buyers[0]["name"] if buyers else None,
        "top1_lots": top1_buy,
        "concentration": round(concentration, 3),
    }
