@echo off
rem Leave this window open: it takes "Upload the file on the Marketplace" jobs from the web app.
cd /d "%~dp0"
python agent.py
pause
