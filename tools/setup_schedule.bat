@echo off
echo 建立台股自動排程...

schtasks /create /tn "台股每日掃描" /tr "C:\Users\Neo\Documents\Claude\Projects\台股研究\run_daily_scan.bat" /sc WEEKLY /d MON,TUE,WED,THU,FRI /st 14:32 /f
if %errorlevel% equ 0 (
    echo [OK] 台股每日掃描 排程已建立（週一至週五 14:32）
) else (
    echo [FAIL] 每日掃描排程建立失敗
)

schtasks /create /tn "台股週報" /tr "C:\Users\Neo\Documents\Claude\Projects\台股研究\run_weekly_summary.bat" /sc WEEKLY /d FRI /st 14:47 /f
if %errorlevel% equ 0 (
    echo [OK] 台股週報 排程已建立（週五 14:47）
) else (
    echo [FAIL] 週報排程建立失敗
)

echo.
echo 驗證排程：
schtasks /query /tn "台股每日掃描" /fo LIST 2>nul
schtasks /query /tn "台股週報" /fo LIST 2>nul

pause
