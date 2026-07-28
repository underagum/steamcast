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
from tkinter import filedialog, messagebox, ttk

# Add project root to path for steamcast imports
sys.path.insert(0, str(Path(__file__).resolve().parent))


class SteamCastGUI:
    """Main application window — tabbed interface."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SteamCast")
        self.root.geometry("720x520")
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

        # ── Tabs ──
        self._build_setup_tab()
        self._build_prep_tab()

        # ── Status bar ──
        self.status = ttk.Label(
            self.root,
            text="Ready",
            font=("Segoe UI", 8),
        )
        self.status.pack(side="bottom", fill="x", padx=5, pady=3)

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

        ttk.Button(btn_frame, text="Add Game", command=self._add_game).pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Paste Key", command=self._paste_key).pack(side="left", padx=(0, 8))
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

    def _add_game(self):
        """Open dialog to add a new game with RTMP key."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Add Game")
        dialog.geometry("400x150")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        tk.Label(dialog, text="Game name:", font=("Segoe UI", 9)).pack(pady=(15, 0))
        name_entry = tk.Entry(dialog, width=45)
        name_entry.pack(pady=(2, 5))
        name_entry.focus_set()

        tk.Label(dialog, text="RTMP key:", font=("Segoe UI", 9)).pack()
        key_entry = tk.Entry(dialog, width=45, show="•")
        key_entry.pack(pady=(2, 10))

        def _save():
            gname = name_entry.get().strip()
            rtmp = key_entry.get().strip()
            if not gname:
                return
            self.config.setdefault("games", {})[gname] = {
                "rtmp_key": rtmp,
                "active": False,
            }
            self._save_config()
            self._refresh_tree()
            self._set_status(f"Added: {gname}")
            dialog.destroy()

        ttk.Button(dialog, text="Add", command=_save).pack()
        dialog.bind("<Return>", lambda e: _save())

    def _paste_key(self):
        """Paste RTMP key from clipboard into selected row."""
        selected = self.tree.selection()
        if not selected:
            messagebox.showwarning("No Selection", "Select a game row first.")
            return
        try:
            clipboard = self.root.clipboard_get()
        except tk.TclError:
            messagebox.showwarning("Clipboard Empty", "Copy an RTMP key first.")
            return

        gname = self.tree.item(selected[0], "values")[0]
        self.config["games"][gname]["rtmp_key"] = clipboard
        self._save_config()
        self._refresh_tree()
        self._set_status(f"Key pasted for: {gname}")

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
            text="Drop video files into the input folder, then click CONVERT.",
            font=("Segoe UI", 9),
            foreground="#aaaaaa",
        ).pack(pady=(15, 5), padx=20, anchor="w")

        # Show paths
        paths_frame = ttk.Frame(tab)
        paths_frame.pack(padx=20, pady=(0, 5), fill="x")
        ttk.Label(paths_frame, text=f"📁 Input:", font=("Segoe UI", 8), foreground="#888").pack(side="left")
        ttk.Label(paths_frame, text=str(self.input_dir), font=("Segoe UI", 8), foreground="#666").pack(side="left", padx=(5, 0))
        ttk.Label(paths_frame, text=f"  📁 Output:", font=("Segoe UI", 8), foreground="#888").pack(side="left", padx=(20, 0))
        ttk.Label(paths_frame, text=str(self.output_dir), font=("Segoe UI", 8), foreground="#666").pack(side="left", padx=(5, 0))

        file_frame = ttk.Frame(tab)
        file_frame.pack(padx=20, pady=5, fill="x")

        self.file_list = tk.Listbox(
            file_frame,
            bg="#2a2a4a",
            fg="#cccccc",
            selectbackground="#5555aa",
            height=6,
        )
        self.file_list.pack(side="left", fill="both", expand=True)

        ttk.Button(file_frame, text="Browse", command=self._browse_files).pack(
            side="right", padx=(10, 0)
        )

        bitrate_frame = ttk.Frame(tab)
        bitrate_frame.pack(padx=20, pady=10, fill="x")

        ttk.Label(bitrate_frame, text="Bitrate:", font=("Segoe UI", 9)).pack(side="left")
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

        ttk.Button(tab, text="CONVERT", command=self._run_prep).pack(pady=(5, 5))

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

    def _browse_files(self):
        """Open file picker for video files."""
        files = filedialog.askopenfilenames(
            title="Select video files",
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.webm"), ("All files", "*.*")],
        )
        for f in files:
            self.file_list.insert("end", f)
        if files:
            self._set_status(f"{len(files)} file(s) selected")

    def _run_prep(self):
        """Placeholder — PREP not wired yet."""
        self.prep_log.insert("end", "🔍 PREP not wired yet — coming in Phase 2\n")
        self.prep_progress["value"] = 0

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
