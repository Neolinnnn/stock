"""
Gemini 文字生成模組
負責所有 TEXT 輸出任務，Claude 不直接生成報告文字
"""
import os
import json
import urllib.request
import urllib.error
from typing import Any

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
DEFAULT_MODEL = "gemini-2.5-flash"

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
}


class GeminiWriter:
    def __init__(self, model: str = DEFAULT_MODEL):
        self.api_key = os.environ.get("GEMINI_API_KEY", "")
        self.model = model
        if not self.api_key:
            raise ValueError("GEMINI_API_KEY 未設定")

    def generate(self, task: str, context: dict[str, Any]) -> str:
        """
        呼叫 Gemini 生成文字

        Args:
            task: PROMPTS 中定義的任務類型
            context: 包含 data, date, extra 等欄位的 dict

        Returns:
            生成的文字字串
        """
        if task not in PROMPTS:
            raise ValueError(f"未知 task: {task}，可用：{list(PROMPTS.keys())}")

        prompt = PROMPTS[task].format(
            data=json.dumps(context.get("data", {}), ensure_ascii=False, indent=2),
            date=context.get("date", ""),
            extra=context.get("extra", ""),
        )

        payload = json.dumps({
            "contents": [{"parts": [{"text": prompt}]}]
        }).encode("utf-8")

        url = GEMINI_API_URL.format(model=self.model, key=self.api_key)
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                return result["candidates"][0]["content"]["parts"][0]["text"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            raise RuntimeError(f"Gemini API 錯誤 {e.code}: {body}") from e


if __name__ == "__main__":
    writer = GeminiWriter()
    text = writer.generate("daily_summary", {
        "date": "20260419",
        "data": {"族群": "光通訊", "漲幅": "3.2%", "成交量": "高"},
        "extra": "請特別提到外資動向",
    })
    print(text)
