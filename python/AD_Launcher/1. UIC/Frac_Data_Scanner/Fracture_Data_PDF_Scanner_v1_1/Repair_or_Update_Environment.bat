@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title Fracture Data PDF Scanner - Rebuild Environment

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

echo This will delete and recreate the scanner's local .venv folder.
echo Your PDF files and scan results will not be changed.
echo.
set /p "CONFIRM=Continue? [Y/N]: "
if /I not "%CONFIRM%"=="Y" exit /b 0

%PYTHON_CMD% "%~dp0bootstrap_fracture_scanner.py" --rebuild --force-reinstall --setup-only
set "RESULT=%ERRORLEVEL%"
echo.
if "%RESULT%"=="0" (
    echo Environment rebuild completed successfully.
) else (
    echo Environment rebuild failed. Review setup_log.txt for details.
)
pause
exit /b %RESULT%
