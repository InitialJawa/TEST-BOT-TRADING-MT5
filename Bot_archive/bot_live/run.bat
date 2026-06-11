@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================
echo  Scalping Bot XAUUSD M5 — Local PC
echo ============================================

python -c "import MetaTrader5; import pandas; import yaml; import requests; import dotenv" 2>nul
if %errorlevel% neq 0 (
    echo [INFO] Installing dependencies...
    pip install -r requirements.txt
)

echo [INFO] Starting bot...
python bot.py
pause
