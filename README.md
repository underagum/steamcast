# SteamCast v1.1.6

> Prepare and broadcast multiple videos to Steam store pages — no OBS, no server setup, no Python required.

SteamCast is a lightweight FFmpeg-based broadcasting tool for game developers and publishers who want to run 24/7 videos on their Steam store pages during sales or events. **Download the `.exe` and run it — that's it.** Near-zero CPU when using hardware encoding (NVENC).

---

## What It Does

Two phases:

| Phase | What |
|-------|------|
| **PREP** | Convert your video files to Steam's broadcast spec (H.264, AAC, 1080p30, 7 Mbps CBR, 44100Hz) and concatenate multi-part videos by game. One `.mp4` per game, ready to stream. Progress bars for download and extraction. |
| **CAST** | Set up RTMP keys, toggle which games to broadcast, and start/stop streams — all from one terminal window. **Live per-stream dashboard** with per-game CPU%, real-time bitrate from FFmpeg output, GPU encoder load + VRAM (NVENC), system RAM, and total network TX rate. |

---

## Getting SteamCast

You have two options — pick one.

### Option A: Standalone .exe (recommended)

**No Python, no pip, no setup.** Download the `.exe` and double-click it.

| Requirement | Detail |
|-------------|--------|
| **Windows 10/11 (64-bit)** | Primary platform |
| **GPU (optional)** | NVIDIA NVENC (driver ≥ 610.00), Intel QSV, or AMD AMF — auto-detected. Falls back to CPU encoding if none found. |
| **Steam broadcast key** | From [Steamworks](https://partner.steamgames.com) for each game |
| **Internet** | First run only — auto-downloads portable FFmpeg (~55 MB). Runs completely offline after that. |

**[Download steamcast.exe](https://github.com/underagum/steamcast/releases/latest)** from the Releases page. Put it in its own folder, double-click, and you're in the main menu. Nothing else to install.

### Option B: Run from source (Python)

For developers, Linux/macOS users, or anyone who prefers running the script directly.

| Requirement | Detail |
|-------------|--------|
| **Python 3.9+** (3.11 recommended) | |
| **`rich`** | `pip install rich` — colored TUI |
| **`psutil`** | `pip install psutil` — live system monitoring in CAST dashboard |
| **Windows 10/11, Linux, or macOS** | Windows is primary; Linux/macOS are tested but secondary |
| **GPU (optional)** | Same as above — NVIDIA NVENC, Intel QSV, AMD AMF, or CPU fallback |
| **Steam broadcast key** | From [Steamworks](https://partner.steamgames.com) |
| **FFmpeg** | Auto-downloaded on first run. Internet required once. |

```bash
git clone https://github.com/underagum/steamcast.git
cd steamcast
pip install rich psutil
python steamcast.py
```

> If `psutil` is not installed, the cast dashboard runs in stream-only mode (no CPU/RAM/GPU stats). The standalone `.exe` bundles everything — no missing dependencies.

---

## Quick Start

### Step 1: Launch SteamCast

| If you're using... | Do this |
|--------------------|---------|
| **Standalone .exe** | Double-click `steamcast.exe`. You'll see the main menu. Jump to **PREP** (option 1). |
| **Python source** | `python steamcast.py` (or `python steamcast.py prep` to jump straight in) |

First launch auto-downloads FFmpeg (~55 MB, one-time). You'll see a progress bar — let it finish.

### Step 2: Prepare videos

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

### Step 3: Set up RTMP keys

From the main menu, pick **Setup** (option 3) — or run `python steamcast.py setup`. Add game names and paste RTMP keys from Steamworks. Keys are stored locally in `config.json`. **No data leaves your machine.**

### Step 4: Cast

From the main menu, pick **CAST** (option 2) — or run `python steamcast.py cast`.

- Toggle games ON/OFF by entering their number — **your choices persist** across sessions and broadcasts until you intentionally change them
- Press `T` to toggle all
- Press `S` to start broadcasting selected games
- While casting: **live per-stream CPU%, bitrate, GPU/encoder load (NVENC), system RAM, and network TX** refresh every 0.5s
- Press Enter to stop all streams

---

## Live Cast Dashboard

While broadcasting, each game row shows its own real-time stats:

```
DreadOut 2          ● RUNNING   (01:23:45)   PID 18492   CPU 12%   7.0M
DreadOut Remaster   ● RUNNING   (01:23:44)   PID 18501   CPU 8%    6.8M

RAM: 58%   TX: 13.2 MB/s
GPU: 24%   ENC: 8%    VRAM: 2.3/8.0 GB
```

| Column | Source | Meaning |
|--------|--------|---------|
| **CPU%** | `psutil` per-PID | That specific FFmpeg child's CPU usage |
| **Bitrate** (7.0M) | FFmpeg stderr `bitrate=...kbits/s` | Actual encoding output rate being pushed to RTMP |
| **RAM** | System-wide `psutil` | Total memory pressure |
| **TX** | System-wide NET I/O delta | Total network send rate (all streams combined) |
| **GPU** | `nvidia-smi` | Total GPU die utilisation (NVIDIA only; silent on Intel/AMD) |
| **ENC** | `nvidia-smi util.encoder` | NVENC ASIC saturation — your real encoding ceiling |
| **VRAM** | `nvidia-smi mem.used/total` | GPU memory used out of total available |

CPU and bitrate turn yellow at 50% (or above target), red at 85%. GPU/ENC turn yellow at 80% encoder load. Requires `psutil`. Falls back gracefully to a clean stream-only display if `psutil` is not installed.

> **Tip:** If you're running CPU-only (libx264), the GPU/ENC/VRAM row doesn't appear — it's hardware-encoder-only.

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

**Standalone .exe users:** Just put `steamcast.exe` in its own folder. The `input/`, `output/`, `ffmpeg/`, `logs/` directories and `config.json` are auto-created next to the `.exe` on first run.

**Python source:**

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
│   ├── steamcast.spec     ← PyInstaller spec for standalone .exe
│   └── steamcast.exe      ← Pre-built binary (GitHub Release download)
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

| Action | Standalone .exe | Python source |
|--------|----------------|---------------|
| Main menu | Double-click `steamcast.exe` | `python steamcast.py` |
| Jump to Prep | `steamcast.exe prep` | `python steamcast.py prep` |
| Jump to Setup | `steamcast.exe setup` | `python steamcast.py setup` |
| Jump to Cast | `steamcast.exe cast` | `python steamcast.py cast` |

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

**Q: Do I need Python?**
No — if you download the standalone `.exe` from [Releases](https://github.com/underagum/steamcast/releases/latest). It bundles Python, Rich, and psutil into a single 11 MB file. FFmpeg auto-downloads on first run. Double-click and go.

If you clone the source, you'll need Python 3.9+ and `pip install rich psutil`.

**Q: Can I broadcast multiple games at once?**
Yes — each game gets its own FFmpeg process. Toggle them in the CAST menu.

**Q: Does it show per-game resource usage?**
Yes. The cast dashboard shows per-stream CPU%, bitrate, GPU encoder load + VRAM (NVENC), system RAM, and total network TX. The standalone `.exe` includes everything out of the box. Python users: `pip install psutil` (gracefully falls back to stream-only display if missing).

**Q: Do I need to re-toggle games every time I start a broadcast?**
No. Your ON/OFF choices in the CAST menu persist in `config.json` and survive across broadcasts and restarts. If you want a clean slate, use `[T]` Toggle ALL to flip everything OFF at once.

**Q: Can I see GPU usage while broadcasting with NVENC?**
Yes — if you're using NVIDIA NVENC, the cast dashboard shows a dedicated GPU row with total GPU utilisation, NVENC encoder load, and VRAM usage. This row disappears automatically if you're on CPU (libx264) or a non-NVIDIA GPU. Does not require additional setup — just needs `nvidia-smi` on your PATH (included with NVIDIA drivers).

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

---

## License

MIT — free to use, modify, and share.
