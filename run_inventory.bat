@echo off
cd /d "%~dp0"
python run_inventory.py %*
pause
