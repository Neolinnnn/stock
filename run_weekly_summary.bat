@echo off
cd /d "C:\Users\Neo\Documents\Claude\Projects\台股研究"
echo [%date% %time%] 週報生成開始 >> logs\weekly_summary.log 2>&1
python strategy_templates/08_weekly_summary.py >> logs\weekly_summary.log 2>&1
echo [%date% %time%] 週報生成完成 >> logs\weekly_summary.log 2>&1
