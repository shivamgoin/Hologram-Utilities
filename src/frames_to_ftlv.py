from __future__ import annotations

import argparse
import math
import os
import struct
from pathlib import Path


FTLV_MAGIC = b"FTLV"
FTLV_VERSION = 1
HEADER_SIZE = 512
INDEX_PREFIX = bytes([0xA5, 0, 0, 0, 0, 0, 0, 0])


def _pad4(n: int) -> int:
    r = n % 4
    return 0 if r == 0 else (4 - r)


def _write_silence(out_f, *, duration_ms: int, sample_rate: int = 44100) -> tuple[int, int, int]:
    offset = out_f.tell()
    size = int((duration_ms * sample_rate) // 1000)
    pad = _pad4(size)

    chunk = bytes([0x80]) * (1024 * 1024)
    remaining = size
    while remaining > 0:
        n = min(remaining, len(chunk))
        out_f.write(chunk[:n])
        remaining -= n
    if pad:
        out_f.write(b"\x00" * pad)
    return offset, size, pad


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


def build_ftlv_from_frames(
    *,
    frames_dir: Path,
    out_path: Path,
    fps: int = 20,
    silent_audio: bool = True,
) -> None:
    frames = sorted([p for p in frames_dir.iterdir() if p.is_file()])
    if not frames:
        raise RuntimeError(f"No frame files found in: {frames_dir}")

    frame_duration_us = int(round(1_000_000 / fps))
    frame_count = len(frames)
    duration_ms = int(round((frame_count * frame_duration_us) / 1000))
    duration_s = int(math.ceil((frame_count * frame_duration_us) / 1_000_000))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_suffix(out_path.suffix + ".tmp")

    offsets_sizes: list[tuple[int, int]] = []
    video_size_with_pad = 0
    audio_size_with_pad = 0

    with open(tmp_out, "wb") as out_f:
        out_f.write(b"\x00" * HEADER_SIZE)

        for frame in frames:
            offset = out_f.tell()
            data = frame.read_bytes()
            size = len(data)
            pad = _pad4(size)
            out_f.write(data)
            if pad:
                out_f.write(b"\x00" * pad)
            offsets_sizes.append((offset, size))
            video_size_with_pad += size + pad

        if silent_audio:
            a_off, a_size, a_pad = _write_silence(out_f, duration_ms=duration_ms)
            offsets_sizes.append((a_off, a_size))
            audio_size_with_pad += a_size + a_pad

        index_offset = out_f.tell()
        out_f.write(INDEX_PREFIX)
        for off, size in offsets_sizes:
            out_f.write(struct.pack("<II", int(off), int(size)))
        index_size = out_f.tell() - index_offset

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
    ap = argparse.ArgumentParser(description="Build a fan FTLV file from a directory of pre-rendered frames (JPG).")
    ap.add_argument("--frames-dir", required=True, help="Directory containing sequential frame files")
    ap.add_argument("--out", required=True, help="Output FTLV file path (usually no extension)")
    ap.add_argument("--fps", type=int, default=20)
    ap.add_argument("--no-audio", action="store_true", help="Do not add silent audio chunk")
    args = ap.parse_args(argv)

    build_ftlv_from_frames(
        frames_dir=Path(args.frames_dir),
        out_path=Path(args.out),
        fps=int(args.fps),
        silent_audio=not bool(args.no_audio),
    )
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))

