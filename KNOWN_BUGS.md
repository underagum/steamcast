# Known Bugs

## yt-dlp --download-sections can truncate audio tail (2026-09-15)

**Symptom:** Steam broadcast dashboard shows "audio behind"; daemon dies exit 152 every ~3 min.

**Root cause:** Source YouTube streams are equal length (verified via googlevideo `dur=` on all candidate videos). `--download-sections` + `bestvideo+bestaudio` downloads video and audio as **separate HTTP streams**, each cut independently (`-ss START -t DUR` per format in yt-dlp's ffmpeg downloader), then merged. The audio stream's delivery for the requested window came back ~25.7s short (94.34s audio vs 120.07s video); the merge silently paired full video with truncated audio. A chunk of audio is left behind by the per-stream section delivery.

**Status:** Mitigated by v2.2.1 gate — `_audio_gap_scan()` catches tail holes (audio EOF < video EOF) → auto-repair via `repair_audio.py` silence pad. No yt-dlp-side fix attempted.

**Workaround:** Re-edit the file in CapCut (or any editor that re-muxes audio to video length).

**Diagnosis recipe:**
```bash
ffprobe -v error -show_entries stream=codec_type,duration -of csv F.mp4
```
Audio duration < video duration = tail hole, even when internal gap scan is clean.
