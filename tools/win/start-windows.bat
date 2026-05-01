@echo off
setlocal enableextensions
rem This launcher does not require Administrator privileges.
rem Avoid UAC prompts so the shared package works on locked-down Windows machines.

for %%I in ("%~dp0..\..") do set "REPO_ROOT=%%~fI"
cd /d "%REPO_ROOT%"

if /I not "%HOLOGRAM_MANAGER_USE_LOCAL_SETTINGS%"=="1" (
    if "%HOLOGRAM_MANAGER_SETTINGS_PATH%"=="" (
        if not "%LOCALAPPDATA%"=="" (
            set "HOLOGRAM_MANAGER_SETTINGS_PATH=%LOCALAPPDATA%\HologramManager\settings.json"
        ) else if not "%APPDATA%"=="" (
            set "HOLOGRAM_MANAGER_SETTINGS_PATH=%APPDATA%\HologramManager\settings.json"
        )
    )
)

set "SYS_PY_CMD=python"
where py >nul 2>&1
if not errorlevel 1 (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "SYS_PY_CMD=py -3"
)

set "VENV_DIR=.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

if not exist "%VENV_PY%" (
    echo Creating virtual environment "%VENV_DIR%"...
    call %SYS_PY_CMD% -m venv "%VENV_DIR%"
)

set "PY_CMD=%SYS_PY_CMD%"
if exist "%VENV_PY%" (
    set "PY_CMD=\"%VENV_PY%\""
)

set "BUNDLED_FFMPEG=%REPO_ROOT%\tools\ffmpeg\ffmpeg.exe"
set "BUNDLED_FFMPEG_BIN=%REPO_ROOT%\tools\ffmpeg\bin\ffmpeg.exe"
if exist "%BUNDLED_FFMPEG_BIN%" (
    set "FFMPEG_PATH=%BUNDLED_FFMPEG_BIN%"
    set "PATH=%REPO_ROOT%\tools\ffmpeg\bin;%PATH%"
) else if exist "%BUNDLED_FFMPEG%" (
    set "FFMPEG_PATH=%BUNDLED_FFMPEG%"
    set "PATH=%REPO_ROOT%\tools\ffmpeg;%PATH%"
)

where ffmpeg >nul 2>&1
if errorlevel 1 (
    echo [!] Warning: ffmpeg not found. MP4/PNG/GIF conversion will fail until ffmpeg.exe is bundled in tools\ffmpeg.
)

echo Ensuring Python dependencies are installed...
call %PY_CMD% -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    call %PY_CMD% -m ensurepip --upgrade >nul 2>&1
)
call %PY_CMD% -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    if exist "%VENV_PY%" (
        echo [!] Warning: pip not available in "%VENV_DIR%". Falling back to system Python...
        set "PY_CMD=%SYS_PY_CMD%"
    )
)

call %PY_CMD% -m pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [!] ERROR: pip is not available.
    echo [!] Reinstall Python with pip/ensurepip support, or install pip manually.
    echo.
    pause
    exit /b 1
)

call %PY_CMD% -m pip install --upgrade pip >nul 2>&1

if exist "requirements.txt" (
    set "WHEELS_DIR=vendor\wheels"
    if exist "%WHEELS_DIR%\*.whl" (
        call %PY_CMD% -m pip install --no-index --find-links "%WHEELS_DIR%" -r requirements.txt
    ) else (
        call %PY_CMD% -m pip install -r requirements.txt
    )
) else (
    call %PY_CMD% -m pip install flask
)

call %PY_CMD% -c "import flask" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo [!] ERROR: Dependencies still missing after install.
    echo [!] Try running: python -m pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo Starting Hologram Fan Playlist Manager...
call %PY_CMD% src\server.py

pause
