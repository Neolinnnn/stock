"""
Gemini 文字生成模組
負責所有 TEXT 輸出任務，Claude 不直接生成報告文字
"""
import os
import json
import time
import urllib.request
import urllib.error
from typing import Any

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
DEFAULT_MODEL = "gemini-2.5-flash"

# Groq：OpenAI 相容格式，作為 Gemini 配額耗盡時的備援
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq 的模型上下架頻繁，預設值僅供起步，正式使用前請以
# GET https://api.groq.com/openai/v1/models 確認當下可用者，並以環境變數覆寫
GROQ_DEFAULT_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")

# 這些 task 的提示詞明確要求「搜尋最新法說會、年報、季報、新聞稿」，
# 依賴 Gemini 的 Google Search grounding。Groq 無內建搜尋，模型只會
# 拿訓練截止前的舊資料編出看似合理的數字，故一律不得退回 Groq。
GROUNDING_REQUIRED_TASKS = {"product_mix", "fundamental_homework"}

PROMPTS = {
    "daily_summary": """你是台股分析師，根據以下族群掃描資料，撰寫今日市場摘要（繁體中文，300字內）：
{data}
日期：{date}
{extra}""",

    "weekly_report": """你是台股分析師，根據以下一週掃描資料，撰寫週報摘要（繁體中文，500字內）：
{data}
週期：{date}
{extra}""",

    "stock_analysis": """你是台股分析師，根據以下個股資料，撰寫深度分析（繁體中文，400字內）：
{data}
{extra}""",

    "market_narrative": """你是台股分析師，根據以下大盤資料，撰寫今日市場敘述（繁體中文，200字內）：
{data}
日期：{date}
{extra}""",

    "product_mix": """你是台股產業分析師，請搜尋並整理以下台灣上市公司的最新產銷組合（業務結構）資料。

公司：{data}
資料日期：{date}

請搜尋該公司最新的法說會、年報、季報、新聞稿，整理出：
1. 各產品線「佔總營收」比例（%）及年增率（%）
2. 地區別營收分布（%）
3. 主要客戶
4. 商業模式（代工/JDM/ODM/自有品牌等）
5. 核心競爭優勢或護城河

重要限制：
- product_lines 只列「最上層產品線」，share_pct 一律是「佔公司總營收」的比例，全部加總須約等於 100%（容許 ±10%）。
- 不要列「某子項目佔某產品線」的細分（例如「DDR5 佔 DRAM 84%」），那會造成加總爆掉 100%；若要表達細分，請併入 summary 文字說明。
- 每條產品線都必須給出 share_pct 數字，不可為 null。

請嚴格以 JSON 格式回覆（不含 markdown code block，直接輸出純 JSON）：
{{
  "product_lines": [
    {{"name": "產品線名稱", "share_pct": 數字, "yoy_growth": 數字或null, "trend": "up/flat/down"}}
  ],
  "regions": [
    {{"name": "地區", "share_pct": 數字}}
  ],
  "customers": ["客戶1", "客戶2"],
  "biz_model": "商業模式一行說明",
  "moat": "核心競爭優勢一行說明",
  "summary": "2-3句整體業務摘要",
  "data_period": "資料期間（如 2025Q1-Q3）",
  "updated_at": "{date}"
}}
{extra}""",

    "llm_debate": """你是台股投資委員會的「魔鬼代言人」，負責覆核程式規則產出的決策。

以下 JSON 是多代理系統對數檔個股的分析與建議。每檔已附：
- analysts：四面向評分與訊號（coverage 為 "none" 表示查無資料、已排除於加權外，不是利空）
- decision：規則產出的行動、進場區、停損、目標價
- rule_check：程式先算好的風險報酬比等硬指標（數字已驗算，直接引用，不要自己重算）

分析日期：{date}

你的任務**不是**重新評分，而是找出規則看不見的問題。針對每一檔：
1. bull / bear：各 1-3 條最強的論點，須引用上面的具體數字
2. risk_objection：這個建議最可能錯在哪裡？特別注意 rule_check 標記的異常
   （例如目標價低於進場價、停損距離過寬、單一面向獨大卻報高信心）
3. verdict：對規則建議的覆核結論，只能是 "維持" / "降級" / "升級" 三者之一
4. reason：一句話說明 verdict 的理由

規則已經考慮過的東西不必重複；沒有異議就寫「無」，不要為了湊字數而編造風險。

請嚴格以 JSON 格式回覆（不含 markdown code block，直接輸出純 JSON），
key 為股票代號字串：
{{
  "2330": {{
    "bull": ["論點1"],
    "bear": ["論點1"],
    "risk_objection": "最可能的錯誤",
    "verdict": "維持",
    "reason": "一句話理由"
  }}
}}

個股資料：
{data}
{extra}""",

    "fundamental_homework": """你是台股基本面研究員，請為以下公司做一份「基本面功課」的質性研究。請搜尋該公司最新的法說會、年報、季報、新聞稿。

公司與量化數據：
{data}
資料日期：{date}

請整理：
1. business：公司在做什麼、主要產品線與商業模式（3-4 句）
2. customers / competitors：主要客戶與主要競爭對手（各 2-4 個，每個附一句定位說明）
3. catalysts：近期題材與成長催化（2-4 條，每條一句）
4. risks：三大風險（每條一句）
5. verdict：綜合判斷（2-3 句），必須引用上面提供的量化數據（如營收 YoY、毛利率走勢、PE 百分位）佐證

請嚴格以 JSON 格式回覆（不含 markdown code block，直接輸出純 JSON）：
{{
  "business": "業務說明",
  "customers": ["客戶：定位說明"],
  "competitors": ["競爭者：定位說明"],
  "catalysts": ["題材1"],
  "risks": ["風險1", "風險2", "風險3"],
  "verdict": "綜合判斷"
}}
{extra}""",
}

# 503/429 可重試的狀態碼
_RETRYABLE_CODES = {429, 503}

# 單次請求逾時（秒）。未設上限時 CI 會整個卡死等不到回應。
DEFAULT_TIMEOUT = 90


def _collect_keys(prefix: str) -> list[str]:
    """依序收集 <prefix>, <prefix>_1, <prefix>_2, ... 的環境變數值（去重、保序）。

    序號中斷即停止掃描，例如設了 _1 未設 _2 則 _3 不會被讀取。
    """
    keys: list[str] = []
    primary = os.environ.get(prefix, "")
    if primary:
        keys.append(primary)
    i = 1
    while True:
        k = os.environ.get(f"{prefix}_{i}", "")
        if not k:
            break
        if k not in keys:
            keys.append(k)
        i += 1
    return keys


def collect_api_keys() -> list[str]:
    """依序收集所有可用 key：GEMINI_API_KEY, GEMINI_API_KEY_1, GEMINI_API_KEY_2, ..."""
    return _collect_keys("GEMINI_API_KEY")


def collect_groq_keys() -> list[str]:
    """依序收集所有可用 key：GROQ_API_KEY, GROQ_API_KEY_1, GROQ_API_KEY_2, ..."""
    return _collect_keys("GROQ_API_KEY")


def call_groq(
    prompt: str,
    *,
    model: str | None = None,
    temperature: float | None = None,
    json_output: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    keys: list[str] | None = None,
) -> str:
    """送出單次 Groq 請求，介面與 call_gemini 對稱，內含多 Key 輪替與 429 重試。

    Groq 採 OpenAI 相容格式，與 Gemini 的差異僅在認證位置、請求結構與回應路徑。
    此處不提供 use_grounding 參數：Groq 無內建搜尋，若任務需要即時資料，
    呼叫端應留在 Gemini，而不是讓模型用訓練截止前的舊資料作答。

    Args:
        prompt: 已組好的完整提示詞
        model: 模型名稱，預設取 GROQ_MODEL 環境變數
        temperature: 取樣溫度，None 表示用 API 預設
        json_output: 要求回傳 JSON 物件（Groq 規定提示詞須含 "JSON" 字樣）
        timeout: 單次請求逾時秒數
        keys: 自訂 Key 清單，預設由環境變數收集

    Returns:
        模型回覆的純文字

    Raises:
        ValueError: 未設定任何 API Key
        RuntimeError: 所有 Key 與重試都失敗
    """
    keys = keys if keys is not None else collect_groq_keys()
    if not keys:
        raise ValueError("GROQ_API_KEY 未設定")

    model = model or GROQ_DEFAULT_MODEL
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if temperature is not None:
        body["temperature"] = temperature
    if json_output:
        body["response_format"] = {"type": "json_object"}

    payload = json.dumps(body).encode("utf-8")
    last_error: Exception | None = None

    # 429 → 換 key；5xx → 等待後同 key 重試，最多 3 次（與 Gemini 路徑一致）
    _5xx_retries = 3
    attempt = 0
    while attempt < len(keys):
        req = urllib.request.Request(
            GROQ_API_URL,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {keys[attempt]}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            last_error = RuntimeError(f"Groq API 錯誤 {e.code}: {err_body}")
            if e.code == 429:
                if attempt < len(keys) - 1:
                    wait = 3 * (attempt + 1)
                    print(f"  [groq] key[{attempt}] 回傳 429，{wait}s 後換下一組 key…")
                    time.sleep(wait)
                    attempt += 1
                    _5xx_retries = 3
                else:
                    raise last_error from e
            elif 500 <= e.code < 600:
                if _5xx_retries > 0:
                    _5xx_retries -= 1
                    print(f"  [groq] key[{attempt}] 回傳 {e.code}，等 30s 後重試（剩 {_5xx_retries} 次）…")
                    time.sleep(30)
                elif attempt < len(keys) - 1:
                    print(f"  [groq] key[{attempt}] {e.code} 重試耗盡，換下一組 key…")
                    attempt += 1
                    _5xx_retries = 3
                else:
                    raise last_error from e
            else:
                raise last_error from e

    raise last_error  # type: ignore


def call_gemini(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    use_grounding: bool = False,
    temperature: float | None = None,
    json_output: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    keys: list[str] | None = None,
) -> str:
    """送出單次 Gemini 請求，內含多 Key 輪替與 429/503 重試。

    這是全專案唯一的 Gemini HTTP 出口；agents/gemini_text.py 亦委派至此，
    以免重試與輪替邏輯散落兩處各修一次。

    Args:
        prompt: 已組好的完整提示詞
        model: 模型名稱
        use_grounding: 是否啟用 Google Search grounding
        temperature: 取樣溫度，None 表示用 API 預設
        json_output: 要求回傳 application/json
        timeout: 單次請求逾時秒數
        keys: 自訂 Key 清單，預設由環境變數收集

    Returns:
        模型回覆的純文字

    Raises:
        ValueError: 未設定任何 API Key
        RuntimeError: 所有 Key 與重試都失敗
    """
    keys = keys if keys is not None else collect_api_keys()
    if not keys:
        raise ValueError("GEMINI_API_KEY 未設定")

    body: dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}]}
    if use_grounding:
        body["tools"] = [{"google_search": {}}]

    gen_cfg: dict[str, Any] = {}
    if temperature is not None:
        gen_cfg["temperature"] = temperature
    if json_output:
        gen_cfg["responseMimeType"] = "application/json"
    if gen_cfg:
        body["generationConfig"] = gen_cfg

    payload = json.dumps(body).encode("utf-8")
    last_error: Exception | None = None

    # 429 → 換 key（不同 project 配額）；503 → 等待後同 key 重試，最多 3 次
    _503_retries = 3
    attempt = 0
    while attempt < len(keys):
        key = keys[attempt]
        url = GEMINI_API_URL.format(model=model, key=key)
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", "replace")
            last_error = RuntimeError(f"Gemini API 錯誤 {e.code}: {err_body}")
            if e.code == 429:
                # 配額耗盡 → 換下一組 key
                if attempt < len(keys) - 1:
                    wait = 3 * (attempt + 1)
                    print(f"  [gemini] key[{attempt}] 回傳 429，{wait}s 後換下一組 key…")
                    time.sleep(wait)
                    attempt += 1
                    _503_retries = 3  # 新 key 重置 503 重試次數
                else:
                    raise last_error from e
            elif e.code == 503:
                # 伺服器繁忙 → 等 30s 後重試同一 key（最多 3 次再換 key）
                if _503_retries > 0:
                    _503_retries -= 1
                    print(f"  [gemini] key[{attempt}] 回傳 503，等 30s 後重試（剩 {_503_retries} 次）…")
                    time.sleep(30)
                elif attempt < len(keys) - 1:
                    print(f"  [gemini] key[{attempt}] 503 重試耗盡，換下一組 key…")
                    attempt += 1
                    _503_retries = 3
                else:
                    raise last_error from e
            else:
                raise last_error from e

    raise last_error  # type: ignore


def call_llm(
    prompt: str,
    *,
    model: str | None = None,
    use_grounding: bool = False,
    temperature: float | None = None,
    json_output: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    allow_fallback: bool = True,
    gemini_keys: list[str] | None = None,
) -> str:
    """以 Gemini 為主、Groq 為備援送出請求。

    Gemini 仍是預設供應商（免費額度大、原生 grounding）。只有在 Gemini
    的所有 Key 都耗盡或持續失敗時才退到 Groq，且僅限不需要即時資料的任務。

    不退回 Groq 的情況：
      1. use_grounding=True —— Groq 無內建搜尋
      2. allow_fallback=False —— 呼叫端明確禁止（例如 grounding 必要的 task）
      3. 未設定 GROQ_API_KEY

    Args:
        allow_fallback: 是否允許在 Gemini 失敗時改用 Groq
        gemini_keys: 自訂 Gemini Key 清單，預設由環境變數收集
        其餘參數語意同 call_gemini

    Returns:
        模型回覆的純文字

    Raises:
        ValueError: 兩邊都沒有可用的 API Key
        RuntimeError: Gemini 失敗且無法（或不允許）退回 Groq
    """
    try:
        return call_gemini(
            prompt,
            model=model or DEFAULT_MODEL,
            use_grounding=use_grounding,
            temperature=temperature,
            json_output=json_output,
            timeout=timeout,
            keys=gemini_keys,
        )
    except (RuntimeError, ValueError) as gemini_err:
        if use_grounding:
            print("  [llm] 此任務需 Google Search grounding，不退回 Groq")
            raise
        if not allow_fallback:
            raise
        if not collect_groq_keys():
            print("  [llm] GROQ_API_KEY 未設定，無備援可用")
            raise

        print(f"  [llm] Gemini 失敗（{str(gemini_err)[:120]}）→ 改用 Groq 備援")
        try:
            text = call_groq(
                prompt,
                temperature=temperature,
                json_output=json_output,
                timeout=timeout,
            )
        except (RuntimeError, ValueError) as groq_err:
            raise RuntimeError(
                f"Gemini 與 Groq 皆失敗。Gemini: {gemini_err} / Groq: {groq_err}"
            ) from groq_err
        print(f"  [llm] Groq 備援成功（model={GROQ_DEFAULT_MODEL}）")
        return text


class GeminiWriter:
    def __init__(self, model: str = DEFAULT_MODEL):
        keys = collect_api_keys()
        if not keys:
            raise ValueError("GEMINI_API_KEY 未設定")

        self._keys = keys
        self.model = model

    def generate(self, task: str, context: dict[str, Any], use_grounding: bool = False,
                 allow_fallback: bool = True) -> str:
        """
        生成文字。503/429 時自動輪替備用 Key，必要時退回 Groq（實作見 call_llm）。

        Args:
            task: PROMPTS 中定義的任務類型
            context: 包含 data, date, extra 等欄位的 dict
            use_grounding: 是否啟用 Google Search grounding（適用 product_mix 等需要即時資訊的任務）
            allow_fallback: Gemini 失敗時是否允許改用 Groq。需要 grounding 的
                task（見 GROUNDING_REQUIRED_TASKS）一律強制關閉，因為 Groq
                無內建搜尋，只會拿舊資料編出看似合理的數字

        Returns:
            生成的文字字串
        """
        if task not in PROMPTS:
            raise ValueError(f"未知 task: {task}，可用：{list(PROMPTS.keys())}")

        data = context.get("data", {})
        prompt = PROMPTS[task].format(
            data=json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else str(data),
            date=context.get("date", ""),
            extra=context.get("extra", ""),
        )

        if task in GROUNDING_REQUIRED_TASKS:
            allow_fallback = False

        return call_llm(
            prompt,
            model=self.model,
            use_grounding=use_grounding,
            allow_fallback=allow_fallback,
            gemini_keys=self._keys,
        )


if __name__ == "__main__":
    writer = GeminiWriter()
    text = writer.generate("daily_summary", {
        "date": "20260419",
        "data": {"族群": "光通訊", "漲幅": "3.2%", "成交量": "高"},
        "extra": "請特別提到外資動向",
    })
    print(text)
