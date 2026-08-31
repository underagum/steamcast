"""SteamCast Daemon — headless background stream manager for Linux.

Usage:
    steamcast daemon start
    steamcast daemon stop
    steamcast daemon status
    steamcast daemon attach

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
    └─ steamcast daemon attach
        GET /status + GET /logs from daemon → live TUI
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from threading import Thread
from typing import Optional

from liveness import probe_page, probe_stream, probe_with_retries

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


def remove_pid(expected_pid: int | None = None):
    """Delete the PID file — optionally only if it matches expected_pid."""
    if not PID_FILE.exists():
        return
    if expected_pid is not None:
        current = read_pid()
        if current != expected_pid:
            return  # another process wrote its PID — don't delete
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
        self._durations: dict[str, Optional[float]] = {}
        # Per-game cumulative playback position (seconds) — survives reconnects
        # and daemon restarts so broadcasts continue instead of restarting 00:00.
        self._resume_offsets: dict[str, float] = self._load_resume_offsets()

    # ── Public API ──

    def start(self, foreground: bool = False):
        """Daemonize and run the headless stream engine.

        When foreground=True (systemd mode): skip double-fork,
        run directly so systemd can supervise the process.
        """
        existing_pid = read_pid()
        if existing_pid and is_process_alive(existing_pid):
            raise DaemonError(
                f"Daemon already running (PID {existing_pid}). "
                f"Use 'steamcast daemon stop' first."
            )

        # Check port availability BEFORE forking (fast feedback)
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", DEFAULT_PORT))
        except OSError:
            s.close()
            raise DaemonError(
                f"Port {DEFAULT_PORT} already in use. "
                f"Another daemon may be running. Check 'steamcast daemon status'."
            )
        s.close()

        _ensure_dir()

        if foreground:
            # ── Foreground mode (systemd) ──
            # No forking — systemd is the process supervisor.
            # Write PID so 'steamcast daemon stop' still works.
            write_pid(os.getpid())

            # Setup logging (prefix so journald picks it up)
            setup_logging()

            # Set start time early
            self._start_time = time.time()

            # Signal handlers
            signal.signal(signal.SIGTERM, self._handle_signal)
            signal.signal(signal.SIGINT, self._handle_signal)

            logger.info("=== SteamCast Daemon started (PID %d) ===", os.getpid())

            # Load config
            games = self.config.get("games", [])
            restart_every = self.config.get("restart_every_hours", 4)

            if not games:
                logger.warning("No games configured — daemon starting idle.")

            # Start HTTP server thread
            try:
                server = SteamCastDaemonServer(("127.0.0.1", DEFAULT_PORT), self)
            except OSError as e:
                logger.error("Cannot bind port %d: %s. Is another daemon running?", DEFAULT_PORT, e)
                remove_pid(os.getpid())  # only delete OUR pid, not another running daemon's
                raise DaemonError(f"Port {DEFAULT_PORT} already in use.") from e
            server_thread = Thread(target=server.serve_forever, daemon=True)
            server_thread.start()
            logger.info("HTTP status server listening on 127.0.0.1:%d", DEFAULT_PORT)

            if not games:
                self._idle_loop()
                return

            logger.info(
                "Starting %d streams, auto-restart every %dh",
                len(games), restart_every,
            )
            self._run_engine(games, restart_every)
            return

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

        if not games:
            logger.warning("No games configured — daemon starting idle. Add games to SteamCast TUI Setup (option 3).")

        # ── Start the HTTP server in a thread ──
        try:
            server = SteamCastDaemonServer(("127.0.0.1", DEFAULT_PORT), self)
        except OSError as e:
            logger.error("Cannot bind port %d: %s. Is another daemon running?", DEFAULT_PORT, e)
            remove_pid(os.getpid())  # only delete OUR pid, not another running daemon's
            raise DaemonError(f"Port {DEFAULT_PORT} already in use. Stop existing daemon first.") from e
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        logger.info("HTTP status server listening on 127.0.0.1:%d", DEFAULT_PORT)

        if not games:
            self._idle_loop()
            return

        logger.info(
            "Starting %d streams, auto-restart every %dh",
            len(games), restart_every,
        )

        # ── Run the headless stream engine ──
        self._run_engine(games, restart_every)

    def stop(self):
        """Stop the daemon gracefully."""
        pid = read_pid()
        if not pid:
            # Idempotent success in systemd-executed contexts (ExecStop,
            # schedule stop-trigger): a missing PID file is normal there —
            # the daemon already exited (or never ran under this service).
            if os.environ.get("INVOCATION_ID"):
                logger.info("No PID file (systemd context) — nothing to stop.")
                print("✅ Daemon already stopped (no PID file).")
                return
            # Before giving up, try systemctl if service is installed
            unit_path = "/etc/systemd/system/steamcast.service"
            if os.path.exists(unit_path):
                logger.info("No PID file — trying systemctl stop instead.")
                try:
                    subprocess.run(["sudo", "systemctl", "stop", "steamcast"], check=True)
                    print("✅ System service stopped via systemctl.")
                    return
                except subprocess.CalledProcessError:
                    pass  # fall through to error below
            raise DaemonError("No PID file found. Daemon is not running.")

        if not is_process_alive(pid):
            remove_pid()
            raise DaemonError(f"PID {pid} exists but process is dead. Removed stale PID.")

        logger.info("Stopping daemon (PID %d)...", pid)

        # If running as systemd service, use systemctl for clean stop.
        # Skip if we're being called FROM systemd (ExecStop) — avoid recursion.
        unit_path = "/etc/systemd/system/steamcast.service"
        if os.path.exists(unit_path) and not os.environ.get("INVOCATION_ID"):
            logger.info("Systemd service detected — using systemctl stop.")
            try:
                subprocess.run(["sudo", "systemctl", "stop", "steamcast"], check=True)
                # Verify the daemon actually stopped (it may have been running outside systemd)
                time.sleep(1)
                if not is_process_alive(pid):
                    print("✅ System service stopped via systemctl.")
                    return
                logger.warning("systemctl stop succeeded but daemon still alive — falling back to SIGTERM.")
            except subprocess.CalledProcessError:
                logger.warning("systemctl stop failed, falling back to SIGTERM.")

        os.kill(pid, signal.SIGTERM)

        # Wait up to 10 seconds for clean shutdown
        for _ in range(20):
            if not is_process_alive(pid):
                remove_pid()
                print(f"✅ Daemon stopped (PID {pid})")
                # Verify port is actually free before confirming
                print(f"     Waiting for port {DEFAULT_PORT} to release...", end="", flush=True)
                if self._wait_port_free(timeout=60):
                    print(f"\n     ✓ Port {DEFAULT_PORT} free")
                else:
                    print(f"\n     ⚠ Port {DEFAULT_PORT} still in use after 60s. "
                          f"Wait before restarting.")
                # Stop means stop — disable auto-start on reboot
                self._disable_service_if_enabled()
                return
            time.sleep(0.5)

        # Force kill if still alive
        try:
            os.kill(pid, signal.SIGKILL)
            remove_pid()
            print(f"⚠️ Daemon killed forcefully (PID {pid})")
            print(f"     Waiting for port {DEFAULT_PORT} to release...", end="", flush=True)
            if self._wait_port_free(timeout=60):
                print(f"\n     ✓ Port {DEFAULT_PORT} free")
            else:
                print(f"\n     ⚠ Port {DEFAULT_PORT} still in use after 60s. "
                      f"Wait before restarting.")
            self._disable_service_if_enabled()
        except OSError:
            raise DaemonError("Could not kill daemon process.")

    def status(self) -> dict:
        """Return current daemon status as a dict."""
        pid = read_pid()
        if not pid or not is_process_alive(pid):
            # PID file missing or process dead — try HTTP API as fallback
            try:
                resp = urllib.request.urlopen(f"http://127.0.0.1:{DEFAULT_PORT}/status", timeout=1)
                data = json.loads(resp.read().decode())
                if data.get("running"):
                    # Daemon is alive, PID file just got lost — restore it
                    live_pid = data.get("pid")
                    if live_pid:
                        write_pid(live_pid)
                    return data
            except Exception:
                pass
            return {"running": False, "pid": None, "uptime": None, "streams": []}

        # Try to get detailed status from the HTTP API
        try:
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

    @staticmethod
    def _disable_service_if_enabled():
        """Disable systemd auto-start if the service is currently enabled."""
        unit_path = "/etc/systemd/system/steamcast.service"
        if not os.path.exists(unit_path):
            return
        # Don't disable when called from systemd's ExecStop
        if os.environ.get("INVOCATION_ID"):
            return
        try:
            subprocess.run(["sudo", "systemctl", "disable", "--quiet", "steamcast"],
                          check=True, capture_output=True)
            print("🔧 System service disabled — won't auto-start on reboot.")
            print("   Re-enable: steamcast daemon service install")
        except subprocess.CalledProcessError:
            # sudo unavailable — user can do it manually
            pass

    @staticmethod
    def _wait_port_free(timeout: float = 60) -> bool:
        """Wait for port 6789 to be released. Returns True if free, False if timed out."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", DEFAULT_PORT))
                s.close()
                return True
            except OSError:
                s.close()
                print(".", end="", flush=True)
                time.sleep(1.0)
        return False

    def _handle_signal(self, signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        self._running = False
        with self._streams_lock:
            for gname, stream in list(self._active_streams.items()):
                proc = stream.get("proc")
                if proc and proc.poll() is None:
                    logger.info("Killing stream: %s", gname)
                    proc.kill()
        remove_pid(os.getpid())  # only delete OUR pid, not another daemon's
        logger.info("Daemon stopped.")
        sys.exit(0)

    def _idle_loop(self):
        """Run an idle loop when no games are configured."""
        self._running = True
        while self._running:
            time.sleep(5)

    def _load_resume_offsets(self) -> dict[str, float]:
        """Load persisted per-game resume offsets from the state file."""
        try:
            if not STATE_FILE.exists():
                return {}
            state = json.loads(STATE_FILE.read_text())
            resume = state.get("resume", {})
            return {g: float(v) for g, v in resume.items() if isinstance(v, (int, float))}
        except Exception:
            return {}

    def _probe_duration(self, video: str) -> Optional[float]:
        """Return video duration in seconds via ffprobe (cached). None on failure."""
        if video in self._durations:
            return self._durations[video]
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from steamcast import find_ffmpeg
            ffmpeg = find_ffmpeg()
            ffprobe = None
            if ffmpeg:
                cand = Path(ffmpeg).with_name("ffprobe" + (".exe" if sys.platform == "win32" else ""))
                if cand.exists():
                    ffprobe = str(cand)
            if not ffprobe:
                import shutil
                ffprobe = shutil.which("ffprobe")
            if not ffprobe:
                return None
            out = subprocess.run(
                [ffprobe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", video],
                capture_output=True, text=True, timeout=30,
            )
            dur = float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else None
        except Exception:
            dur = None
        self._durations[video] = dur
        return dur

    def _build_stream_args(self, ffmpeg: str, video: str, stream_key: str,
                           bitrate: str, resume_offset: float = 0.0) -> list[str]:
        """Build ffmpeg args for one stream, seeking to resume_offset when > 0.

        The video is looped (-stream_loop -1), so a resume offset past the end
        wraps via modulo when duration is known — playback continues seamlessly
        instead of restarting at 00:00 on every reconnect/daemon restart.
        """
        args = [ffmpeg, "-re", "-stream_loop", "-1"]
        if resume_offset > 0:
            args += ["-ss", f"{resume_offset:.3f}"]
        args += [
            "-i", str(video),
            "-c", "copy",
            "-f", "flv",
            "-b:v", bitrate,
            "-maxrate", bitrate,
            "-bufsize", f"{_parse_bitrate_kbps(bitrate) * 2}k",
            f"rtmp://ingest-rtmp.broadcast.steamcontent.com/app/{stream_key}",
        ]
        return args

    def _run_engine(self, games: list[dict], restart_every: int):
        """Headless stream engine — extracted from run_cast_stream logic."""
        self._running = True

        # ── Import steamcast internals ──
        sys.path.insert(0, str(Path(__file__).parent))
        from steamcast import find_ffmpeg

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            logger.error("ffmpeg not found. Cannot start streaming.")
            return

        # Determine end time: absolute schedule end (schedule.json) if set.
        # No duration-based stop — the schedule file is the only source of truth.
        start_time = datetime.now()
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
            # Resume: continue from the persisted playback position instead of
            # restarting at 00:00 on every daemon start. Offset wraps via modulo
            # when the video loops (duration probed once, cached).
            resume_offset = self._resume_offsets.get(gname, 0.0)
            duration = self._probe_duration(str(video))
            if duration and resume_offset >= duration:
                resume_offset = resume_offset % duration
                self._resume_offsets[gname] = resume_offset
            args = self._build_stream_args(ffmpeg, str(video), game.get("stream_key", ""), bitrate, resume_offset)
            if resume_offset > 0:
                self._log(f"⏯ {gname} resuming at {self._fmt_offset(resume_offset)} (of {self._fmt_offset(duration) if duration else '?'})")

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
                    "appid": game.get("appid", ""),
                    "started_at": datetime.now().isoformat(),
                    "resume_offset": resume_offset,
                    "duration": duration,
                    # PUSHED = transmitting to Steam RTMP; promoted to LIVE only
                    # once the storefront probe confirms visibility.
                    "status": "PUSHED",
                    "storefront": None,
                    "start_args": args,
                }
            time.sleep(2)

        self._log(f"All {len(self._active_streams)} streams launched.")

        # Storefront verification thread — promotes PUSHED → LIVE when the
        # storefront confirms the broadcast (or demotes LIVE → PUSHED if it
        # disappears). Runs continuously so status stays honest.
        verification_thread = Thread(target=self._storefront_loop, daemon=True)
        verification_thread.start()

        # ── Monitor loop ──
        while self._running:
            now = datetime.now()

            # Check absolute schedule end (schedule.json — re-read each tick)
            sched_end = _read_schedule_end()
            if sched_end and now >= sched_end:
                logger.info("Schedule end reached (%s). Stopping all streams.",
                            sched_end.strftime("%Y-%m-%d %H:%M"))
                self._stop_all_streams("schedule_end")
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

                        # Resume: fold this incarnation's played time into the
                        # offset so the rebroadcast continues, not restarts.
                        self._accumulate_resume(gname, stream)
                        resume_offset = self._resume_offsets.get(gname, 0.0)

                        # Restart stream using freshly built args (with -ss seek)
                        try:
                            sys.path.insert(0, str(Path(__file__).parent))
                            from steamcast import find_ffmpeg
                            ffmpeg_path = find_ffmpeg()
                        except Exception:
                            ffmpeg_path = None
                        args = stream.get("start_args", [])
                        if ffmpeg_path and stream.get("video"):
                            args = self._build_stream_args(
                                ffmpeg_path,
                                stream["video"],
                                stream.get("stream_key", ""),
                                stream.get("bitrate", "5000k"),
                                resume_offset,
                            )
                        if args:
                            new_proc = subprocess.Popen(
                                args,
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                            )
                            stream["proc"] = new_proc
                            proc = new_proc  # update local ref for status check below
                            stream["start_args"] = args
                            # Reconnected = back to PUSHED until storefront re-confirms.
                            stream["status"] = "PUSHED"
                            stream["storefront"] = None
                            stream["started_at"] = datetime.now().isoformat()
                            stream["resume_offset"] = resume_offset
                            self._log(f"↻ {gname} reconnected (PID {new_proc.pid})")
                            if resume_offset > 0:
                                self._log(f"⏯ {gname} resuming at {self._fmt_offset(resume_offset)}")
                        else:
                            stream["status"] = "DEAD"
                            self._log(f"✗ {gname} — start args missing, cannot reconnect")

                    if proc and proc.poll() is None:
                        # ffmpeg alive → PUSHED floor; LIVE only via storefront probe
                        if stream.get("status") != "LIVE":
                            stream["status"] = "PUSHED"

            self._write_state()
            time.sleep(5)

        self._stop_all_streams("engine_stop")

    def _storefront_loop(self):
        """Continuously verify storefront visibility for active streams.

        Promotes PUSHED → LIVE when the anonymous Steam broadcast API confirms
        the stream is visible on the storefront, and demotes LIVE → PUSHED if
        the storefront stops showing it (while ffmpeg still transmits).

        Retry policy: a stream whose ``storefront`` dict is None has not been
        confirmed since its last (re)start — Steam's storefront registration
        lags the RTMP connect by ~5-40s, so those get probe_with_retries
        (benefit of the doubt). Once any probe result is recorded, later
        passes use a single probe. Transitions only happen on *clean* probes:
        a probe error (timeout, 5xx, bad JSON) says nothing about storefront
        visibility, so it never demotes a LIVE stream (or promotes one).
        """
        while self._running:
            with self._streams_lock:
                targets = [
                    (gname, stream, stream.get("proc"), stream.get("storefront") is None)
                    for gname, stream in self._active_streams.items()
                    if stream.get("proc") and stream["proc"].poll() is None
                ]
            if not targets:
                time.sleep(10)
                continue

            for gname, stream, proc_ref, unconfirmed in targets:
                if not self._running:
                    break
                key = stream.get("stream_key", "")
                if not key:
                    continue
                try:
                    if unconfirmed:
                        result = probe_with_retries(key, attempts=3, delay=10.0)
                    else:
                        result = probe_stream(key)
                except Exception as e:
                    logger.warning("Storefront probe failed for %s: %s", gname, e)
                    continue

                online = bool(result.get("online"))
                err = result.get("error")
                # Per-app page context: Steam tags the broadcast to the app the
                # account is CURRENTLY active in, so the tag can wander while
                # the stream is fine. Informational — never transitions.
                cfg_appid = str(stream.get("appid", "") or "")
                probe_appid = str(result.get("appid") or "")
                # Parked = the account is tagged to a DIFFERENT game than the
                # one this key is configured for (e.g. a delegated user playing
                # another title). Appid match means the broadcast is on our
                # game — even if the hub page renders stripped to anonymous
                # visitors (dreadout 2's hub does this), the API is the truth.
                parked = bool(online and not err and cfg_appid and probe_appid and probe_appid != cfg_appid)
                page = None
                page_err = None
                if online and not err:
                    pg = probe_page(cfg_appid, result.get("steamid"))
                    page = pg.get("on_page")
                    page_err = pg.get("error")
                    if parked:
                        self._log(
                            f"📍 {gname} tag on '{result.get('title') or probe_appid}' page — "
                            f"not {cfg_appid} (delegated user active?)"
                        )
                with self._streams_lock:
                    # The monitor loop may have reconnected this stream (new
                    # Popen object) while we probed — this result describes
                    # the previous incarnation. Discard it: storefront stays
                    # None so the next pass re-probes with retries.
                    if stream.get("proc") is not proc_ref:
                        continue
                    if online and not err and stream.get("status") == "PUSHED":
                        stream["status"] = "LIVE"
                        self._log(f"✅ {gname} LIVE on storefront (confirmed by probe)")
                    elif not online and not err and stream.get("status") == "LIVE":
                        stream["status"] = "PUSHED"
                        self._log(f"⚠ {gname} no longer visible on storefront — back to PUSHED")
                    stream["storefront"] = {
                        "online": online,
                        "appid": result.get("appid"),
                        "title": result.get("title"),
                        "resolution": result.get("resolution"),
                        "bandwidth_kbps": result.get("bandwidth_kbps"),
                        "hls_url": result.get("hls_url"),
                        "error": result.get("error"),
                        "on_page": page,
                        "page_error": page_err,
                        "parked": parked,
                    }

            time.sleep(30)

    def _kill_all_streams(self):
        """Kill all ffmpeg processes. Existing reconnect logic will respawn them."""
        with self._streams_lock:
            for gname, stream in list(self._active_streams.items()):
                proc = stream.get("proc")
                if proc and proc.poll() is None:
                    # Fold played time into resume offset before killing —
                    # reconnect respawns with -ss and the broadcast continues.
                    self._accumulate_resume(gname, stream)
                    proc.kill()
                    logger.info("Killed stream: %s (PID %d)", gname, proc.pid)
                    # Back to PUSHED + unconfirmed until the storefront
                    # re-verifies the restarted ingest (reconnect would also
                    # reset these, but don't show a stale LIVE meanwhile).
                    stream["status"] = "PUSHED"
                    stream["storefront"] = None
        self._log("♻ All streams killed — reconnect will pick them up.")
        time.sleep(2)

    def _stop_all_streams(self, reason: str):
        """Terminate all streams permanently."""
        logger.info("Stopping all streams (reason: %s)", reason)
        with self._streams_lock:
            for gname, stream in list(self._active_streams.items()):
                proc = stream.get("proc")
                if proc and proc.poll() is None:
                    # Persist final position so the next scheduled window
                    # resumes where this one stopped.
                    self._accumulate_resume(gname, stream)
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

    @staticmethod
    def _fmt_offset(seconds: Optional[float]) -> str:
        """Format seconds as H:MM:SS (or '?' when unknown)."""
        if seconds is None:
            return "?"
        seconds = max(0, int(seconds))
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}"

    def _accumulate_resume(self, gname: str, stream: dict):
        """Add this incarnation's played time to the game's resume offset.

        Called when a stream process dies (before respawn) or is being
        stopped permanently. The offset is capped/wrapped to the video
        duration when known so looping stays seamless.
        """
        duration = stream.get("duration")
        base = stream.get("resume_offset", 0.0) or 0.0
        try:
            started = datetime.fromisoformat(stream["started_at"])
            played = max(0.0, (datetime.now() - started).total_seconds())
        except Exception:
            played = 0.0
        offset = base + played
        if duration:
            offset = offset % duration
        self._resume_offsets[gname] = offset

    def _write_state(self):
        """Write current state to JSON file for external tools."""
        with self._streams_lock:
            # Project live positions: base offset + elapsed since process start,
            # so even a hard kill (SIGKILL/power loss) loses at most 5s of
            # position instead of the whole current incarnation.
            resume = {g: round(v, 3) for g, v in self._resume_offsets.items()}
            now = datetime.now()
            for gname, s in self._active_streams.items():
                if s.get("proc") and s["proc"].poll() is None:
                    base = s.get("resume_offset", 0.0) or 0.0
                    try:
                        started = datetime.fromisoformat(s["started_at"])
                        played = max(0.0, (now - started).total_seconds())
                    except Exception:
                        played = 0.0
                    duration = s.get("duration")
                    projected = base + played
                    if duration:
                        projected = projected % duration
                    resume[gname] = round(projected, 3)
            state = {
                "pid": os.getpid(),
                "uptime_seconds": int(time.time() - self._start_time) if self._start_time else 0,
                "resume": resume,
                "streams": {
                    gname: {
                        "status": s.get("status", "UNKNOWN"),
                        "bitrate": s.get("bitrate", "?"),
                        "started_at": s.get("started_at", ""),
                        "pid": s.get("proc", None) and (s["proc"].pid or None),
                        "appid": s.get("appid", ""),
                        "resume_offset": s.get("resume_offset", 0.0),
                        "storefront": s.get("storefront"),
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
                    # Build the payload under the lock, but send the response
                    # (socket I/O) outside it — a stalled HTTP client would
                    # otherwise block the monitor and storefront threads.
                    with daemon._streams_lock:
                        payload = {
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
                                    "appid": s.get("appid", ""),
                                    "resume_offset": round(s.get("resume_offset", 0.0) or 0.0, 1),
                                    "storefront": s.get("storefront"),
                                }
                                for gname, s in daemon._active_streams.items()
                            ],
                        }
                    self._send_json(payload)
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
        self._server.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    def serve_forever(self):
        self._server.serve_forever()


# ── CLI Entry Points ──


def cmd_start(config: dict | None = None, foreground: bool = False):
    """Start the daemon (called from steamcast.py).

    foreground=True: skip double-fork, run under systemd supervision.
    """
    mgr = DaemonManager(config)
    mgr.start(foreground=foreground)  # let DaemonError propagate to caller


def cmd_stop():
    """Stop the daemon."""
    mgr = DaemonManager()
    mgr.stop()  # let DaemonError propagate to caller


def cmd_status() -> dict:
    """Show daemon status."""
    mgr = DaemonManager()
    return mgr.status()


def _read_schedule_end():
    """Read absolute schedule end from ~/.steamcast/schedule.json (or None)."""
    sched_path = Path.home() / ".steamcast" / "schedule.json"
    try:
        if not sched_path.exists():
            return None
        data = json.loads(sched_path.read_text())
        end_str = data.get("end")
        if not end_str:
            return None
        return datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def load_config() -> dict:
    """Load daemon config, merging TUI config with daemon overrides.

    Reads:
      1. ~/projects/steamcast/config.json  (TUI — game names + RTMP keys)
      2. ~/.steamcast/config.json           (override — restart_every, duration, extra games)

    Auto-discovers video files from ~/projects/steamcast/output/<name>.mp4.
    Only active games from the TUI config are included.
    """
    config: dict = {"games": [], "restart_every_hours": 4}

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
            "appid": gdata.get("appid", ""),
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
