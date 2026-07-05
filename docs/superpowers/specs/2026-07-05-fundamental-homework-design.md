# 基本面功課工具設計（2026-07-05）

## 目標

針對單一台股個股產出一份「基本面功課」報告：量化數據 + 檢查清單紅綠燈評分 + 籌碼補充 + Gemini grounding 質性研究，CLI 觸發、GitHub Pages 網頁呈現。

## 使用方式

```
python scripts/fundamental_homework.py 2330            # 單檔
python scripts/fundamental_homework.py --auto --top 5  # 讀 docs/breakout.json 行動清單前 N 檔
python scripts/fundamental_homework.py 2330 --force    # 忽略 7 天快取重做
```

- 7 天內已產出的功課直接沿用（比對 `docs/homework/{sid}.json` 的 `generated_at`），控制 Gemini 額度。
- `--auto` 讀 `docs/breakout.json` 的行動清單（picks）個股，逐檔執行，已有新鮮快取者跳過。

## 架構

新增 `scripts/fundamental_homework.py` 作為編排器，重用既有零件：

| 區塊 | 來源 | 說明 |
|------|------|------|
| 月營收 / EPS / 毛利營益率 | FinMind（經 `scripts/datafeed.finmind_fetch`，token 輪替） | 重用 `fundamentals_fetcher.parse_revenue` / `parse_financials` |
| 估值 | FinMind `taiwan_stock_per_pbr` | 現值 PE/PB 在近 5 年歷史中的百分位（河流概念） |
| 籌碼 | `indicators/chip.py` | 近 20 / 60 日法人買賣超趨勢、chip_tier |
| 檢查清單評分 | 純 rule-based（Claude 邏輯，不用 Gemini） | 約 8 條紅綠燈 → 0–100 綜合分 |
| 質性研究 | `gemini_writer.py` 新 task `fundamental_homework`，`gemini-2.5-flash` + Google Search grounding | 業務與產品組合（優先重用 `enrich_product_mix` 30 天快取）、客戶/競爭者、題材催化、三大風險 |

## 檢查清單規則（紅/黃/綠，各配分加總 0–100）

1. 月營收 YoY 連 3 月為正
2. 累計 YoY > 10%
3. 月營收 MoM 最新為正
4. 毛利率連 2 季走升
5. EPS 近 4 季皆為正
6. EPS 近 4 季合計 YoY 成長
7. PE 歷史百分位 < 50%
8. 法人近 20 日淨買超

各條回傳 `green/yellow/red` 與說明文字；資料缺失標 `gray` 不計分（分母同步扣除）。

## 輸出

- `docs/homework/{sid}.json`：`{stock_id, name, generated_at, revenue, financials, valuation, chip, checklist, score, narrative}`
- `docs/homework/index.json`：已完成功課清單（sid、name、score、generated_at）
- `docs/homework.html`：單一模板頁，`?sid=2330` 讀 JSON 渲染；紅綠燈清單、營收/毛利小圖（沿用主站 lightweight-charts 風格）、質性段落。不做每檔一頁 HTML。

## 錯誤處理（降級輸出）

- FinMind 某資料集失敗 → 該區塊為 `null`，對應檢查項標 `gray`，報告照出。
- Gemini 失敗 → 沿用 gemini_writer 既有 429 換 key / 503 重試；最終失敗則 `narrative` 標「生成失敗」，量化部分不受影響。

## CI

本階段不動 `daily_scan.yml`；手動用順後再加一步 `--auto --top 5`（介面已預留）。

## 驗證

- pytest：合成營收/EPS/PE 序列 → 預期燈號與分數；Gemini 以 mock 驗 payload 格式。
- 端到端：實跑一檔個股，確認 JSON 與網頁渲染。
