# Changelog

## v1.5.1 — 2026-07-16

### Fixed

- **`steamcast update` compatibility on modern Linux.** Passes `--break-system-packages` to `pip3` for PEP 668 compliance (Ubuntu 24.04+, Debian 12+).
- **CLI consistency.** `steamcast daemon attach` replaces `steamcast attach` — all daemon commands now share the `daemon` prefix. Old `steamcast attach` kept as backward-compat alias.
- **Resolved UI flash on keypress.** Empty `Prompt.ask()` submissions are now silently skipped in both main and daemon menus.
- **Daemon status no longer freezes the TUI.** HTTP timeout reduced to 1 second, status is cached for 3 seconds in main menu loop.
- **Zombie processes eliminated.** Daemon double-fork now properly reaps intermediate child processes (`os.waitpid`). Zero zombies per start/stop cycle.
- **Thread safety.** Added `threading.Lock` around all `_active_streams` dict access for HTTP handler and monitor loop.

### Changed

- **No more venv.** Install script and launcher use system Python directly. `pip3` installs deps globally.
- **Attach no longer crashes the TUI.** `sys.exit(1)` replaced with controlled `return` — failed attach returns to daemon menu instead of killing the session.
- **Bitrate parsing robust.** Now handles `5000K`, `5M`, `7.5M`, and bare `5000` formats.
- **Menu navigation consistency.** Daemon Manager uses `[Q]` to go back (matches all other menus).

## v1.5.0 — 2026-07-16

### Added

- **Daemon Mode (Linux).** Headless background streaming daemon for 24/7 broadcasts.
  - `steamcast daemon start|stop|status|attach` commands
  - HTTP API on `127.0.0.1:6789` — `/status`, `/logs`, `/shutdown`
  - Auto-restart at configurable intervals (default: 4 hours)
  - Optional duration limit — stops all streams after N hours
  - Auto-detects games from TUI config & videos from `output/`
  - Graceful signal handling (SIGTERM/SIGINT) with stream cleanup
- **TUI Integration.** Daemon status banner on main menu (live, cached 3s). `[4] Daemon Manager` submenu with start/stop/restart/attach.
- **One-line installer.** `curl -fsSL https://raw.githubusercontent.com/underagum/steamcast/main/install.sh | bash`
- **`steamcast update` command.** `git pull` + `pip3 install -r requirements.txt`
- **`requirements.txt`.** Single source for Python dependencies (`rich>=13.0`, `psutil>=5.9`)
- **`steamcast attach`.** Read-only live TUI dashboard polling the daemon's HTTP API.
- **`Support Me!` section** in README with Digital Happiness game links.

### Changed

- CLI shortcuts table updated in README with daemon commands

## v1.4.0 — 2026-07-05

### Added

- **Auto-restart every N hours.** New `restart_every_hours` parameter on `run_cast_stream()`. Kills all ffmpeg streams every N hours — the auto-reconnect loop respawns them cleanly. Default 4 hours in the interactive prompt ([S] and [SCH] menus both ask). Banner shows "♻ auto-restarts every 4h" on cast start. Works in both Rich and plain-text monitor loops. Set to 0 to disable.

## v1.3.0 — 2026-07-03

### Added

- **Per-stream RAM monitoring.** Each live stream row now shows the ffmpeg process's RSS (resident memory in MB) alongside CPU% and bitrate. Footer shows total stream count + aggregate ffmpeg memory instead of system-wide RAM% and network TX.
- **Ctrl+C everywhere.** PREP cancels at any encoding step (single-file convert, multi-file part convert, concat) — kills ffmpeg, deletes partial output, cleans temp directories. Schedule countdown cancels back to CAST menu. No orphaned processes.
- **Duration formatting with minutes.** Schedule confirmation and countdown now show `"2h 30m"` instead of dropping the fractional hour. Multi-day displays as `"6d 2h 30m"`.

### Changed

- **Schedule input: absolute datetime.** Instead of "start in X minutes, run for X hours," the SCH prompt now takes `YYYYMMDD HH:MM` for both start and end. Zero mental math.
- **Bitrate lowered to 5000k CBR.** Was 7000k, which could hit Steam's ingest soft cap (~7000k + AAC overhead) and cause stream rejection. 5000k matches known-working OBS configs.
- **Bitrate display notation.** Dashboard shows `"7.1Mbps"` / `"1498kbps"` instead of `"7.1M"` / `"1498K"`. Unambiguous.

### Removed

- **GPU monitoring from CAST dashboard.** `-c copy` streams don't encode — GPU utilization was misleading noise. Removed `get_gpu_stats()`, `_NVIDIA_SMI` global, and GPU rows from both plain-text and Rich dashboards. `detect_encoder()` remains for PREP.
- **System-wide RAM/TX footer.** Replaced by per-process RAM on each stream row + aggregate footer. Removed `get_system_ram_tx()`, `_net_baseline`, `_net_baseline_time`.

### Fixed

- **Multi-part PREP: `has_audio` not passed to part conversions.** Videos with no audio track would fail AAC encoding on individual parts in the multi-file path. Now probes once and applies to all parts.
- **Reconnect messages interleaving with dashboard.** `_attempt_reconnect` no longer calls `console.print` directly — uses the `reconnect_msg` mechanism handled by the monitor loop.
- **`repair_config` / `save_config` console dependency.** Both functions are now safe to call before the Rich TUI initializes (console reference guarded with `NameError` fallback to stderr).

## v1.2.2 — 2026-07-02

### Fixed

- **RTMP multi-stream visibility.** Multiple simultaneous broadcasts to Steam's RTMP ingest sometimes only showed one stream on the store page, even though `steamapp` confirmed all were live. Root cause: ffmpeg's RTMP library can merge the stream key into `tcUrl` (the RTMP connect URL), making Steam's ingest see each stream connecting to a different "application" — `app/steam_aaaa`, `app/steam_bbbb` — instead of all connecting to `app`. The CDN routing layer then only served streams from the "primary" application. **Fix:** use explicit `-rtmp_app app -rtmp_playpath <key>` instead of embedding the key in the URL. This matches what OBS does on the wire — server URL and stream key as separate RTMP parameters — and ensures every ffmpeg process sends identical `tcUrl` and `app` values. Confirmed working with 6 simultaneous store page broadcasts.

- **Schedule confirmation display.** Dayless `strftime('%H:%M')` hid date changes — a 120-hour broadcast showed identical start and end times (`21:42 → 21:42`). Now shows `03 Jul 21:42` when dates differ from start or today.

### Added

- **Auto-reconnect on stream failure.** Dead ffmpeg processes are restarted automatically with retry count and 10-second cooldown. Unlimited retries by default (`MAX_RECONNECT_RETRIES=0`). Dashboard shows dead-stream state: `DEAD · retrying (3/∞)`, countdown timer, or reconnect flash message on success. Old PIDs are cleaned from atexit guard, logs are appended so diagnostics survive reconnects.

- **Pre-flight codec validation.** Before spawning `-c copy`, each video is probed for FLV-compatible codecs. Rejects HEVC, AV1, VP9, non-AAC/MP3 audio. Warns at spawn time but doesn't block. CAST menu now shows four states: `✓ ready`, `⚠ no key`, `⚠ no video`, `⚠ bad codec`. Games with bad codecs are excluded from broadcast launch.

- **ffmpeg error tail in dashboard.** When a stream dies, the dashboard shows the last diagnostic line from the stream's log file — connection refused, broken pipe, timeout, etc. — instead of just "✗ DEAD." Scans last 4KB of log for recognizable error keywords. Both Rich and plain-text dashboard supported.

- **`venv/` in .gitignore.** Linux development with virtual environments no longer risks committing thousands of site-package files.

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