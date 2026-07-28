"""
SteamCast GUI — Prototype (Windows)
Requires: tkinter (built-in), steamcast core modules
Status: POC / rough sketch — not functional yet

Design notes:
  - GUI wraps existing steamcast.py logic as library calls
  - No rewrite — all steamcast internals stay untouched
  - Current TUI remains available via steamcast.py --cli
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


class SteamCastGUI:
    """Main application window — tabbed interface."""

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("SteamCast")
        self.root.geometry("720×520")
        self.root.resizable(True, True)

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
        self._build_daemon_tab()

        # ── Status bar ──
        self.status = ttk.Label(
            self.root,
            text="🔵 Daemon inactive | 0 streams",
            font=("Segoe UI", 8),
        )
        self.status.pack(side="bottom", fill="x", padx=5, pady=3)

    # ────────────────────────────────────────────────────────
    # Tab 1: Setup (RTMP Keys)
    # ────────────────────────────────────────────────────────

    def _build_setup_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Setup (Keys)")

        # Instructions
        ttk.Label(
            tab,
            text="Paste your RTMP keys from Steamworks → Store Page → Broadcast Settings",
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
        self.tree.column("Key", width=300)
        self.tree.column("Active", width=60)
        self.tree.pack(padx=20, pady=5, fill="x")

        # Buttons
        btn_frame = ttk.Frame(tab)
        btn_frame.pack(padx=20, pady=10, fill="x")

        ttk.Button(btn_frame, text="Add Game", command=self._add_game).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="Paste Key", command=self._paste_key).pack(side="left", padx=(0, 10))
        ttk.Button(btn_frame, text="Remove", command=self._remove_game).pack(side="left")

    def _add_game(self):
        """Sandbox: open a dialog to add a game name."""
        messagebox.showinfo("Add Game", "Dialog: Enter game name + paste RTMP key (not wired yet)")

    def _paste_key(self):
        """Sandbox: paste from clipboard into selected row."""
        messagebox.showinfo("Paste Key", "Would auto-paste RTMP key from clipboard into selected row")

    def _remove_game(self):
        """Sandbox: remove selected game."""
        selected = self.tree.selection()
        if selected:
            self.tree.delete(selected[0])

    # ────────────────────────────────────────────────────────
    # Tab 2: Prep Videos
    # ────────────────────────────────────────────────────────

    def _build_prep_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="PREP")

        # File picker
        ttk.Label(
            tab,
            text="Select input videos (drag-and-drop friendly on Windows):",
            font=("Segoe UI", 9),
            foreground="#aaaaaa",
        ).pack(pady=(15, 5), padx=20, anchor="w")

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

        # Bitrate selector
        bitrate_frame = ttk.Frame(tab)
        bitrate_frame.pack(padx=20, pady=10, fill="x")

        ttk.Label(bitrate_frame, text="Bitrate:", font=("Segoe UI", 9)).pack(side="left")
        self.bitrate_var = tk.StringVar(value="5000k")
        bitrate_menu = ttk.OptionMenu(
            bitrate_frame,
            self.bitrate_var,
            "5000k",
            "3500k",
            "4000k",
            "5000k",
            "6000k",
            "7000k",
        )
        bitrate_menu.pack(side="left", padx=(10, 0))

        # Progress + go button
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
        """Sandbox: open file picker to select video files."""
        files = filedialog.askopenfilenames(
            title="Select video files",
            filetypes=[("Video files", "*.mp4 *.mkv *.avi *.mov *.webm"), ("All files", "*.*")],
        )
        for f in files:
            self.file_list.insert("end", f)

    def _run_prep(self):
        """Sandbox: trigger PREP pipeline."""
        self.prep_log.insert("end", "🔍 Scanning input files...\n")
        self.prep_progress["value"] = 0
        messagebox.showinfo("PREP", "Would run: ffmpeg convert + concat (not wired yet)")

    # ────────────────────────────────────────────────────────
    # Tab 3: Daemon
    # ────────────────────────────────────────────────────────

    def _build_daemon_tab(self):
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Daemon")

        ttk.Label(
            tab,
            text="Background streaming daemon — runs 24/7 without the GUI open",
            font=("Segoe UI", 9),
            foreground="#aaaaaa",
        ).pack(pady=(15, 10), padx=20, anchor="w")

        btn_frame = ttk.Frame(tab)
        btn_frame.pack(padx=20, pady=10, fill="x")

        ttk.Button(btn_frame, text="Start Daemon", command=self._daemon_start).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(btn_frame, text="Stop Daemon", command=self._daemon_stop).pack(
            side="left", padx=(0, 10)
        )
        ttk.Button(btn_frame, text="Live Dashboard", command=self._daemon_attach).pack(
            side="left"
        )

        # Stream status preview
        self.daemon_tree = ttk.Treeview(
            tab,
            columns=("Game", "Status", "Bitrate", "Uptime"),
            show="headings",
            height=6,
        )
        self.daemon_tree.heading("Game", text="Game")
        self.daemon_tree.heading("Status", text="Status")
        self.daemon_tree.heading("Bitrate", text="Bitrate")
        self.daemon_tree.heading("Uptime", text="Uptime")
        self.daemon_tree.column("Game", width=180)
        self.daemon_tree.column("Status", width=80)
        self.daemon_tree.column("Bitrate", width=80)
        self.daemon_tree.column("Uptime", width=100)
        self.daemon_tree.pack(padx=20, pady=10, fill="x")

    def _daemon_start(self):
        messagebox.showinfo("Daemon", "Would run: steamcast daemon start (not wired yet)")

    def _daemon_stop(self):
        messagebox.showinfo("Daemon", "Would run: steamcast daemon stop (not wired yet)")

    def _daemon_attach(self):
        messagebox.showinfo("Daemon", "Would open: live dashboard window")

    # ────────────────────────────────────────────────────────
    # Run
    # ────────────────────────────────────────────────────────

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = SteamCastGUI()
    app.run()
