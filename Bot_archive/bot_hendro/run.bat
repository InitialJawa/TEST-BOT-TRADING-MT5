@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  Hendro Bot — ADAPTIVE Multi-Strategy M5
echo ============================================

python -c "import MetaTrader5; import pandas; import yaml" 2>nul
if %errorlevel% neq 0 (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
)

echo [INFO] Starting bot...
python bot.py
pause
