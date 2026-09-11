@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fracture Data PDF Scanner - Environment Setup

set "PYTHON_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>nul
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo ERROR: Python 3.10 or newer was not found.
    pause
    exit /b 2
)

%PYTHON_CMD% "%~dp0bootstrap_fracture_scanner.py" --setup-only
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
    echo Environment setup completed successfully.
) else (
    echo Environment setup failed. Review setup_log.txt for details.
)
pause
exit /b %RESULT%
