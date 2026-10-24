@echo off
setlocal
cd /d "%~dp0"
if errorlevel 1 goto setup_failed

if exist ".venv\Scripts\python.exe" goto check_dependencies
echo Creating a Python 3.12 environment...
py -3.12 -m venv .venv
if errorlevel 1 goto setup_failed

:check_dependencies
if not exist ".venv\installed-requirements.lock" goto install_dependencies
fc /b "requirements.lock" ".venv\installed-requirements.lock" >nul 2>&1
if errorlevel 1 goto install_dependencies
goto run_app

:install_dependencies
echo Installing dependencies. The first launch needs Internet access.
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.lock
if errorlevel 1 goto setup_failed
copy /y "requirements.lock" ".venv\installed-requirements.lock" >nul
if errorlevel 1 goto setup_failed

:run_app
echo Starting MyDarkRoom. Keep this window open while editing.
echo Press Ctrl+C to stop. Your browser will open when the app is ready.
".venv\Scripts\python.exe" run.py --open-browser
if errorlevel 1 goto run_failed
exit /b 0

:setup_failed
echo.
echo Setup failed. Install Python 3.12 64-bit with the Python launcher enabled.
echo If Python is installed, check the error above and your Internet connection.
pause
exit /b 1

:run_failed
echo.
echo MyDarkRoom could not start. Check the error above.
echo If port 8765 is in use, close the other MyDarkRoom window first.
pause
exit /b 1
