@echo off
title SteamCast v1.0.0-beta
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0steamcast.ps1" %*
echo.
echo [i] Script exited with code %ERRORLEVEL%
