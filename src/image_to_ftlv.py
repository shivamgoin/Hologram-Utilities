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


def _jpeg_dims(jpeg: bytes) -> tuple[int, int] | None:
    if len(jpeg) < 4 or jpeg[0:2] != b"\xFF\xD8":
        return None
    pos = 2
    while pos + 3 < len(jpeg):
        if jpeg[pos] != 0xFF:
            pos += 1
            continue
        while pos < len(jpeg) and jpeg[pos] == 0xFF:
            pos += 1
        if pos >= len(jpeg):
            break
        marker = jpeg[pos]
        pos += 1
        if marker in (0xD9, 0xDA):
            break
        if pos + 2 > len(jpeg):
            break
        seglen = struct.unpack(">H", jpeg[pos : pos + 2])[0]
        pos += 2
        if marker in (0xC0, 0xC2) and pos + 5 <= len(jpeg):
            # precision = jpeg[pos]
            height = struct.unpack(">H", jpeg[pos + 1 : pos + 3])[0]
            width = struct.unpack(">H", jpeg[pos + 3 : pos + 5])[0]
            return int(width), int(height)
        pos += max(0, seglen - 2)
    return None


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


def convert_image_to_ftlv(
    *,
    image_path: Path,
    out_path: Path,
    fps: int = 20,
    require_672: bool = True,
    frame_size: int = 672,
) -> None:
    data = image_path.read_bytes()
    dims = _jpeg_dims(data)
    if dims is None:
        raise RuntimeError("Only JPEG is supported without ffmpeg. Provide a 672x672 .jpg/.jpeg.")
    w, h = dims
    if require_672 and (w != frame_size or h != frame_size):
        raise RuntimeError(f"JPEG must be {frame_size}x{frame_size}, got {w}x{h}. Install ffmpeg for auto resize.")

    frame_duration_us = int(round(1_000_000 / fps))
    duration_ms = int(round(frame_duration_us / 1000))
    duration_s = int(math.ceil(frame_duration_us / 1_000_000))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = out_path.with_suffix(out_path.suffix + ".tmp")

    with open(tmp_out, "wb") as out_f:
        out_f.write(b"\x00" * HEADER_SIZE)

        offsets_sizes: list[tuple[int, int]] = []
        video_size_with_pad = 0
        audio_size_with_pad = 0

        # Video (single JPEG)
        v_off = out_f.tell()
        v_size = len(data)
        v_pad = _pad4(v_size)
        out_f.write(data)
        if v_pad:
            out_f.write(b"\x00" * v_pad)
        offsets_sizes.append((v_off, v_size))
        video_size_with_pad += v_size + v_pad

        # Audio (silent)
        a_off, a_size, a_pad = _write_silence(out_f, duration_ms=duration_ms)
        offsets_sizes.append((a_off, a_size))
        audio_size_with_pad += a_size + a_pad

        # Index
        index_off = out_f.tell()
        out_f.write(INDEX_PREFIX)
        for off, size in offsets_sizes:
            out_f.write(struct.pack("<II", int(off), int(size)))
        index_size = out_f.tell() - index_off

        total_size = HEADER_SIZE + video_size_with_pad + audio_size_with_pad + index_size
        header = _build_header(
            total_size=total_size,
            video_size=video_size_with_pad,
            audio_size=audio_size_with_pad,
            index_size=index_size,
            frame_duration_us=frame_duration_us,
            frame_count=1,
            duration_s=duration_s,
        )

        out_f.seek(0)
        out_f.write(header)
        out_f.flush()
        os.fsync(out_f.fileno())

    tmp_out.replace(out_path)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Convert a 672x672 JPEG into a 1-frame fan FTLV file.")
    ap.add_argument("--in", dest="inp", required=True, help="Input .jpg/.jpeg path (672x672)")
    ap.add_argument("--out", dest="out", required=True, help="Output FTLV file path (usually no extension)")
    ap.add_argument("--fps", type=int, default=20)
    args = ap.parse_args(argv)

    convert_image_to_ftlv(image_path=Path(args.inp), out_path=Path(args.out), fps=int(args.fps))
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(os.sys.argv[1:]))

