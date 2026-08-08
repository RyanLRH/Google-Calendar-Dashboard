@echo off
REM Installs the Python packages the dashboard needs. Run this once.
cd /d "%~dp0"
echo Installing dependencies...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo Done. Next: put credentials.json in this folder, then run:
echo     python dashboard.pyw
echo (first run only - it opens a browser so you can sign in to Google)
pause
