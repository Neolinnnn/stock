"""
macro — 宏觀情緒面濾網（獨立模組）

此套件為「由上而下」的市場 regime 濾網，與既有的技術面掃描（daily_scan）
完全解耦，輸出獨立寫入 macro/output/，不會影響 docs/ 靜態網址產出。

三階段：
  1. market_regime  — 國際指標（VIX/SOX/美債/DXY/台幣）→ risk_score
  2. macro_events   — TWSE 重訊 + Gemini Google Search（川普/總經頭條）
  3. regime_score   — 彙整成最終 regime_score 與部位建議

設計分工（遵循 CLAUDE.md）：
  - 情緒判讀 / 即時情報蒐集 → Gemini
  - 數值評分 / 決策邏輯      → 本模組（Claude 程式邏輯）
"""

__all__ = ["market_regime", "macro_events", "regime_score"]
