"""
選用：用 Gemini 把結構化訊號寫成投組經理敘述。

預設不呼叫 Gemini（用確定性模板），pipeline 加 --gemini 才啟用。
分工：分數與決策一律 Claude 邏輯產生，Gemini 只負責「把數字寫成人話」。
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

GEMINI_MODEL = os.environ.get("AGENTS_GEMINI_MODEL", "gemini-3.5-flash")
_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)


def template_summary(r: dict[str, Any]) -> str:
    """確定性模板敘述（無需 API）。"""
    d = r["decision"]
    a = r["analysts"]
    verds = "、".join(f"{name}{a[k]['verdict']}"
                      for k, name in [("technical", "技術面"), ("fundamental", "基本面"),
                                      ("macro", "總經"), ("sentiment", "籌碼")])
    flags = "、".join(d["flags"])
    return (
        f"{r['name']}（{r['id']}）綜合評分 {d['composite']}，"
        f"四面向：{verds}。交易員建議「{d['action']}」（信心 {d['confidence']}%），"
        f"依大盤 regime 部位上限 {d['exposure_pct']}%。"
        f"停損參考 {d['stop_loss']}、短期目標 {d['target_short']}。風控旗標：{flags}。"
    )


def gemini_summary(r: dict[str, Any]) -> str:
    """用 Gemini 改寫成更口語的投組經理敘述；失敗則退回模板。"""
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return template_summary(r)

    prompt = (
        "你是台股投組經理。根據以下這檔個股的多代理分析 JSON，"
        "用繁體中文寫一段 120 字內的決策敘述，須點出進場理由、主要風險與部位建議，"
        "語氣專業、不浮誇，只輸出敘述本文：\n"
        + json.dumps(r, ensure_ascii=False)
    )
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4},
    }).encode("utf-8")
    req = urllib.request.Request(
        _URL.format(model=GEMINI_MODEL, key=key),
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        return res["candidates"][0]["content"]["parts"][0]["text"].strip()
    except (urllib.error.HTTPError, urllib.error.URLError, KeyError, TimeoutError):
        return template_summary(r)


def summarize(r: dict[str, Any], use_gemini: bool = False) -> str:
    return gemini_summary(r) if use_gemini else template_summary(r)
