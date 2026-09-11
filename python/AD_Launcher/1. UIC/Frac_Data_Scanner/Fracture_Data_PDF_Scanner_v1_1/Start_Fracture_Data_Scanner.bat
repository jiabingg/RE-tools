@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fracture Data PDF Scanner - Setup and Launch

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"

if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo.
    echo ERROR: Python was not found.
    echo Install Python 3.10 or newer, then run this file again.
    echo During Python installation, enable "Add Python to PATH" or install the Python Launcher.
    echo.
    pause
    exit /b 2
)

%PYTHON_CMD% "%~dp0bootstrap_fracture_scanner.py"
set "RESULT=%ERRORLEVEL%"
if not "%RESULT%"=="0" (
    echo.
    echo The scanner could not be started. Review setup_log.txt for details.
    pause
)
exit /b %RESULT%
