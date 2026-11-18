@echo off
rem One-time setup on a new laptop (also safe to run again after "git pull").
cd /d "%~dp0"
echo === MyDeal inventory bot setup ===
where python >nul 2>&1
if errorlevel 1 (
  echo Python not found. Install Python 3.11 or newer from python.org ^(tick "Add python.exe to PATH"^), then run setup.bat again.
  exit /b 1
)
python -m pip install -U -r requirements.txt
if errorlevel 1 (
  echo pip install failed. Check the internet connection and run setup.bat again.
  exit /b 1
)
if not exist input mkdir input
python setup_check.py
exit /b %errorlevel%
