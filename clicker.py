"""ClickerNK – Cell to Singularity Auto Clicker
Dependencies: pip install pywin32 keyboard
"""
import ctypes
import ctypes.wintypes
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

import keyboard
import win32api
import win32con 
import win32gui

# Config file sits next to the exe (frozen) or the script (dev)
_BASE = os.path.dirname(sys.executable if getattr(sys, "frozen", False)
                        else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(_BASE, "clicker_config.json")

# ─── SendInput type definitions ────────────────────────────────────────────

PUL = ctypes.POINTER(ctypes.c_ulong)


class KeyBdInput(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.c_ushort),
        ("wScan",       ctypes.c_ushort),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg",    ctypes.c_ulong),
        ("wParamL", ctypes.c_short),
        ("wParamH", ctypes.c_ushort),
    ]


class MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx",          ctypes.c_long),
        ("dy",          ctypes.c_long),
        ("mouseData",   ctypes.c_ulong),
        ("dwFlags",     ctypes.c_ulong),
        ("time",        ctypes.c_ulong),
        ("dwExtraInfo", PUL),
    ]


class _IUnion(ctypes.Union):
    _fields_ = [("ki", KeyBdInput), ("mi", MouseInput), ("hi", HardwareInput)]


class Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _IUnion)]


INPUT_MOUSE          = 0
INPUT_KEYBOARD       = 1
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_KEYUP      = 0x0002

_LETTER_KEYS_RAW = [chr(c) for c in range(0x41, 0x5B)
                    if chr(c) not in ("W", "A", "S", "D")]

KEY_MAP: dict[str, int] = {
    "Space": 0x20,
    "Ctrl":  0xA2,
    "0": 0x30,
    "1": 0x31, "2": 0x32, "3": 0x33, "4": 0x34, "5": 0x35,
    "6": 0x36, "7": 0x37, "8": 0x38, "9": 0x39,
    "Num0": 0x60, "Num1": 0x61, "Num2": 0x62, "Num3": 0x63, "Num4": 0x64,
    "Num5": 0x65, "Num6": 0x66, "Num7": 0x67, "Num8": 0x68, "Num9": 0x69,
    **{ch: ord(ch) for ch in _LETTER_KEYS_RAW},
}
DEFAULT_KEYS   = {"Space", "Ctrl"}
NUMBER_KEYS    = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
NUMPAD_KEYS    = ["Num0", "Num1", "Num2", "Num3", "Num4", "Num5",
                  "Num6", "Num7", "Num8", "Num9"]
LETTER_KEYS    = _LETTER_KEYS_RAW          # A-Z excl. WASD
DEFAULT_GOLDEN_COOLDOWN = 2.0              # seconds to pause after last golden detection
DEFAULT_TARGET_CPS      = 200              # clicks per second sent to the game
DEFAULT_HOTKEY = "f3"
GAME_TITLE     = "Cell to Singularity"

# ─── Input builders ────────────────────────────────────────────────────────

_extra = ctypes.cast(ctypes.pointer(ctypes.c_ulong(0)), PUL)


def _mouse_evt(nx: int, ny: int, flags: int) -> Input:
    inp = Input()
    inp.type = INPUT_MOUSE
    inp.ii.mi.dx, inp.ii.mi.dy = nx, ny
    inp.ii.mi.dwFlags = flags | MOUSEEVENTF_ABSOLUTE
    inp.ii.mi.dwExtraInfo = _extra
    return inp


def _key_evt(vk: int, up: bool = False) -> Input:
    inp = Input()
    inp.type = INPUT_KEYBOARD
    inp.ii.ki.wVk = vk
    inp.ii.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    inp.ii.ki.dwExtraInfo = _extra
    return inp


def _normalize(x: int, y: int) -> tuple[int, int]:
    """Convert pixel coords to the 0–65535 range SendInput expects."""
    w = ctypes.windll.user32.GetSystemMetrics(0)
    h = ctypes.windll.user32.GetSystemMetrics(1)
    return int(x * 65535 / w), int(y * 65535 / h)


def _find_popup_skip(hwnd: int, sct=None) -> tuple[int, int] | None:
    """Locate the Skip button in any charge popup (language-independent).

    Uses mss for fast screen capture (hardware-accelerated windows safe).
    Looks for a horizontal band of purple pixels (Trigger button) followed
    by a tall band of grey/teal pixels below it (Skip button).

    Returns screen pixel (x, y) centre of the Skip button, or None.
    Pass an open mss.mss() instance as `sct` to avoid per-call overhead.
    """
    import mss as _mss

    try:
        wx1, wy1, wx2, wy2 = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None

    ww, wh = wx2 - wx1, wy2 - wy1
    if ww < 100 or wh < 100:
        return None

    cx, cy = (wx1 + wx2) // 2, (wy1 + wy2) // 2

    # Restrict scan to centre 40 % × 60 % of the window — popup is always centred
    sx1 = cx - ww // 5
    sx2 = cx + ww // 5
    sy1 = cy - wh * 3 // 10
    sy2 = cy + wh * 3 // 10

    mon = {"left": sx1, "top": sy1, "width": sx2 - sx1, "height": sy2 - sy1}
    _own_sct = sct is None
    if _own_sct:
        sct = _mss.mss()
    raw = memoryview(bytes(sct.grab(mon).raw))  # BGRA bytes
    if _own_sct:
        sct.close()
    iw, ih = sx2 - sx1, sy2 - sy1
    stride = iw * 4  # 4 bytes per pixel (BGRA)

    STEP      = 6
    scan_cols = max(1, iw // STEP)
    min_hits  = max(5, scan_cols // 5)

    # Helper: get R, G, B from raw BGRA memoryview at image pixel (px, py)
    def px_rgb(px: int, py: int) -> tuple[int, int, int]:
        o = py * stride + px * 4
        return raw[o + 2], raw[o + 1], raw[o]   # R, G, B (BGRA order)

    # ── Phase 1: find the FULL extent of the purple (Trigger button) band ───
    trigger_band_bottom = None
    for py in range(0, ih, STEP):
        hits = 0
        for px in range(0, iw, STEP):
            r, g, b = px_rgb(px, py)
            if b > 130 and r > 90 and b > r and r > g and g < 165:
                hits += 1
        if hits >= min_hits:
            trigger_band_bottom = py

    if trigger_band_bottom is None:
        return None

    # ── Phase 2: find the Skip button band below Trigger ──────────────────
    # Require ≥ MIN_SKIP_ROWS to reject the Trigger's thin bottom border.
    MIN_SKIP_ROWS = 7

    skip_rows:   list[int] = []
    skip_x_sums: list[int] = []
    skip_hits:   list[int] = []

    for dy in range(STEP * 2, 280, 4):
        ty_rel = trigger_band_bottom + dy
        if ty_rel >= ih:
            break
        hits  = 0
        x_sum = 0
        for px in range(0, iw, STEP):
            r, g, b = px_rgb(px, ty_rel)
            is_grey = (26 <= r <= 90 and 26 <= g <= 90 and 26 <= b <= 90
                       and abs(r - g) < 26 and abs(r - b) < 26)
            is_teal = r < 80 and g > 140 and b > 140 and abs(g - b) < 50
            if is_grey or is_teal:
                hits  += 1
                x_sum += (sx1 + px)
        if hits >= min_hits:
            skip_rows.append(ty_rel)
            skip_x_sums.append(x_sum)
            skip_hits.append(hits)
        elif skip_rows:
            if len(skip_rows) >= MIN_SKIP_ROWS:
                break
            else:
                skip_rows.clear()
                skip_x_sums.clear()
                skip_hits.clear()

    if len(skip_rows) < MIN_SKIP_ROWS:
        return None

    # Centre of the Skip button band
    mid_y      = skip_rows[len(skip_rows) // 2]
    total_x    = sum(skip_x_sums)
    total_hits = sum(skip_hits)
    return total_x // total_hits, sy1 + mid_y


_GOLDEN_RADIUS = 25   # px around cursor to scan for golden sphere

def _pixel_is_golden(x: int, y: int, sct=None) -> bool:
    """Return True if the golden sphere is visible near (x, y).

    Uses mss for fast capture and raw BGRA memoryview for zero-overhead
    pixel access — no PIL getpixel() overhead.
    Pass an open mss.mss() instance as `sct` to avoid per-call init cost.

    Two colour signatures:
      • Orange/amber body  — R clearly dominant over G and B
      • Yellow orbital rings — R ≈ G both high, B low
    """
    import mss as _mss
    R    = _GOLDEN_RADIUS
    mon  = {"left": x - R, "top": y - R, "width": 2 * R + 1, "height": 2 * R + 1}
    _own = sct is None
    if _own:
        sct = _mss.mss()
    raw    = memoryview(bytes(sct.grab(mon).raw))  # BGRA
    if _own:
        sct.close()
    iw     = 2 * R + 1
    stride = iw * 4
    STEP   = 3
    for py in range(0, 2 * R + 1, STEP):
        row = py * stride
        for px in range(0, iw, STEP):
            o = row + px * 4
            b, g, r = raw[o], raw[o + 1], raw[o + 2]  # BGRA order
            if r > 120 and g > 70 and b < 100 and r > g and (r - b) > 70:
                return True
            if r > 160 and g > 140 and b < 90 and abs(r - g) < 60 and (r - b) > 80:
                return True
    return False


# ─── Application ───────────────────────────────────────────────────────────

class ClickerApp:
    def __init__(self):
        ctypes.windll.user32.SetProcessDPIAware()

        self.target_x: int | None = None
        self.target_y: int | None = None
        self.running       = False
        self._cps          = 0
        self._gold_last_seen: float = -999.0

        # Pre-built SendInput array (rebuilt on point/key change)
        self._arr:     ctypes.Array | None = None
        self._arr_cnt: int = 0
        self._arr_sz:  int = ctypes.sizeof(Input)

        self.root = tk.Tk()
        self.root.title("ClickerNK")
        self.root.resizable(False, False)

        self._key_vars: dict[str, tk.BooleanVar] = {
            k: tk.BooleanVar(value=(k in DEFAULT_KEYS)) for k in KEY_MAP
        }
        self._status_var      = tk.StringVar(value="Stopped")
        self._cps_var         = tk.StringVar(value="CPS: –")
        self._point_var       = tk.StringVar(value="Not set")
        self._hotkey_var      = tk.StringVar(value=DEFAULT_HOTKEY.upper())
        self._golden_mode     = tk.StringVar(value="avoid")  # "off" | "avoid" | "auto"
        self._letters_toggle  = tk.BooleanVar(value=False)
        self._target_cps      = tk.IntVar(value=DEFAULT_TARGET_CPS)
        self._golden_cooldown = tk.DoubleVar(value=DEFAULT_GOLDEN_COOLDOWN)

        self._current_hotkey: str = DEFAULT_HOTKEY
        self._hotkey_handle  = None
        self._popup_active   = False

        self._build_ui()
        self._load_config()
        self._register_hotkey(self._current_hotkey)

        # Stop clicker immediately if user presses Alt manually
        self._alt_hooks = [
            keyboard.on_press_key("left alt",  lambda _: self._on_alt_press()),
            # keyboard.on_press_key("right alt", lambda _: self._on_alt_press()),
        ]

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ─────────────────────────────────────────────────────────────────

    def _build_ui(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        PAD = dict(padx=10, pady=6)

        # ── Target point ──
        tf = ttk.LabelFrame(self.root, text=" Target Point ", padding=8)
        tf.grid(row=0, column=0, sticky="ew", **PAD)

        ttk.Button(tf, text="Pick Point", command=self._pick).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(tf, text="→", foreground="#888").grid(row=0, column=1, padx=6)
        ttk.Label(tf, textvariable=self._point_var, foreground="#444",
                  font=("Consolas", 9)).grid(row=0, column=2, sticky="w")

        # ── Keys ──
        kf = ttk.LabelFrame(self.root, text=" Keys to Press (each iteration) ", padding=8)
        kf.grid(row=1, column=0, sticky="ew", **PAD)

        # Special keys row
        for col, k in enumerate(["Space", "Ctrl"]):
            ttk.Checkbutton(
                kf, text=k, variable=self._key_vars[k],
                command=self._on_keys_changed,
            ).grid(row=0, column=col, sticky="w", padx=6, pady=2)

        # Number keys row with All / None
        ttk.Separator(kf, orient="horizontal").grid(
            row=1, column=0, columnspan=14, sticky="ew", pady=(4, 2))
        for col, k in enumerate(NUMBER_KEYS):
            ttk.Checkbutton(
                kf, text=k, variable=self._key_vars[k],
                command=self._on_keys_changed,
            ).grid(row=2, column=col, sticky="w", padx=3, pady=2)
        ttk.Button(kf, text="All",  width=4,
                   command=lambda: self._set_group(NUMBER_KEYS, True)
                   ).grid(row=2, column=10, padx=(8, 2))
        ttk.Button(kf, text="None", width=5,
                   command=lambda: self._set_group(NUMBER_KEYS, False)
                   ).grid(row=2, column=11, padx=2)

        # Numpad row with All / None
        ttk.Separator(kf, orient="horizontal").grid(
            row=3, column=0, columnspan=14, sticky="ew", pady=(4, 2))
        for col, k in enumerate(NUMPAD_KEYS):
            ttk.Checkbutton(
                kf, text=k.replace("Num", "N"), variable=self._key_vars[k],
                command=self._on_keys_changed,
            ).grid(row=4, column=col, sticky="w", padx=3, pady=2)
        ttk.Button(kf, text="All",  width=4,
                   command=lambda: self._set_group(NUMPAD_KEYS, True)
                   ).grid(row=4, column=10, padx=(8, 2))
        ttk.Button(kf, text="None", width=5,
                   command=lambda: self._set_group(NUMPAD_KEYS, False)
                   ).grid(row=4, column=11, padx=2)

        # Letters row (single toggle, no individual checkboxes)
        ttk.Separator(kf, orient="horizontal").grid(
            row=5, column=0, columnspan=14, sticky="ew", pady=(4, 2))
        ttk.Checkbutton(
            kf,
            text="Letters A–Z  (excl. W A S D)",
            variable=self._letters_toggle,
            command=self._on_letters_toggle,
        ).grid(row=6, column=0, columnspan=6, sticky="w", padx=6, pady=2)

        # ── Status bar ──
        sf = ttk.Frame(self.root, padding=(10, 4, 10, 2))
        sf.grid(row=2, column=0, sticky="ew")

        self._dot = tk.Label(sf, text="●", fg="#bb3333", font=("Segoe UI", 14))
        self._dot.grid(row=0, column=0, padx=(0, 6))

        ttk.Label(sf, textvariable=self._status_var,
                  font=("Segoe UI", 10, "bold")).grid(row=0, column=1)
        ttk.Label(sf, textvariable=self._cps_var,
                  font=("Consolas", 10)).grid(row=0, column=2, padx=(16, 0))

        # Popup detection indicator
        self._popup_dot = tk.Label(sf, text="● popup", fg="#444444",
                                   font=("Segoe UI", 8))
        self._popup_dot.grid(row=0, column=3, padx=(14, 0))

        ttk.Label(sf, text="Limit:", foreground="#555").grid(row=0, column=4, padx=(14, 2))
        ttk.Spinbox(sf, from_=1, to=500, textvariable=self._target_cps, width=5,
                    command=self._save_config).grid(row=0, column=5)
        ttk.Label(sf, text="CPS", foreground="#555").grid(row=0, column=6, padx=(2, 0))

        # ── Golden / Popup mode ──
        gf = ttk.LabelFrame(self.root, text=" Golden Sphere & Popup ", padding=8)
        gf.grid(row=3, column=0, sticky="ew", **PAD)

        # Mode radio buttons
        modes = [
            ("off",   "Off  — no special golden/popup handling"),
            ("avoid", "Avoid  — pause clicking when golden sphere detected"),
            ("auto",  "Auto  — click goldens; auto-skip popup, then briefly avoid"),
        ]
        for row_i, (val, label) in enumerate(modes):
            ttk.Radiobutton(
                gf, text=label, variable=self._golden_mode, value=val,
                command=self._save_config,
            ).grid(row=row_i, column=0, sticky="w", pady=1)

        # Cooldown row (relevant for avoid + auto modes)
        cf = ttk.Frame(gf)
        cf.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Label(cf, text="Cooldown:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(cf, from_=0.2, to=5.0, increment=0.1,
                    textvariable=self._golden_cooldown, width=5,
                    format="%.1f", command=self._save_config).grid(row=0, column=1, padx=(6, 2))
        ttk.Label(cf, text="s", foreground="#555").grid(row=0, column=2)
        if not getattr(sys, "frozen", False):   # debug buttons only when run as script
            ttk.Button(cf, text="Capture golden", width=13,
                       command=self._debug_capture_golden).grid(row=0, column=3, padx=(16, 0))
            ttk.Button(cf, text="Capture popup", width=12,
                       command=self._debug_capture).grid(row=0, column=4, padx=(6, 0))

        # ── Hotkey ──
        hf = ttk.LabelFrame(self.root, text=" Toggle Hotkey ", padding=8)
        hf.grid(row=4, column=0, sticky="ew", **PAD)

        ttk.Label(hf, textvariable=self._hotkey_var,
                  font=("Consolas", 10, "bold"), foreground="#225").grid(
            row=0, column=0, padx=(0, 10), sticky="w"
        )
        self._capture_btn = ttk.Button(
            hf, text="Change", width=8, command=self._start_hotkey_capture
        )
        self._capture_btn.grid(row=0, column=1, sticky="w")
        ttk.Label(hf, text="(click then press any key)", foreground="#999",
                  font=("Segoe UI", 8)).grid(row=0, column=2, padx=(8, 0))

        ttk.Separator(self.root, orient="horizontal").grid(
            row=5, column=0, sticky="ew", padx=10, pady=(2, 0)
        )
        ttk.Label(
            self.root,
            text="  ESC cancels point pick  •  Window auto-focused on start  ",
            foreground="#999",
            font=("Segoe UI", 8),
        ).grid(row=6, column=0, sticky="w", pady=(2, 8))

    # ── Hotkey registration & capture ──────────────────────────────────────

    def _register_hotkey(self, key: str):
        if self._hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self._hotkey_handle)
            except Exception:
                pass
        self._current_hotkey = key
        self._hotkey_handle = keyboard.add_hotkey(
            key,
            lambda: self.root.after(0, self._toggle),
            suppress=True,
        )

    def _start_hotkey_capture(self):
        self._capture_btn.config(text="Press a key…", state="disabled")
        # Temporarily remove the current hotkey so it doesn't fire during capture
        if self._hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self._hotkey_handle)
                self._hotkey_handle = None
            except Exception:
                pass
        threading.Thread(target=self._capture_key, daemon=True).start()

    def _capture_key(self):
        key = keyboard.read_key(suppress=True)
        self.root.after(0, lambda: self._apply_captured_hotkey(key))

    def _apply_captured_hotkey(self, key: str):
        self._register_hotkey(key)
        self._hotkey_var.set(key.upper())
        self._capture_btn.config(text="Change", state="normal")
        self._save_config()

    # ── Config persistence ─────────────────────────────────────────────────

    def _load_config(self):
        try:
            with open(CONFIG_PATH) as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return

        x, y = cfg.get("target_x"), cfg.get("target_y")
        if isinstance(x, int) and isinstance(y, int):
            self.target_x, self.target_y = x, y
            self._point_var.set(f"({x}, {y})")
            self._rebuild_cache()

        for key, val in cfg.get("keys", {}).items():
            if key in self._key_vars and isinstance(val, bool):
                self._key_vars[key].set(val)
        self._rebuild_cache()  # refresh after key state is restored

        hotkey = cfg.get("hotkey")
        if isinstance(hotkey, str) and hotkey:
            self._current_hotkey = hotkey
            self._hotkey_var.set(hotkey.upper())

        if cfg.get("golden_mode") in ("off", "avoid", "auto"):
            self._golden_mode.set(cfg["golden_mode"])
        elif isinstance(cfg.get("avoid_golden"), bool):
            # Migrate old config: avoid_golden + auto_skip → golden_mode
            self._golden_mode.set("avoid" if cfg["avoid_golden"] else "off")

        if isinstance(cfg.get("golden_cooldown"), (int, float)):
            self._golden_cooldown.set(float(cfg["golden_cooldown"]))

        if isinstance(cfg.get("target_cps"), int):
            self._target_cps.set(cfg["target_cps"])

        if isinstance(cfg.get("letters_toggle"), bool):
            self._letters_toggle.set(cfg["letters_toggle"])
            # Sync the individual key vars to match the saved toggle state
            self._set_group(LETTER_KEYS, cfg["letters_toggle"])

        wx, wy = cfg.get("window_x"), cfg.get("window_y")
        if isinstance(wx, int) and isinstance(wy, int):
            self.root.geometry(f"+{wx}+{wy}")

    def _save_config(self):
        cfg = {
            "target_x":     self.target_x,
            "target_y":     self.target_y,
            "keys":         {k: v.get() for k, v in self._key_vars.items()},
            "hotkey":       self._current_hotkey,
            "golden_mode":     self._golden_mode.get(),
            "golden_cooldown": round(self._golden_cooldown.get(), 1),
            "target_cps":      self._target_cps.get(),
            "letters_toggle":  self._letters_toggle.get(),
            "window_x":        self.root.winfo_x(),
            "window_y":        self.root.winfo_y(),
        }
        try:
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2)
        except OSError:
            pass

    # ── Point picker ───────────────────────────────────────────────────────

    def _pick(self):
        self.root.withdraw()
        ov = tk.Toplevel()
        ov.attributes("-fullscreen", True)
        ov.attributes("-alpha", 0.01)
        ov.attributes("-topmost", True)
        ov.config(cursor="crosshair")

        def on_click(event):
            self.target_x, self.target_y = event.x_root, event.y_root
            ov.destroy()
            self.root.deiconify()
            self._point_var.set(f"({self.target_x}, {self.target_y})")
            self._rebuild_cache()
            self._save_config()

        ov.bind("<Button-1>", on_click)
        ov.bind("<Escape>", lambda _: (ov.destroy(), self.root.deiconify()))
        ov.focus_set()

    # ── Input cache ────────────────────────────────────────────────────────

    def _on_keys_changed(self):
        self._rebuild_cache()
        self._save_config()

    def _set_group(self, keys: list[str], value: bool):
        for k in keys:
            self._key_vars[k].set(value)
        self._on_keys_changed()

    def _on_letters_toggle(self):
        self._set_group(LETTER_KEYS, self._letters_toggle.get())
        self._save_config()

    def _rebuild_cache(self):
        """Build the static SendInput array sent every loop iteration."""
        if self.target_x is None:
            return
        nx, ny = _normalize(self.target_x, self.target_y)

        items: list[Input] = [
            _mouse_evt(nx, ny, MOUSEEVENTF_LEFTDOWN),
            _mouse_evt(nx, ny, MOUSEEVENTF_LEFTUP),
        ]
        for name, var in self._key_vars.items():
            if var.get():
                vk = KEY_MAP[name]
                items.append(_key_evt(vk, False))
                items.append(_key_evt(vk, True))

        n = len(items)
        self._arr     = (Input * n)(*items)
        self._arr_cnt = n

    # ── Toggle / start / stop ──────────────────────────────────────────────

    def _toggle(self):
        (self._stop if self.running else self._start)()

    def _start(self):
        if self.target_x is None:
            messagebox.showwarning("No Target", "Pick a target point first.")
            return

        hwnd = self._find_window()
        if not hwnd:
            messagebox.showwarning(
                "Game Not Found",
                f'Could not find a window matching "{GAME_TITLE}".\n'
                "Make sure the game is running.",
            )
            return

        if self._arr is None:
            self._rebuild_cache()

        self._gold_last_seen = -999.0
        self.running = True
        self._set_status(True)
        threading.Thread(target=self._loop, args=(hwnd,), daemon=True).start()
        threading.Thread(target=self._popup_watcher, args=(hwnd,), daemon=True).start()
        threading.Thread(target=self._golden_watcher, daemon=True).start()
        self._tick_cps()

    def _stop(self):
        self.running = False

    def _on_alt_press(self):
        """Called when user presses Alt — stop clicker immediately."""
        if self.running:
            self.root.after(0, self._stop)

    def _release_all_keys(self):
        """Send key-up for every active key to ensure nothing stays held."""
        items = [_key_evt(KEY_MAP[k], True)
                 for k, v in self._key_vars.items() if v.get()]
        if items:
            n = len(items)
            ctypes.windll.user32.SendInput(
                n, (Input * n)(*items), ctypes.sizeof(Input))

    # ── Window helper ──────────────────────────────────────────────────────

    def _find_window(self) -> int | None:
        found: list[int] = []

        def cb(hwnd, _):
            if (win32gui.IsWindowVisible(hwnd) and
                    GAME_TITLE.lower() in win32gui.GetWindowText(hwnd).lower()):
                found.append(hwnd)

        win32gui.EnumWindows(cb, None)
        return found[0] if found else None

    # ── Popup watcher ──────────────────────────────────────────────────────

    def _debug_capture(self):
        """Capture the game window scan area, highlight matching pixels, open result."""
        def _run():
            time.sleep(3)
            hwnd = self._find_window()
            if not hwnd:
                self.root.after(0, lambda: messagebox.showinfo(
                    "Capture", "Game window not found."))
                return

            from PIL import Image, ImageDraw, ImageGrab

            wx1, wy1, wx2, wy2 = win32gui.GetWindowRect(hwnd)
            ww, wh = wx2 - wx1, wy2 - wy1
            cx, cy = (wx1 + wx2) // 2, (wy1 + wy2) // 2
            sx1 = cx - ww // 5
            sx2 = cx + ww // 5
            sy1 = cy - wh * 3 // 10
            sy2 = cy + wh * 3 // 10

            STEP = 6
            scan_cols = max(1, (sx2 - sx1) // STEP)
            min_hits  = max(5, scan_cols // 5)

            img  = ImageGrab.grab(bbox=(sx1, sy1, sx2, sy2))
            over = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(over)
            iw, ih = img.size

            purple_rows: dict[int, int] = {}
            grey_hits_total = 0

            for py in range(0, ih, STEP):
                for px in range(0, iw, STEP):
                    r, g, b = img.getpixel((px, py))
                    if b > 130 and r > 90 and b > r and r > g and g < 165:
                        draw.rectangle([px - 3, py - 3, px + 3, py + 3],
                                       fill=(255, 0, 255, 200))
                        purple_rows[py] = purple_rows.get(py, 0) + 1
                    elif (26 <= r <= 90 and 26 <= g <= 90 and 26 <= b <= 90
                          and abs(r - g) < 26 and abs(r - b) < 26):
                        draw.rectangle([px - 3, py - 3, px + 3, py + 3],
                                       fill=(0, 220, 220, 200))
                        grey_hits_total += 1

            best_purple_row  = max(purple_rows.values()) if purple_rows else 0
            purple_hits_total = sum(purple_rows.values())
            trigger_rows = [y for y, h in purple_rows.items() if h >= min_hits]

            result = Image.alpha_composite(img.convert("RGBA"), over)
            out = os.path.join(_BASE, "popup_debug.png")
            result.save(out)
            os.startfile(out)

            detect_result = _find_popup_skip(hwnd)
            status = f"✓ Detected — Skip at {detect_result}" if detect_result else "✗ Not detected"

            msg = (f"Detection: {status}\n\n"
                   f"Purple hits total: {purple_hits_total}  (best row: {best_purple_row})\n"
                   f"Grey hits total:   {grey_hits_total}\n"
                   f"Trigger rows found (≥{min_hits} hits): {len(trigger_rows)}\n\n"
                   f"Magenta = purple matches  |  Cyan = grey matches")
            self.root.after(0, lambda: messagebox.showinfo("Capture result", msg))

        messagebox.showinfo("Capture",
                            "Capturing in 3 seconds…\n"
                            "Switch to the game and open a popup, then wait.")
        threading.Thread(target=_run, daemon=True).start()

    def _debug_capture_golden(self):
        """Capture area around current cursor, highlight golden pixel matches, open result."""
        RADIUS = 60

        def _run():
            time.sleep(3)
            mx, my = win32api.GetCursorPos()

            from PIL import Image, ImageDraw, ImageGrab

            bx1, by1 = mx - RADIUS, my - RADIUS
            bx2, by2 = mx + RADIUS, my + RADIUS

            img  = ImageGrab.grab(bbox=(bx1, by1, bx2, by2))
            over = Image.new("RGBA", img.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(over)
            iw, ih = img.size

            orange_hits = 0
            yellow_hits = 0

            for py in range(ih):
                for px in range(iw):
                    r, g, b = img.getpixel((px, py))
                    if r > 120 and g > 70 and b < 100 and r > g and (r - b) > 70:
                        draw.rectangle([px - 2, py - 2, px + 2, py + 2],
                                       fill=(255, 80, 0, 220))   # orange = amber body
                        orange_hits += 1
                    elif r > 160 and g > 140 and b < 90 and abs(r - g) < 60 and (r - b) > 80:
                        draw.rectangle([px - 2, py - 2, px + 2, py + 2],
                                       fill=(255, 230, 0, 220))  # yellow = orbital ring
                        yellow_hits += 1

            # Mark the 9 sample points actually used by _pixel_is_golden
            cx, cy = RADIUS, RADIUS
            for spx, spy in (
                (cx, cy),
                (cx + 12, cy), (cx - 12, cy),
                (cx, cy + 12), (cx, cy - 12),
                (cx + 9,  cy + 9),  (cx - 9,  cy + 9),
                (cx + 9,  cy - 9),  (cx - 9,  cy - 9),
            ):
                draw.ellipse([spx - 4, spy - 4, spx + 4, spy + 4],
                             outline=(255, 255, 255, 255), width=2)

            result = Image.alpha_composite(img.convert("RGBA"), over)
            out = os.path.join(_BASE, "golden_debug.png")
            result.save(out)
            os.startfile(out)

            msg = (f"Cursor was at ({mx}, {my})\n\n"
                   f"Orange dots = amber body matches ({orange_hits} px)\n"
                   f"Yellow dots = orbital ring matches ({yellow_hits} px)\n"
                   f"White circles = the 9 sample points\n\n"
                   f"Detection fires if ANY sample point is orange or yellow.")
            self.root.after(0, lambda: messagebox.showinfo("Golden capture", msg))

        messagebox.showinfo("Capture golden",
                            "Capturing in 3 seconds…\n"
                            "Move your cursor over the golden sphere in the game, then hold still.")
        threading.Thread(target=_run, daemon=True).start()

    def _golden_watcher(self):
        """Daemon thread: samples cursor for golden sphere, reusing one mss context."""
        import mss as _mss
        with _mss.mss() as sct:
            while self.running:
                mode = self._golden_mode.get()
                if mode == "avoid":
                    mx, my = win32api.GetCursorPos()
                    if _pixel_is_golden(mx, my, sct):
                        self._gold_last_seen = time.perf_counter()
                time.sleep(0.03)

    def _popup_watcher(self, hwnd: int):
        """Separate thread: scans for charge popups and optionally clicks Skip.

        Sets _popup_active=True as soon as a popup is detected so the clicker
        pauses immediately, regardless of whether auto-skip is enabled.
        """
        import mss as _mss
        send = ctypes.windll.user32.SendInput
        sz   = ctypes.sizeof(Input)

        with _mss.mss() as sct:
            while self.running:
                time.sleep(0.3)

                pos = _find_popup_skip(hwnd, sct)

                if pos is None:
                    if self._popup_active:
                        self._popup_active = False
                    continue

                # ── Debounce: confirm popup is still there 200 ms later ────
                time.sleep(0.2)
                pos = _find_popup_skip(hwnd, sct)
                if pos is None:
                    continue

                # ── Confirmed popup — pause the clicker ────────────────────
                self._popup_active = True
                mode = self._golden_mode.get()

                if mode in ("avoid", "auto"):
                    sx, sy = pos
                    nx, ny = _normalize(sx, sy)
                    orig = win32api.GetCursorPos()
                    ctypes.windll.user32.SetCursorPos(sx, sy)
                    time.sleep(0.06)
                    click = (Input * 2)(
                        _mouse_evt(nx, ny, MOUSEEVENTF_LEFTDOWN),
                        _mouse_evt(nx, ny, MOUSEEVENTF_LEFTUP),
                    )
                    send(2, click, sz)
                    time.sleep(0.12)
                    ctypes.windll.user32.SetCursorPos(*orig)

                # ── Wait until popup is gone ────────────────────────────────
                for _ in range(20):
                    time.sleep(0.3)
                    if _find_popup_skip(hwnd, sct) is None:
                        break

                if mode == "auto":
                    self._gold_last_seen = time.perf_counter()

                self._popup_active = False

    # ── Clicker loop ───────────────────────────────────────────────────────

    def _loop(self, hwnd: int):
        orig = win32api.GetCursorPos()

        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            self.running = False
            return

        time.sleep(0.15)  # let the window actually reach the foreground
        ctypes.windll.user32.SetCursorPos(self.target_x, self.target_y)

        # Capture local refs — avoids attribute lookups inside the hot loop
        send        = ctypes.windll.user32.SendInput
        arr         = self._arr
        cnt         = self._arr_cnt
        sz          = self._arr_sz
        get_fg      = win32gui.GetForegroundWindow
        golden_mode = self._golden_mode.get()
        interval    = 1.0 / max(self._target_cps.get(), 1)
        cooldown    = self._golden_cooldown.get()

        # 1 ms Windows timer resolution for precise sleep
        ctypes.windll.winmm.timeBeginPeriod(1)

        n      = 0
        chk    = 0
        t_next = time.perf_counter()
        t_cps  = time.perf_counter()

        while self.running:
            # ── Rate limiting ──────────────────────────────────────────────
            now  = time.perf_counter()
            wait = t_next - now
            if wait > 0:
                time.sleep(wait)
            t_next += interval
            # Clamp: don't accumulate debt across missed ticks (prevents bursts)
            if t_next < time.perf_counter():
                t_next = time.perf_counter()

            now = time.perf_counter()
            # ── Golden pause: flag is updated by _golden_watcher thread ────
            gold_paused = (golden_mode != "off") and (now - self._gold_last_seen < cooldown)

            if not gold_paused and not self._popup_active:
                send(cnt, arr, sz)
                n += 1

            # ── Focus + CPS update every 5 iters (~83 ms at 60 CPS) ───────
            chk += 1
            if chk >= 5:
                chk = 0
                if get_fg() != hwnd:
                    break
                dt = now - t_cps
                if dt >= 1.0:
                    self._cps = int(n / dt)
                    n, t_cps = 0, now

        ctypes.windll.winmm.timeEndPeriod(1)
        self._release_all_keys()
        ctypes.windll.user32.SetCursorPos(*orig)
        self.running = False
        self.root.after(0, lambda: self._set_status(False))

    # ── CPS display ────────────────────────────────────────────────────────

    def _tick_cps(self):
        if self.running:
            self._cps_var.set(f"CPS: {self._cps:,}")
            self._popup_dot.config(
                fg="#ff7700" if self._popup_active else "#444444"
            )
            self.root.after(250, self._tick_cps)
        else:
            self._cps_var.set("CPS: –")
            self._popup_dot.config(fg="#444444")

    # ── Status indicator ───────────────────────────────────────────────────

    def _set_status(self, running: bool):
        if running:
            self._status_var.set("Running")
            self._dot.config(fg="#33bb33")
            self._capture_btn.config(state="disabled")
        else:
            self._status_var.set("Stopped")
            self._dot.config(fg="#bb3333")
            self._capture_btn.config(state="normal")

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def _on_close(self):
        self._save_config()   # persist window position before destroying root
        self.running = False
        if self._hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self._hotkey_handle)
            except Exception:
                pass
        for h in self._alt_hooks:
            try:
                keyboard.unhook(h)
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ClickerApp().run()
