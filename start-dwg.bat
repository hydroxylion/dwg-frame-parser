@echo off
rem dwg-frame-parser launcher - isolated port to avoid conflicts with other local projects
rem To use a different port, edit FLASK_PORT below or set it before running.
set FLASK_PORT=5001
cd /d "%~dp0"
python app.py
pause
