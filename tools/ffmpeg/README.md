## Bundled ffmpeg (optional)

This project can use `ffmpeg` from:
- Your system `PATH`, or
- `FFMPEG_PATH` env var, or
- A local copy placed in this folder.

### Windows

Place one of these files:
- `tools/ffmpeg/ffmpeg.exe`, or
- `tools/ffmpeg/bin/ffmpeg.exe`

Then run `tools/win/start-windows.bat`.

### macOS

Place one of these files:
- `tools/ffmpeg/ffmpeg`, or
- `tools/ffmpeg/bin/ffmpeg`

Then run `tools/mac/start-mac.command`.

### Share packages

The packaging scripts try to include both platform launchers in the same package.
If `ffmpeg.exe` and `ffmpeg` are both present here, MP4 conversion works out of the box on both Windows and macOS from the generated package.
