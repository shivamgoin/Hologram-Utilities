import os
import json
import sys
import atexit
from pathlib import Path
import tempfile
import subprocess
import shutil
import struct
import io
from functools import lru_cache

try:
    from flask import Flask, render_template, request, send_file, jsonify
    from werkzeug.utils import secure_filename
except ImportError:
    print("\n[!] ERROR: Flask is not installed.")
    print("[!] Please run 'pip install flask' or use 'start.bat' to launch the manager.\n")
    sys.exit(1)

# For directory browsing
try:
    import tkinter as tk
    from tkinter import filedialog
    HAS_TKINTER = True
except ImportError:
    HAS_TKINTER = False

def _default_settings_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "HologramManager"
    return Path.home() / ".hologram_manager"

def _settings_json_path() -> Path:
    """
    Settings location priority:
    1) `HOLOGRAM_MANAGER_SETTINGS_PATH` env var (full file path)
    2) legacy `src/settings.json` if present (back-compat for existing installs)
    3) per-user settings in AppData/Home (better for sharing the project)
    """
    explicit = os.environ.get("HOLOGRAM_MANAGER_SETTINGS_PATH")
    if explicit:
        return Path(explicit)

    legacy = Path(__file__).resolve().parent / "settings.json"
    if legacy.exists():
        return legacy

    return _default_settings_dir() / "settings.json"

def _find_ffmpeg() -> tuple[str | None, str | None]:
    """
    Returns (ffmpeg_exe_path, ffmpeg_dir_for_PATH).

    Supports:
    - `FFMPEG_PATH` env var pointing to ffmpeg(.exe)
    - `ffmpeg` in PATH
    - bundled `tools/ffmpeg/(bin/)ffmpeg.exe` relative to repo root
    """
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists() and p.is_file():
            return str(p), str(p.parent)

    found = shutil.which("ffmpeg")
    if found:
        return found, None

    try:
        repo_root = Path(BASE_DIR).resolve().parent
        exe_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
        candidates = [
            repo_root / "tools" / "ffmpeg" / "bin" / exe_name,
            repo_root / "tools" / "ffmpeg" / exe_name,
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return str(c), str(c.parent)
    except Exception:
        pass

    return None, None


def _ffmpeg_status() -> dict:
    ffmpeg_exe, ffmpeg_dir = _find_ffmpeg()
    source = None
    if ffmpeg_exe:
        try:
            ffmpeg_path = Path(ffmpeg_exe).resolve()
            repo_root = Path(BASE_DIR).resolve().parent
            try:
                ffmpeg_path.relative_to(repo_root / "tools" / "ffmpeg")
                source = "bundled"
            except ValueError:
                env_path = os.environ.get("FFMPEG_PATH")
                if env_path and Path(env_path).resolve() == ffmpeg_path:
                    source = "env"
                else:
                    source = "path"
        except Exception:
            source = "path"
    return {
        "available": bool(ffmpeg_exe),
        "path": ffmpeg_exe,
        "dir": ffmpeg_dir,
        "source": source,
    }


def _ffmpeg_missing_message() -> str:
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    return f"MP4/PNG/GIF conversion requires ffmpeg. Put {exe} in tools/ffmpeg or set FFMPEG_PATH before sharing this project."


def _detect_media_kind(path: Path, original_name: str) -> str:
    head = path.read_bytes()[:32]
    if head.startswith(b"\xFF\xD8"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return "gif"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    if b"ftyp" in head[:24]:
        return "mp4"
    lower = (original_name or "").lower()
    if lower.endswith(".mp4"):
        return "mp4"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "jpeg"
    if lower.endswith(".png"):
        return "png"
    if lower.endswith(".gif"):
        return "gif"
    return "unknown"


def _kind_requires_ffmpeg(kind: str) -> bool:
    return kind in {"mp4", "png", "gif", "unknown"}


def _preflight_ffmpeg_requirements(files, ffmpeg_available: bool) -> tuple[bool, str | None]:
    if ffmpeg_available:
        return True, None

    blocked: list[str] = []
    for uploaded in files:
        name = secure_filename(getattr(uploaded, "filename", "") or "")
        if not name:
            continue
        try:
            pos = uploaded.stream.tell()
        except Exception:
            pos = None
        try:
            sample = uploaded.stream.read(32)
        except Exception:
            sample = b""
        try:
            if pos is not None:
                uploaded.stream.seek(pos)
            else:
                uploaded.stream.seek(0)
        except Exception:
            pass
        kind = "unknown"
        if sample.startswith(b"\xFF\xD8"):
            kind = "jpeg"
        elif sample.startswith(b"\x89PNG\r\n\x1a\n"):
            kind = "png"
        elif sample.startswith(b"GIF87a") or sample.startswith(b"GIF89a"):
            kind = "gif"
        elif len(sample) >= 12 and sample[4:8] == b"ftyp":
            kind = "mp4"
        elif b"ftyp" in sample[:24]:
            kind = "mp4"
        else:
            lower = name.lower()
            if lower.endswith(".mp4"):
                kind = "mp4"
            elif lower.endswith(".jpg") or lower.endswith(".jpeg"):
                kind = "jpeg"
            elif lower.endswith(".png"):
                kind = "png"
            elif lower.endswith(".gif"):
                kind = "gif"
        if _kind_requires_ffmpeg(kind):
            blocked.append(name)

    if not blocked:
        return True, None

    if len(blocked) == 1:
        return False, f"{_ffmpeg_missing_message()} Blocked file: {blocked[0]}"
    return False, f"{_ffmpeg_missing_message()} Blocked files: {', '.join(blocked)}"


def load_settings():

    path = str(_settings_json_path())
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                raw = f.read()
            if not raw.strip():
                return {}
            return json.loads(raw)
        except json.JSONDecodeError:
            # Corrupted/partial settings file (often from an interrupted write). Back it up and continue.
            try:
                import time as _time
                backup = f"{path}.corrupt.{int(_time.time())}"
                os.replace(path, backup)
            except Exception:
                pass
            return {}
        except Exception:
            return {}
    return {}

def save_settings(settings):
    path = str(_settings_json_path())
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(prefix="settings_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

def _minimal_settings(settings: dict, media_names: list[str] | None = None) -> dict:
    """
    Keep settings file small and share-friendly:
    - Always keep `__generator`
    - Keep per-file entries only when they override defaults:
      - `enabled: false` (default is true)
      - a valid `crc` override (user-specified)
    - Drop entries for files that are not currently in MEDIA_FOLDER
    """
    out: dict = {}

    gen = settings.get("__generator")
    if isinstance(gen, dict):
        out["__generator"] = gen

    if media_names is None:
        return out

    media_set = set(media_names)
    for name in media_names:
        st = settings.get(name, {})
        if not isinstance(st, dict):
            continue

        entry: dict = {}
        if "enabled" in st and bool(st.get("enabled")) is False:
            entry["enabled"] = False

        if bool(st.get("crc_manual")) is True:
            crc = _safe_hex8(st.get("crc"))
            if crc:
                entry["crc"] = crc
                entry["crc_manual"] = True

        if entry:
            out[name] = entry

    # Also keep overrides for files that still exist but weren't in media_names list for any reason.
    for k, v in list(settings.items()):
        if k == "__generator":
            continue
        if k in out:
            continue
        if k in media_set and isinstance(v, dict):
            crc = _safe_hex8(v.get("crc")) if (bool(v.get("crc_manual")) is True) else None
            enabled_false = ("enabled" in v and bool(v.get("enabled")) is False)
            if enabled_false or crc:
                out[k] = {}
                if enabled_false:
                    out[k]["enabled"] = False
                if crc:
                    out[k]["crc"] = crc
                    out[k]["crc_manual"] = True

    return out

def _maybe_prune_settings(settings: dict) -> dict:
    try:
        media = _list_media_files()
    except Exception:
        media = None
    pruned = _minimal_settings(settings, media_names=media)
    save_settings(pruned)
    return pruned


app = Flask(__name__)

# Task tracking for conversions
import threading
import time
import uuid

# Global tasks dictionary
# task_id -> {status, progress, input, output, results, start_time}
conversion_tasks = {}

def get_task_status(task_id):
    return conversion_tasks.get(task_id, {"status": "not_found"})

player_file_tokens: dict[str, dict] = {}
PLAYER_TOKEN_TTL_S = 60 * 30  # 30 minutes

def _settings_housekeeping() -> None:
    try:
        update_paths()
        _maybe_prune_settings(load_settings())
    except Exception:
        pass

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# These will be updated dynamically
MEDIA_FOLDER = os.path.join(BASE_DIR, 'media')
OUTPUT_FILE = os.path.join(BASE_DIR, 'FTL.LIS')

SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')
CONFIG_INI_FILE = os.path.abspath(os.path.join(BASE_DIR, '..', 'config.ini'))
REFERENCE_FTL_LIS = os.path.abspath(os.path.join(BASE_DIR, '..', 'FTL.LIS'))
REFERENCE_DIR = os.path.join(BASE_DIR, "reference")
os.makedirs(REFERENCE_DIR, exist_ok=True)
REFERENCE_UPLOAD_PATH = os.path.join(REFERENCE_DIR, "FTL.LIS")

def update_paths():
    global MEDIA_FOLDER, OUTPUT_FILE
    settings = load_settings()
    gen = settings.get("__generator", {})
    target = gen.get("target_directory")
    
    if not target or not os.path.exists(target):
        target = str(Path.home() / "Downloads")
        
    if target and os.path.isdir(target):
        MEDIA_FOLDER = target
        OUTPUT_FILE = os.path.join(target, 'FTL.LIS')
    else:
        MEDIA_FOLDER = BASE_DIR
        OUTPUT_FILE = os.path.join(BASE_DIR, 'FTL.LIS')
    
    app.config['UPLOAD_FOLDER'] = MEDIA_FOLDER


def _load_generator_settings() -> dict:
    update_paths()
    settings = load_settings()
    ffmpeg = _ffmpeg_status()
    media_files = _list_media_files()

    gen = settings.get("__generator", {})

    # Reference playlist: default to repo root FTL.LIS when available; fall back to current output.
    default_ref = REFERENCE_FTL_LIS if os.path.exists(REFERENCE_FTL_LIS) else OUTPUT_FILE
    reference_lis = str(gen.get("reference_lis", default_ref))
    merge_mode = bool(gen.get("merge_mode", False))

    ref_path = Path(REFERENCE_FTL_LIS)
    inferred_style = infer_header_style(ref_path) or "count_fc"
    inferred_count = infer_header_count(ref_path) or 0

    # Back-compat: older settings used header_mode=fixed/dynamic to represent used_len.
    header_style = str(gen.get("header_style") or "").lower().strip()
    if not header_style:
        # If the reference clearly uses count_fc (0x??FC), prefer it even if older settings exist.
        if inferred_style == "count_fc":
            header_style = "count_fc"
        else:
            legacy_mode = str(gen.get("header_mode") or "").lower().strip()
            if legacy_mode in {"fixed", "dynamic"}:
                header_style = "used_len"
                if legacy_mode == "dynamic":
                    gen.setdefault("header_used_slots", 0)
            else:
                header_style = inferred_style

    max_entries_default = 0 if header_style == "count_fc" else (inferred_count or 7)
    max_entries = int(gen.get("max_entries", max_entries_default))
    header_used_slots = int(gen.get("header_used_slots", 0))
    record_count = int(gen.get("record_count", 100))
    target_directory = str(gen.get("target_directory") or Path.home() / "Downloads")
    playlist_order = _sanitize_playlist_order(gen.get("playlist_order", []), media_files)

    if header_style not in {"count_fc", "used_len"}:
        header_style = inferred_style
    if record_count <= 0:
        record_count = 100
    if max_entries < 0:
        max_entries = max_entries_default

    # Persist inferred defaults if missing, so UI/API can display them.
    if "__generator" not in settings:
        settings["__generator"] = {}
    settings["__generator"].update(
        {
            "max_entries": max_entries,
            "header_style": header_style,
            "header_used_slots": header_used_slots,
            "record_count": record_count,
            "reference_lis": reference_lis,
            "merge_mode": merge_mode,
            "target_directory": target_directory,
            "playlist_order": playlist_order,
        }
    )
    _maybe_prune_settings(settings)
    update_paths()
    payload = dict(settings["__generator"])
    payload["ffmpegAvailable"] = ffmpeg["available"]
    payload["ffmpegSource"] = ffmpeg["source"]
    payload["ffmpegPath"] = ffmpeg["path"]
    payload["ffmpegMessage"] = None if ffmpeg["available"] else _ffmpeg_missing_message()
    return payload


update_paths()


from ftl_lis_format import (
    FtlLisEntry,
    build_ftl_lis,
    default_crc_hex8_for_file,
    infer_header_count,
    infer_header_style,
    parse_ftl_lis,
    read_md_ftlv_meta,
    read_reference_crc_map,
    read_reference_order,
)


def _write_bytes_if_changed(path: str, data: bytes) -> bool:
    """
    Write `data` to `path` only if content differs.
    Returns True if a write occurred, False if skipped (already identical).
    """
    try:
        if os.path.exists(path):
            existing = Path(path).read_bytes()
            if existing == data:
                return False
    except Exception:
        # If we can't read the old file, fall back to writing.
        pass

    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="ftl_", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        return True
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass




def _is_media_candidate(filename: str) -> bool:
    name = os.path.basename(filename)
    if name.upper() in {"FTL.LIS", "CONFIG.INI"}:
        return False
    if name.startswith("."):
        return False
    return True


def _safe_hex8(val: str | None) -> str | None:
    if not val:
        return None
    s = str(val).strip().upper()
    if len(s) != 8 or any(c not in "0123456789ABCDEF" for c in s):
        return None
    return s


def _is_ftlv_file(path: str | Path) -> bool:
    try:
        with Path(path).open("rb") as f:
            return f.read(4) == FTLV_MAGIC
    except Exception:
        return False


def _sanitize_playlist_order(raw_order, available_names: list[str]) -> list[str]:
    if not isinstance(raw_order, list):
        return []
    available = set(available_names)
    out: list[str] = []
    for item in raw_order:
        name = os.path.basename(str(item or ""))
        if name and name in available and name not in out:
            out.append(name)
    return out


def _apply_playlist_order(names: list[str], preferred_order: list[str]) -> list[str]:
    ordered: list[str] = []
    for name in preferred_order:
        if name in names and name not in ordered:
            ordered.append(name)
    for name in names:
        if name not in ordered:
            ordered.append(name)
    return ordered


def _list_media_files() -> list[str]:
    files = []
    for name in sorted(os.listdir(MEDIA_FOLDER)):
        if not _is_media_candidate(name):
            continue
        p = os.path.join(MEDIA_FOLDER, name)
        if os.path.isfile(p) and _is_ftlv_file(p):
            files.append(name)
    return files

FTLV_MAGIC = b"FTLV"

def _resolve_media_path(name: str) -> Path:
    update_paths()
    safe = os.path.basename(str(name or ""))
    if not safe or not _is_media_candidate(safe):
        raise ValueError("Invalid media filename")
    p = Path(MEDIA_FOLDER) / safe
    if not p.exists() or not p.is_file():
        raise FileNotFoundError("File not found")
    return p


def _cleanup_player_tokens(now: float | None = None) -> None:
    try:
        now = float(now if now is not None else time.time())
    except Exception:
        now = time.time()
    expired: list[str] = []
    for tok, meta in list(player_file_tokens.items()):
        created = float(meta.get("created", 0.0) or 0.0)
        if created <= 0 or (now - created) > PLAYER_TOKEN_TTL_S:
            expired.append(tok)
    for tok in expired:
        player_file_tokens.pop(tok, None)


def _resolve_ftlv_source(name: str | None, token: str | None) -> Path:
    if token:
        _cleanup_player_tokens()
        meta = player_file_tokens.get(str(token))
        if not meta:
            raise FileNotFoundError("Token not found or expired")
        p = Path(str(meta.get("path") or ""))
        if not p.exists() or not p.is_file():
            raise FileNotFoundError("File not found")
        return p
    if name:
        return _resolve_media_path(str(name))
    raise ValueError("Missing name/token")


def _wav_from_u8_pcm_mono(pcm: bytes, *, sample_rate: int = 44100) -> bytes:
    sample_rate = int(sample_rate)
    num_channels = 1
    bits_per_sample = 8
    audio_format = 1  # PCM
    byte_rate = sample_rate * num_channels * (bits_per_sample // 8)
    block_align = num_channels * (bits_per_sample // 8)

    data_size = len(pcm)
    riff_size = 4 + (8 + 16) + (8 + data_size)

    out = bytearray()
    out += b"RIFF"
    out += struct.pack("<I", riff_size)
    out += b"WAVE"
    out += b"fmt "
    out += struct.pack("<I", 16)
    out += struct.pack("<HHIIHH", audio_format, num_channels, sample_rate, byte_rate, block_align, bits_per_sample)
    out += b"data"
    out += struct.pack("<I", data_size)
    out += pcm
    return bytes(out)


@lru_cache(maxsize=64)
def _parse_ftlv_cached(path_str: str, mtime_ns: int) -> dict:
    """
    Parse an FTLV file and cache results keyed by path + mtime.
    Returns header fields and index entries.
    """
    p = Path(path_str)
    with p.open("rb") as f:
        head = f.read(512)
    if len(head) < 0x30 or head[0:4] != FTLV_MAGIC:
        raise ValueError("Not an FTLV file")

    def u32(off: int) -> int:
        return int(struct.unpack_from("<I", head, off)[0])

    total_size = u32(0x08)
    header_size = u32(0x0C)
    video_size = u32(0x10)
    audio_size = u32(0x14)
    index_size = u32(0x18)
    frame_duration_us = u32(0x24)
    frame_count = u32(0x28)
    duration_s = u32(0x2C)

    if header_size <= 0 or header_size > (1024 * 1024):
        raise ValueError(f"Unexpected header_size={header_size}")
    if index_size <= 8 or index_size > (1024 * 1024 * 64):
        raise ValueError(f"Unexpected index_size={index_size}")

    index_offset = int(header_size) + int(video_size) + int(audio_size)

    entries: list[tuple[int, int]] = []
    with p.open("rb") as f:
        f.seek(index_offset, os.SEEK_SET)
        idx = f.read(index_size)

    if len(idx) < 8 or idx[0] != 0xA5:
        raise ValueError("Invalid index table (missing 0xA5 prefix)")

    entry_count = (len(idx) - 8) // 8
    for i in range(entry_count):
        off, size = struct.unpack_from("<II", idx, 8 + i * 8)
        entries.append((int(off), int(size)))

    # Build a list of JPEG frames by checking the start bytes at each entry offset.
    # This avoids assuming "audio is entry 0" and also avoids limiting to only the first 32 entries.
    want = int(frame_count or 0) if int(frame_count or 0) > 0 else 0
    video_entries: list[tuple[int, int]] = []
    with p.open("rb") as f:
        for off, size in entries:
            if off <= 0 or size <= 0:
                continue
            f.seek(off, os.SEEK_SET)
            if f.read(2) == b"\xFF\xD8":
                video_entries.append((off, size))
                if want and len(video_entries) >= want:
                    break

    # Detect audio entry: any non-JPEG entry (not in video_entries).
    # Prefer a size close to header audio_size (which may include padding).
    video_set = set(video_entries)
    candidates = [(off, size) for (off, size) in entries if (off, size) not in video_set and off > 0 and size > 0]

    audio_entry: tuple[int, int] | None = None
    if candidates:
        target = int(audio_size or 0)
        if target > 0:
            # Choose candidate with smallest delta to audio_size allowing for padding.
            def score(c: tuple[int, int]) -> tuple[int, int]:
                off, size = c
                delta = abs(target - size)
                return (delta, -size)
            audio_entry = sorted(candidates, key=score)[0]
        else:
            audio_entry = max(candidates, key=lambda x: x[1])

    fps = 0.0
    if frame_duration_us > 0:
        fps = 1_000_000.0 / float(frame_duration_us)

    return {
        "total_size": int(total_size),
        "header_size": int(header_size),
        "video_size": int(video_size),
        "audio_size": int(audio_size),
        "index_size": int(index_size),
        "index_offset": int(index_offset),
        "frame_duration_us": int(frame_duration_us),
        "frame_count": int(frame_count),
        "duration_s": int(duration_s),
        "fps": fps,
        "entries": entries,
        "video_entries": video_entries,
        "audio_entry": audio_entry,
    }


def _parse_ftlv(path: Path) -> dict:
    st = path.stat()
    return _parse_ftlv_cached(str(path), int(st.st_mtime_ns))


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/files', methods=['GET'])
def list_files():
    update_paths()
    settings = load_settings()
    ref_crc_map = read_reference_crc_map(Path(REFERENCE_FTL_LIS))
    gen = _load_generator_settings()
    max_entries = int(gen.get("max_entries", 0))
    header_style = str(gen.get("header_style", "count_fc"))
    header_used_slots = int(gen.get("header_used_slots", 0))
    record_count = int(gen.get("record_count", 100))

    media_files = _list_media_files()
    playlist_order = _sanitize_playlist_order(gen.get("playlist_order", []), media_files)
    ordered_media_files = _apply_playlist_order(media_files, playlist_order)
    enabled_names = [name for name in ordered_media_files if settings.get(name, {}).get("enabled", True)]
    # Dynamic max entries: when using count_fc header, include all enabled files.
    effective_max = len(enabled_names) if header_style == "count_fc" else (max_entries if max_entries else len(enabled_names))
    if record_count > 0:
        effective_max = min(effective_max, record_count)
    if header_style == "used_len" and header_used_slots > 0:
        effective_max = min(effective_max, header_used_slots)
    included_names = set(enabled_names[:effective_max]) if effective_max else set(enabled_names)

    result = []
    for order_index, f in enumerate(ordered_media_files, start=1):
        fp = Path(MEDIA_FOLDER) / f

        st = settings.get(f, {})
        if not isinstance(st, dict):
            st = {}

        item = {}
        if "enabled" in st:
            item["enabled"] = bool(st.get("enabled"))
        if ("crc" in st) and (bool(st.get("crc_manual")) is True):
            crc_override = _safe_hex8(st.get("crc"))
            if crc_override:
                item["crc"] = crc_override

        item["fileName"] = f
        item["orderIndex"] = order_index
        item["willBeIncluded"] = f in included_names
        item["maxEntries"] = effective_max
        item["headerStyle"] = header_style
        item["headerUsedSlots"] = header_used_slots

        try:
            v1, v2, version = read_md_ftlv_meta(fp)
            item["mdVersion"] = version
            item["v1"] = v1
            item["v2"] = v2
            item["v3"] = 1
            item["marker"] = "0x0200"

            crc = _safe_hex8(item.get("crc")) or _safe_hex8(ref_crc_map.get(f)) or default_crc_hex8_for_file(fp)
            item["crc"] = crc
        except Exception as e:
            item["parseError"] = str(e)
            item.setdefault("enabled", False)

        result.append(item)

    _maybe_prune_settings(settings)
    return jsonify(result)

@app.route('/api/upload', methods=['POST'])
def upload_files():
    update_paths()
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files')
    settings = load_settings()
    
    for f in files:
        if f.filename:
            filename = secure_filename(f.filename)
            f.save(os.path.join(MEDIA_FOLDER, filename))
            # If this file was previously disabled, re-enable it (otherwise default is enabled).
            if isinstance(settings.get(filename), dict) and ("enabled" in settings[filename]) and (not bool(settings[filename].get("enabled"))):
                settings[filename]["enabled"] = True
    
    _maybe_prune_settings(settings)
    return jsonify({'message': f'Uploaded {len(files)} files'})


@app.route('/api/convert_mp4', methods=['POST'])
def convert_mp4():
    update_paths()
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Missing filename'}), 400

    name = secure_filename(f.filename)
    if not name.lower().endswith(".mp4"):
        return jsonify({'error': 'Only .mp4 is supported here'}), 400

    ffmpeg_exe, ffmpeg_dir = _find_ffmpeg()
    if not ffmpeg_exe:
        return jsonify({'error': 'ffmpeg not found. Install ffmpeg or place it at tools/ffmpeg (or set FFMPEG_PATH).'}), 400

    base_name = os.path.splitext(name)[0]
    output_name = base_name[:20] if base_name else f"MEDIA_{int(tempfile.mkstemp()[0])}"
    output_path = Path(MEDIA_FOLDER) / output_name

    # API params
    conv_quality = int(request.form.get("quality", request.args.get("quality", 50)))
    conv_fps = int(request.form.get("fps", request.args.get("fps", 20)))

    with tempfile.TemporaryDirectory(prefix="mp4_upload_") as tmp:
        mp4_path = Path(tmp) / name
        f.save(str(mp4_path))

        script = Path(BASE_DIR) / "mp4_to_ftlv.py"
        cmd = [
            sys.executable, str(script), 
            "--in", str(mp4_path), 
            "--out", str(output_path),
            "--quality", str(conv_quality),
            "--fps", str(conv_fps)
        ]
        env = os.environ.copy()
        if ffmpeg_dir:
            env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
        if proc.returncode != 0:
            return jsonify({'error': 'Conversion failed', 'details': proc.stderr.strip() or proc.stdout.strip()}), 500

    settings = load_settings()
    # Default is enabled; only need to change if it was explicitly disabled.
    if isinstance(settings.get(output_name), dict) and ("enabled" in settings[output_name]) and (not bool(settings[output_name].get("enabled"))):
        settings[output_name]["enabled"] = True
    _maybe_prune_settings(settings)

    return jsonify({'message': 'Converted', 'output': output_name, 'outputPath': str(output_path.resolve()), 'targetDirectory': str(Path(MEDIA_FOLDER).resolve())})


@app.route('/api/convert_media', methods=['POST'])
def convert_media():
    update_paths()
    files = request.files.getlist('file')
    if not files:
        files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No file provided'}), 400

    ffmpeg = _ffmpeg_status()
    ffmpeg_exe = ffmpeg["path"]
    ffmpeg_dir = ffmpeg["dir"]
    env = os.environ.copy()
    if ffmpeg_dir:
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")
    allowed, error_message = _preflight_ffmpeg_requirements(files, ffmpeg["available"])
    if not allowed:
        return jsonify({'error': error_message}), 400

    def make_unique_output_name(raw_name: str) -> str:
        base = os.path.splitext(raw_name)[0].strip()
        base = base or f"MEDIA_{os.getpid()}"
        base = base[:20]

        candidate = base
        i = 1
        while (Path(MEDIA_FOLDER) / candidate).exists():
            suffix = f"_{i}"
            keep = max(1, 20 - len(suffix))
            candidate = f"{base[:keep]}{suffix}"
            i += 1
        return candidate

    def convert_one(uploaded) -> dict:
        if not uploaded or not uploaded.filename:
            return {"ok": False, "error": "Missing filename"}

        original = secure_filename(uploaded.filename)
        output_name = make_unique_output_name(original)
        output_path = Path(MEDIA_FOLDER) / output_name

        with tempfile.TemporaryDirectory(prefix="media_upload_") as tmp:
            in_path = Path(tmp) / original
            uploaded.save(str(in_path))
            kind = _detect_media_kind(in_path, original)

            if kind == "mp4":
                if not ffmpeg_exe:
                    return {"ok": False, "input": original, "error": _ffmpeg_missing_message()}
                script = Path(BASE_DIR) / "mp4_to_ftlv.py"
                cmd = [sys.executable, str(script), "--in", str(in_path), "--out", str(output_path)]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                if proc.returncode != 0:
                    return {"ok": False, "input": original, "error": "Conversion failed", "details": (proc.stderr.strip() or proc.stdout.strip())}
                return {"ok": True, "input": original, "output": output_name, "outputPath": str(output_path.resolve()), "targetDirectory": str(Path(MEDIA_FOLDER).resolve()), "kind": "mp4"}

            # Image path. Without ffmpeg, we only accept 672x672 JPEG.
            if kind == "jpeg":
                script = Path(BASE_DIR) / "image_to_ftlv.py"
                cmd = [sys.executable, str(script), "--in", str(in_path), "--out", str(output_path)]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    return {"ok": True, "input": original, "output": output_name, "outputPath": str(output_path.resolve()), "targetDirectory": str(Path(MEDIA_FOLDER).resolve()), "kind": "jpeg"}

                if not ffmpeg_exe:
                    return {
                        "ok": False,
                        "input": original,
                        "error": "Image must be 672x672 JPEG, or install ffmpeg for auto resize",
                        "details": (proc.stderr.strip() or proc.stdout.strip()),
                    }

                frames_dir = Path(tmp) / "frames"
                frames_dir.mkdir(parents=True, exist_ok=True)
                jpg_path = frames_dir / "frame_000001.jpg"
                vf = "scale=672:672:force_original_aspect_ratio=increase,crop=672:672"
                qv = "8"
                cmd_ff = [ffmpeg_exe, "-y", "-i", str(in_path), "-vf", vf, "-q:v", qv, "-frames:v", "1", str(jpg_path)]
                proc2 = subprocess.run(cmd_ff, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc2.returncode != 0 or not jpg_path.exists():
                    return {"ok": False, "input": original, "error": "ffmpeg image resize failed", "details": (proc2.stderr.strip() or proc2.stdout.strip())}
                script2 = Path(BASE_DIR) / "frames_to_ftlv.py"
                cmd2 = [sys.executable, str(script2), "--frames-dir", str(frames_dir), "--out", str(output_path), "--fps", "20"]
                proc3 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc3.returncode != 0:
                    return {"ok": False, "input": original, "error": "Packing resized image failed", "details": (proc3.stderr.strip() or proc3.stdout.strip())}
                return {"ok": True, "input": original, "output": output_name, "outputPath": str(output_path.resolve()), "targetDirectory": str(Path(MEDIA_FOLDER).resolve()), "kind": kind}

            if not ffmpeg_exe:
                return {"ok": False, "input": original, "error": "Unsupported file without ffmpeg. Use MP4, or a 672x672 JPEG, or install ffmpeg."}

            # With ffmpeg, convert any image to one 672x672 JPEG, then pack.
            frames_dir = Path(tmp) / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            jpg_path = frames_dir / "frame_000001.jpg"
            vf = "scale=672:672:force_original_aspect_ratio=increase,crop=672:672"
            qv = "8"
            cmd_ff = [ffmpeg_exe, "-y", "-i", str(in_path), "-vf", vf, "-q:v", qv, "-frames:v", "1", str(jpg_path)]
            proc2 = subprocess.run(cmd_ff, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc2.returncode != 0 or not jpg_path.exists():
                return {"ok": False, "input": original, "error": "ffmpeg image convert failed", "details": (proc2.stderr.strip() or proc2.stdout.strip())}
            script2 = Path(BASE_DIR) / "frames_to_ftlv.py"
            cmd2 = [sys.executable, str(script2), "--frames-dir", str(frames_dir), "--out", str(output_path), "--fps", "20"]
            proc3 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc3.returncode != 0:
                return {"ok": False, "input": original, "error": "Packing converted image failed", "details": (proc3.stderr.strip() or proc3.stdout.strip())}
            return {"ok": True, "input": original, "output": output_name, "outputPath": str(output_path.resolve()), "targetDirectory": str(Path(MEDIA_FOLDER).resolve()), "kind": kind}

    results = []
    for f in files:
        results.append(convert_one(f))

    ok_outputs = [r.get("output") for r in results if r.get("ok") and r.get("output")]
    settings = load_settings()
    for out in ok_outputs:
        if out:
            if isinstance(settings.get(out), dict) and ("enabled" in settings[out]) and (not bool(settings[out].get("enabled"))):
                settings[out]["enabled"] = True
    _maybe_prune_settings(settings)

    ok_count = sum(1 for r in results if r.get("ok"))
    fail_count = len(results) - ok_count
    payload = {"message": "Converted", "okCount": ok_count, "failCount": fail_count, "results": results}
    if ok_count == 1:
        payload["output"] = ok_outputs[0]

    if ok_count == 0:
        return jsonify({"error": "No files converted", **payload}), 400
    return jsonify(payload)

@app.route('/api/convert_media_async', methods=['POST'])
def convert_media_async():
    update_paths()
    files = request.files.getlist('file')
    if not files:
        files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No file provided'}), 400

    ffmpeg = _ffmpeg_status()
    allowed, error_message = _preflight_ffmpeg_requirements(files, ffmpeg["available"])
    if not allowed:
        return jsonify({
            'error': error_message,
            'ffmpegAvailable': ffmpeg["available"],
            'ffmpegSource': ffmpeg["source"],
        }), 400

    task_id = str(uuid.uuid4())
    conversion_tasks[task_id] = {
        "id": task_id,
        "status": "pending",
        "progress": 0,
        "files": [{"original": f.filename} for f in files],
        "results": [],
        "okCount": 0,
        "failCount": 0,
        "targetDirectory": str(Path(MEDIA_FOLDER).resolve()),
        "startTime": time.time()
    }

    # Internal copies to avoid disappearing request context
    class SavedFile:
        def __init__(self, path, filename):
            self.path = path
            self.filename = filename
        def save(self, dest):
            shutil.copy(self.path, dest)

    ffmpeg_exe = ffmpeg["path"]
    ffmpeg_dir = ffmpeg["dir"]
    env = os.environ.copy()
    if ffmpeg_dir:
        env["PATH"] = ffmpeg_dir + os.pathsep + env.get("PATH", "")

    def make_unique_output_name_local(raw_name: str) -> str:
        base = os.path.splitext(raw_name)[0].strip()
        base = base or f"MEDIA_{os.getpid()}"
        base = base[:20]
        candidate = base
        i = 1
        while (Path(MEDIA_FOLDER) / candidate).exists():
            suffix = f"_{i}"
            keep = max(1, 20 - len(suffix))
            candidate = f"{base[:keep]}{suffix}"
            i += 1
        return candidate

    def convert_one_local(uploaded, quality=50, fps=20, progress_cb=None) -> dict:
        if not uploaded or not uploaded.filename:
            return {"ok": False, "error": "Missing filename"}
        original = secure_filename(uploaded.filename)
        try:
            if progress_cb:
                progress_cb(0.2)
        except Exception:
            pass
        output_name = make_unique_output_name_local(original)
        output_path = Path(MEDIA_FOLDER) / output_name
        with tempfile.TemporaryDirectory(prefix="media_upload_") as tmp:
            in_path = Path(tmp) / original
            uploaded.save(str(in_path))
            kind = _detect_media_kind(in_path, original)
            try:
                if progress_cb:
                    progress_cb(0.4)
            except Exception:
                pass
            if kind == "mp4":
                if not ffmpeg_exe:
                    return {"ok": False, "input": original, "error": _ffmpeg_missing_message()}
                script = Path(BASE_DIR) / "mp4_to_ftlv.py"
                try:
                    if progress_cb:
                        progress_cb(0.6)
                except Exception:
                    pass
                cmd = [
                    sys.executable, str(script), 
                    "--in", str(in_path), 
                    "--out", str(output_path),
                    "--quality", str(quality),
                    "--fps", str(fps)
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                if proc.returncode != 0:
                    return {"ok": False, "input": original, "error": "Conversion failed", "details": proc.stderr.strip()}
                try:
                    if progress_cb:
                        progress_cb(0.8)
                except Exception:
                    pass
                return {"ok": True, "input": original, "output": output_name, "outputPath": str(output_path.resolve()), "targetDirectory": str(Path(MEDIA_FOLDER).resolve()), "kind": "mp4"}

            # Image handling...
            if kind == "jpeg":
                script = Path(BASE_DIR) / "image_to_ftlv.py"
                cmd = [sys.executable, str(script), "--in", str(in_path), "--out", str(output_path)]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    try:
                        if progress_cb:
                            progress_cb(1.0)
                    except Exception:
                        pass
                    return {"ok": True, "input": original, "output": output_name, "outputPath": str(output_path.resolve()), "targetDirectory": str(Path(MEDIA_FOLDER).resolve()), "kind": "jpeg"}
            if not ffmpeg_exe:
                return {"ok": False, "input": original, "error": "Unsupported file without ffmpeg."}
            try:
                if progress_cb:
                    progress_cb(0.6)
            except Exception:
                pass
            # With ffmpeg, convert any image to one 672x672 JPEG, then pack.
            frames_dir = Path(tmp) / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            jpg_path = frames_dir / "frame_000001.jpg"
            vf = "scale=672:672:force_original_aspect_ratio=increase,crop=672:672"
            
            # Use the same quality mapping as mp4_to_ftlv if possible, or just -q:v
            # ffmpeg -q:v 2 is best, 31 is worst.
            q_val = max(2, min(31, int(round(31 - (quality / 100.0) * 29))))
            
            cmd_ff = [ffmpeg_exe, "-y", "-i", str(in_path), "-vf", vf, "-pix_fmt", "yuvj420p", "-q:v", str(q_val), "-frames:v", "1", str(jpg_path)]
            proc2 = subprocess.run(cmd_ff, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc2.returncode != 0 or not jpg_path.exists():
                return {"ok": False, "input": original, "error": "ffmpeg resize failed"}
            script2 = Path(BASE_DIR) / "frames_to_ftlv.py"
            cmd2 = [sys.executable, str(script2), "--frames-dir", str(frames_dir), "--out", str(output_path), "--fps", str(fps)]
            proc3 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc3.returncode != 0:
                return {"ok": False, "input": original, "error": "Packing failed"}
            try:
                if progress_cb:
                    progress_cb(1.0)
            except Exception:
                pass
            return {"ok": True, "input": original, "output": output_name, "outputPath": str(output_path.resolve()), "targetDirectory": str(Path(MEDIA_FOLDER).resolve()), "kind": kind}

    # API params
    conv_quality = int(request.form.get("quality", request.args.get("quality", 50)))
    conv_fps = int(request.form.get("fps", request.args.get("fps", 20)))

    # Save files to a persistent temp location before request ends

    sync_tmp_dir = tempfile.mkdtemp(prefix="async_conv_")
    saved_files_metadata = []
    for f in files:
        if not f.filename: continue
        safe_name = secure_filename(f.filename)
        temp_p = Path(sync_tmp_dir) / safe_name
        f.save(str(temp_p))
        saved_files_metadata.append({"path": temp_p, "original_name": f.filename})

    # Helper to run in background
    def run_conversion(dir_to_clean, files_to_process):
        task = conversion_tasks[task_id]
        task["status"] = "processing"
        
        try:
            total = len(files_to_process)
            for i, meta in enumerate(files_to_process):
                sf = SavedFile(meta["path"], meta["original_name"])
                last_progress = {"val": 0}

                def _progress_cb(frac: float) -> None:
                    try:
                        frac = float(frac)
                    except Exception:
                        return
                    if frac < 0:
                        frac = 0.0
                    if frac > 1:
                        frac = 1.0
                    # Keep monotonic progress per file.
                    if frac < last_progress["val"]:
                        frac = last_progress["val"]
                    last_progress["val"] = frac

                    # Overall progress across all files.
                    overall = int(((i + frac) / max(1, total)) * 100)
                    if overall < task.get("progress", 0):
                        overall = int(task.get("progress", 0))
                    task["progress"] = overall

                # For a single file, show stepwise 20/40/60/80/100 style updates.
                _progress_cb(0.0)
                res = convert_one_local(sf, quality=conv_quality, fps=conv_fps, progress_cb=_progress_cb)
                task["results"].append(res)

                if res.get("ok"):
                    task["okCount"] += 1
                else:
                    task["failCount"] += 1
                _progress_cb(1.0)

            settings = load_settings()
            for r in task["results"]:
                if r.get("ok") and r.get("output"):
                    out = r["output"]
                    st = settings.get(out)
                    if not isinstance(st, dict):
                        st = {}
                        settings[out] = st
                    # Default is enabled; remove any previous "disabled" override.
                    st.pop("enabled", None)
            _maybe_prune_settings(settings)
        finally:
            # Always clean up the sync temp directory
            shutil.rmtree(dir_to_clean, ignore_errors=True)
            
        task["status"] = "done"
        task["endTime"] = time.time()

    thread = threading.Thread(target=run_conversion, args=(sync_tmp_dir, saved_files_metadata))
    thread.daemon = True
    thread.start()
    return jsonify({"taskId": task_id})


@app.route('/api/tasks/<task_id>', methods=['GET'])
def get_task(task_id):
    return jsonify(get_task_status(task_id))

@app.route('/api/tasks', methods=['GET'])
def list_tasks():
    sorted_tasks = sorted(conversion_tasks.values(), key=lambda x: x.get("startTime", 0), reverse=True)
    return jsonify(sorted_tasks[:10])


@app.route('/api/update', methods=['POST'])
def update_settings():
    data = request.json
    filename = data.get('fileName')
    if not filename: return jsonify({'error': 'Missing filename'}), 400
    
    settings = load_settings()
    st = settings.get(filename)
    if not isinstance(st, dict):
        st = {}

    if "enabled" in data:
        en = bool(data.get("enabled"))
        if en:
            # default is enabled; only keep an override if disabled or crc override exists
            st.pop("enabled", None)
        else:
            st["enabled"] = False

    if "crc" in data:
        raw_crc = data.get("crc")
        if raw_crc is None or str(raw_crc).strip() == "":
            # Allow clearing the CRC override.
            st.pop("crc", None)
            st.pop("crc_manual", None)
        else:
            crc = _safe_hex8(raw_crc)
            if not crc:
                return jsonify({'error': 'crc must be 8 hex characters (0-9, A-F)'}), 400
            st["crc"] = crc
            st["crc_manual"] = True

    if st:
        settings[filename] = st
    else:
        settings.pop(filename, None)

    _maybe_prune_settings(settings)
    return jsonify({'message': 'Updated'})

@app.route('/api/delete', methods=['POST'])
def delete_file():
    update_paths()
    data = request.json
    filename = data.get('fileName')
    if not filename: return jsonify({'error': 'Missing filename'}), 400
    
    filepath = os.path.join(MEDIA_FOLDER, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    
    settings = load_settings()
    if filename in settings:
        del settings[filename]
        _maybe_prune_settings(settings)
        
    return jsonify({'message': 'Deleted'})


def _delete_one_media(filename: str, settings: dict) -> bool:
    name = os.path.basename(filename or "")
    if not name or not _is_media_candidate(name):
        return False

    filepath = os.path.join(MEDIA_FOLDER, name)
    deleted = False
    try:
        if os.path.exists(filepath):
            os.remove(filepath)
            deleted = True
    except Exception:
        deleted = False

    if name in settings:
        del settings[name]
    return deleted


@app.route('/api/delete_many', methods=['POST'])
def delete_many():
    update_paths()
    data = request.json or {}
    names = data.get("fileNames")
    if not isinstance(names, list) or not names:
        return jsonify({'error': 'fileNames must be a non-empty list'}), 400

    settings = load_settings()
    deleted = 0
    for n in names:
        if _delete_one_media(str(n), settings):
            deleted += 1

    _maybe_prune_settings(settings)
    return jsonify({'message': 'Deleted', 'deleted': deleted, 'requested': len(names)})


@app.route('/api/delete_all', methods=['POST'])
def delete_all():
    update_paths()
    settings = load_settings()
    names = _list_media_files()
    deleted = 0
    for n in names:
        if _delete_one_media(n, settings):
            deleted += 1

    _maybe_prune_settings(settings)
    return jsonify({'message': 'Deleted all', 'deleted': deleted})


@app.route('/api/reference_lis', methods=['GET'])
def get_reference_lis():
    gen = _load_generator_settings()
    ref = Path(gen.get("reference_lis", REFERENCE_FTL_LIS))
    if not ref.exists():
        return jsonify({"exists": False, "path": str(ref)})

    try:
        header_value, record_count, entries = parse_ftl_lis(ref)
        return jsonify(
            {
                "exists": True,
                "path": str(ref),
                "header_value": header_value,
                "record_count": record_count,
                "entry_count": len(entries),
                "header_style": infer_header_style(ref) or "unknown",
            }
        )
    except Exception as e:
        return jsonify({"exists": True, "path": str(ref), "error": str(e)}), 400


@app.route('/api/reference_lis', methods=['POST'])
def upload_reference_lis():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Missing filename'}), 400

    # Always store as reference/FTL.LIS
    f.save(REFERENCE_UPLOAD_PATH)

    settings = load_settings()
    settings.setdefault("__generator", {})
    settings["__generator"]["reference_lis"] = REFERENCE_UPLOAD_PATH
    _maybe_prune_settings(settings)
    return jsonify({"message": "Reference FTL.LIS loaded", "path": REFERENCE_UPLOAD_PATH})


@app.route('/api/reference_lis/browse', methods=['GET'])
def browse_reference_lis():
    try:
        update_paths()
        settings = load_settings()
        initial_dir = settings.get("__generator", {}).get("target_directory") or str(Path.home() / "Downloads")
        initial_dir = initial_dir if os.path.isdir(initial_dir) else str(Path.home() / "Downloads")

        path = ""
        if sys.platform == "darwin":
            script = f'set theFile to choose file with prompt "Select existing FTL.LIS" default location (POSIX file "{initial_dir}")\nPOSIX path of theFile'
            try:
                result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
                if result.returncode == 0:
                    path = result.stdout.strip()
            except Exception:
                pass
        else:
            if not HAS_TKINTER:
                return jsonify({"error": "Tkinter not available on this server"}), 501
            
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)

            path = filedialog.askopenfilename(
                initialdir=initial_dir,
                title="Select existing FTL.LIS",
                filetypes=[("FTL.LIS", "*.LIS;*.lis"), ("All files", "*.*")],
            )
            root.destroy()

        if path:
            return jsonify({"path": os.path.normpath(path)})
        return jsonify({"path": None})

        if not path:
            return jsonify({"path": None})

        p = Path(path)
        if not p.exists() or not p.is_file():
            return jsonify({"error": "File not found"}), 404

        # Validate it's parsable as FTL.LIS
        parse_ftl_lis(p)

        settings.setdefault("__generator", {})
        settings["__generator"]["reference_lis"] = str(p)
        # Auto-set target directory to the folder containing this FTL.LIS.
        settings["__generator"]["target_directory"] = str(p.parent)
        _maybe_prune_settings(settings)
        update_paths()
        return jsonify({"message": "Reference FTL.LIS selected", "path": str(p), "target_directory": str(p.parent)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/reference_lis/clear', methods=['POST'])
def clear_reference_lis():
    settings = load_settings()
    settings.setdefault("__generator", {})
    if "reference_lis" in settings["__generator"]:
        del settings["__generator"]["reference_lis"]
    _maybe_prune_settings(settings)
    return jsonify(_load_generator_settings())

@app.route('/api/generate', methods=['POST'])
def generate_ftl():
    settings = load_settings()

    gen = _load_generator_settings()
    reference_path = Path(gen.get("reference_lis") or REFERENCE_FTL_LIS)
    if not reference_path.exists():
        reference_path = Path(OUTPUT_FILE)
    ref_crc_map = read_reference_crc_map(reference_path)
    ref_order = read_reference_order(reference_path)

    max_entries = int(gen.get("max_entries", 0))
    header_style = str(gen.get("header_style", "count_fc"))
    header_used_slots = int(gen.get("header_used_slots", 0))
    record_count = int(gen.get("record_count", 100))
    merge_mode = bool(gen.get("merge_mode", False))

    enabled = []
    for fname in _list_media_files():
        st = settings.get(fname, {})
        if st.get("enabled", True):
            enabled.append(fname)

    custom_order = _sanitize_playlist_order(gen.get("playlist_order", []), enabled)
    ordered: list[str] = []
    if custom_order:
        ordered = _apply_playlist_order(enabled, custom_order)
    elif ref_order:
        for name in ref_order:
            if name in enabled and name not in ordered:
                ordered.append(name)
        for name in enabled:
            if name not in ordered:
                ordered.append(name)
    else:
        ordered = enabled

    # Dynamic max entries: when using count_fc header, include all enabled files.
    if header_style != "count_fc":
        if max_entries and len(ordered) > max_entries:
            ordered = ordered[:max_entries]

    if record_count <= 0:
        record_count = 100

    entries: list[FtlLisEntry] = []
    base_names: set[str] = set()
    truncated = False

    if merge_mode and reference_path.exists():
        try:
            _hv, _rc, base_entries = parse_ftl_lis(reference_path)
            for e in base_entries:
                entries.append(e)
                base_names.add(e.name)
        except Exception:
            # If reference can't be parsed, fall back to building from enabled files only.
            entries = []
            base_names = set()

    for fname in ordered:
        if merge_mode and fname in base_names:
            # If the file exists locally, we'll update its metadata below; otherwise keep reference entry.
            fp = Path(MEDIA_FOLDER) / fname
            if not fp.exists():
                continue

        st = settings.get(fname, {})
        fp = Path(MEDIA_FOLDER) / fname
        try:
            v1_u32, v2, _version = read_md_ftlv_meta(fp)
        except Exception:
            continue

        crc_override = None
        if isinstance(st, dict) and (bool(st.get("crc_manual")) is True):
            crc_override = _safe_hex8(st.get("crc"))
        crc = crc_override or _safe_hex8(ref_crc_map.get(fname)) or default_crc_hex8_for_file(fp)
        updated = FtlLisEntry(
            name=fname,
            marker=0x0200,
            v1=int(v1_u32) & 0xFFFF,
            v2=int(v2) & 0xFFFF,
            v3=1,
            crc_hex8=crc,
        )
        if merge_mode and fname in base_names:
            entries = [updated if e.name == fname else e for e in entries]
        else:
            entries.append(updated)

        if record_count and len(entries) >= record_count:
            # Stop appending once we reach capacity.
            if len(ordered) > len(entries):
                truncated = True
            break

    if record_count and len(entries) > record_count:
        entries = entries[:record_count]
        truncated = True

    header_slots: int | None = None
    if header_style == "used_len" and header_used_slots > 0:
        if len(entries) > header_used_slots:
            entries = entries[:header_used_slots]
        header_slots = header_used_slots

    data = build_ftl_lis(
        entries,
        record_count=record_count,
        min_used_slots=7,
        header_used_slots=header_slots,
        header_style=header_style,
    )
    wrote = _write_bytes_if_changed(OUTPUT_FILE, data)

    return jsonify(
        {
            'message': 'FTL.LIS updated' if wrote else 'FTL.LIS unchanged',
            'path': OUTPUT_FILE,
            'entries': len(entries),
            'size': len(data),
            'truncated': truncated,
            'capacity': record_count,
        }
    )


@app.route('/api/generator', methods=['GET'])
def get_generator_settings():
    return jsonify(_load_generator_settings())


@app.route('/api/generator', methods=['POST'])
def set_generator_settings():
    data = request.json or {}
    settings = load_settings()
    gen = settings.get("__generator", {})

    if "max_entries" in data:
        gen["max_entries"] = int(data["max_entries"])
    if "header_style" in data:
        gen["header_style"] = str(data["header_style"]).lower().strip()
    if "header_used_slots" in data:
        gen["header_used_slots"] = int(data["header_used_slots"])
    if "record_count" in data:
        gen["record_count"] = int(data["record_count"])
    if "merge_mode" in data:
        gen["merge_mode"] = bool(data["merge_mode"])
    if "reference_lis" in data and data["reference_lis"]:
        gen["reference_lis"] = str(data["reference_lis"])
    if "target_directory" in data:
        gen["target_directory"] = str(data["target_directory"])
    if "playlist_order" in data:
        gen["playlist_order"] = _sanitize_playlist_order(data.get("playlist_order", []), _list_media_files())

    settings["__generator"] = gen
    _maybe_prune_settings(settings)
    update_paths()
    return jsonify(_load_generator_settings())


@app.route('/api/browse', methods=['GET'])
def browse_directory():
    try:
        settings = load_settings()
        initial = settings.get("__generator", {}).get("target_directory") or str(Path.home() / "Downloads")
        initial = initial if os.path.isdir(initial) else str(Path.home() / "Downloads")
        directory = ""

        if sys.platform == "darwin":
            script = f'set theFolder to choose folder with prompt "Select Target Storage Directory" default location (POSIX file "{initial}")\nPOSIX path of theFolder'
            try:
                result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
                if result.returncode == 0:
                    directory = result.stdout.strip()
            except Exception:
                pass
        else:
            if not HAS_TKINTER:
                return jsonify({"error": "Tkinter not available on this server"}), 501
            
            root = tk.Tk()
            root.withdraw()  # Hide main window
            root.attributes("-topmost", True) # Bring to front
            
            directory = filedialog.askdirectory(initialdir=initial, title="Select Target Storage Directory")
            root.destroy()
            
        if directory:
            return jsonify({"path": os.path.normpath(directory)})
        return jsonify({"path": None})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/config', methods=['GET'])

def get_config():
    if not os.path.exists(CONFIG_INI_FILE):
        return jsonify({'error': 'config.ini not found'}), 404
    
    with open(CONFIG_INI_FILE, 'rb') as f:
        data = f.read(512)
    
    # Heuristic extraction based on common patterns
    def extract_string(offset, length):
        chunk = data[offset:offset+length]
        return chunk.split(b'\x00')[0].decode('ascii', errors='ignore')

    config = {
        'model': extract_string(0x30, 16),
        'ssid': extract_string(0x40, 32),
        'password': extract_string(0x60, 32),
        'deviceName': extract_string(0xA0, 32)
    }
    return jsonify(config)

@app.route('/api/config', methods=['POST'])
def save_config():
    if not os.path.exists(CONFIG_INI_FILE):
        return jsonify({'error': 'config.ini not found'}), 404
    
    data = request.json
    with open(CONFIG_INI_FILE, 'rb') as f:
        content = bytearray(f.read(512))
        if len(content) < 512:
            content.extend(b'\x00' * (512 - len(content)))

    def write_string(offset, length, val):
        b_val = val.encode('ascii', errors='ignore')[:length]
        for i in range(length):
            content[offset + i] = b_val[i] if i < len(b_val) else 0

    if 'ssid' in data: write_string(0x40, 32, data['ssid'])
    if 'password' in data: write_string(0x60, 32, data['password'])
    if 'deviceName' in data: 
        write_string(0xA0, 32, data['deviceName'])
        write_string(0xE8, 32, data['deviceName']) # Often mirrored

    with open(CONFIG_INI_FILE, 'wb') as f:
        f.write(content)
        
    return jsonify({'message': 'config.ini updated'})

@app.route('/api/download_ftl')
def download_ftl():
    if os.path.exists(OUTPUT_FILE):
        return send_file(OUTPUT_FILE, as_attachment=True)
    return "Not generated yet", 404


@app.route('/api/ftlv_info', methods=['GET'])
def ftlv_info():
    try:
        name = request.args.get("name") or None
        token = request.args.get("token") or None
        p = _resolve_ftlv_source(name, token)
        info = _parse_ftlv(p)
        # Do not return the raw index entries (large); UI only needs metadata.
        frame_count = int(info.get("frame_count", 0) or 0)
        return jsonify(
            {
                "fileName": p.name,
                "frameCount": frame_count,
                "frameDurationUs": info.get("frame_duration_us", 0),
                "fps": info.get("fps", 0.0),
                "durationSeconds": info.get("duration_s", 0),
                "totalSize": info.get("total_size", 0),
                "videoSize": info.get("video_size", 0),
                "audioSize": info.get("audio_size", 0),
                "indexSize": info.get("index_size", 0),
            }
        )
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/api/ftlv_frame', methods=['GET'])
def ftlv_frame():
    try:
        name = request.args.get("name") or None
        token = request.args.get("token") or None
        frame = int(request.args.get("frame", "0"))
        p = _resolve_ftlv_source(name, token)
        info = _parse_ftlv(p)
        frame_count = int(info.get("frame_count", 0) or 0)
        if frame_count <= 0:
            return jsonify({"error": "No frames"}), 400
        if frame < 0 or frame >= frame_count:
            return jsonify({"error": "Frame out of range", "frameCount": frame_count}), 400

        video_entries: list[tuple[int, int]] = info.get("video_entries", []) or []
        if not video_entries or frame >= len(video_entries):
            return jsonify({"error": "Index table missing frame entry"}), 400

        off, size = video_entries[frame]
        if off <= 0 or size <= 0:
            return jsonify({"error": "Invalid frame entry"}), 400

        with p.open("rb") as f:
            f.seek(off, os.SEEK_SET)
            data = f.read(size)

        if len(data) != size:
            return jsonify({"error": "Short read"}), 400

        # Generated FTLV uses JPG frames.
        return send_file(
            io.BytesIO(data),
            mimetype="image/jpeg",
            as_attachment=False,
            download_name=f"{p.name}_frame_{frame:06d}.jpg",
            max_age=3600,
        )
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404
    except ValueError:
        return jsonify({"error": "Invalid parameters"}), 400
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route('/api/ftlv_audio_wav', methods=['GET'])
def ftlv_audio_wav():
    try:
        name = request.args.get("name") or None
        token = request.args.get("token") or None
        p = _resolve_ftlv_source(name, token)
        info = _parse_ftlv(p)

        audio_entry = info.get("audio_entry")
        if not audio_entry:
            return jsonify({"error": "Audio chunk not detected"}), 400

        off, size = audio_entry
        if off <= 0 or size <= 0:
            return jsonify({"error": "Invalid audio entry"}), 400

        with p.open("rb") as f:
            f.seek(off, os.SEEK_SET)
            pcm = f.read(size)

        if len(pcm) != size:
            return jsonify({"error": "Short read"}), 400

        wav = _wav_from_u8_pcm_mono(pcm, sample_rate=44100)
        return send_file(
            io.BytesIO(wav),
            mimetype="audio/wav",
            as_attachment=False,
            download_name=f"{p.name}.wav",
            max_age=3600,
        )
    except FileNotFoundError:
        return jsonify({"error": "File not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/api/player_token', methods=['POST'])
def player_token_for_path():
    try:
        data = request.json or {}
        path = data.get("path")
        if not path:
            return jsonify({"error": "Missing path"}), 400

        p = Path(str(path))
        try:
            p = p.expanduser().resolve()
        except Exception:
            p = Path(str(path))

        if not p.exists() or not p.is_file():
            return jsonify({"error": "File not found"}), 404

        _parse_ftlv(p)

        tok = str(uuid.uuid4())
        _cleanup_player_tokens()
        player_file_tokens[tok] = {"path": str(p), "created": time.time()}
        return jsonify({"token": tok, "fileName": p.name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/player_browse', methods=['GET'])
def player_browse_file():
    try:
        update_paths()
        initial = MEDIA_FOLDER if os.path.isdir(MEDIA_FOLDER) else BASE_DIR
        path = ""

        if sys.platform == "darwin":
            # Use AppleScript on macOS to avoid Tkinter threading crash (NSWindow must be on main thread)
            script = f'set theFile to choose file with prompt "Select FTLV file" default location (POSIX file "{initial}")\nPOSIX path of theFile'
            try:
                result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
                if result.returncode == 0:
                    path = result.stdout.strip()
            except Exception:
                pass
        else:
            if not HAS_TKINTER:
                return jsonify({"error": "Tkinter not available on this server"}), 501
            
            # Since we only use Tkinter occasionally, it's safer to attempt it
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)

            path = filedialog.askopenfilename(
                initialdir=initial,
                title="Select FTLV file (fan generated)",
                filetypes=[("All files", "*.*")],
            )
            root.destroy()

        if not path:
            return jsonify({"token": None})

        p = Path(path)
        if not p.exists() or not p.is_file():
            return jsonify({"error": "File not found"}), 404

        # Validate it's actually FTLV before issuing a token.
        _parse_ftlv(p)

        tok = str(uuid.uuid4())
        _cleanup_player_tokens()
        player_file_tokens[tok] = {"path": str(p), "created": time.time()}
        return jsonify({"token": tok, "fileName": p.name})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    print("Fan Playlist Manager Running...")
    try:
        atexit.register(_settings_housekeeping)
    except Exception:
        pass
    _settings_housekeeping()

    # Dynamic Port and Browser Launch
    import socket
    import threading
    import webbrowser
    import time

    def find_free_port(start_port=5050, max_port=5100):
        for p in range(start_port, max_port + 1):
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                try:
                    s.bind(("127.0.0.1", p))
                    return p
                except OSError:
                    continue
        raise SystemExit("No free port found in range 5050-5100")

    if "SERVER_PORT" in os.environ:
        port = int(os.environ["SERVER_PORT"])
    else:
        port = find_free_port(5050, 5100)
        os.environ["SERVER_PORT"] = str(port)

    def open_browser():
        # wait a short beat to ensure Flask has started accepting connections
        time.sleep(1.5)
        webbrowser.open(f"http://127.0.0.1:{port}/")

    # Only open browser once, preventing duplicates when the Werkzeug reloader forks the process
    if "BROWSER_OPENED" not in os.environ:
        os.environ["BROWSER_OPENED"] = "1"
        threading.Thread(target=open_browser, daemon=True).start()

    app.run(debug=True, port=port)
