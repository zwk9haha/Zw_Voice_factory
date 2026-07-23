@echo off
setlocal
chcp 65001 >nul
title Zw Voice Factory

if /i "%~1"=="run" goto launch_visible
if /i "%~1"=="own" goto own
if not "%~1"=="" goto direct

powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_factory.ps1" focus >nul 2>&1
if "%ERRORLEVEL%"=="0" exit /b 0
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\launcher_menu.ps1"
exit /b %ERRORLEVEL%

:direct
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_factory.ps1" %*
exit /b %ERRORLEVEL%

:launch_visible
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_factory.ps1" status >nul 2>&1
if "%ERRORLEVEL%"=="0" (
    powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_factory.ps1" run
    exit /b %ERRORLEVEL%
)
start "Zw Voice Factory" "%ComSpec%" /d /c ""%~f0" own"
exit /b 0

:own
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_factory.ps1" run
set "launcher_code=%ERRORLEVEL%"
if not "%launcher_code%"=="0" pause
exit /b %launcher_code%
