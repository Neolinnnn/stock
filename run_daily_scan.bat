@echo off
cd /d "C:\Users\Neo\Documents\Claude\Projects\台股研究"
echo [%date% %time%] 每日掃描開始 >> logs\daily_scan.log 2>&1
python strategy_templates/07_daily_scan.py >> logs\daily_scan.log 2>&1
echo [%date% %time%] 每日掃描完成 >> logs\daily_scan.log 2>&1
