@echo off
title AutoBot
cd /d "%~dp0"
echo Starting AutoBot...
python main.py
echo.
echo AutoBot closed. Press any key to exit.
pause >nul
