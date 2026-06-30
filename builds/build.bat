@echo off
REM SteamCast Windows build script
REM Run this from the steamcast/ directory on a Windows machine with Python 3.11+

echo === SteamCast Build ===
echo.

REM Check for Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.11+ from https://python.org
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
pip install -r ..\requirements.txt pyinstaller
if errorlevel 1 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)

echo [2/3] Building steamcast.exe...
pyinstaller --clean --onefile --name steamcast --console steamcast.spec
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed
    pause
    exit /b 1
)

echo [3/3] Copying to builds/...
move /Y dist\steamcast.exe builds\steamcast.exe
if errorlevel 1 (
    echo [ERROR] Could not move binary
    pause
    exit /b 1
)

echo.
echo === Build complete ===
echo    builds\steamcast.exe
echo.
echo To distribute: send the .exe along with empty input/ and output/ folders.
echo Users do NOT need Python or Rich installed.
pause
