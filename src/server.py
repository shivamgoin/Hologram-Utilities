import os
import json
import sys
from pathlib import Path
import tempfile
import subprocess
import shutil

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

def load_settings():

    # SETTINGS_FILE is defined below, but we can access it if we call load_settings after its definition
    # Or just use BASE_DIR here directly if we want to be safe
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {}

def save_settings(settings):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'settings.json')
    with open(path, 'w') as f:
        json.dump(settings, f, indent=4)


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

    gen = settings.get("__generator", {})

    # Reference playlist: default to repo root FTL.LIS when available; fall back to current output.
    default_ref = REFERENCE_FTL_LIS if os.path.exists(REFERENCE_FTL_LIS) else OUTPUT_FILE
    reference_lis = str(gen.get("reference_lis", default_ref))
    # Forced to False to prevent injecting old/default media into FTL.LIS
    merge_mode = False
    gen["merge_mode"] = False

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
    target_directory = gen.get("target_directory", BASE_DIR)

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
            "target_directory": target_directory
        }
    )
    save_settings(settings)
    update_paths()
    return settings["__generator"]


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


def _list_media_files() -> list[str]:
    files = []
    for name in sorted(os.listdir(MEDIA_FOLDER)):
        if not _is_media_candidate(name):
            continue
        p = os.path.join(MEDIA_FOLDER, name)
        if os.path.isfile(p):
            files.append(name)
    return files

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/files', methods=['GET'])
def list_files():
    settings = load_settings()
    ref_crc_map = read_reference_crc_map(Path(REFERENCE_FTL_LIS))
    gen = _load_generator_settings()
    max_entries = int(gen.get("max_entries", 0))
    header_style = str(gen.get("header_style", "count_fc"))
    header_used_slots = int(gen.get("header_used_slots", 0))
    record_count = int(gen.get("record_count", 100))

    enabled_names = [name for name in _list_media_files() if settings.get(name, {}).get("enabled", True)]
    # Dynamic max entries: when using count_fc header, include all enabled files.
    effective_max = len(enabled_names) if header_style == "count_fc" else (max_entries if max_entries else len(enabled_names))
    if record_count > 0:
        effective_max = min(effective_max, record_count)
    if header_style == "used_len" and header_used_slots > 0:
        effective_max = min(effective_max, header_used_slots)
    included_names = set(enabled_names[:effective_max]) if effective_max else set(enabled_names)

    result = []
    for f in _list_media_files():
        fp = Path(MEDIA_FOLDER) / f

        if f not in settings:
            settings[f] = {}

        if "enabled" not in settings[f]:
            settings[f]["enabled"] = True

        item = settings[f].copy()
        item["fileName"] = f
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

            crc = _safe_hex8(item.get("crc"))
            if not crc:
                crc = _safe_hex8(ref_crc_map.get(f)) or default_crc_hex8_for_file(fp)
                settings[f]["crc"] = crc
            item["crc"] = crc
        except Exception as e:
            item["parseError"] = str(e)
            item.setdefault("enabled", False)

        result.append(item)

    save_settings(settings)
    return jsonify(result)

@app.route('/api/upload', methods=['POST'])
def upload_files():
    if 'files' not in request.files:
        return jsonify({'error': 'No files provided'}), 400
    
    files = request.files.getlist('files')
    settings = load_settings()
    
    for f in files:
        if f.filename:
            filename = secure_filename(f.filename)
            f.save(os.path.join(MEDIA_FOLDER, filename))
            
            if filename not in settings:
                settings[filename] = {"enabled": True}
    
    save_settings(settings)
    return jsonify({'message': f'Uploaded {len(files)} files'})


@app.route('/api/convert_mp4', methods=['POST'])
def convert_mp4():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'Missing filename'}), 400

    name = secure_filename(f.filename)
    if not name.lower().endswith(".mp4"):
        return jsonify({'error': 'Only .mp4 is supported here'}), 400

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return jsonify({'error': 'ffmpeg not found in PATH on this PC. Install ffmpeg, or use frames_to_ftlv.py.'}), 400

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
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            return jsonify({'error': 'Conversion failed', 'details': proc.stderr.strip() or proc.stdout.strip()}), 500

    settings = load_settings()
    if output_name not in settings:
        settings[output_name] = {"enabled": True}
    else:
        settings[output_name]["enabled"] = True
    save_settings(settings)

    return jsonify({'message': 'Converted', 'output': output_name})


@app.route('/api/convert_media', methods=['POST'])
def convert_media():
    files = request.files.getlist('file')
    if not files:
        files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No file provided'}), 400

    ffmpeg = shutil.which("ffmpeg")

    def detect_kind(path: Path, original_name: str) -> str:
        head = path.read_bytes()[:32]
        if head.startswith(b"\xFF\xD8"):
            return "jpeg"
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return "png"
        if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
            return "gif"
        # MP4: look for an ftyp box near the start (common at offset 4).
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
            kind = detect_kind(in_path, original)

            if kind == "mp4":
                if not ffmpeg:
                    return {"ok": False, "input": original, "error": "ffmpeg not found in PATH on this PC. Install ffmpeg to convert MP4."}
                script = Path(BASE_DIR) / "mp4_to_ftlv.py"
                cmd = [sys.executable, str(script), "--in", str(in_path), "--out", str(output_path)]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode != 0:
                    return {"ok": False, "input": original, "error": "Conversion failed", "details": (proc.stderr.strip() or proc.stdout.strip())}
                return {"ok": True, "input": original, "output": output_name, "kind": "mp4"}

            # Image path. Without ffmpeg, we only accept 672x672 JPEG.
            if kind == "jpeg":
                script = Path(BASE_DIR) / "image_to_ftlv.py"
                cmd = [sys.executable, str(script), "--in", str(in_path), "--out", str(output_path)]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    return {"ok": True, "input": original, "output": output_name, "kind": "jpeg"}

                if not ffmpeg:
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
                cmd_ff = [ffmpeg, "-y", "-i", str(in_path), "-vf", vf, "-q:v", qv, "-frames:v", "1", str(jpg_path)]
                proc2 = subprocess.run(cmd_ff, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc2.returncode != 0 or not jpg_path.exists():
                    return {"ok": False, "input": original, "error": "ffmpeg image resize failed", "details": (proc2.stderr.strip() or proc2.stdout.strip())}
                script2 = Path(BASE_DIR) / "frames_to_ftlv.py"
                cmd2 = [sys.executable, str(script2), "--frames-dir", str(frames_dir), "--out", str(output_path), "--fps", "20"]
                proc3 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc3.returncode != 0:
                    return {"ok": False, "input": original, "error": "Packing resized image failed", "details": (proc3.stderr.strip() or proc3.stdout.strip())}
                return {"ok": True, "input": original, "output": output_name, "kind": kind}

            if not ffmpeg:
                return {"ok": False, "input": original, "error": "Unsupported file without ffmpeg. Use MP4, or a 672x672 JPEG, or install ffmpeg."}

            # With ffmpeg, convert any image to one 672x672 JPEG, then pack.
            frames_dir = Path(tmp) / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            jpg_path = frames_dir / "frame_000001.jpg"
            vf = "scale=672:672:force_original_aspect_ratio=increase,crop=672:672"
            qv = "8"
            cmd_ff = [ffmpeg, "-y", "-i", str(in_path), "-vf", vf, "-q:v", qv, "-frames:v", "1", str(jpg_path)]
            proc2 = subprocess.run(cmd_ff, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc2.returncode != 0 or not jpg_path.exists():
                return {"ok": False, "input": original, "error": "ffmpeg image convert failed", "details": (proc2.stderr.strip() or proc2.stdout.strip())}
            script2 = Path(BASE_DIR) / "frames_to_ftlv.py"
            cmd2 = [sys.executable, str(script2), "--frames-dir", str(frames_dir), "--out", str(output_path), "--fps", "20"]
            proc3 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc3.returncode != 0:
                return {"ok": False, "input": original, "error": "Packing converted image failed", "details": (proc3.stderr.strip() or proc3.stdout.strip())}
            return {"ok": True, "input": original, "output": output_name, "kind": kind}

    results = []
    for f in files:
        results.append(convert_one(f))

    ok_outputs = [r.get("output") for r in results if r.get("ok") and r.get("output")]
    settings = load_settings()
    for out in ok_outputs:
        if out:
            settings.setdefault(out, {})
            settings[out]["enabled"] = True
    save_settings(settings)

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
    files = request.files.getlist('file')
    if not files:
        files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No file provided'}), 400

    task_id = str(uuid.uuid4())
    conversion_tasks[task_id] = {
        "id": task_id,
        "status": "pending",
        "progress": 0,
        "files": [{"original": f.filename} for f in files],
        "results": [],
        "okCount": 0,
        "failCount": 0,
        "startTime": time.time()
    }

    # Internal copies to avoid disappearing request context
    class SavedFile:
        def __init__(self, path, filename):
            self.path = path
            self.filename = filename
        def save(self, dest):
            shutil.copy(self.path, dest)

    ffmpeg = shutil.which("ffmpeg")

    def detect_kind(path: Path, original_name: str) -> str:
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

    def convert_one_local(uploaded, quality=50, fps=20) -> dict:
        if not uploaded or not uploaded.filename:
            return {"ok": False, "error": "Missing filename"}
        original = secure_filename(uploaded.filename)
        output_name = make_unique_output_name_local(original)
        output_path = Path(MEDIA_FOLDER) / output_name
        with tempfile.TemporaryDirectory(prefix="media_upload_") as tmp:
            in_path = Path(tmp) / original
            uploaded.save(str(in_path))
            kind = detect_kind(in_path, original)
            if kind == "mp4":
                if not ffmpeg:
                    return {"ok": False, "input": original, "error": "ffmpeg not found"}
                script = Path(BASE_DIR) / "mp4_to_ftlv.py"
                cmd = [
                    sys.executable, str(script), 
                    "--in", str(in_path), 
                    "--out", str(output_path),
                    "--quality", str(quality),
                    "--fps", str(fps)
                ]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode != 0:
                    return {"ok": False, "input": original, "error": "Conversion failed", "details": proc.stderr.strip()}
                return {"ok": True, "input": original, "output": output_name, "kind": "mp4"}

            # Image handling...
            if kind == "jpeg":
                script = Path(BASE_DIR) / "image_to_ftlv.py"
                cmd = [sys.executable, str(script), "--in", str(in_path), "--out", str(output_path)]
                proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if proc.returncode == 0:
                    return {"ok": True, "input": original, "output": output_name, "kind": "jpeg"}
            if not ffmpeg:
                return {"ok": False, "input": original, "error": "Unsupported file without ffmpeg."}
            # With ffmpeg, convert any image to one 672x672 JPEG, then pack.
            frames_dir = Path(tmp) / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            jpg_path = frames_dir / "frame_000001.jpg"
            vf = "scale=672:672:force_original_aspect_ratio=increase,crop=672:672"
            
            # Use the same quality mapping as mp4_to_ftlv if possible, or just -q:v
            # ffmpeg -q:v 2 is best, 31 is worst.
            q_val = max(2, min(31, int(round(31 - (quality / 100.0) * 29))))
            
            cmd_ff = [ffmpeg, "-y", "-i", str(in_path), "-vf", vf, "-pix_fmt", "yuvj420p", "-q:v", str(q_val), "-frames:v", "1", str(jpg_path)]
            proc2 = subprocess.run(cmd_ff, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc2.returncode != 0 or not jpg_path.exists():
                return {"ok": False, "input": original, "error": "ffmpeg resize failed"}
            script2 = Path(BASE_DIR) / "frames_to_ftlv.py"
            cmd2 = [sys.executable, str(script2), "--frames-dir", str(frames_dir), "--out", str(output_path), "--fps", str(fps)]
            proc3 = subprocess.run(cmd2, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if proc3.returncode != 0:
                return {"ok": False, "input": original, "error": "Packing failed"}
            return {"ok": True, "input": original, "output": output_name, "kind": kind}

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
                res = convert_one_local(sf, quality=conv_quality, fps=conv_fps)
                task["results"].append(res)

                if res.get("ok"):
                    task["okCount"] += 1
                else:
                    task["failCount"] += 1
                task["progress"] = int(((i + 1) / total) * 100)

            settings = load_settings()
            for r in task["results"]:
                if r.get("ok") and r.get("output"):
                    settings.setdefault(r["output"], {})["enabled"] = True
            save_settings(settings)
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
    if filename in settings:
        if "enabled" in data:
            settings[filename]["enabled"] = bool(data.get("enabled"))
        if "crc" in data:
            crc = _safe_hex8(data.get("crc"))
            if not crc:
                return jsonify({'error': 'crc must be 8 hex characters (0-9, A-F)'}), 400
            settings[filename]["crc"] = crc
        save_settings(settings)
        return jsonify({'message': 'Updated'})
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/delete', methods=['POST'])
def delete_file():
    data = request.json
    filename = data.get('fileName')
    if not filename: return jsonify({'error': 'Missing filename'}), 400
    
    filepath = os.path.join(MEDIA_FOLDER, filename)
    if os.path.exists(filepath):
        os.remove(filepath)
    
    settings = load_settings()
    if filename in settings:
        del settings[filename]
        save_settings(settings)
        
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
    data = request.json or {}
    names = data.get("fileNames")
    if not isinstance(names, list) or not names:
        return jsonify({'error': 'fileNames must be a non-empty list'}), 400

    settings = load_settings()
    deleted = 0
    for n in names:
        if _delete_one_media(str(n), settings):
            deleted += 1

    save_settings(settings)
    return jsonify({'message': 'Deleted', 'deleted': deleted, 'requested': len(names)})


@app.route('/api/delete_all', methods=['POST'])
def delete_all():
    settings = load_settings()
    names = _list_media_files()
    deleted = 0
    for n in names:
        if _delete_one_media(n, settings):
            deleted += 1

    save_settings(settings)
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
    save_settings(settings)
    return jsonify({"message": "Reference FTL.LIS loaded", "path": REFERENCE_UPLOAD_PATH})


@app.route('/api/reference_lis/clear', methods=['POST'])
def clear_reference_lis():
    settings = load_settings()
    settings.setdefault("__generator", {})
    if "reference_lis" in settings["__generator"]:
        del settings["__generator"]["reference_lis"]
    save_settings(settings)
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

        crc = _safe_hex8(st.get("crc")) or _safe_hex8(ref_crc_map.get(fname)) or default_crc_hex8_for_file(fp)
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

    settings["__generator"] = gen
    save_settings(settings)
    update_paths()
    return jsonify(_load_generator_settings())


@app.route('/api/browse', methods=['GET'])
def browse_directory():
    if not HAS_TKINTER:
        return jsonify({"error": "Tkinter not available on this server"}), 501
    
    try:
        root = tk.Tk()
        root.withdraw()  # Hide main window
        root.attributes("-topmost", True) # Bring to front
        
        # Determine current target as starting point
        settings = load_settings()
        initial = settings.get("__generator", {}).get("target_directory", BASE_DIR)
        
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

if __name__ == '__main__':
    print("Fan Playlist Manager Running...")
    app.run(debug=True, port=5000)
