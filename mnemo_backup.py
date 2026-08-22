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

def _asset(name: str) -> str:
    """Resolve a bundled asset path for both dev and PyInstaller onefile."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)

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

# Brand-specific folders keyed by manufacturer name (lowercase substring match)
BRAND_FOLDERS: dict[str, dict[str, str]] = {
    "samsung": {
        "/sdcard/Android/media/com.samsung.android.messaging": "Samsung Messages",
        "/sdcard/Ringtones": "Ringtones",
    },
    "xiaomi": {
        "/sdcard/MIUI/Gallery": "MIUI Gallery",
        "/sdcard/Android/media/com.miui.gallery": "Mi Gallery",
    },
    "huawei": {
        "/sdcard/Android/media/com.huawei.himovie.overseas": "Huawei Video",
        "/sdcard/Huawei/Themes": "Huawei Themes",
    },
    "oneplus": {
        "/sdcard/Android/media/com.oneplus.gallery": "OnePlus Gallery",
    },
    "oppo": {
        "/sdcard/Android/media/com.coloros.gallery3d": "OPPO Gallery",
    },
    "vivo": {
        "/sdcard/Android/media/com.vivo.gallery": "Vivo Gallery",
    },
    "google": {
        "/sdcard/Android/media/com.google.android.apps.photos": "Google Photos",
    },
}


# ---------------------------------------------------------------------------
# ADB core
# ---------------------------------------------------------------------------

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# Global ppadb device handle — set once at startup
_device = None


def _start_adb_server():
    """Ensure adb server is running (one subprocess call, no window)."""
    subprocess.run(
        ["adb", "start-server"],
        capture_output=True,
        creationflags=_NO_WINDOW,
    )


def check_adb() -> bool:
    try:
        from ppadb.client import Client as AdbClient
        _start_adb_server()
        client = AdbClient(host="127.0.0.1", port=5037)
        client.version()
        return True
    except Exception:
        return False


def get_connected_device():
    """Return ppadb device handle or None."""
    try:
        from ppadb.client import Client as AdbClient
        _start_adb_server()
        client  = AdbClient(host="127.0.0.1", port=5037)
        devices = client.devices()
        return devices[0] if devices else None
    except Exception:
        return None


def get_brand_folders(manufacturer: str) -> dict[str, str]:
    """Return brand-specific {path: label} for the given manufacturer string."""
    m = manufacturer.lower()
    for brand, folders in BRAND_FOLDERS.items():
        if brand in m:
            return folders
    return {}


def get_device_name(device=None) -> str | None:
    try:
        d = device or get_connected_device()
        if not d:
            return None
        manufacturer = d.shell("getprop ro.product.manufacturer").strip()
        model        = d.shell("getprop ro.product.model").strip()
        if manufacturer and model:
            if model.lower().startswith(manufacturer.lower()):
                return model
            return f"{manufacturer.capitalize()} {model}"
        return model or manufacturer or None
    except Exception:
        return None


def get_remote_files(device, src: str, skip_whatsapp_sent: bool = False,
                     skip_hidden: bool = True) -> list[tuple[str, int]]:
    try:
        output = device.shell(f"find '{src}' -type f -printf '%s %p\\n' 2>/dev/null")
    except Exception:
        return []

    if not output.strip():
        return []

    files = []
    for line in output.splitlines():
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
        rel_parts = path[len(src):].lstrip("/").replace("\\", "/").split("/")
        if skip_hidden and any(p.startswith(".") for p in rel_parts):
            continue
        if skip_whatsapp_sent and any(p.lower() in WHATSAPP_SENT_DIRS for p in parts):
            continue
        if os.path.splitext(path)[1].lower() in SKIP_EXTENSIONS:
            continue

        files.append((path, size))
    return files


def incremental_pull(device, src: str, dest: str, log, progress_cb,
                     skip_whatsapp_sent: bool = False,
                     skip_hidden: bool = True,
                     stop_event: threading.Event = None,
                     global_offset: int = 0,
                     global_total: int = 0,
                     _prefetched=None) -> tuple[int, int, int]:
    folder_name = src.rstrip("/").split("/")[-1]
    local_base  = os.path.join(dest, folder_name)

    if _prefetched is not None:
        remote_files = _prefetched
        log(f"Scanning {src}...")
    else:
        log(f"Scanning {src}...")
        remote_files = get_remote_files(device, src,
                                        skip_whatsapp_sent=skip_whatsapp_sent,
                                        skip_hidden=skip_hidden)

    if not remote_files:
        log(f"  No files found in {src}")
        return 0, 0, 0

    to_pull = []
    skipped = 0
    for remote_path, remote_size in remote_files:
        rel        = remote_path[len(src):].lstrip("/")
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
    width      = len(str(len(to_pull)))
    start_time = time.monotonic()

    for i, (remote_path, rel, local_path) in enumerate(to_pull, 1):
        if stop_event and stop_event.is_set():
            log("  Stopped.")
            break
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        try:
            device.pull(remote_path, local_path)
            pulled += 1
            log(f"  [{i:>{width}}/{len(to_pull)}] {rel}")
        except Exception as e:
            err = str(e)
            failed += 1
            log(f"  [{i:>{width}}/{len(to_pull)}] FAILED: {rel} ({err})")
            if "closed" in err.lower() or "connection" in err.lower() or "broken pipe" in err.lower():
                log("  Device disconnected — aborting.")
                break
        elapsed = time.monotonic() - start_time
        rate    = i / elapsed if elapsed > 0 else 0
        g_current = global_offset + i
        g_total   = global_total or len(to_pull)
        eta       = int((g_total - g_current) / rate) if rate > 0 else 0
        progress_cb(g_current, g_total, eta, rate)

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
        existing = load_settings()
        existing.update(data)
        with open(SETTINGS_FILE, "w") as f:
            json.dump(existing, f, indent=2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Color palette
# ---------------------------------------------------------------------------

BG       = "#f8f9fb"
SURFACE  = "#ffffff"
PANEL    = "#f1f3f7"
BORDER   = "#dde1ea"
ACCENT   = "#4f46e5"
TEXT     = "#1e1e2e"
MUTED    = "#6b7280"
SUCCESS  = "#059669"
DANGER   = "#dc2626"
WARN     = "#d97706"
LOG_BG   = "#f8f9fb"
INPUT    = "#ffffff"
BTN_PRI  = "#4f46e5"
BTN_SEC  = "#e9eaf0"
BTN_STOP = "#fef2f2"


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

        self._device      = getattr(parent, "_adb_device", None)
        _shv = getattr(parent, "skip_hidden_var", None)
        self._skip_hidden = _shv.get() if _shv else True
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
        threading.Thread(target=self._load_worker, args=(path,), daemon=True).start()

    def _load_worker(self, path):
        dirs = []
        if self._device:
            try:
                output = self._device.shell(f"ls -d '{path}'/*/  2>/dev/null")
                for line in output.splitlines():
                    line = line.strip().rstrip("/")
                    if line:
                        dirs.append(line)
            except Exception:
                pass
        self.after(0, self._load_done, dirs)

    def _load_done(self, dirs):
        self.listbox.delete(0, "end")
        if not dirs:
            self.listbox.insert("end", "  (no subfolders)")
        for d in sorted(dirs):
            name = d.split("/")[-1]
            if self._skip_hidden and name.startswith("."):
                continue
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
            _ver = open(_asset("VERSION")).read().strip()
        except Exception:
            _ver = ""
        self.title(f"Mnemo {_ver}" if _ver else "Mnemo")
        self.configure(bg=BG)
        self.resizable(False, False)
        try:
            from PIL import Image as _Img, ImageTk as _ITk
            _img = _Img.open(_asset("mnemo.png")).resize((64, 64), _Img.LANCZOS)
            self._icon_photo = _ITk.PhotoImage(_img)
            self.iconphoto(True, self._icon_photo)
        except Exception:
            try:
                self.iconbitmap(_asset("mnemo.ico"))
            except Exception:
                pass
        self._stop_event  = threading.Event()
        self._tray_icon   = None
        self._adb_device  = None
        self._style_ttk()
        self._build_ui()
        self._is_first_adb_check = True
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
        hover = "#6366f1" if primary else ("#fee2e2" if danger else BORDER)
        fg    = "white" if primary else (DANGER if danger else TEXT)
        b = tk.Button(parent, text=text, command=command,
                      bg=bg, fg=fg, relief="flat", bd=0,
                      font=("Segoe UI", 9, "bold" if primary else "normal"),
                      padx=14, pady=7, cursor="hand2", **kw)
        _hover(b, hover, bg)
        return b

    def _chk(self, parent, text, var, small=False):
        fg   = MUTED if small else TEXT
        font = ("Segoe UI", 8 if small else 9)
        frame = tk.Frame(parent, bg=PANEL, cursor="hand2")

        box_size = 14
        canvas = tk.Canvas(frame, width=box_size, height=box_size,
                           bg=PANEL, highlightthickness=0, bd=0)
        canvas.pack(side="left", padx=(0, 5))
        label = tk.Label(frame, text=text, bg=PANEL, fg=fg, font=font)
        label.pack(side="left")

        def _draw():
            canvas.delete("all")
            if var.get():
                canvas.create_rectangle(1, 1, box_size - 1, box_size - 1,
                                        fill=ACCENT, outline=ACCENT)
                # checkmark
                canvas.create_line(3, 7, 6, 11, 11, 3,
                                   fill="white", width=2, capstyle="round", joinstyle="round")
            else:
                canvas.create_rectangle(1, 1, box_size - 1, box_size - 1,
                                        fill=SURFACE, outline=BORDER)

        def _toggle(_event=None):
            var.set(not var.get())

        var.trace_add("write", lambda *_: _draw())
        for widget in (frame, canvas, label):
            widget.bind("<Button-1>", _toggle)

        _draw()
        # expose configure so callers can disable the widget
        frame.configure_state = lambda state: [
            w.configure(state=state) for w in (label,)
        ]
        return frame

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
        self.status_label.bind("<Button-1>", self._on_status_click)
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
        _s = load_settings()
        _saved_folders  = _s.get("selected_folders", {})
        _saved_custom   = _s.get("custom_folders", [])
        self.skip_sent_var   = tk.BooleanVar(value=_s.get("skip_sent", True))
        self.skip_hidden_var = tk.BooleanVar(value=_s.get("skip_hidden", True))

        for folder in COMMON_FOLDERS:
            default = _saved_folders.get(folder, True)
            var = tk.BooleanVar(value=default)
            self._chk(fc, FOLDER_LABELS.get(folder, folder), var).pack(
                anchor="w", padx=12, pady=1)
            self.folder_vars[folder] = var
            if "whatsapp" in folder.lower():
                self._chk(fc, "    ↳ Skip Sent", self.skip_sent_var, small=True).pack(
                    anchor="w", padx=12)

        # Brand-specific folders — populated on device connect
        self._brand_frame = tk.Frame(fc, bg=PANEL)
        self._brand_frame.pack(fill="x")
        self._brand_vars: dict[str, tk.BooleanVar] = {}

        tk.Frame(fc, bg=BORDER, height=1).pack(fill="x", padx=12, pady=6)
        self._chk(fc, "Skip hidden files", self.skip_hidden_var, small=True).pack(
            anchor="w", padx=12, pady=(0, 10))

        # ── Destination (right column) ───────────────────────────────────────
        dc = self._card(right, "Destination")
        dest_row = tk.Frame(dc, bg=PANEL)
        dest_row.pack(fill="x", padx=12, pady=10)
        self.dest_var = tk.StringVar(value=load_settings().get("last_destination", ""))
        dest_entry = tk.Entry(dest_row, textvariable=self.dest_var, bg=INPUT, fg=TEXT,
                              insertbackground=TEXT, relief="flat", bd=0,
                              font=("Segoe UI", 9), width=32)
        dest_entry.pack(side="left", ipady=5, padx=(0, 8))
        dest_entry.bind("<FocusOut>", lambda _: save_settings({"last_destination": self.dest_var.get().strip()}))
        dest_entry.bind("<Return>",   lambda _: save_settings({"last_destination": self.dest_var.get().strip()}))
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

        # Persist checkbox state on every toggle
        for var in list(self.folder_vars.values()) + [self.skip_sent_var, self.skip_hidden_var]:
            var.trace_add("write", lambda *_: self._save_folder_settings())

        # Restore saved custom folders
        for path in _saved_custom:
            self.custom_listbox.insert("end", path)

    def _setup_tray(self):
        try:
            img = PilImage.open(_asset("mnemo.png"))
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
            save_settings({"last_destination": path})

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
        self._save_folder_settings()

    def _remove_custom_folder(self):
        sel = self.custom_listbox.curselection()
        if sel:
            self.custom_listbox.delete(sel[0])
            self._save_folder_settings()

    def _save_folder_settings(self):
        save_settings({
            "selected_folders":       {f: v.get() for f, v in self.folder_vars.items()},
            "selected_brand_folders": {f: v.get() for f, v in self._brand_vars.items()},
            "custom_folders":         list(self.custom_listbox.get(0, "end")),
            "skip_sent":              self.skip_sent_var.get(),
            "skip_hidden":            self.skip_hidden_var.get(),
        })

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

    def _set_progress(self, current: int, total: int, eta: int = 0, rate: float = 0):
        self.progress["maximum"] = total
        self.progress["value"] = current
        pct = int(current / total * 100) if total else 0
        self.progress_label.configure(text=f"{current} / {total} files  ({pct}%)")
        parts = []
        if eta > 0:
            m, s = divmod(eta, 60)
            parts.append(f"ETA {m}m {s:02d}s" if m else f"ETA {s}s")
        if rate > 0:
            parts.append(f"{rate:.1f} files/s")
        self.eta_label.configure(text="  ".join(parts))

    def _stop_backup(self):
        self._stop_event.set()
        self.stop_btn.configure(state="disabled")
        self._log("  Stopping after current file...")

    def _populate_brand_folders(self, manufacturer: str):
        for w in self._brand_frame.winfo_children():
            w.destroy()
        self._brand_vars.clear()
        folders = get_brand_folders(manufacturer)
        if not folders:
            return
        tk.Frame(self._brand_frame, bg=BORDER, height=1).pack(fill="x", padx=12, pady=(0, 4))
        tk.Label(self._brand_frame, text=f"{manufacturer.capitalize()} folders".upper(),
                 bg=PANEL, fg=MUTED, font=("Segoe UI", 7, "bold")).pack(anchor="w", padx=12, pady=(0, 2))
        saved_brand = load_settings().get("selected_brand_folders", {})
        for path, label in folders.items():
            var = tk.BooleanVar(value=saved_brand.get(path, True))
            self._chk(self._brand_frame, label, var).pack(anchor="w", padx=12, pady=1)
            self._brand_vars[path] = var
            var.trace_add("write", lambda *_: self._save_folder_settings())

    def _show_adb_help(self):
        messagebox.showinfo(
            "ADB Not Found",
            "Mnemo requires Android Platform Tools (adb) to communicate with your phone.\n\n"
            "Setup steps:\n"
            "  1. Download Platform Tools from:\n"
            "     https://developer.android.com/tools/releases/platform-tools\n\n"
            "  2. Extract the zip and add the folder to your system PATH\n"
            "     (e.g. C:\\platform-tools)\n\n"
            "  3. On your phone: enable USB Debugging\n"
            "     Settings → Developer Options → USB Debugging\n\n"
            "  4. Reconnect your phone and restart Mnemo\n\n"
            "Verify it works by opening a terminal and running: adb devices",
        )

    def _on_status_click(self, _event=None):
        if "ADB not found" in self.status_label.cget("text"):
            self._show_adb_help()

    def _check_adb_on_start(self):
        self._adb_device = None
        self._populate_brand_folders("")
        self.status_label.configure(text="Checking ADB…")
        self.status_dot.configure(fg=MUTED)
        self.start_btn.configure(state="disabled")
        show_dialog = self._is_first_adb_check
        self._is_first_adb_check = False
        threading.Thread(target=self._check_adb_worker, args=(show_dialog,), daemon=True).start()

    def _check_adb_worker(self, show_dialog: bool):
        if not check_adb():
            self.after(0, self.status_label.configure, {"text": "ADB not found — see setup instructions",
                                                        "cursor": "hand2", "fg": ACCENT})
            self.after(0, self.status_dot.configure,   {"fg": DANGER})
            self.after(0, self.start_btn.configure,    {"state": "disabled"})
            if show_dialog:
                self.after(0, self._show_adb_help)
            return
        device = get_connected_device()
        if device:
            self._adb_device = device
            name = get_device_name(device) or str(device)
            manufacturer = ""
            try:
                manufacturer = device.shell("getprop ro.product.manufacturer").strip()
            except Exception:
                pass
            self.after(0, self.status_label.configure, {"text": f"Connected: {name}",
                                                        "cursor": "", "fg": MUTED})
            self.after(0, self.status_dot.configure,   {"fg": SUCCESS})
            self.after(0, self.start_btn.configure,    {"state": "normal"})
            self.after(0, self._populate_brand_folders, manufacturer)
        else:
            self.after(0, self.status_label.configure, {"text": "No device — connect & enable USB Debugging",
                                                        "cursor": "", "fg": MUTED})
            self.after(0, self.status_dot.configure,   {"fg": WARN})
            self.after(0, self.start_btn.configure,    {"state": "disabled"})

    def _start_backup(self):
        dest = self.dest_var.get().strip()
        if not dest:
            messagebox.showwarning("No destination", "Please select a destination folder.")
            return
        seen = set()
        folders = []
        for f in (
            [f for f, var in self.folder_vars.items() if var.get()]
            + [f for f, var in self._brand_vars.items() if var.get()]
            + list(self.custom_listbox.get(0, "end"))
        ):
            key = f.rstrip("/")
            if key not in seen:
                seen.add(key)
                folders.append(f)
        if not folders:
            messagebox.showwarning("No folders", "Please select at least one folder to back up.")
            return
        os.makedirs(dest, exist_ok=True)
        skip_sent   = self.skip_sent_var.get()
        skip_hidden = self.skip_hidden_var.get()
        self._stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress["value"] = 0
        self.progress_label.configure(text="")
        self.eta_label.configure(text="")
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")
        threading.Thread(target=self._run_backup, args=(folders, dest, skip_sent, skip_hidden), daemon=True).start()

    def _run_backup(self, folders: list[str], dest: str, skip_sent: bool, skip_hidden: bool):
        device = self._adb_device or get_connected_device()
        if not device:
            self.after(0, self._log, "ERROR: No device connected.")
            self.after(0, self.start_btn.configure, {"state": "normal"})
            self.after(0, self.stop_btn.configure,  {"state": "disabled"})
            return

        total_pulled = total_skipped = total_failed = 0
        self.after(0, self._log, "=" * 48)
        self.after(0, self._log, "  Starting backup...")
        self.after(0, self._log, "=" * 48)

        # Pre-scan to get global total for cross-folder progress bar
        all_remote = {}
        for folder in folders:
            files = get_remote_files(device, folder,
                                     skip_whatsapp_sent=skip_sent,
                                     skip_hidden=skip_hidden)
            all_remote[folder] = files
        def _needs_pull(folder, rp, rs):
            local = os.path.join(dest, folder.rstrip("/").split("/")[-1],
                                 rp[len(folder):].lstrip("/").replace("/", os.sep))
            try:
                return not (os.path.exists(local) and os.path.getsize(local) == rs)
            except OSError:
                return True

        global_total = sum(
            sum(1 for rp, rs in files if _needs_pull(folder, rp, rs))
            for folder, files in all_remote.items()
        ) or 1

        global_offset = 0
        for folder in folders:
            if self._stop_event.is_set():
                break
            pulled, skipped, failed = incremental_pull(
                device, folder, dest,
                log=lambda m: self.after(0, self._log, m),
                progress_cb=lambda c, t, e=0, r=0: self.after(0, self._set_progress, c, t, e, r),
                skip_whatsapp_sent=skip_sent,
                skip_hidden=skip_hidden,
                stop_event=self._stop_event,
                global_offset=global_offset,
                global_total=global_total,
                _prefetched=all_remote.get(folder),
            )
            global_offset += pulled + failed
            total_pulled  += pulled
            total_skipped += skipped
            total_failed  += failed
        self.after(0, self._log, "")
        self.after(0, self._log, "=" * 48)
        self.after(0, self._log, f"  Pulled (new):     {total_pulled}")
        self.after(0, self._log, f"  Skipped (exists): {total_skipped}")
        if total_failed:
            self.after(0, self._log, f"  Failed:           {total_failed}")
        self.after(0, self._log, "  Stopped." if self._stop_event.is_set() else "  Done.")
        self.after(0, self._log, "=" * 48)
        self.after(0, self.start_btn.configure, {"state": "normal"})
        self.after(0, self.stop_btn.configure,  {"state": "disabled"})
        self.after(0, self.eta_label.configure,  {"text": ""})
        if TRAY_AVAILABLE:
            self._tray_notify("Mnemo", "Backup complete!" if not self._stop_event.is_set() else "Backup stopped.")


if __name__ == "__main__":
    App().mainloop()
