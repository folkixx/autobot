@echo off
REM ============================================================
REM  Run this ON THE HOST. Pulls the latest code from GitHub
REM  and launches the bot. No token inside (it lives in the
REM  host's local .git config from the one-time setup).
REM ============================================================
title AutoBot
cd /d "%~dp0"

echo [1/2] Pulling latest code from GitHub...
git pull --ff-only
echo     done.

echo.
echo [2/2] Starting AutoBot...
python main.py

echo.
echo AutoBot closed. Press any key to exit.
pause >nul
