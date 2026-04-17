from __future__ import annotations

import argparse
from pathlib import Path

from ftl_lis_format import FtlLisEntry, build_ftl_lis, read_md_ftlv_meta, read_reference_crc_map


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate an FTL.LIS from media files using CRCs from a reference FTL.LIS, then byte-compare."
    )
    ap.add_argument("--media", default=str(Path(__file__).resolve().parent / "media"), help="Media folder path")
    ap.add_argument(
        "--reference",
        default=str(Path(__file__).resolve().parent.parent / "FTL.LIS"),
        help="Reference FTL.LIS path",
    )
    ap.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parent / "FTL.verified.LIS"),
        help="Where to write the rebuilt file",
    )
    args = ap.parse_args()

    media_dir = Path(args.media)
    ref_path = Path(args.reference)
    out_path = Path(args.output)

    ref_crc = read_reference_crc_map(ref_path)
    if not ref_crc:
        raise SystemExit(f"No entries found in reference: {ref_path}")

    # Keep reference order (as it appears in the reference file).
    # read_reference_crc_map returns a dict, so we re-parse for ordering.
    from ftl_lis_format import parse_ftl_lis

    _, _, ref_entries = parse_ftl_lis(ref_path)
    names_in_order = [e.name for e in ref_entries]

    entries: list[FtlLisEntry] = []
    for name in names_in_order:
        p = media_dir / name
        if not p.exists():
            raise SystemExit(f"Missing media file referenced by FTL.LIS: {p}")
        v1, v2, _ = read_md_ftlv_meta(p)
        entries.append(FtlLisEntry(name=name, v1=v1 & 0xFFFF, v2=v2 & 0xFFFF, v3=1, crc_hex8=ref_crc[name]))

    rebuilt = build_ftl_lis(entries, record_count=100, min_used_slots=7)
    out_path.write_bytes(rebuilt)

    same = rebuilt == ref_path.read_bytes()
    print(f"rebuilt={out_path} bytes={len(rebuilt)} match={same}")
    return 0 if same else 1


if __name__ == "__main__":
    raise SystemExit(main())

