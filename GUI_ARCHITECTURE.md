# SteamCast GUI — Architecture Plan

## Design Goals

- **Non-technical users first.** No terminal, no JSON editing, no command-line args.
- **Hybrid approach.** GUI wraps existing steamcast.py as library code. No rewrite.
- **Power users keep TUI.** `steamcast.py --cli` stays exactly as-is. GUI is a new entry point.

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   steamcast.exe                  │
│  (double-click)                                  │
│                                                   │
│  ┌─────────┐  ┌─────────┐  ┌──────────────────┐ │
│  │  SETUP  │  │  PREP   │  │    DAEMON        │ │
│  │ (keys)  │  │(convert)│  │ (start/stop/     │ │
│  │         │  │         │  │  live dashboard)  │ │
│  └────┬────┘  └────┬────┘  └───────┬──────────┘ │
│       │            │               │             │
│       └────────────┼───────────────┘             │
│                    │                             │
│           steamcast_core.py                      │
│           (existing TUI logic as library)        │
│                    │                             │
│         ┌──────────┼──────────┐                  │
│         │          │          │                  │
│    config.json  ffmpeg    daemon.py              │
│    (RTMP keys)  (convert) (headless streams)     │
└─────────────────────────────────────────────────┘

steamcast.py --cli         → current Rich TUI (unchanged)
steamcast_gui.py            → tkinter GUI (new)
```

## Tab Breakdown

### 1. Setup (RTMP Keys)
| Widget | Purpose |
|--------|---------|
| ttk.Treeview | Game list — name, key (masked), active toggle |
| "Add" button | Dialog: name + paste key |
| "Paste" button | Auto-pastes from clipboard into selected row |
| "Remove" button | Delete selected game |
| Backend | `load_config()`, `save_config()` from steamcast.py |

### 2. PREP (Video Conversion)
| Widget | Purpose |
|--------|---------|
| File list | Drag-and-drop area (or Browse button) |
| Bitrate selector | Dropdown: 3500k–7000k |
| "CONVERT" button | Runs ffmpeg pipeline |
| Progress bar | Frame-by-frame progress |
| Log output | Last N lines of ffmpeg output |
| Backend | `show_prep_phase()` logic extracted as library |

### 3. Daemon (Background Streams)
| Widget | Purpose |
|--------|---------|
| Start/Stop buttons | `steamcast daemon start` / `stop` |
| Live Dashboard button | Opens `steamcast attach` in a terminal |
| Stream status table | Polls `GET /status` every 5s |
| Backend | `daemon.py` HTTP client calls |

### 4. (Future) CAST Tab
| Widget | Purpose |
|--------|---------|
| Game toggle switches | ON/OFF per game |
| "GO LIVE" button | Start all toggled games |
| "STOP" button | Stop all streams |

## Implementation Strategy

### Phase 1: Core GUI skeleton (this branch)
- 3 tabs with placeholder widgets
- Config read/write wired (Setup tab functional)
- PREP browse button wired (file selection works)

### Phase 2: Wire PREP backend
- Extract `_prep_single_game()`, `_concat_game()` from steamcast.py
- Progress bar updated via ffmpeg stderr parsing thread
- Log output streamed to Text widget

### Phase 3: Wire Daemon backend
- Start/stop via `daemon.py` CLI calls (or direct import)
- Stream table auto-refreshed via HTTP polling thread
- Status bar reflects daemon state

### Phase 4: Polish + .exe
- CustomTkinter dark theme (or stick with ttk clam)
- PyInstaller: `steamcast.exe --gui` (default) / `steamcast.exe --cli`
- Windows Defender happy (zip release)

## Dependencies

| Package | Reason | Status |
|---------|--------|--------|
| `tkinter` | GUI toolkit | Built-in (Windows + most Linux) |
| `customtkinter` | Modern dark theme wrapper | Optional — pip install |

## File Map

```
steamcast/
├── steamcast.py           # TUI (unchanged)
├── steamcast_gui.py       # GUI (new) ← this branch
├── steamcast_core.py      # Refactored shared logic (future)
├── daemon.py              # Daemon (unchanged)
├── attach.py              # Attach TUI (unchanged)
└── build/
    └── steamcast_gui.spec # PyInstaller spec for GUI build
```
