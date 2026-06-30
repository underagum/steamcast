# SteamCast v1.0.0-beta

> Prepare and broadcast multiple game trailers to Steam store pages — no OBS, no server setup.

SteamCast is a **Python** tool for game developers and publishers who want to run 24/7 trailers on their Steam store pages during sales or events. It replaces OBS with lightweight FFmpeg streaming — near-zero CPU when using `-c copy`.

---

## What It Does

Two phases:

| Phase | What |
|-------|------|
| **PREP** | Convert your video files to Steam's broadcast spec (H.264, AAC, 1080p30, 7 Mbps CBR, 44100Hz) and concatenate multi-part videos by game. One `.mp4` per game, ready to stream. |
| **CAST** | Set up RTMP keys, toggle which games to broadcast, and start/stop streams — all from one terminal window. Real-time monitor with bitrate and uptime. |

---

## Requirements

- **Python 3.9+** (3.11 recommended)
- **`rich`** — `pip install rich` (for colored interface; falls back to plain text if not installed)
- **Windows 10 or later (64-bit)** — the tool is cross-platform but GPU detection targets Windows
- **Internet connection** (first run only — downloads portable FFmpeg ~55MB from gyan.dev)
- **Optional GPU:** NVIDIA (NVENC), Intel (QSV), or AMD (AMF) for hardware-accelerated encoding; falls back to `libx264` software encoding if none detected
- **Steam broadcast key** from [Steamworks](https://partner.steamgames.com) for each game

---

## Quick Start

### 1. Clone & install

```bash
git clone https://github.com/underagum/steamcast.git
cd steamcast
pip install rich
```

### 2. Prepare videos

```bash
python steamcast.py prep
```

1. Copy your video files into the `input/` folder
2. Name them like this:

```
input/
├── dreadout 2.mp4          ← single video for this game
├── dreadout 2_1.mkv        ← part 1 (multiple videos for same game)
├── dreadout 2_2.mp4        ← part 2
└── dreadhaunt.mp4          ← another game
```

> The tool parses game names from your filenames. `_1`, `_2` etc. mean "multiple files to concatenate". Everything before `_NUMBER` is the game name.

3. Follow the prompts — the tool detects your GPU and picks the best encoder
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
- While casting, press `Q` to stop all streams
- Failed conversions show the last 10 lines of the FFmpeg log immediately

### Or use the main menu

```bash
python steamcast.py
```

---

## Folder Structure

```
steamcast/
├── steamcast.py          ← Main script (cross-platform)
├── config.json           ← Your RTMP keys (local only, gitignored)
├── README.md
├── version.txt
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
| Bitrate | 7000 kbps CBR | 7000 kbps CBR (`-b:v -maxrate -bufsize` all equal) |
| Keyframe interval | 2 seconds | 2 seconds (60 frames @30fps) |
| Pixel format | yuv420p | yuv420p |
| Audio codec | AAC-LC | AAC-LC |
| Audio bitrate | 128 kbps max | 128 kbps |
| Audio sample rate | 44100 Hz | 44100 Hz |

---

## GPU Support

SteamCast automatically detects available hardware encoders in priority order:

| Priority | Encoder | Required | Notes |
|----------|---------|----------|-------|
| 1 | **NVIDIA NVENC** (`h264_nvenc`) | NVIDIA GPU (GTX 600+) | Preset p7, CBR |
| 2 | **Intel QSV** (`h264_qsv`) | Intel GPU with Quick Sync | Preset veryfast |
| 3 | **AMD AMF** (`h264_amf`) | AMD GPU | Only in FFmpeg FULL build; Preset quality |
| 4 | **Software fallback** (`libx264`) | CPU only | Slower encode, same quality |

If no hardware encoder is found, SteamCast falls back to `libx264` software encoding.

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

**Q: What happens if I close the terminal while casting?**
FFmpeg processes will be orphaned. On Windows, use Task Manager to kill remaining `ffmpeg.exe` processes. (PyInstaller `.exe` with proper process management coming in a future release.)

**Q: Do you send my RTMP keys anywhere?**
No. Everything stays in `config.json` on your machine. Keys are redacted from logs.

**Q: What if the conversion fails?**
The last 10 lines of the FFmpeg log are shown immediately. The full log is saved in `logs/` for deeper investigation.

**Q: My GPU doesn't support NVENC. Will it still work?**
Yes. The tool checks for NVIDIA NVENC, Intel QSV, and AMD AMF in that order, then falls back to `libx264` (slower encode, same quality).

**Q: Does it check for updates?**
On startup, SteamCast performs a quick version check against GitHub. If a newer version is available, it shows a notification. The check times out silently after 5 seconds if you're offline.

**Q: Are there plans for a standalone .exe?**
Yes — PyInstaller bundling is planned so you can run SteamCast without installing Python.

---

## License

MIT — free to use, modify, and share.
