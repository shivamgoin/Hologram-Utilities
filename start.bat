@echo off
echo Checking for Python Flask...
python -m flask --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Flask not found. Installing Flask...
    pip install flask
)
echo Starting Hologram Fan Playlist Manager...
echo Open http://127.0.0.1:5000 in your browser.
python src/server.py
pause
