@echo off
setlocal
chcp 65001 > nul
cd /d "%~dp0"

echo Building DNS Checker...
echo.

tasklist /FI "IMAGENAME eq DNS_Checker.exe" | find /I "DNS_Checker.exe" > nul
if not errorlevel 1 (
    echo Closing running DNS_Checker.exe instances...
    taskkill /IM DNS_Checker.exe /F /T > nul 2>&1
    timeout /t 2 /nobreak > nul
    echo.
)

if exist "dist\DNS_Checker.exe" (
    del /F /Q "dist\DNS_Checker.exe" > nul 2>&1
    if exist "dist\DNS_Checker.exe" (
        echo Failed to remove old dist\DNS_Checker.exe
        pause
        exit /b 1
    )
    echo Old exe deleted
    echo.
)

python build.py
if errorlevel 1 (
    echo.
    echo Build failed.
    pause
    exit /b 1
)

if not exist "dist\DNS_Checker.exe" (
    echo.
    echo Build failed: dist\DNS_Checker.exe was not created.
    pause
    exit /b 1
)

echo Updating shortcut...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$base = (Resolve-Path '.').Path; $target = Join-Path $base 'dist\DNS_Checker.exe'; $workdir = Join-Path $base 'dist'; $icon = Join-Path $base 'icon.ico'; $shortcutPath = Join-Path $base 'DNS_Checker.lnk'; $shell = New-Object -ComObject WScript.Shell; $shortcut = $shell.CreateShortcut($shortcutPath); $shortcut.TargetPath = $target; $shortcut.WorkingDirectory = $workdir; $shortcut.IconLocation = $icon; $shortcut.Save()"
if errorlevel 1 (
    echo.
    echo Failed to update shortcut.
    pause
    exit /b 1
)

echo.
echo Done! Run: "%CD%\DNS_Checker.lnk"
pause
