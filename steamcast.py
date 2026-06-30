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
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import urllib.request

# ─── Config ───────────────────────────────────────────────────────────

VERSION = "1.0.0-beta"
ROOT_DIR = Path(__file__).resolve().parent
INPUT_DIR = ROOT_DIR / "input"
OUTPUT_DIR = ROOT_DIR / "output"
FFMPEG_DIR = ROOT_DIR / "ffmpeg"
FFMPEG_EXE = FFMPEG_DIR / "ffmpeg.exe" if sys.platform == "win32" else FFMPEG_DIR / "ffmpeg"
CONFIG_PATH = ROOT_DIR / "config.json"
LOG_DIR = ROOT_DIR / "logs"


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


FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
VERSION_CHECK_URL = "https://raw.githubusercontent.com/underagum/steamcast/main/version.txt"
RTMP_INGEST = "rtmp://ingest-rtmp.broadcast.steamcontent.com/app"
SPEC = SteamSpec()

# ─── Utility ──────────────────────────────────────────────────────────

VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".webm", ".flv", ".m4v"}
_cached_encoder: Optional[EncoderSettings] = None


def sanitize_filename(name: str, max_len: int = 80) -> str:
    """Remove path-illegal characters and truncate."""
    safe = re.sub(r'[<>:"/\\|?*]', "_", name)
    return safe[:max_len]


def format_duration(seconds: float) -> str:
    h, m = divmod(int(seconds), 3600)
    m, s = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def format_size(size_bytes: int) -> str:
    return f"{size_bytes / (1024 * 1024):.1f} MB"


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
    for attempt in range(1, 6):
        try:
            console.print(f"[dim]Download attempt {attempt}/5...[/]")
            urllib.request.urlretrieve(FFMPEG_URL, zip_path)
            break
        except Exception as e:
            console.print(f"[yellow]Download failed: {e}[/]")
            if attempt < 5:
                time.sleep(3)
            else:
                console.print("[red]Could not download FFmpeg after 5 attempts.[/]")
                console.print(f"[dim]Download manually from: {FFMPEG_URL}[/]")
                return False

    console.print("[dim]Extracting...[/]")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(FFMPEG_DIR)
        # Find ffmpeg.exe in extracted tree
        for root, _, files in os.walk(FFMPEG_DIR):
            for fname in files:
                if "ffmpeg" in fname.lower() and (
                    fname.endswith(".exe") or ("ffmpeg" == fname and sys.platform != "win32")
                ):
                    src = Path(root) / fname
                    src.rename(FFMPEG_EXE)
                    break
        # Cleanup
        for d in FFMPEG_DIR.iterdir():
            if d.is_dir() and d.name != "ffmpeg":
                shutil.rmtree(d, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        FFMPEG_EXE.chmod(0o755)
        console.print(f"[green]FFmpeg ready at {FFMPEG_EXE}[/]")
        return True
    except Exception as e:
        console.print(f"[red]Extraction failed: {e}[/]")
        return False


def detect_encoder(console) -> EncoderSettings:
    """Probe ffmpeg for hardware encoders. Results cached."""
    global _cached_encoder
    if _cached_encoder:
        return _cached_encoder

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise RuntimeError("FFmpeg not found")

    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True, text=True
    )
    encoders = result.stdout + result.stderr

    # Priority 1: NVIDIA NVENC
    if "h264_nvenc" in encoders:
        console.print("[cyan]NVIDIA NVENC detected — using hardware encoding.[/]")
        _cached_encoder = EncoderSettings(codec="h264_nvenc", preset="p7", cbr_flags=["-rc", "cbr"])
        return _cached_encoder
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


def build_ffmpeg_args(
    enc: EncoderSettings,
    input_file: str,
    output_file: str,
    playlist: Optional[str] = None,
) -> list[str]:
    """Build ffmpeg argument list for convert or concat."""
    args = ["-y"]

    if playlist:
        args += ["-f", "concat", "-safe", "0", "-i", playlist]
    else:
        args += ["-i", input_file]

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


def run_ffmpeg(args: list[str], log_file: Optional[Path] = None) -> tuple[bool, str]:
    """Run ffmpeg, capture output, optionally write to log. Returns (success, output)."""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return False, "FFmpeg not found"

    proc = subprocess.run(
        [ffmpeg, *args],
        capture_output=True,
        text=True,
    )
    output = proc.stdout + proc.stderr

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        log_file.write_text(output, encoding="utf-8", errors="replace")

    success = proc.returncode == 0

    # Clean up 0-byte output from failed runs
    # Find output path: scan for last positional arg that looks like a file path
    output_path = None
    # Find output after -movflags +faststart
    try:
        mov_idx = args.index("-movflags")
        if mov_idx + 2 < len(args):
            output_path = Path(args[mov_idx + 2])
    except ValueError:
        pass
    if not output_path:
        # Fallback: last arg if it looks like a path (not a flag)
        last = args[-1]
        if not last.startswith("-") and "/" in last or "\\" in last or "." in last:
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


def convert_video(
    input_file: Path,
    output_file: Path,
    enc: EncoderSettings,
    log_file: Optional[Path] = None,
) -> bool:
    """Convert a single video to Steam spec."""
    args = build_ffmpeg_args(enc, str(input_file), str(output_file))
    success, _ = run_ffmpeg(args, log_file)
    return success


def concat_videos(
    playlist_file: Path,
    output_file: Path,
    enc: EncoderSettings,
    log_file: Optional[Path] = None,
) -> bool:
    """Concatenate multiple videos to Steam spec."""
    args = build_ffmpeg_args(enc, "", str(output_file), playlist=str(playlist_file))
    success, _ = run_ffmpeg(args, log_file)
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
    """Load config.json or return default."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text())
        except json.JSONDecodeError:
            pass
    return {"version": VERSION, "games": {}}


def save_config(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))


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

def tail_log(log_path: Path, lines: int = 10) -> str:
    """Return last N lines of a log file."""
    if not log_path.exists():
        return "(no log)"
    content = log_path.read_text(errors="replace")
    return "\n".join(content.splitlines()[-lines:])


# ══════════════════════════════════════════════════════════════════════
# TUI (rich)
# ══════════════════════════════════════════════════════════════════════

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich.live import Live
    from rich.prompt import Prompt, Confirm
    from rich.align import Align

    console = Console()
    RICH = True
except ImportError:
    # Fallback: plain print
    RICH = False

    class _FakeConsole:
        def print(self, *args, **kwargs):
            text = " ".join(str(a) for a in args)
            # Strip rich markup
            text = re.sub(r"\[/?\w+\]", "", text)
            text = re.sub(r"\[dim\].*?\[/\]", "", text)
            print(text)

        def input(self, prompt="", **kwargs):
            """Styled input fallback — strips markup and passes to builtin."""
            text = str(prompt)
            text = re.sub(r"\[/?\w+\]", "", text)
            text = re.sub(r"\[dim\].*?\[/\]", "", text)
            return builtins.input(text)

        def rule(self, *args, **kwargs):
            print("-" * 60)

    console = _FakeConsole()


def banner():
    """Display SteamCast banner."""
    console.print()
    console.print(
        Panel.fit(
            Align.center(
                f"[bold magenta]STEAMCAST v{VERSION}[/]\n"
                "[dim]Steam broadcast video prep & cast[/]"
            ),
            border_style="magenta",
        )
    )


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

    ready = Confirm.ask("Have you placed all video files in the input folder?")
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

    table = Table(title="Found Videos", border_style="green")
    table.add_column("File", style="white")
    table.add_column("Duration", style="dim", justify="right")
    table.add_column("Size", style="dim", justify="right")
    for f in video_files:
        dur = get_video_duration(f)
        size = format_size(f.stat().st_size)
        table.add_row(f.name, dur, size)
    console.print(table)

    # Step 3: Group by game name
    game_groups: dict[str, list[Path]] = {}
    for f in video_files:
        game_name, _, _ = parse_game_name(f.name)
        game_groups.setdefault(game_name, []).append(f)

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
        group_table.add_row(f"[{gname}]", action, exists)
    console.print(group_table)

    if not Confirm.ask("\nProceed with prep?"):
        return

    # Step 4: Process each game group
    enc = detect_encoder(console)
    success_count = 0
    fail_count = 0

    for gname in sorted(game_groups):
        files = sorted(game_groups[gname], key=lambda f: f.name)
        safe_name = sanitize_filename(gname)
        out_path = OUTPUT_DIR / f"{safe_name}.mp4"

        # Check overwrite
        if out_path.exists():
            if not Confirm.ask(f'"{gname}.mp4" already exists. Overwrite?'):
                console.print(f"[dim]Skipping {gname}[/]")
                continue

        if len(files) == 1:
            # Single file — just convert
            prep_log = LOG_DIR / f"{safe_name}_prep.log"
            console.print(f"\n[dim]Converting {gname}...[/]")
            ok = convert_video(files[0], out_path, enc, log_file=prep_log)
            if ok:
                console.print(f"[green]✓ {gname} converted successfully[/]")
                success_count += 1
            else:
                console.print(f"[red]✗ Failed to convert {gname}[/]")
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

            for f in files:
                temp_out = temp_dir / f"{f.stem}_steam.mp4"
                part_log = LOG_DIR / f"{safe_name}_part_prep.log"
                console.print(f"\n[dim]Converting {f.name}...[/]")
                ok = convert_video(f, temp_out, enc, log_file=part_log)
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
                        esc = str(cf).replace("\\", "/").replace("'", "'\\''")
                        pf.write(f"file '{esc}'\n")

                # Concat
                concat_log = LOG_DIR / f"{safe_name}_concat.log"
                console.print(f"\n[dim]Concatenating {len(converted)} files for {gname}...[/]")
                ok = concat_videos(playlist_path, out_path, enc, log_file=concat_log)

                if ok:
                    console.print(f"[green]✓ {gname} ready: {out_path}[/]")
                    success_count += 1
                else:
                    console.print(f"[red]✗ Failed to concatenate {gname}[/]")
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

        # Validate config structure — recover from corruption
        if not isinstance(cfg.get("games"), dict):
            console.print("[yellow]Config corrupted — resetting games.[/]")
            cfg["games"] = {}
            save_config(cfg)

        existing = sorted(cfg["games"].keys())

        if existing:
            console.print("[yellow]Configured games:[/]")
            for i, gname in enumerate(existing, 1):
                key = get_rtmp_key(gname)
                # Guard against non-string keys from corrupted config
                if not isinstance(key, str):
                    key = ""
                masked = f"{key[:8]}..." if key else "(no key)"
                safe_g = sanitize_filename(gname)
                vid = available_videos.get(safe_g) or available_videos.get(gname)
                video_status = "✓" if vid else "⚠ no video"
                console.print(f"  [white][{i}][/] {gname}  [{dim}{masked}[/]]  {video_status}")
        else:
            console.print("[dim]No games configured yet.[/]")

        if available_videos:
            console.print("\n[yellow]Videos in output folder:[/]")
            for stem, f in available_videos.items():
                has_key = bool(get_rtmp_key(stem))
                status = "✓ key set" if has_key else "⚠ no key"
                console.print(f"  [white]{f.name}[/] — {status}")

        # Menu
        console.print(f"\n[yellow]{'─' * 40}[/]")
        if existing:
            console.print("[white][1-{n}][/] Edit game  |  [cyan][A][/] Add new  |  [red][D][/] Delete  |  [dim][Q][/] Done".format(n=len(existing)))
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
                console.print(f"[yellow]'{gname}' already configured.[/]")

            if RICH:
                key = Prompt.ask(f"[cyan]Enter RTMP key for '{gname}'[/]", default=current_key)
            else:
                key = input(f"Enter RTMP key for '{gname}' [{current_key}]: ").strip()
            if key:
                set_rtmp_key(gname, key)
                console.print(f"[green]✓ Key saved for '{gname}'[/]")
            else:
                console.print("[yellow]No key entered — skipped.[/]")

        elif choice == "d" and existing:
            # Delete game(s)
            console.print()
            console.print("[yellow]Delete which game?[/]")
            for i, gname in enumerate(existing, 1):
                console.print(f"  [white][{i}][/] {gname}")
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
                        confirm = Confirm.ask(f"[red]Delete '{gname}' and its RTMP key?[/]")
                    else:
                        confirm = input(f"Delete '{gname}'? (y/n): ").lower().startswith("y")
                    if confirm:
                        del cfg["games"][gname]
                        save_config(cfg)
                        console.print(f"[green]✓ '{gname}' deleted.[/]")
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
                    console.print(f"\n[yellow]Editing: {gname}[/]")
                    console.print(f"  Current key: [dim]{key[:8]}...[/]" if key else "  [dim]No key set[/]")

                    # Edit name
                    if RICH:
                        new_name = Prompt.ask("[cyan]New name (leave empty to keep)[/]", default="").strip()
                    else:
                        new_name = input("New name (leave empty to keep): ").strip()
                    if new_name and new_name != gname:
                        # Rename: copy key to new name, delete old
                        cfg["games"][new_name] = cfg["games"].pop(gname)
                        save_config(cfg)
                        console.print(f"[green]✓ Renamed '{gname}' → '{new_name}'[/]")
                        gname = new_name

                    # Edit key
                    current_key = get_rtmp_key(gname)
                    if not isinstance(current_key, str):
                        current_key = ""
                    if RICH:
                        new_key = Prompt.ask(f"[cyan]New RTMP key for '{gname}'[/]", default=current_key)
                    else:
                        new_key = input(f"New RTMP key for '{gname}' [{current_key}]: ").strip()
                    if new_key != current_key:
                        set_rtmp_key(gname, new_key)
                        console.print(f"[green]✓ Key updated for '{gname}'[/]")
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
        console.print("[yellow]No games configured yet.[/]")
        if RICH:
            go = Confirm.ask("Go to setup?")
        else:
            go = input("Go to setup? (y/n): ").lower().startswith("y")
        if go:
            show_cast_setup()
            show_cast()
        return

    # Scan output
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    available_videos = {
        f.stem.lower(): f for f in OUTPUT_DIR.glob("*.mp4") if f.stat().st_size > 0
    }

    while True:
        banner()
        console.print("[bold yellow]=== CAST: Select Games ===[/]\n")

        menu_items = []
        for i, gname in enumerate(config_games, 1):
            is_active = get_game_active(gname)
            has_video = gname.lower() in available_videos
            has_key = bool(get_rtmp_key(gname))

            toggle = "[green]ON[/]" if is_active else "[dim]OFF[/]"
            if has_video:
                status = "✓ ready" if has_key else "⚠ no key"
            else:
                status = "⚠ no video"

            console.print(f"  [white][{i}][/] {gname}  [{toggle}]  {status}")
            menu_items.append({
                "index": i, "game": gname, "active": is_active,
                "has_video": has_video, "has_key": has_key,
            })

        console.print()
        console.print("[cyan][T][/] Toggle ALL")
        console.print("[cyan][A][/] Add/Edit keys (Setup)")
        console.print("[cyan][P][/] Go to Prep")
        console.print("[green][S][/] Start broadcasting[/]")
        console.print("[red][Q][/] Back to main menu[/]")

        if RICH:
            choice = Prompt.ask("\n[cyan]Enter number to toggle, or command[/]", default="").strip().lower()
        else:
            choice = input("\nEnter number to toggle, or command: ").strip().lower()

        if choice == "q":
            break
        elif choice == "t":
            any_on = any(m["active"] for m in menu_items)
            new_state = not any_on
            for m in menu_items:
                set_game_active(m["game"], new_state)
            console.print(f"[cyan]All games toggled {'ON' if new_state else 'OFF'}[/]")
        elif choice == "a":
            show_cast_setup()
            cfg = load_config()
            config_games = sorted(cfg["games"].keys())
        elif choice == "p":
            show_prep_phase()
            return
        elif choice == "s":
            to_start = [m for m in menu_items if m["active"] and m["has_video"] and m["has_key"]]
            problems = [m for m in menu_items if m["active"] and (not m["has_video"] or not m["has_key"])]

            if problems:
                console.print()
                console.print("[yellow]Some active games have issues:[/]")
                for p in problems:
                    if not p["has_video"]:
                        console.print(f"  [red]  {p['game']}: no video file (run Prep)[/]")
                    if not p["has_key"]:
                        console.print(f"  [red]  {p['game']}: no RTMP key (run Setup)[/]")
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
                        console.print(f"[yellow]'{item['game']}' has no video. Press P to go to Prep.[/]")
                    if not item["has_key"]:
                        console.print(f"[yellow]'{item['game']}' has no RTMP key. Press A to go to Setup.[/]")
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
            console.print(f"[red]Video not found for '{gname}' — skipping.[/]")
            continue

        rtmp_key = get_rtmp_key(gname)
        if not rtmp_key:
            console.print(f"[red]No RTMP key for '{gname}' — skipping.[/]")
            continue

        log_file = LOG_DIR / f"{safe_name}_cast.log"
        stream_url = f"{RTMP_INGEST}/{rtmp_key}"

        console.print(f"[dim]Starting stream for {gname}...[/]")

        cmd = [
            ffmpeg_path,
            "-re", "-y", "-stream_loop", "-1",
            "-i", str(video_path),
            "-c", "copy",
            "-f", "flv",
            stream_url,
        ]

        log_fh = open(log_file, "w")
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

        active_streams[gname] = {
            "pid": proc.pid,
            "process": proc,
            "start_time": datetime.now(),
            "log_file": log_file,
            "log_fh": log_fh,
            "rtmp_key": rtmp_key,
        }
        console.print(f"[green]✓ {gname} started (PID {proc.pid})[/]")
        time.sleep(1)  # Stagger starts

    if not active_streams:
        console.print("[red]No streams started.[/]")
        if RICH:
            console.input("[dim]Press Enter to continue...[/]")
        else:
            input("\nPress Enter to continue...")
        return

    # Monitor loop
    console.print("\n[bold red]=== 🔴 CASTING — Press Q to stop all ===[/]")

    if not RICH:
        # Plain text monitor (non-rich)
        from threading import Thread
        import select

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
            for gname in sorted(active_streams):
                stream = active_streams[gname]
                proc = stream["process"]
                elapsed = (datetime.now() - stream["start_time"]).total_seconds()
                status = "● RUNNING" if proc.poll() is None else "✗ STOPPED"
                color = "\033[32m" if proc.poll() is None else "\033[31m"
                print(f"\033[K  {color}{gname}  [{status}]  ({format_duration(elapsed)})  PID {stream['pid']}\033[0m")
            print(f"\033[{len(active_streams)}A", end="")  # Move cursor back up
            time.sleep(2)
        print("\n")
    else:
        # Rich live monitor
        running = True

        def generate_table():
            table = Table.grid(padding=(0, 2))
            for gname in sorted(active_streams):
                stream = active_streams[gname]
                proc = stream["process"]
                elapsed = (datetime.now() - stream["start_time"]).total_seconds()
                if proc.poll() is None:
                    row_style = "green"
                    icon = "[green]●[/]"
                    status = "RUNNING"
                else:
                    row_style = "red"
                    icon = "[red]✗[/]"
                    status = "STOPPED"
                table.add_row(
                    f"[bold {row_style}]{gname}[/]",
                    f"[{row_style}]{icon} {status}[/]",
                    f"[dim]({format_duration(elapsed)})[/]",
                    f"[dim]PID {stream['pid']}[/]",
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
            console.print(f"[dim]Stopping {gname} (PID {proc.pid})...[/]")
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
        console.print(f"[green]✓ {gname} stopped[/]")

    console.print("\n[green]✓ All streams stopped.[/]")
    if RICH:
        console.input("[dim]Press Enter to continue...[/]")
    else:
        input("\nPress Enter to continue...")


def version_check():
    """Check GitHub for newer version (non-blocking)."""
    try:
        req = urllib.request.Request(VERSION_CHECK_URL)
        with urllib.request.urlopen(req, timeout=5) as resp:
            remote = resp.read().decode().strip()
        if remote and remote != VERSION:
            console.print()
            console.print(f"[yellow]⚠ A newer version of SteamCast ({remote}) is available![/]")
            console.print("[yellow]  Download: https://github.com/underagum/steamcast/releases[/]")
            console.print()
    except Exception:
        pass


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

def main():
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()

        if cmd == "prep":
            version_check()
            show_prep_phase()
        elif cmd == "setup":
            version_check()
            show_cast_setup()
        elif cmd == "cast":
            version_check()
            show_cast()
        else:
            show_main_menu()
    else:
        show_main_menu()


if __name__ == "__main__":
    main()
