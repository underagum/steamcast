#!/usr/bin/env python3
"""
SteamCast — Prepare and broadcast multiple game trailers to Steam store pages.
Two-phase tool: PREP (convert + concat video files to Steam RTMP spec)
                CAST (configure RTMP keys, toggle streams on/off, monitor)

Usage:
    python steamcast.py          # Main menu
    python steamcast.py prep     # Jump to Prep
    python steamcast.py setup    # Jump to key setup
    python steamcast.py cast     # Jump to stream toggle
"""

import builtins
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

import urllib.request

# System monitoring — optional, auto-detected
try:
    import psutil

    _PSUTIL = True
except ImportError:
    _PSUTIL = False

# ─── Config ───────────────────────────────────────────────────────────

VERSION = "1.1.4"
if getattr(sys, 'frozen', False):
    ROOT_DIR = Path(sys.executable).resolve().parent
else:
    ROOT_DIR = Path(__file__).resolve().parent
INPUT_DIR = ROOT_DIR / "input"
OUTPUT_DIR = ROOT_DIR / "output"
FFMPEG_DIR = ROOT_DIR / "ffmpeg"
FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe" if sys.platform == "win32" else FFMPEG_DIR / "ffmpeg"
CONFIG_PATH = ROOT_DIR / "config.json"
LOG_DIR = ROOT_DIR / "logs"
CRASH_LOG = LOG_DIR / "steamcast_crash.log"


@dataclass
class EncoderSettings:
    codec: str
    preset: str
    cbr_flags: list[str] = field(default_factory=list)


@dataclass
class SteamSpec:
    video_profile: str = "high"
    video_level: str = "4.1"
    video_bitrate: str = "7000k"
    video_fps: int = 30
    video_width: int = 1920
    video_height: int = 1080
    keyframe_interval: int = 60  # 2s at 30fps
    audio_codec: str = "aac"
    audio_bitrate: str = "128k"
    audio_sample_rate: int = 44100

    def resolution(self) -> str:
        return f"{self.video_width}x{self.video_height}"


FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.0.1-essentials_build.zip"
VERSION_CHECK_URL = "https://raw.githubusercontent.com/underagum/steamcast/main/version.txt"
RTMP_INGEST = "rtmp://ingest-rtmp.broadcast.steamcontent.com/app"
SPEC = SteamSpec()

# ─── Utility ──────────────────────────────────────────────────────────

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".webm", ".flv", ".m4v"}
_cached_encoder: Optional[EncoderSettings] = None

# ─── Named Constants ──────────────────────────────────────────────────
RTMP_KEY_DISPLAY_CHARS = 8       # number of prefix chars shown for masked RTMP keys
SANITIZED_FILENAME_MAX = 80      # max length for sanitized filenames
DOWNLOAD_RETRY_MAX = 5           # max FFmpeg download attempts
DOWNLOAD_RETRY_DELAY = 3         # seconds between download retries
LOG_TAIL_LINES = 10              # lines shown on prep failure
VERSION_CHECK_TIMEOUT = 5        # seconds timeout for version check HTTP request


def repair_config(cfg: dict) -> dict:
    """Remove obviously corrupted game entries and return cleaned config."""
    if not isinstance(cfg.get("games"), dict):
        cfg["games"] = {}
        return cfg

    to_delete = []
    for gname, entry in list(cfg["games"].items()):
        # Delete entries that are not dicts
        if not isinstance(entry, dict):
            to_delete.append(gname)
            continue
        # Delete entries where the game name looks like a Steam RTMP key
        if gname.startswith("steam_") and len(gname) > 20:
            to_delete.append(gname)

    for gname in to_delete:
        del cfg["games"][gname]

    if to_delete:
        ok = save_config(cfg)
        if not ok:
            # Config couldn't be written — still return cleaned in-memory cfg
            # to prevent crash, but warn that corruption will return on restart
            console.print("[yellow]Config corruption detected but could not be saved to disk.[/]")
            console.print("[dim]The config will be cleaned on next successful save.[/]")
    return cfg


def sanitize_filename(name: str, max_len: int = SANITIZED_FILENAME_MAX) -> str:
    """Remove path-illegal characters and truncate."""
    safe = re.sub(r'[<>:"/\\|?*]', "_", name)
    return safe[:max_len]


def format_duration(seconds: float) -> str:
    h, m = divmod(int(seconds), 3600)
    m, s = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_size(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def rich_escape(text: str) -> str:
    """Escape [ in user-provided strings to prevent Rich MarkupErrors.
    Only [ needs escaping — ] is only dangerous as part of unclosed [/]."""
    return text.replace("[", "\\[")


# ══════════════════════════════════════════════════════════════════════
# System monitoring (psutil — optional)
# ══════════════════════════════════════════════════════════════════════

_net_baseline: Optional[int] = None  # bytes_sent from previous sample
_net_baseline_time: Optional[float] = None
_proc_cache: dict[int, "psutil.Process"] = {}  # pid → Process object, survives refreshes


def _read_log_bitrate(log_path: Path) -> str:
    """Read last 8 KB of ffmpeg stderr log to find latest ``bitrate=`` value.
    Returns '—' if no bitrate line found or file is unreadable."""
    try:
        with open(log_path, "r", errors="replace") as f:
            f.seek(0, 2)  # end of file
            size = f.tell()
            if size == 0:
                return "—"
            chunk_start = max(0, size - 8192)
            f.seek(chunk_start)
            new_data = f.read()
    except (OSError, ValueError):
        return "—"

    # Scan backwards — last bitrate= line is the current one
    for line in reversed(new_data.splitlines()):
        m = re.search(r"bitrate=\s*([\d.]+)kbits/s", line)
        if m:
            kbps = float(m.group(1))
            if kbps >= 1000:
                return f"{kbps / 1000:.1f}M"
            return f"{kbps:.0f}K"
    return "—"


def get_per_stream_stats(active_streams: dict) -> dict:
    """Return per-stream {gname: {cpu, bitrate}}. Empty if psutil missing."""
    if not _PSUTIL:
        return {}

    global _proc_cache
    result = {}

    for gname, stream in active_streams.items():
        proc = stream["process"]
        if proc.poll() is not None:
            # Dead process — no live stats
            continue

        # ── Per-process CPU ──
        pid = proc.pid
        try:
            p = _proc_cache.get(pid)
            if p is None:
                p = psutil.Process(pid)
                p.cpu_percent()  # prime
                _proc_cache[pid] = p
            cpu = p.cpu_percent()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            _proc_cache.pop(pid, None)
            cpu = 0.0

        # ── Bitrate from ffmpeg stderr log ──
        bitrate = _read_log_bitrate(stream["log_file"])

        result[gname] = {"cpu": cpu, "bitrate": bitrate}

    # Prune dead PIDs from cache
    live_pids = {s["process"].pid for s in active_streams.values() if s["process"].poll() is None}
    for pid in list(_proc_cache):
        if pid not in live_pids:
            _proc_cache.pop(pid, None)

    return result


def get_system_ram_tx() -> Optional[dict]:
    """Return system RAM% and total network TX rate. Returns None if psutil missing."""
    if not _PSUTIL:
        return None

    global _net_baseline, _net_baseline_time

    mem = psutil.virtual_memory().percent
    net = psutil.net_io_counters()
    now = time.time()
    rate_str = "—"

    if _net_baseline is not None and _net_baseline_time is not None:
        elapsed = now - _net_baseline_time
        if elapsed > 0.5:
            tx_delta = net.bytes_sent - _net_baseline
            rate = tx_delta / elapsed
            if rate >= 1024 * 1024:
                rate_str = f"{rate / 1024 / 1024:.1f} MB/s"
            elif rate >= 1024:
                rate_str = f"{rate / 1024:.0f} KB/s"
            else:
                rate_str = f"{rate:.0f} B/s"

    _net_baseline = net.bytes_sent
    _net_baseline_time = now

    return {"mem": mem, "net_tx": rate_str}


# ─── FFmpeg ───────────────────────────────────────────────────────────

def find_ffmpeg() -> Optional[str]:
    """Return path to ffmpeg. Priority: bundled > system PATH."""
    if FFMPEG_EXE.exists():
        return str(FFMPEG_EXE)
    # Check system PATH for local development/testing
    found = shutil.which("ffmpeg")
    if found:
        return found
    return None


def download_ffmpeg(console) -> bool:
    """Download portable FFmpeg from gyan.dev. Returns True on success."""
    console.print("[yellow]FFmpeg not found. Downloading portable build (~55 MB)...[/]")
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)

    zip_path = FFMPEG_DIR / "ffmpeg.zip"
    max_attempts = DOWNLOAD_RETRY_MAX

    for attempt in range(1, max_attempts + 1):
        try:
            console.print(f"[dim]Download attempt {attempt}/{max_attempts}...[/]")

            if RICH:
                with Progress(
                    TextColumn("[bold blue]{task.description}"),
                    BarColumn(),
                    DownloadColumn(),
                    TransferSpeedColumn(),
                    TimeRemainingColumn(),
                    console=console,
                    transient=True,
                ) as progress:
                    task = progress.add_task("Downloading...", total=None)

                    def reporthook(block_num: int, block_size: int, total_size: int):
                        downloaded = block_num * block_size
                        if total_size > 0:
                            progress.update(task, total=total_size, completed=downloaded)

                    urllib.request.urlretrieve(FFMPEG_URL, zip_path, reporthook=reporthook)
            else:
                urllib.request.urlretrieve(FFMPEG_URL, zip_path)

            break
        except Exception as e:
            console.print(f"[yellow]Download failed: {e}[/]")
            zip_path.unlink(missing_ok=True)
            if attempt < max_attempts:
                time.sleep(DOWNLOAD_RETRY_DELAY)
            else:
                console.print(f"[red]Could not download FFmpeg after {max_attempts} attempts.[/]")
                console.print(f"[dim]Download manually from: {FFMPEG_URL}[/]")
                return False

    # ── Extraction ──
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            members = zf.infolist()

            if RICH:
                with Progress(
                    TextColumn("[bold blue]{task.description}"),
                    BarColumn(),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    console=console,
                    transient=True,
                ) as progress:
                    task = progress.add_task("Extracting...", total=len(members))
                    for member in members:
                        zf.extract(member, FFMPEG_DIR)
                        progress.advance(task)
            else:
                console.print("[dim]Extracting...[/]")
                zf.extractall(FFMPEG_DIR)

        # Find ffmpeg.exe in extracted tree
        found = False
        for root, _, files in os.walk(FFMPEG_DIR):
            for fname in files:
                if "ffmpeg" in fname.lower() and (
                    fname.endswith(".exe") or ("ffmpeg" == fname and sys.platform != "win32")
                ):
                    src = Path(root) / fname
                    src.rename(FFMPEG_EXE)
                    found = True
                    break
            if found:
                break
        # Cleanup extracted subdirectories — binary is already at FFMPEG_EXE
        for d in FFMPEG_DIR.iterdir():
            if d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        FFMPEG_EXE.chmod(0o755)
        console.print(f"[green]FFmpeg ready at {FFMPEG_EXE}[/]")
        return True
    except Exception as e:
        console.print(f"[red]Extraction failed: {e}[/]")
        zip_path.unlink(missing_ok=True)
        return False


def detect_encoder(console) -> Optional[EncoderSettings]:
    """Probe ffmpeg for hardware encoders. Results cached.
    Returns None if the user declines fallback after NVENC validation
    failure — caller should abort Prep."""
    global _cached_encoder
    if _cached_encoder:
        return _cached_encoder

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found")

    console.print(f"[dim]Probing: {ffmpeg}[/]")

    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True, text=True
    )
    encoders = result.stdout + result.stderr

    # Priority 1: NVIDIA NVENC
    if "h264_nvenc" in encoders:
        enc = EncoderSettings(codec="h264_nvenc", preset="p7", cbr_flags=["-rc", "cbr"])
        ok, reason, debug_output = _validate_encoder(ffmpeg, enc)
        if ok:
            console.print("[cyan]NVIDIA NVENC detected — using hardware encoding.[/]")
            _cached_encoder = enc
            return _cached_encoder
        else:
            console.print(f"\n[yellow]⚠  NVENC test encode failed.[/]")
            console.print(f"[dim]Reason: {reason}[/]")
            if debug_output:
                console.print(f"[dim]FFmpeg output:[/]")
                for line in debug_output.strip().splitlines():
                    console.print(f"[dim]  {line}[/]")
            console.print()

            if RICH:
                fallback = Confirm.ask(
                    "[yellow]Fall back to CPU encoding (libx264)?[/]",
                    default=True,
                )
            else:
                fallback = input("Fall back to CPU encoding (libx264)? (Y/n): ").strip().lower() != "n"

            if fallback:
                console.print("[yellow]Using libx264 (CPU encoding — slower).[/]")
                _cached_encoder = EncoderSettings(codec="libx264", preset="slow")
                return _cached_encoder
            else:
                console.print("[dim]Aborting prep.[/]")
                console.print("[dim]To debug: run the ffmpeg command shown above and check the error.[/]")
                return None
    # Priority 2: Intel QSV
    if "h264_qsv" in encoders:
        console.print("[cyan]Intel QSV detected — using hardware encoding.[/]")
        _cached_encoder = EncoderSettings(codec="h264_qsv", preset="veryfast")
        return _cached_encoder
    # Priority 3: AMD AMF
    if "h264_amf" in encoders:
        console.print("[cyan]AMD AMF detected — using hardware encoding.[/]")
        _cached_encoder = EncoderSettings(codec="h264_amf", preset="quality", cbr_flags=["-rc", "cbr"])
        return _cached_encoder
    # Fallback
    console.print("[yellow]No hardware encoder. Using libx264 (slower).[/]")
    _cached_encoder = EncoderSettings(codec="libx264", preset="slow")
    return _cached_encoder


def _validate_encoder(ffmpeg: str, enc: EncoderSettings) -> tuple[bool, str, str]:
    """Run a minimal 1-frame encode to verify the encoder + driver actually work.
    Returns (ok, human_reason, full_ffmpeg_output).

    Only passes -c:v (codec).  Preset / CBR / bitrate flags are stripped
    because extreme combinations (e.g. p7 + CBR + 100k) can cause false
    negatives on perfectly valid driver installations."""
    cmd = [
        ffmpeg, "-y", "-hide_banner",
        "-f", "lavfi", "-i", "color=c=black:s=32x32:d=0.1",
        "-frames:v", "1",
        "-c:v", enc.codec,
        "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        merged = proc.stdout + proc.stderr

        # NVENC driver version mismatch
        m = re.search(r"Driver does not support the required nvenc API version.*", merged)
        if m:
            return False, m.group(0), merged
        m = re.search(r"minimum required Nvidia driver for nvenc is.*", merged)
        if m:
            return False, m.group(0), merged

        # Generic "could not open encoder"
        if "Could not open encoder" in merged or "Error while opening encoder" in merged:
            return False, "Encoder failed to initialize.", merged

        # Non-zero exit — show last meaningful error line
        if proc.returncode != 0:
            # Try to find the most specific error
            for pattern in [
                r"NVENCCuda.*error",
                r"nvenc.*fail",
                r"Cannot (init|load|open|find).*",
                r"Unknown encoder",
                r"(Error|error):.*",
                r"failed.*",
            ]:
                m = re.search(pattern, merged, re.IGNORECASE)
                if m:
                    return False, m.group(0).strip(), merged
            return False, f"exit code {proc.returncode}", merged

        return True, "", merged
    except subprocess.TimeoutExpired:
        return False, "Encoder validation timed out.", ""
    except Exception as e:
        return False, str(e), ""


def build_ffmpeg_args(
    enc: EncoderSettings,
    output_file: str,
    input_file: Optional[str] = None,
    playlist: Optional[str] = None,
) -> list[str]:
    """Build ffmpeg argument list for convert or concat."""
    args = ["-y"]

    if playlist:
        args += ["-f", "concat", "-safe", "0", "-i", playlist]
    elif input_file:
        args += ["-i", input_file]
    else:
        raise ValueError("Either input_file or playlist must be provided")

    args += [
        "-c:v", enc.codec,
        "-preset", enc.preset,
        *enc.cbr_flags,
        "-profile:v", SPEC.video_profile,
        "-level:v", SPEC.video_level,
        "-b:v", SPEC.video_bitrate,
        "-maxrate", SPEC.video_bitrate,
        "-bufsize", SPEC.video_bitrate,
        "-g", str(SPEC.keyframe_interval),
        "-keyint_min", str(SPEC.keyframe_interval),
        "-sc_threshold", "0",
        "-r", str(SPEC.video_fps),
        "-s", SPEC.resolution(),
        "-pix_fmt", "yuv420p",
        "-c:a", SPEC.audio_codec,
        "-b:a", SPEC.audio_bitrate,
        "-ar", str(SPEC.audio_sample_rate),
        "-movflags", "+faststart",
        output_file,
    ]

    return args


def run_ffmpeg(
    args: list[str],
    log_file: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> tuple[bool, str]:
    """Run ffmpeg, capture output, optionally write to log. Returns (success, output).

    If ``on_progress`` is provided, it is called with the condensed status string
    (e.g. ``"frame 180/??  fps 30  7.0M  speed 1.0x"``) each time ffmpeg emits
    a ``frame=`` or ``progress=`` line. The caller is responsible for display
    (typically ``print(..., end='\\r')`` or a Rich status line)."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False, "FFmpeg not found"

    proc = subprocess.Popen(
        [ffmpeg, *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Read stderr line-by-line (ffmpeg writes progress to stderr)
    all_lines: list[str] = []
    last_progress = ""
    try:
        assert proc.stderr is not None
        for line in proc.stderr:
            all_lines.append(line)
            stripped = line.strip()
            if "frame=" in stripped or "progress=" in stripped:
                last_progress = stripped
                if on_progress:
                    on_progress(_condense_progress(stripped))
        proc.wait()
    except Exception:
        proc.kill()
        proc.wait()

    # Also collect stdout (usually empty for ffmpeg)
    stdout = proc.stdout.read() if proc.stdout else ""
    proc.stdout.close() if proc.stdout else None
    proc.stderr.close() if proc.stderr else None

    output = stdout + "".join(all_lines)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(output, encoding="utf-8", errors="replace")

    success = proc.returncode == 0

    # Clean up 0-byte output from failed runs
    output_path = None
    try:
        mov_idx = args.index("-movflags")
        if mov_idx + 2 < len(args):
            output_path = Path(args[mov_idx + 2])
    except ValueError:
        pass
    if not output_path:
        last = args[-1]
        if not last.startswith("-") and ("/" in last or "\\" in last or "." in last):
            output_path = Path(last)

    if output_path:
        try:
            if success and output_path.exists():
                if output_path.stat().st_size == 0:
                    output_path.unlink()
                    success = False
            elif not success and output_path.exists():
                output_path.unlink(missing_ok=True)
        except OSError:
            pass

    return success, output


def _condense_progress(line: str) -> str:
    """Extract key fields from an ffmpeg status line into a compact display string.

    Input:  ``frame=  180 fps=30 q=-1.0 size=   15360kB time=00:00:06.00 bitrate=20971.5kbits/s speed=1.0x``
    Output: ``"frame 180  fps 30  7.0M  speed 1.0x"``
    """
    parts = []
    m = re.search(r"frame=\s*(\d+)", line)
    if m:
        parts.append(f"frame {m.group(1)}")
    m = re.search(r"fps=\s*([\d.]+)", line)
    if m:
        parts.append(f"fps {m.group(1)}")
    m = re.search(r"bitrate=\s*([\d.]+)kbits/s", line)
    if m:
        kbps = float(m.group(1))
        if kbps >= 1000:
            parts.append(f"{kbps / 1000:.1f}M")
        else:
            parts.append(f"{kbps:.0f}K")
    m = re.search(r"speed=\s*([\d.]+)x", line)
    if m:
        parts.append(f"speed {m.group(1)}x")
    return "  ".join(parts) if parts else line


def convert_video(
    input_file: Path,
    output_file: Path,
    enc: EncoderSettings,
    log_file: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> bool:
    """Convert a single video to Steam spec."""
    args = build_ffmpeg_args(enc, str(output_file), input_file=str(input_file))
    success, _ = run_ffmpeg(args, log_file, on_progress=on_progress)
    return success


def concat_videos(
    playlist_file: Path,
    output_file: Path,
    enc: EncoderSettings,
    log_file: Optional[Path] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> bool:
    """Concatenate multiple videos to Steam spec."""
    args = build_ffmpeg_args(enc, str(output_file), playlist=str(playlist_file))
    success, _ = run_ffmpeg(args, log_file, on_progress=on_progress)
    return success


def get_video_duration(filepath: Path) -> str:
    """Get video duration as HH:MM:SS string."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return "??:??:??"
    try:
        result = subprocess.run(
            [ffmpeg, "-i", str(filepath), "-hide_banner"],
            capture_output=True, text=True,
        )
        match = re.search(r"Duration: (\d+:\d+:\d+\.\d+)", result.stderr)
        if match:
            return match.group(1)
    except Exception:
        pass
    return "??:??:??"


# ─── Config ───────────────────────────────────────────────────────────

def load_config() -> dict:
    """Load config.json or return default. Repairs corrupted entries automatically."""
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text())
            return repair_config(cfg)
        except json.JSONDecodeError:
            backup = CONFIG_PATH.with_suffix(".json.bak")
            try:
                shutil.copy2(CONFIG_PATH, backup)
                console.print(f"[yellow]Warning: config.json is unreadable. Backed up to {backup}. Starting fresh.[/]")
            except OSError:
                console.print("[yellow]Warning: config.json is unreadable and could not be backed up. Starting fresh.[/]")
    return {"version": VERSION, "games": {}}


def save_config(cfg: dict) -> bool:
    """Write config.json. Returns True on success, False if write failed."""
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        return True
    except OSError as e:
        console.print(f"[red]Failed to save config: {e}[/]")
        return False


def get_rtmp_key(game_name: str) -> str:
    cfg = load_config()
    entry = cfg["games"].get(game_name, {})
    if not isinstance(entry, dict):
        return ""
    return entry.get("rtmp_key", "")


def set_rtmp_key(game_name: str, key: str):
    cfg = load_config()
    if game_name not in cfg["games"] or not isinstance(cfg["games"].get(game_name), dict):
        cfg["games"][game_name] = {"rtmp_key": key, "active": False}
    else:
        cfg["games"][game_name]["rtmp_key"] = key
    save_config(cfg)


def get_game_active(game_name: str) -> bool:
    cfg = load_config()
    entry = cfg["games"].get(game_name, {})
    if not isinstance(entry, dict):
        return False
    return entry.get("active", False)


def set_game_active(game_name: str, active: bool):
    cfg = load_config()
    if game_name not in cfg["games"] or not isinstance(cfg["games"].get(game_name), dict):
        cfg["games"][game_name] = {"rtmp_key": "", "active": active}
    else:
        cfg["games"][game_name]["active"] = active
    save_config(cfg)


# ─── Game Name Parsing ────────────────────────────────────────────────

def parse_game_name(filename: str) -> tuple[str, int, bool]:
    """Parse game name from filename. Returns (game_name, part_number, is_part)."""
    base = Path(filename).stem
    match = re.match(r"^(.+?)_(\d+)$", base)
    if match:
        try:
            part_num = int(match.group(2))
        except ValueError:
            part_num = 0
        return match.group(1).strip(), part_num, True
    return base.strip(), 0, False


# ─── Log helpers ──────────────────────────────────────────────────────

def tail_log(log_path: Path, lines: int = LOG_TAIL_LINES) -> str:
    """Return last N lines of a log file."""
    if not log_path.exists():
        return "(no log)"
    content = log_path.read_text(errors="replace")
    return "\n".join(content.splitlines()[-lines:])


# ══════════════════════════════════════════════════════════════════════
# Prerequisites
# ══════════════════════════════════════════════════════════════════════

_REQUIRED_PYTHON = (3, 9)

def check_prerequisites() -> None:
    """Verify Python version, Rich, and psutil before the app renders anything.
    Prints clear install instructions and exits with code 1 on failure."""
    failed = False

    # 1. Python version
    if sys.version_info < _REQUIRED_PYTHON:
        print(f"[ERROR] Python {'.'.join(map(str, _REQUIRED_PYTHON))}+ required.")
        print(f"        You are running Python {sys.version.split()[0]}.")
        print(f"        Download: https://python.org")
        failed = True

    # 2. Rich
    try:
        import rich  # noqa: F401 — import check only
    except ImportError:
        print("[ERROR] 'rich' is not installed.  It powers the SteamCast TUI.")
        print("        Fix:  pip install rich")
        failed = True

    # 3. psutil
    try:
        import psutil  # noqa: F401 — import check only
    except ImportError:
        print("[ERROR] 'psutil' is not installed.  Required for CPU/RAM monitoring.")
        print("        Fix:  pip install psutil")
        failed = True

    if failed:
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════════
# TUI (rich)
# ══════════════════════════════════════════════════════════════════════

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.live import Live
from rich.prompt import Prompt, Confirm
from rich.align import Align
from rich.progress import (
    Progress, BarColumn, TextColumn, DownloadColumn,
    TransferSpeedColumn, TimeRemainingColumn,
)

RICH = True
if sys.platform == "win32":
    # Python 3.14+ / Windows Terminal detection can degrade even
    # when ANSI is fully available.  Bypass auto-detection and
    # explicitly request truecolor output.
    console = Console(force_terminal=True, color_system="truecolor")
else:
    console = Console()
    # Terminal detection can fail on newer Python versions (3.14+).
    # If auto-detection returned no color system but the user isn't
    # explicitly suppressing color, retry with force_terminal=True.
    if console._color_system is None and "NO_COLOR" not in os.environ:
        console = Console(force_terminal=True)


def banner():
    """Display SteamCast banner."""
    console.print()
    if RICH:
        console.print(
            Panel.fit(
                Align.center(
                    f"[bold magenta]STEAMCAST v{VERSION}[/]\n"
                    "[dim]Steam broadcast video prep & cast[/]"
                ),
                border_style="magenta",
            )
        )
    else:
        console.print(f"  STEAMCAST v{VERSION}")
        console.print("  Steam broadcast video prep & cast")
        console.print()


def show_prep_phase():
    """PREP: Convert and concatenate video files to Steam spec."""
    banner()
    console.print("[bold yellow]=== PREP: Video Preparation ===[/]\n")

    # Ensure directories
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # Ensure FFmpeg
    if not find_ffmpeg():
        if not download_ffmpeg(console):
            console.input("[dim]Press Enter to continue...[/]") if RICH else input("\nPress Enter to continue...")
            return

    # Step 1: Tell user where to put files
    console.print(f"[bold]Step 1:[/] Copy your video files into: [cyan]{INPUT_DIR}[/]")
    console.print()
    console.print("[yellow]Naming guide:[/]")
    console.print('  Single video:     [white]gamename.mp4[/] (e.g. "dreadout 2.mp4")')
    console.print('  Multiple videos:  [white]gamename_1.mp4, gamename_2.mp4[/]')
    console.print()

    if RICH:
        ready = Confirm.ask("Have you placed all video files in the input folder?")
    else:
        ready = input("Have you placed all video files in the input folder? (y/n): ").lower().startswith("y")
    if not ready:
        console.print("[yellow]Come back when ready![/]")
        return

    # Step 2: Scan for videos
    console.print("\n[dim]Scanning input folder (including subdirectories)...[/]")
    video_files: list[Path] = []
    for ext in VIDEO_EXTENSIONS:
        video_files.extend(INPUT_DIR.rglob(f"*{ext}"))
        video_files.extend(INPUT_DIR.rglob(f"*{ext.upper()}"))

    video_files = sorted(set(video_files))

    if not video_files:
        console.print("[red]No video files found in input folder.[/]")
        console.input("[dim]Press Enter to continue...[/]") if RICH else input("\nPress Enter to continue...")
        return

    if RICH:
        table = Table(title="Found Videos", border_style="green")
        table.add_column("File", style="white")
        table.add_column("Duration", style="dim", justify="right")
        table.add_column("Size", style="dim", justify="right")
        for f in video_files:
            dur = get_video_duration(f)
            size = format_size(f.stat().st_size)
            table.add_row(f.name, dur, size)
        console.print(table)
    else:
        console.print("\n[Found Videos]")
        for f in video_files:
            dur = get_video_duration(f)
            size = format_size(f.stat().st_size)
            console.print(f"  {f.name}  ({dur})  {size}")

    # Step 3: Group by game name
    game_groups: dict[str, list[Path]] = {}
    for f in video_files:
        game_name, _, _ = parse_game_name(f.name)
        game_groups.setdefault(game_name, []).append(f)

    if RICH:
        group_table = Table(title="Grouped by Game", border_style="yellow")
        group_table.add_column("Game", style="white")
        group_table.add_column("Action", style="dim")
        group_table.add_column("Status", style="dim")
        for gname in sorted(game_groups):
            files = sorted(game_groups[gname], key=lambda f: f.name)
            action = f"convert + concat ({len(files)} files)" if len(files) > 1 else "convert only"
            safe = sanitize_filename(gname)
            out_path = OUTPUT_DIR / f"{safe}.mp4"
            exists = "[yellow][EXISTS][/]" if out_path.exists() else ""
            group_table.add_row(rich_escape(f"[{gname}]"), action, exists)
        console.print(group_table)
    else:
        console.print("\n[Grouped by Game]")
        for gname in sorted(game_groups):
            files = sorted(game_groups[gname], key=lambda f: f.name)
            action = f"convert + concat ({len(files)} files)" if len(files) > 1 else "convert only"
            safe = sanitize_filename(gname)
            out_path = OUTPUT_DIR / f"{safe}.mp4"
            exists = " [EXISTS]" if out_path.exists() else ""
            console.print(f"  {gname}: {action}{exists}")

    if RICH:
        ok = Confirm.ask("\nProceed with prep?")
    else:
        ok = input("\nProceed with prep? (y/n): ").lower().startswith("y")
    if not ok:
        return

    # Step 4: Process each game group
    enc = detect_encoder(console)
    if enc is None:
        return  # User declined fallback after NVENC validation failure
    success_count = 0
    fail_count = 0

    # ── Progress display helper ──
    def _show_progress(status: str) -> None:
        """Display a live-updating ffmpeg progress line with carriage return."""
        # Truncate to terminal width (assume 100 cols if unavailable)
        cols = shutil.get_terminal_size().columns or 100
        # Clean line (overwrite previous) + new status
        line = f"\r\033[K  [dim]{status}[/]" if RICH else f"\r\033[K  {status}"
        # Strip Rich markup for the display below
        clean = re.sub(r"\[/?[^\]]+\]", "", line) if RICH else line
        clean = clean[:cols]
        print(clean, end="", flush=True)

    for gname in sorted(game_groups):
        files = sorted(game_groups[gname], key=lambda f: f.name)
        safe_name = sanitize_filename(gname)
        out_path = OUTPUT_DIR / f"{safe_name}.mp4"

        # Check overwrite
        if out_path.exists():
            if RICH:
                ok_overwrite = Confirm.ask(f'"{rich_escape(gname)}.mp4" already exists. Overwrite?')
            else:
                ok_overwrite = input(f'"{rich_escape(gname)}.mp4" already exists. Overwrite? (y/n): ').lower().startswith("y")
            if not ok_overwrite:
                console.print(f"[dim]Skipping {rich_escape(gname)}[/]")
                continue

        if len(files) == 1:
            # Single file — just convert
            prep_log = LOG_DIR / f"{safe_name}_prep.log"
            console.print(f"\n[dim]Converting {rich_escape(gname)}...[/]")
            ok = convert_video(files[0], out_path, enc, log_file=prep_log, on_progress=_show_progress)
            # Clear progress line
            print("\r\033[K", end="", flush=True)
            if ok:
                console.print(f"[green]✓ {rich_escape(gname)} converted successfully[/]")
                success_count += 1
            else:
                console.print(f"[red]✗ Failed to convert {rich_escape(gname)}[/]")
                console.print(f"  [dim]Full log: {prep_log}[/]")
                if prep_log.exists():
                    console.print("  [dim]Last 10 lines:[/]")
                    for line in tail_log(prep_log).splitlines():
                        console.print(f"    [white]{line}[/]")
                fail_count += 1
        else:
            # Multi-file: convert each part, then concat
            temp_dir = INPUT_DIR / f".temp_{uuid.uuid4().hex[:8]}"
            temp_dir.mkdir(parents=True, exist_ok=True)

            converted = []
            all_ok = True

            for idx, f in enumerate(files, 1):
                temp_out = temp_dir / f"{f.stem}_steam.mp4"
                part_log = LOG_DIR / f"{safe_name}_part{idx}_prep.log"
                console.print(f"\n[dim]Converting {f.name}...[/]")
                ok = convert_video(f, temp_out, enc, log_file=part_log, on_progress=_show_progress)
                # Clear progress line
                print("\r\033[K", end="", flush=True)
                if not ok:
                    console.print(f"[red]✗ Failed to convert {f.name}[/]")
                    console.print(f"  [dim]Full log: {part_log}[/]")
                    if part_log.exists():
                        console.print("  [dim]Last 10 lines:[/]")
                        for line in tail_log(part_log).splitlines():
                            console.print(f"    [white]{line}[/]")
                    all_ok = False
                    break
                converted.append(temp_out)

            if all_ok:
                # Create playlist
                playlist_path = temp_dir / "playlist.txt"
                with open(playlist_path, "w") as pf:
                    for cf in converted:
                        path_str = str(cf).replace("\\", "/")
                        if "'" in path_str:
                            console.print(f"[yellow]Warning: path contains quote, concat may fail: {cf.name}[/]")
                        pf.write(f"file '{path_str}'\n")

                # Concat
                concat_log = LOG_DIR / f"{safe_name}_concat.log"
                console.print(f"\n[dim]Concatenating {len(converted)} files for {rich_escape(gname)}...[/]")
                ok = concat_videos(playlist_path, out_path, enc, log_file=concat_log, on_progress=_show_progress)
                # Clear progress line
                print("\r\033[K", end="", flush=True)

                if ok:
                    console.print(f"[green]✓ {rich_escape(gname)} ready: {out_path}[/]")
                    success_count += 1
                else:
                    console.print(f"[red]✗ Failed to concatenate {rich_escape(gname)}[/]")
                    console.print(f"  [dim]Full log: {concat_log}[/]")
                    if concat_log.exists():
                        console.print("  [dim]Last 10 lines:[/]")
                        for line in tail_log(concat_log).splitlines():
                            console.print(f"    [white]{line}[/]")
                    fail_count += 1
            else:
                fail_count += 1

            # Cleanup temp
            shutil.rmtree(temp_dir, ignore_errors=True)

    # Report
    console.print(f"\n[bold green]=== Prep Complete ===[/]")
    if success_count > 0:
        console.print(f"[green]✓ {success_count} succeeded[/]")
        for f in sorted(OUTPUT_DIR.glob("*.mp4")):
            if f.stat().st_size > 0:
                console.print(f"  [dim]{f.name} ({format_size(f.stat().st_size)})[/]")
    if fail_count > 0:
        console.print(f"[red]✗ {fail_count} failed — see full logs in: {LOG_DIR}[/]")
    if success_count == 0 and fail_count == 0:
        console.print("[yellow]No files were processed.[/]")

    if RICH:
        console.input("[dim]Press Enter to continue...[/]")
    else:
        input("\nPress Enter to continue...")


def show_cast_setup():
    """Setup: Add, edit, or delete games and RTMP keys."""
    banner()
    console.print("[bold yellow]=== CAST SETUP: Game & Key Configuration ===[/]\n")
    console.print(f"[dim]All data stored locally in: {CONFIG_PATH}[/]")
    console.print("[dim]No data is sent anywhere — this file stays on your machine.[/]\n")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    available_videos = {f.stem: f for f in sorted(OUTPUT_DIR.glob("*.mp4")) if f.stat().st_size > 0}

    while True:
        cfg = load_config()
        existing = sorted(cfg["games"].keys())

        if existing:
            console.print("[yellow]Configured games:[/]")
            for i, gname in enumerate(existing, 1):
                entry = cfg["games"].get(gname, {})
                if isinstance(entry, dict):
                    key = entry.get("rtmp_key", "")
                else:
                    key = ""
                if not isinstance(key, str):
                    key = ""
                masked = f"{key[:RTMP_KEY_DISPLAY_CHARS]}..." if key else "(no key)"
                safe_g = sanitize_filename(gname)
                vid = available_videos.get(safe_g)
                video_status = "✓" if vid else "⚠ no video"
                console.print(f"  [white][{i}][/] {rich_escape(gname)}  [dim]{masked}[/]  {video_status}")
        else:
            console.print("[dim]No games configured yet.[/]")

        if available_videos:
            console.print("\n[yellow]Videos in output folder:[/]")
            for stem, f in available_videos.items():
                entry = cfg["games"].get(stem, {})
                has_key = bool(entry.get("rtmp_key", "")) if isinstance(entry, dict) else False
                status = "✓ key set" if has_key else "⚠ no key"
                console.print(f"  [white]{f.name}[/] — {status}")

        # Menu
        console.print(f"\n[yellow]{'─' * 40}[/]")
        if existing:
            console.print(f"[white]\\[1-{len(existing)}][/] Edit game  |  [cyan][A][/] Add new  |  [red][D][/] Delete  |  [dim][Q][/] Done")
        else:
            console.print("[cyan][A][/] Add new game  |  [dim][Q][/] Done")
        console.print()

        if RICH:
            choice = Prompt.ask("[cyan]Select option[/]", default="q").strip().lower()
        else:
            choice = input("Select option: ").strip().lower()

        if choice == "q":
            break

        elif choice == "a":
            # Add new game
            if RICH:
                gname = Prompt.ask("[cyan]Enter game name[/]", default="").strip()
            else:
                gname = input("Enter game name: ").strip()
            if not gname:
                console.print("[yellow]Game name cannot be empty.[/]")
                continue

            current_key = get_rtmp_key(gname)
            if current_key:
                console.print(f"[yellow]'{rich_escape(gname)}' already configured.[/]")

            if RICH:
                key = Prompt.ask(f"[cyan]Enter RTMP key for '{rich_escape(gname)}'[/]", default=current_key)
            else:
                key = input(f"Enter RTMP key for '{rich_escape(gname)}' [{current_key}]: ").strip()
            if key:
                set_rtmp_key(gname, key)
                console.print(f"[green]✓ Key saved for '{rich_escape(gname)}'[/]")
            else:
                console.print("[yellow]No key entered — skipped.[/]")

        elif choice == "d" and existing:
            # Delete game(s)
            console.print()
            console.print("[yellow]Delete which game?[/]")
            for i, gname in enumerate(existing, 1):
                console.print(f"  [white][{i}][/] {rich_escape(gname)}")
            console.print(f"  [red][X][/] Cancel")
            if RICH:
                del_choice = Prompt.ask("[red]Enter number to delete[/]", default="x").strip().lower()
            else:
                del_choice = input("Enter number to delete (x to cancel): ").strip().lower()
            if del_choice == "x":
                continue
            try:
                idx = int(del_choice) - 1
                if 0 <= idx < len(existing):
                    gname = existing[idx]
                    if RICH:
                        confirm = Confirm.ask(f"[red]Delete '{rich_escape(gname)}' and its RTMP key?[/]")
                    else:
                        confirm = input(f"Delete '{rich_escape(gname)}'? (y/n): ").lower().startswith("y")
                    if confirm:
                        del cfg["games"][gname]
                        save_config(cfg)
                        console.print(f"[green]✓ '{rich_escape(gname)}' deleted.[/]")
                else:
                    console.print("[yellow]Invalid number.[/]")
            except ValueError:
                console.print("[yellow]Invalid input.[/]")

        elif choice in "123456789" and existing:
            # Edit existing game
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(existing):
                    gname = existing[idx]
                    key = get_rtmp_key(gname)
                    if not isinstance(key, str):
                        key = ""
                    console.print(f"\n[yellow]Editing: {rich_escape(gname)}[/]")
                    console.print(f"  Current key: [dim]{key[:RTMP_KEY_DISPLAY_CHARS]}...[/]" if key else "  [dim]No key set[/]")

                    # Edit name
                    if RICH:
                        new_name = Prompt.ask("[cyan]New name (leave empty to keep)[/]", default="").strip()
                    else:
                        new_name = input("New name (leave empty to keep): ").strip()
                    if new_name and new_name != gname:
                        if new_name in cfg["games"]:
                            console.print(f"[yellow]'{rich_escape(new_name)}' already exists — rename cancelled.[/]")
                        else:
                            # Rename: copy key to new name, delete old
                            cfg["games"][new_name] = cfg["games"].pop(gname)
                            save_config(cfg)
                            console.print(f"[green]✓ Renamed '{rich_escape(gname)}' → '{rich_escape(new_name)}'[/]")
                            gname = new_name

                    # Edit key
                    current_key = get_rtmp_key(gname)
                    if not isinstance(current_key, str):
                        current_key = ""
                    if RICH:
                        new_key = Prompt.ask(f"[cyan]New RTMP key for '{rich_escape(gname)}'[/]", default=current_key)
                    else:
                        new_key = input(f"New RTMP key for '{rich_escape(gname)}' [{current_key}]: ").strip()
                    if new_key != current_key:
                        set_rtmp_key(gname, new_key)
                        console.print(f"[green]✓ Key updated for '{rich_escape(gname)}'[/]")
                else:
                    console.print("[yellow]Invalid number.[/]")
            except ValueError:
                console.print("[yellow]Invalid input.[/]")
        else:
            continue

    console.print("[green]✓ Setup complete.[/]")
    if RICH:
        console.input("[dim]Press Enter to continue...[/]")
    else:
        input("\nPress Enter to continue...")


def show_cast():
    """CAST: Toggle games and start broadcasting."""
    banner()
    console.print("[bold yellow]=== CAST: Stream Toggle ===[/]\n")

    # Ensure FFmpeg
    if not find_ffmpeg():
        if not download_ffmpeg(console):
            return

    cfg = load_config()
    config_games = sorted(cfg["games"].keys())

    if not config_games:
        while not config_games:
            console.print("[yellow]No games configured yet.[/]")
            if RICH:
                go = Confirm.ask("Go to setup?")
            else:
                go = input("Go to setup? (y/n): ").lower().startswith("y")
            if not go:
                return
            show_cast_setup()
            cfg = load_config()
            config_games = sorted(cfg["games"].keys())

    # Scan output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    available_videos = {
        f.stem: f for f in OUTPUT_DIR.glob("*.mp4") if f.stat().st_size > 0
    }

    while True:
        banner()
        console.print("[bold yellow]=== CAST: Select Games ===[/]\n")

        menu_items = []
        cfg_render = load_config()
        for i, gname in enumerate(config_games, 1):
            entry = cfg_render["games"].get(gname, {})
            if isinstance(entry, dict):
                is_active = entry.get("active", False)
                has_key = bool(entry.get("rtmp_key", ""))
            else:
                is_active = False
                has_key = False
            has_video = sanitize_filename(gname) in available_videos

            toggle = "[green]ON[/]" if is_active else "[dim]OFF[/]"
            if has_video:
                status = "✓ ready" if has_key else "⚠ no key"
            else:
                status = "⚠ no video"

            console.print(f"  [white][{i}][/] {rich_escape(gname)}  [{toggle}]  {status}")
            menu_items.append({
                "index": i, "game": gname, "active": is_active,
                "has_video": has_video, "has_key": has_key,
            })

        console.print()
        console.print("[cyan][T][/] Toggle ALL")
        console.print("[cyan][A][/] Add/Edit keys (Setup)")
        console.print("[cyan][P][/] Go to Prep")
        console.print("[green][S][/] Start broadcasting")
        console.print("[red][Q][/] Back to main menu")

        if RICH:
            choice = Prompt.ask("\n[cyan]Enter number to toggle, or command[/]", default="").strip().lower()
        else:
            choice = input("\nEnter number to toggle, or command: ").strip().lower()

        if choice == "q":
            break
        elif choice == "t":
            any_on = any(m["active"] for m in menu_items)
            new_state = not any_on
            cfg_toggle = load_config()
            for m in menu_items:
                if m["game"] in cfg_toggle["games"] and isinstance(cfg_toggle["games"][m["game"]], dict):
                    cfg_toggle["games"][m["game"]]["active"] = new_state
            save_config(cfg_toggle)
            console.print(f"[cyan]All games toggled {'ON' if new_state else 'OFF'}[/]")
        elif choice == "a":
            show_cast_setup()
            cfg = load_config()
            config_games = sorted(cfg["games"].keys())
            available_videos = {
                f.stem: f for f in OUTPUT_DIR.glob("*.mp4") if f.stat().st_size > 0
            }
        elif choice == "p":
            show_prep_phase()
            # Refresh video list after Prep may have created new files
            available_videos = {
                f.stem: f for f in OUTPUT_DIR.glob("*.mp4") if f.stat().st_size > 0
            }
            continue
        elif choice == "s":
            to_start = [m for m in menu_items if m["active"] and m["has_video"] and m["has_key"]]
            problems = [m for m in menu_items if m["active"] and (not m["has_video"] or not m["has_key"])]

            if problems:
                console.print()
                console.print("[yellow]Some active games have issues:[/]")
                for p in problems:
                    if not p["has_video"]:
                        console.print(f"  [red]  {rich_escape(p['game'])}: no video file (run Prep)[/]")
                    if not p["has_key"]:
                        console.print(f"  [red]  {rich_escape(p['game'])}: no RTMP key (run Setup)[/]")
                if RICH:
                    ok = Confirm.ask("\nStart anyway (skip problematic games)?")
                else:
                    ok = input("\nStart anyway (y/n): ").lower().startswith("y")
                if not ok:
                    continue

            if not to_start:
                console.print("[yellow]No games ready to broadcast.[/]")
                if RICH:
                    console.input("[dim]Press Enter to continue...[/]")
                else:
                    input("\nPress Enter to continue...")
                continue

            run_cast_stream(to_start)
            return
        else:
            # Try number input
            try:
                num = int(choice)
                item = next((m for m in menu_items if m["index"] == num), None)
                if item:
                    new_state = not item["active"]
                    set_game_active(item["game"], new_state)
                    if not item["has_video"]:
                        console.print(f"[yellow]'{rich_escape(item['game'])}' has no video. Press P to go to Prep.[/]")
                    if not item["has_key"]:
                        console.print(f"[yellow]'{rich_escape(item['game'])}' has no RTMP key. Press A to go to Setup.[/]")
            except ValueError:
                pass


def run_cast_stream(games: list[dict]):
    """Start streaming selected games and monitor."""
    banner()
    console.print("[bold red]=== 🔴 STARTING BROADCAST ===[/]\n")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    active_streams = {}

    ffmpeg_path = find_ffmpeg()
    if not ffmpeg_path:
        console.print("[red]FFmpeg not found.[/]")
        return

    for game in games:
        gname = game["game"]
        safe_name = sanitize_filename(gname)
        video_path = OUTPUT_DIR / f"{safe_name}.mp4"

        if not video_path.exists():
            console.print(f"[red]Video not found for '{rich_escape(gname)}' — skipping.[/]")
            continue

        rtmp_key = get_rtmp_key(gname)
        if not rtmp_key:
            console.print(f"[red]No RTMP key for '{rich_escape(gname)}' — skipping.[/]")
            continue

        log_file = LOG_DIR / f"{safe_name}_cast.log"
        stream_url = f"{RTMP_INGEST}/{rtmp_key}"

        console.print(f"[dim]Starting stream for {rich_escape(gname)}...[/]")

        cmd = [
            ffmpeg_path,
            "-re", "-y", "-stream_loop", "-1",
            "-i", str(video_path),
            "-c", "copy",
            "-f", "flv",
            stream_url,
        ]

        try:
            log_fh = open(log_file, "w")
        except OSError as e:
            console.print(f"[red]Failed to open log file for '{rich_escape(gname)}': {e}[/]")
            continue

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
        except Exception as e:
            log_fh.close()
            console.print(f"[red]Failed to start stream for {rich_escape(gname)}: {e}[/]")
            continue

        active_streams[gname] = {
            "pid": proc.pid,
            "process": proc,
            "start_time": datetime.now(),
            "log_file": log_file,
            "log_fh": log_fh,
            "rtmp_key": rtmp_key,
        }
        console.print(f"[green]✓ {rich_escape(gname)} started (PID {proc.pid})[/]")
        time.sleep(1)  # Stagger starts

    if not active_streams:
        console.print("[red]No streams started.[/]")
        if RICH:
            console.input("[dim]Press Enter to continue...[/]")
        else:
            input("\nPress Enter to continue...")
        return

    # Monitor loop
    console.print("\n[bold red]=== 🔴 CASTING — Press Enter to stop all ===[/]")

    if not RICH:
        # Plain text monitor (non-rich)
        from threading import Thread

        running = True

        def key_listener():
            nonlocal running
            try:
                input()
                running = False
            except EOFError:
                pass

        Thread(target=key_listener, daemon=True).start()

        while running:
            stream_stats = get_per_stream_stats(active_streams) if _PSUTIL else {}
            show_detail = _PSUTIL
            for gname in sorted(active_streams):
                stream = active_streams[gname]
                proc = stream["process"]
                elapsed = (datetime.now() - stream["start_time"]).total_seconds()
                status = "● RUNNING" if proc.poll() is None else "✗ STOPPED"
                color = "\033[32m" if proc.poll() is None else "\033[31m"

                # Per-stream CPU + bitrate (only if psutil is available)
                if show_detail and proc.poll() is None:
                    ss = stream_stats.get(gname, {})
                    cpu_val = ss.get("cpu", 0)
                    bitrate_val = ss.get("bitrate", "—")
                    cpu_color = "\033[33m" if cpu_val > 50 else "\033[32m"
                    detail = f"  CPU: {cpu_color}{cpu_val:.0f}%\033[0m  {bitrate_val}"
                else:
                    detail = ""

                print(f"\033[K  {color}{gname}  [{status}]  ({format_duration(elapsed)})  PID {stream['pid']}{detail}\033[0m")

            # System RAM + total TX
            sys_stats = get_system_ram_tx()
            if sys_stats:
                mem_color = "\033[31m" if sys_stats["mem"] > 85 else "\033[33m" if sys_stats["mem"] > 60 else "\033[32m"
                print(f"\033[K  RAM: {mem_color}{sys_stats['mem']:.0f}%\033[0m  TX: {sys_stats['net_tx']}")
                lines = len(active_streams) + 1
            else:
                lines = len(active_streams)
            print(f"\033[{lines}A", end="")
            time.sleep(2)
        print("\n")
    else:
        # Rich live monitor
        running = True

        def generate_table():
            table = Table.grid(padding=(0, 2))

            # Per-stream stats (CPU + bitrate from ffmpeg log)
            stream_stats = get_per_stream_stats(active_streams) if _PSUTIL else {}
            show_detail = _PSUTIL

            for gname in sorted(active_streams):
                stream = active_streams[gname]
                proc = stream["process"]
                elapsed = (datetime.now() - stream["start_time"]).total_seconds()
                if proc.poll() is None:
                    row_style = "green"
                    icon = "[green]●[/]"
                    status = "RUNNING"

                    # Per-stream CPU + bitrate (only if psutil is available)
                    if show_detail:
                        ss = stream_stats.get(gname, {})
                        cpu_val = ss.get("cpu", 0)
                        bitrate_val = ss.get("bitrate", "—")
                        cpu_str = f"[dim]{cpu_val:.0f}%[/]" if cpu_val < 50 else f"[yellow]{cpu_val:.0f}%[/]"
                        bitrate_str = f"[dim]{bitrate_val}[/]"
                        detail = f"  CPU {cpu_str}  {bitrate_str}"
                    else:
                        detail = ""
                else:
                    row_style = "red"
                    icon = "[red]✗[/]"
                    status = "STOPPED"
                    detail = ""

                table.add_row(
                    f"[bold {row_style}]{rich_escape(gname)}[/]",
                    f"[{row_style}]{icon} {status}[/]",
                    f"[dim]({format_duration(elapsed)})[/]",
                    f"[dim]PID {stream['pid']}[/]" + detail,
                )

            # System RAM + total TX (system-wide)
            sys_stats = get_system_ram_tx()
            if sys_stats:
                table.add_row("")  # spacer
                mem_color = "red" if sys_stats["mem"] > 85 else "yellow" if sys_stats["mem"] > 60 else "green"
                table.add_row(
                    f"[dim]RAM:[/] [{mem_color}]{sys_stats['mem']:.0f}%[/]  "
                    f"[dim]TX:[/] [white]{sys_stats['net_tx']}[/]",
                )

            return table

        import threading

        def key_listener():
            nonlocal running
            try:
                input()
                running = False
            except EOFError:
                pass

        threading.Thread(target=key_listener, daemon=True).start()

        try:
            with Live(generate_table(), refresh_per_second=2, console=console) as live:
                while running:
                    live.update(generate_table())
                    time.sleep(0.5)
        except KeyboardInterrupt:
            running = False

    # Stop all streams
    console.print("\n[yellow]=== Stopping all streams ===[/]")
    for gname in sorted(active_streams):
        stream = active_streams[gname]
        proc = stream["process"]
        if proc.poll() is None:
            console.print(f"[dim]Stopping {rich_escape(gname)} (PID {proc.pid})...[/]")
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        stream["log_fh"].close()

        # Redact RTMP key from log
        log_file = stream["log_file"]
        if log_file.exists() and stream["rtmp_key"]:
            try:
                content = log_file.read_text()
                content = content.replace(stream["rtmp_key"], "***REDACTED***")
                log_file.write_text(content)
            except Exception:
                pass

        set_game_active(gname, False)
        console.print(f"[green]✓ {rich_escape(gname)} stopped[/]")

    console.print("\n[green]✓ All streams stopped.[/]")
    if RICH:
        console.input("[dim]Press Enter to continue...[/]")
    else:
        input("\nPress Enter to continue...")


def version_check():
    """Check GitHub for newer version (runs in background thread to avoid
    blocking the main menu on slow/no connections)."""
    def _fetch():
        try:
            req = urllib.request.Request(VERSION_CHECK_URL)
            with urllib.request.urlopen(req, timeout=VERSION_CHECK_TIMEOUT) as resp:
                remote = resp.read().decode().strip()
            if remote and remote != VERSION:
                console.print()
                console.print(f"[yellow]⚠ A newer version of SteamCast ({remote}) is available![/]")
                console.print("[yellow]  Download: https://github.com/underagum/steamcast/releases[/]")
                console.print()
        except Exception:
            pass

    t = threading.Thread(target=_fetch, daemon=True)
    t.start()


def show_main_menu():
    """Main menu loop."""
    version_check()

    while True:
        banner()
        console.print("  [white][1][/] [bold]Prepare Videos (PREP)[/]")
        console.print("       [dim]Convert + concatenate game trailers[/]")
        console.print()
        console.print("  [white][2][/] [bold]Manage Broadcast (CAST)[/]")
        console.print("       [dim]Set up keys, toggle streams, start/stop[/]")
        console.print()
        console.print("  [white][3][/] [bold]Setup (RTMP Keys)[/]")
        console.print("       [dim]Add or edit game names and stream keys[/]")
        console.print()
        console.print("  [red][Q][/] Quit")
        console.print()

        if RICH:
            choice = Prompt.ask("[cyan]Select option[/]", default="").strip().lower()
        else:
            choice = input("Select option: ").strip().lower()

        if choice == "1":
            show_prep_phase()
        elif choice == "2":
            show_cast()
        elif choice == "3":
            show_cast_setup()
        elif choice == "q":
            console.print("\n[green]Goodbye![/]")
            break
        else:
            console.print("[yellow]Invalid option.[/]")


# ─── Entry Point ─────────────────────────────────────────────────────

def setup_crash_logging():
    """Install a global exception hook that writes tracebacks to a crash log."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    # File handler — appends every crash
    fh = logging.FileHandler(str(CRASH_LOG), encoding="utf-8")
    fh.setLevel(logging.ERROR)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    crash_logger = logging.getLogger("steamcast.crash")
    crash_logger.addHandler(fh)

    # Install global exception hook
    def _crash_handler(exc_type, exc_value, exc_tb):
        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        crash_logger.error("Unhandled exception:\n%s", tb_text)
        # Also print to stderr so it's visible in terminal
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  STEAMCAST CRASH — see: {CRASH_LOG}", file=sys.stderr)
        print(f"{'='*60}", file=sys.stderr)
        traceback.print_exception(exc_type, exc_value, exc_tb, file=sys.stderr)
        if RICH:
            try:
                console.input("\n[dim]Press Enter to exit...[/]")
            except Exception:
                pass

    sys.excepthook = _crash_handler


def main():
    check_prerequisites()
    setup_crash_logging()
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()

        if cmd == "prep":
            show_prep_phase()
        elif cmd == "setup":
            show_cast_setup()
        elif cmd == "cast":
            show_cast()
        else:
            show_main_menu()
    else:
        show_main_menu()


if __name__ == "__main__":
    main()
