# Changelog

## v1.1.4 — 2026-07-01

### Changed

- **FFmpeg version pinned to 8.0.1.** gyan.dev's generic "latest" URL now serves FFmpeg 8.1.2 (released 2026-06-27), which requires NVENC API version not yet available in driver 610.x. Pinned to 8.0.1 — the last release known to work with driver 610.x. 

### Fixed

- **NVENC validation overhaul.** Test encode failures no longer hardcode "driver too old" as the cause. Actual ffmpeg error output is printed in full, with better error pattern matching. The probed ffmpeg binary path is shown so users can copy-paste the command themselves.

## v1.1.3 — 2026-07-01

### Added

- **Prerequisites check.** SteamCast now validates Python ≥3.9, Rich, and psutil *before* rendering anything. Prints clear `[ERROR]` messages and `pip install` instructions on failure — no more silent fallback to plain-text when libraries are missing.

### Changed

- **Version check is now non-blocking.** GitHub HTTP request runs in a daemon thread instead of blocking the main menu for up to 5 seconds on slow/no connections. Menu appears instantly.
- **Rich auto-detection bypassed on Windows.** Python 3.14+ changes stdout behaviour in ways that trip Rich's terminal colour detection. Windows 10+ consoles all support ANSI truecolour — SteamCast now skips detection and enables it directly.

## v1.1.2 — 2026-07-01

### Fixed

- **Game name display.** Rich's markup parser silently ate lowercase/mixed-case bracket-wrapped names like `[dreadout 3]` and `[test]`. Now uses `rich_escape()` on all game names.
- **NVENC false negative.** `_validate_encoder` was passing `-preset p7 -rc cbr -b:v 100k` to the 1-frame test encode — an extreme combination that ffmpeg's NVENC wrapper can reject on perfectly fine driver installations. Stripped down to `-c:v h264_nvenc` only.

## v1.1.1 — 2026-07-01

### Fixed

- **Frozen `.exe` root-path resolution.** `__file__` resolves to a temp directory inside PyInstaller's runtime extraction, so `ffmpeg/`, `input/`, `output/`, and `config.json` were all looked up from the wrong location. Now uses `sys.executable` for frozen builds — root directory is correctly the folder where `steamcast.exe` lives.
- **Build audit (4 issues).** `build.bat` move destination no longer creates a nested `build\build\` path. Comment corrected to `build/` directory. `dist/` cleaned up after move. `.gitignore` covers `build/dist/`.

## v1.1.0 — 2026-07-01

### New Features

- **Live FFmpeg progress during PREP.** Real-time encode progress line shows frame count, fps, bitrate, and speed as ffmpeg converts or concatenates videos. No more staring at a silent terminal.
- **Live system monitoring in CAST dashboard.** Per-stream CPU% (psutil per-PID), real-time bitrate from ffmpeg stderr logs, system RAM usage, and total network TX rate. All update every 0.5s in the broadcast monitor. Requires `psutil` (gracefully skipped if not installed).
- **Per-stream CPU & bitrate display.** Each game row in the cast dashboard shows its own numbers — see exactly which encoder is working hard and whether bitrate is on target.
- **Progress bars for FFmpeg download and extraction.** Rich-powered download (size, speed, ETA) and extraction (file count) bars with `transient=True` — disappear cleanly on completion.
- **NVENC driver validation.** SteamCast runs a 1-frame test encode before accepting NVENC. If the driver is too old (≥610.00 required for FFmpeg 8.x), it shows a clear diagnostic and **asks** whether to fall back to CPU encoding instead of silently failing after a full encode attempt.
- **GitHub Actions CI.** Every push to `main` auto-builds `steamcast.exe` on a Windows runner and uploads it as an artifact. The `.exe` bundles Python + rich + psutil; FFmpeg auto-downloads on first run.
- **Windows build script.** `build/build.bat` — double-click to produce `build/dist/steamcast.exe` on any Windows machine with Python 3.11+.

### Changes

- FFmpeg moved to top of Requirements section in README; internet connection listed as a sub-note under it.
- `builds/` folder renamed to `build/`.
- `build.bat` fixed: paths now correctly relative to the `build/` directory.

### Bug Fixes

- **H1:** NVENC validation test encode now includes `-b:v 100k` — prevents false validation failure when NVENC CBR requires an explicit bitrate.
- **H2:** Failed FFmpeg downloads now clean up partial/corrupt zip files.
- **H3:** Failed extractions now clean up lingering zip files.
- **M4:** Bitrate reader replaced offset tracking with tail-read (last 8 KB) — eliminates TOCTOU race where ffmpeg writes a progress line between seek and read.
- **M5:** Removed `_read_offsets` side-effect from `active_streams` dict (tail-read needs no persistent state).
- **M6:** `show_cast()` "no games configured" path: recursion replaced with `while` loop.
- **M7:** `.format()` → f-string in `show_cast_setup` menu prompt.
- **L8:** Dead `gname` fallback removed from `available_videos` lookup.
- **L9:** `available_videos` dict keys use raw `f.stem` (not lowered) — matches Prep output naming exactly, avoids case-insensitive dedup.

---

## v1.0.0-beta — 2026-06-29

Initial public beta. Two-phase tool: PREP (convert + concat videos to Steam RTMP spec) and CAST (RTMP key management, multi-game stream toggle, broadcast monitor). Hardware encoder detection (NVENC > QSV > AMF > libx264). Portable FFmpeg auto-download. Local-only config. RTMP key redaction in logs.
