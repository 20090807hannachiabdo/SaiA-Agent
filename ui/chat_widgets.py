"""
ui/chat_widgets.py - interactive cards embedded inside the agent's answers.

  FunctionCard   live function-analysis window: slider for x, moving point on the graph,
                 value + slope readout, zeros / y-intercept / extrema (drawn with SaiA's own colors)
  FileCard       a file produced by the agent: open it, save a copy, or show it in its folder
  split_answer   splits an answer into text / function / file segments

The agent asks for a function card by writing a fenced block inside its answer:

    ```saia-func
    f(x) = 2x + 3
    range: -10, 10
    x: 3.5
    ```

Files are detected from absolute paths (or a ```saia-file block) that exist on disk.
Expressions are parsed with a strict AST whitelist - nothing is ever passed to eval().
"""

from __future__ import annotations

import ast
import math
import os
import re
import shutil
import subprocess
import sys
import tkinter as tk
from dataclasses import dataclass, field
from tkinter import filedialog
from typing import Any, Callable, List, Optional, Tuple

# ------------------------------------------------------------------ safe math
_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "sqrt": math.sqrt, "exp": math.exp, "ln": math.log, "log": math.log10,
    "abs": abs, "floor": math.floor, "ceil": math.ceil,
}
_CONSTS = {"pi": math.pi, "e": math.e}
_MAX_EXPR = 200


def _normalize(expr: str) -> str:
    s = expr.strip()
    s = re.sub(r"^\s*(?:[A-Za-z]\w*\s*\(\s*x\s*\)|y)\s*=\s*", "", s)
    for a, b in (
        ("×", "*"), ("·", "*"), ("÷", "/"), ("−", "-"), ("–", "-"), ("π", "pi"), ("√", "sqrt"),
        ("²", "**2"), ("³", "**3"), ("^", "**"), ("\u200f", ""), ("\u200e", ""), ("\u00a0", " "),
    ):
        s = s.replace(a, b)
    s = re.sub(r"\)\s*(?=[A-Za-z0-9(])", ")*", s)                       # (x+1)(x-1), (x)2
    s = re.sub(r"(?<![A-Za-z_\d.])(\d+(?:\.\d+)?)\s*(?=[A-Za-z(])", r"\1*", s)  # 2x, 2(x+1), 2sin(x)
    s = re.sub(r"\b(x|pi|e)\b\s*(?=[A-Za-z(])", r"\1*", s)             # x(x+1), x sin(x)
    return s


class _Fn:
    """A function of x parsed from text and evaluated through a strict AST whitelist."""

    def __init__(self, source: str):
        if not source or len(source) > _MAX_EXPR:
            raise ValueError("bad expression")
        self.source = source.strip()
        self.pretty = re.sub(
            r"^\s*(?:[A-Za-z]\w*\s*\(\s*x\s*\)|y)\s*=\s*", "", self.source
        ).replace("**", "^")
        tree = ast.parse(_normalize(source), mode="eval")
        self._check(tree.body)
        self._tree = tree.body

    def _check(self, node: ast.AST) -> None:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise ValueError("bad constant")
        elif isinstance(node, ast.Name):
            if node.id != "x" and node.id not in _CONSTS:
                raise ValueError(f"unknown name {node.id}")
        elif isinstance(node, ast.BinOp):
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv)):
                raise ValueError("bad operator")
            self._check(node.left)
            self._check(node.right)
        elif isinstance(node, ast.UnaryOp):
            if not isinstance(node.op, (ast.USub, ast.UAdd)):
                raise ValueError("bad operator")
            self._check(node.operand)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _FUNCS or node.keywords:
                raise ValueError("bad call")
            if len(node.args) != 1:
                raise ValueError("bad arity")
            self._check(node.args[0])
        else:
            raise ValueError("unsupported syntax")

    def _ev(self, node: ast.AST, x: float) -> float:
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            return x if node.id == "x" else _CONSTS[node.id]
        if isinstance(node, ast.UnaryOp):
            v = self._ev(node.operand, x)
            return -v if isinstance(node.op, ast.USub) else v
        if isinstance(node, ast.Call):
            return float(_FUNCS[node.func.id](self._ev(node.args[0], x)))
        a, b, op = self._ev(node.left, x), self._ev(node.right, x), node.op
        if isinstance(op, ast.Add):
            return a + b
        if isinstance(op, ast.Sub):
            return a - b
        if isinstance(op, ast.Mult):
            return a * b
        if isinstance(op, ast.Div):
            return a / b
        if isinstance(op, ast.Mod):
            return a % b
        if isinstance(op, ast.FloorDiv):
            return a // b
        return math.pow(a, b)

    def __call__(self, x: float) -> float:
        try:
            v = self._ev(self._tree, float(x))
        except (ArithmeticError, ValueError, TypeError):
            return math.nan
        return v if isinstance(v, float) or isinstance(v, int) else math.nan


def _fmt(v: float) -> str:
    if v != v:
        return "—"
    if v in (math.inf, -math.inf):
        return "∞" if v > 0 else "-∞"
    a = abs(v)
    if a < 1e-12:
        return "0"
    if a >= 1e9 or a < 1e-5:
        return f"{v:.4g}"
    s = f"{v:.5f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _nice_step(span: float, target: int = 6) -> float:
    raw = span / max(target, 1)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 2.5, 5, 10):
        if raw <= m * mag:
            return m * mag
    return 10 * mag


# ------------------------------------------------------------------ analysis
def _analyze(fn: _Fn, xmin: float, xmax: float) -> dict:
    n = 600
    xs = [xmin + (xmax - xmin) * i / n for i in range(n + 1)]
    ys = [fn(x) for x in xs]
    span = xmax - xmin

    zeros: List[float] = []

    def add_zero(z: float) -> None:
        if all(abs(z - o) > 1e-6 * span for o in zeros):
            zeros.append(z)

    for i in range(n):
        a, b, fa, fb = xs[i], xs[i + 1], ys[i], ys[i + 1]
        if fa != fa or fb != fb:
            continue
        if fa == 0:
            add_zero(a)
        elif fa * fb < 0:
            lo, hi, flo = a, b, fa
            for _ in range(60):
                mid = (lo + hi) / 2
                fm = fn(mid)
                if fm != fm:
                    break
                if fm == 0 or (fm < 0) == (flo < 0):
                    if fm == 0:
                        lo = hi = mid
                        break
                    lo, flo = mid, fm
                else:
                    hi = mid
            z = (lo + hi) / 2
            fz = fn(z)
            if fz == fz and abs(fz) < 1e-6 * max(1.0, abs(fa), abs(fb)):
                add_zero(z)
    if ys[-1] == 0:
        add_zero(xs[-1])

    extrema: List[Tuple[str, float, float]] = []
    for i in range(1, n):
        y0, y1, y2 = ys[i - 1], ys[i], ys[i + 1]
        if y0 != y0 or y1 != y1 or y2 != y2:
            continue
        kind = "max" if (y1 > y0 and y1 > y2) else "min" if (y1 < y0 and y1 < y2) else None
        if not kind:
            continue
        sign = -1.0 if kind == "max" else 1.0
        lo, hi = xs[i - 1], xs[i + 1]
        g = (math.sqrt(5) - 1) / 2
        c, d = hi - g * (hi - lo), lo + g * (hi - lo)
        for _ in range(60):
            fc, fd = sign * fn(c), sign * fn(d)
            if fc != fc or fd != fd:
                break
            if fc < fd:
                hi, d = d, c
                c = hi - g * (hi - lo)
            else:
                lo, c = c, d
                d = lo + g * (hi - lo)
        xe = round((lo + hi) / 2, 6)
        ye = fn(xe)
        if ye != ye or abs(ye) > 1e8:
            continue
        ok = ye >= max(y0, y1, y2) - 1e-9 if kind == "max" else ye <= min(y0, y1, y2) + 1e-9
        if ok and all(abs(xe - e[1]) > 1e-4 * span for e in extrema):
            extrema.append((kind, xe, ye))

    y_int = fn(0.0) if xmin <= 0 <= xmax else math.nan
    return {"zeros": zeros[:6], "extrema": extrema[:4], "yint": y_int}


# ------------------------------------------------------------------ parsing
@dataclass
class FuncSpec:
    fns: List[Tuple[str, _Fn]] = field(default_factory=list)
    xmin: float = -10.0
    xmax: float = 10.0
    x0: Optional[float] = None


def parse_func_block(body: str) -> Optional[FuncSpec]:
    spec = FuncSpec()
    names = "fgh"
    for raw in body.splitlines():
        line = raw.strip().strip("`").strip()
        if not line:
            continue
        m = re.match(r"^(range|xrange|domain|x|start|title)\s*[:=]\s*(.+)$", line, re.I)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip().replace("−", "-")
            if key in ("range", "xrange", "domain"):
                nums = re.findall(r"-?\d+(?:\.\d+)?", val)
                if len(nums) >= 2:
                    a, b = float(nums[0]), float(nums[1])
                    if a < b and (b - a) <= 1e6:
                        spec.xmin, spec.xmax = a, b
            elif key in ("x", "start"):
                try:
                    spec.x0 = float(val)
                except ValueError:
                    pass
            continue
        if len(spec.fns) >= 3:
            continue
        try:
            fn = _Fn(line)
        except (ValueError, SyntaxError, RecursionError):
            continue
        nm = re.match(r"^\s*([A-Za-z])\s*\(\s*x\s*\)\s*=", line)
        spec.fns.append((nm.group(1) if nm else names[len(spec.fns)], fn))
    if not spec.fns:
        return None
    if spec.x0 is not None:
        spec.x0 = min(max(spec.x0, spec.xmin), spec.xmax)
    return spec


def _line_path(line: str) -> Optional[str]:
    """If the whole line is just an existing file path, return it."""
    from core.api_saia import extract_file_paths

    candidate = line.strip().strip("`'\"*-• ").strip()
    if not re.match(r"^[A-Za-z]:[\\/]", candidate):
        return None
    found = extract_file_paths(candidate, max_items=1)
    if found and os.path.normcase(found[0]) == os.path.normcase(os.path.normpath(candidate)):
        return found[0]
    return None


_FENCE = re.compile(r"```[ \t]*(saia[-_]?func|saia[-_]?plot|saia[-_]?file)[ \t]*\r?\n(.*?)```", re.S | re.I)

Segment = Tuple[str, Any]


def split_answer(text: str) -> List[Segment]:
    """Returns [('text', str) | ('func', FuncSpec) | ('file', path), ...] in reading order."""
    from core.api_saia import extract_file_paths

    segments: List[Segment] = []
    seen_files: set = set()

    def add_text(chunk: str) -> None:
        lines, buf = chunk.split("\n"), []

        def flush() -> None:
            body = "\n".join(buf).strip()
            buf.clear()
            if body:
                segments.append(("text", body))

        for line in lines:
            path = _line_path(line)
            if path:
                flush()
                if path.lower() not in seen_files:
                    seen_files.add(path.lower())
                    segments.append(("file", path))
            else:
                buf.append(line)
        flush()
        # paths mentioned inside sentences: put their cards right after this text
        for path in extract_file_paths(chunk):
            if path.lower() not in seen_files:
                seen_files.add(path.lower())
                segments.append(("file", path))

    pos = 0
    for m in _FENCE.finditer(text):
        add_text(text[pos:m.start()])
        kind, body = m.group(1).lower(), m.group(2)
        if "file" in kind:
            for line in body.splitlines():
                p = line.strip().strip("`'\"")
                if p and os.path.isfile(p) and p.lower() not in seen_files:
                    seen_files.add(p.lower())
                    segments.append(("file", os.path.normpath(p)))
        else:
            spec = parse_func_block(body)
            if spec:
                segments.append(("func", spec))
            else:
                segments.append(("text", m.group(0)))
        pos = m.end()
    add_text(text[pos:])
    return segments


# ------------------------------------------------------------------ shared helpers
def _wheel_all(widget: tk.Misc, handler: Optional[Callable]) -> None:
    if not handler:
        return
    widget.bind("<MouseWheel>", handler)
    for child in widget.winfo_children():
        _wheel_all(child, handler)


class _Styled:
    """Mixin: remembers widget -> {option: Theme token} so the card can be re-colored on theme change."""

    def _init_style(self, theme: Any) -> None:
        self.theme = theme
        self._styled: List[Tuple[tk.Misc, dict]] = []

    def _col(self, token: str) -> str:
        return token if token.startswith("#") else getattr(self.theme, token)

    def _reg(self, widget: tk.Misc, **opts: str) -> tk.Misc:
        self._styled.append((widget, opts))
        widget.configure(**{k: self._col(v) for k, v in opts.items()})
        return widget

    def _restyle_widgets(self) -> None:
        for widget, opts in self._styled:
            try:
                widget.configure(**{k: self._col(v) for k, v in opts.items()})
            except tk.TclError:
                pass


def _tip(widget: tk.Misc, text: str) -> None:
    state = {"win": None}

    def show(_e: Any) -> None:
        if state["win"] or not text:
            return
        win = tk.Toplevel(widget)
        win.wm_overrideredirect(True)
        win.wm_geometry(f"+{widget.winfo_rootx() + 4}+{widget.winfo_rooty() + widget.winfo_height() + 4}")
        tk.Label(win, text=text, bg="#111111", fg="#ffffff", font=("Segoe UI", 8), padx=6, pady=2).pack()
        state["win"] = win

    def hide(_e: Any) -> None:
        if state["win"]:
            state["win"].destroy()
            state["win"] = None

    widget.bind("<Enter>", show, add="+")
    widget.bind("<Leave>", hide, add="+")
    widget.bind("<Destroy>", hide, add="+")


# ------------------------------------------------------------------ function card
class FunctionCard(tk.Frame, _Styled):
    SERIES = ("ACCENT", "#f2a65a", "#a78bfa")

    def __init__(self, parent: tk.Misc, theme: Any, spec: FuncSpec, t: Callable[..., str],
                 width: int = 460, on_wheel: Optional[Callable] = None):
        tk.Frame.__init__(self, parent, bd=0, highlightthickness=1)
        self._init_style(theme)
        self.spec, self.t = spec, t
        self.cw = max(240, width - 30)
        self.ch = 300

        span = spec.xmax - spec.xmin
        raw = span / 200
        mag = 10 ** math.floor(math.log10(raw))
        self.res = next(m * mag for m in (1, 2, 5, 10) if raw <= m * mag)
        x0 = spec.x0 if spec.x0 is not None else spec.xmin + span * 0.675
        self.x = round(round(x0 / self.res) * self.res, 8)
        self.x = min(max(self.x, spec.xmin), spec.xmax)

        self._samples = [self._sample(fn) for _, fn in spec.fns]
        self.ylim = self._y_limits()
        self._analysis = [_analyze(fn, spec.xmin, spec.xmax) for _, fn in spec.fns]

        self._reg(self, bg="BG_FIELD", highlightbackground="BORDER", highlightcolor="BORDER")
        inner = self._reg(tk.Frame(self), bg="BG_FIELD")
        inner.pack(fill="both", padx=14, pady=12)

        self._reg(tk.Label(inner, text=t("func_title"), font=("Segoe UI", 8, "bold"), anchor="w"),
                  bg="BG_FIELD", fg="TEXT_MUTED").pack(fill="x", pady=(0, 8))

        formula = self._reg(tk.Frame(inner), bg="ACCENT_DIM")
        formula.pack(fill="x", pady=(0, 10))
        for i, (name, fn) in enumerate(spec.fns):
            row = self._reg(tk.Frame(formula), bg="ACCENT_DIM")
            row.pack(pady=(8, 8 if i == len(spec.fns) - 1 else 0))
            if len(spec.fns) > 1:
                self._reg(tk.Label(row, text="●", font=("Segoe UI", 11)), bg="ACCENT_DIM", fg=self.SERIES[i]).pack(side="left", padx=(0, 6))
            self._reg(
                tk.Label(row, text=f"{name}(x) = {fn.pretty}", font=("Segoe UI", 14), wraplength=self.cw - 50),
                bg="ACCENT_DIM", fg="TEXT_PRIMARY",
            ).pack(side="left")

        srow = self._reg(tk.Frame(inner), bg="BG_FIELD")
        srow.pack(fill="x", pady=(0, 10))
        self.x_label = self._reg(
            tk.Label(srow, text="", font=("Segoe UI", 10, "bold"), width=11, anchor="w"),
            bg="BG_FIELD", fg="TEXT_PRIMARY",
        )
        self.x_label.pack(side="left")
        self.scale = tk.Scale(
            srow, from_=spec.xmin, to=spec.xmax, resolution=self.res, orient="horizontal", showvalue=False,
            command=self._on_scale, bd=0, highlightthickness=0, sliderlength=16, width=9, sliderrelief="flat",
        )
        self._reg(self.scale, bg="ACCENT", troughcolor="BORDER_LIGHT", activebackground="ACCENT_HOVER", fg="TEXT_PRIMARY")
        self.scale.set(self.x)
        self.scale.pack(side="left", fill="x", expand=True)

        res_box = self._reg(tk.Frame(inner), bg="BG_PANEL")
        res_box.pack(fill="x", pady=(0, 10))
        self._readouts: List[Tuple[tk.Label, tk.Label, tk.Label]] = []
        for i, (name, _fn) in enumerate(spec.fns):
            row = self._reg(tk.Frame(res_box), bg="BG_PANEL")
            row.pack(pady=(8, 8 if i == len(spec.fns) - 1 else 0))
            if len(spec.fns) > 1:
                self._reg(tk.Label(row, text="●", font=("Segoe UI", 10)), bg="BG_PANEL", fg=self.SERIES[i]).pack(side="left", padx=(0, 6))
            lhs = self._reg(tk.Label(row, text="", font=("Segoe UI", 11)), bg="BG_PANEL", fg="TEXT_SECONDARY")
            val = self._reg(tk.Label(row, text="", font=("Segoe UI", 12, "bold")), bg="BG_PANEL", fg="ACCENT_HOVER")
            slope = self._reg(tk.Label(row, text="", font=("Segoe UI", 9)), bg="BG_PANEL", fg="TEXT_MUTED")
            lhs.pack(side="left")
            val.pack(side="left", padx=(4, 0))
            slope.pack(side="left", padx=(12, 0))
            self._readouts.append((lhs, val, slope))

        self.canvas = tk.Canvas(inner, width=self.cw, height=self.ch, bd=0, highlightthickness=1)
        self._reg(self.canvas, bg="BG_PANEL", highlightbackground="BORDER", highlightcolor="BORDER")
        self.canvas.pack(fill="x", pady=(0, 10))
        self.canvas.bind("<Configure>", lambda e: self._draw())
        self.canvas.bind("<Button-1>", self._on_canvas)
        self.canvas.bind("<B1-Motion>", self._on_canvas)
        self.canvas.configure(cursor="sb_h_double_arrow")

        lines = self._analysis_lines()
        if lines:
            self._reg(
                tk.Label(inner, text="\n".join(lines), font=("Segoe UI", 9), anchor="w", justify="left", wraplength=self.cw),
                bg="BG_FIELD", fg="TEXT_SECONDARY",
            ).pack(fill="x")

        self._update_readouts()
        self._draw()
        _wheel_all(self, on_wheel)

    # ---- data
    def _sample(self, fn: _Fn) -> List[Tuple[float, float]]:
        n = 400
        a, b = self.spec.xmin, self.spec.xmax
        return [(a + (b - a) * i / n, fn(a + (b - a) * i / n)) for i in range(n + 1)]

    def _y_limits(self) -> Tuple[float, float]:
        vals = sorted(y for s in self._samples for _, y in s if y == y and abs(y) < 1e12)
        if not vals:
            return -1.0, 1.0
        lo, hi = vals[0], vals[-1]
        if len(vals) >= 30:
            k = max(1, int(len(vals) * 0.03))
            lo2, hi2 = vals[k], vals[-1 - k]
            if hi2 > lo2 and (hi - lo) > 6 * (hi2 - lo2):
                lo, hi = lo2, hi2
        if hi - lo < 1e-9:
            lo, hi = lo - 1, hi + 1
        pad = (hi - lo) * 0.12
        lo, hi = lo - pad, hi + pad
        if lo > 0 and lo < (hi - lo) * 0.5:
            lo = -pad
        if hi < 0 and -hi < (hi - lo) * 0.5:
            hi = pad
        return lo, hi

    def _analysis_lines(self) -> List[str]:
        t, out = self.t, []
        for (name, _fn), info in zip(self.spec.fns, self._analysis):
            parts = []
            zeros = ", ".join(f"x = {_fmt(z)}" for z in info["zeros"]) or t("func_none")
            parts.append(f"{t('func_zeros')}: {zeros}")
            if info["yint"] == info["yint"]:
                parts.append(f"{t('func_yint')}: {_fmt(info['yint'])}")
            ext = ", ".join(
                f"{t('func_' + k)} ({_fmt(x)}, {_fmt(y)})" for k, x, y in info["extrema"]
            ) or t("func_none")
            parts.append(f"{t('func_extrema')}: {ext}")
            prefix = f"{name}: " if len(self.spec.fns) > 1 else ""
            out.append(prefix + "   ·   ".join(parts))
        return out

    # ---- interaction
    def _on_scale(self, value: str) -> None:
        try:
            self.x = round(float(value), 8)
        except ValueError:
            return
        self._update_readouts()
        self._draw_point()

    def _on_canvas(self, event: Any) -> None:
        ml, mr = self._geo[0], self._geo[1]
        W = self._geo[4]
        frac = (event.x - ml) / max(1, (W - mr - ml))
        x = self.spec.xmin + min(max(frac, 0.0), 1.0) * (self.spec.xmax - self.spec.xmin)
        x = round(round(x / self.res) * self.res, 8)
        self.scale.set(min(max(x, self.spec.xmin), self.spec.xmax))

    def _slope(self, fn: _Fn, x: float) -> float:
        h = 1e-5 * (1 + abs(x))
        a, b = fn(x - h), fn(x + h)
        return (b - a) / (2 * h) if a == a and b == b else math.nan

    def _update_readouts(self) -> None:
        self.x_label.configure(text=f"x = {_fmt(self.x)}")
        for (name, fn), (lhs, val, slope) in zip(self.spec.fns, self._readouts):
            lhs.configure(text=f"{name}({_fmt(self.x)}) =")
            val.configure(text=_fmt(fn(self.x)))
            slope.configure(text=f"{self.t('func_slope')}: {_fmt(self._slope(fn, self.x))}")

    def restyle(self) -> None:
        self._restyle_widgets()
        self._draw()

    # ---- drawing
    def _series(self, i: int) -> str:
        return self._col(self.SERIES[i])

    def _geometry(self) -> Tuple[int, int, int, int, int, int]:
        W, H = self.canvas.winfo_width(), self.canvas.winfo_height()
        if W < 80:
            W = self.cw
        if H < 80:
            H = self.ch
        return 40, 12, 14, 22, W, H

    def _px(self, x: float, y: float) -> Tuple[float, float]:
        ml, mr, mt, mb, W, H = self._geo
        xmin, xmax, (ymin, ymax) = self.spec.xmin, self.spec.xmax, self.ylim
        return (ml + (x - xmin) / (xmax - xmin) * (W - ml - mr),
                (H - mb) - (y - ymin) / (ymax - ymin) * (H - mb - mt))

    def _draw(self) -> None:
        c = self.canvas
        try:
            c.delete("all")
        except tk.TclError:
            return
        self._geo = self._geometry()
        ml, mr, mt, mb, W, H = self._geo
        xmin, xmax = self.spec.xmin, self.spec.xmax
        ymin, ymax = self.ylim
        grid, axis, muted = self._col("BORDER"), self._col("TEXT_MUTED"), self._col("TEXT_MUTED")

        step = _nice_step(xmax - xmin, 8)
        k = math.ceil(xmin / step - 1e-9)
        while k * step <= xmax + 1e-9:
            gx = self._px(k * step, 0)[0]
            c.create_line(gx, mt, gx, H - mb, fill=grid)
            c.create_text(gx, H - mb + 4, text=_fmt(k * step), anchor="n", fill=muted, font=("Segoe UI", 7))
            k += 1
        step = _nice_step(ymax - ymin, 5)
        k = math.ceil(ymin / step - 1e-9)
        while k * step <= ymax + 1e-9:
            gy = self._px(0, k * step)[1]
            c.create_line(ml, gy, W - mr, gy, fill=grid)
            c.create_text(ml - 5, gy, text=_fmt(k * step), anchor="e", fill=muted, font=("Segoe UI", 7))
            k += 1

        if ymin <= 0 <= ymax:
            ay = self._px(0, 0)[1]
            c.create_line(ml, ay, W - mr, ay, fill=axis, width=1.5)
        if xmin <= 0 <= xmax:
            ax = self._px(0, 0)[0]
            c.create_line(ax, mt, ax, H - mb, fill=axis, width=1.5)

        view = ymax - ymin
        for i, samples in enumerate(self._samples):
            color = self._series(i)
            prev = None
            for x, y in samples:
                if y != y or abs(y) > 1e12:
                    prev = None
                    continue
                if prev is not None and abs(y - prev[1]) <= 1.2 * view:
                    seg = self._clip(prev[0], prev[1], x, y)
                    if seg:
                        (x0, y0), (x1, y1) = seg
                        c.create_line(*self._px(x0, y0), *self._px(x1, y1), fill=color, width=2.5, capstyle="round")
                prev = (x, y)
        self._draw_point()

    def _clip(self, x0: float, y0: float, x1: float, y1: float):
        lo, hi = self.ylim
        if (y0 < lo and y1 < lo) or (y0 > hi and y1 > hi):
            return None

        def at(y: float) -> float:
            return x0 + (y - y0) / (y1 - y0) * (x1 - x0)

        ax, ay, bx, by = x0, y0, x1, y1
        if y0 < lo:
            ax, ay = at(lo), lo
        elif y0 > hi:
            ax, ay = at(hi), hi
        if y1 < lo:
            bx, by = at(lo), lo
        elif y1 > hi:
            bx, by = at(hi), hi
        return (ax, ay), (bx, by)

    def _draw_point(self) -> None:
        c = self.canvas
        try:
            c.delete("pt")
        except tk.TclError:
            return
        ml, mr, mt, mb, W, H = self._geo
        ymin, ymax = self.ylim
        axis_y = self._px(0, 0)[1] if ymin <= 0 <= ymax else H - mb
        axis_x = self._px(0, 0)[0] if self.spec.xmin <= 0 <= self.spec.xmax else ml
        for i, (_name, fn) in enumerate(self.spec.fns):
            y = fn(self.x)
            if y != y or not (ymin <= y <= ymax):
                continue
            color = self._series(i)
            px, py = self._px(self.x, y)
            c.create_line(px, py, px, axis_y, fill=color, dash=(4, 3), tags="pt")
            c.create_line(px, py, axis_x, py, fill=color, dash=(4, 3), tags="pt")
            c.create_oval(px - 5, py - 5, px + 5, py + 5, fill=color, outline=self._col("BG_PANEL"), width=2, tags="pt")
            label = f"({_fmt(self.x)}, {_fmt(y)})"
            anchor = "s"
            ty = py - 9
            if ty < mt + 8:
                anchor, ty = "n", py + 9
            tx = min(max(px, ml + 36), W - mr - 36)
            c.create_text(tx, ty, text=label, anchor=anchor, fill=color, font=("Segoe UI", 8, "bold"), tags="pt")


# ------------------------------------------------------------------ file card
_ICONS = {
    ".html": "🌐", ".htm": "🌐", ".pdf": "📕", ".doc": "📘", ".docx": "📘", ".xls": "📗", ".xlsx": "📗",
    ".csv": "📗", ".ppt": "📙", ".pptx": "📙", ".png": "🖼", ".jpg": "🖼", ".jpeg": "🖼", ".gif": "🖼",
    ".webp": "🖼", ".bmp": "🖼", ".wav": "🎵", ".mp3": "🎵", ".ogg": "🎵", ".mp4": "🎬", ".mkv": "🎬",
    ".avi": "🎬", ".zip": "🗜", ".rar": "🗜", ".7z": "🗜", ".py": "🐍", ".txt": "📝", ".md": "📝",
    ".json": "🧾", ".js": "🧾", ".css": "🧾",
}


_CODE_EXT = {
    ".py", ".js", ".ts", ".html", ".htm", ".css", ".json", ".txt", ".md", ".csv", ".sql", ".ps1", ".bat",
    ".sh", ".xml", ".yaml", ".yml", ".java", ".c", ".cpp", ".cs", ".go", ".rs",
}


def _human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{n} B"


class FileCard(tk.Frame, _Styled):
    def __init__(self, parent: tk.Misc, theme: Any, path: str, t: Callable[..., str],
                 width: int = 460, on_wheel: Optional[Callable] = None,
                 notify: Optional[Callable[[str], None]] = None):
        tk.Frame.__init__(self, parent, bd=0, highlightthickness=1, cursor="hand2")
        self._init_style(theme)
        self.path, self.t, self.notify = path, t, notify
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1].lower()
        try:
            sub = f"{t('file_open')} · {_human_size(os.path.getsize(path))}"
        except OSError:
            sub = t("file_missing")
        shown = name if len(name) <= 42 else name[:24] + "…" + name[-16:]

        self._reg(self, bg="BG_FIELD", highlightbackground="BORDER", highlightcolor="BORDER")
        self.configure(width=min(width, 520), height=62)
        self.pack_propagate(False)

        icon = self._reg(tk.Label(self, text=_ICONS.get(ext, "📄"), font=("Segoe UI Emoji", 20), cursor="hand2"),
                         bg="BG_FIELD", fg="TEXT_PRIMARY")
        icon.pack(side="left", padx=(14, 8))

        actions = self._reg(tk.Frame(self), bg="BG_FIELD")
        actions.pack(side="right", padx=(0, 10))
        buttons = [("⬇", t("file_save_copy"), self._save_copy), ("📂", t("file_show_folder"), self._show_folder)]
        if ext in _CODE_EXT:  # code / text files: one click copies the whole source
            buttons.insert(0, ("📋", t("copy_code"), self._copy_code))
        for glyph, tip, cmd in buttons:
            btn = tk.Button(actions, text=glyph, font=("Segoe UI Emoji", 11), relief="flat", bd=0, padx=6, pady=2,
                            cursor="hand2", command=cmd)
            self._reg(btn, bg="BG_FIELD", fg="TEXT_SECONDARY", activebackground="BORDER", activeforeground="TEXT_PRIMARY")
            btn.pack(side="left", padx=2)
            _tip(btn, tip)

        text_box = self._reg(tk.Frame(self, cursor="hand2"), bg="BG_FIELD")
        text_box.pack(side="left", fill="both", expand=True)
        name_lbl = self._reg(tk.Label(text_box, text=shown, font=("Segoe UI", 10, "bold"), anchor="w", cursor="hand2"),
                             bg="BG_FIELD", fg="TEXT_PRIMARY")
        sub_lbl = self._reg(tk.Label(text_box, text=sub, font=("Segoe UI", 8), anchor="w", cursor="hand2"),
                            bg="BG_FIELD", fg="TEXT_MUTED")
        name_lbl.pack(fill="x", pady=(12, 0))
        sub_lbl.pack(fill="x")

        for w in (self, icon, text_box, name_lbl, sub_lbl):
            w.bind("<Button-1>", lambda e: self._open())
        _wheel_all(self, on_wheel)

    def _notify(self, text: str) -> None:
        if self.notify:
            self.notify(text)

    def _open(self) -> None:
        if not os.path.isfile(self.path):
            self._notify(self.t("file_missing"))
            return
        try:
            if sys.platform == "win32":
                os.startfile(self.path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", self.path])
            else:
                subprocess.Popen(["xdg-open", self.path])
        except Exception as exc:
            self._notify(self.t("file_open_error", error=str(exc)[:140]))

    def _save_copy(self) -> None:
        if not os.path.isfile(self.path):
            self._notify(self.t("file_missing"))
            return
        ext = os.path.splitext(self.path)[1]
        target = filedialog.asksaveasfilename(
            initialfile=os.path.basename(self.path), defaultextension=ext, parent=self.winfo_toplevel()
        )
        if not target:
            return
        try:
            shutil.copy2(self.path, target)
            self._notify(self.t("saved"))
        except Exception as exc:
            self._notify(self.t("file_open_error", error=str(exc)[:140]))

    def _copy_code(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8-sig", errors="replace") as fh:
                content = fh.read(400_000)
            self.clipboard_clear()
            self.clipboard_append(content)
            self._notify(self.t("copied_code"))
        except Exception as exc:
            self._notify(self.t("file_open_error", error=str(exc)[:140]))

    def _show_folder(self) -> None:
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer", f"/select,{self.path}"])
            elif sys.platform == "darwin":
                subprocess.Popen(["open", "-R", self.path])
            else:
                subprocess.Popen(["xdg-open", os.path.dirname(self.path)])
        except Exception as exc:
            self._notify(self.t("file_open_error", error=str(exc)[:140]))

    def restyle(self) -> None:
        self._restyle_widgets()