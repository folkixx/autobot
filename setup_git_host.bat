@echo off
REM ============================================================
REM  ONE-TIME setup. Run this ONCE on the host, inside the
REM  existing autobot folder. It turns the folder into a git
REM  clone of the GitHub repo. Your local config.py is kept
REM  (it's gitignored). After this, use pull.bat every time.
REM ============================================================
title AutoBot git setup
cd /d "%~dp0"

echo Setting up git in this folder...
git init
git remote remove origin 2>nul
git remote add origin https://github.com/folkixx/autobot.git
git fetch origin
git reset --hard origin/main
git branch --set-upstream-to=origin/main main

echo.
echo ============================================================
echo  Done. From now on just double-click pull.bat to update+run.
echo ============================================================
pause
