@echo off
REM ============================================================
REM  Run this ON THE HOST (over RDP). It pulls the latest code
REM  from your LOCAL machine via the RDP-redirected drive and
REM  launches the bot. Requires RDP "Drives" redirection ON.
REM
REM  SRC = your local project, seen from the host as \\tsclient\
REM  If your local path differs, edit SRC below.
REM ============================================================
title AutoBot deploy
cd /d "%~dp0"

set "SRC=\\tsclient\C\Users\vasil\Desktop\autobot"

if not exist "%SRC%" (
    echo [ERROR] Cannot see local folder at:
    echo        %SRC%
    echo.
    echo Make sure RDP drive redirection is ON:
    echo   mstsc - Local Resources - More - check "Drives" - reconnect.
    pause
    exit /b 1
)

echo [1/2] Syncing latest code from local machine...
REM /R:1 /W:1  -> no million-retry hang on a locked file (default is 1,000,000!)
REM /XD runs flows -> skip heavy run-output folders (screenshots of past runs)
REM /MT:16 -> parallel copy
robocopy "%SRC%" "%~dp0." *.py *.bat *.txt /S /XO /R:1 /W:1 /MT:16 ^
    /XD __pycache__ screenshots .git runs flows ^
    /XF chat_log.txt learned_instructions.txt /NFL /NDL /NJH /NJS /NP
echo     done.

echo.
echo [2/2] Starting AutoBot...
python main.py

echo.
echo AutoBot closed. Press any key to exit.
pause >nul
