"""
ui/rich_text.py - Rich answer renderer for the SaiA answer bubble.

Turns Markdown-like model output into formatted content inside the existing
bubble Text widget: headings, bold, inline code, bullet / numbered lists,
tables, code blocks (language header + copy button) and dividers.

Uses only the existing Theme palette (flat, no glow / gloss / animation).
Nothing here touches the original code: app_gui.py only calls render_rich().
"""

from __future__ import annotations

import math
import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Any, Callable, Dict, List, Optional

try:  # LaTeX -> human-looking math (d/dx(x²) instead of $\\frac{d}{dx}(x^2)$)
    from ui.math_text import convert_math
except Exception:  # standalone use / import problems: render text as-is
    try:
        from math_text import convert_math
    except Exception:
        def convert_math(text: str) -> str:
            return text

try:  # code-block syntax colouring (VS Code-like)
    from ui.syntax_highlight import TAG_PREFIX, build_palette, highlight, tk_ranges
except Exception:
    try:
        from syntax_highlight import TAG_PREFIX, build_palette, highlight, tk_ranges
    except Exception:
        highlight = None

# Arabic lines: put the bullet / number after the text so it appears on the right.
RTL_FLIP_MARKERS = True

_AR = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_FENCE = re.compile(r"^\s*```\s*([\w+#.-]*)\s*$")
_HEAD = re.compile(r"^\s{0,3}(#{1,3})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_BUL = re.compile(r"^(\s*)[-*+\u2022]\s+(.*)$")
_NUM = re.compile(r"^(\s*)(\d+)[.)]\s+(.*)$")
_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_INLINE = re.compile(r"(\*\*[^*\n]+?\*\*|`[^`\n]+`)")

_LANG_NAMES = {
    "py": "Python", "python": "Python", "js": "JavaScript", "javascript": "JavaScript",
    "ts": "TypeScript", "typescript": "TypeScript", "html": "HTML", "css": "CSS",
    "json": "JSON", "sql": "SQL", "bash": "Bash", "sh": "Shell", "ps1": "PowerShell",
    "powershell": "PowerShell", "cpp": "C++", "c++": "C++", "c": "C", "cs": "C#",
    "csharp": "C#", "java": "Java", "xml": "XML", "yaml": "YAML", "md": "Markdown",
}


# ---------------------------------------------------------------- parsing
def is_rtl(s: str) -> bool:
    letters = sum(1 for ch in s if ch.isalpha())
    return letters > 0 and len(_AR.findall(s)) >= 0.4 * letters


def _plain(s: str) -> str:
    return re.sub(r"\*\*|`", "", s)


def _split_row(line: str) -> List[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse(text: str) -> List[tuple]:
    lines = text.replace("\r\n", "\n").split("\n")
    blocks: List[tuple] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _FENCE.match(line)
        if m:
            lang, buf = m.group(1), []
            i += 1
            while i < len(lines) and not _FENCE.match(lines[i]):
                buf.append(lines[i])
                i += 1
            i += 1
            blocks.append(("code", lang, "\n".join(buf)))
            continue
        if "|" in line and i + 1 < len(lines) and _SEP.match(lines[i + 1]):
            rows = [_split_row(line)]
            i += 2
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_split_row(lines[i]))
                i += 1
            n = max(len(r) for r in rows)
            blocks.append(("table", [r + [""] * (n - len(r)) for r in rows]))
            continue
        if not line.strip():
            i += 1
            continue
        h = _HEAD.match(line)
        if h:
            blocks.append(("heading", len(h.group(1)), h.group(2)))
        elif _HR.match(line):
            blocks.append(("hr",))
        elif _BUL.match(line):
            b = _BUL.match(line)
            blocks.append(("bullet", len(b.group(1).replace("\t", "    ")) // 2, b.group(2)))
        elif _NUM.match(line):
            n = _NUM.match(line)
            blocks.append(("number", len(n.group(1).replace("\t", "    ")) // 2, n.group(2), n.group(3)))
        else:
            blocks.append(("para", line.strip()))
        i += 1
    return blocks


def has_wide_blocks(text: str) -> bool:
    """True when the answer has a table or code block (bubble should use full width)."""
    return any(b[0] in ("code", "table") for b in parse(convert_math(text)))


# ---------------------------------------------------------------- blocks
def _bind_wheel(widget: tk.Widget, fn: Optional[Callable]) -> None:
    if not fn:
        return
    widget.bind("<MouseWheel>", fn)
    for child in widget.winfo_children():
        _bind_wheel(child, fn)


class CodeBlock(tk.Frame):
    def __init__(self, parent: tk.Widget, theme: Any, lang: str, code: str, labels: Dict[str, str]):
        super().__init__(parent, bg=theme.BG_DARK, bd=0, highlightthickness=1, highlightbackground=theme.BORDER)
        self.theme = theme
        self.labels = labels
        self.code = code.rstrip("\n").expandtabs(4)
        self.pack_propagate(False)
        mono = theme.FONT_MONO
        self._font = tkfont.Font(font=mono)

        self.head = tk.Frame(self, bg=theme.BG_PANEL)
        self.head.pack(fill="x")
        name = _LANG_NAMES.get(lang.lower(), lang[:1].upper() + lang[1:]) if lang else "Code"
        tk.Label(self.head, text=name, font=("Segoe UI", 9), bg=theme.BG_PANEL, fg=theme.TEXT_MUTED).pack(
            side="left", padx=10, pady=4
        )
        self.btn = tk.Button(
            self.head, text=labels["copy"], font=("Segoe UI", 9), bg=theme.BG_PANEL, fg=theme.TEXT_SECONDARY,
            activebackground=theme.BORDER, activeforeground=theme.TEXT_PRIMARY, relief="flat", bd=0,
            cursor="hand2", padx=8, pady=2, command=self._copy,
        )
        self.btn.pack(side="right", padx=6, pady=2)
        self.btn.bind("<Enter>", lambda e: self.btn.configure(bg=self.theme.BORDER))
        self.btn.bind("<Leave>", lambda e: self.btn.configure(bg=self.theme.BG_PANEL))

        self.body = tk.Text(
            self, bg=theme.BG_DARK, fg=theme.TEXT_PRIMARY, font=mono, wrap="char", relief="flat", bd=0,
            padx=10, pady=8, highlightthickness=0, height=1, width=1, insertwidth=0, cursor="arrow",
        )
        self.body.insert("1.0", self.code)
        self._apply_syntax(lang)
        self.body.configure(state="disabled")
        self.body.bind("<Button-1>", lambda e: self.body.focus_set())
        self.body.pack(fill="both", expand=True)

    def _apply_syntax(self, lang: str) -> None:
        """Colours the code like a real editor (keywords, strings, comments, numbers...)."""
        if highlight is None:
            return
        try:
            r, g, b = self.winfo_rgb(self.theme.BG_DARK)
            dark = (0.299 * r + 0.587 * g + 0.114 * b) / 65535 < 0.5
            for name, color in build_palette(dark).items():
                self.body.tag_configure(TAG_PREFIX + name, foreground=color)
            for tag, a, b2 in tk_ranges(self.code, highlight(self.code, lang)):
                self.body.tag_add(TAG_PREFIX + tag, a, b2)
            self.body.tag_raise("sel")
        except Exception:
            pass  # colouring is cosmetic: never break rendering

    def _copy(self) -> None:
        try:
            self.clipboard_clear()
            self.clipboard_append(self.code)
            self.btn.configure(text=self.labels["copied"])
            self.after(1500, self._restore)
        except tk.TclError:
            pass

    def _restore(self) -> None:
        try:
            self.btn.configure(text=self.labels["copy"])
        except tk.TclError:
            pass

    def resize(self, w: int) -> None:
        cw = max(1, self._font.measure("0"))
        lh = self._font.metrics("linespace")
        cpl = max(1, (w - 2 - 20) // cw)
        n = sum(max(1, math.ceil(len(line) / cpl)) for line in self.code.split("\n"))
        self.configure(width=w, height=self.head.winfo_reqheight() + n * lh + 16 + 2)


class TableBlock(tk.Frame):
    def __init__(self, parent: tk.Widget, theme: Any, rows: List[List[str]], rtl: bool):
        super().__init__(parent, bg=theme.BORDER, bd=0)
        ui = theme.FONT_UI
        bold = (ui[0], ui[1], "bold")
        self.ncols = len(rows[0])
        self.cells: List[tk.Label] = []
        last_r, last_c = len(rows) - 1, self.ncols - 1
        for r, row in enumerate(rows):
            for c, cell in enumerate(row):
                lb = tk.Label(
                    self, text=_plain(cell), font=bold if r == 0 else ui,
                    bg=theme.BG_PANEL if r == 0 else theme.BG_FIELD, fg=theme.TEXT_PRIMARY,
                    anchor="e" if rtl else "w", justify="right" if rtl else "left", padx=8, pady=5,
                )
                lb.grid(row=r, column=c, sticky="nsew", padx=(1, 1 if c == last_c else 0),
                        pady=(1, 1 if r == last_r else 0))
                self.cells.append(lb)
        for c in range(self.ncols):
            self.columnconfigure(c, weight=1, uniform="col")

    def resize(self, w: int) -> None:
        wrap = max(40, (w - 1) // self.ncols - 17)
        for lb in self.cells:
            lb.configure(wraplength=wrap)


class RuleBlock(tk.Frame):
    def __init__(self, parent: tk.Widget, theme: Any):
        super().__init__(parent, bg=theme.BORDER, height=1, bd=0)

    def resize(self, w: int) -> None:
        self.configure(width=w, height=1)


# ---------------------------------------------------------------- renderer
class _Renderer:
    def __init__(self, txt: tk.Text, theme: Any, labels: Optional[Dict[str, str]],
                 on_layout: Optional[Callable[[], None]], on_wheel: Optional[Callable]):
        self.txt, self.theme = txt, theme
        self.labels = {"copy": "Copy", "copied": "Copied \u2713"}
        self.labels.update(labels or {})
        self.on_layout, self.on_wheel = on_layout, on_wheel
        self.blocks: List[Any] = []
        self._w = 280
        self._setup_tags()

    def _setup_tags(self) -> None:
        t, th = self.txt, self.theme
        fam, size = th.FONT_UI[0], th.FONT_UI[1]
        t.tag_configure("b", font=(fam, size, "bold"))
        t.tag_configure("ic", font=(th.FONT_MONO[0], max(8, size - 1)), background=th.BG_DARK,
                        foreground=th.TEXT_PRIMARY)
        t.tag_configure("h1", font=(fam, size + 4, "bold"), spacing1=6, spacing3=4)
        t.tag_configure("h2", font=(fam, size + 2, "bold"), spacing1=6, spacing3=3)
        t.tag_configure("h3", font=(fam, size + 1, "bold"), spacing1=4, spacing3=2)
        t.tag_configure("para", spacing1=2)
        t.tag_configure("rtl", justify="right")
        for lvl in range(5):
            base = 8 + lvl * 16
            t.tag_configure(f"li{lvl}", lmargin1=base, lmargin2=base + 14, spacing1=2)
            t.tag_configure(f"lir{lvl}", rmargin=base, justify="right", spacing1=2)

    def _inline(self, s: str, base: tuple = ()) -> None:
        t, pos = self.txt, 0
        for m in _INLINE.finditer(s):
            if m.start() > pos:
                t.insert("end", s[pos:m.start()], base)
            tok = m.group(0)
            if tok.startswith("**"):
                t.insert("end", tok[2:-2], base + ("b",))
            else:
                t.insert("end", " " + tok[1:-1] + " ", base + ("ic",))
            pos = m.end()
        if pos < len(s):
            t.insert("end", s[pos:], base)

    def _embed(self, widget: tk.Widget) -> None:
        self.txt.window_create("end", window=widget, pady=4)
        self.blocks.append(widget)
        _bind_wheel(widget, self.on_wheel)

    def _list_item(self, lvl: int, marker: str, text: str) -> None:
        lvl = min(lvl, 4)
        if is_rtl(text):
            tags = (f"lir{lvl}",)
            if RTL_FLIP_MARKERS:
                self._inline(text, tags)
                self.txt.insert("end", "  " + marker, tags)
            else:
                self.txt.insert("end", marker + "  ", tags)
                self._inline(text, tags)
        else:
            self.txt.insert("end", marker + "  ", (f"li{lvl}",))
            self._inline(text, (f"li{lvl}",))

    def render(self, text: str) -> None:
        t = self.txt
        t.configure(state="normal")
        try:
            t.delete("1.0", "end")
            first = True
            for b in parse(text):
                if not first:
                    t.insert("end", "\n")
                first = False
                kind = b[0]
                if kind == "code":
                    self._embed(CodeBlock(t, self.theme, b[1], b[2], self.labels))
                elif kind == "table":
                    self._embed(TableBlock(t, self.theme, b[1], is_rtl(" ".join(b[1][0]))))
                elif kind == "hr":
                    self._embed(RuleBlock(t, self.theme))
                elif kind == "heading":
                    tags = ("h%d" % b[1],) + (("rtl",) if is_rtl(b[2]) else ())
                    t.insert("end", _plain(b[2]), tags)
                elif kind == "bullet":
                    self._list_item(b[1], "\u2022", b[2])
                elif kind == "number":
                    self._list_item(b[1], b[2] + ".", b[3])
                else:
                    self._inline(b[1], ("para",) + (("rtl",) if is_rtl(b[1]) else ()))
        finally:
            t.configure(state="disabled")
        for blk in self.blocks:
            blk.resize(self._w)

    def attach(self) -> None:
        """Binds the resize handler (replaces any older <Configure> binding on the widget)."""
        self.txt.bind("<Configure>", self._on_cfg)

    def _on_cfg(self, e: tk.Event) -> None:
        w = max(200, e.width - 2)
        if w != self._w:
            self._w = w
            for blk in self.blocks:
                blk.resize(w)
        if self.on_layout:
            self.on_layout()

    @property
    def has_blocks(self) -> bool:
        return bool(self.blocks)


def render_rich(txt: tk.Text, text: str, theme: Any, labels: Optional[Dict[str, str]] = None,
                on_layout: Optional[Callable[[], None]] = None,
                on_wheel: Optional[Callable] = None) -> _Renderer:
    """Renders `text` into the (disabled) bubble Text widget `txt`."""
    r = _Renderer(txt, theme, labels, on_layout, on_wheel)
    r.render(convert_math(text))
    return r


def recolor_tags(txt: tk.Text, mapping: Dict[str, str]) -> None:
    """Theme switch support: remaps tag colors (inline code etc.)."""
    for tag in txt.tag_names():
        for opt in ("foreground", "background"):
            try:
                new = mapping.get(str(txt.tag_cget(tag, opt)).lower())
            except tk.TclError:
                continue
            if new:
                txt.tag_configure(tag, **{opt: new})