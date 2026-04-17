from __future__ import annotations

import dataclasses
import struct
import zlib
from pathlib import Path


HEADER_SIZE = 16
RECORD_SIZE = 180
RECORD_COUNT_DEFAULT = 100

NAME_FIELD_LEN = 0x64  # 100 bytes, null-terminated, zero-padded

# Record offsets
OFF_MARKER = 0x64  # u16 little-endian (observed 0x0200)
OFF_V1 = 0x66  # u16 little-endian
OFF_V2 = 0x68  # u16 little-endian
OFF_V3 = 0x6A  # u16 little-endian
OFF_CRC = 0x6C  # ascii[8] uppercase hex

# MD/FTLV header offsets
MD_MAGIC = b"FTLV"
MD_OFF_VERSION = 0x04  # u32
MD_OFF_V1 = 0x28  # u32 (fits in u16 for FTL.LIS v1 field in observed samples)
MD_OFF_V2 = 0x2C  # u16


@dataclasses.dataclass(frozen=True)
class FtlLisEntry:
    name: str
    marker: int = 0x0200
    v1: int = 0
    v2: int = 0
    v3: int = 1
    crc_hex8: str = "00000000"

    def normalized_crc(self) -> str:
        crc = (self.crc_hex8 or "").upper()
        if len(crc) != 8 or any(c not in "0123456789ABCDEF" for c in crc):
            raise ValueError(f"crc_hex8 must be 8 hex chars, got {self.crc_hex8!r} for {self.name!r}")
        return crc


def _read_u16le(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 2], "little", signed=False)


def _read_u32le(buf: bytes, off: int) -> int:
    return int.from_bytes(buf[off : off + 4], "little", signed=False)


def parse_ftl_lis(path: Path) -> tuple[int, int, list[FtlLisEntry]]:
    data = path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise ValueError("File too small to be an FTL.LIS")
    header_value = _read_u32le(data, 0)

    payload_len = len(data) - HEADER_SIZE
    if payload_len % RECORD_SIZE != 0:
        raise ValueError(f"Unexpected size: len={len(data)} (payload not multiple of {RECORD_SIZE})")
    record_count = payload_len // RECORD_SIZE

    entries: list[FtlLisEntry] = []
    for i in range(record_count):
        rs = HEADER_SIZE + i * RECORD_SIZE
        rec = data[rs : rs + RECORD_SIZE]

        name_raw = rec[:NAME_FIELD_LEN]
        nul = name_raw.find(b"\x00")
        if nul == -1:
            nul = len(name_raw)
        name = name_raw[:nul].decode("ascii", errors="strict").strip()
        if not name:
            continue

        marker = _read_u16le(rec, OFF_MARKER)
        v1 = _read_u16le(rec, OFF_V1)
        v2 = _read_u16le(rec, OFF_V2)
        v3 = _read_u16le(rec, OFF_V3)
        crc_hex8 = rec[OFF_CRC : OFF_CRC + 8].decode("ascii", errors="strict").upper()

        entries.append(
            FtlLisEntry(name=name, marker=marker, v1=v1, v2=v2, v3=v3, crc_hex8=crc_hex8)
        )

    return header_value, record_count, entries


def build_ftl_lis(
    entries: list[FtlLisEntry],
    *,
    record_count: int = RECORD_COUNT_DEFAULT,
    min_used_slots: int = 7,
    header_used_slots: int | None = None,
    header_style: str = "used_len",
) -> bytes:
    if record_count <= 0:
        raise ValueError("record_count must be > 0")
    if len(entries) > record_count:
        raise ValueError("Too many entries for record_count")

    header_style = str(header_style or "used_len").lower().strip()
    if header_style not in {"used_len", "count_fc"}:
        raise ValueError(f"Unsupported header_style: {header_style!r}")

    if header_style == "count_fc":
        # Observed in multiple fan FTL.LIS files:
        # header_u32 = 0x0000??FC where ?? is the number of non-empty entries.
        # Example: 4 entries -> 0x04FC, 64 entries -> 0x40FC.
        count = len(entries)
        if count > 0xFF:
            raise ValueError("count_fc header_style supports at most 255 entries")
        header_value = (count << 8) | 0xFC
    else:
        if header_used_slots is not None:
            if header_used_slots <= 0:
                raise ValueError("header_used_slots must be > 0")
            if len(entries) > int(header_used_slots):
                raise ValueError(
                    f"entries ({len(entries)}) exceed header_used_slots ({int(header_used_slots)})"
                )
            used_slots = int(header_used_slots)
        else:
            used_slots = max(len(entries), int(min_used_slots))
        header_value = HEADER_SIZE + RECORD_SIZE * used_slots

    out = bytearray(HEADER_SIZE + RECORD_SIZE * record_count)
    out[0:4] = int(header_value).to_bytes(4, "little", signed=False)

    for i, e in enumerate(entries):
        rec = bytearray(RECORD_SIZE)

        name_b = e.name.encode("ascii", errors="strict")
        if len(name_b) >= NAME_FIELD_LEN:
            raise ValueError(f"Entry name too long for {NAME_FIELD_LEN} bytes: {e.name!r}")
        rec[0 : len(name_b)] = name_b
        rec[len(name_b)] = 0

        rec[OFF_MARKER : OFF_MARKER + 2] = int(e.marker).to_bytes(2, "little", signed=False)
        rec[OFF_V1 : OFF_V1 + 2] = int(e.v1).to_bytes(2, "little", signed=False)
        rec[OFF_V2 : OFF_V2 + 2] = int(e.v2).to_bytes(2, "little", signed=False)
        rec[OFF_V3 : OFF_V3 + 2] = int(e.v3).to_bytes(2, "little", signed=False)
        rec[OFF_CRC : OFF_CRC + 8] = e.normalized_crc().encode("ascii", errors="strict")

        start = HEADER_SIZE + i * RECORD_SIZE
        out[start : start + RECORD_SIZE] = rec

    return bytes(out)


def header_count_from_header_value(header_value: int) -> int | None:
    """
    Best-effort: for the common 0x??FC header style, return the entry count (??).
    """
    header_value = int(header_value)
    if header_value < 0:
        return None
    if (header_value & 0xFF) != 0xFC:
        return None
    return (header_value >> 8) & 0xFF


def infer_header_style(reference_ftl_lis: Path) -> str | None:
    """
    Infer which header style a fan uses.
    - "count_fc": header_u32 looks like 0x0000??FC (?? = entry count)
    - "used_len": header_u32 looks like HEADER_SIZE + RECORD_SIZE * used_slots
    """
    if not reference_ftl_lis.exists():
        return None
    header_value, _record_count, _entries = parse_ftl_lis(reference_ftl_lis)
    if header_count_from_header_value(header_value) is not None:
        return "count_fc"
    if header_value >= HEADER_SIZE and (header_value - HEADER_SIZE) % RECORD_SIZE == 0:
        return "used_len"
    return None


def infer_header_count(reference_ftl_lis: Path) -> int | None:
    """
    If the reference uses 0x??FC header, return the entry count.
    """
    if not reference_ftl_lis.exists():
        return None
    header_value, _record_count, _entries = parse_ftl_lis(reference_ftl_lis)
    return header_count_from_header_value(header_value)


def read_md_ftlv_meta(path: Path) -> tuple[int, int, int]:
    """
    Read (v1, v2, version) from an FTLV/MD file.
    """
    with path.open("rb") as f:
        head = f.read(0x30)
    if len(head) < 0x30 or head[0:4] != MD_MAGIC:
        raise ValueError("Not an FTLV/MD file")

    version = _read_u32le(head, MD_OFF_VERSION) & 0xFFFFFFFF
    v1 = _read_u32le(head, MD_OFF_V1) & 0xFFFFFFFF
    v2 = _read_u16le(head, MD_OFF_V2) & 0xFFFF
    return int(v1), int(v2), int(version)


def default_crc_hex8_for_file(path: Path) -> str:
    """
    Fallback when a device-style CRC/id is not known.
    This is *not* guaranteed to match the device's CRC/id, but is stable.
    """
    crc = 0
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            crc = zlib.crc32(chunk, crc)
    return f"{crc & 0xFFFFFFFF:08X}"


def read_reference_crc_map(reference_ftl_lis: Path) -> dict[str, str]:
    """
    Map filename -> crc_hex8 from an existing (reference) FTL.LIS.
    """
    if not reference_ftl_lis.exists():
        return {}
    _, _, entries = parse_ftl_lis(reference_ftl_lis)
    return {e.name: e.crc_hex8.upper() for e in entries}


def read_reference_order(reference_ftl_lis: Path) -> list[str]:
    """
    Return entry names in the order they appear in a reference FTL.LIS.
    """
    if not reference_ftl_lis.exists():
        return []
    _, _, entries = parse_ftl_lis(reference_ftl_lis)
    return [e.name for e in entries]
