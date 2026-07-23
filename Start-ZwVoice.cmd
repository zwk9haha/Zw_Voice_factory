@echo off
setlocal
chcp 65001 >nul
title Zw Voice Factory Launcher

if not "%~1"=="" goto direct

:menu
cls
echo ========================================================
echo                 Zw Voice Factory 2.0
echo ========================================================
echo.
echo   [1] Start or open WebUI
echo   [2] Show runtime status
echo   [3] Stop managed services
echo   [4] Run full launcher test
echo   [0] Exit
echo.
choice /c 12340 /n /m "Select: "
if errorlevel 5 goto end
if errorlevel 4 call :action test
if errorlevel 3 call :action stop
if errorlevel 2 call :action status
if errorlevel 1 call :action run
goto menu

:action
cls
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_factory.ps1" %1
set "launcher_code=%ERRORLEVEL%"
echo.
if not "%launcher_code%"=="0" echo Launcher command failed with exit code %launcher_code%.
pause
exit /b 0

:direct
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start_factory.ps1" %*
exit /b %ERRORLEVEL%

:end
exit /b 0
