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
    "Alt":   0xA4,
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
DEFAULT_GOLDEN_COOLDOWN = 1.2              # seconds to pause after last golden detection
DEFAULT_TARGET_CPS      = 60              # clicks per second sent to the game
DEFAULT_HOTKEY = "f4"
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


def _find_popup_skip(hdc: int, screen_w: int, screen_h: int) -> tuple[int, int] | None:
    """Locate the Skip button in the Outbreaks Charge popup.

    Strategy (language-independent):
      1. Coarse-scan the centre region for the *purple* Trigger button.
      2. From that anchor, scan downward for a wide band of *dark grey* — the Skip button.
    Returns pixel (x, y) to click, or None.
    """
    get_pixel = ctypes.windll.gdi32.GetPixel
    cx, cy = screen_w // 2, screen_h // 2

    # ── Step 1: find a purple pixel (Trigger button) ──────────────────────
    purple = None
    for py in range(cy - 230, cy + 130, 10):
        for px in range(cx - 300, cx + 300, 10):
            c = get_pixel(hdc, px, py)
            r, g, b = c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
            # Purple/violet: B dominant, R medium, G clearly lowest
            if b > 145 and r > 105 and b > r > g and g < 140:
                purple = (px, py)
                break
        if purple:
            break

    if not purple:
        return None

    ax, ay = purple  # anchor inside the Trigger button

    # ── Step 2: scan downward from anchor for the dark-grey Skip band ─────
    for dy in range(45, 210, 4):
        ty = ay + dy
        hits = 0
        for dx in range(-110, 110, 9):
            c = get_pixel(hdc, ax + dx, ty)
            r, g, b = c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
            # Dark grey: all channels low & roughly equal
            if 35 <= r <= 88 and 35 <= g <= 88 and 35 <= b <= 88 \
                    and abs(r - g) < 18 and abs(g - b) < 18:
                hits += 1
        if hits >= 10:          # wide enough to confirm it's a button
            return ax, ty

    return None


def _pixel_is_golden(hdc: int, x: int, y: int) -> bool:
    """Return True if any sample point around (x, y) looks like the golden sphere.

    Samples a wider 9-point grid (±12 px) because the sphere centre can appear
    white/bright — the gold colour lives on the ring edge.
    Thresholds: warm orange/amber, R dominant, B low.
    """
    get_pixel = ctypes.windll.gdi32.GetPixel
    for px, py in (
        (x,      y     ),
        (x + 12, y     ), (x - 12, y     ),
        (x,      y + 12), (x,      y - 12),
        (x + 9,  y + 9 ), (x - 9,  y + 9 ),
        (x + 9,  y - 9 ), (x - 9,  y - 9 ),
    ):
        c = get_pixel(hdc, px, py)
        r, g, b = c & 0xFF, (c >> 8) & 0xFF, (c >> 16) & 0xFF
        # Gold/orange/amber: R clearly dominant, G moderate, B low
        if r > 120 and g > 70 and b < 100 and r > g and (r - b) > 70:
            return True
    return False


# ─── Application ───────────────────────────────────────────────────────────

class ClickerApp:
    def __init__(self):
        ctypes.windll.user32.SetProcessDPIAware()

        self.target_x: int | None = None
        self.target_y: int | None = None
        self.running  = False
        self._cps     = 0

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
        self._avoid_golden    = tk.BooleanVar(value=True)
        self._auto_skip       = tk.BooleanVar(value=True)
        self._letters_toggle  = tk.BooleanVar(value=False)
        self._target_cps      = tk.IntVar(value=DEFAULT_TARGET_CPS)
        self._golden_cooldown = tk.DoubleVar(value=DEFAULT_GOLDEN_COOLDOWN)

        self._current_hotkey: str = DEFAULT_HOTKEY
        self._hotkey_handle = None

        self._build_ui()
        self._load_config()
        self._register_hotkey(self._current_hotkey)
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
        for col, k in enumerate(["Space", "Ctrl", "Alt"]):
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

        ttk.Label(sf, text="Limit:", foreground="#555").grid(row=0, column=3, padx=(16, 2))
        ttk.Spinbox(sf, from_=1, to=500, textvariable=self._target_cps, width=5,
                    command=self._save_config).grid(row=0, column=4)
        ttk.Label(sf, text="CPS", foreground="#555").grid(row=0, column=5, padx=(2, 0))

        # ── Golden avoidance ──
        gf = ttk.LabelFrame(self.root, text=" Golden Sphere Avoidance ", padding=8)
        gf.grid(row=3, column=0, sticky="ew", **PAD)
        ttk.Checkbutton(
            gf,
            text="Pause clicking when golden sphere detected at target",
            variable=self._avoid_golden,
            command=self._save_config,
        ).grid(row=0, column=0, sticky="w")
        cf = ttk.Frame(gf)
        cf.grid(row=1, column=0, sticky="w", pady=(4, 0))
        ttk.Label(cf, text="Cooldown after last detection:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(cf, from_=0.2, to=5.0, increment=0.1,
                    textvariable=self._golden_cooldown, width=5,
                    format="%.1f", command=self._save_config).grid(row=0, column=1, padx=(6, 2))
        ttk.Label(cf, text="s", foreground="#555").grid(row=0, column=2)

        ttk.Checkbutton(
            gf,
            text="Auto-click Skip on Outbreaks popup  (detects purple→grey button pattern)",
            variable=self._auto_skip,
            command=self._save_config,
        ).grid(row=2, column=0, sticky="w", pady=(6, 0))

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

        if isinstance(cfg.get("avoid_golden"), bool):
            self._avoid_golden.set(cfg["avoid_golden"])

        if isinstance(cfg.get("auto_skip"), bool):
            self._auto_skip.set(cfg["auto_skip"])

        if isinstance(cfg.get("golden_cooldown"), (int, float)):
            self._golden_cooldown.set(float(cfg["golden_cooldown"]))

        if isinstance(cfg.get("target_cps"), int):
            self._target_cps.set(cfg["target_cps"])

        if isinstance(cfg.get("letters_toggle"), bool):
            self._letters_toggle.set(cfg["letters_toggle"])
            # Sync the individual key vars to match the saved toggle state
            self._set_group(LETTER_KEYS, cfg["letters_toggle"])

    def _save_config(self):
        cfg = {
            "target_x":     self.target_x,
            "target_y":     self.target_y,
            "keys":         {k: v.get() for k, v in self._key_vars.items()},
            "hotkey":       self._current_hotkey,
            "avoid_golden":    self._avoid_golden.get(),
            "auto_skip":       self._auto_skip.get(),
            "golden_cooldown": round(self._golden_cooldown.get(), 1),
            "target_cps":      self._target_cps.get(),
            "letters_toggle":  self._letters_toggle.get(),
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

        self.running = True
        self._set_status(True)
        threading.Thread(target=self._loop, args=(hwnd,), daemon=True).start()
        threading.Thread(target=self._popup_watcher, daemon=True).start()
        self._tick_cps()

    def _stop(self):
        self.running = False

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

    def _popup_watcher(self):
        """Separate thread: scans for the Outbreaks popup and clicks Skip."""
        hdc      = ctypes.windll.user32.GetDC(0)
        screen_w = ctypes.windll.user32.GetSystemMetrics(0)
        screen_h = ctypes.windll.user32.GetSystemMetrics(1)
        send     = ctypes.windll.user32.SendInput
        sz       = ctypes.sizeof(Input)

        while self.running:
            time.sleep(0.35)

            if not self._auto_skip.get():
                continue

            pos = _find_popup_skip(hdc, screen_w, screen_h)
            if pos is None:
                continue

            sx, sy = pos
            nx, ny = _normalize(sx, sy)

            # Move cursor to Skip button then send click
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

            # Back-off so we don't re-trigger on the same popup
            time.sleep(1.2)

        ctypes.windll.user32.ReleaseDC(0, hdc)

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
        avoid_gold  = self._avoid_golden.get()
        tx, ty      = self.target_x, self.target_y
        hdc         = ctypes.windll.user32.GetDC(0) if avoid_gold else None
        interval    = 1.0 / max(self._target_cps.get(), 1)
        cooldown    = self._golden_cooldown.get()

        # 1 ms Windows timer resolution for precise sleep
        ctypes.windll.winmm.timeBeginPeriod(1)

        n              = 0
        chk            = 0
        gold_last_seen = -999.0
        t_next         = time.perf_counter()
        t_cps          = time.perf_counter()

        while self.running:
            # ── Rate limiting ──────────────────────────────────────────────
            now  = time.perf_counter()
            wait = t_next - now
            if wait > 0:
                time.sleep(wait)
            t_next += interval

            # ── Golden detection (every iteration — cheap at limited CPS) ──
            now = time.perf_counter()
            if avoid_gold and _pixel_is_golden(hdc, tx, ty):
                gold_last_seen = now

            paused = avoid_gold and (now - gold_last_seen < cooldown)
            if not paused:
                send(cnt, arr, sz)
                n += 1

            # ── Focus + CPS update every 60 iters ─────────────────────────
            chk += 1
            if chk >= 60:
                chk = 0
                if get_fg() != hwnd:
                    break
                dt = now - t_cps
                if dt >= 1.0:
                    self._cps = int(n / dt)
                    n, t_cps = 0, now

        ctypes.windll.winmm.timeEndPeriod(1)
        if hdc is not None:
            ctypes.windll.user32.ReleaseDC(0, hdc)
        ctypes.windll.user32.SetCursorPos(*orig)
        self.running = False
        self.root.after(0, lambda: self._set_status(False))

    # ── CPS display ────────────────────────────────────────────────────────

    def _tick_cps(self):
        if self.running:
            self._cps_var.set(f"CPS: {self._cps:,}")
            self.root.after(250, self._tick_cps)
        else:
            self._cps_var.set("CPS: –")

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
        self.running = False
        if self._hotkey_handle is not None:
            try:
                keyboard.remove_hotkey(self._hotkey_handle)
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ClickerApp().run()
