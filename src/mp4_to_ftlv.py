from __future__ import annotations

import argparse
import array
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path


FTLV_MAGIC = b"FTLV"
FTLV_VERSION = 1
HEADER_SIZE = 512
INDEX_PREFIX = bytes([0xA5, 0, 0, 0, 0, 0, 0, 0])
AUDIO_SAMPLE_RATE = 44_100
AUDIO_HEADROOM = 0.86
AUDIO_DITHER_LSB = 0.35
AUDIO_HIGH_PASS_HZ = 38
AUDIO_LOW_PASS_HZ = 15_000
AUDIO_FILTER_CHAINS = [
    ",".join(
        [
            "pan=mono|c0=0.5*c0+0.5*c1",
            f"highpass=f={AUDIO_HIGH_PASS_HZ}",
            f"lowpass=f={AUDIO_LOW_PASS_HZ}",
            "aresample=44100:async=1:min_hard_comp=0.100000:first_pts=0",
            "volume=0.80",
            "acompressor=threshold=-18dB:ratio=2.0:attack=5:release=50:makeup=1",
            "alimiter=limit=0.88",
        ]
    ),
    ",".join(
        [
            f"highpass=f={AUDIO_HIGH_PASS_HZ}",
            f"lowpass=f={AUDIO_LOW_PASS_HZ}",
            "aresample=44100:async=1:min_hard_comp=0.100000:first_pts=0",
            "volume=0.80",
            "acompressor=threshold=-18dB:ratio=2.0:attack=5:release=50:makeup=1",
        ]
    ),
]


def _pad4(n: int) -> int:
    r = n % 4
    return 0 if r == 0 else (4 - r)


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}")


def _ensure_ffmpeg() -> str:
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return str(p)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        return ffmpeg

    # Support a bundled ffmpeg in this repo: tools/ffmpeg/(bin/)ffmpeg.exe
    exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    repo_root = Path(__file__).resolve().parent.parent
    candidates = [
        repo_root / "tools" / "ffmpeg" / "bin" / exe_name,
        repo_root / "tools" / "ffmpeg" / exe_name,
    ]
    for c in candidates:
        if c.exists() and c.is_file():
            return str(c)

    raise RuntimeError(
        "ffmpeg not found. Install ffmpeg, or place it at tools/ffmpeg (or set FFMPEG_PATH). "
        "You can also extract 672x672 JPG frames and use frames_to_ftlv.py."
    )


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


def _silence_u8(sample_count: int) -> bytes:
    return bytes([0x80]) * max(0, int(sample_count))


def _next_tpdf_noise(state: int) -> tuple[int, float]:
    state = (1664525 * state + 1013904223) & 0xFFFFFFFF
    a = state / 4294967295.0
    state = (1664525 * state + 1013904223) & 0xFFFFFFFF
    b = state / 4294967295.0
    return state, (a - b)


def _s16le_mono_to_u8_pcm(raw_pcm: bytes, *, target_samples: int) -> bytes:
    if target_samples <= 0:
        return b""
    if not raw_pcm:
        return _silence_u8(target_samples)

    samples = array.array("h")
    samples.frombytes(raw_pcm[: (len(raw_pcm) // 2) * 2])
    if sys.byteorder != "little":
        samples.byteswap()

    if not samples:
        return _silence_u8(target_samples)

    working_count = min(len(samples), target_samples)
    peak = max(abs(int(samples[i])) for i in range(working_count)) if working_count else 0
    safe_peak = max(1, int(round(32767 * AUDIO_HEADROOM)))
    gain = min(1.0, safe_peak / float(peak)) if peak else 1.0

    out = bytearray()
    rng_state = 0x13579BDF
    for i in range(working_count):
        sample = float(samples[i]) * gain
        rng_state, dither = _next_tpdf_noise(rng_state)
        value = int(round((sample / 256.0) + 128.0 + (dither * AUDIO_DITHER_LSB)))
        if value < 0:
            value = 0
        elif value > 255:
            value = 255
        out.append(value)

    if len(out) < target_samples:
        out.extend(_silence_u8(target_samples - len(out)))
    elif len(out) > target_samples:
        del out[target_samples:]
    return bytes(out)


def _decode_audio_pcm16(ffmpeg: str, mp4: Path, *, duration_ms: int, sample_rate: int) -> bytes | None:
    fd, tmp_path = tempfile.mkstemp(suffix=".pcm")
    os.close(fd)
    try:
        for filter_chain in AUDIO_FILTER_CHAINS:
            cmd = [
                ffmpeg,
                "-y",
                "-i",
                str(mp4),
                "-vn",
                "-t",
                f"{duration_ms / 1000.0:.3f}",
                "-ac",
                "1",
                "-ar",
                str(sample_rate),
                "-af",
                filter_chain,
                "-f",
                "s16le",
                "-acodec",
                "pcm_s16le",
                tmp_path,
            ]
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if proc.returncode == 0 and os.path.exists(tmp_path):
                data = Path(tmp_path).read_bytes()
                if data:
                    return data
                return b""
        return None
    except Exception:
        return None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def _extract_audio(ffmpeg: str, mp4: Path, *, duration_ms: int, sample_rate: int = AUDIO_SAMPLE_RATE) -> bytes:
    """
    Extract audio as safer device-friendly unsigned 8-bit PCM, mono, 44.1 kHz.
    If no audio is found or decoding fails, return silence.
    """
    target_samples = int((duration_ms * sample_rate) // 1000)
    pcm16 = _decode_audio_pcm16(ffmpeg, mp4, duration_ms=duration_ms, sample_rate=sample_rate)
    if pcm16 is None:
        return _silence_u8(target_samples)
    return _s16le_mono_to_u8_pcm(pcm16, target_samples=target_samples)


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

            # 2. Determine layout offsets.
            # Device reference files commonly list ALL video frames first in the index table,
            # then put the audio entry LAST. We follow that ordering to avoid playback glitches.
            video_start_offset = out_f.tell()
            
            # Temporary storage for frames
            video_data_list = []
            for frame in frames:
                data = frame.read_bytes()
                video_data_list.append(data)
                
            # Now we know video size
            video_size_total = sum(len(d) + _pad4(len(d)) for d in video_data_list)
            audio_start_offset = video_start_offset + video_size_total

            # 3. Add Video index entries first (frame 0..N-1)
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

            # Audio entry LAST in the index table (after all video frames).
            offsets_sizes.append((audio_start_offset, a_size))

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
    ap = argparse.ArgumentParser(description="Convert an MP4 into a fan-playable FTLV file (672x672 JPG frames + conditioned mono audio).")
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
