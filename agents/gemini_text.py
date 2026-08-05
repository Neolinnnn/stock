"""
選用：用 Gemini 把結構化訊號寫成投組經理敘述。

預設不呼叫 Gemini（用確定性模板），pipeline 加 --gemini 才啟用。
分工：分數與決策一律 Claude 邏輯產生，Gemini 只負責「把數字寫成人話」。

呼叫策略：以「族群」為單位批次生成（一次一個族群、一次 API call），
而非逐檔呼叫——後者在 100+ 檔的規模會直接打爆免費額度，
且 Gemini 看不到同族群的橫向比較。
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Any

# CLAUDE.md 指定的預設模型（GA、免費額度、原生 grounding）
GEMINI_MODEL = os.environ.get("AGENTS_GEMINI_MODEL", "gemini-2.5-flash")
_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)


def _log(msg: str) -> None:
    """寫到 stderr，讓 CI log 看得到降級原因（過去是靜默 fallback，壞掉沒人知道）。"""
    print(f"[gemini_text] {msg}", file=sys.stderr)


def template_summary(r: dict[str, Any]) -> str:
    """確定性模板敘述（無需 API）。"""
    d = r["decision"]
    a = r["analysts"]
    used = d.get("weights_used") or {}
    verds = "、".join(
        f"{name}{a[k]['verdict']}"
        for k, name in [("technical", "技術面"), ("fundamental", "基本面"),
                        ("macro", "總經"), ("sentiment", "籌碼")]
        if k in a
    )
    flags = "、".join(d["flags"])
    skipped = [name for k, name in [("technical", "技術面"), ("fundamental", "基本面"),
                                    ("sentiment", "籌碼面")]
               if k not in used]
    note = f"（{ '、'.join(skipped) }缺資料，未納入加權）" if skipped else ""
    return (
        f"{r['name']}（{r['id']}）綜合評分 {d['composite']}{note}，"
        f"四面向：{verds}。交易員建議「{d['action']}」（信心 {d['confidence']}%），"
        f"依大盤 regime 部位上限 {d['exposure_pct']}%。"
        f"停損參考 {d['stop_loss']}、短期目標 {d['target_short']}。風控旗標：{flags}。"
    )


def _compact(r: dict[str, Any]) -> dict[str, Any]:
    """送給 Gemini 的精簡版個股記錄。

    刻意剔除 chart（60 日 × 6 條線）與 news 全文——它們佔了記錄體積的九成，
    對「把數字寫成人話」這件事沒有幫助，卻會把 prompt 撐爆。
    """
    a = r["analysts"]
    d = r["decision"]
    return {
        "id": r["id"], "name": r["name"], "price": r["price"],
        "composite": d["composite"], "action": d["action"],
        "confidence": d["confidence"], "exposure_pct": d["exposure_pct"],
        "stop_loss": d["stop_loss"], "target_short": d["target_short"],
        "flags": d["flags"], "weights_used": d.get("weights_used"),
        "analysts": {k: {"score": a[k]["score"], "verdict": a[k]["verdict"],
                         "coverage": a[k].get("coverage"), "signals": a[k]["signals"]}
                     for k in a},
        "bull": r["debate"]["bull"][:3],
        "bear": r["debate"]["bear"][:3],
    }


def _call_gemini(prompt: str) -> str | None:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        _log("GEMINI_API_KEY 未設定 → 使用模板敘述")
        return None
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }).encode("utf-8")
    req = urllib.request.Request(
        _URL.format(model=GEMINI_MODEL, key=key),
        data=payload, headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        return res["candidates"][0]["content"]["parts"][0]["text"].strip()
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        _log(f"HTTP {e.code}（model={GEMINI_MODEL}）→ 降級模板：{body}")
    except (urllib.error.URLError, TimeoutError) as e:
        _log(f"連線失敗 → 降級模板：{e}")
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        _log(f"回應格式非預期 → 降級模板：{type(e).__name__}")
    return None


def _parse_map(text: str) -> dict[str, str]:
    """解析 Gemini 回傳的 {股票代號: 敘述} JSON（容忍被 ``` 包住）。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v).strip() for k, v in obj.items() if isinstance(v, str)}


def attach_summaries(stocks: list[dict[str, Any]], sector_name: str,
                     use_gemini: bool = False) -> None:
    """就地填入每檔的 summary_text。整個族群一次呼叫，失敗逐檔退回模板。"""
    if not use_gemini:
        for r in stocks:
            r["summary_text"] = template_summary(r)
        return

    prompt = (
        f"你是台股投組經理，正在檢視「{sector_name}」族群的多代理分析結果。\n"
        "以下 JSON 陣列每個元素是一檔個股的分析。請為每一檔寫一段 120 字內的繁體中文"
        "決策敘述，須點出進場理由、主要風險與部位建議，並在合適時做同族群橫向比較。"
        "語氣專業、不浮誇，不要重複條列數字。\n"
        "注意 analysts.*.coverage 為 \"none\" 代表該面向查無資料（已排除於加權之外），"
        "敘述中應說明這點，不可當成利空。\n"
        "只輸出一個 JSON 物件，key 為股票代號字串，value 為該檔的敘述本文：\n"
        + json.dumps([_compact(r) for r in stocks], ensure_ascii=False)
    )
    text = _call_gemini(prompt)
    mapping = _parse_map(text) if text else {}
    if text and not mapping:
        _log(f"「{sector_name}」回應無法解析為 JSON map → 降級模板")

    n_ok = 0
    for r in stocks:
        got = mapping.get(str(r["id"]))
        r["summary_text"] = got if got else template_summary(r)
        n_ok += bool(got)
    if mapping:
        _log(f"「{sector_name}」Gemini 敘述 {n_ok}/{len(stocks)} 檔")


def summarize(r: dict[str, Any], use_gemini: bool = False) -> str:
    """單檔敘述（保留給臨時呼叫；批次請用 attach_summaries）。"""
    if not use_gemini:
        return template_summary(r)
    text = _call_gemini(
        "你是台股投組經理。根據以下這檔個股的多代理分析 JSON，"
        "用繁體中文寫一段 120 字內的決策敘述，須點出進場理由、主要風險與部位建議，"
        "語氣專業、不浮誇。只輸出 {\"summary\": \"...\"} 這樣的 JSON：\n"
        + json.dumps(_compact(r), ensure_ascii=False)
    )
    if text:
        obj = _parse_map(text)
        if obj.get("summary"):
            return obj["summary"]
    return template_summary(r)
