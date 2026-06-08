"""
agents — 台股族群／個股多代理分析後端

改寫自 TauricResearch/TradingAgents 的多代理流程，套用到本專案：
  分析師團隊(技術/基本/新聞總經/情緒) → 多空研究員辯論 → 交易員 → 風控 → 投組經理

分工（遵循 CLAUDE.md）：
  - 評分、辯論彙整、決策規則 → 本套件（Claude 程式邏輯，可回測可稽核）
  - 各角色的文字判讀          → Gemini（gemini_text.py，選用；預設為模板）

輸入：daily_reports/<date>/summary.json（既有每股資料）＋ macro 模組 regime
輸出：agents/output/analysis_<date>.json（族群 → 各股完整結果）

此套件與 docs/ 靜態網址產出完全解耦。
"""

__all__ = ["analysts", "pipeline", "gemini_text"]
