#!/bin/zsh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$REPO_ROOT"

OUT_ROOT="$REPO_ROOT/share"
OUT_DIR="$OUT_ROOT/hologram_manager"
ZIP_PATH="$OUT_ROOT/hologram_manager.zip"

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"
mkdir -p "$OUT_DIR/src" "$OUT_DIR/tools/ffmpeg" "$OUT_DIR/tools/win" "$OUT_DIR/tools/mac" "$OUT_DIR/vendor/wheels"

echo "Copying runtime files to: $OUT_DIR"

cp "$REPO_ROOT/README.md" "$OUT_DIR/README.md"
cp "$REPO_ROOT/requirements.txt" "$OUT_DIR/requirements.txt"

rsync -a \
  --exclude "__pycache__" \
  --exclude "*.pyc" \
  --exclude "*.pyo" \
  --exclude "*.log" \
  --exclude ".DS_Store" \
  --exclude "settings.json" \
  --exclude "FTL.LIS" \
  "$REPO_ROOT/src/" "$OUT_DIR/src/"

if [[ -d "$REPO_ROOT/vendor/wheels" ]]; then
  rsync -a --exclude ".DS_Store" "$REPO_ROOT/vendor/wheels/" "$OUT_DIR/vendor/wheels/"
fi

if [[ -d "$REPO_ROOT/tools/ffmpeg" ]]; then
  rsync -a --exclude ".DS_Store" "$REPO_ROOT/tools/ffmpeg/" "$OUT_DIR/tools/ffmpeg/"
fi

cp "$REPO_ROOT/tools/win/start-windows.bat" "$OUT_DIR/tools/win/start-windows.bat"
cp "$REPO_ROOT/tools/mac/start-mac.command" "$OUT_DIR/tools/mac/start-mac.command"

cat > "$OUT_DIR/start-windows.bat" <<'EOF'
@echo off
call "%~dp0tools\win\start-windows.bat"
EOF

cat > "$OUT_DIR/start-mac.command" <<'EOF'
#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/tools/mac/start-mac.command"
EOF
chmod +x "$OUT_DIR/start-mac.command" "$OUT_DIR/tools/mac/start-mac.command"

UNIX_FFMPEG_SOURCE=""
for candidate in "$REPO_ROOT/tools/ffmpeg/ffmpeg" "$REPO_ROOT/tools/ffmpeg/bin/ffmpeg"; do
  if [[ -x "$candidate" ]]; then
    UNIX_FFMPEG_SOURCE="$candidate"
    break
  fi
done

if [[ -z "$UNIX_FFMPEG_SOURCE" ]] && command -v ffmpeg >/dev/null 2>&1; then
  UNIX_FFMPEG_SOURCE="$(command -v ffmpeg)"
fi

DEST_FFMPEG_DIR="$OUT_DIR/tools/ffmpeg"
if [[ -n "$UNIX_FFMPEG_SOURCE" ]]; then
  cp "$UNIX_FFMPEG_SOURCE" "$DEST_FFMPEG_DIR/ffmpeg"
  chmod +x "$DEST_FFMPEG_DIR/ffmpeg"
  echo "Bundled mac/Linux ffmpeg from: $UNIX_FFMPEG_SOURCE"
else
  echo "[!] Warning: unix ffmpeg not found. The package can still run, but MP4 conversion on macOS will require ffmpeg in PATH or a later bundled copy."
fi

WINDOWS_FFMPEG_SOURCE=""
for candidate in "$REPO_ROOT/tools/ffmpeg/ffmpeg.exe" "$REPO_ROOT/tools/ffmpeg/bin/ffmpeg.exe"; do
  if [[ -f "$candidate" ]]; then
    WINDOWS_FFMPEG_SOURCE="$candidate"
    break
  fi
done

if [[ -n "$WINDOWS_FFMPEG_SOURCE" ]]; then
  cp "$WINDOWS_FFMPEG_SOURCE" "$DEST_FFMPEG_DIR/ffmpeg.exe"
  echo "Included Windows ffmpeg from repo: $WINDOWS_FFMPEG_SOURCE"
else
  echo "[!] Warning: Windows ffmpeg.exe not found in tools/ffmpeg. The package can still run, but MP4 conversion on Windows will require ffmpeg in PATH or a later bundled copy."
fi

if command -v xattr >/dev/null 2>&1; then
  xattr -cr "$OUT_DIR" || true
fi

echo "Creating zip: $ZIP_PATH"
rm -f "$ZIP_PATH"
(
  cd "$OUT_ROOT"
  COPYFILE_DISABLE=1 zip -r "$ZIP_PATH" "$(basename "$OUT_DIR")" -x "*.DS_Store" "__MACOSX/*" "*/.DS_Store"
)

echo
echo "Done."
echo "Folder: $OUT_DIR"
echo "Zip:    $ZIP_PATH"
