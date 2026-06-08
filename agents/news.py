"""
新聞時效性與連結處理。

- recency_label：依新聞日期與分析基準日算「今日 / 昨日 / N天前」
- ensure_link：優先用既有 url；舊資料（無 url）退回 Google News 搜尋連結
- prepare_news：把個股新聞整理成 UI 可直接用的清單（含時效 badge 與連結）
"""
from __future__ import annotations

import datetime as _dt
import urllib.parse
from typing import Any


def _to_date(s: str) -> _dt.date | None:
    if not s:
        return None
    token = str(s).strip().split(" ")[0]  # 去掉時間部分
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return _dt.datetime.strptime(token, fmt).date()
        except ValueError:
            continue
    return None


def recency_label(news_date: str, ref_date: str) -> str:
    nd, rd = _to_date(news_date), _to_date(ref_date)
    if not nd or not rd:
        return ""
    days = (rd - nd).days
    if days <= 0:
        return "今日"
    if days == 1:
        return "昨日"
    if days <= 7:
        return f"{days}天前"
    return nd.strftime("%m/%d")


def ensure_link(item: dict[str, Any]) -> str:
    url = item.get("url")
    if url:
        return url
    # 舊資料無連結 → 用標題做 Google News 搜尋當 fallback
    q = urllib.parse.quote(item.get("title", ""))
    return f"https://www.google.com/search?q={q}&tbm=nws" if q else ""


def prepare_news(stock: dict[str, Any], ref_date: str) -> list[dict[str, Any]]:
    out = []
    for n in stock.get("news") or []:
        d = n.get("datetime") or n.get("date", "")
        out.append({
            "title": n.get("title", ""),
            "source": n.get("source", ""),
            "datetime": d,
            "recency": recency_label(n.get("date", d), ref_date),
            "link": ensure_link(n),
        })
    return out


def prepare_events(regime: dict[str, Any]) -> list[dict[str, Any]]:
    """外電／總經事件（來自 macro grounding），整理成 UI 清單。"""
    sent = (regime or {}).get("_events", {}) or {}
    out = []
    for ev in sent.get("events", []) or []:
        src = ev.get("source", "")
        link = src if str(src).startswith("http") else (
            f"https://www.google.com/search?q={urllib.parse.quote(ev.get('headline',''))}&tbm=nws"
        )
        out.append({
            "headline": ev.get("headline", ""),
            "category": ev.get("category", ""),
            "impact": ev.get("impact", "neutral"),
            "severity": ev.get("severity", 1),
            "rationale": ev.get("rationale", ""),
            "source": src,
            "link": link,
        })
    return out
