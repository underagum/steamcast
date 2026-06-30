@echo off
title SteamCast v1.0
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0steamcast.ps1" %*
