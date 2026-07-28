"""
SteamCast GUI — Windows
Requires: tkinter (built-in), rich, psutil
Status: Phase 1 — Setup tab wired, PREP tab placeholder

Design:
  - GUI wraps existing steamcast.py logic as library calls
  - No rewrite — all steamcast internals stay untouched
  - TUI remains available via steamcast.py --cli
"""

import json
import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

# Add project root to path for steamcast imports
sys.path.insert(0, str(Path(__file__).resolve().parent))


class SteamCastGUI:
    """Main application window — tabbed interface."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SteamCast")
        self.root.geometry("800x680")
        self.root.minsize(720, 560)
        self.root.resizable(True, True)

        # ── Project root + folders ──
        self.project_dir = Path(__file__).resolve().parent
        self.input_dir = self.project_dir / "input"
        self.output_dir = self.project_dir / "output"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # ── Load config ──
        self.config = self._load_config()

        # ── Style ──
        self.root.configure(bg="#1a1a2e")
        style = ttk.Style()
        style.theme_use("clam")

        # ── Title bar ──
        header = tk.Label(
            self.root,
            text="STEAMCAST",
            font=("Segoe UI", 18, "bold"),
            fg="#00d4ff",
            bg="#1a1a2e",
        )
        header.pack(pady=(15, 5))

        sub = tk.Label(
            self.root,
            text="Steam broadcast video prep & cast",
            font=("Segoe UI", 9),
            fg="#888888",
            bg="#1a1a2e",
        )
        sub.pack(pady=(0, 15))

        # ── Tab bar ──
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=15, pady=(0, 10))

        # ── Status bar ──
        self.status = ttk.Label(
            self.root,
            text="Ready",
            font=("Segoe UI", 8),
        )
        self.status.pack(side="bottom", fill="x", padx=5, pady=3)

        # ── Tabs ──
        self._build_setup_tab()
        self._build_prep_tab()

    # ────────────────────────────────────────────────────────
    # Config I/O
    # ────────────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """Load config.json using steamcast's own loader."""
        try:
            from steamcast import load_config as _load
            return _load()
        except ImportError:
            cfg_path = Path(__file__).resolve().parent / "config.json"
            if cfg_path.exists():
                return json.loads(cfg_path.read_text())
            return {"version": "1.0.0", "games": {}}

    def _save_config(self) -> bool:
        """Save config.json using steamcast's own saver."""
        try:
            from steamcast import save_config as _save
            return _save(self.config)
        except ImportError:
            cfg_path = Path(__file__).resolve().parent / "config.json"
            cfg_path.write_text(json.dumps(self.config, indent=2))
            return True

    # ────────────────────────────────────────────────────────
    # Tab 1: Setup (RTMP Keys)
    # ────────────────────────────────────────────────────────

    def _build_setup_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Setup (Keys)")

        # Instructions
        ttk.Label(
            tab,
            text="RTMP keys from Steamworks → Store Page → Broadcast Settings",
            font=("Segoe UI", 9),
            foreground="#aaaaaa",
        ).pack(pady=(15, 10), padx=20, anchor="w")

        # Game list (treeview)
        columns = ("Game", "Key", "Active")
        self.tree = ttk.Treeview(tab, columns=columns, show="headings", height=8)
        self.tree.heading("Game", text="Game")
        self.tree.heading("Key", text="RTMP Key")
        self.tree.heading("Active", text="Active")
        self.tree.column("Game", width=180)
        self.tree.column("Key", width=280)
        self.tree.column("Active", width=60)
        self.tree.pack(padx=20, pady=5, fill="x")

        # Populate from config
        self._refresh_tree()

        # Double-click to toggle active
        self.tree.bind("<Double-1>", self._toggle_active)

        # Buttons
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(padx=20, pady=10, fill="x")

        ttk.Button(btn_frame, text="Add Game", command=lambda: self._game_dialog()).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Edit", command=self._edit_game).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Remove", command=self._remove_game).pack(side="left")

    def _refresh_tree(self):
        """Reload treeview from self.config."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        games = self.config.get("games", {})
        for gname, gdata in games.items():
            active = "✓" if gdata.get("active", False) else "✗"
            rtmp = gdata.get("rtmp_key", "")
            masked = rtmp[:12] + "…" if len(rtmp) > 12 else rtmp
            self.tree.insert("", "end", values=(gname, masked, active))

    def _game_dialog(self, edit_name: str | None = None):
        """Open dialog to add or edit a game and its RTMP key."""
        is_edit = edit_name is not None
        existing_key = ""
        if is_edit:
            existing_key = self.config["games"].get(edit_name, {}).get("rtmp_key", "")

        dialog = tk.Toplevel(self.root)
        dialog.title("Edit Game" if is_edit else "Add Game")
        dialog.geometry("400x150")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Game name:", font=("Segoe UI", 9)).pack(pady=(15, 0))
        name_entry = tk.Entry(dialog, width=45)
        name_entry.pack(pady=(2, 5))
        if is_edit:
            name_entry.insert(0, edit_name)
        name_entry.focus_set()

        tk.Label(dialog, text="RTMP key:", font=("Segoe UI", 9)).pack()
        key_entry = tk.Entry(dialog, width=45, show="•")
        key_entry.pack(pady=(2, 10))
        if existing_key:
            key_entry.insert(0, existing_key)

        def _save():
            new_name = name_entry.get().strip()
            rtmp = key_entry.get().strip()
            if not new_name:
                return
            games = self.config.setdefault("games", {})
            if is_edit and new_name != edit_name:
                games.pop(edit_name, None)
            games[new_name] = {"rtmp_key": rtmp, "active": games.get(new_name, {}).get("active", False)}
            self._save_config()
            self._refresh_tree()
            self._set_status(f"{'Updated' if is_edit else 'Added'}: {new_name}")
            dialog.destroy()

        ttk.Button(dialog, text="Save", command=_save).pack()
        dialog.bind("<Return>", lambda e: _save())

    def _edit_game(self):
        """Open edit dialog for selected game."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Select a game row first.")
            return
        gname = self.tree.item(selected[0], "values")[0]
        self._game_dialog(edit_name=gname)

    def _remove_game(self):
        """Remove selected game from config and tree."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Select a game row first.")
            return
        gname = self.tree.item(selected[0], "values")[0]
        if messagebox.askyesno("Confirm", f'Remove "{gname}"?'):
            self.config["games"].pop(gname, None)
            self._save_config()
            self._refresh_tree()
            self._set_status(f"Removed: {gname}")

    def _toggle_active(self, event):
        """Double-click a row to toggle active/inactive."""
        selected = self.tree.selection()
        if not selected:
            return
        gname = self.tree.item(selected[0], "values")[0]
        current = self.config["games"].get(gname, {}).get("active", False)
        self.config["games"][gname]["active"] = not current
        self._save_config()
        self._refresh_tree()

    # ────────────────────────────────────────────────────────
    # Tab 2: Prep Videos (placeholder)
    # ────────────────────────────────────────────────────────

    def _build_prep_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="PREP")

        ttk.Label(
            tab,
            text="Drop video files into the input folder — detected automatically.",
            font=("Segoe UI", 9),
            foreground="#aaaaaa",
        ).pack(pady=(15, 5), padx=20, anchor="w")

        # Naming guide
        info_text = (
            "Naming:  gamename.mp4 (single)   |   gamename_1.mp4, gamename_2.mp4 (multi-part)\n"
            "Output:  H.264, AAC, 1080p30, CBR at selected bitrate. Ready for Steam broadcast."
        )
        ttk.Label(
            tab, text=info_text,
            font=("Segoe UI", 8), foreground="#666666",
        ).pack(padx=20, pady=(0, 5), anchor="w")

        # Show paths
        paths_frame = ttk.Frame(tab)
        paths_frame.pack(padx=20, pady=(0, 5), fill="x")
        ttk.Label(paths_frame, text="📁 Input:", font=("Segoe UI", 8), foreground="#888").pack(side="left")
        ttk.Label(paths_frame, text=str(self.input_dir), font=("Segoe UI", 8), foreground="#666").pack(side="left", padx=(5, 0))
        ttk.Label(paths_frame, text="  📁 Output:", font=("Segoe UI", 8), foreground="#888").pack(side="left", padx=(20, 0))
        ttk.Label(paths_frame, text=str(self.output_dir), font=("Segoe UI", 8), foreground="#666").pack(side="left", padx=(5, 0))

        # Detected games treeview
        prep_cols = ("Game", "Files", "Status")
        self.prep_tree = ttk.Treeview(tab, columns=prep_cols, show="headings", height=8)
        self.prep_tree.heading("Game", text="Game")
        self.prep_tree.heading("Files", text="Files")
        self.prep_tree.heading("Status", text="Status")
        self.prep_tree.column("Game", width=200)
        self.prep_tree.column("Files", width=100)
        self.prep_tree.column("Status", width=250)
        self.prep_tree.pack(padx=20, pady=5, fill="x")

        # Refresh button
        ttk.Button(tab, text="↻ Refresh", command=self._scan_input).pack(pady=(0, 5))

        # Bitrate selector
        bitrate_frame = ttk.Frame(tab)
        bitrate_frame.pack(padx=20, pady=10, fill="x")

        ttk.Label(bitrate_frame, text="Encoding bitrate:", font=("Segoe UI", 9)).pack(side="left")
        self.bitrate_var = tk.StringVar(value="5000k")
        ttk.OptionMenu(
            bitrate_frame,
            self.bitrate_var,
            "5000k",
            "3500k",
            "4000k",
            "5000k",
            "6000k",
            "7000k",
        ).pack(side="left", padx=(10, 0))

        # Progress + convert button
        ttk.Button(tab, text="[ CONVERT ALL ]", command=self._run_prep).pack(pady=(5, 5))

        self.prep_progress = ttk.Progressbar(
            tab, orient="horizontal", length=300, mode="determinate"
        )
        self.prep_progress.pack(pady=5)

        self.prep_log = tk.Text(
            tab,
            bg="#0d0d1a",
            fg="#00cc88",
            font=("Consolas", 8),
            height=6,
            wrap="word",
        )
        self.prep_log.pack(padx=20, pady=5, fill="x")

        # Initial scan
        self._scan_input()

        # Auto-refresh when tab is selected
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _on_tab_change(self, event):
        """Auto-scan when switching to PREP tab."""
        tab_id = self.notebook.select()
        tab_text = self.notebook.tab(tab_id, "text")
        if tab_text == "PREP":
            self._scan_input()

    def _scan_input(self):
        """Scan input/ folder and populate the PREP treeview."""
        # Import steamcast internals for consistent detection
        try:
            from steamcast import VIDEO_EXTENSIONS, parse_game_name, sanitize_filename
        except ImportError:
            VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".wmv", ".webm", ".flv", ".m4v"}

            def parse_game_name(filename):
                import re
                m = re.match(r"^(.+?)(?:_(\d+))?\.\w+$", filename, re.IGNORECASE)
                if not m:
                    return filename.rsplit(".", 1)[0], 0, False
                return m.group(1), int(m.group(2) or 1), bool(m.group(2))

            def sanitize_filename(name):
                return "".join(c if c.isalnum() or c in " _-" else "_" for c in name)

        # Clear tree
        for item in self.prep_tree.get_children():
            self.prep_tree.delete(item)

        # Scan input/ for video files
        video_files: list[Path] = []
        for ext in VIDEO_EXTENSIONS:
            video_files.extend(self.input_dir.rglob(f"*{ext}"))
            video_files.extend(self.input_dir.rglob(f"*{ext.upper()}"))
        video_files = sorted(set(video_files))

        if not video_files:
            self.prep_tree.insert("", "end", values=(
                "—", "No video files found", f"Drop files into {self.input_dir.name}/"
            ))
            self._set_status("No videos in input/ folder")
            self.prep_games = {}
            return

        # Group by game name
        game_groups: dict[str, list[Path]] = {}
        for f in video_files:
            gname, _, _ = parse_game_name(f.name)
            game_groups.setdefault(gname, []).append(f)

        self.prep_games = game_groups  # store for _run_prep

        # Populate treeview
        for gname in sorted(game_groups):
            files = sorted(game_groups[gname], key=lambda f: f.name)
            safe = sanitize_filename(gname)
            out_path = self.output_dir / f"{safe}.mp4"
            action = f"convert + concat ({len(files)} files)" if len(files) > 1 else "convert only"
            status = "✅ Ready" if out_path.exists() else "🔄 Needs conversion"
            self.prep_tree.insert("", "end", values=(gname, action, status))

        self._set_status(f"{len(video_files)} file(s) across {len(game_groups)} game(s)")

    def _run_prep(self):
        """Run ffmpeg conversion for all detected game groups (background thread)."""
        if not self.prep_games:
            messagebox.showwarning("No Videos", "Drop video files into the input folder first.")
            return

        # Disable convert button during run
        for child in self.root.winfo_children():
            if isinstance(child, ttk.Frame):
                for w in child.winfo_children():
                    if isinstance(w, ttk.Button) and "CONVERT" in str(w.cget("text")):
                        w.config(state="disabled")

        self.prep_log.delete("1.0", "end")
        self.prep_progress["value"] = 0
        self._prep_queue = []  # thread-safe log queue
        self._prep_running = True

        import threading
        t = threading.Thread(target=self._prep_worker, daemon=True)
        t.start()
        self._prep_poll_queue()

    def _prep_log_to_gui(self, text: str):
        """Called from worker thread or poll loop — thread-safe via root.after."""
        self.prep_log.insert("end", text)
        self.prep_log.see("end")

    def _prep_poll_queue(self):
        """Poll the worker's log queue and push to GUI every 100ms."""
        while self._prep_queue:
            msg = self._prep_queue.pop(0)
            self._prep_log_to_gui(msg)
        if self._prep_running:
            self.root.after(100, self._prep_poll_queue)
        else:
            # Re-enable convert button
            for child in self.root.winfo_children():
                if isinstance(child, ttk.Frame):
                    for w in child.winfo_children():
                        if isinstance(w, ttk.Button) and "CONVERT" in str(w.cget("text")):
                            w.config(state="normal")
            self._scan_input()

    def _prep_worker(self):
        """Background thread: run ffmpeg conversion for each game group."""
        try:
            from steamcast import (
                convert_video, concat_videos, detect_encoder, find_ffmpeg,
                sanitize_filename, get_video_duration, has_audio_stream,
                LOG_DIR,
            )
            from pathlib import Path as _Path
            import uuid, shutil, subprocess, re as _re

            LOG_DIR.mkdir(parents=True, exist_ok=True)

            # Find ffmpeg
            ffmpeg = find_ffmpeg()
            if not ffmpeg:
                self._prep_queue.append("❌ FFmpeg not found.\n")
                self._prep_running = False
                return

            # Detect encoder
            enc = detect_encoder(None)  # None = no rich console
            if enc is None:
                self._prep_queue.append("❌ No usable encoder found.\n")
                self._prep_running = False
                return

            bitrate = self.bitrate_var.get().replace("k", "")
            from steamcast import SPEC
            SPEC.video_bitrate = f"{bitrate}k"

            total_groups = len(self.prep_games)
            group_idx = 0
            success_count = 0
            fail_count = 0
            cancelled = False

            for gname in sorted(self.prep_games):
                if not self._prep_running:
                    cancelled = True
                    break

                files = sorted(self.prep_games[gname], key=lambda f: f.name)
                safe_name = sanitize_filename(gname)
                out_path = self.output_dir / f"{safe_name}.mp4"
                group_idx += 1

                # Skip if already exists
                if out_path.exists():
                    self._prep_queue.append(f"⏭ {gname} — already exists, skipping\n")
                    self.prep_progress["value"] = (group_idx / total_groups) * 100
                    success_count += 1
                    continue

                if len(files) == 1:
                    # ── Single file ──
                    prep_log = LOG_DIR / f"{safe_name}_prep.log"
                    has_audio = has_audio_stream(files[0])
                    dur = get_video_duration(files[0])
                    self._prep_queue.append(f"🎬 {gname} ({dur}) — converting...\n")

                    def on_progress(raw):
                        self._prep_queue.append(raw + "\n")

                    try:
                        ok = convert_video(files[0], out_path, enc,
                                          log_file=prep_log,
                                          on_progress=on_progress,
                                          has_audio=has_audio)
                    except Exception as e:
                        self._prep_queue.append(f"  ❌ Error: {e}\n")
                        ok = False

                    if ok:
                        self._prep_queue.append(f"  ✅ {gname} converted\n")
                        success_count += 1
                    else:
                        self._prep_queue.append(f"  ❌ Failed: {gname} \n")
                        fail_count += 1

                else:
                    # ── Multi-file: convert each part, then concat ──
                    temp_dir = self.input_dir / f".temp_{uuid.uuid4().hex[:8]}"
                    temp_dir.mkdir(parents=True, exist_ok=True)

                    has_audio = has_audio_stream(files[0])
                    converted_parts = []
                    all_ok = True

                    for idx, f in enumerate(files, 1):
                        if not self._prep_running:
                            cancelled = True
                            break

                        temp_out = temp_dir / f"{f.stem}_steam.mp4"
                        part_log = LOG_DIR / f"{safe_name}_part{idx}_prep.log"
                        self._prep_queue.append(f"  🎬 Part {idx}/{len(files)}: {f.name}...\n")

                        def on_progress(raw):
                            self._prep_queue.append(raw + "\n")

                        try:
                            ok = convert_video(f, temp_out, enc,
                                              log_file=part_log,
                                              on_progress=on_progress,
                                              has_audio=has_audio)
                        except Exception as e:
                            self._prep_queue.append(f"    ❌ Error: {e}\n")
                            ok = False

                        if ok:
                            self._prep_queue.append(f"    ✅ Part {idx} done\n")
                            converted_parts.append(temp_out)
                        else:
                            self._prep_queue.append(f"    ❌ Part {idx} failed\n")
                            all_ok = False
                            break

                    if not cancelled and all_ok:
                        # Create playlist + concat
                        playlist = temp_dir / "playlist.txt"
                        with open(playlist, "w") as pf:
                            for cf in converted_parts:
                                pf.write(f"file '{str(cf).replace(chr(92), '/')}'\n")

                        concat_log = LOG_DIR / f"{safe_name}_concat.log"
                        self._prep_queue.append(f"  🔗 Concatenating {len(converted_parts)} parts...\n")

                        def on_progress(raw):
                            self._prep_queue.append(raw + "\n")

                        try:
                            ok = concat_videos(playlist, out_path, enc,
                                              log_file=concat_log,
                                              on_progress=on_progress,
                                              has_audio=has_audio)
                        except Exception as e:
                            self._prep_queue.append(f"    ❌ Error: {e}\n")
                            ok = False

                        if ok:
                            self._prep_queue.append(f"  ✅ {gname} ready ({len(files)} parts merged)\n")
                            success_count += 1
                        else:
                            self._prep_queue.append(f"  ❌ Concat failed: {gname} \n")
                            fail_count += 1
                    elif cancelled:
                        shutil.rmtree(temp_dir, ignore_errors=True)
                        break
                    else:
                        fail_count += 1

                    # Cleanup temp
                    shutil.rmtree(temp_dir, ignore_errors=True)

                # Update progress bar
                self.prep_progress["value"] = (group_idx / total_groups) * 100

            # Summary
            self._prep_queue.append(f"\n{'='*50}\n")
            self._prep_queue.append(f"  ✅ {success_count} converted  |  ❌ {fail_count} failed")
            if cancelled:
                self._prep_queue.append("  ⚠ Cancelled")
            self._prep_queue.append(f"\n  Output: {self.output_dir}\n")

        except Exception as e:
            self._prep_queue.append(f"\n❌ PREP error: {e}\n")
        finally:
            self._prep_running = False

    # ────────────────────────────────────────────────────────
    # Helpers
    # ────────────────────────────────────────────────────────

    def _set_status(self, msg: str):
        self.status.config(text=msg)

    # ────────────────────────────────────────────────────────
    # Run
    # ────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = SteamCastGUI()
    app.run()
