from __future__ import annotations

import argparse
import math
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path


FTLV_MAGIC = b"FTLV"
FTLV_VERSION = 1
HEADER_SIZE = 512
INDEX_PREFIX = bytes([0xA5, 0, 0, 0, 0, 0, 0, 0])


def _pad4(n: int) -> int:
    r = n % 4
    return 0 if r == 0 else (4 - r)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}")


def _ensure_ffmpeg() -> str:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found in PATH. Install ffmpeg, or extract 672x672 JPG frames "
            "with another tool and use a frames-to-FTLV workflow."
        )
    return ffmpeg


def _extract_frames(ffmpeg: str, mp4: Path, out_dir: Path, *, fps: int, size: int, quality: int) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pattern = str(out_dir / "frame_%06d.jpg")

    # Keep it simple and match the sample: square 672x672 JPEG frames.
    # - scale: cover then crop to square
    vf = f"fps={fps},scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}"

    # ffmpeg JPEG quality: 2(best) .. 31(worst). Map 0..100 into 31..2.
    q = max(2, min(31, int(round(31 - (quality / 100.0) * 29))))

    # Force 4:2:0 chroma subsampling for hardware decoders
    _run([ffmpeg, "-y", "-i", str(mp4), "-vf", vf, "-pix_fmt", "yuvj420p", "-q:v", str(q), pattern])

    frames = sorted(out_dir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError("No frames extracted (ffmpeg produced 0 JPG files)")
    return frames


def _extract_audio(ffmpeg: str, mp4: Path, *, duration_ms: int, sample_rate: int = 44100) -> bytes:
    """
    Extract audio as raw unsigned 8-bit PCM, mono, 44100Hz.
    If no audio found or error, returns silence.
    """

    target_size = int((duration_ms * sample_rate) // 1000)
    
    # Use a temp path but close it immediately for Windows
    fd, tmp_path = tempfile.mkstemp(suffix=".pcm")
    os.close(fd)
    
    try:
        # -ac 1: mono
        # -ar 44100: sample rate
        # -f u8: unsigned 8-bit
        # -t: limit to duration
        # -af aresample: ensure sync and consistent sample rate
        cmd = [
            ffmpeg, "-y", "-i", str(mp4),
            "-vn", "-f", "u8", "-ac", "1", "-ar", str(sample_rate),
            "-filter_complex", "aresample=async=1:min_hard_comp=0.100000:first_pts=0",
            "-t", f"{duration_ms / 1000.0:.3f}",
            tmp_path
        ]


        
        # Run and capture
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        if proc.returncode == 0 and os.path.exists(tmp_path):
            data = Path(tmp_path).read_bytes()
            if not data:
                return bytes([0x80]) * target_size
                
            if len(data) > target_size:
                data = data[:target_size]
            elif len(data) < target_size:
                data += bytes([0x80]) * (target_size - len(data))
            return data
    except Exception:
        pass
    finally:
        if os.path.exists(tmp_path):
            try: os.remove(tmp_path)
            except: pass
            
    return bytes([0x80]) * target_size


def _build_header(
    *,
    total_size: int,
    video_size: int,
    audio_size: int,
    index_size: int,
    frame_duration_us: int,
    frame_count: int,
    duration_s: int,
) -> bytes:

    """
    Header layout observed in sample files:
      0x00  'FTLV'
      0x04  u32 version
      0x08  u32 totalSize
      0x0C  u32 headerSize (512)
      0x10  u32 videoSize (with padding)
      0x14  u32 audioSize (with padding)
      0x18  u32 indexSize
      0x1C  u32 reserved (0)
      0x20  8 bytes reserved (0)
      0x24  u32 frameDurationUs (usually 50000)
      0x28  u32 frameCount
      0x2C  u32 durationSeconds (low 16 bits used)
      rest  zeros
    """
    hdr = bytearray(b"\x00" * HEADER_SIZE)
    hdr[0:4] = FTLV_MAGIC
    struct.pack_into("<I", hdr, 0x04, int(FTLV_VERSION))
    struct.pack_into("<I", hdr, 0x08, int(total_size))
    struct.pack_into("<I", hdr, 0x0C, int(HEADER_SIZE))
    struct.pack_into("<I", hdr, 0x10, int(video_size))
    struct.pack_into("<I", hdr, 0x14, int(audio_size))
    struct.pack_into("<I", hdr, 0x18, int(index_size))
    struct.pack_into("<I", hdr, 0x24, int(frame_duration_us))
    struct.pack_into("<I", hdr, 0x28, int(frame_count))
    struct.pack_into("<I", hdr, 0x2C, int(duration_s))
    return bytes(hdr)


def convert_mp4_to_ftlv(
    *,
    mp4: Path,
    out_path: Path,
    fps: int = 20,
    frame_size: int = 672,
    jpeg_quality: int = 50,
    frame_duration_us: int = 50_000,
) -> None:
    ffmpeg = _ensure_ffmpeg()

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ftlv_frames_") as tmp:
        frames_dir = Path(tmp)
        frames = _extract_frames(ffmpeg, mp4, frames_dir, fps=fps, size=frame_size, quality=jpeg_quality)
        frame_count = len(frames)

        duration_ms = int(round((frame_count * frame_duration_us) / 1000))
        duration_s = int(math.ceil((frame_count * frame_duration_us) / 1_000_000))

        tmp_out = out_path.with_suffix(out_path.suffix + ".tmp")
        offsets_sizes: list[tuple[int, int]] = []

        video_size_with_pad = 0
        audio_size_with_pad = 0

        with open(tmp_out, "wb") as out_f:
            out_f.write(b"\x00" * HEADER_SIZE)

            # 1. Extract audio data first
            audio_data = _extract_audio(ffmpeg, mp4, duration_ms=duration_ms)
            a_size = len(audio_data)
            a_pad = _pad4(a_size)

            # 2. Add Audio Index Entry (Many fans expect Audio at index 0)
            # We'll write audio after video, but we need the offset.
            video_start_offset = out_f.tell()
            
            # Temporary storage for frames
            video_data_list = []
            for frame in frames:
                data = frame.read_bytes()
                video_data_list.append(data)
                
            # Now we know video size
            video_size_total = sum(len(d) + _pad4(len(d)) for d in video_data_list)
            audio_start_offset = video_start_offset + video_size_total
            
            # Add AUDIO entry at index 0
            offsets_sizes.append((audio_start_offset, a_size))

            # 3. Add Video index entries (starting from index 1)
            current_off = video_start_offset
            for data in video_data_list:
                size = len(data)
                pad = _pad4(size)
                offsets_sizes.append((current_off, size))
                # Write video frame
                out_f.write(data)
                if pad:
                    out_f.write(b"\x00" * pad)
                current_off += size + pad
            
            video_size_with_pad = video_size_total

            # 4. Write audio data (it should be at audio_start_offset now)
            out_f.write(audio_data)
            if a_pad:
                out_f.write(b"\x00" * a_pad)
            audio_size_with_pad = a_size + a_pad

            # 5. Index table
            index_offset = out_f.tell()
            out_f.write(INDEX_PREFIX)
            for off, size in offsets_sizes:
                out_f.write(struct.pack("<II", int(off), int(size)))
            index_size = (out_f.tell() - index_offset)

            total_size = HEADER_SIZE + video_size_with_pad + audio_size_with_pad + index_size


            header = _build_header(
                total_size=total_size,
                video_size=video_size_with_pad,
                audio_size=audio_size_with_pad,
                index_size=index_size,
                frame_duration_us=frame_duration_us,
                frame_count=frame_count,
                duration_s=duration_s,
            )

            out_f.seek(0)
            out_f.write(header)
            out_f.flush()
            os.fsync(out_f.fileno())

        tmp_out.replace(out_path)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Convert an MP4 into a fan-playable FTLV file (672x672 JPG frames + silent audio).")
    ap.add_argument("--in", dest="inp", required=True, help="Input .mp4 path")
    ap.add_argument("--out", dest="out", required=True, help="Output file path (usually no extension)")
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--size", type=int, default=672)
    ap.add_argument("--quality", type=int, default=50, help="0..100 (mapped to ffmpeg -q:v)")
    args = ap.parse_args(argv)

    convert_mp4_to_ftlv(
        mp4=Path(args.inp),
        out_path=Path(args.out),
        fps=int(args.fps),
        frame_size=int(args.size),
        jpeg_quality=int(args.quality),
        frame_duration_us=int(round(1_000_000 / int(args.fps))),
    )
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))
