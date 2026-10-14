@echo off
rem First-time setup: checks Python, creates config.json, tests the connection,
rem tests e-mail, and puts a shortcut on the desktop.
cd /d "%~dp0"
title Helper setup

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 setup_assistant.py
    goto done
)
if exist "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe" (
    "%LOCALAPPDATA%\Programs\Python\Python38-32\python.exe" setup_assistant.py
    goto done
)
if exist "C:\Program Files\Python38-32\python.exe" (
    "C:\Program Files\Python38-32\python.exe" setup_assistant.py
    goto done
)
if exist "C:\Python38-32\python.exe" (
    "C:\Python38-32\python.exe" setup_assistant.py
    goto done
)
where python >nul 2>nul
if %errorlevel%==0 (
    python setup_assistant.py
    goto done
)
echo.
echo Python 3.8 was not found.
echo Install python-3.8.10.exe (32-bit) first - see README.md - then run SETUP.bat again.
echo.
pause
:done
