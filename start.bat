@echo off
start "" pythonw "%~dp0rsi_bot.py"
echo Bot started in the background. Logs: bot.log
timeout /t 3 >nul
