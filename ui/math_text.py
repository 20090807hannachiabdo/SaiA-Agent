"""
ui/math_text.py - turns LaTeX-style math into plain, human-looking text.

    $\\frac{d}{dx}(x^n) = n \\cdot x^{n-1}$   ->   d/dx(xⁿ) = n · xⁿ⁻¹
    $\\left(\\frac{f}{g}\\right)' = \\frac{f'g - fg'}{g^2}$   ->   (f/g)' = (f'g - fg')/g²

Only text outside code blocks / `inline code` is touched. Digits stay as typed
(Western digits 0-9). Pure Python: no tkinter, safe to test anywhere.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Strong left-to-right mark: keeps "(f/g)'" in the right visual order inside Arabic text.
# Set to "" if your Tk build ever shows it as a small box.
LRM = "\u200e"

_SUP_SRC = "0123456789+-−=()abcdefghijklmnoprstuvwxyzABDEGHIJKLMNOPRTUVW"
_SUP_DST = "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁻⁼⁽⁾ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻᴬᴮᴰᴱᴳᴴᴵᴶᴷᴸᴹᴺᴼᴾᴿᵀᵁⱽᵂ"
_SUB_SRC = "0123456789+-−=()aehijklmnoprstuvx"
_SUB_DST = "₀₁₂₃₄₅₆₇₈₉₊₋₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ"
assert len(_SUP_SRC) == len(_SUP_DST) and len(_SUB_SRC) == len(_SUB_DST)
SUP = dict(zip(_SUP_SRC, _SUP_DST))
SUB = dict(zip(_SUB_SRC, _SUB_DST))
_SUP_VALUES = set(SUP.values())
_SUB_VALUES = set(SUB.values())

SYMBOLS = {
    # greek
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε", "varepsilon": "ε",
    "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ",
    "sigma": "σ", "tau": "τ", "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ",
    "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # operators / relations
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓", "ast": "*", "star": "⋆",
    "circ": "∘", "bullet": "•", "oplus": "⊕", "otimes": "⊗",
    "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠", "approx": "≈",
    "equiv": "≡", "sim": "∼", "simeq": "≃", "cong": "≅", "propto": "∝", "ll": "≪", "gg": "≫",
    "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "subseteq": "⊆", "supset": "⊃",
    "supseteq": "⊇", "cup": "∪", "cap": "∩", "setminus": "∖", "emptyset": "∅", "varnothing": "∅",
    "forall": "∀", "exists": "∃", "nexists": "∄", "neg": "¬", "lnot": "¬", "land": "∧",
    "lor": "∨", "wedge": "∧", "vee": "∨", "perp": "⊥", "parallel": "∥", "angle": "∠",
    "therefore": "∴", "because": "∵",
    # arrows
    "to": "→", "rightarrow": "→", "leftarrow": "←", "leftrightarrow": "↔", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "Leftrightarrow": "⇔", "implies": "⇒", "iff": "⇔", "mapsto": "↦",
    "uparrow": "↑", "downarrow": "↓", "longrightarrow": "⟶", "gets": "←",
    # misc
    "infty": "∞", "partial": "∂", "nabla": "∇", "degree": "°", "prime": "′", "hbar": "ħ",
    "ell": "ℓ", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ", "dots": "…", "ldots": "…", "cdots": "⋯",
    "vdots": "⋮", "ddots": "⋱", "sum": "∑", "prod": "∏", "int": "∫", "iint": "∬",
    "iiint": "∭", "oint": "∮", "bigcup": "⋃", "bigcap": "⋂", "lceil": "⌈", "rceil": "⌉",
    "lfloor": "⌊", "rfloor": "⌋", "langle": "⟨", "rangle": "⟩", "lvert": "∣", "rvert": "∣",
    "vert": "∣", "mid": "∣", "Vert": "∥", "lVert": "∥", "rVert": "∥", "triangle": "△",
    "square": "□", "checkmark": "✓",
}
_BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "H": "ℍ"}
_FUNCS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh",
    "tanh", "log", "ln", "lg", "exp", "det", "gcd", "deg", "dim", "ker", "arg", "mod", "Pr",
}
_LIMOPS = {"lim", "limsup", "liminf", "max", "min", "sup", "inf", "sum", "prod", "int", "iint",
           "iiint", "oint", "bigcup", "bigcap"}
_BIG_OPS = {"sum", "prod", "int", "iint", "iiint", "oint", "bigcup", "bigcap"}
_WRAP1 = {"text", "textrm", "textbf", "textit", "mathrm", "mathbf", "mathit", "mathsf", "mathtt",
          "mathcal", "mathscr", "mathfrak", "operatorname", "mbox", "boldsymbol", "bm", "emph",
          "mathnormal", "textnormal"}
_IGNORE = {"displaystyle", "textstyle", "scriptstyle", "limits", "nolimits", "left.", "right."}
_DELIM_CMDS = {"left", "right", "big", "Big", "bigg", "Bigg", "bigl", "bigr", "Bigl", "Bigr",
               "biggl", "biggr", "Biggl", "Biggr", "middle"}
_ACCENTS = {"bar": "\u0304", "overline": "\u0305", "hat": "\u0302", "widehat": "\u0302",
            "tilde": "\u0303", "widetilde": "\u0303", "vec": "\u20d7", "dot": "\u0307",
            "ddot": "\u0308", "check": "\u030c", "acute": "\u0301", "grave": "\u0300"}
_SPACES = {",": " ", ";": " ", ":": " ", "!": "", " ": " ", ">": " "}
_ESCAPED = set("{}%$&_#")
_DELIM_MAP = {".": "", "\\{": "{", "\\}": "}", "\\|": "∥", "|": "∣", "\\langle": "⟨",
              "\\rangle": "⟩", "\\lvert": "∣", "\\rvert": "∣", "\\vert": "∣", "\\Vert": "∥",
              "\\lceil": "⌈", "\\rceil": "⌉", "\\lfloor": "⌊", "\\rfloor": "⌋", "\\\\": "∥"}
_CMD = re.compile(r"\\([a-zA-Z]+)\*?")


# ------------------------------------------------------------------ helpers
def _strict_script(s: str, table: dict, values: set) -> Optional[str]:
    """Unicode super/subscript string, or None when some character has no glyph."""
    s = s.replace(" ", "")
    out = []
    for ch in s:
        if ch in table:
            out.append(table[ch])
        elif ch in values:
            out.append(ch)
        else:
            return None
    return "".join(out)


def _script(s: str, sup: bool) -> str:
    s = s.strip()
    if not s:
        return ""
    mapped = _strict_script(s, SUP if sup else SUB, _SUP_VALUES if sup else _SUB_VALUES)
    if mapped is not None:
        return mapped
    mark = "^" if sup else "_"
    return f"{mark}({s})" if len(s) > 1 else f"{mark}{s}"


_NO_WRAP_CHARS = set(" +−±∓=<>≤≥≠≈·×÷/,;")


def _is_simple(s: str) -> bool:
    body = s[1:] if s[:1] in "-−" else s
    if not body:
        return False
    if re.fullmatch(r"\([^()]*\)", s):
        return True
    return not any(ch in _NO_WRAP_CHARS or ch.isspace() for ch in body) or bool(
        re.fullmatch(r"[^\s+\-−±∓=<>≤≥≠≈·×÷/,;()]+(\([^()]*\))?", body)
    )


def _wrap(s: str) -> str:
    return s if _is_simple(s) else f"({s})"


def _frac(a: str, b: str) -> str:
    a, b = a.strip(), b.strip()
    if not b:
        return a
    return f"{_wrap(a)}/{_wrap(b)}"


def _read_arg(s: str, i: int) -> Tuple[str, int]:
    """Reads one LaTeX argument ({...}, a command, or a single char)."""
    while i < len(s) and s[i] == " ":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    if s[i] == "\\":
        m = re.match(r"\\[a-zA-Z]+|\\.", s[i:])
        return m.group(0), i + len(m.group(0))
    return s[i], i + 1


def _read_optional(s: str, i: int) -> Tuple[Optional[str], int]:
    j = i
    while j < len(s) and s[j] == " ":
        j += 1
    if j < len(s) and s[j] == "[":
        k = s.find("]", j)
        if k != -1:
            return s[j + 1:k], k + 1
    return None, i


def _delim(tok: str) -> str:
    if tok in _DELIM_MAP:
        return _DELIM_MAP[tok]
    if tok.startswith("\\"):
        return SYMBOLS.get(tok[1:], tok[1:])
    return tok


def _accent(arg: str, comb: str) -> str:
    arg = arg.strip()
    if len(arg) == 1:
        return arg + comb
    return "".join(ch + comb if ch.isalnum() else ch for ch in arg)


def _limits(op: str, sub: str, sup: str) -> str:
    sub, sup = sub.strip(), sup.strip()
    if op in ("∑", "∏", "∫", "∬", "∭", "∮", "⋃", "⋂"):
        a = _strict_script(sub, SUB, _SUB_VALUES) if sub else ""
        b = _strict_script(sup, SUP, _SUP_VALUES) if sup else ""
        if a is not None and b is not None:
            return op + a + b
        inner = sub
        if sup:
            inner = f"{sub}→{sup}" if sub else sup
        return f"{op}({inner})" if inner else op
    return f"{op}({sub})" if sub else op


# ------------------------------------------------------------------ core
def _environment(env: str, body: str) -> str:
    rows = [r.strip() for r in body.split("\\\\") if r.strip()]
    cells = [[latex_to_text(c.strip()) for c in r.split("&")] for r in rows]
    if env.rstrip("*") == "cases":
        return "{ " + "; ".join("   ".join(c) for c in cells) + " }"
    if "matrix" in env:
        left, right = {"pmatrix": "(", "bmatrix": "[", "vmatrix": "|", "Vmatrix": "‖",
                       "Bmatrix": "{"}.get(env, "["), {"pmatrix": ")", "bmatrix": "]",
                                                         "vmatrix": "|", "Vmatrix": "‖",
                                                         "Bmatrix": "}"}.get(env, "]")
        return left + "; ".join(", ".join(c) for c in cells) + right
    return "; ".join("  ".join(c) for c in cells)


def latex_to_text(s: str) -> str:
    out: List[str] = []
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            m = _CMD.match(s, i)
            if m:
                name = m.group(1)
                i = m.end()
                if name in ("frac", "dfrac", "tfrac", "cfrac"):
                    a, i = _read_arg(s, i)
                    b, i = _read_arg(s, i)
                    out.append(_frac(latex_to_text(a), latex_to_text(b)))
                elif name == "binom":
                    a, i = _read_arg(s, i)
                    b, i = _read_arg(s, i)
                    out.append(f"C({latex_to_text(a)}, {latex_to_text(b)})")
                elif name == "sqrt":
                    idx, i = _read_optional(s, i)
                    a, i = _read_arg(s, i)
                    body = latex_to_text(a)
                    root = {"3": "∛", "4": "∜"}.get(idx.strip(), None) if idx else "√"
                    if idx and root is None:
                        out.append(_script(latex_to_text(idx), True) + "√" + _wrap(body))
                    else:
                        out.append(root + _wrap(body))
                elif name in _DELIM_CMDS:
                    tok, i = _read_arg(s, i)
                    out.append(_delim(tok))
                elif name in _ACCENTS:
                    a, i = _read_arg(s, i)
                    out.append(_accent(latex_to_text(a), _ACCENTS[name]))
                elif name == "mathbb":
                    a, i = _read_arg(s, i)
                    out.append("".join(_BLACKBOARD.get(ch, ch) for ch in a))
                elif name in _WRAP1:
                    a, i = _read_arg(s, i)
                    out.append(latex_to_text(a))
                elif name in ("quad", "qquad", "enspace", "thinspace"):
                    out.append(" ")
                elif name in ("begin",):
                    env, i = _read_arg(s, i)
                    end_tok = f"\\end{{{env}}}"
                    k = s.find(end_tok, i)
                    body = s[i:] if k == -1 else s[i:k]
                    i = len(s) if k == -1 else k + len(end_tok)
                    out.append(_environment(env, body))
                elif name == "end":
                    _, i = _read_arg(s, i)
                elif name in _IGNORE:
                    pass
                elif name in _LIMOPS:
                    op = SYMBOLS.get(name, name)
                    sub = sup = ""
                    while True:
                        j = i
                        while j < n and s[j] == " ":
                            j += 1
                        if j < n and s[j] in "_^":
                            which = s[j]
                            a, i = _read_arg(s, j + 1)
                            if which == "_":
                                sub = latex_to_text(a)
                            else:
                                sup = latex_to_text(a)
                        else:
                            break
                    out.append(_limits(op, sub, sup) + (" " if name in _BIG_OPS else ""))
                elif name in _FUNCS:
                    out.append(name)
                elif name in SYMBOLS:
                    out.append(SYMBOLS[name])
                else:
                    out.append(name + " ")  # unknown command: show its name, never a raw backslash
                continue
            # backslash + single non-letter char
            nxt = s[i + 1] if i + 1 < n else ""
            if nxt in _SPACES:
                out.append(_SPACES[nxt])
            elif nxt == "\\":
                out.append(" ")
            elif nxt in _ESCAPED or nxt:
                out.append(nxt)
            i += 2
            continue
        if c in "^_":
            a, i = _read_arg(s, i + 1)
            out.append(_script(latex_to_text(a), c == "^"))
            continue
        if c == "{":
            a, i = _read_arg(s, i)
            out.append(latex_to_text(a))
            continue
        if c == "}":
            i += 1
            continue
        if c == "~":
            out.append(" ")
        elif c == "|":
            out.append("∣")  # keeps Markdown table columns intact
        elif c == "-":
            out.append("-")
        else:
            out.append(c)
        i += 1
    return re.sub(r" {2,}", " ", "".join(out)).strip()


# ------------------------------------------------------------------ text level
_CODE = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)
_MATH = re.compile(
    r"\$\$(.+?)\$\$"
    r"|\\\[(.+?)\\\]"
    r"|\\\((.+?)\\\)"
    r"|(?<![\\$])\$(?=[^\s$])((?:[^$\n\\]|\\.)+?)(?<=[^\s\\])\$(?!\d)",
    re.S,
)
_BARE_CMD = re.compile(r"\\([a-zA-Z]+)")
_BARE_FRAC = re.compile(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}")
_BARE_SQRT = re.compile(r"\\sqrt\{([^{}]*)\}")


def _bare(seg: str) -> str:
    """Stray commands outside $...$ (the model forgot the dollars): symbols, \\frac, \\sqrt only."""
    if "\\" not in seg:
        return seg
    seg = _BARE_FRAC.sub(lambda m: _frac(m.group(1), m.group(2)), seg)
    seg = _BARE_SQRT.sub(lambda m: "√" + _wrap(m.group(1)), seg)
    return _BARE_CMD.sub(lambda m: SYMBOLS.get(m.group(1), m.group(0)), seg)


def _math_segment(seg: str) -> str:
    def repl(m: re.Match) -> str:
        body = next(g for g in m.groups() if g is not None).strip()
        try:
            res = latex_to_text(body)
        except Exception:
            return m.group(0)
        return f"{LRM}{res}{LRM}" if res else ""

    parts: List[str] = []
    pos = 0
    for m in _MATH.finditer(seg):
        parts.append(_bare(seg[pos:m.start()]))
        parts.append(repl(m))
        pos = m.end()
    parts.append(_bare(seg[pos:]))
    return "".join(parts)


def convert_math(text: str) -> str:
    """Converts math in `text`, leaving fenced code and `inline code` untouched."""
    if "$" not in text and "\\" not in text:
        return text
    pieces = _CODE.split(text)
    for idx in range(0, len(pieces), 2):  # even = outside code
        pieces[idx] = _math_segment(pieces[idx])
    return "".join(pieces)