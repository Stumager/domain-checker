@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

echo Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo Starting DNS Checker...
echo Open browser: http://127.0.0.1:8080
echo.

python run.py
pause
