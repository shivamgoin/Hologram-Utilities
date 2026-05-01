from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass
from pathlib import Path


INDEX_PREFIX = bytes([0xA5, 0, 0, 0, 0, 0, 0, 0])


def _u32le(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


@dataclass(frozen=True)
class FtlvSummary:
    path: Path
    total_size: int
    header_size: int
    video_size: int
    audio_size: int
    index_size: int
    frame_duration_us: int
    frame_count: int
    duration_seconds: int
    index_offset: int
    index_entries: int
    audio_index: int | None
    ok_prefix: bool


def _read_index_pairs(data: bytes, *, index_offset: int, index_size: int) -> list[tuple[int, int]]:
    table = data[index_offset : index_offset + index_size]
    if len(table) < 8 or table[:8] != INDEX_PREFIX:
        raise ValueError("Index prefix not found at expected offset")
    entries = table[8:]
    if len(entries) % 8 != 0:
        raise ValueError("Index table size is not a multiple of 8 bytes")
    out: list[tuple[int, int]] = []
    for i in range(len(entries) // 8):
        off, size = struct.unpack_from("<II", entries, i * 8)
        out.append((int(off), int(size)))
    return out


def summarize_ftlv(path: Path) -> tuple[FtlvSummary, list[tuple[int, int]]]:
    data = path.read_bytes()
    if len(data) < 0x30 or data[:4] != b"FTLV":
        raise ValueError("Not an FTLV container (missing FTLV magic)")

    total_size = _u32le(data, 0x08)
    header_size = _u32le(data, 0x0C)
    video_size = _u32le(data, 0x10)
    audio_size = _u32le(data, 0x14)
    index_size = _u32le(data, 0x18)
    frame_duration_us = _u32le(data, 0x24)
    frame_count = _u32le(data, 0x28)
    duration_seconds = _u32le(data, 0x2C)

    index_offset = int(header_size) + int(video_size) + int(audio_size)
    ok_prefix = data[index_offset : index_offset + 8] == INDEX_PREFIX
    pairs = _read_index_pairs(data, index_offset=index_offset, index_size=index_size)

    audio_index = None
    for i, (off, size) in enumerate(pairs):
        if off + 2 <= len(data) and data[off : off + 2] != b"\xFF\xD8":
            audio_index = i
            break

    summary = FtlvSummary(
        path=path,
        total_size=total_size,
        header_size=header_size,
        video_size=video_size,
        audio_size=audio_size,
        index_size=index_size,
        frame_duration_us=frame_duration_us,
        frame_count=frame_count,
        duration_seconds=duration_seconds,
        index_offset=index_offset,
        index_entries=len(pairs),
        audio_index=audio_index,
        ok_prefix=ok_prefix,
    )
    return summary, pairs


def validate_layout(summary: FtlvSummary, pairs: list[tuple[int, int]]) -> list[str]:
    problems: list[str] = []

    data = summary.path.read_bytes()

    if len(data) != summary.total_size:
        problems.append(f"total_size mismatch: header={summary.total_size} file_len={len(data)}")

    if summary.header_size != 512:
        problems.append(f"unexpected header_size: {summary.header_size} (expected 512)")

    if not summary.ok_prefix:
        problems.append("index prefix mismatch (expected A5 00 00 00 00 00 00 00)")

    if summary.index_entries != summary.frame_count + 1:
        problems.append(
            f"index entry count mismatch: entries={summary.index_entries} expected={summary.frame_count + 1}"
        )

    # Expect: first frame_count entries are JPEG (FF D8), last entry is audio (non-JPEG).
    if pairs:
        # Video frames
        for i in range(min(summary.frame_count, len(pairs))):
            off, size = pairs[i]
            if off + 2 > len(data) or data[off : off + 2] != b"\xFF\xD8":
                problems.append(f"video entry {i} is not JPEG (offset={off} size={size})")
                break

        # Audio entry last
        last_i = min(len(pairs) - 1, summary.frame_count)
        off, size = pairs[last_i]
        sig2 = data[off : off + 2] if off + 2 <= len(data) else b""
        if sig2 == b"\xFF\xD8":
            problems.append(f"last entry {last_i} looks like JPEG (expected audio)")
        # Typical silence starts with 0x80 0x80; accept any non-JPEG but note if not silence-ish.
        if sig2 and sig2 != b"\x80\x80" and sig2 != b"\x80\x7F":
            # Not a hard failure; just informational.
            problems.append(f"note: audio signature starts with {sig2.hex()} (expected 8080 for silence)")

        # Ensure audio index is last (best-effort).
        if summary.audio_index is not None and summary.audio_index != last_i:
            problems.append(f"audio index is {summary.audio_index} but expected last ({last_i})")

    return problems


def _print_summary(summary: FtlvSummary) -> None:
    fps = (1_000_000 / summary.frame_duration_us) if summary.frame_duration_us else 0.0
    print(f"file: {summary.path}")
    print(f"  total_size: {summary.total_size}")
    print(f"  header_size: {summary.header_size}")
    print(f"  video_size: {summary.video_size}")
    print(f"  audio_size: {summary.audio_size}")
    print(f"  index_size: {summary.index_size}")
    print(f"  index_offset: {summary.index_offset}")
    print(f"  frame_duration_us: {summary.frame_duration_us} (fps≈{fps:.3f})")
    print(f"  frame_count: {summary.frame_count}")
    print(f"  duration_seconds: {summary.duration_seconds}")
    print(f"  index_entries: {summary.index_entries}")
    print(f"  audio_index: {summary.audio_index}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify an FTLV container layout (video frames first, audio last).")
    ap.add_argument("--file", dest="file", help="Path to FTLV file")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"), help="Compare two FTLV files (summaries + validations)")
    args = ap.parse_args(argv)

    paths: list[Path] = []
    if args.compare:
        paths = [Path(args.compare[0]), Path(args.compare[1])]
    elif args.file:
        paths = [Path(args.file)]
    else:
        ap.error("Provide --file or --compare")

    exit_code = 0
    for p in paths:
        s, pairs = summarize_ftlv(p)
        _print_summary(s)
        problems = validate_layout(s, pairs)
        if problems:
            print("  validation:")
            for msg in problems:
                print(f"    - {msg}")
            # "note:" lines are informational; treat others as failure.
            hard = [m for m in problems if not m.startswith("note:")]
            if hard:
                exit_code = 1
        else:
            print("  validation: OK")
        print()

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())

