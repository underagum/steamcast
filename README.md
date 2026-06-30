# SteamCast v1.0.0-beta

> Prepare and broadcast multiple game trailers to Steam store pages — no OBS, no Linux, no server setup.

---

## What It Does

**SteamCast** is a Windows tool for game developers and publishers who want to run 24/7 trailers on their Steam store pages during sales or events.

Two phases:

| Phase | What |
|-------|------|
| **PREP** | Convert your video files to Steam's broadcast spec (H.264, AAC, 1080p30, 7 Mbps CBR) and concatenate them by game. One `.mp4` per game, ready to stream. |
| **CAST** | Set up RTMP keys, toggle which games to broadcast, and start/stop streams — all from your Windows machine. |

---

## Requirements

- **Windows 10 or later** (64-bit)
- **Internet connection** (first run only — downloads portable FFmpeg)
- **Optional: NVIDIA GPU** (NVENC), **Intel GPU** (QSV), or **AMD GPU** (AMF) for hardware-accelerated encoding; falls back to software (libx264) if none detected
- **Steam broadcast key** from [Steamworks](https://partner.steamgames.com) for each game

---

## Quick Start

### 1. Download & run

Extract the SteamCast folder anywhere. Double-click `steamcast.bat` — the main menu appears.

### 2. Prepare videos

1. Choose **PREP** from the menu (option `1`)
2. Copy your video files into the `input\` folder
3. Name them like this:

```
input\
├── dreadout 2.mp4          ← single video for this game
├── dreadout 2_1.mkv        ← part 1 (multiple videos for same game)
├── dreadout 2_2.mp4        ← part 2
└── dreadhaunt.mp4          ← another game
```

> The tool parses game names from your filenames. `_1`, `_2` etc. mean "multiple files to concatenate". Everything before `_NUMBER` is the game name.

4. Follow the prompts. The tool detects your GPU and picks best settings.
5. Output goes to `output\` — one `.mp4` per game.

### 3. Set up RTMP keys

From the main menu, choose **Setup** (option `3`) or go directly to **CAST** — it'll ask for keys the first time.

Keys are stored locally in `config.json`. **No data leaves your machine.**

### 4. Cast

Choose **CAST** (option `2`) from the main menu.

- Toggle games ON/OFF by entering their number
- Press `T` to toggle all
- Press `S` to start broadcasting selected games
- While casting, press `Q` to stop all streams

---

## Folder Structure

```
steamcast\
├── steamcast.bat         ← Double-click to launch
├── steamcast.ps1         ← Main script
├── config.json           ← Your RTMP keys (local only)
├── README.md
├── input\                ← Drop video files here
├── output\               ← Processed .mp4s appear here
├── ffmpeg\               ← Auto-downloaded portable FFmpeg
│   └── ffmpeg.exe
└── logs\                 ← Stream logs (for troubleshooting)
```

---

## CLI Usage

You can also run from Command Prompt / PowerShell directly:

```cmd
steamcast.bat prep       # jump straight to Prep
steamcast.bat setup      # jump straight to key setup
steamcast.bat cast       # jump straight to stream toggle
steamcast.bat            # show main menu (default)
```

---

## Steam Broadcast Spec

| Parameter | Steam Requirement | SteamCast Setting |
|-----------|-------------------|-------------------|
| Video codec | H.264 | H.264 (NVENC, QSV, AMF, or x264) |
| Profile | High | High |
| Level | 4.1 | 4.1 |
| Resolution | 1920×1080 | 1920×1080 |
| Frame rate | 30 or 60 FPS | 30 FPS |
| Bitrate | 7000 kbps CBR | 7000 kbps CBR |
| Keyframe interval | 2 seconds | 2 seconds (60 frames @30fps) |
| Audio codec | AAC-LC | AAC-LC |
| Audio bitrate | 128 kbps max | 128 kbps |

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

## Privacy & Security

- **No telemetry.** SteamCast does not collect, send, or report any usage data.
- **No cloud.** All configuration (RTMP keys, game settings) stays in `config.json` on your machine.
- **No network activity** except:
  - One-time FFmpeg download on first run
  - Optional version check at startup (silently fails if offline)
  - The actual RTMP stream you explicitly start
- **RTMP keys are redacted** from log files after each session.

---

## FAQ

**Q: Can I broadcast multiple games at once?**
Yes — each game gets its own FFmpeg process. Toggle them in the CAST menu.

**Q: What happens if I close the terminal while casting?**
All FFmpeg processes are automatically killed via Windows Job Object. No orphan processes left behind.

**Q: Do you send my RTMP keys anywhere?**
No. Everything stays in `config.json` on your machine. Keys are redacted from logs.

**Q: Can I change the bitrate or resolution?**
Not yet — edit `steamcast.ps1` variables (`$Script:VideoBitrate`, etc.) if you need custom values.

**Q: My GPU doesn't support NVENC. Will it still work?**
Yes. The tool checks for NVIDIA NVENC, Intel QSV, and AMD AMF in that order, then falls back to `libx264` (slower encode, same quality).

**Q: Does it check for updates?**
On startup, SteamCast performs a quick version check against GitHub. If a newer version is available, it shows a notification. The check times out silently after 5 seconds if you're offline.

---

## License

MIT — free to use, modify, and share.
