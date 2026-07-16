"""SteamCast Daemon — headless background stream manager for Linux.

Usage:
    steamcast daemon start
    steamcast daemon stop
    steamcast daemon status
    steamcast attach

Architecture:
    ┌─ steamcast daemon start
    │   double-fork → child becomes headless stream engine
    │                 ├── HTTP server (:6789) for status/control
    │                 ├── monitor loop (auto-restart, reconnect)
    │                 └── writes PID to ~/.steamcast/daemon.pid
    │
    ├─ steamcast daemon stop
    │   reads PID → SIGTERM → graceful shutdown
    │
    ├─ steamcast daemon status
    │   GET /status from daemon → prints JSON to console
    │
    └─ steamcast attach
        GET /status + GET /logs from daemon → live TUI
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Optional

# ── Helpers ──

STEAMCAST_DIR = Path.home() / ".steamcast"
PID_FILE = STEAMCAST_DIR / "daemon.pid"
LOG_FILE = STEAMCAST_DIR / "daemon.log"
STATE_FILE = STEAMCAST_DIR / "state.json"
DEFAULT_PORT = 6789


def _ensure_dir():
    STEAMCAST_DIR.mkdir(parents=True, exist_ok=True)


def _parse_bitrate_kbps(bitrate: str) -> int:
    """Extract numeric kbps from bitrate string (e.g. '5000k', '5M', '5000').

    Returns 5000 (kbps) as default on parse failure.
    """
    import re
    raw = bitrate.strip().lower()
    m = re.match(r'^([\d.]+)\s*(k|kb|kbps|m|mb|mbps)?$', raw)
    if not m:
        return 5000  # sensible default
    val = float(m.group(1))
    unit = m.group(2) or 'k'
    if unit in ('m', 'mb', 'mbps'):
        return int(val * 1000)
    return int(val)


# ── Logging ──

def setup_logging():
    _ensure_dir()
    logging.basicConfig(
        filename=str(LOG_FILE),
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logging.getLogger("").addHandler(console)


logger = logging.getLogger("steamcast.daemon")


# ── PID Management ──

def write_pid(pid: int):
    _ensure_dir()
    PID_FILE.write_text(str(pid))


def read_pid() -> Optional[int]:
    if PID_FILE.exists():
        try:
            return int(PID_FILE.read_text().strip())
        except (ValueError, OSError):
            return None
    return None


def remove_pid():
    if PID_FILE.exists():
        PID_FILE.unlink()


def is_process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


# ── Daemon Manager ──

class DaemonError(Exception):
    pass


class DaemonManager:
    """Manages the SteamCast daemon lifecycle (start/stop/status)."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._running = False
        self._active_streams: dict = {}
        self._streams_lock = threading.Lock()
        self._log_buffer: list[str] = []
        self._max_log_lines = 500
        self._start_time: float | None = None

    # ── Public API ──

    def start(self):
        """Daemonize and run the headless stream engine."""
        existing_pid = read_pid()
        if existing_pid and is_process_alive(existing_pid):
            raise DaemonError(
                f"Daemon already running (PID {existing_pid}). "
                f"Use 'steamcast daemon stop' first."
            )

        _ensure_dir()

        # ── First fork ──
        pid = os.fork()
        if pid < 0:
            raise DaemonError("Failed to fork (resource exhaustion?)")
        if pid > 0:
            # Parent: wait for intermediate child to complete its double-fork
            os.waitpid(pid, 0)
            return  # Caller continues (TUI stays, CLI returns to shell)

        # ── Intermediate child ──
        os.setsid()

        # ── Second fork ──
        pid = os.fork()
        if pid < 0:
            raise DaemonError("Failed to fork (resource exhaustion?)")
        if pid > 0:
            os._exit(0)  # Intermediate child exits (no zombie — parent will wait)

        # ── Daemon process ──
        write_pid(os.getpid())

        # Redirect stdio to log file
        sys.stdout.flush()
        sys.stderr.flush()
        with open(LOG_FILE, "a") as f:
            os.dup2(f.fileno(), sys.stdout.fileno())
            os.dup2(f.fileno(), sys.stderr.fileno())

        # Close stdin
        with open(os.devnull, "r") as f:
            os.dup2(f.fileno(), sys.stdin.fileno())

        # Setup logging
        setup_logging()

        # Set start time early (before games check)
        self._start_time = time.time()

        # Signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        logger.info("=== SteamCast Daemon started (PID %d) ===", os.getpid())

        # Load config: which games to stream, auto-restart interval
        games = self.config.get("games", [])
        restart_every = self.config.get("restart_every_hours", 4)
        duration = self.config.get("duration_hours", 0)

        if not games:
            logger.warning("No games configured — daemon starting idle. Add games to ~/.steamcast/config.json")

        # ── Start the HTTP server in a thread ──
        try:
            server = SteamCastDaemonServer(("127.0.0.1", DEFAULT_PORT), self)
        except OSError as e:
            logger.error("Cannot bind port %d: %s. Is another daemon running?", DEFAULT_PORT, e)
            remove_pid()
            raise DaemonError(f"Port {DEFAULT_PORT} already in use. Stop existing daemon first.") from e
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        logger.info("HTTP status server listening on 127.0.0.1:%d", DEFAULT_PORT)

        if not games:
            self._idle_loop()
            return

        logger.info(
            "Starting %d streams, auto-restart every %dh%s",
            len(games), restart_every,
            f", stop after {duration}h" if duration else "",
        )

        # ── Run the headless stream engine ──
        self._run_engine(games, restart_every, duration)

    def stop(self):
        """Stop the daemon gracefully."""
        pid = read_pid()
        if not pid:
            raise DaemonError("No PID file found. Daemon is not running.")

        if not is_process_alive(pid):
            remove_pid()
            raise DaemonError(f"PID {pid} exists but process is dead. Removed stale PID.")

        logger.info("Stopping daemon (PID %d)...", pid)
        os.kill(pid, signal.SIGTERM)

        # Wait up to 10 seconds for clean shutdown
        for _ in range(20):
            if not is_process_alive(pid):
                remove_pid()
                print(f"✅ Daemon stopped (PID {pid})")
                return
            time.sleep(0.5)

        # Force kill if still alive
        try:
            os.kill(pid, signal.SIGKILL)
            remove_pid()
            print(f"⚠️ Daemon killed forcefully (PID {pid})")
        except OSError:
            raise DaemonError("Could not kill daemon process.")

    def status(self) -> dict:
        """Return current daemon status as a dict."""
        pid = read_pid()
        if not pid or not is_process_alive(pid):
            return {"running": False, "pid": None, "uptime": None, "streams": []}

        # Try to get detailed status from the HTTP API
        try:
            import urllib.request
            resp = urllib.request.urlopen(f"http://127.0.0.1:{DEFAULT_PORT}/status", timeout=1)
            return json.loads(resp.read().decode())
        except Exception:
            # Daemon is running but API unreachable (still starting up?)
            uptime = None
            proc_path = f"/proc/{pid}"
            if os.path.exists(proc_path):
                try:
                    created = os.path.getctime(proc_path)
                    uptime = str(timedelta(seconds=int(time.time() - created)))
                except OSError:
                    pass
            return {"running": True, "pid": pid, "uptime": uptime, "streams": []}

    # ── Internal ──

    def _handle_signal(self, signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        self._running = False
        with self._streams_lock:
            for gname, stream in list(self._active_streams.items()):
                proc = stream.get("proc")
                if proc and proc.poll() is None:
                    logger.info("Killing stream: %s", gname)
                    proc.kill()
        remove_pid()
        logger.info("Daemon stopped.")
        sys.exit(0)

    def _idle_loop(self):
        """Run an idle loop when no games are configured."""
        self._running = True
        while self._running:
            time.sleep(5)

    def _run_engine(self, games: list[dict], restart_every: int, duration_hours: int):
        """Headless stream engine — extracted from run_cast_stream logic."""
        self._running = True

        # ── Import steamcast internals ──
        sys.path.insert(0, str(Path(__file__).parent))
        from steamcast import find_ffmpeg, sanitize_filename, LOG_DIR

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            logger.error("ffmpeg not found. Cannot start streaming.")
            return

        # Determine end time for duration-based stop
        start_time = datetime.now()
        end_at = start_time + timedelta(hours=duration_hours) if duration_hours > 0 else None
        next_restart_at = start_time + timedelta(hours=restart_every) if restart_every > 0 else None

        if next_restart_at:
            logger.info("Auto-restart every %dh (first at %s)", restart_every, next_restart_at.strftime("%H:%M:%S"))

        # ── Launch all streams ──
        for game in games:
            gname = game.get("name", "Unknown")
            bitrate = game.get("bitrate", "5000k")
            video = game.get("video")

            if not video or not Path(video).exists():
                logger.error("Video not found for '%s': %s", gname, video)
                continue

            rtmp_url = f"rtmp://ingest-rtmp.broadcast.steamcontent.com/app/{game.get('stream_key', '')}"
            args = [
                ffmpeg,
                "-re", "-stream_loop", "-1",
                "-i", str(video),
                "-c", "copy",
                "-f", "flv",
                "-b:v", bitrate,
                "-maxrate", bitrate,
                "-bufsize", f"{_parse_bitrate_kbps(bitrate) * 2}k",
                str(rtmp_url),
            ]

            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            self._log(f"Streaming {gname} — {bitrate} (PID {proc.pid})")

            with self._streams_lock:
                self._active_streams[gname] = {
                    "proc": proc,
                    "bitrate": bitrate,
                    "video": str(video),
                    "stream_key": game.get("stream_key", ""),
                    "started_at": datetime.now().isoformat(),
                    "status": "LIVE",
                    "start_args": args,
                }
            time.sleep(2)

        self._log(f"All {len(self._active_streams)} streams launched.")

        # ── Monitor loop ──
        while self._running:
            now = datetime.now()

            # Check duration limit
            if end_at and now >= end_at:
                logger.info("Duration limit reached (%dh). Stopping all streams.", duration_hours)
                self._stop_all_streams("duration_limit")
                break

            # Check auto-restart
            if next_restart_at and now >= next_restart_at:
                logger.info("Auto-restart triggered — killing all streams...")
                self._log("♻ Auto-restart triggered — killing all streams...")
                self._kill_all_streams()
                next_restart_at = now + timedelta(hours=restart_every)
                logger.info("Next auto-restart at %s", next_restart_at.strftime("%H:%M:%S"))
                self._log(f"♻ Next auto-restart at {next_restart_at.strftime('%H:%M:%S')}")

            # Check each stream's health
            with self._streams_lock:
                for gname, stream in list(self._active_streams.items()):
                    proc = stream.get("proc")
                    if proc and proc.poll() is not None:
                        exit_code = proc.returncode
                        logger.warning("Stream %s died (exit %d). Reconnecting...", gname, exit_code)
                        self._log(f"✗ {gname} died (exit {exit_code}). Reconnecting...")

                        # Restart stream using stored args
                        args = stream.get("start_args", [])
                        if args:
                            new_proc = subprocess.Popen(
                                args,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            stream["proc"] = new_proc
                            stream["status"] = "LIVE"
                            stream["started_at"] = datetime.now().isoformat()
                            self._log(f"↻ {gname} reconnected (PID {new_proc.pid})")
                        else:
                            stream["status"] = "DEAD"
                            self._log(f"✗ {gname} — start args missing, cannot reconnect")

                    if proc and proc.poll() is None:
                        stream["status"] = "LIVE"

            self._write_state()
            time.sleep(5)

        self._stop_all_streams("engine_stop")

    def _kill_all_streams(self):
        """Kill all ffmpeg processes. Existing reconnect logic will respawn them."""
        with self._streams_lock:
            for gname, stream in list(self._active_streams.items()):
                proc = stream.get("proc")
                if proc and proc.poll() is None:
                    proc.kill()
                    logger.info("Killed stream: %s (PID %d)", gname, proc.pid)
        self._log("♻ All streams killed — reconnect will pick them up.")
        time.sleep(2)

    def _stop_all_streams(self, reason: str):
        """Terminate all streams permanently."""
        logger.info("Stopping all streams (reason: %s)", reason)
        with self._streams_lock:
            for gname, stream in list(self._active_streams.items()):
                proc = stream.get("proc")
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                stream["status"] = "STOPPED"
            self._active_streams.clear()
        self._write_state()
        self._running = False

    def _log(self, msg: str):
        """Add a line to the in-memory log buffer."""
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        self._log_buffer.append(line)
        if len(self._log_buffer) > self._max_log_lines:
            self._log_buffer.pop(0)
        logger.info("%s", msg)

    def _uptime_str(self) -> str:
        if self._start_time:
            elapsed = time.time() - self._start_time
            return str(timedelta(seconds=int(elapsed)))
        return "unknown"

    def _write_state(self):
        """Write current state to JSON file for external tools."""
        with self._streams_lock:
            state = {
                "pid": os.getpid(),
                "uptime_seconds": int(time.time() - self._start_time) if self._start_time else 0,
                "streams": {
                    gname: {
                        "status": s.get("status", "UNKNOWN"),
                        "bitrate": s.get("bitrate", "?"),
                        "started_at": s.get("started_at", ""),
                        "pid": s.get("proc", None) and (s["proc"].pid or None),
                    }
                    for gname, s in self._active_streams.items()
                },
            }
        try:
            STATE_FILE.write_text(json.dumps(state, indent=2))
        except OSError:
            pass


# ── HTTP Status Server ──


class SteamCastDaemonServer:
    """Lightweight HTTP server exposing daemon status via JSON API.

    Endpoints:
        GET /status    — Stream states, uptime, PID
        GET /logs?n=N  — Last N log lines (default 50)
        POST /shutdown — Graceful stop
    """

    def __init__(self, addr: tuple[str, int], daemon: DaemonManager):
        self._addr = addr
        self._daemon = daemon

        class _Handler(BaseHTTPRequestHandler):

            def do_GET(self):
                if self.path in ("/status", "/"):
                    with daemon._streams_lock:
                        self._send_json({
                            "running": True,
                            "pid": os.getpid(),
                            "uptime": daemon._uptime_str(),
                            "streams": [
                                {
                                    "name": gname,
                                    "status": s.get("status", "UNKNOWN"),
                                    "bitrate": s.get("bitrate", ""),
                                    "pid": proc.pid if (proc := s.get("proc")) and proc.poll() is None else None,
                                    "started_at": s.get("started_at", ""),
                                }
                                for gname, s in daemon._active_streams.items()
                            ],
                        })
                elif self.path.startswith("/logs"):
                    n = 50
                    if "?n=" in self.path:
                        try:
                            n = int(self.path.split("?n=")[1])
                        except (ValueError, IndexError):
                            pass
                    self._send_json({"lines": daemon._log_buffer[-n:]})
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error": "not found"}')

            def do_POST(self):
                if self.path == "/shutdown":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"status": "shutting down"}')
                    Thread(target=lambda: (time.sleep(0.5), daemon._handle_signal(signal.SIGTERM, None)), daemon=True).start()
                else:
                    self.send_response(404)
                    self.end_headers()
                    self.wfile.write(b'{"error": "not found"}')

            def _send_json(self, data: dict):
                body = json.dumps(data).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format, *args):
                pass

            def do_OPTIONS(self):
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.end_headers()

        self._handler_class = _Handler
        self._server = HTTPServer(addr, _Handler)

    def serve_forever(self):
        self._server.serve_forever()


# ── CLI Entry Points ──


def cmd_start(config: dict | None = None):
    """Start the daemon (called from steamcast.py)."""
    mgr = DaemonManager(config)
    try:
        mgr.start()
    except DaemonError as e:
        print(f"❌ {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nShutdown requested.")
        mgr.stop()


def cmd_stop():
    """Stop the daemon."""
    mgr = DaemonManager()
    try:
        mgr.stop()
    except DaemonError as e:
        print(f"❌ {e}")
        sys.exit(1)


def cmd_status() -> dict:
    """Show daemon status."""
    mgr = DaemonManager()
    return mgr.status()


def load_config() -> dict:
    """Load daemon config, merging TUI config with daemon overrides.

    Reads:
      1. ~/projects/steamcast/config.json  (TUI — game names + RTMP keys)
      2. ~/.steamcast/config.json           (override — restart_every, duration, extra games)

    Auto-discovers video files from ~/projects/steamcast/output/<name>.mp4.
    Only active games from the TUI config are included.
    """
    config: dict = {"games": [], "restart_every_hours": 4, "duration_hours": 0}

    # 1. Load TUI config
    tui_cfg_path = Path.home() / "projects" / "steamcast" / "config.json"
    tui_games: dict = {}
    if tui_cfg_path.exists():
        try:
            tui_cfg = json.loads(tui_cfg_path.read_text())
            tui_games = tui_cfg.get("games", {})
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not parse %s, skipping", tui_cfg_path)

    # 2. Load daemon overrides
    daemon_cfg_path = STEAMCAST_DIR / "config.json"
    if daemon_cfg_path.exists():
        try:
            dm_cfg = json.loads(daemon_cfg_path.read_text())
            config["restart_every_hours"] = dm_cfg.get("restart_every_hours", 4)
            config["duration_hours"] = dm_cfg.get("duration_hours", 0)
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Convert TUI games to daemon format
    output_dir = Path.home() / "projects" / "steamcast" / "output"
    for gname, gdata in tui_games.items():
        if not gdata.get("active", False):
            continue

        stream_key = gdata.get("rtmp_key", "")
        if not stream_key:
            continue

        # Auto-detect video from output dir
        video_path = output_dir / f"{gname}.mp4"
        if not video_path.exists():
            # Try alternate filenames
            for ext in (".mp4", ".mkv", ".webm"):
                candidate = output_dir / f"{gname}{ext}"
                if candidate.exists():
                    video_path = candidate
                    break

        if not video_path.exists():
            logger.warning("No video found for '%s' in %s — skipping", gname, output_dir)
            continue

        config["games"].append({
            "name": gname,
            "bitrate": "5000k",
            "video": str(video_path),
            "stream_key": stream_key,
        })

    return config


if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "start":
            cmd_start(load_config())
        elif sys.argv[1] == "stop":
            cmd_stop()
        elif sys.argv[1] == "status":
            st = cmd_status()
            print(json.dumps(st, indent=2))
        else:
            print(f"Usage: {sys.argv[0]} {{start|stop|status}}")
    else:
        print(f"Usage: {sys.argv[0]} {{start|stop|status}}")
