@echo off
setlocal
cd /d "%~dp0"

echo ============================================================
echo WBD Marker QC and Repair - Windows setup
echo ============================================================

set "PYTHON_CMD="
where py >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3"
if not defined PYTHON_CMD (
    where python >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo.
    echo Python was not found.
    echo Install 64-bit Python 3.10 or newer from python.org,
    echo select "Add Python to PATH", then run this file again.
    echo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating local Python environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
echo Installing or updating required packages...
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo.
echo Starting the desktop application...
start "" ".venv\Scripts\pythonw.exe" "wbd_marker_windows_gui.pyw"
exit /b 0

:error
echo.
echo Setup failed. Review the messages above.
pause
exit /b 1
