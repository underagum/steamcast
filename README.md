# SteamCast

> Put your game videos on your Steam store pages. 24/7. No OBS, no server, no fuss.

SteamCast is a lightweight broadcasting tool for game developers and publishers who want looping video on their Steam store during sales, festivals, or just because it looks good. **Download the `.exe`, drop in your videos, paste your RTMP keys, and you're live.** Near-zero CPU overhead because it uses `-c copy`. No re-encoding at broadcast time.

---

## What It Does

Two simple phases:

| Phase | What happens |
|---|---|
| **PREP** | Converts your videos to Steam's broadcast spec (H.264, AAC, 1080p30, 5 Mbps CBR). Merges multi-part videos per game. One clean `.mp4` per title, ready to go. |
| **CAST** | Pick which games to broadcast, toggle them on/off, and start streaming. Either right now or on a schedule. A live dashboard shows per-stream CPU%, memory (RSS), and real-time bitrate. |

---

## Quick Start

### 1. Get the tool

**[Download steamcast.exe](https://github.com/underagum/steamcast/releases/latest)** from Releases. Put it in its own folder. Double-click.

No Python. No pip. No setup. First launch auto-downloads FFmpeg (~55 MB, one-time).

*Prefer running from source?* `git clone`, `pip install rich psutil`, `python steamcast.py`.

### 2. Drop in your videos

Copy files into the `input/` folder. Name them by game:

```
input/
├── dreadout 3.mp4          ← one video for this game
├── dreadout 3_1.mkv        ← part 1 of a multi-part video
├── dreadout 3_2.mp4        ← part 2 (gets concatenated)
└── graveless.mp4           ← another game
```

> Files ending with `_1`, `_2` etc. are treated as parts of the same game and merged during PREP.

### 3. Prep your videos

From the menu, pick **PREP** (or run `python steamcast.py prep`). SteamCast detects your best encoder, converts everything to Steam spec, and writes ready-to-stream `.mp4` files to `output/`.

### 4. Add RTMP keys

Pick **Setup** (or `python steamcast.py setup`). Paste the RTMP key from [Steamworks](https://partner.steamgames.com) for each game. Keys stay in `config.json` on your machine. Nothing leaves.

> **Heads up:** Make sure the Steam account that owns each RTMP key is whitelisted in Steamworks → Store Page → Broadcast Settings. If the whitelist isn't published, Steam will boot the stream after a couple minutes.

### 5. Go live

Pick **CAST** (or `python steamcast.py cast`). Toggle games on/off, then hit **S** to start now or **SCH** to schedule (e.g., `20260715 09:00` to `20260715 18:00`). Ctrl+C cancels anytime.

---

## The Dashboard

While broadcasting, each game row shows health status and live stats:

```
DreadOut 2          ● RUNNING   (01:23:45)   PID 18492   OK     CPU 12%  RAM 145MB  7.0Mbps
DreadOut Remaster   ● RUNNING   (01:23:44)   PID 18501   SLOW   CPU 8%   RAM 132MB  6.8Mbps

5 streams · 720 MB total ffmpeg memory
⚠ Upload bandwidth saturated — 2 stream(s) behind real-time (slowest: 0.84x)
```

| Column | What it means |
|---|---|
| **Health** | `OK` (green), `SLOW` (yellow, minor congestion), `CONG` (orange, backing up), `CRIT`/`LAG` (red, falling behind). Detected from ffmpeg's `speed=` and lag metrics — same principle as OBS dropped frames. |
| **CPU%** | That ffmpeg process's CPU usage |
| **RAM** | Resident memory (RSS) in MB |
| **Bitrate** | Data rate going to Steam, read from ffmpeg output |

CPU turns yellow at 50%, red at 85%. A bandwidth warning appears in the footer whenever any stream drops below 0.98x real-time speed.

---

## GPU Encoding (PREP only)

During PREP, SteamCast picks the best hardware encoder available. CAST uses `-c copy`. No encoding happens at broadcast time, so this only matters for the one-time conversion step.

| Priority | Encoder | What you need |
|---|---|---|
| 1 | **NVIDIA NVENC** | NVIDIA GPU + driver ≥ 610.00 |
| 2 | **Intel QSV** | Intel GPU with Quick Sync |
| 3 | **AMD AMF** | AMD GPU |
| 4 | **CPU fallback** (`libx264`) | Works everywhere, just slower |

NVENC gets a 1-frame test encode to verify the driver actually works. If your driver's too old, SteamCast tells you exactly what's wrong and asks before falling back to CPU.

---

## How It Starts Up

Every time SteamCast starts, it checks a few things in the background:

- **FFmpeg:** found in the bundled `ffmpeg/` folder, on your system PATH, or auto-downloaded from gyan.dev
- **Encoder:** probed and validated. NVENC gets a test encode to catch driver issues
- **Config:** checked for corruption on every load. Broken entries are silently removed
- **Version:** quick ping to GitHub. If there's a newer release, you get a notification
- **Crashes:** unhandled errors are caught and written to `logs/steamcast_crash.log` with the full traceback

If something breaks, SteamCast tells you. Otherwise, none of this is visible.

---

## CLI Shortcuts

| What | `.exe` | Python |
|---|---|---|
| Main menu | Double-click | `python steamcast.py` |
| Jump to PREP | `steamcast.exe prep` | `python steamcast.py prep` |
| Jump to SETUP | `steamcast.exe setup` | `python steamcast.py setup` |
| Jump to CAST | `steamcast.exe cast` | `python steamcast.py cast` |

---

## Steam Broadcast Spec Reference

| Parameter | Steam wants | SteamCast delivers |
|---|---|---|
| Video codec | H.264 High | H.264 High |
| Resolution | 1920×1080 | 1920×1080 |
| Frame rate | 30 or 60 FPS | 30 FPS |
| Bitrate | 7000 kbps CBR | 5000 kbps CBR |
| Keyframe interval | 2 seconds | 2 seconds |
| Pixel format | yuv420p | yuv420p |
| Audio codec | AAC-LC | AAC-LC |
| Audio bitrate | 128 kbps max | 128 kbps |
| Audio sample rate | 44100 Hz | 44100 Hz |

---

## Logging

Every ffmpeg session writes to `logs/`:

| Log | When |
|---|---|
| `{Game}_prep.log` | Single-file conversion |
| `{Game}_part_prep.log` | Multi-part individual conversions |
| `{Game}_concat.log` | Concatenation step |
| `{Game}_cast.log` | Live stream (RTMP key is redacted on stop) |
| `steamcast_crash.log` | Unhandled exceptions |

On failure, the last 10 lines are printed immediately. Full logs are saved for debugging.

---

## Folder Layout

```
steamcast/
├── steamcast.py
├── config.json           ← your RTMP keys (local only, gitignored)
├── input/                ← drop videos here
├── output/               ← processed .mp4s appear here
├── ffmpeg/               ← auto-downloaded portable FFmpeg
├── logs/                 ← conversion + stream logs
└── build/                ← PyInstaller spec + Windows build script
```

All of this is created on first run.

---

## FAQ

**Do I need Python?**
Not if you use the `.exe`. It bundles Python, Rich, and psutil into a single file. FFmpeg auto-downloads on first run.

**Can I broadcast multiple games at once?**
Yes. Each game gets its own ffmpeg process. Toggle them in the CAST menu.

**My streams show SLOW/CONG/CRIT in the dashboard. What's wrong?**
Your upload bandwidth is saturated. The dashboard detects this from ffmpeg's `speed=` metric — same principle as OBS dropped frames. SteamCast shows per-stream health (green/yellow/orange/red) and a footer warning when any stream falls behind. Fix: run fewer concurrent streams, or lower the PREP bitrate. 8 streams at 5 Mbps = 40 Mbps continuous. Most home connections can't sustain that.

**Do I have to re-toggle games every time?**
No. Your ON/OFF choices persist in `config.json` across sessions. Use `[T]` Toggle ALL to flip everything at once.

**Can I schedule a broadcast?**
Yes. Type `SCH` in the CAST menu, give it a start and end datetime (`YYYYMMDD HH:MM`), and SteamCast handles the countdown and auto-stop. Ctrl+C cancels.

**What if SteamCast crashes mid-broadcast?**
An `atexit` handler cleans up orphaned ffmpeg processes. If that somehow fails, kill remaining `ffmpeg.exe` processes in Task Manager.

**Do you send my RTMP keys anywhere?**
No telemetry. No cloud. Everything stays in `config.json` on your machine. Keys are redacted from log files after each session.

**What if conversion fails?**
The last 10 lines of the ffmpeg log are shown. The full log is in `logs/`.

**My GPU doesn't support NVENC.**
SteamCast probes NVENC → QSV → AMF → CPU fallback. If your NVENC driver is too old, it tells you exactly what's wrong and asks before switching to software encoding.

**Does it check for updates?**
At startup it pings GitHub. If there's a newer version, you'll see a notification. Fails silently if offline.

---

## Privacy

- No telemetry, no analytics, no cloud
- RTMP keys live in `config.json` and never leave your machine
- Keys are redacted from stream logs
- The only network activity is the FFmpeg download (one-time), the version check (optional, silent), and the RTMP streams you explicitly start

---

## License

MIT. Free to use, modify, and share.
