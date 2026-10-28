@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title VP3 Round Wearable Preview
echo.
echo   VP3 - Local Round Screen and Desktop Floating Agent
echo   -------------------------------------------------
echo   This preview runs ONLY on this computer.
echo   It will NOT access your live HomeServer, VP3 Cloud, or microphone.
echo.
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 goto launch_py
)
where python >nul 2>&1
if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
    if not errorlevel 1 goto launch_python
)
echo Python 3.10 or newer is needed to run the simulator.
echo Opening the official Python download page in your browser.
echo Install Python for Windows and double-click this file again.
start "" "https://www.python.org/downloads/windows/"
pause
exit /b 1

:launch_py
echo Starting local VP3 preview...
py -3 "%~dp0simulator_bridge.py" --open
goto finished

:launch_python
echo Starting local VP3 preview...
python "%~dp0simulator_bridge.py" --open

:finished
if errorlevel 1 (
    echo.
    echo Preview stopped with an error. Copy this window's message for support.
    pause
)
endlocal
