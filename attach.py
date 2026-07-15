"""
SteamCast Attach — read-only live TUI for the daemon.

Connects to the daemon's HTTP API and renders:
    - Stream status table (LIVE / DEAD)
    - Uptime
    - Auto-restart status
    - Recent log lines

Usage: steamcast attach
"""

from __future__ import annotations

import json
import shutil
import sys
import time
import urllib.request


ATTACH_POLL_INTERVAL = 3
DAEMON_PORT = 6789
DAEMON_STATUS_URL = f"http://127.0.0.1:{DAEMON_PORT}/status"
DAEMON_LOGS_URL = f"http://127.0.0.1:{DAEMON_PORT}/logs?n=10"


def _fetch(url: str, timeout: int = 3) -> dict | None:
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read().decode())
    except Exception:
        return None


def _clear():
    """Clear terminal."""
    print("\033[2J\033[H", end="", flush=True)


def attach():
    """Main attach loop — polls daemon and renders live TUI."""
    cols = shutil.get_terminal_size().columns

    print(f"\033[2J\033[H", end="", flush=True)  # Clean slate
    print(f"{'═' * min(cols, 60)}")
    print("  🔍 Connecting to SteamCast daemon...")
    print(f"{'═' * min(cols, 60)}")
    print(f"     API: {DAEMON_STATUS_URL}")
    print()

    # Wait for daemon to be ready
    for attempt in range(10):
        status = _fetch(DAEMON_STATUS_URL)
        if status and status.get("running"):
            break
        time.sleep(1)
    else:
        print("❌ Could not reach SteamCast daemon.")
        print("   Is it running? Try: steamcast daemon start")
        sys.exit(1)

    try:
        while True:
            status = _fetch(DAEMON_STATUS_URL)
            logs = _fetch(DAEMON_LOGS_URL)

            _clear()
            cols = shutil.get_terminal_size().columns or 80

            # ── Title ──
            print(f"\033[1m{'═' * min(cols, 60)}\033[0m")
            print(f"  \033[36m🔵 SteamCast Daemon\033[0m")
            print(f"{'═' * min(cols, 60)}")

            if status:
                pid = status.get("pid", "?")
                uptime = status.get("uptime", "?")
                print(f"  PID {pid}   \033[32m● running\033[0m   uptime {uptime}")
            else:
                print(f"  \033[31m● disconnected\033[0m")

            print()

            # ── Streams table ──
            streams = status.get("streams", []) if status else []
            if streams:
                # Table header
                header = f"  {'Game':<25} {'Status':<10} {'Bitrate':<10} {'PID':<8} {'Started':<10}"
                sep = f"  {'─' * 24} {'─' * 9} {'─' * 9} {'─' * 7} {'─' * 9}"
                print(header)
                print(sep)

                for s in streams:
                    name = s.get("name", "?")[:24]
                    st = s.get("status", "?")
                    bitrate = s.get("bitrate", "?")
                    spid = s.get("pid")
                    started = s.get("started_at", "")[11:19] if s.get("started_at") else ""

                    st_icon = "🟢" if st == "LIVE" else ("🔴" if st == "DEAD" else "⚪")
                    st_display = f"{st_icon} {st}"

                    pid_str = str(spid) if spid else "-"
                    print(f"  {name:<25} {st_display:<10} {bitrate:<10} {pid_str:<8} {started:<10}")
            else:
                print("  \033[33mNo active streams.\033[0m")
                print("  Add games to ~/.steamcast/config.json or restart the daemon.")

            print()

            # ── Logs ──
            if logs and logs.get("lines"):
                print(f"  \033[2m── Recent logs ──\033[0m")
                for line in logs["lines"][-6:]:
                    print(f"  \033[2m{line}\033[0m")

            print()
            print(f"  \033[2mCtrl+C to detach — daemon keeps running\033[0m")
            print(f"{'═' * min(cols, 60)}")

            time.sleep(ATTACH_POLL_INTERVAL)

    except KeyboardInterrupt:
        print()
        print("  Detached. Daemon still running.")
