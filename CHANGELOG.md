# Changelog

## v1.2.0 — 2026-07-02

### Added

- **Broadcast scheduling.** `[SCH]` in the CAST menu lets you set a delayed start ("in X minutes") and an automatic stop ("run for X hours"). Shows calculated start/end times with a confirmation prompt. Countdown timer before launch. Monitor loop auto-stops at the scheduled end time (or when you press Enter).

### Fixed

- **Silent video PREP failure.** Videos with no audio track (e.g. game footage with music stripped) no longer fail with "matches no streams." `has_audio_stream()` probes the input via `ffmpeg -i` before encoding and conditionally omits `-c:a aac -b:a 128k -ar 44100`. Safe fallback: assumes audio exists if the probe fails.
- **Multi-GPU nvidia-smi.** `get_gpu_stats()` now processes all GPU rows from `nvidia-smi` and picks the GPU with the highest encoder utilization — so the dashboard shows stats from the GPU actually doing NVENC work. Falls back to first GPU if none have encoder activity.
- **Orphaned ffmpeg on crash.** `atexit` handler registered in `setup_crash_logging()` kills all tracked ffmpeg PIDs on abnormal exit. PIDs are tracked in `_ACTIVE_FFMPEG_PIDS` when streams launch and cleared on graceful shutdown. No more Task Manager cleanup needed.

### Changed

- **Wording:** "game trailers" → "videos" in docstring and main menu subtitle. SteamCast broadcasts any video, not just trailers.
- **`build_ffmpeg_args`**, **`convert_video`**, **`concat_videos`** all accept `has_audio: bool = True`.

## v1.1.6 — 2026-07-02

### Fixed

- **Active state persistence.** `"active"` in config.json was being force-reset to `false` for every streamed game when a broadcast ended (`run_cast_stream` cleanup loop called `set_game_active(gname, False)`). This meant the user's toggle choices in the CAST menu never survived past one broadcast. The cleanup now leaves `active` state alone — toggle once, stays toggled until you change it.

## v1.1.5 — 2026-07-02

### Added

- **GPU monitoring in CAST dashboard.** When using a hardware encoder (NVENC/QSV/AMF), the live broadcast monitor now shows GPU utilisation, NVENC encoder load, and VRAM usage. Uses `nvidia-smi` for NVIDIA — Intel QSV and AMD AMF return no GPU row for now (probe infrastructure is ready). Gracefully degrades if `nvidia-smi` is not on PATH.
- **`EncoderSettings.is_hardware`** — dataclass field so downstream code can check whether the active encoder is GPU-accelerated without string-parsing the codec name.

## v1.1.4 — 2026-07-01

### Changed

- **FFmpeg version pinned to 8.0.1.** gyan.dev's generic "latest" URL now serves FFmpeg 8.1.2 (released 2026-06-27), which requires NVENC API version not yet available in driver 610.x. Pinned to 8.0.1 — the last release known to work with driver 610.x. 

### Fixed

- **NVENC validation overhaul.** Test encode failures no longer hardcode "driver too old" as the cause. Actual ffmpeg error output is printed in full, with better error pattern matching. The probed ffmpeg binary path is shown so users can copy-paste the command themselves.

## v1.1.3 — 2026-07-01

### Added

- **Rich color on Python 3.14+ / Windows.** Auto-detection for Rich's terminal color system fails on newer Python versions even when ANSI is fully available. SteamCast now passes `force_terminal=True, color_system="truecolor"` on Windows, and retries with `force_terminal=True` on Linux/macOS if auto-detection returned no color system.

### Fixed

- **`rich_escape` applied to game names in Rich table.** Game names containing `[` (e.g. `[DreadOut 3]`) were being silently stripped by Rich's markup parser. Now escaped at render time in the CAST menu and dashboard.

## v1.1.2 — 2026-07-01

### Fixed

- **NVENC validation false negatives.** Test encode now uses a 256×256 black frame instead of 32×32 (below NVENC's 145×145 minimum). Previously every validation failure was reported as "driver too old" — now actual ffmpeg error output is shown.

## v1.1.1 — 2026-06-30

### Fixed

- **Frozen `.exe` root-path resolution.** `Path(__file__)` inside PyInstaller temp directory → now uses `sys.executable` when frozen to locate `ffmpeg/`, `input/`, `output/`.
- **FFmpeg download URL.** Missing `/packages/` path segment in the pinned 8.0.1 URL caused 404.

## v1.1.0 — 2026-06-30

Initial public beta. Two-phase tool: PREP (convert + concat videos to Steam RTMP spec) and CAST (RTMP key management, multi-game stream toggle, broadcast monitor). Hardware encoder detection (NVENC > QSV > AMF > libx264). Portable FFmpeg auto-download. Local-only config. RTMP key redaction in logs.