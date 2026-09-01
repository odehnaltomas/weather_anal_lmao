@echo off
setlocal
set SCRIPT_DIR=%~dp0
set PROJECT_DIR=%SCRIPT_DIR%..\
cd /d "%PROJECT_DIR%"
py -3 -m chmi_downloader.cli collect radar
if errorlevel 1 exit /b %errorlevel%
py -3 -m chmi_downloader.cli collect daily
if errorlevel 1 exit /b %errorlevel%
