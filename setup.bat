@echo off
echo ============================================
echo  AutoBot RPA — first-time setup
echo ============================================
echo.

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

echo [1/2] Installing dependencies...
python -m pip install -r requirements.txt --no-cache-dir --disable-pip-version-check --timeout 60

echo.
echo [2/2] Creating work directories...
if not exist "screenshots" mkdir screenshots

echo.
echo ============================================
echo  Done! Run the bot:  python main.py
echo ============================================
pause
