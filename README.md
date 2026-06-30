# SteamCast v1.1.0

> Prepare and broadcast multiple videos to Steam store pages — no OBS, no server setup.

SteamCast is a **Python** tool for game developers and publishers who want to run 24/7 videos on their Steam store pages during sales or events. It replaces OBS with lightweight FFmpeg streaming — near-zero CPU when using hardware encoding.

---

## What It Does

Two phases:

| Phase | What |
|-------|------|
| **PREP** | Convert your video files to Steam's broadcast spec (H.264, AAC, 1080p30, 7 Mbps CBR, 44100Hz) and concatenate multi-part videos by game. One `.mp4` per game, ready to stream. Progress bars for download and extraction. |
| **CAST** | Set up RTMP keys, toggle which games to broadcast, and start/stop streams — all from one terminal window. **Live per-stream dashboard** with per-game CPU%, real-time bitrate from FFmpeg output, system RAM, and total network TX rate. |

---

## Requirements

- **FFmpeg** — the only hard requirement. SteamCast auto-downloads a portable build (~55 MB) from gyan.dev on first run if not already installed. No manual setup needed — just let the progress bar finish.
  > This requires an **internet connection** on first launch. After the download, SteamCast runs entirely offline.
- **Python 3.9+** (3.11 recommended)
- **Windows 10/11 (64-bit)** — primary target. Linux/macOS work but are secondary.
- **`rich`** — `pip install rich` (colored TUI; graceful plain-text fallback)
- **`psutil`** — `pip install psutil` (live system monitoring in CAST dashboard; gracefully skipped if missing)
- **GPU (optional but recommended):**
  - **NVIDIA NVENC** — requires **driver ≥ 610.00** (FFmpeg 8.x drops `<610.00` support). SteamCast validates your driver before accepting NVENC — see [GPU Support](#gpu-support) below.
  - **Intel QSV** — any Intel GPU with Quick Sync
  - **AMD AMF** — any modern AMD GPU
  - CPU fallback (`libx264`) if no compatible GPU is found
- **Steam broadcast key** from [Steamworks](https://partner.steamgames.com) for each game

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/underagum/steamcast.git
cd steamcast
pip install rich psutil
```

### 2. Prepare videos

```bash
python steamcast.py prep
```

1. Copy your video files into the `input/` folder
2. Name them like this:

```
input/
├── dreadout 3.mp4          ← single video for this game
├── dreadout 3_1.mkv        ← part 1 (multiple videos for same game)
├── dreadout 3_2.mp4        ← part 2
└── graveless.mp4          ← another game
```

> The tool parses game names from your filenames. `_1`, `_2` etc. mean "multiple files to concatenate". Everything before `_NUMBER` is the game name.

3. Follow the prompts — the tool probes your GPU, **validates the driver**, and picks the best encoder
4. Output goes to `output/` — one `.mp4` per game

### 3. Set up RTMP keys

```bash
python steamcast.py setup
```

Add game names and paste RTMP keys from Steamworks. Keys are stored locally in `config.json`. **No data leaves your machine.**

### 4. Cast

```bash
python steamcast.py cast
```

- Toggle games ON/OFF by entering their number
- Press `T` to toggle all
- Press `S` to start broadcasting selected games
- While casting: **live per-stream CPU%, bitrate, system RAM, and network TX** refresh every 0.5s
- Press Enter to stop all streams

### Or use the main menu

```bash
python steamcast.py
```

---

## Live Cast Dashboard

While broadcasting, each game row shows its own real-time stats:

```
DreadOut 2          ● RUNNING   (01:23:45)   PID 18492   CPU 12%   7.0M
DreadOut Remaster   ● RUNNING   (01:23:44)   PID 18501   CPU 8%    6.8M

RAM: 58%   TX: 13.2 MB/s
```

| Column | Source | Meaning |
|--------|--------|---------|
| **CPU%** | `psutil` per-PID | That specific FFmpeg child's CPU usage |
| **Bitrate** (7.0M) | FFmpeg stderr `bitrate=...kbits/s` | Actual encoding output rate being pushed to RTMP |
| **RAM** | System-wide `psutil` | Total memory pressure |
| **TX** | System-wide NET I/O delta | Total network send rate (all streams combined) |

CPU and bitrate turn yellow at 50% (or above target), red at 85%. Requires `psutil`. Falls back gracefully to a clean stream-only display if `psutil` is not installed.

---

## GPU Support (Updated)

SteamCast automatically detects available hardware encoders in priority order — and **validates driver compatibility** before using NVENC:

| Priority | Encoder | Required | Notes |
|----------|---------|----------|-------|
| 1 | **NVIDIA NVENC** (`h264_nvenc`) | NVIDIA GPU + **driver ≥ 610.00** | Preset p7, CBR. Validated with a 1-frame test encode. If driver is too old, SteamCast shows a clear diagnostic and asks whether to fall back to CPU. |
| 2 | **Intel QSV** (`h264_qsv`) | Intel GPU with Quick Sync | Preset veryfast |
| 3 | **AMD AMF** (`h264_amf`) | AMD GPU | Only in FFmpeg FULL build; Preset quality, CBR |
| 4 | **Software fallback** (`libx264`) | CPU only | Slower encode, same quality |

### NVENC Driver Warning

If your NVIDIA driver is older than 610.00, you'll see:

```
NVIDIA NVENC found but driver is too old.
  Driver does not support the required nvenc API version. Required: 13.1 Found: 13.0
Fix: install NVIDIA driver ≥ 610.00 from https://www.nvidia.com/download/

Fall back to CPU encoding (libx264)? [Y/n]
```

- **Y** — proceeds with libx264 (slower but works)
- **n** — aborts Prep so you can update drivers and retry

**Fix:** Download the latest driver from [nvidia.com/download](https://www.nvidia.com/download/) and restart SteamCast.

---

## Folder Structure

```
steamcast/
├── steamcast.py          ← Main script (cross-platform)
├── config.json           ← Your RTMP keys (local only, gitignored)
├── README.md
├── version.txt
├── requirements.txt      ← rich, psutil
├── .github/workflows/    ← GitHub Actions CI (auto-builds Windows .exe)
├── build/
│   ├── build.bat          ← Windows build script (double-click to build .exe)
│   └── steamcast.spec     ← PyInstaller spec for standalone .exe
├── input/                ← Drop video files here (gitignored)
├── output/               ← Processed .mp4s appear here (gitignored)
├── ffmpeg/               ← Auto-downloaded portable FFmpeg (gitignored)
└── logs/                 ← Conversion + stream logs (gitignored)
    ├── BSE_prep.log
    ├── BSE_cast.log
    └── ...
```

---

## CLI Usage

```bash
python steamcast.py prep       # Jump straight to Prep
python steamcast.py setup      # Jump straight to key setup
python steamcast.py cast       # Jump straight to stream toggle
python steamcast.py            # Show main menu (default)
```

---

## Steam Broadcast Spec

| Parameter | Steam Requirement | SteamCast Setting |
|-----------|-------------------|-------------------|
| Video codec | H.264 | H.264 (NVENC, QSV, AMF, or libx264) |
| Profile | High | High |
| Level | 4.1 | 4.1 |
| Resolution | 1920×1080 | 1920×1080 |
| Frame rate | 30 or 60 FPS | 30 FPS |
| Bitrate | 7000 kbps CBR | 7000 kbps CBR (`-b:v -maxrate -bufsize` all equal; `-rc cbr` for NVENC/AMF) |
| Keyframe interval | 2 seconds | 2 seconds (60 frames @30fps) |
| Pixel format | yuv420p | yuv420p |
| Audio codec | AAC-LC | AAC-LC |
| Audio bitrate | 128 kbps max | 128 kbps |
| Audio sample rate | 44100 Hz | 44100 Hz |

---

## Logging

Every FFmpeg run writes a log to `logs/`:

| Log | When |
|-----|------|
| `{Game}_prep.log` | Single-file conversion |
| `{Game}_part_prep.log` | Multi-part individual conversions |
| `{Game}_concat.log` | Concatenation step |
| `{Game}_cast.log` | Live stream output (RTMP key redacted on stop) |

On failure, the last 10 lines are shown immediately. Full logs are preserved for debugging.

---

## Privacy & Security

- **No telemetry.** SteamCast does not collect, send, or report any usage data.
- **No cloud.** All configuration (RTMP keys, game settings) stays in `config.json` on your machine.
- **No network activity** except:
  - One-time FFmpeg download on first run (gyan.dev)
  - Optional version check at startup (GitHub, silently fails if offline)
  - The actual RTMP stream you explicitly start
- **RTMP keys are redacted** from stream log files after each session.
- **config.json is gitignored** — never accidentally committed.

---

## FAQ

**Q: Can I broadcast multiple games at once?**
Yes — each game gets its own FFmpeg process. Toggle them in the CAST menu.

**Q: Does it show per-game resource usage?**
Yes. The cast dashboard shows per-stream CPU%, bitrate, system RAM, and total network TX. Requires `psutil` (`pip install psutil`).

**Q: What if I close the terminal while casting?**
FFmpeg processes will be orphaned. On Windows, use Task Manager to kill remaining `ffmpeg.exe` processes. (Process group management via the standalone `.exe` is planned.)

**Q: Do you send my RTMP keys anywhere?**
No. Everything stays in `config.json` on your machine. Keys are redacted from logs.

**Q: What if the conversion fails?**
The last 10 lines of the FFmpeg log are shown immediately. The full log is saved in `logs/`.

**Q: My GPU doesn't support NVENC. Will it still work?**
Yes. SteamCast probes NVIDIA NVENC, Intel QSV, and AMD AMF in that order. If none are found — or the NVENC driver is too old — it asks before falling back to `libx264` software encoding.

**Q: My NVENC driver is old. What do I do?**
SteamCast now validates NVENC with a 1-frame test encode before accepting it. If your driver is too old (pre-610.00), it shows a clear message and asks whether to fall back to CPU encoding. To fix permanently: update your NVIDIA driver from [nvidia.com/download](https://www.nvidia.com/download/).

**Q: Does it check for updates?**
On startup, SteamCast performs a quick version check against GitHub. If a newer version is available, it shows a notification. The check times out silently after 5 seconds if you're offline.

**Q: Is there a standalone .exe?**
Yes — every push to `main` triggers a GitHub Actions workflow that builds `steamcast.exe` via PyInstaller. Download it from the [Actions](https://github.com/underagum/steamcast/actions) or [Releases](https://github.com/underagum/steamcast/releases) tab. The `.exe` bundles Python + Rich + psutil; FFmpeg auto-downloads on first run.

---

## License

MIT — free to use, modify, and share.
