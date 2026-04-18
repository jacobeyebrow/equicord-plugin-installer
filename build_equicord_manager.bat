@echo off
setlocal
cd /d "%~dp0"

echo Installing build deps (pyinstaller, customtkinter)...
python -m pip install -r requirements-manager-build.txt -q
if errorlevel 1 (
    echo pip failed. Install Python 3.10+ from python.org and try again.
    pause
    exit /b 1
)

echo.
echo Building dist\EquicordManager.exe (one file, no console)...
python -m PyInstaller --noconfirm --clean EquicordManager.spec

if errorlevel 1 (
    echo Build failed.
    pause
    exit /b 1
)

echo.
echo Done. Run: dist\EquicordManager.exe
explorer dist
pause
