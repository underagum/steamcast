@echo off
title SteamCast v1.0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0steamcast.ps1" %*
echo.
echo [i] Script exited with code %ERRORLEVEL%
echo Press any key to close this window...
pause >nul
