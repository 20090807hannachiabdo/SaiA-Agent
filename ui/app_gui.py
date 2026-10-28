"""
SaiA Desktop Graphical User Interface
English is the primary development codebase.
Features:
- Three layout modes: Full window (Ex:1), Floating Glass Orb (Ex:2), Compact Bar (Ex:3).
- Real-time animated step tracking (e.g. '✓ Gathering files', 'Running action ●○○').
- Dynamic growing prompt text field with custom pill scrollbars.
- Full localization via ui/i18n.py for English, Arabic, and French.
- Chat History navigation drawer with session renaming and disk restore.
- Visual n8n-style Node Flow Studio with Windows Task Scheduler persistence.
"""

from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from tkinter import filedialog
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.agent_workflow import ai_agent
from core.api_saia import ENV_FIELDS, ENV_FILE, Cancelled, read_env_values, save_env_values
from storage.database import Database
from ui.chat_widgets import FileCard, FunctionCard, split_answer
from ui.i18n import LANGUAGES, STRINGS, get_language, set_language, t

try:
    from ui.rich_text import has_wide_blocks, recolor_tags, render_rich
    RICH_AVAILABLE = True
except Exception:
    RICH_AVAILABLE = False

import time  # noqa: E402

try:
    from core.attachments import DIALOG_PATTERN, MAX_FILES
except Exception:
    DIALOG_PATTERN = "*.png *.jpg *.jpeg *.webp *.bmp *.gif *.txt *.py *.html *.md *.pdf"
    MAX_FILES = 100

try:
    from core.voice import MAX_RECORD_SECONDS, Recorder, Speaker, transcribe_wav
    VOICE_AVAILABLE = True
except Exception:
    VOICE_AVAILABLE = False

try:
    from core.api_saia import DEFAULT_VOICE, VOICES, get_media_voice, set_media_voice
except Exception:
    DEFAULT_VOICE = "Kore"
    VOICES = {"Kore": "female"}

    def get_media_voice() -> str:
        return DEFAULT_VOICE

    def set_media_voice(name: str) -> str:
        return DEFAULT_VOICE

try:
    from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageOps, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "static", "img", "logo.png")

BASE_URL = "http://127.0.0.1:5000"
URL_ABOUT = BASE_URL
URL_SUBSCRIPTION = BASE_URL + "/Subscription"
URL_LOGIN = BASE_URL + "/login"
URL_SIGN_IN = BASE_URL + "/create_account"


def _hex_to_colorref(hex_color: str) -> int:
    """Converts a #RRGGBB hex color to Windows COLORREF format (0x00BBGGRR)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return r | (g << 8) | (b << 16)


def _apply_titlebar_color_now(
    window: tk.Tk | tk.Toplevel, bar_color: str, text_color: Optional[str] = None, border_color: Optional[str] = None
) -> bool:
    """Applies native Windows DWM caption and border styling."""
    if sys.platform != "win32":
        return False
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        if not hwnd:
            return False

        DWMWA_BORDER_COLOR = 34
        DWMWA_CAPTION_COLOR = 35
        DWMWA_TEXT_COLOR = 36

        caption_ref = ctypes.c_int(_hex_to_colorref(bar_color))
        result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_CAPTION_COLOR, ctypes.byref(caption_ref), ctypes.sizeof(caption_ref)
        )

        border_ref = ctypes.c_int(_hex_to_colorref(border_color or bar_color))
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_BORDER_COLOR, ctypes.byref(border_ref), ctypes.sizeof(border_ref)
        )

        if text_color:
            text_ref = ctypes.c_int(_hex_to_colorref(text_color))
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_TEXT_COLOR, ctypes.byref(text_ref), ctypes.sizeof(text_ref)
            )

        return result == 0
    except Exception:
        return False


def apply_native_titlebar_color(
    window: tk.Tk | tk.Toplevel, bar_color: str, text_color: Optional[str] = None, border_color: Optional[str] = None
) -> None:
    """Queues native titlebar styling asynchronously."""
    if sys.platform != "win32":
        return
    window.after(10, lambda: _apply_titlebar_color_now(window, bar_color, text_color, border_color))
    window.after(150, lambda: _apply_titlebar_color_now(window, bar_color, text_color, border_color))


def load_logo_photo(max_size: Optional[int] = None) -> Optional[Any]:
    """Loads and caches the application logo."""
    if not os.path.isfile(LOGO_PATH):
        return None
    try:
        if PIL_AVAILABLE:
            img = Image.open(LOGO_PATH)
            if max_size:
                img.thumbnail((max_size, max_size), Image.LANCZOS)
            return ImageTk.PhotoImage(img)
        photo = tk.PhotoImage(file=LOGO_PATH)
        if max_size and photo.width() > max_size:
            factor = max(1, photo.width() // max_size)
            photo = photo.subsample(factor, factor)
        return photo
    except Exception:
        return None


# =========================================================
#  Floating Orb Component (Ex:2)
# =========================================================
def build_orb_image(size: int, hover: bool = False, letter: str = "S") -> Image.Image:
    """Generates the glassy silver orb with radial gradients and Gaussian blur."""
    S = 4
    big = size * S
    d = int(size * 0.60 * S)
    m = (big - d) // 2
    box = (m, m, m + d, m + d)

    canvas = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse((box[0] + 2 * S, box[1] + 6 * S, box[2] - 2 * S, box[3] + 6 * S), fill=(0, 0, 0, 170))
    shadow = shadow.filter(ImageFilter.GaussianBlur(9 * S))
    canvas = Image.alpha_composite(canvas, shadow)

    glow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    g = 7 * S
    ImageDraw.Draw(glow).ellipse((box[0] - g, box[1] - g, box[2] + g, box[3] + g), fill=(255, 255, 255, 135 if hover else 70))
    glow = glow.filter(ImageFilter.GaussianBlur(11 * S))
    canvas = Image.alpha_composite(canvas, glow)

    grad = Image.radial_gradient("L").resize((int(d * 1.5), int(d * 1.5)), Image.BICUBIC)
    left, top = int(d * 0.40), int(d * 0.45)
    grad = grad.crop((left, top, left + d, top + d))
    body = ImageOps.colorize(
        grad,
        black=(255, 255, 255) if hover else (246, 246, 248),
        mid=(222, 222, 228) if hover else (196, 196, 202),
        white=(96, 96, 104) if hover else (72, 72, 78),
    ).convert("RGBA")
    mask = Image.new("L", (d, d), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, d - 1, d - 1), fill=255)
    canvas.paste(body, (m, m), mask)

    ring = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse(box, outline=(255, 255, 255, 110), width=max(1, int(1.5 * S)))
    canvas = Image.alpha_composite(canvas, ring)

    gw, gh = int(d * 0.68), int(d * 0.42)
    vgrad = ImageOps.invert(Image.linear_gradient("L")).resize((gw, gh), Image.BICUBIC)
    emask = Image.new("L", (gw, gh), 0)
    ImageDraw.Draw(emask).ellipse((0, 0, gw - 1, gh - 1), fill=255)
    gloss_alpha = ImageChops.multiply(vgrad, emask).point(lambda v: int(v * 0.62))
    gloss = Image.new("RGBA", (gw, gh), (255, 255, 255, 255))
    canvas.paste(gloss, (m + (d - gw) // 2, m + int(d * 0.06)), gloss_alpha)

    draw = ImageDraw.Draw(canvas)
    font = None
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            font = ImageFont.truetype(name, int(d * 0.46))
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    draw.text((big / 2, big / 2 + d * 0.02), letter, font=font, fill=(18, 18, 20, 255), anchor="mm")
    return canvas.resize((size, size), Image.LANCZOS)


def _premultiplied_bgra(img: Image.Image) -> bytes:
    """Pre-multiplies alpha into BGRA channels for UpdateLayeredWindow."""
    r, g, b, a = img.split()
    r = ImageChops.multiply(r, a)
    g = ImageChops.multiply(g, a)
    b = ImageChops.multiply(b, a)
    return Image.merge("RGBA", (b, g, r, a)).tobytes()


class FloatingOrb:
    KEY = "#0a0a0b"

    def __init__(self, root: tk.Tk, size: int, on_click: Callable[[], None]):
        self.root = root
        self.size = size
        self.on_click = on_click
        self.win = tk.Toplevel(root)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.wm_attributes("-topmost", True)
        self.win.geometry(f"{size}x{size}+100+100")
        self._ready = False
        self._layered = False
        self._hover = False
        self._drag = {"x": 0, "y": 0, "wx": 0, "wy": 0, "moved": False}
        self._target = self.win

    def _setup(self):
        self._ready = True
        if sys.platform == "win32" and PIL_AVAILABLE:
            try:
                self._setup_layered()
                self._layered = True
            except Exception:
                self._layered = False
        if not self._layered:
            self._setup_fallback()
        t_w = self._target
        t_w.configure(cursor="fleur")
        t_w.bind("<Enter>", self._on_enter)
        t_w.bind("<Leave>", self._on_leave)
        t_w.bind("<ButtonPress-1>", self._press)
        t_w.bind("<B1-Motion>", self._motion)
        t_w.bind("<ButtonRelease-1>", self._release)

    def _setup_layered(self):
        from ctypes import wintypes
        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        class SIZE(ctypes.Structure):
            _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]

        class BLENDFUNCTION(ctypes.Structure):
            _fields_ = [
                ("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)
            ]

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
            ]

        user32.GetParent.argtypes = [wintypes.HWND]
        user32.GetParent.restype = wintypes.HWND
        user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.GetDC.restype = wintypes.HDC
        user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE),
            wintypes.HDC, ctypes.POINTER(POINT), wintypes.DWORD, ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD,
        ]
        user32.UpdateLayeredWindow.restype = wintypes.BOOL
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD,
        ]
        gdi32.CreateDIBSection.restype = wintypes.HBITMAP
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ

        self.win.update_idletasks()
        inner = self.win.winfo_id()
        self._hwnd = user32.GetParent(inner) or inner

        GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TOOLWINDOW = -20, 0x80000, 0x80
        style = user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(self._hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TOOLWINDOW)

        screen_dc = user32.GetDC(None)
        n = self.size
        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth, bmi.biHeight = n, -n
        bmi.biPlanes, bmi.biBitCount, bmi.biCompression = 1, 32, 0

        self._dcs = {}
        for hover in (False, True):
            data = _premultiplied_bgra(build_orb_image(n, hover))
            bits = ctypes.c_void_p()
            hbmp = gdi32.CreateDIBSection(screen_dc, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
            if not hbmp or not bits.value:
                raise RuntimeError("CreateDIBSection failed")
            ctypes.memmove(bits, data, len(data))
            memdc = gdi32.CreateCompatibleDC(screen_dc)
            gdi32.SelectObject(memdc, hbmp)
            self._dcs[hover] = (memdc, hbmp)

        self._screen_dc = screen_dc
        self._sz = SIZE(n, n)
        self._src = POINT(0, 0)
        self._blend = BLENDFUNCTION(0, 0, 255, 1)
        self._ulw = user32.UpdateLayeredWindow
        self._byref = ctypes.byref
        if not self._present(False):
            raise RuntimeError("UpdateLayeredWindow failed")

    def _present(self, hover: bool) -> bool:
        memdc = self._dcs[hover][0]
        return bool(
            self._ulw(
                self._hwnd, self._screen_dc, None, self._byref(self._sz),
                memdc, self._byref(self._src), 0, self._byref(self._blend), 2,
            )
        )

    def _setup_fallback(self):
        n = self.size
        self.win.configure(bg=self.KEY)
        try:
            self.win.wm_attributes("-transparentcolor", self.KEY)
        except tk.TclError:
            pass
        cv = tk.Canvas(self.win, width=n, height=n, bg=self.KEY, highlightthickness=0, bd=0)
        cv.pack()
        steps = 14
        r0 = n * 0.30
        for i in range(steps):
            t_ratio = i / (steps - 1)
            shade = int(250 - 170 * t_ratio)
            r = r0 * (1 - t_ratio * 0.85)
            cx, cy = n / 2 - t_ratio * 6, n / 2 - t_ratio * 6
            cv.create_oval(
                cx - r, cy - r, cx + r, cy + r, outline="",
                fill=f"#{shade:02x}{shade:02x}{shade + 4 if shade < 251 else 255:02x}",
            )
        cv.create_text(n / 2, n / 2, text="S", fill="#121214", font=("Segoe UI", 22, "bold"))
        self._canvas = cv
        self._target = cv

    def _on_enter(self, _e):
        if self._layered and not self._hover:
            self._hover = True
            self._present(True)

    def _on_leave(self, _e):
        if self._layered and self._hover:
            self._hover = False
            self._present(False)

    def _press(self, e):
        self._drag = {
            "x": e.x_root, "y": e.y_root,
            "wx": self.win.winfo_x(), "wy": self.win.winfo_y(), "moved": False,
        }

    def _motion(self, e):
        dx, dy = e.x_root - self._drag["x"], e.y_root - self._drag["y"]
        if abs(dx) > 3 or abs(dy) > 3:
            self._drag["moved"] = True
        self.win.geometry(f"+{self._drag['wx'] + dx}+{self._drag['wy'] + dy}")

    def _release(self, _e):
        if not self._drag.get("moved"):
            self.on_click()

    @property
    def visible(self) -> bool:
        return self.win.winfo_viewable()

    def position(self) -> Tuple[int, int]:
        return self.win.winfo_x(), self.win.winfo_y()

    def show(self, x: Optional[int] = None, y: Optional[int] = None):
        if x is not None and y is not None:
            self.win.geometry(f"{self.size}x{self.size}+{x}+{y}")
        self.win.deiconify()
        self.win.update_idletasks()
        if not self._ready:
            self._setup()
        elif self._layered:
            self._hover = False
            self._present(False)
        self.win.wm_attributes("-topmost", True)

    def hide(self):
        self.win.withdraw()


# =========================================================
#  Asynchronous Worker Thread
# =========================================================
class QueryWorker(threading.Thread):
    """Runs every question in its own thread, so a cancelled request that is still waiting for the
    network never blocks the next one. Status updates and results of cancelled jobs are dropped."""

    def __init__(self, agent: ai_agent):
        super().__init__(daemon=True)
        self._queue = queue.Queue()
        self.results = queue.Queue()
        self.agent = agent
        self._generation = 0

    def cancel(self):
        self._generation += 1  # everything submitted before this moment is abandoned

    def run(self):
        while True:
            job = self._queue.get()
            threading.Thread(target=self._run_job, args=job, daemon=True).start()

    def _run_job(self, query: str, entry_widget: tk.Widget, file_path: Any, generation: int):
        def report(text: str):
            if generation == self._generation:
                self.results.put(("status", text, entry_widget))

        try:
            result = self.agent.run_workflow(query, file_path, on_status=report)
        except Cancelled:
            return
        except Exception as e:
            result = t("no_api_key") if "AI_API_KEY" in str(e) else t("error_exec", error=e)
        if generation == self._generation:
            self.results.put(("result", result, entry_widget))

    def submit(self, query: str, entry_widget: tk.Widget, file_path: Optional[str] = None):
        self._queue.put((query, entry_widget, file_path, self._generation))


# =========================================================
#  Theme and Color Palettes
# =========================================================
class Theme:
    PALETTES = {
        "dark": {
            "BG_DARK": "#0f1720",
            "BG_PANEL": "#16212c",
            "BG_FIELD": "#1d2a37",
            "BORDER": "#2b3a4a",
            "BORDER_LIGHT": "#3d4f63",
            "TEXT_PRIMARY": "#eef3f8",
            "TEXT_SECONDARY": "#9fb0c0",
            "TEXT_MUTED": "#64778a",
            "ACCENT": "#3fb8a5",
            "ACCENT_HOVER": "#5fd0be",
            "ACCENT_DIM": "#2a4a48",
            "DANGER_HOVER": "#3a2328",
        },
        "light": {
            "BG_DARK": "#e8eef3",
            "BG_PANEL": "#f6f9fb",
            "BG_FIELD": "#fcfdfe",
            "BORDER": "#cbd6df",
            "BORDER_LIGHT": "#a9b8c5",
            "TEXT_PRIMARY": "#14212c",
            "TEXT_SECONDARY": "#4d6070",
            "TEXT_MUTED": "#82929f",
            "ACCENT": "#0f7b6c",
            "ACCENT_HOVER": "#0a5c51",
            "ACCENT_DIM": "#bfd4d1",
            "DANGER_HOVER": "#f5dfe1",
        },
    }
    mode = "dark"

    FONT_UI = ("Segoe UI", 10)
    FONT_UI_BOLD = ("Segoe UI Semibold", 10)
    FONT_TITLE = ("Segoe UI", 15, "bold")
    FONT_LABEL = ("Segoe UI", 9)
    FONT_MONO = ("Cascadia Mono", 10)

    @classmethod
    def apply(cls, mode: str):
        if mode not in cls.PALETTES:
            mode = "dark"
        cls.mode = mode
        for name, value in cls.PALETTES[mode].items():
            setattr(cls, name, value)

    @classmethod
    def mapping(cls, old_mode: str, new_mode: str) -> Dict[str, str]:
        old, new = cls.PALETTES[old_mode], cls.PALETTES[new_mode]
        return {old[name].lower(): new[name] for name in old}

    @classmethod
    def live(cls, color: Any) -> Any:
        name = cls._TOKEN_OF.get(str(color).lower())
        return cls.PALETTES[cls.mode][name] if name else color


Theme._TOKEN_OF = {
    value.lower(): name for palette in Theme.PALETTES.values() for name, value in palette.items()
}
Theme.apply("dark")


class UserSettings:
    DEFAULTS = {
        "theme": "light",
        "language": "en",
        "user_nickname": "",
        "selected_model": "gemini-3.5-flash-lite",
        "active_role": "student",
        "tts_voice": DEFAULT_VOICE,
    }

    def __init__(self):
        self.db = Database()
        self.data = dict(self.DEFAULTS)
        try:
            self.db.create_database()
            self.data.update(self.db.get_preferences())
        except Exception:
            pass
        self._sanitize()

    def _sanitize(self):
        if self.data.get("theme") not in Theme.PALETTES:
            self.data["theme"] = self.DEFAULTS["theme"]
        if self.data.get("language") not in STRINGS:
            self.data["language"] = self.DEFAULTS["language"]
        if not isinstance(self.data.get("user_nickname"), str):
            self.data["user_nickname"] = ""
        if self.data.get("tts_voice") not in VOICES:
            self.data["tts_voice"] = DEFAULT_VOICE
        try:
            ids = [m["id"] for m in self.db.list_models()]
        except Exception:
            ids = []
        if ids and self.data.get("selected_model") not in ids:
            self.data["selected_model"] = ids[0]
            self._save({"selected_model": ids[0]})

    def _save(self, values: Dict[str, Any]):
        try:
            self.db.set_preferences(**values)
        except Exception:
            pass

    def get(self, key: str) -> Any:
        return self.data.get(key, self.DEFAULTS.get(key))

    def set(self, **values: Any):
        self.data.update(values)
        self._save(values)


_COLOR_OPTIONS = (
    "bg", "fg", "activebackground", "activeforeground", "selectcolor",
    "insertbackground", "highlightbackground", "highlightcolor",
    "disabledbackground", "disabledforeground", "readonlybackground",
)


def recolor_tree(widget: tk.Widget, mapping: Dict[str, str]):
    for option in _COLOR_OPTIONS:
        try:
            current = str(widget.cget(option)).lower()
        except tk.TclError:
            continue
        new = mapping.get(current)
        if new:
            try:
                widget.configure(**{option: new})
            except tk.TclError:
                pass
    if isinstance(widget, MinimalScrollbar):
        widget._draw()
    elif isinstance(widget, tk.Canvas):
        for item in widget.find_all():
            for option in ("fill", "outline"):
                try:
                    new = mapping.get(str(widget.itemcget(item, option)).lower())
                    if new:
                        widget.itemconfigure(item, **{option: new})
                except tk.TclError:
                    pass
    for child in widget.winfo_children():
        recolor_tree(child, mapping)


class LiveText:
    def __init__(self):
        self._items = []

    def bind(self, widget: tk.Widget, text_fn: Callable[[], str], option: str = "text") -> tk.Widget:
        self._items.append((widget, option, text_fn))
        widget.configure(**{option: text_fn()})
        return widget

    def refresh(self):
        alive = []
        for widget, option, text_fn in self._items:
            try:
                widget.configure(**{option: text_fn()})
            except tk.TclError:
                continue
            alive.append((widget, option, text_fn))
        self._items = alive


def add_hover(widget: tk.Widget, normal_bg: str, hover_bg: str, normal_fg: Optional[str] = None, hover_fg: Optional[str] = None):
    def on_enter(e):
        widget.configure(bg=Theme.live(hover_bg))
        if hover_fg:
            widget.configure(fg=Theme.live(hover_fg))

    def on_leave(e):
        widget.configure(bg=Theme.live(normal_bg))
        if normal_fg:
            widget.configure(fg=Theme.live(normal_fg))

    widget.bind("<Enter>", on_enter)
    widget.bind("<Leave>", on_leave)


def ghost_button(
    parent: tk.Widget, text: str, command: Callable[[], None],
    fg: Optional[str] = None, hover_fg: Optional[str] = None, font: Optional[Any] = None,
    bg: Optional[str] = None, hover_bg: Optional[str] = None, padx: int = 10, pady: int = 6,
) -> tk.Button:
    fg = fg or Theme.TEXT_SECONDARY
    hover_fg = hover_fg or Theme.TEXT_PRIMARY
    font = font or Theme.FONT_UI
    bg = bg or Theme.BG_DARK
    hover_bg = hover_bg or Theme.BG_PANEL
    btn = tk.Button(
        parent, text=text, font=font, bg=bg, fg=fg,
        activebackground=hover_bg, activeforeground=hover_fg,
        relief="flat", bd=0, cursor="hand2", padx=padx, pady=pady, command=command,
    )
    add_hover(btn, bg, hover_bg, fg, hover_fg)
    return btn


def solid_button(parent: tk.Widget, text: str, command: Callable[[], None], padx: int = 16, pady: int = 5) -> tk.Button:
    btn = tk.Button(
        parent, text=text, font=Theme.FONT_UI_BOLD, bg=Theme.ACCENT, fg=Theme.BG_DARK,
        activebackground=Theme.ACCENT_HOVER, activeforeground=Theme.BG_DARK,
        relief="flat", bd=0, cursor="hand2", padx=padx, pady=pady, command=command,
    )
    add_hover(btn, Theme.ACCENT, Theme.ACCENT_HOVER, Theme.BG_DARK, Theme.BG_DARK)
    return btn


def outline_button(parent: tk.Widget, text: str, command: Callable[[], None], padx: int = 14, pady: int = 5) -> tk.Button:
    btn = ghost_button(
        parent, text, command, fg=Theme.TEXT_PRIMARY, bg=Theme.BG_FIELD, hover_bg=Theme.BORDER, padx=padx, pady=pady
    )
    btn.configure(highlightthickness=1, highlightbackground=Theme.BORDER, highlightcolor=Theme.BORDER_LIGHT)
    return btn


def divider(parent: tk.Widget, orient: str = "h") -> tk.Frame:
    if orient == "h":
        return tk.Frame(parent, bg=Theme.BORDER, height=1)
    return tk.Frame(parent, bg=Theme.BORDER, width=1)


class MinimalScrollbar(tk.Canvas):
    def __init__(self, parent: tk.Widget, command: Optional[Callable] = None, width: int = 8, min_thumb: int = 32):
        super().__init__(parent, width=width, bg=Theme.BG_PANEL, highlightthickness=0, bd=0)
        self.command = command
        self._bar_w = width
        self._min = min_thumb
        self._lo, self._hi = 0.0, 1.0
        self._hover = False
        self._offset = None

        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)

    def set(self, lo: float, hi: float):
        self._lo, self._hi = float(lo), float(hi)
        self._draw()

    def _bounds(self):
        h = self.winfo_height()
        top, bot = self._lo * h, self._hi * h
        if bot - top < self._min:
            bot = min(h, top + self._min)
            top = bot - self._min
        return top, bot, h

    def _draw(self):
        self.delete("all")
        if self._hi - self._lo >= 0.999:
            return
        top, bot, h = self._bounds()
        if h <= 1:
            return
        r = self._bar_w / 2
        active = self._hover or self._offset is not None
        color = Theme.TEXT_MUTED if active else Theme.BORDER_LIGHT
        self.create_line(
            self._bar_w / 2, top + r, self._bar_w / 2, bot - r,
            width=self._bar_w - 2, capstyle="round", fill=color,
        )

    def _set_hover(self, value: bool):
        self._hover = value
        self._draw()

    def _press(self, e):
        top, bot, h = self._bounds()
        if top <= e.y <= bot:
            self._offset = e.y - top
        else:
            self._offset = (bot - top) / 2
            self._motion(e)
        self._draw()

    def _motion(self, e):
        if self._offset is None or not self.command:
            return
        h = self.winfo_height()
        if h > 1:
            self.command("moveto", max(0.0, (e.y - self._offset) / h))

    def _release(self, _e):
        self._offset = None
        self._draw()


class ModelPicker(tk.Menubutton):
    def __init__(self, parent: tk.Widget, app: Any, width: int = 18):
        self.app = app
        super().__init__(
            parent, text="", font=Theme.FONT_UI, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY,
            activebackground=Theme.BORDER, activeforeground=Theme.TEXT_PRIMARY,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=Theme.BORDER,
            highlightcolor=Theme.BORDER_LIGHT, cursor="hand2", padx=10, pady=5, anchor="w", width=width,
        )
        self.menu = tk.Menu(
            self, tearoff=0, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY,
            activebackground=Theme.BORDER_LIGHT, activeforeground=Theme.TEXT_PRIMARY,
            relief="flat", bd=0, font=Theme.FONT_UI, postcommand=self._rebuild_menu,
        )
        self.configure(menu=self.menu)
        add_hover(self, Theme.BG_FIELD, Theme.BORDER)
        app.model_pickers.append(self)
        self.refresh_label()

    @staticmethod
    def _title(model: Dict[str, Any]) -> str:
        name = model.get("model_name") or model["id"]
        return f"{name} · v{model['version']}" if model.get("version") else name

    def _rebuild_menu(self):
        self.menu.delete(0, "end")
        models = self.app.models()
        if not models:
            self.menu.add_command(label=t("no_models"), state="disabled")
            return
        current = self.app.settings.get("selected_model")
        for model in models:
            mark = "✓ " if model["id"] == current else " "
            label = mark + self._title(model)
            if model.get("description"):
                label += f" — {model['description']}"
            self.menu.add_command(
                label=label, command=lambda mid=model["id"]: self.app.change_model(mid),
            )

    def refresh_label(self):
        model = self.app.model_by_id(self.app.settings.get("selected_model"))
        text = self._title(model) if model else t("no_models")
        self.configure(text=f"{text} ▾")


class RolePicker(tk.Menubutton):
    def __init__(self, parent: tk.Widget, app: Any, width: int = 18):
        self.app = app
        super().__init__(
            parent, text="", font=Theme.FONT_UI, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY,
            activebackground=Theme.BORDER, activeforeground=Theme.TEXT_PRIMARY,
            relief="flat", bd=0, highlightthickness=1, highlightbackground=Theme.BORDER,
            highlightcolor=Theme.BORDER_LIGHT, cursor="hand2", padx=10, pady=5, anchor="w", width=width,
        )
        self.menu = tk.Menu(
            self, tearoff=0, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY,
            activebackground=Theme.BORDER_LIGHT, activeforeground=Theme.TEXT_PRIMARY,
            relief="flat", bd=0, font=Theme.FONT_UI, postcommand=self._rebuild_menu,
        )
        self.configure(menu=self.menu)
        add_hover(self, Theme.BG_FIELD, Theme.BORDER)
        app.role_pickers.append(self)
        self.refresh_label()

    def _rebuild_menu(self):
        self.menu.delete(0, "end")
        roles = self.app.roles()
        current = self.app.settings.get("active_role")
        lang = get_language()

        for role in roles:
            mark = "✓ " if role["id"] == current else " "
            name = role.get("name_ar" if lang == "ar" else "name_en", role["id"])
            icon = role.get("icon", "⚡")
            label = f"{mark}{icon} {name}"
            self.menu.add_command(
                label=label, command=lambda rid=role["id"]: self.app.change_role(rid)
            )

    def refresh_label(self):
        current_id = self.app.settings.get("active_role")
        role = self.app.role_by_id(current_id)
        lang = get_language()
        if role:
            name = role.get("name_ar" if lang == "ar" else "name_en", role["id"])
            icon = role.get("icon", "⚡")
            self.configure(text=f"{icon} {name} ▾")
        else:
            self.configure(text=f"🎓 {current_id} ▾")


def _voice_label(name: str) -> str:
    """Picker label: 'Kore ♀' / 'Puck ♂'."""
    return f"{name} {'♂' if VOICES.get(name) == 'male' else '♀'}"


def _voice_from_label(label: str) -> str:
    return label.split()[0] if label else DEFAULT_VOICE


def _language_label(code: str) -> str:
    for label, lang_code in LANGUAGES.items():
        if lang_code == code:
            return label
    return next(iter(LANGUAGES))


# =========================================================
# Chat History Drawer Window
# =========================================================
class ChatHistoryWindow:
    def __init__(self, parent: tk.Widget, app: Any):
        self.app = app
        self.db = app.settings.db
        self.win = tk.Toplevel(parent)
        self.win.title(t("chat_history"))
        self.win.geometry("420x520")
        self.win.configure(bg=Theme.BG_DARK)
        self.win.resizable(False, False)
        apply_native_titlebar_color(self.win, "#ffffff", text_color="#000000")

        header = tk.Frame(self.win, bg=Theme.BG_DARK)
        header.pack(fill="x", padx=16, pady=(16, 10))

        title_lbl = tk.Label(
            header, text=t("chat_history"), font=Theme.FONT_TITLE, bg=Theme.BG_DARK, fg=Theme.TEXT_PRIMARY
        )
        title_lbl.pack(side="left")

        new_btn = solid_button(header, t("new_chat"), self._create_new_session, padx=12, pady=4)
        new_btn.pack(side="right")

        divider(self.win).pack(fill="x", padx=16, pady=(0, 10))

        container = tk.Frame(self.win, bg=Theme.BG_PANEL, highlightthickness=1, highlightbackground=Theme.BORDER)
        container.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        scroll = MinimalScrollbar(container, width=8)
        scroll.pack(side="right", fill="y", padx=(0, 4), pady=4)

        self.canvas = tk.Canvas(container, bg=Theme.BG_PANEL, highlightthickness=0, bd=0, yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.command = self.canvas.yview

        self.inner_frame = tk.Frame(self.canvas, bg=Theme.BG_PANEL)
        self.win_id = self.canvas.create_window((0, 0), window=self.inner_frame, anchor="nw")

        self.inner_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win_id, width=e.width))

        self.refresh_list()

    def refresh_list(self):
        for w in self.inner_frame.winfo_children():
            w.destroy()

        sessions = self.db.list_sessions(limit=40)
        if not sessions:
            lbl = tk.Label(
                self.inner_frame, text=t("no_chats"), font=Theme.FONT_UI, bg=Theme.BG_PANEL, fg=Theme.TEXT_MUTED
            )
            lbl.pack(pady=40)
            return

        current_sid = self.app.current_session_id
        for s in sessions:
            sid = s["id"]
            is_active = (sid == current_sid)
            row = tk.Frame(
                self.inner_frame,
                bg=Theme.BG_FIELD if is_active else Theme.BG_PANEL,
                highlightthickness=1,
                highlightbackground=Theme.ACCENT if is_active else Theme.BORDER,
            )
            row.pack(fill="x", padx=6, pady=4)

            title_text = s.get("title") or t("new_conversation")
            display_str = f"💬 {title_text[:24]}"

            btn = ghost_button(
                row, display_str, lambda sid=sid: self._switch_to(sid),
                bg=Theme.BG_FIELD if is_active else Theme.BG_PANEL,
                fg=Theme.ACCENT if is_active else Theme.TEXT_PRIMARY,
                padx=8, pady=6, font=Theme.FONT_UI_BOLD if is_active else Theme.FONT_UI
            )
            btn.pack(side="left", fill="x", expand=True)

            edit_btn = tk.Button(
                row, text="✏️", font=("Segoe UI", 9), bg=row["bg"], fg=Theme.TEXT_MUTED,
                activebackground=Theme.BG_FIELD, activeforeground=Theme.TEXT_PRIMARY,
                bd=0, relief="flat", cursor="hand2", padx=4, pady=4,
                command=lambda sid=sid, cur=title_text: self._rename_session(sid, cur)
            )
            edit_btn.pack(side="right", padx=(0, 2))

            del_btn = tk.Button(
                row, text="✕", font=("Segoe UI", 8), bg=row["bg"], fg=Theme.TEXT_MUTED,
                activebackground=Theme.DANGER_HOVER, activeforeground="#ff6b6b",
                bd=0, relief="flat", cursor="hand2", padx=6, pady=4,
                command=lambda sid=sid: self._delete_session(sid)
            )
            del_btn.pack(side="right", padx=4)

    def _rename_session(self, session_id: str, current_title: str):
        dialog = tk.Toplevel(self.win)
        dialog.title(t("rename_dialog_title"))
        dialog.geometry("340x140")
        dialog.configure(bg=Theme.BG_DARK)
        dialog.resizable(False, False)
        apply_native_titlebar_color(dialog, "#ffffff", text_color="#000000")

        tk.Label(dialog, text=t("rename_dialog_label"), font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.TEXT_PRIMARY).pack(anchor="w", padx=16, pady=(14, 4))
        entry = tk.Entry(dialog, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, relief="flat", font=Theme.FONT_UI)
        entry.pack(fill="x", padx=16, ipady=4)
        entry.insert(0, current_title)
        entry.focus_set()

        def save():
            new_val = entry.get().strip()
            if new_val:
                self.db.update_session_title(session_id, new_val)
                self.refresh_list()
            dialog.destroy()

        entry.bind("<Return>", lambda e: save())
        solid_button(dialog, t("save"), save, padx=16, pady=4).pack(pady=12)

    def _switch_to(self, session_id: str):
        self.app.switch_session(session_id)
        self.win.destroy()

    def _delete_session(self, session_id: str):
        self.db.delete_session(session_id)
        if self.app.current_session_id == session_id:
            self._create_new_session()
        else:
            self.refresh_list()

    def _create_new_session(self):
        new_id = self.db.create_session(t("new_conversation"), self.app.active_role)
        self.app.switch_session(new_id)
        self.win.destroy()


# =========================================================
# Visual Flow Studio Window (n8n-style)
# =========================================================
class AutomationFlowWindow:
    def __init__(self, parent: tk.Widget, app: Any):
        self.app = app
        self.db = app.settings.db
        self.win = tk.Toplevel(parent)
        self.win.title(t("flow_studio_title"))
        self.win.geometry("860x600")
        self.win.configure(bg=Theme.BG_DARK)
        self.win.minsize(760, 500)
        apply_native_titlebar_color(self.win, "#ffffff", text_color="#000000")

        header = tk.Frame(self.win, bg=Theme.BG_DARK)
        header.pack(fill="x", padx=20, pady=(16, 12))

        left_header = tk.Frame(header, bg=Theme.BG_DARK)
        left_header.pack(side="left")

        tk.Label(left_header, text=f"⚡ {t('flow_studio_title')}", font=Theme.FONT_TITLE, bg=Theme.BG_DARK, fg=Theme.TEXT_PRIMARY).pack(side="left")
        
        status_badge = tk.Label(
            left_header, text=t("flow_scheduler_active"), font=("Segoe UI", 8, "bold"),
            bg=Theme.BG_FIELD, fg="#3fb8a5", padx=8, pady=2
        )
        status_badge.pack(side="left", padx=12)

        right_header = tk.Frame(header, bg=Theme.BG_DARK)
        right_header.pack(side="right")

        solid_button(right_header, t("add_new_flow"), self._add_dialog, padx=14, pady=5).pack(side="left", padx=4)
        ghost_button(right_header, t("refresh"), self.refresh, padx=10, pady=5).pack(side="left")

        divider(self.win).pack(fill="x", padx=20, pady=(0, 10))

        canvas_wrap = tk.Frame(self.win, bg=Theme.BG_PANEL, highlightthickness=1, highlightbackground=Theme.BORDER)
        canvas_wrap.pack(fill="both", expand=True, padx=20, pady=(0, 18))

        scroll = MinimalScrollbar(canvas_wrap, width=8)
        scroll.pack(side="right", fill="y", padx=(0, 4), pady=4)

        self.canvas = tk.Canvas(canvas_wrap, bg=Theme.BG_PANEL, highlightthickness=0, bd=0, yscrollcommand=scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scroll.command = self.canvas.yview

        self.refresh()

    def _draw_canvas_grid(self, max_w: int, max_h: int):
        self.canvas.delete("grid_dot")
        grid_step = 28
        dot_color = Theme.BORDER_LIGHT if Theme.mode == "dark" else "#d0d8e0"
        for x in range(14, max_w, grid_step):
            for y in range(14, max_h, grid_step):
                self.canvas.create_rectangle(x, y, x + 1, y + 1, fill=dot_color, outline="", tags="grid_dot")
        self.canvas.tag_lower("grid_dot")

    def refresh(self):
        self.canvas.delete("all")
        tasks = self.db.list_scheduled_tasks()
        y = 35

        if not tasks:
            self._draw_canvas_grid(840, 520)
            self.canvas.create_text(
                400, 200,
                text=t("no_flows_desc"),
                fill=Theme.TEXT_MUTED, font=Theme.FONT_UI, justify="center"
            )
            return

        for task in tasks:
            self._render_n8n_flow(task, y)
            y += 115

        self.canvas.configure(scrollregion=(0, 0, 820, max(y + 30, 550)))
        self._draw_canvas_grid(840, max(y + 40, 560))

    def _render_n8n_flow(self, task: Dict[str, Any], y: int):
        tid = task["id"]
        is_active = bool(task.get("is_active", 1))

        # Node 1: Trigger Node
        self._draw_node(
            x=30, y=y, w=150, h=68, title=t("node_trigger"),
            sub=t("node_every_minutes", mins=task.get("trigger_value", "60")),
            tag_color="#8b5cf6", active=is_active
        )

        self.canvas.create_line(180, y + 34, 225, y + 34, fill="#8b5cf6" if is_active else Theme.BORDER, width=2, arrow=tk.LAST)

        # Node 2: Persona Node
        role_name = task.get("role_id", "student").capitalize()
        self._draw_node(
            x=225, y=y, w=145, h=68, title=t("node_persona"),
            sub=role_name,
            tag_color="#10b981", active=is_active
        )

        self.canvas.create_line(370, y + 34, 415, y + 34, fill="#10b981" if is_active else Theme.BORDER, width=2, arrow=tk.LAST)

        # Node 3: Execution Node
        self._draw_node(
            x=415, y=y, w=220, h=68, title=t("node_action"),
            sub=task.get("title", "")[:26] or task.get("prompt", "")[:26],
            tag_color="#3b82f6", active=is_active
        )

        btn_frame = tk.Frame(self.canvas, bg=Theme.BG_PANEL)
        
        run_btn = tk.Button(
            btn_frame, text=t("flow_run_now"), font=("Segoe UI", 8, "bold"),
            bg=Theme.BG_FIELD, fg=Theme.ACCENT, bd=0, relief="flat", cursor="hand2", padx=8, pady=4,
            command=lambda tid=tid: self._run_now(tid)
        )
        run_btn.pack(side="left", padx=4)

        toggle_txt = t("flow_active") if is_active else t("flow_inactive")
        toggle_fg = "#10b981" if is_active else Theme.TEXT_MUTED
        toggle_btn = tk.Button(
            btn_frame, text=toggle_txt, font=("Segoe UI", 8, "bold"),
            bg=Theme.BG_FIELD, fg=toggle_fg, bd=0, relief="flat", cursor="hand2", padx=8, pady=4,
            command=lambda tid=tid, act=is_active: self._toggle_flow(tid, not act)
        )
        toggle_btn.pack(side="left", padx=4)

        del_btn = tk.Button(
            btn_frame, text="✕", font=("Segoe UI", 9, "bold"),
            bg=Theme.BG_FIELD, fg="#ff6b6b", bd=0, relief="flat", cursor="hand2", padx=6, pady=4,
            command=lambda tid=tid: self._delete_flow(tid)
        )
        del_btn.pack(side="left", padx=4)

        self.canvas.create_window(710, y + 34, window=btn_frame)

    def _draw_node(self, x: int, y: int, w: int, h: int, title: str, sub: str, tag_color: str, active: bool):
        border_col = tag_color if active else Theme.BORDER
        bg_col = Theme.BG_FIELD if active else Theme.BG_PANEL

        self.canvas.create_rectangle(x, y, x + w, y + h, fill=bg_col, outline=border_col, width=2)
        self.canvas.create_rectangle(x, y, x + w, y + 5, fill=tag_color if active else Theme.BORDER, outline="")
        self.canvas.create_text(x + 12, y + 24, text=title, font=Theme.FONT_UI_BOLD, fill=Theme.TEXT_PRIMARY, anchor="w")
        self.canvas.create_text(x + 12, y + 46, text=sub, font=Theme.FONT_LABEL, fill=Theme.TEXT_SECONDARY, anchor="w")
        self.canvas.create_oval(x - 4, y + h / 2 - 4, x + 4, y + h / 2 + 4, fill=border_col, outline="")
        self.canvas.create_oval(x + w - 4, y + h / 2 - 4, x + w + 4, y + h / 2 + 4, fill=border_col, outline="")

    def _run_now(self, task_id: str):
        from core.automation_service import execute_flow_task
        threading.Thread(target=lambda: execute_flow_task(task_id), daemon=True).start()

    def _toggle_flow(self, task_id: str, new_state: bool):
        from core.automation_service import (
            parse_interval_minutes, register_windows_task, unregister_windows_task,
        )
        self.db.toggle_scheduled_task(task_id, new_state)

        tasks = self.db.list_scheduled_tasks()
        task = next((x for x in tasks if x["id"] == task_id), None)
        if task:
            if new_state:
                mins = parse_interval_minutes(task.get("trigger_value"))
                worker = lambda: register_windows_task(task_id, mins)
            else:
                worker = lambda: unregister_windows_task(task_id)
            # PowerShell takes several seconds: never run it on the UI thread
            threading.Thread(target=worker, daemon=True).start()
        self.refresh()

    def _delete_flow(self, task_id: str):
        from core.automation_service import unregister_windows_task
        threading.Thread(target=lambda: unregister_windows_task(task_id), daemon=True).start()
        self.db.delete_scheduled_task(task_id)
        self.refresh()

    def _add_dialog(self):
        dialog = tk.Toplevel(self.win)
        dialog.title(t("dialog_new_flow"))
        dialog.geometry("420x420")
        dialog.configure(bg=Theme.BG_DARK)
        dialog.resizable(False, False)
        apply_native_titlebar_color(dialog, "#ffffff", text_color="#000000")

        tk.Label(dialog, text=t("dialog_flow_name"), font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.TEXT_PRIMARY).pack(anchor="w", padx=20, pady=(16, 2))
        title_e = tk.Entry(dialog, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, relief="flat", font=Theme.FONT_UI)
        title_e.pack(fill="x", padx=20, ipady=4)
        title_e.insert(0, "Academic Revision Check")

        tk.Label(dialog, text=t("dialog_interval_min"), font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.TEXT_PRIMARY).pack(anchor="w", padx=20, pady=(12, 2))
        val_e = tk.Entry(dialog, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, relief="flat", font=Theme.FONT_UI)
        val_e.pack(fill="x", padx=20, ipady=4)
        val_e.insert(0, "60")

        tk.Label(dialog, text=t("dialog_specialist"), font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.TEXT_PRIMARY).pack(anchor="w", padx=20, pady=(12, 2))
        role_var = tk.StringVar(value="student")
        role_menu = tk.OptionMenu(dialog, role_var, "student", "coder", "thinker", "career", "general")
        role_menu.config(bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, relief="flat", bd=0)
        role_menu.pack(anchor="w", padx=20)

        tk.Label(dialog, text=t("dialog_prompt"), font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.TEXT_PRIMARY).pack(anchor="w", padx=20, pady=(12, 2))
        prompt_e = tk.Entry(dialog, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, relief="flat", font=Theme.FONT_UI)
        prompt_e.pack(fill="x", padx=20, ipady=4)
        prompt_e.insert(0, "Show a desktop notification with a Feynman-style quiz question.")

        def save():
            import uuid
            from core.automation_service import register_windows_task, parse_interval_minutes
            tid = f"flow_{uuid.uuid4().hex[:6]}"
            mins = parse_interval_minutes(val_e.get())
            self.db.upsert_scheduled_task(
                tid, title_e.get().strip(), "interval", str(mins),
                prompt_e.get().strip(), role_var.get()
            )

            save_btn.configure(state="disabled")
            status = tk.Label(dialog, text="", font=Theme.FONT_UI, bg=Theme.BG_DARK, fg=Theme.ACCENT)
            status.pack(pady=(0, 10))

            result = {"done": False, "ok": False}

            def work():
                result["ok"] = register_windows_task(tid, mins)
                result["done"] = True

            threading.Thread(target=work, daemon=True).start()

            frames = ("●○○", "○●○", "○○●", "○●○")

            def tick(i=0):
                try:
                    if not dialog.winfo_exists():
                        return
                except tk.TclError:
                    return
                if result["done"]:
                    if result["ok"]:
                        self.db.toggle_scheduled_task(tid, True)
                        status.configure(text=t("flow_activated"))
                        dialog.after(700, lambda: (dialog.destroy(), self.refresh()))
                    else:
                        status.configure(text=t("flow_activation_failed"), fg="#ff6b6b")
                        save_btn.configure(state="normal")
                        self.refresh()
                    return
                status.configure(text=f"{t('flow_activating')} {frames[i % 4]}")
                dialog.after(350, lambda: tick(i + 1))

            tick()

        save_btn = solid_button(dialog, t("dialog_save_activate"), save, padx=18, pady=5)
        save_btn.pack(pady=(22, 8))


# =========================================================
# Settings Window
# =========================================================
class CredentialsWindow:
    """View / edit the API key, Gmail address and Gmail app password stored in api_keys.env."""

    FIELDS = (
        ("AI_API_KEY", "cred_api_key", True),
        ("SENDER_EMAIL", "cred_gmail", False),
        ("APP_PASSWRD", "cred_app_password", True),
    )

    def __init__(self, parent: tk.Widget, app: Any):
        self.app = app
        live = app.live
        values = read_env_values()

        self.window = tk.Toplevel(parent)
        self.window.title(t("cred_title"))
        self.window.geometry("520x470")
        self.window.configure(bg=Theme.BG_DARK)
        self.window.resizable(False, False)
        self.window.transient(parent)
        apply_native_titlebar_color(self.window, "#ffffff", text_color="#000000")

        header = tk.Frame(self.window, bg=Theme.BG_DARK)
        header.pack(fill="x", padx=24, pady=(18, 6))
        title_lbl = tk.Label(header, text="", font=("Segoe UI", 10, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        live.bind(title_lbl, lambda: t("credentials"))
        title_lbl.pack(side="left")

        divider(self.window).pack(fill="x", padx=24, pady=(10, 14))

        body = tk.Frame(self.window, bg=Theme.BG_DARK)
        body.pack(fill="both", expand=True, padx=24)

        if not any(values.values()):
            hint = tk.Label(
                body, text="", font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.ACCENT_HOVER,
                wraplength=460, justify="left",
            )
            live.bind(hint, lambda: t("cred_empty_hint"))
            hint.pack(anchor="w", pady=(0, 12))

        self.vars: Dict[str, tk.StringVar] = {}
        self.entries: Dict[str, tk.Entry] = {}
        for key, label_key, secret in self.FIELDS:
            lbl = tk.Label(body, text="", font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.TEXT_SECONDARY)
            live.bind(lbl, lambda k=label_key: t(k))
            lbl.pack(anchor="w", pady=(0, 6))

            row = tk.Frame(body, bg=Theme.BG_DARK)
            row.pack(fill="x", pady=(0, 14))

            wrap = tk.Frame(
                row, bg=Theme.BG_FIELD, highlightthickness=1,
                highlightbackground=Theme.BORDER, highlightcolor=Theme.BORDER_LIGHT,
            )
            wrap.pack(side="left", fill="x", expand=True)

            var = tk.StringVar(value=values.get(key, ""))
            entry = tk.Entry(
                wrap, textvariable=var, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY,
                insertbackground=Theme.TEXT_PRIMARY, relief="flat", bd=0, font=Theme.FONT_UI,
                show="\u2022" if secret else "",
            )
            entry.pack(fill="x", ipady=7, padx=10)
            entry.bind("<Return>", lambda e: self.save())
            self.vars[key] = var
            self.entries[key] = entry

            if secret:
                eye = ghost_button(row, "\U0001F441", lambda en=entry: self._toggle(en), padx=8, pady=2)
                eye.pack(side="left", padx=(8, 0))

        file_lbl = tk.Label(
            body, text="", font=("Segoe UI", 8), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED,
            wraplength=460, justify="left",
        )
        live.bind(file_lbl, lambda: t("cred_file", path=str(ENV_FILE)))
        file_lbl.pack(anchor="w", pady=(0, 2))

        note_lbl = tk.Label(body, text="", font=("Segoe UI", 8), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        live.bind(note_lbl, lambda: t("cred_note"))
        note_lbl.pack(anchor="w", pady=(0, 12))

        actions = tk.Frame(body, bg=Theme.BG_DARK)
        actions.pack(fill="x")
        self.save_btn = solid_button(actions, "", self.save)
        live.bind(self.save_btn, lambda: t("save"))
        self.save_btn.pack(side="left")

        self.status = tk.Label(
            actions, text="", font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.ACCENT_HOVER,
            wraplength=320, justify="left",
        )
        self.status.pack(side="left", padx=(12, 0))

    @staticmethod
    def _toggle(entry: tk.Entry):
        entry.configure(show="" if entry.cget("show") else "\u2022")

    def _set_status(self, text: str, error: bool = False):
        try:
            self.status.configure(text=text, fg="#ff6b6b" if error else Theme.ACCENT_HOVER)
            self.window.after(3000, lambda: self._clear_status(text))
        except tk.TclError:
            pass

    def _clear_status(self, text: str):
        try:
            if self.status.cget("text") == text:
                self.status.configure(text="")
        except tk.TclError:
            pass

    def save(self):
        values = {key: self.vars[key].get().strip() for key in ENV_FIELDS}
        email = values["SENDER_EMAIL"]
        if email and ("@" not in email or " " in email):
            self._set_status(t("cred_invalid_email"), error=True)
            return
        try:
            save_env_values(values)
        except Exception as exc:
            self._set_status(t("cred_save_failed", error=str(exc)[:140]), error=True)
            return
        self._set_status(t("saved"))


class SettingsWindow:
    def __init__(self, parent: tk.Widget, app: Any):
        self.app = app
        live = app.live

        self.window = tk.Toplevel(parent)
        self.window.title(t("settings_title"))
        self.window.geometry("600x580")
        self.window.configure(bg=Theme.BG_DARK)
        self.window.resizable(False, False)

        apply_native_titlebar_color(self.window, "#ffffff", text_color="#000000")

        header = tk.Frame(self.window, bg=Theme.BG_DARK)
        header.pack(fill="x", padx=20, pady=(18, 6))

        self.back_btn = ghost_button(header, "", self.window.destroy, padx=6, pady=2)
        live.bind(self.back_btn, lambda: t("back"))
        self.back_btn.pack(side="left")

        title_lbl = tk.Label(header, text="", font=("Segoe UI", 10, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        live.bind(title_lbl, lambda: t("settings"))
        title_lbl.pack(side="left", expand=True)

        self.about_btn = solid_button(header, "", self.open_about_web)
        live.bind(self.about_btn, lambda: t("about"))
        self.about_btn.pack(side="right")

        header_div = divider(self.window)
        header_div.pack(fill="x", padx=20, pady=(10, 18))

        body = tk.Frame(self.window, bg=Theme.BG_DARK)
        body.pack(fill="both", expand=True, padx=20)

        left_col = tk.Frame(body, bg=Theme.BG_DARK)
        left_col.pack(side="left", fill="y", padx=(0, 24))

        section_label = tk.Label(left_col, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        live.bind(section_label, lambda: t("general"))
        section_label.pack(anchor="w", pady=(0, 12))

        self.theme_btn = outline_button(left_col, "", app.toggle_theme)
        live.bind(self.theme_btn, lambda: t("theme_to_light") if Theme.mode == "dark" else t("theme_to_dark"))
        self.theme_btn.pack(anchor="w", pady=(0, 20))

        self.lang_label = tk.Label(left_col, text="", font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.TEXT_SECONDARY)
        live.bind(self.lang_label, lambda: t("language"))
        self.lang_label.pack(anchor="w", pady=(0, 6))

        self.lang_var = tk.StringVar(value=_language_label(get_language()))
        lang_field = tk.Frame(
            left_col, bg=Theme.BG_FIELD, highlightthickness=1, highlightbackground=Theme.BORDER, highlightcolor=Theme.BORDER
        )
        lang_field.pack(anchor="w")

        self.lang_dropdown = tk.OptionMenu(lang_field, self.lang_var, *LANGUAGES.keys(), command=self._on_language)
        self.lang_dropdown.config(
            bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, relief="flat", bd=0, activebackground=Theme.BORDER_LIGHT,
            activeforeground=Theme.TEXT_PRIMARY, highlightthickness=0, font=Theme.FONT_UI, width=12, anchor="w",
        )
        self.lang_dropdown["menu"].config(
            bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, activebackground=Theme.BORDER_LIGHT,
            activeforeground=Theme.TEXT_PRIMARY, relief="flat", bd=0,
        )
        self.lang_dropdown.pack(ipady=5, padx=4)

        model_label = tk.Label(left_col, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        live.bind(model_label, lambda: t("ai_model"))
        model_label.pack(anchor="w", pady=(24, 8))

        self.model_picker = ModelPicker(left_col, app, width=20)
        self.model_picker.pack(anchor="w")

        if VOICE_AVAILABLE:
            voice_section = tk.Label(left_col, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
            live.bind(voice_section, lambda: t("voice_section"))
            voice_section.pack(anchor="w", pady=(24, 8))

            voice_row = tk.Frame(left_col, bg=Theme.BG_DARK)
            voice_row.pack(anchor="w")

            self.voice_var = tk.StringVar(value=_voice_label(app.settings.get("tts_voice")))
            voice_field = tk.Frame(
                voice_row, bg=Theme.BG_FIELD, highlightthickness=1, highlightbackground=Theme.BORDER, highlightcolor=Theme.BORDER
            )
            voice_field.pack(side="left")

            self.voice_dropdown = tk.OptionMenu(
                voice_field, self.voice_var, *[_voice_label(n) for n in VOICES], command=self._on_voice
            )
            self.voice_dropdown.config(
                bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, relief="flat", bd=0, activebackground=Theme.BORDER_LIGHT,
                activeforeground=Theme.TEXT_PRIMARY, highlightthickness=0, font=Theme.FONT_UI, width=11, anchor="w",
            )
            self.voice_dropdown["menu"].config(
                bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, activebackground=Theme.BORDER_LIGHT,
                activeforeground=Theme.TEXT_PRIMARY, relief="flat", bd=0,
            )
            self.voice_dropdown.pack(ipady=5, padx=4)

            self.voice_preview_btn = outline_button(voice_row, "", self.app.preview_voice)
            live.bind(self.voice_preview_btn, lambda: t("voice_preview"))
            self.voice_preview_btn.pack(side="left", padx=(8, 0))

        vdiv = divider(body, orient="v")
        vdiv.pack(side="left", fill="y", pady=4)

        right_col = tk.Frame(body, bg=Theme.BG_DARK)
        right_col.pack(side="left", fill="both", expand=True, padx=(24, 0))

        profile_label = tk.Label(right_col, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        live.bind(profile_label, lambda: t("profile"))
        profile_label.pack(anchor="w", pady=(0, 12))

        name_label = tk.Label(right_col, text="", font=Theme.FONT_LABEL, bg=Theme.BG_DARK, fg=Theme.TEXT_SECONDARY)
        live.bind(name_label, lambda: t("user_nickname"))
        name_label.pack(anchor="w", pady=(0, 6))

        wrap = tk.Frame(
            right_col, bg=Theme.BG_FIELD, highlightthickness=1, highlightbackground=Theme.BORDER, highlightcolor=Theme.BORDER_LIGHT
        )
        wrap.pack(fill="x", pady=(0, 12))

        self.user_entry = tk.Entry(
            wrap, bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY, insertbackground=Theme.TEXT_PRIMARY,
            relief="flat", bd=0, font=Theme.FONT_UI
        )
        self.user_entry.pack(fill="x", ipady=7, padx=10)
        self.user_entry.insert(0, app.settings.get("user_nickname"))
        self.user_entry.bind("<Return>", lambda e: self.save_name())

        buttons_row = tk.Frame(right_col, bg=Theme.BG_DARK)
        buttons_row.pack(fill="x", pady=(0, 24))

        self.save_btn = outline_button(buttons_row, "", self.save_name)
        live.bind(self.save_btn, lambda: t("save"))
        self.save_btn.pack(side="left")

        self.subscription_btn = solid_button(buttons_row, "", self.open_subscription)
        live.bind(self.subscription_btn, lambda: t("subscription"))
        self.subscription_btn.pack(side="left", padx=(10, 0))

        account_label = tk.Label(right_col, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        live.bind(account_label, lambda: t("account"))
        account_label.pack(anchor="w", pady=(0, 12))

        account_row = tk.Frame(right_col, bg=Theme.BG_DARK)
        account_row.pack(fill="x")

        self.login_btn = outline_button(account_row, "", self.open_login)
        live.bind(self.login_btn, lambda: t("login"))
        self.login_btn.pack(side="left")

        self.sign_up_btn = outline_button(account_row, "", self.open_sign_in)
        live.bind(self.sign_up_btn, lambda: t("sign_up"))
        self.sign_up_btn.pack(side="left", padx=(10, 0))

        conn_label = tk.Label(right_col, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        live.bind(conn_label, lambda: t("connections_section"))
        conn_label.pack(anchor="w", pady=(24, 12))

        self.keys_btn = outline_button(right_col, "", self.open_credentials)
        live.bind(self.keys_btn, lambda: t("credentials"))
        self.keys_btn.pack(anchor="w")

    def _on_language(self, label: str):
        self.app.change_language(LANGUAGES.get(label, "en"))

    def _on_voice(self, label: str):
        self.app.change_voice(_voice_from_label(label))

    def open_credentials(self):
        win = getattr(self, "_cred_win", None)
        try:
            alive = win is not None and bool(win.window.winfo_exists())
        except tk.TclError:
            alive = False
        if alive:
            win.window.lift()
            win.window.focus_force()
            return
        self._cred_win = CredentialsWindow(self.window, self.app)

    def save_name(self):
        self.app.set_nickname(self.user_entry.get())
        self.save_btn.configure(text=t("saved"))
        self.window.after(1500, self._restore_save_label)

    def _restore_save_label(self):
        try:
            self.save_btn.configure(text=t("save"))
        except tk.TclError:
            pass

    def open_about_web(self):
        webbrowser.open(URL_ABOUT)

    def open_subscription(self):
        webbrowser.open(URL_SUBSCRIPTION)

    def open_login(self):
        webbrowser.open(URL_LOGIN)

    def open_sign_in(self):
        webbrowser.open(URL_SIGN_IN)


# =========================================================
# Main SaiA Application Coordinator
# =========================================================
class SaiAApp:
    def __init__(self):
        self.settings = UserSettings()
        Theme.apply(self.settings.get("theme"))
        set_language(self.settings.get("language"))
        self.live = LiveText()
        self._settings_win = None
        self.model_pickers: List[ModelPicker] = []
        self.role_pickers: List[RolePicker] = []
        self.attached_file: Optional[str] = None
        self.attached_files: List[str] = []
        self.recorder = Recorder() if VOICE_AVAILABLE else None
        self.speaker = Speaker() if VOICE_AVAILABLE else None
        set_media_voice(self.settings.get("tts_voice"))
        self._mic_btns: List[tk.Widget] = []
        self._mic_target: Optional[tk.Text] = None
        self._stt_busy = False
        self._bubbles: List[Tuple[tk.Canvas, int, int, int]] = []
        self._rich_texts: List[tk.Text] = []
        self._cards: List[Any] = []

        self.active_role = self.settings.get("active_role") or "student"

        latest = self.settings.db.get_latest_session()
        if latest:
            self.current_session_id = latest["id"]
            self.active_role = latest.get("role_id") or self.active_role
            self.agent = ai_agent(role_id=self.active_role, session_id=self.current_session_id)
        else:
            self.agent = ai_agent(role_id=self.active_role)
            self.current_session_id = self.agent.session_id

        self.root = tk.Tk()
        self.root.title(t("app_title"))
        self.root.geometry("480x600")
        self.root.configure(bg=Theme.BG_DARK)

        self.EX2_SIZE = 110

        self._app_icon = load_logo_photo()
        if self._app_icon:
            self.root.iconphoto(True, self._app_icon)

        apply_native_titlebar_color(self.root, "#ffffff", text_color="#000000")

        self._pending = set()
        self._think_tick = 0
        self._think_job = None
        self._steps: List[str] = []
        self._current_status: Optional[str] = None
        self._rows_shown = 0

        self.worker = QueryWorker(self.agent)
        self.worker.start()
        self._poll_results()

        self.setup_ex1_full_window()
        self.setup_ex2_floating_icon()
        self.setup_ex3_compact_bar()

        self.switch_session(self.current_session_id)
        self.show_ex1()

    def setup_ex1_full_window(self):
        self.frame_ex1 = tk.Frame(self.root, bg=Theme.BG_DARK)

        header = tk.Frame(self.frame_ex1, bg=Theme.BG_DARK)
        header.pack(fill="x", pady=(18, 0), padx=20)

        self._header_logo = load_logo_photo(max_size=30)
        if self._header_logo:
            logo_lbl = tk.Label(header, image=self._header_logo, bg=Theme.BG_DARK)
            logo_lbl.pack(side="left", padx=(0, 8))

        title_lbl = tk.Label(header, text="SaiA", font=Theme.FONT_TITLE, bg=Theme.BG_DARK, fg=Theme.TEXT_PRIMARY)
        title_lbl.pack(side="left")

        subtitle_lbl = tk.Label(header, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        subtitle_lbl.pack(side="left", pady=(6, 0))
        self.live.bind(subtitle_lbl, lambda: "  AGENT")

        controls = tk.Frame(header, bg=Theme.BG_DARK)
        controls.pack(side="right")

        btn_ex4 = ghost_button(controls, "⚙", self.open_settings, padx=7, pady=4, font=("Segoe UI", 11))
        btn_ex4.pack(side="right", padx=(4, 0))

        btn_hist = ghost_button(controls, "📜", self.open_history, padx=7, pady=4, font=("Segoe UI", 11))
        btn_hist.pack(side="right", padx=(4, 0))

        btn_flow = ghost_button(controls, "⚡", self.open_flows, padx=7, pady=4, font=("Segoe UI", 11))
        btn_flow.pack(side="right", padx=(4, 0))

        btn_ex3 = ghost_button(controls, "▭", self.show_ex3, padx=7, pady=4, font=("Segoe UI", 11))
        btn_ex3.pack(side="right", padx=(4, 0))

        btn_ex2 = ghost_button(controls, "⤢", self.show_ex2, padx=7, pady=4, font=("Segoe UI", 11))
        btn_ex2.pack(side="right", padx=(4, 0))

        top_div = divider(self.frame_ex1)
        top_div.pack(fill="x", padx=20, pady=(14, 10))

        selectors_row = tk.Frame(self.frame_ex1, bg=Theme.BG_DARK)
        selectors_row.pack(fill="x", padx=20, pady=(0, 12))

        model_wrap = tk.Frame(selectors_row, bg=Theme.BG_DARK)
        model_wrap.pack(side="left", fill="x", expand=True, padx=(0, 6))

        model_lbl = tk.Label(model_wrap, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        self.live.bind(model_lbl, lambda: t("model").upper())
        model_lbl.pack(anchor="w", pady=(0, 4))
        ModelPicker(model_wrap, self, width=16).pack(fill="x")

        role_wrap = tk.Frame(selectors_row, bg=Theme.BG_DARK)
        role_wrap.pack(side="right", fill="x", expand=True, padx=(6, 0))

        role_lbl = tk.Label(role_wrap, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_DARK, fg=Theme.TEXT_MUTED)
        self.live.bind(role_lbl, lambda: t("specialist").upper())
        role_lbl.pack(anchor="w", pady=(0, 4))
        RolePicker(role_wrap, self, width=16).pack(fill="x")

        console_wrap = tk.Frame(
            self.frame_ex1, bg=Theme.BG_PANEL, highlightthickness=1, highlightbackground=Theme.BORDER
        )
        console_wrap.pack(fill="both", expand=True, padx=20)

        console_label = tk.Label(
            console_wrap, text="", font=("Segoe UI", 8, "bold"), bg=Theme.BG_PANEL, fg=Theme.TEXT_MUTED, anchor="w"
        )
        console_label.pack(fill="x", pady=(10, 4), padx=6)
        self.live.bind(console_label, lambda: f"  {t('agent_terminal')}")

        text_frame = tk.Frame(console_wrap, bg=Theme.BG_PANEL)
        text_frame.pack(fill="both", expand=True, padx=4, pady=(0, 4))

        scrollbar = MinimalScrollbar(text_frame, width=8)
        scrollbar.pack(side="right", fill="y", padx=(0, 4), pady=4)

        self.output_box = tk.Text(
            text_frame, height=16, bg=Theme.BG_PANEL, fg=Theme.TEXT_PRIMARY,
            insertbackground=Theme.TEXT_PRIMARY, relief="flat", bd=0, font=Theme.FONT_MONO,
            wrap="word", padx=10, pady=4, highlightthickness=0, yscrollcommand=scrollbar.set,
        )
        scrollbar.command = self.output_box.yview

        self.output_box.tag_configure("user_tag", foreground=Theme.TEXT_SECONDARY)
        self.output_box.tag_configure("bot_tag", foreground=Theme.ACCENT_HOVER)
        self.output_box.tag_configure("thinking", foreground=Theme.TEXT_MUTED)
        self.output_box.tag_configure("name_user", foreground=Theme.TEXT_SECONDARY, font=("Segoe UI", 8, "bold"), spacing1=4, spacing3=3)
        self.output_box.tag_configure("name_bot", foreground=Theme.ACCENT_HOVER, font=("Segoe UI", 8, "bold"), spacing1=4, spacing3=3)
        self.output_box.tag_configure("right_align", justify="right", rmargin=8)
        self.output_box.tag_configure("left_align", justify="left", lmargin1=8, lmargin2=8)
        self.output_box.tag_configure("gap", spacing3=12)
        self.output_box.pack(side="left", fill="both", expand=True)

        self.output_box.bind(
            "<Configure>",
            lambda e: [
                (cv.configure(width=w), cv.itemconfigure(win, width=w - 2 * pad))
                for cv, win, natural, pad in self._bubbles
                for w in [max(140, min(e.width - 44, natural))]
            ],
        )
        self.output_box.configure(state="disabled")
        self.output_box.bind("<Button-1>", lambda e: self.output_box.focus_set())

        input_frame = tk.Frame(self.frame_ex1, bg=Theme.BG_DARK)
        input_frame.pack(fill="x", padx=20, pady=16)
        self.input_frame1 = input_frame
        self.tray = tk.Frame(self.frame_ex1, bg=Theme.BG_PANEL, highlightthickness=1, highlightbackground=Theme.BORDER)

        attach_btn = ghost_button(input_frame, "📎", self.attach_file, padx=8, pady=4, font=("Segoe UI", 12))
        attach_btn.pack(side="left", padx=(0, 8))
        if VOICE_AVAILABLE:
            mic1 = ghost_button(input_frame, "🎤", lambda: self.toggle_mic(self.entry_ex1), padx=8, pady=4, font=("Segoe UI", 12))
            mic1.pack(side="left", padx=(0, 8))
            self._mic_btns.append(mic1)

        entry_wrap = tk.Frame(
            input_frame, bg=Theme.BG_FIELD, highlightthickness=1,
            highlightbackground=Theme.BORDER, highlightcolor=Theme.BORDER_LIGHT
        )
        entry_wrap.pack(side="left", fill="x", expand=True)

        entry_scroll1 = MinimalScrollbar(entry_wrap, width=6)
        entry_scroll1.configure(bg=Theme.BG_FIELD)
        self.entry_ex1 = tk.Text(
            entry_wrap, height=1, width=1, wrap="word", bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY,
            insertbackground=Theme.TEXT_PRIMARY, relief="flat", bd=0, highlightthickness=0,
            font=Theme.FONT_MONO,
            yscrollcommand=lambda lo, hi: (
                entry_scroll1.set(lo, hi),
                entry_scroll1.pack(side="right", fill="y", padx=(0, 6), pady=6)
                if float(hi) - float(lo) < 0.999 else entry_scroll1.pack_forget(),
            ),
        )
        entry_scroll1.command = self.entry_ex1.yview
        self.entry_ex1.pack(side="left", fill="both", expand=True, pady=9, padx=(12, 8))

        _grow1 = lambda e: (
            e.widget.configure(height=min(6, max(1, (e.widget.count("1.0", "end", "displaylines") or (1,))[0])))
            if e.widget.winfo_ismapped() else None
        )
        for _seq in ("<KeyRelease>", "<ButtonRelease>", "<<Resize>>"):
            self.entry_ex1.bind(_seq, _grow1)
        self.entry_ex1.bind("<Return>", lambda event: (self.send_prompt(self.entry_ex1), "break")[1])
        self.entry_ex1.bind("<Shift-Return>", lambda event: None)
        self.entry_ex1.bind("<Escape>", lambda event: (self.cancel_current(), "break")[1] if self._pending else None)

        self.send_btn1 = send_btn = tk.Button(
            input_frame, text="", font=Theme.FONT_UI_BOLD, bg=Theme.ACCENT, fg=Theme.BG_DARK,
            activebackground=Theme.ACCENT_HOVER, activeforeground=Theme.BG_DARK, relief="flat",
            bd=0, cursor="hand2", padx=18, command=lambda: self._send_or_cancel(self.entry_ex1),
        )
        add_hover(send_btn, Theme.ACCENT, Theme.ACCENT_HOVER, Theme.BG_DARK, Theme.BG_DARK)
        send_btn.pack(side="right", padx=(10, 0), ipady=8)
        self.live.bind(send_btn, lambda: self._send_label())

    def setup_ex2_floating_icon(self):
        self.orb = FloatingOrb(self.root, self.EX2_SIZE, on_click=self.show_ex1)

    def setup_ex3_compact_bar(self):
        self.frame_ex3 = tk.Frame(self.root, bg=Theme.BG_DARK)

        bar_container = tk.Frame(
            self.frame_ex3, bg=Theme.BG_FIELD, highlightthickness=1, highlightbackground=Theme.BORDER
        )
        bar_container.pack(fill="both", expand=True, pady=10, padx=10)

        left_icon = self.left_icon3 = tk.Label(bar_container, text=" ❯ ", font=("Segoe UI", 11, "bold"), bg=Theme.BG_FIELD, fg=Theme.TEXT_SECONDARY)
        left_icon.pack(side="left", padx=(6, 2))

        icon_div = divider(bar_container, orient="v")
        icon_div.pack(side="left", fill="y", pady=8)

        entry_scroll3 = MinimalScrollbar(bar_container, width=6)
        entry_scroll3.configure(bg=Theme.BG_FIELD)
        self.entry_ex3 = tk.Text(
            bar_container, height=1, width=1, wrap="word", bg=Theme.BG_FIELD, fg=Theme.TEXT_PRIMARY,
            insertbackground=Theme.TEXT_PRIMARY, relief="flat", bd=0, highlightthickness=0, font=Theme.FONT_MONO,
            yscrollcommand=lambda lo, hi: (
                entry_scroll3.set(lo, hi),
                entry_scroll3.pack(side="left", fill="y", padx=(0, 4), pady=6, after=self.entry_ex3)
                if float(hi) - float(lo) < 0.999 else entry_scroll3.pack_forget(),
            ),
        )
        entry_scroll3.command = self.entry_ex3.yview
        self.entry_ex3.pack(side="left", fill="x", expand=True, padx=10, pady=6)

        _grow3 = lambda e: (
            (
                e.widget.configure(height=min(6, max(1, (e.widget.count("1.0", "end", "displaylines") or (1,))[0]))),
                self.root.update_idletasks(),
                self.root.geometry(f"{self.root.winfo_width()}x{max(60, self.frame_ex3.winfo_reqheight())}"),
            )
            if e.widget.winfo_ismapped() else None
        )
        for _seq in ("<KeyRelease>", "<ButtonRelease>", "<<Resize>>"):
            self.entry_ex3.bind(_seq, _grow3)
        self.entry_ex3.bind("<Return>", lambda event: (self.send_prompt(self.entry_ex3), "break")[1])
        self.entry_ex3.bind("<Shift-Return>", lambda event: None)
        self.entry_ex3.bind("<Escape>", lambda event: (self.cancel_current(), "break")[1] if self._pending else None)

        btn_expand_box = ghost_button(
            bar_container, "⤢", self.show_ex1, bg=Theme.BG_FIELD, hover_bg=Theme.BORDER, padx=10, pady=4,
        )
        btn_expand_box.pack(side="right", padx=6)
        self.cancel3 = ghost_button(
            bar_container, "■", self.cancel_current, bg=Theme.BG_FIELD, hover_bg=Theme.BORDER,
            padx=8, pady=4, font=("Segoe UI", 11),
        )  # shown only while an answer is being prepared
        if VOICE_AVAILABLE:
            mic3 = ghost_button(
                bar_container, "🎤", lambda: self.toggle_mic(self.entry_ex3), bg=Theme.BG_FIELD,
                hover_bg=Theme.BORDER, padx=8, pady=4, font=("Segoe UI", 11),
            )
            mic3.pack(side="right")
            self._mic_btns.append(mic3)

    # ---------------- attachments (any number of files) ----------------
    def attach_file(self):
        paths = filedialog.askopenfilenames(
            parent=self.root,
            filetypes=[("Supported files", DIALOG_PATTERN), ("All files", "*.*")],
        )
        added = []
        for p in paths or ():
            if p not in self.attached_files and len(self.attached_files) < MAX_FILES:
                self.attached_files.append(p)
                added.append(p)
        if added:
            shown = ", ".join(os.path.basename(p) for p in added[:3]) + (f" (+{len(added) - 3})" if len(added) > 3 else "")
            self.output_box.configure(state="normal")
            self.output_box.insert(tk.END, f"📎 {shown}\n", "user_tag")
            self.output_box.configure(state="disabled")
            self.output_box.see(tk.END)
        self._refresh_tray()

    def _refresh_tray(self):
        tray = self.tray
        for w in tray.winfo_children():
            w.destroy()
        files = self.attached_files
        if not files:
            tray.pack_forget()
            return
        names = ", ".join(os.path.basename(p) for p in files[:2]) + (f" +{len(files) - 2}" if len(files) > 2 else "")
        text = f"📎 {t('attach_n_files', n=len(files))}: {names}"
        tk.Label(
            tray, text=text if len(text) <= 58 else text[:55] + "...", font=Theme.FONT_LABEL,
            bg=Theme.BG_PANEL, fg=Theme.TEXT_SECONDARY, anchor="w",
        ).pack(side="left", padx=(10, 4), pady=5)
        ghost_button(tray, "✕", self.clear_attachments, bg=Theme.BG_PANEL, hover_bg=Theme.BORDER, padx=6, pady=2).pack(side="right", padx=(0, 4))
        ghost_button(tray, t("manage"), self.manage_attachments, bg=Theme.BG_PANEL, hover_bg=Theme.BORDER, padx=8, pady=2, font=Theme.FONT_LABEL).pack(side="right")
        if not tray.winfo_ismapped():
            tray.pack(fill="x", padx=20, before=self.input_frame1)

    def clear_attachments(self):
        self.attached_files = []
        self._refresh_tray()

    def manage_attachments(self):
        win = tk.Toplevel(self.root)
        win.title(t("attachments"))
        win.geometry("460x380")
        win.configure(bg=Theme.BG_DARK)
        apply_native_titlebar_color(win, "#ffffff", text_color="#000000")

        container = tk.Frame(win, bg=Theme.BG_PANEL, highlightthickness=1, highlightbackground=Theme.BORDER)
        container.pack(fill="both", expand=True, padx=16, pady=(16, 8))
        scroll = MinimalScrollbar(container, width=8)
        scroll.pack(side="right", fill="y", padx=(0, 4), pady=4)
        listbox = tk.Listbox(
            container, selectmode="extended", bg=Theme.BG_PANEL, fg=Theme.TEXT_PRIMARY,
            selectbackground=Theme.ACCENT_DIM, selectforeground=Theme.TEXT_PRIMARY, activestyle="none",
            relief="flat", bd=0, highlightthickness=0, font=Theme.FONT_UI, yscrollcommand=scroll.set,
        )
        listbox.pack(side="left", fill="both", expand=True, padx=6, pady=6)
        scroll.command = listbox.yview

        def fill():
            listbox.delete(0, "end")
            for p in self.attached_files:
                listbox.insert("end", "  " + os.path.basename(p))

        def remove():
            for idx in reversed(listbox.curselection()):
                del self.attached_files[idx]
            fill()
            self._refresh_tray()
            if not self.attached_files:
                win.destroy()

        def add_more():
            self.attach_file()
            fill()

        row = tk.Frame(win, bg=Theme.BG_DARK)
        row.pack(fill="x", padx=16, pady=(0, 16))
        outline_button(row, t("remove_selected"), remove).pack(side="left")
        solid_button(row, t("add_more"), add_more).pack(side="right")
        fill()

    def _send_label(self) -> str:
        return f"■ {t('cancel')}" if self._pending else t("send")

    def _send_or_cancel(self, entry_widget: tk.Text):
        if self._pending:
            self.cancel_current()
        else:
            self.send_prompt(entry_widget)

    def _update_busy_ui(self):
        """Send button <-> Cancel button (and the compact bar's indicator) follow the busy state."""
        busy = bool(self._pending)
        try:
            self.send_btn1.configure(text=self._send_label())
        except (tk.TclError, AttributeError):
            pass
        try:
            if busy and not self.cancel3.winfo_ismapped():
                self.cancel3.pack(side="right", padx=(0, 2))
            elif not busy:
                self.cancel3.pack_forget()
                self.left_icon3.configure(text=" ❯ ")
        except (tk.TclError, AttributeError):
            pass

    def cancel_current(self):
        """Stops the question being processed: the model request is abandoned, a running command is
        killed, and the chat is free again. Whatever you typed meanwhile stays in the input box."""
        if not self._pending:
            return
        try:
            self.agent.cancel()
        except Exception:
            pass
        self.worker.cancel()
        for entry in list(self._pending):
            self._stop_thinking(entry)
        self._note(t("cancelled"))

    def send_prompt(self, entry_widget: tk.Text):
        if self._pending:  # one question at a time; the button is "Cancel" meanwhile
            return
        query = entry_widget.get("1.0", "end-1c").strip()
        files = list(self.attached_files)
        if not query and not files:
            return
        if not query:
            query = t("analyze_attachments")

        self._show_result(query, entry_widget, role="user")
        entry_widget.delete("1.0", "end")
        entry_widget.event_generate("<<Resize>>")
        self._start_thinking(entry_widget)
        self.attached_files = []
        self._refresh_tray()
        self.worker.submit(query, entry_widget, files or None)

    # ---------------- notes ----------------
    def _note(self, text: str):
        self.output_box.configure(state="normal")
        self.output_box.insert(tk.END, f"{text}\n", "user_tag")
        self.output_box.configure(state="disabled")
        self.output_box.see(tk.END)

    # ---------------- speech to text (microphone) ----------------
    def toggle_mic(self, entry_widget: tk.Text):
        if not VOICE_AVAILABLE or self._stt_busy:
            return
        if self.recorder.recording:
            self._finish_recording()
            return
        ok, err = self.recorder.start()
        if not ok:
            self._note(t("mic_error", error=err))
            return
        self._mic_target = entry_widget
        self._set_mic_ui("rec", "0:00")
        self._tick_mic()

    def _tick_mic(self):
        if not self.recorder.recording:
            return
        secs = self.recorder.elapsed
        if secs >= MAX_RECORD_SECONDS:
            self._finish_recording()
            return
        self._set_mic_ui("rec", f"{secs // 60}:{secs % 60:02d}")
        self.root.after(500, self._tick_mic)

    def _set_mic_ui(self, state: str, extra: str = ""):
        for btn in list(self._mic_btns):
            try:
                if state == "rec":
                    btn.configure(text=f"⏹ {extra}", fg="#ff6b6b")
                elif state == "busy":
                    btn.configure(text="…", fg=Theme.TEXT_MUTED)
                else:
                    btn.configure(text="🎤", fg=Theme.TEXT_SECONDARY)
            except tk.TclError:
                pass

    def _finish_recording(self):
        path, err = self.recorder.stop()
        target = self._mic_target
        if not path:
            self._set_mic_ui("idle")
            self._note(t("mic_too_short") if err == "too_short" else t("mic_error", error=err))
            return
        self._stt_busy = True
        self._set_mic_ui("busy")
        mml_obj = self.agent.mml

        def work():
            text, error = "", None
            try:
                text = transcribe_wav(mml_obj, path)
            except Exception as exc:
                error = str(exc)
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            self.worker.results.put(("stt", (text, error), target))

        threading.Thread(target=work, daemon=True).start()

    def _on_transcript(self, payload, entry_widget: tk.Text):
        text, error = payload
        self._stt_busy = False
        self._set_mic_ui("idle")
        if error:
            self._note(t("mic_error", error=error[:160]))
            return
        if not text:
            self._note(t("mic_no_speech"))
            return
        try:
            entry_widget.configure(state="normal")
            if entry_widget.get("1.0", "end-1c").strip():
                entry_widget.insert("end", " ")
            entry_widget.insert("end", text)
            entry_widget.event_generate("<KeyRelease>")
            entry_widget.see("end")
            entry_widget.focus_set()
        except tk.TclError:
            pass

    # ---------------- text to speech (read the answer aloud) ----------------
    def toggle_speak(self, text: str, btn: tk.Button):
        sp = self.speaker
        if sp.active and sp.token is btn:
            sp.stop()  # on_done resets the button
            return
        old = sp.token
        if old is not None and old is not btn:
            try:
                old.configure(text="🔊")
            except tk.TclError:
                pass
        btn.configure(text="⏹")
        sp.speak(
            text, self.agent.mml, token=btn,
            on_done=lambda tok, err: self.worker.results.put(("tts", err, tok)),
        )

    def preview_voice(self):
        """Plays a short sample with the currently selected voice (used by the Settings window)."""
        if not self.speaker:
            return
        self.speaker.speak(
            t("voice_preview_text"), self.agent.mml, token=None,
            on_done=lambda tok, err: self.worker.results.put(("tts", err, tok)),
        )

    def _on_speak_done(self, error, btn: tk.Button):
        if error:
            self._note(t("tts_error", error=str(error)[:160]))
        if btn is None:  # voice preview in Settings has no button to reset
            return
        if self.speaker.active and self.speaker.token is btn:
            return  # a newer playback on this button already started
        try:
            btn.configure(text="🔊")
        except tk.TclError:
            pass

    def _poll_results(self):
        try:
            while True:
                kind, payload, entry_widget = self.worker.results.get_nowait()
                if kind == "status":
                    self._on_status(payload)
                elif kind == "stt":
                    self._on_transcript(payload, entry_widget)
                elif kind == "tts":
                    self._on_speak_done(payload, entry_widget)
                else:
                    self._show_result(payload, entry_widget, role="bot")
        except queue.Empty:
            pass
        self.root.after(100, self._poll_results)

    THINK_FRAMES = ("●○○", "○●○", "○○●", "○●○")

    def _start_thinking(self, entry_widget: tk.Text):
        self._pending.add(entry_widget)
        self._update_busy_ui()
        self._steps = []
        self._current_status = None
        if self._think_job is None:
            self._think_tick = 0
            self._animate_thinking()

    def _on_status(self, text: str):
        if text.startswith("@"):
            text = t("status_" + text[1:])
        if text == self._current_status:
            return
        if self._current_status and self._current_status not in self._steps:
            self._steps.append(self._current_status)
        self._current_status = text
        if self._pending:
            self._render_thinking()

    def _stop_thinking(self, entry_widget: tk.Text):
        self._pending.discard(entry_widget)
        self._steps = []
        self._current_status = None
        try:
            ranges = self.output_box.tag_ranges("thinking")
            if ranges:
                self.output_box.configure(state="normal")
                self.output_box.delete(ranges[0], ranges[1])
                self.output_box.configure(state="disabled")
        except Exception:
            pass
        self._update_busy_ui()

    def _render_thinking(self):
        frame = self.THINK_FRAMES[self._think_tick % len(self.THINK_FRAMES)]
        current = self._current_status or t("thinking")

        try:  # compact bar: the left icon animates; the input box itself stays free for typing
            self.left_icon3.configure(text=f" {frame} ")
        except (tk.TclError, AttributeError):
            pass

        rows = [f"✓ {step}" for step in self._steps[-6:]] + [f"{current} {frame}"]
        block = "SaiA ◆ " + ("\n" + " " * 8).join(rows) + "\n"
        box = self.output_box
        ranges = box.tag_ranges("thinking")
        box.configure(state="normal")
        if ranges:
            start = str(ranges[0])
            box.delete(ranges[0], ranges[1])
            box.insert(start, block, "thinking")
        else:
            box.insert(tk.END, block, "thinking")
        box.configure(state="disabled")
        if not ranges or len(rows) != self._rows_shown:
            box.see(tk.END)
        self._rows_shown = len(rows)

    def _animate_thinking(self):
        if not self._pending:
            self._think_job = None
            return
        self._render_thinking()
        self._think_tick += 1
        self._think_job = self.root.after(350, self._animate_thinking)

    def _show_result(self, result: Any, entry_widget: tk.Text, role: str = "bot"):
        """Shows a message. Bot answers may contain interactive cards (function analysis, files)."""
        if result is None:
            result = t("done")
        if role == "user":
            self._show_bubble(result, entry_widget, role)
            return

        self._stop_thinking(entry_widget)
        text_value = str(result).strip()
        try:
            segments = split_answer(text_value)
        except Exception:
            traceback.print_exc()  # do not hide why the cards disappeared
            segments = [("text", text_value)]

        if all(kind == "text" for kind, _ in segments):
            self._show_bubble(text_value, entry_widget, role)
        else:
            speech = "\n".join(v for k, v in segments if k == "text")
            header_done = False
            for kind, value in segments:
                if kind == "text":
                    self._show_bubble(value, entry_widget, role, header=not header_done, speech_text=speech)
                else:
                    if not header_done:
                        self.output_box.configure(state="normal")
                        self._insert_header(False, "left_align", speech)
                        self.output_box.configure(state="disabled")
                    self._insert_card(kind, value)
                header_done = True
        entry_widget.focus_set()

    def _insert_card(self, kind: str, value: Any):
        box = self.output_box
        avail = box.winfo_width() - 48
        # function window uses the full chat width (bigger graph); file cards stay compact
        width = max(300, min(900, avail)) if kind == "func" else max(280, min(560, avail - 8))

        def wheel(e):
            box.yview_scroll(int(-e.delta / 120), "units")
            return "break"

        try:
            if kind == "func":
                card = FunctionCard(box, Theme, value, t, width=width, on_wheel=wheel)
            else:
                card = FileCard(box, Theme, value, t, width=width, on_wheel=wheel, notify=self._note)
        except Exception:
            traceback.print_exc()
            if kind == "file":  # never lose the path if the card cannot be built
                box.configure(state="normal")
                box.insert(tk.END, f"{value}\n", "left_align")
                box.configure(state="disabled")
            return
        self._cards.append(card)
        box.configure(state="normal")
        start = box.index("end-1c")
        box.window_create(tk.END, window=card)
        box.insert(tk.END, "\n")
        box.tag_add("left_align", start, "end-1c")
        box.tag_add("gap", start, "end-1c")
        box.configure(state="disabled")
        box.see(tk.END)
        self.root.after(80, lambda: box.see(tk.END))
        self.root.after(300, lambda: box.see(tk.END))

    def _insert_header(self, is_user: bool, align: str, speech_text: str):
        """Writes the 'SaiA ◆ 🔊' / user-name line. The output box must be in normal state."""
        box = self.output_box
        who = (self.settings.get("user_nickname") or t("you")) if is_user else "SaiA ◆"
        name_tags = (align, "name_user" if is_user else "name_bot")
        if is_user or not VOICE_AVAILABLE:
            box.insert(tk.END, f"{who}\n", name_tags)
        else:
            box.insert(tk.END, f"{who}  ", name_tags)
            speak_btn = tk.Button(
                box, text="🔊", font=("Segoe UI", 9), bg=Theme.BG_PANEL, fg=Theme.TEXT_MUTED,
                activebackground=Theme.BG_PANEL, activeforeground=Theme.TEXT_PRIMARY,
                relief="flat", bd=0, cursor="hand2", padx=4, pady=0,
            )
            speak_btn.configure(command=lambda b=speak_btn, s=speech_text: self.toggle_speak(s, b))
            box.window_create(tk.END, window=speak_btn)
            box.insert(tk.END, "\n", name_tags)

    def _show_bubble(self, result: Any, entry_widget: tk.Text, role: str = "bot",
                     header: bool = True, speech_text: Optional[str] = None):
        is_user = (role == "user")
        box = self.output_box
        text_value = str(result).strip()
        ui_font = tkfont.Font(font=Theme.FONT_UI)
        line_h = ui_font.metrics("linespace")
        pad = 12
        fill_token = Theme.ACCENT_DIM if is_user else Theme.BG_FIELD
        line_token = Theme.ACCENT if is_user else Theme.BORDER

        use_rich = RICH_AVAILABLE and not is_user
        natural = max(ui_font.measure(line) for line in text_value.split("\n")) + 2 * pad + 6
        if use_rich and has_wide_blocks(text_value):
            natural = 10 ** 4  # tables / code blocks use the full available width
        bubble_w = max(140, min(max(box.winfo_width(), 300) - 44, natural))

        cv = tk.Canvas(box, bg=Theme.BG_PANEL, highlightthickness=0, bd=0, width=bubble_w, height=line_h + 2 * pad)
        txt = tk.Text(
            cv, width=1, height=1, wrap="word", bg=fill_token, fg=Theme.TEXT_PRIMARY,
            font=Theme.FONT_UI, relief="flat", bd=0, padx=0, pady=0, highlightthickness=0,
            insertwidth=0, cursor="arrow",
        )
        def wheel(e):
            box.yview_scroll(int(-e.delta / 120), "units")
            return "break"

        rich = None
        if use_rich:
            try:
                rich = render_rich(
                    txt, text_value, Theme,
                    labels={"copy": t("copy_code"), "copied": t("copied_code")},
                    on_layout=lambda: relayout(), on_wheel=wheel,
                )
            except Exception:
                rich = None  # any parsing/layout problem -> fall back to plain text
        if rich is None:
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            txt.insert("1.0", text_value)
            txt.configure(state="disabled")
        win = cv.create_window(pad, pad, anchor="nw", window=txt, width=bubble_w - 2 * pad, height=line_h)

        def relayout():
            def apply():
                try:
                    px = int((txt.count("1.0", "end", "ypixels") or (line_h,))[0])
                    px = max(line_h, px)
                    if abs(px - float(cv.itemcget(win, "height"))) > 0.5:
                        cv.itemconfigure(win, height=px)
                        cv.configure(height=px + 2 * pad)
                except tk.TclError:
                    pass
            txt.after_idle(apply)

        cv.bind(
            "<Configure>",
            lambda e: (
                cv.delete("bubble"),
                cv.create_polygon(
                    [
                        px for point in (
                            (16, 0), (16, 0), (e.width - 17, 0), (e.width - 17, 0), (e.width - 1, 0),
                            (e.width - 1, 16), (e.width - 1, 16), (e.width - 1, e.height - 17), (e.width - 1, e.height - 17),
                            (e.width - 1, e.height - 1), (e.width - 17, e.height - 1), (e.width - 17, e.height - 1),
                            (16, e.height - 1), (16, e.height - 1), (0, e.height - 1), (0, e.height - 17),
                            (0, e.height - 17), (0, 16), (0, 16), (0, 0),
                        )
                        for px in point
                    ],
                    smooth=True, fill=Theme.live(fill_token), outline=Theme.live(line_token), tags="bubble",
                ),
                cv.tag_lower("bubble"),
            ),
        )

        txt.bind(
            "<Configure>",
            lambda e: (
                lambda n: (
                    cv.itemconfigure(win, height=n * line_h),
                    cv.configure(height=n * line_h + 2 * pad),
                )
                if abs(n * line_h - float(cv.itemcget(win, "height"))) > 0.5 else None
            )((txt.count("1.0", "end", "displaylines") or (1,))[0]),
        )

        if rich is not None:
            rich.attach()  # pixel-based sizing (tables / code blocks) replaces the line-based handler
            self._rich_texts.append(txt)
            txt.after(40, relayout)

        for part in (cv, txt):
            part.bind("<MouseWheel>", lambda e: (box.yview_scroll(int(-e.delta / 120), "units"), "break")[1])
        txt.bind("<Button-1>", lambda e: txt.focus_set())

        align = "right_align" if is_user else "left_align"
        box.configure(state="normal")
        if header:
            self._insert_header(is_user, align, speech_text or text_value)
        start = box.index("end-1c")
        box.window_create(tk.END, window=cv)
        box.insert(tk.END, "\n")
        box.tag_add(align, start, "end-1c")
        box.tag_add("gap", start, "end-1c")
        box.configure(state="disabled")
        self._bubbles.append((cv, win, natural, pad))

        box.see(tk.END)
        self.root.after(80, lambda: box.see(tk.END))
        self.root.after(300, lambda: box.see(tk.END))

    def switch_session(self, session_id: str):
        """Switches the active conversation session and redraws historic bubbles from disk."""
        self.current_session_id = session_id
        self.agent.set_session(session_id, self.active_role)
        self._bubbles.clear()
        self._rich_texts.clear()
        self._cards.clear()
        if VOICE_AVAILABLE:
            self.speaker.stop()

        self.output_box.configure(state="normal")
        self.output_box.delete("1.0", tk.END)
        self.output_box.configure(state="disabled")

        history = self.settings.db.get_session_history(session_id, limit=30)
        for msg in history:
            self._show_result(msg["content"], self.entry_ex1, role=msg["role"])

    def open_history(self):
        ChatHistoryWindow(self.root, self)

    def open_flows(self):
        AutomationFlowWindow(self.root, self)

    def _place_root(self, width: int, height: int):
        geo = f"{width}x{height}"
        if self.orb.visible:
            x, y = self.orb.position()
            x = max(0, min(x, self.root.winfo_screenwidth() - width))
            y = max(0, min(y, self.root.winfo_screenheight() - height - 40))
            geo += f"+{x}+{y}"
            self.orb.hide()
        self.root.geometry(geo)

    def _reveal_root(self):
        self.root.deiconify()
        self.root.lift()
        apply_native_titlebar_color(self.root, "#ffffff", text_color="#000000")

    def show_ex1(self):
        self.frame_ex3.pack_forget()
        self._place_root(480, 600)
        self.frame_ex1.pack(fill="both", expand=True)
        self._reveal_root()

    def show_ex2(self):
        x, y = self.root.winfo_x(), self.root.winfo_y()
        self.root.withdraw()
        self.orb.show(x, y)

    def show_ex3(self):
        self.frame_ex1.pack_forget()
        self._place_root(420, 60)
        self.frame_ex3.pack(fill="both", expand=True)
        self._reveal_root()

    def _settings_alive(self) -> bool:
        win = self._settings_win
        try:
            return win is not None and bool(win.window.winfo_exists())
        except tk.TclError:
            return False

    def open_settings(self):
        if self._settings_alive():
            self._settings_win.window.lift()
            self._settings_win.window.focus_force()
            return
        self._settings_win = SettingsWindow(self.root, self)

    def _apply_tag_colors(self):
        self.output_box.tag_configure("user_tag", foreground=Theme.TEXT_SECONDARY)
        self.output_box.tag_configure("bot_tag", foreground=Theme.ACCENT_HOVER)
        self.output_box.tag_configure("thinking", foreground=Theme.TEXT_MUTED)
        self.output_box.tag_configure("name_user", foreground=Theme.TEXT_SECONDARY)
        self.output_box.tag_configure("name_bot", foreground=Theme.ACCENT_HOVER)

    def toggle_theme(self):
        old = Theme.mode
        new = "light" if old == "dark" else "dark"
        mapping = Theme.mapping(old, new)
        Theme.apply(new)
        recolor_tree(self.root, mapping)
        if RICH_AVAILABLE:
            for rt in self._rich_texts:
                try:
                    recolor_tags(rt, mapping)
                except tk.TclError:
                    pass
        self._apply_tag_colors()
        for card in self._cards:
            try:
                card.restyle()
            except Exception:
                pass
        self.live.refresh()
        self.settings.set(theme=new)

    def change_language(self, code: str):
        set_language(code)
        self.settings.set(language=get_language())
        self.root.title(t("app_title"))
        self.live.refresh()
        self.refresh_model_pickers()
        self.refresh_role_pickers()
        if self._settings_alive():
            self._settings_win.window.title(t("settings_title"))

    def change_voice(self, name: str):
        self.settings.set(tts_voice=set_media_voice(name))

    def set_nickname(self, name: str):
        self.settings.set(user_nickname=name.strip())

    def models(self) -> List[Dict[str, Any]]:
        try:
            return self.settings.db.list_models()
        except Exception:
            return []

    def model_by_id(self, model_id: str) -> Optional[Dict[str, Any]]:
        for model in self.models():
            if model["id"] == model_id:
                return model
        return None

    def refresh_model_pickers(self):
        alive = []
        for picker in self.model_pickers:
            try:
                picker.refresh_label()
            except tk.TclError:
                continue
            alive.append(picker)
        self.model_pickers = alive

    def change_model(self, model_id: str):
        self.settings.set(selected_model=model_id)
        self.agent.mml.model = model_id
        self.refresh_model_pickers()

    def roles(self) -> List[Dict[str, Any]]:
        try:
            return self.settings.db.list_roles()
        except Exception:
            return []

    def role_by_id(self, role_id: str) -> Optional[Dict[str, Any]]:
        for role in self.roles():
            if role["id"] == role_id:
                return role
        return None

    def refresh_role_pickers(self):
        alive = []
        for picker in self.role_pickers:
            try:
                picker.refresh_label()
            except tk.TclError:
                continue
            alive.append(picker)
        self.role_pickers = alive

    def change_role(self, role_id: str):
        self.active_role = role_id
        self.settings.set(active_role=role_id)
        self.agent.role_id = role_id
        self.refresh_role_pickers()


def start_app():
    app = SaiAApp()
    app.root.mainloop()


if __name__ == "__main__":
    start_app()
