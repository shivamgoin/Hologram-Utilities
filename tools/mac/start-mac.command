#!/bin/zsh

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT" || exit 1

SYS_PY_CMD=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        SYS_PY_CMD="$candidate"
        break
    fi
done

if [[ -z "$SYS_PY_CMD" ]]; then
    echo
    echo "[!] ERROR: Python 3 was not found."
    echo "[!] Install Python 3, then run this launcher again."
    echo
    read -r "?Press Enter to close..."
    exit 1
fi

VENV_DIR=".venv"
VENV_PY="$VENV_DIR/bin/python3"
if [[ ! -x "$VENV_PY" ]]; then
    VENV_PY="$VENV_DIR/bin/python"
fi

if [[ ! -x "$VENV_PY" ]]; then
    echo "Creating virtual environment \"$VENV_DIR\"..."
    "$SYS_PY_CMD" -m venv "$VENV_DIR"
fi

PY_CMD="$SYS_PY_CMD"
if [[ -x "$VENV_PY" ]]; then
    PY_CMD="$VENV_PY"
fi

BUNDLED_FFMPEG_BIN="$REPO_ROOT/tools/ffmpeg/bin/ffmpeg"
BUNDLED_FFMPEG="$REPO_ROOT/tools/ffmpeg/ffmpeg"
if [[ -x "$BUNDLED_FFMPEG_BIN" ]]; then
    export FFMPEG_PATH="$BUNDLED_FFMPEG_BIN"
    export PATH="$REPO_ROOT/tools/ffmpeg/bin:$PATH"
elif [[ -x "$BUNDLED_FFMPEG" ]]; then
    export FFMPEG_PATH="$BUNDLED_FFMPEG"
    export PATH="$REPO_ROOT/tools/ffmpeg:$PATH"
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[!] Warning: ffmpeg not found. MP4/PNG/GIF conversion will fail until ffmpeg is installed or bundled in tools/ffmpeg."
fi

echo "Ensuring Python dependencies are installed..."
if ! "$PY_CMD" -m pip --version >/dev/null 2>&1; then
    "$PY_CMD" -m ensurepip --upgrade >/dev/null 2>&1
fi

if ! "$PY_CMD" -m pip --version >/dev/null 2>&1; then
    if [[ -x "$VENV_PY" ]]; then
        echo "[!] Warning: pip is not available in \"$VENV_DIR\". Falling back to system Python..."
        PY_CMD="$SYS_PY_CMD"
    fi
fi

if ! "$PY_CMD" -m pip --version >/dev/null 2>&1; then
    echo
    echo "[!] ERROR: pip is not available."
    echo "[!] Reinstall Python with pip/ensurepip support, or install pip manually."
    echo
    read -r "?Press Enter to close..."
    exit 1
fi

"$PY_CMD" -m pip install --upgrade pip >/dev/null 2>&1

if [[ -f "requirements.txt" ]]; then
    "$PY_CMD" -m pip install -r requirements.txt || exit 1
else
    "$PY_CMD" -m pip install flask || exit 1
fi

if ! "$PY_CMD" -c "import flask" >/dev/null 2>&1; then
    echo
    echo "[!] ERROR: Dependencies are still missing after install."
    echo "[!] Try running: $PY_CMD -m pip install -r requirements.txt"
    echo
    read -r "?Press Enter to close..."
    exit 1
fi

echo "Starting Hologram Fan Playlist Manager..."
"$PY_CMD" src/server.py

read -r "?Press Enter to close..."
