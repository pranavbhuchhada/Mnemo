#!/usr/bin/env python3
"""Android backup tool using ADB — incremental export of selected phone folders to local PC."""

import subprocess
import sys
import os
import threading
import time
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import ctypes

try:
    import pystray
    from PIL import Image as PilImage
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor DPI aware
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()   # fallback for older Windows
    except Exception:
        pass


COMMON_FOLDERS = [
    "/sdcard/DCIM",
    "/sdcard/Pictures",
    "/sdcard/Movies",
    "/sdcard/Music",
    "/sdcard/Documents",
    "/sdcard/Download",
    "/sdcard/Android/media/com.whatsapp/WhatsApp",
]

FOLDER_LABELS = {
    "/sdcard/DCIM": "DCIM (Camera)",
    "/sdcard/Pictures": "Pictures",
    "/sdcard/Movies": "Movies",
    "/sdcard/Music": "Music",
    "/sdcard/Documents": "Documents",
    "/sdcard/Download": "Download",
    "/sdcard/Android/media/com.whatsapp/WhatsApp": "WhatsApp",
}

SKIP_DIRS = {".thumbnails", "thumbnails", ".thumb"}
SKIP_EXTENSIONS = {".db", ".nomedia"}
WHATSAPP_SENT_DIRS = {"sent"}


# ---------------------------------------------------------------------------
# ADB core
# ---------------------------------------------------------------------------

def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace")


def check_adb() -> bool:
    try:
        return run(["adb", "version"]).returncode == 0
    except FileNotFoundError:
        return False


def get_connected_device() -> str | None:
    result = run(["adb", "devices"])
    lines = result.stdout.strip().splitlines()
    devices = [l for l in lines[1:] if l.strip() and "device" in l and "offline" not in l]
    return devices[0].split()[0] if devices else None


def get_device_name() -> str | None:
    try:
        manufacturer = run(["adb", "shell", "getprop ro.product.manufacturer"]).stdout.strip()
        model        = run(["adb", "shell", "getprop ro.product.model"]).stdout.strip()
        if manufacturer and model:
            if model.lower().startswith(manufacturer.lower()):
                return model
            return f"{manufacturer.capitalize()} {model}"
        return model or manufacturer or None
    except Exception:
        return None


def get_remote_files(src: str, skip_whatsapp_sent: bool = False,
                     skip_hidden: bool = True) -> list[tuple[str, int]]:
    result = run(["adb", "shell", f"find '{src}' -type f -printf '%s %p\\n' 2>/dev/null"])
    if not result.stdout.strip():
        return []

    files = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        size_str, _, path = line.partition(" ")
        try:
            size = int(size_str)
        except ValueError:
            continue

        parts = path.replace("\\", "/").split("/")
        if any(p.lower() in SKIP_DIRS for p in parts):
            continue
        if skip_hidden and any(p.startswith(".") for p in parts):
            continue
        if skip_whatsapp_sent and any(p.lower() in WHATSAPP_SENT_DIRS for p in parts):
            continue
        if os.path.splitext(path)[1].lower() in SKIP_EXTENSIONS:
            continue

        files.append((path, size))
    return files


def incremental_pull(src: str, dest: str, log, progress_cb,
                     skip_whatsapp_sent: bool = False,
                     skip_hidden: bool = True,
                     stop_event: threading.Event = None) -> tuple[int, int, int]:
    folder_name = src.rstrip("/").split("/")[-1]
    local_base = os.path.join(dest, folder_name)

    log(f"Scanning {src}...")
    remote_files = get_remote_files(src, skip_whatsapp_sent=skip_whatsapp_sent,
                                    skip_hidden=skip_hidden)

    if not remote_files:
        log(f"  No files found in {src}")
        return 0, 0, 0

    to_pull = []
    skipped = 0
    for remote_path, remote_size in remote_files:
        rel = remote_path[len(src):].lstrip("/")
        local_path = os.path.join(local_base, rel.replace("/", os.sep))
        if os.path.exists(local_path) and os.path.getsize(local_path) == remote_size:
            skipped += 1
        else:
            to_pull.append((remote_path, rel, local_path))

    total = len(remote_files)
    log(f"  {total} files — {skipped} already backed up, {len(to_pull)} to pull")

    if not to_pull:
        return 0, skipped, 0

    pulled = failed = 0
    width = len(str(len(to_pull)))
    start_time = time.monotonic()
    for i, (remote_path, rel, local_path) in enumerate(to_pull, 1):
        if stop_event and stop_event.is_set():
            log("  Stopped.")
            break
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        result = subprocess.run(
            ["adb", "pull", remote_path, local_path],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        if result.returncode == 0:
            pulled += 1
            log(f"  [{i:>{width}}/{len(to_pull)}] {rel}")
        else:
            failed += 1
            log(f"  [{i:>{width}}/{len(to_pull)}] FAILED: {rel}")
        elapsed = time.monotonic() - start_time
        rate = i / elapsed if elapsed > 0 else 0
        eta = int((len(to_pull) - i) / rate) if rate > 0 else 0
        progress_cb(i, len(to_pull), eta)

    return pulled, skipped, failed


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
_config_dir = os.path.join(_appdata, "Mnemo")
os.makedirs(_config_dir, exist_ok=True)
SETTINGS_FILE = os.path.join(_config_dir, "settings.json")


def load_settings() -> dict:
    try:
        with open(SETTINGS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_settings(data: dict):
    try:
        with open(SETTINGS_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

BG       = "#0f0f1a"
SURFACE  = "#1a1a2e"
PANEL    = "#22223a"
BORDER   = "#36365a"
ACCENT   = "#818cf8"
TEXT     = "#e2e8f0"
MUTED    = "#64748b"
SUCCESS  = "#34d399"
DANGER   = "#f87171"
WARN     = "#fbbf24"
LOG_BG   = "#09090f"
INPUT    = "#1e1e32"
BTN_PRI  = "#4f46e5"
BTN_SEC  = "#252540"
BTN_STOP = "#7f1d1d"


def _hover(btn, enter_bg, leave_bg):
    btn.bind("<Enter>", lambda _: btn.configure(bg=enter_bg))
    btn.bind("<Leave>", lambda _: btn.configure(bg=leave_bg))


# ---------------------------------------------------------------------------
# Phone folder browser dialog
# ---------------------------------------------------------------------------

class PhoneBrowserDialog(tk.Toplevel):
    def __init__(self, parent, start_path="/sdcard"):
        super().__init__(parent)
        self.title("Browse Phone Folders")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.geometry("480x420")
        self.transient(parent)
        self.grab_set()

        self.current_path = start_path
        self.selected_path = None

        self._build_ui()
        self._load(self.current_path)

    def _build_ui(self):
        top = tk.Frame(self, bg=SURFACE, pady=8)
        top.pack(fill="x")
        tk.Label(top, text="📂", bg=SURFACE, fg=ACCENT,
                 font=("Segoe UI", 11)).pack(side="left", padx=(12, 4))
        self.path_var = tk.StringVar()
        tk.Entry(top, textvariable=self.path_var, font=("Consolas", 9),
                 bg=INPUT, fg=TEXT, readonlybackground=INPUT,
                 insertbackground=TEXT, relief="flat",
                 state="readonly", bd=0, width=46).pack(side="left", padx=(0, 12), ipady=4)

        list_frame = tk.Frame(self, bg=BG)
        list_frame.pack(fill="both", expand=True, padx=12, pady=8)
        self.listbox = tk.Listbox(
            list_frame, font=("Consolas", 10),
            bg=PANEL, fg=TEXT, selectbackground=ACCENT, selectforeground=BG,
            activestyle="none", relief="flat", bd=0,
            highlightthickness=1, highlightcolor=BORDER, highlightbackground=BORDER,
        )
        sb = tk.Scrollbar(list_frame, bg=SURFACE, troughcolor=SURFACE,
                          activebackground=ACCENT, relief="flat", width=10)
        self.listbox.configure(yscrollcommand=sb.set)
        sb.configure(command=self.listbox.yview)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<Double-Button-1>", self._on_double_click)

        btn_frame = tk.Frame(self, bg=BG)
        btn_frame.pack(pady=(0, 12), padx=12, fill="x")

        up_btn = tk.Button(btn_frame, text="⬆  Up", font=("Segoe UI", 9),
                           bg=BTN_SEC, fg=TEXT, relief="flat", bd=0,
                           padx=12, pady=6, cursor="hand2", command=self._go_up)
        up_btn.pack(side="left", padx=(0, 6))
        _hover(up_btn, BORDER, BTN_SEC)

        sel_btn = tk.Button(btn_frame, text="✔  Select This Folder",
                            font=("Segoe UI", 9, "bold"),
                            bg=BTN_PRI, fg="white", relief="flat", bd=0,
                            padx=14, pady=6, cursor="hand2", command=self._select)
        sel_btn.pack(side="left", padx=(0, 6))
        _hover(sel_btn, ACCENT, BTN_PRI)

        cancel_btn = tk.Button(btn_frame, text="Cancel", font=("Segoe UI", 9),
                               bg=BTN_SEC, fg=MUTED, relief="flat", bd=0,
                               padx=12, pady=6, cursor="hand2", command=self.destroy)
        cancel_btn.pack(side="left")
        _hover(cancel_btn, BORDER, BTN_SEC)

    def _load(self, path):
        self.current_path = path
        self.path_var.set(path)
        self.listbox.delete(0, "end")
        self.listbox.insert("end", "  ⏳ loading...")
        self.update()

        result = run(["adb", "shell", f"ls -d '{path}'/*/  2>/dev/null"])
        self.listbox.delete(0, "end")

        dirs = []
        for line in result.stdout.splitlines():
            line = line.strip().rstrip("/")
            if line:
                dirs.append(line)

        if not dirs:
            self.listbox.insert("end", "  (no subfolders)")
        for d in sorted(dirs):
            name = d.split("/")[-1]
            if not name.startswith("."):
                self.listbox.insert("end", f"  📁 {name}")

    def _on_double_click(self, _event):
        sel = self.listbox.curselection()
        if not sel:
            return
        name = self.listbox.get(sel[0]).strip().replace("📁 ", "").strip()
        if not name or name.startswith("(") or name.startswith("⏳"):
            return
        self._load(f"{self.current_path.rstrip('/')}/{name}")

    def _go_up(self):
        parent = "/".join(self.current_path.rstrip("/").split("/")[:-1]) or "/"
        self._load(parent)

    def _select(self):
        sel = self.listbox.curselection()
        if sel:
            name = self.listbox.get(sel[0]).strip().replace("📁 ", "").strip()
            if name and not name.startswith("(") and not name.startswith("⏳"):
                self.selected_path = f"{self.current_path.rstrip('/')}/{name}"
                self.destroy()
                return
        self.selected_path = self.current_path
        self.destroy()


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        try:
            _ver = open("VERSION").read().strip()
        except Exception:
            _ver = ""
        self.title(f"Mnemo {_ver}" if _ver else "Mnemo")
        self.configure(bg=BG)
        self.resizable(False, False)
        try:
            from PIL import Image as _Img, ImageTk as _ITk
            _img = _Img.open("mnemo.png").resize((64, 64), _Img.LANCZOS)
            self._icon_photo = _ITk.PhotoImage(_img)
            self.iconphoto(True, self._icon_photo)
        except Exception:
            try:
                self.iconbitmap("mnemo.ico")
            except Exception:
                pass
        self._stop_event = threading.Event()
        self._tray_icon = None
        self._style_ttk()
        self._build_ui()
        self._check_adb_on_start()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if TRAY_AVAILABLE:
            self._setup_tray()

    def _style_ttk(self):
        style = ttk.Style(self)
        style.theme_use("default")
        style.configure("TProgressbar",
                        troughcolor=SURFACE, background=ACCENT,
                        darkcolor=ACCENT, lightcolor=ACCENT,
                        bordercolor=SURFACE, thickness=8)

    def _card(self, parent, title=""):
        wrapper = tk.Frame(parent, bg=BG)
        wrapper.pack(fill="x", padx=16, pady=(0, 10))
        if title:
            tk.Label(wrapper, text=title.upper(), bg=BG, fg=MUTED,
                     font=("Segoe UI", 7, "bold")).pack(anchor="w", pady=(0, 4))
        inner = tk.Frame(wrapper, bg=PANEL, highlightthickness=1,
                         highlightbackground=BORDER, highlightcolor=BORDER)
        inner.pack(fill="x")
        return inner

    def _btn(self, parent, text, command, primary=False, danger=False, **kw):
        bg    = BTN_PRI if primary else (BTN_STOP if danger else BTN_SEC)
        hover = "#6366f1" if primary else ("#991b1b" if danger else BORDER)
        fg    = "white" if (primary or danger) else TEXT
        b = tk.Button(parent, text=text, command=command,
                      bg=bg, fg=fg, relief="flat", bd=0,
                      font=("Segoe UI", 9, "bold" if primary else "normal"),
                      padx=14, pady=7, cursor="hand2", **kw)
        _hover(b, hover, bg)
        return b

    def _chk(self, parent, text, var, small=False):
        return tk.Checkbutton(parent, text=text, variable=var,
                              bg=PANEL, fg=MUTED if small else TEXT,
                              selectcolor=ACCENT,
                              activebackground=PANEL, activeforeground=TEXT,
                              disabledforeground=MUTED,
                              font=("Segoe UI", 8 if small else 9),
                              indicatoron=True)

    def _build_ui(self):
        # ── Header ──────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=SURFACE, pady=14)
        hdr.pack(fill="x")
        tk.Label(hdr, text="Mnemo", bg=SURFACE, fg=TEXT,
                 font=("Segoe UI", 18, "bold")).pack(side="left", padx=(20, 6))
        tk.Label(hdr, text="Android → PC backup", bg=SURFACE, fg=MUTED,
                 font=("Segoe UI", 9)).pack(side="left", pady=6)
        self.status_dot = tk.Label(hdr, text="●", bg=SURFACE, fg=DANGER,
                                   font=("Segoe UI", 13))
        self.status_dot.pack(side="right", padx=(0, 16))
        self.status_label = tk.Label(hdr, bg=SURFACE, fg=MUTED, font=("Segoe UI", 9))
        self.status_label.pack(side="right")
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── Two-column body ──────────────────────────────────────────────────
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, pady=(14, 0))

        left  = tk.Frame(body, bg=BG)
        left.pack(side="left", fill="y", anchor="n")
        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)

        # ── Folders (left column) ────────────────────────────────────────────
        fc = self._card(left, "Phone Folders")
        self.folder_vars   = {}
        self.skip_sent_var = tk.BooleanVar(value=True)
        self.skip_hidden_var = tk.BooleanVar(value=True)

        for folder in COMMON_FOLDERS:
            var = tk.BooleanVar(value=True)
            self._chk(fc, FOLDER_LABELS.get(folder, folder), var).pack(
                anchor="w", padx=12, pady=1)
            self.folder_vars[folder] = var
            if "whatsapp" in folder.lower():
                self._chk(fc, "    ↳ Skip Sent", self.skip_sent_var, small=True).pack(
                    anchor="w", padx=12)

        tk.Frame(fc, bg=BORDER, height=1).pack(fill="x", padx=12, pady=6)
        self._chk(fc, "Skip hidden files", self.skip_hidden_var, small=True).pack(
            anchor="w", padx=12, pady=(0, 10))

        # ── Destination (right column) ───────────────────────────────────────
        dc = self._card(right, "Destination")
        dest_row = tk.Frame(dc, bg=PANEL)
        dest_row.pack(fill="x", padx=12, pady=10)
        self.dest_var = tk.StringVar(value=load_settings().get("last_destination", ""))
        self.dest_var.trace_add("write", lambda *_: save_settings({"last_destination": self.dest_var.get().strip()}))
        tk.Entry(dest_row, textvariable=self.dest_var, bg=INPUT, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=0,
                 font=("Segoe UI", 9), width=32).pack(side="left", ipady=5, padx=(0, 8))
        self._btn(dest_row, "Browse…", self._browse_dest).pack(side="left")

        # ── Additional folders (right column) ────────────────────────────────
        ec = self._card(right, "Additional Phone Folders")
        self.custom_listbox = tk.Listbox(
            ec, height=3, font=("Consolas", 8),
            bg=INPUT, fg=TEXT, selectbackground=ACCENT, selectforeground=BG,
            relief="flat", bd=0, highlightthickness=0,
        )
        self.custom_listbox.pack(fill="x", padx=12, pady=(8, 4))
        add_row = tk.Frame(ec, bg=PANEL)
        add_row.pack(fill="x", padx=12, pady=(0, 10))
        self.custom_var = tk.StringVar()
        tk.Entry(add_row, textvariable=self.custom_var, bg=INPUT, fg=TEXT,
                 insertbackground=TEXT, relief="flat", bd=0,
                 font=("Segoe UI", 9), width=20).pack(side="left", ipady=5, padx=(0, 6))
        self._btn(add_row, "Browse…", self._browse_phone_folder).pack(side="left", padx=(0, 4))
        self._btn(add_row, "Add",     self._add_custom_folder).pack(side="left", padx=(0, 4))
        self._btn(add_row, "✕",       self._remove_custom_folder, danger=True).pack(side="left")

        # ── Log (right column) ───────────────────────────────────────────────
        lc = self._card(right, "Log")
        log_inner = tk.Frame(lc, bg=PANEL)
        log_inner.pack(fill="both", expand=True, padx=12, pady=(4, 10))
        self.log_box = tk.Text(
            log_inner, height=11, font=("Consolas", 8),
            bg=LOG_BG, fg=TEXT, insertbackground=TEXT,
            relief="flat", bd=0, state="disabled",
            highlightthickness=1, highlightcolor=BORDER, highlightbackground=BORDER,
        )
        self.log_box.tag_config("pulled",  foreground=SUCCESS)
        self.log_box.tag_config("skipped", foreground=MUTED)
        self.log_box.tag_config("failed",  foreground=DANGER)
        self.log_box.tag_config("header",  foreground=ACCENT)
        sb = tk.Scrollbar(log_inner, bg=SURFACE, troughcolor=LOG_BG,
                          activebackground=ACCENT, relief="flat", width=8)
        self.log_box.configure(yscrollcommand=sb.set)
        sb.configure(command=self.log_box.yview)
        self.log_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ── Progress ─────────────────────────────────────────────────────────
        prog_wrap = tk.Frame(self, bg=BG)
        prog_wrap.pack(fill="x", padx=16, pady=(8, 0))
        prog_top = tk.Frame(prog_wrap, bg=BG)
        prog_top.pack(fill="x", pady=(0, 4))
        self.progress_label = tk.Label(prog_top, text="", bg=BG, fg=TEXT,
                                       font=("Segoe UI", 9))
        self.progress_label.pack(side="left")
        self.eta_label = tk.Label(prog_top, text="", bg=BG, fg=MUTED,
                                  font=("Segoe UI", 9))
        self.eta_label.pack(side="right")
        self.progress = ttk.Progressbar(prog_wrap, mode="determinate", style="TProgressbar")
        self.progress.pack(fill="x", ipady=2)

        # ── Action bar ───────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x", pady=(10, 0))
        bar = tk.Frame(self, bg=SURFACE, pady=10)
        bar.pack(fill="x")
        self.start_btn = self._btn(bar, "▶  Start Backup", self._start_backup, primary=True)
        self.start_btn.pack(side="left", padx=(16, 6))
        self.stop_btn = self._btn(bar, "■  Stop", self._stop_backup, danger=True)
        self.stop_btn.configure(state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 6))
        self._btn(bar, "↺  Refresh Device", self._check_adb_on_start).pack(side="left")
        if TRAY_AVAILABLE:
            self._btn(bar, "⊟  Minimize to Tray", self._on_close).pack(side="right", padx=(0, 16))

    def _setup_tray(self):
        try:
            img = PilImage.open("mnemo.ico")
        except Exception:
            img = PilImage.new("RGBA", (64, 64), (79, 70, 229, 255))
        menu = pystray.Menu(
            pystray.MenuItem("Show Mnemo", self._tray_restore, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._tray_quit),
        )
        self._tray_icon = pystray.Icon("Mnemo", img, "Mnemo", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _tray_restore(self, icon=None, item=None):
        self.after(0, self.deiconify)
        self.after(0, self.lift)

    def _tray_quit(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self.destroy)

    def _tray_notify(self, title: str, msg: str):
        if self._tray_icon:
            try:
                self._tray_icon.notify(msg, title)
            except Exception:
                pass

    def _on_close(self):
        if TRAY_AVAILABLE and self._tray_icon:
            self.withdraw()
        else:
            if self._tray_icon:
                self._tray_icon.stop()
            self.destroy()

    def _browse_dest(self):
        path = filedialog.askdirectory(title="Select destination folder")
        if path:
            self.dest_var.set(path)

    def _browse_phone_folder(self):
        dialog = PhoneBrowserDialog(self)
        self.wait_window(dialog)
        if dialog.selected_path:
            self.custom_var.set(dialog.selected_path)
            self._add_custom_folder()

    def _add_custom_folder(self):
        path = self.custom_var.get().strip()
        if not path:
            return
        if path not in list(self.custom_listbox.get(0, "end")):
            self.custom_listbox.insert("end", path)
        self.custom_var.set("")

    def _remove_custom_folder(self):
        sel = self.custom_listbox.curselection()
        if sel:
            self.custom_listbox.delete(sel[0])

    def _log(self, msg: str):
        self.log_box.configure(state="normal")
        m = msg.strip()
        if m.startswith("[") and "FAILED" not in m:
            tag = "pulled"
        elif "FAILED" in m:
            tag = "failed"
        elif "already backed up" in m:
            tag = "skipped"
        elif m.startswith("=") or m.startswith("Scanning") or m in ("Starting backup...", "Done.", "Stopped."):
            tag = "header"
        else:
            tag = ""
        self.log_box.insert("end", msg + "\n", tag)
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _set_progress(self, current: int, total: int, eta: int = 0):
        self.progress["maximum"] = total
        self.progress["value"] = current
        pct = int(current / total * 100) if total else 0
        self.progress_label.configure(text=f"{current} / {total} files  ({pct}%)")
        if eta > 0:
            m, s = divmod(eta, 60)
            self.eta_label.configure(text=f"ETA {m}m {s:02d}s" if m else f"ETA {s}s")
        else:
            self.eta_label.configure(text="")

    def _stop_backup(self):
        self._stop_event.set()
        self.stop_btn.configure(state="disabled")
        self._log("  Stopping after current file...")

    def _check_adb_on_start(self):
        if not check_adb():
            self.status_label.configure(text="ADB not found — install Platform Tools")
            self.status_dot.configure(fg=DANGER)
            self.start_btn.configure(state="disabled")
            return
        device = get_connected_device()
        if device:
            name = get_device_name() or device
            self.status_label.configure(text=f"Connected: {name}")
            self.status_dot.configure(fg=SUCCESS)
            self.start_btn.configure(state="normal")
        else:
            self.status_label.configure(text="No device — connect & enable USB Debugging")
            self.status_dot.configure(fg=WARN)
            self.start_btn.configure(state="disabled")

    def _start_backup(self):
        dest = self.dest_var.get().strip()
        if not dest:
            messagebox.showwarning("No destination", "Please select a destination folder.")
            return
        folders = [f for f, var in self.folder_vars.items() if var.get()]
        folders += list(self.custom_listbox.get(0, "end"))
        if not folders:
            messagebox.showwarning("No folders", "Please select at least one folder to back up.")
            return
        os.makedirs(dest, exist_ok=True)
        self._stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress["value"] = 0
        self.progress_label.configure(text="")
        self.eta_label.configure(text="")
        threading.Thread(target=self._run_backup, args=(folders, dest), daemon=True).start()

    def _run_backup(self, folders: list[str], dest: str):
        total_pulled = total_skipped = total_failed = 0
        self._log("=" * 48)
        self._log("  Starting backup...")
        self._log("=" * 48)
        skip_sent   = self.skip_sent_var.get()
        skip_hidden = self.skip_hidden_var.get()
        for folder in folders:
            if self._stop_event.is_set():
                break
            pulled, skipped, failed = incremental_pull(
                folder, dest,
                log=lambda m: self.after(0, self._log, m),
                progress_cb=lambda c, t, e=0: self.after(0, self._set_progress, c, t, e),
                skip_whatsapp_sent=skip_sent,
                skip_hidden=skip_hidden,
                stop_event=self._stop_event,
            )
            total_pulled  += pulled
            total_skipped += skipped
            total_failed  += failed
        self._log("")
        self._log("=" * 48)
        self._log(f"  Pulled (new):     {total_pulled}")
        self._log(f"  Skipped (exists): {total_skipped}")
        if total_failed:
            self._log(f"  Failed:           {total_failed}")
        self._log("  Stopped." if self._stop_event.is_set() else "  Done.")
        self._log("=" * 48)
        self.after(0, self.start_btn.configure, {"state": "normal"})
        self.after(0, self.stop_btn.configure,  {"state": "disabled"})
        self.after(0, self.eta_label.configure,  {"text": ""})
        if TRAY_AVAILABLE:
            self._tray_notify("Mnemo", "Backup complete!" if not self._stop_event.is_set() else "Backup stopped.")


if __name__ == "__main__":
    App().mainloop()
