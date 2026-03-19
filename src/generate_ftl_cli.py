from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from ftl_lis_format import (
    FtlLisEntry,
    build_ftl_lis,
    default_crc_hex8_for_file,
    infer_header_count,
    infer_header_style,
    read_md_ftlv_meta,
    read_reference_crc_map,
    read_reference_order,
)


def _safe_hex8(val: str | None) -> str | None:
    if not val:
        return None
    s = str(val).strip().upper()
    if len(s) != 8 or any(c not in "0123456789ABCDEF" for c in s):
        return None
    return s


def _list_media_files(media_dir: Path) -> list[str]:
    names: list[str] = []
    for p in sorted(media_dir.iterdir()):
        if not p.is_file():
            continue
        if p.name.upper() in {"FTL.LIS", "CONFIG.INI"}:
            continue
        if p.name.startswith("."):
            continue
        names.append(p.name)
    return names


def _write_bytes_if_changed(path: Path, data: bytes) -> bool:
    """
    Write `data` to `path` only if content differs.
    Returns True if a write occurred, False if skipped (already identical).
    """
    try:
        if path.exists() and path.read_bytes() == data:
            return False
    except Exception:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="ftl_", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        Path(tmp_path).replace(path)
        return True
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Generate a fan-compatible FTL.LIS for a media folder.")
    ap.add_argument("--media-dir", default="media", help="Folder containing media files")
    ap.add_argument("--out", default="FTL.LIS", help="Output FTL.LIS path")
    ap.add_argument("--reference", default=None, help="Reference FTL.LIS to copy CRCs and ordering from")
    ap.add_argument("--header-style", choices=["count_fc", "used_len"], default="count_fc")
    ap.add_argument("--header-used-slots", type=int, default=0, help="Used slots when header-style=used_len (0=auto)")
    ap.add_argument("--max-entries", type=int, default=0, help="Max entries to include (0=all)")
    ap.add_argument("--record-count", type=int, default=100, help="Total slots in table (usually 100)")
    args = ap.parse_args(argv)

    media_dir = Path(args.media_dir)
    if not media_dir.exists():
        raise SystemExit(f"media-dir not found: {media_dir}")

    ref_path = Path(args.reference) if args.reference else None
    ref_crc_map = read_reference_crc_map(ref_path) if ref_path else {}
    ref_order = read_reference_order(ref_path) if ref_path else []

    inferred_style = infer_header_style(ref_path) if ref_path else None
    header_style = str(args.header_style or inferred_style or "count_fc").lower().strip()

    inferred_count = infer_header_count(ref_path) if ref_path else None
    header_used_slots = int(args.header_used_slots) if int(args.header_used_slots) > 0 else int(inferred_count or 7)

    enabled = _list_media_files(media_dir)
    ordered: list[str] = []
    if ref_order:
        for name in ref_order:
            if name in enabled and name not in ordered:
                ordered.append(name)
        for name in enabled:
            if name not in ordered:
                ordered.append(name)
    else:
        ordered = enabled

    if args.max_entries and len(ordered) > int(args.max_entries):
        ordered = ordered[: int(args.max_entries)]

    entries: list[FtlLisEntry] = []
    for fname in ordered:
        fp = media_dir / fname
        try:
            v1_u32, v2, _version = read_md_ftlv_meta(fp)
        except Exception:
            continue

        crc = _safe_hex8(ref_crc_map.get(fname)) or default_crc_hex8_for_file(fp)
        entries.append(
            FtlLisEntry(
                name=fname,
                marker=0x0200,
                v1=int(v1_u32) & 0xFFFF,
                v2=int(v2) & 0xFFFF,
                v3=1,
                crc_hex8=crc,
            )
        )

    header_slots = None
    if header_style == "used_len" and header_used_slots > 0:
        if len(entries) > header_used_slots:
            entries = entries[:header_used_slots]
        header_slots = header_used_slots

    out = build_ftl_lis(
        entries,
        record_count=int(args.record_count),
        min_used_slots=7,
        header_used_slots=header_slots,
        header_style=header_style,
    )
    out_path = Path(args.out)
    wrote = _write_bytes_if_changed(out_path, out)
    action = "Wrote" if wrote else "Unchanged"
    print(f"{action} {out_path} ({len(out)} bytes) entries={len(entries)} header_style={header_style}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
