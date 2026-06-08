"""
階段二：事件面情報蒐集

兩個來源：
  A. TWSE/MOPS 當日重大訊息（個股重訊、法說、財報）— 公開 API，免費
  B. Gemini Google Search grounding — 抓「川普談話 / 社群發言 / 總經頭條」
     這是解決「資訊落後」的關鍵：用 Gemini 內建即時搜尋抓當天國際消息並附來源。

設計分工：情報蒐集與情緒判讀交給 Gemini；本檔只負責「呼叫 + 結構化」，
最終的數值評分留在 regime_score.py（Claude 邏輯）。

環境變數：GEMINI_API_KEY（KEY2，沿用 CLAUDE.md 規範）
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Gemini grounding 用 2.5-flash（實在的最新 GA 版、原生 google_search、免費額度大）
GEMINI_MODEL = os.environ.get("MACRO_GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "{model}:generateContent?key={key}"
)


# ──────────────────────────────────────────────────────────────────────────
# A. TWSE/MOPS 當日重大訊息
# ──────────────────────────────────────────────────────────────────────────
def fetch_twse_material_news(limit: int = 30) -> list[dict[str, Any]]:
    """
    抓上市公司「當日重大訊息」。使用 MOPS ajax 端點（回傳 HTML 表格）。

    回傳精簡清單：[{date, code, name, subject}]。
    失敗時回傳 [] 並不中斷流程（事件面為輔助訊號）。
    """
    today = _dt.date.today()
    roc_year = today.year - 1911
    url = "https://mopsov.twse.com.tw/mops/web/ajax_t05st02"
    payload = urllib.parse.urlencode({
        "step": "0",
        "firstin": "1",
        "TYPEK": "sii",          # 上市
        "year": str(roc_year),
        "month": f"{today.month:02d}",
        "day": f"{today.day:02d}",
    }).encode()
    try:
        req = urllib.request.Request(url, data=payload, headers=_UA, method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
        return _parse_mops_table(html, limit=limit)
    except Exception as e:
        return [{"error": f"TWSE 重訊抓取失敗：{str(e)[:80]}"}]


def _parse_mops_table(html: str, limit: int) -> list[dict[str, Any]]:
    """以 BeautifulSoup 解析 MOPS 重訊表格；缺套件時退化為空清單。"""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        return [{"error": "需要 beautifulsoup4 以解析 MOPS 表格"}]
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select("table tr")
    out: list[dict[str, Any]] = []
    for tr in rows:
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        # MOPS 欄位：發言日期 / 公司代號 / 公司名稱 / 主旨 ...
        if len(cells) >= 4 and cells[1].isdigit():
            out.append({
                "date": cells[0],
                "code": cells[1],
                "name": cells[2],
                "subject": cells[3],
            })
        if len(out) >= limit:
            break
    return out


# ──────────────────────────────────────────────────────────────────────────
# B. Gemini Google Search grounding（川普 / 總經 / 國際股市）
# ──────────────────────────────────────────────────────────────────────────
_GROUNDING_PROMPT = """你是國際金融市場即時情報分析師。今天是 {today}。
請用 Google 搜尋找出「最近 48 小時內」最可能影響台股與全球股市的重大事件，重點涵蓋：
1. 川普（Trump）的公開談話、政策表態、社群（Truth Social / X）發言
2. 美國總經數據與 Fed 動向（CPI、非農、利率、FOMC 官員談話）
3. 重大地緣政治 / 關稅 / 科技產業（半導體、AI、輝達等）新聞
4. 國際股市與資金流的明顯異動

嚴格要求：
- 只收錄最近 48 小時內發生的事件；找不到夠新的就回傳空的 events 陣列，不要用舊聞充數。
- 每則事件「必須」附上實際發生日期 date（格式 YYYY-MM-DD），不確定就不要收錄。

只輸出 JSON（不要額外文字、不要 markdown 圍欄），格式：
{{
  "events": [
    {{
      "headline": "事件標題",
      "date": "YYYY-MM-DD",
      "category": "trump | macro | geopolitics | tech | flow",
      "impact": "bullish | bearish | neutral",
      "severity": 1到5的整數,
      "rationale": "一句話說明對台股/全球股市的影響",
      "source": "來源媒體或網址"
    }}
  ],
  "overall_bias": "risk_on | neutral | risk_off",
  "summary": "兩句話總結今日國際情緒"
}}
"""


def fetch_global_sentiment() -> dict[str, Any]:
    """
    用 Gemini + Google Search grounding 取得即時國際情緒。

    回傳解析後的 dict（含 events / overall_bias / summary），
    無金鑰或失敗時回傳 {"error": ...}。
    """
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"error": "GEMINI_API_KEY 未設定"}

    prompt = _GROUNDING_PROMPT.format(today=_dt.date.today().isoformat())
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],          # 開啟即時搜尋 grounding
        "generationConfig": {"temperature": 0.3},
    }).encode("utf-8")

    url = GEMINI_URL.format(model=GEMINI_MODEL, key=key)
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = result["candidates"][0]["content"]["parts"][0]["text"]
        return _parse_json_block(text)
    except urllib.error.HTTPError as e:
        return {"error": f"Gemini API {e.code}: {e.read().decode('utf-8')[:120]}"}
    except Exception as e:
        return {"error": f"Gemini grounding 失敗：{str(e)[:120]}"}


def _parse_json_block(text: str) -> dict[str, Any]:
    """從模型輸出抽出 JSON（容忍 ```json 圍欄與前後雜訊）。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("```", 2)[1]
        if t.lstrip().startswith("json"):
            t = t.lstrip()[4:]
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        t = t[start:end + 1]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        return {"error": "Gemini 回傳非合法 JSON", "raw": text[:300]}


def run() -> dict[str, Any]:
    """同時取得 TWSE 重訊與國際情緒。"""
    return {
        "stage": "macro_events",
        "asof": _dt.datetime.now().isoformat(timespec="seconds"),
        "twse_material_news": fetch_twse_material_news(),
        "global_sentiment": fetch_global_sentiment(),
    }


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False, indent=2))
