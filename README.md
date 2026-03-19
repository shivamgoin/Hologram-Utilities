# File Generator (MP4 -> FTLV)

This folder contains utilities to convert a normal `.mp4` into the fan-playable **FTLV** format (the same header layout observed in `C:\Users\Admin\Downloads\fan generated file\samples\6dtxPAG41\*`).

## Requirements

- `ffmpeg` available in `PATH` (used to extract frames).

## Usage

Convert an MP4 into a fan media file (no extension) and place it into the playlist manager media folder:

```powershell
python "C:\Users\Admin\Downloads\fan generated file\file_generator\mp4_to_ftlv.py" `
  --in "C:\path\to\video.mp4" `
  --out "C:\Users\Admin\Downloads\fan generated file\playlist_manager\media\video_name"
```

Notes:
- Output is written as a single file whose first 512 bytes start with `FTLV`.
- Frames are extracted as 672x672 JPEG at 20 fps (50,000 µs per frame).
- Audio is written as **silent 8-bit unsigned PCM** at 44.1 kHz (matches sample files that start with `0x80` in the audio chunk).

## Images

If you have a **672x672 JPEG**, you can generate a 1-frame playable file without ffmpeg:

```powershell
python "C:\Users\Admin\Downloads\fan generated file\file_generator\image_to_ftlv.py" `
  --in "C:\path\to\image_672.jpg" `
  --out "C:\Users\Admin\Downloads\fan generated file\playlist_manager\media\IMG_0001"
```

If your image is not 672x672, install `ffmpeg` and use the Playlist Manager “File Generator” tab (it will resize/crop).
