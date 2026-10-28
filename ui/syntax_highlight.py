"""
ui/syntax_highlight.py - Lightweight syntax highlighter for the SaiA code blocks.

Pure-Python, regex based, zero dependencies. Produces VS Code-like colouring
(Dark+ / Light+ palettes) for tkinter.Text widgets.

Usage (see rich_text.CodeBlock):
    tokens = highlight(code, "python")          # [(start, end, tag), ...]
    for tag, a, b in tk_ranges(code, tokens):   # tag -> Tk "line.col" indices
        text_widget.tag_add(TAG_PREFIX + tag, a, b)
"""

from __future__ import annotations

import re
from bisect import bisect_right
from typing import Dict, Iterator, List, Optional, Tuple

Token = Tuple[int, int, str]

TAG_PREFIX = "sx_"
MAX_CHARS = 40000  # above this, skip highlighting (keeps the UI snappy)

# ------------------------------------------------------------------ palettes
DARK: Dict[str, str] = {
    "kw": "#569CD6", "ctl": "#C586C0", "string": "#CE9178", "comment": "#6A9955",
    "number": "#B5CEA8", "func": "#DCDCAA", "cls": "#4EC9B0", "type": "#4EC9B0",
    "builtin": "#DCDCAA", "const": "#4FC1FF", "deco": "#C586C0", "prop": "#9CDCFE",
    "attr": "#9CDCFE", "var": "#9CDCFE", "tag": "#569CD6",
}
LIGHT: Dict[str, str] = {
    "kw": "#0000FF", "ctl": "#AF00DB", "string": "#A31515", "comment": "#008000",
    "number": "#098658", "func": "#795E26", "cls": "#267F99", "type": "#267F99",
    "builtin": "#795E26", "const": "#0070C1", "deco": "#AF00DB", "prop": "#001080",
    "attr": "#E50000", "var": "#001080", "tag": "#800000",
}


def build_palette(dark: bool = True) -> Dict[str, str]:
    return dict(DARK if dark else LIGHT)


# ------------------------------------------------------------------ patterns
_NEVER = r"(?!)"
_NUM = r"(?<![\w.])(?:0[xX][0-9a-fA-F_]+|0[bB][01_]+|\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?)"
_C_COMMENT = r"//[^\n]*|/\*[\s\S]*?(?:\*/|$)"
_HASH = r"(?:(?m:^)|(?<=\s))#[^\n]*"
_DQ = r'"(?:\\.|[^"\\\n])*"?'
_SQ = r"'(?:\\.|[^'\\\n])*'?"
_BT = r"`(?:\\.|[^`\\])*`?"
_PY_STR = (
    r"""(?i:[rbfu]{0,2})(?:\"\"\"[\s\S]*?(?:\"\"\"|$)|'''[\s\S]*?(?:'''|$)"""
    r"""|"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)"""
)
_PS_STR = (
    r"""@'[\s\S]*?(?:'@|$)|@"[\s\S]*?(?:"@|$)|"(?:`.|[^"`\n])*"?|'(?:''|[^'\n])*'?"""
)
_SQL_STR = r"""'(?:''|[^'])*'?|"(?:\\.|[^"\\\n])*"?"""
_RUST_STR = r"""b?"(?:\\.|[^"\\])*"?|'(?:\\.|[^'\\\n])'"""
_SHELL_WORD = r"\$\{?(?:\w+:)?\w+\}?|-{1,2}[A-Za-z][\w-]*|[A-Za-z_][\w-]*"
_COLON = re.compile(r"\s*:")

_CLASS_PREV = {"class", "interface", "enum", "struct", "trait", "record"}
_FUNC_PREV = {"def", "function", "fn", "func"}
_CTRL = set(
    "if elif else for foreach while do return import from export try except catch finally "
    "raise throw with yield await break continue switch case default goto then fi done esac "
    "elseif until".split()
)


def _set(s: str) -> set:
    return set(s.split())


class _Lang:
    def __init__(self, comment: str, strings: str, kw: str = "", const: str = "", builtin: str = "",
                 types: str = "", deco: Optional[str] = None, word: str = r"[A-Za-z_$][\w$]*",
                 ci: bool = False, caps: bool = True, key_colon: bool = False, key_word: bool = False,
                 dollar: bool = False, cmdlet: Optional[str] = None):
        self.ci, self.caps, self.key_colon = ci, caps, key_colon
        self.key_word, self.dollar = key_word, dollar
        self.kw, self.const = _set(kw), _set(const)
        self.builtin, self.types = _set(builtin), _set(types)
        if ci:
            self.const = {c.lower() for c in self.const}
        self.cmdlet = re.compile(cmdlet) if cmdlet else None
        parts = [f"(?P<comment>{comment})", f"(?P<string>{strings})"]
        if deco:
            parts.append(f"(?P<deco>{deco})")
        parts += [f"(?P<number>{_NUM})", f"(?P<word>{word})"]
        self.rx = re.compile("|".join(parts))

    def _word(self, w: str, code: str, end: int, prev: str) -> Optional[str]:
        wl = w.lower() if self.ci else w
        if self.dollar and w.startswith("$"):
            return "const" if wl in self.const else "var"
        if self.dollar and w.startswith("-"):
            return "attr"
        if prev in _CLASS_PREV:
            return "cls"
        if prev in _FUNC_PREV:
            return "func"
        if wl in self.kw:
            return "ctl" if (wl in _CTRL and not self.ci) else "kw"
        if wl in self.const:
            return "const"
        if wl in self.builtin:
            return "builtin"
        if wl in self.types:
            return "type"
        if self.cmdlet and self.cmdlet.match(w):
            return "func"
        nxt = code[end:end + 1]
        if nxt == "(":
            return "func"
        if self.key_word and nxt == ":":
            return "prop"
        if self.caps and len(w) > 1 and w[0].isupper():
            return "const" if w.isupper() else "type"
        return None

    def tokenize(self, code: str) -> List[Token]:
        out: List[Token] = []
        prev = ""
        for m in self.rx.finditer(code):
            kind, (s, e) = m.lastgroup, m.span()
            if kind == "word":
                tag = self._word(m.group(), code, e, prev)
                prev = m.group().lower()
            elif kind == "string":
                tag = "prop" if (self.key_colon and _COLON.match(code, e)) else "string"
            else:
                tag = kind
            if tag:
                out.append((s, e, tag))
        return out


# ------------------------------------------------------------------ languages
_PY_KW = ("and as assert async await break class continue def del elif else except finally for from "
          "global if import in is lambda nonlocal not or pass raise return try while with yield match case")
_PY_BUILTIN = ("print len range int str float list dict set tuple bool bytes open type isinstance issubclass "
               "super enumerate zip map filter sorted reversed sum min max abs round any all input repr id "
               "iter next getattr setattr hasattr format vars dir callable")
_JS_KW = ("break case catch class const continue debugger default delete do else export extends finally for "
          "from function if import in instanceof let new of return static super switch this throw try typeof "
          "var void while with yield async await get set as implements interface type enum namespace declare "
          "readonly public private protected abstract")
_JS_BUILTIN = ("console document window Math JSON Object Array String Number Boolean Promise Map Set Date "
               "RegExp Error parseInt parseFloat setTimeout setInterval fetch require module exports")

_LANGS: Dict[str, _Lang] = {
    "python": _Lang(_HASH, _PY_STR, _PY_KW, "True False None self cls", _PY_BUILTIN,
                    deco=r"@[A-Za-z_][\w.]*", word=r"[A-Za-z_]\w*"),
    "js": _Lang(_C_COMMENT, f"{_DQ}|{_SQ}|{_BT}", _JS_KW, "true false null undefined NaN Infinity",
                _JS_BUILTIN, "string number boolean any void never unknown object"),
    "json": _Lang(_NEVER, _DQ, "", "true false null", caps=False, key_colon=True, word=r"[A-Za-z_]\w*"),
    "java": _Lang(_C_COMMENT, f"{_DQ}|{_SQ}",
                  "abstract assert break case catch class continue default do else enum extends final finally "
                  "for if implements import instanceof interface native new package private protected public "
                  "return static strictfp super switch synchronized this throw throws transient try volatile "
                  "while var record sealed permits",
                  "true false null", types="int long double float boolean char byte short void",
                  deco=r"@[A-Za-z_][\w.]*"),
    "c": _Lang(_C_COMMENT, f"{_DQ}|{_SQ}",
               "alignas auto break case catch class const constexpr continue default delete do else enum "
               "explicit extern for friend goto if inline namespace new noexcept operator private protected "
               "public register return sizeof static struct switch template this throw try typedef typename "
               "union using virtual volatile while override final",
               "true false NULL nullptr TRUE FALSE",
               types="int long double float bool char short unsigned signed void size_t string vector map",
               deco=r"(?m:^[ \t]*#[ \t]*[a-z]+)", word=r"[A-Za-z_]\w*"),
    "cs": _Lang(_C_COMMENT, f"@?{_DQ}|{_SQ}",
                "abstract as base break case catch checked class const continue default delegate do else enum "
                "event explicit extern finally fixed for foreach goto if implicit in interface internal is lock "
                "namespace new operator out override params private protected public readonly ref return "
                "sealed sizeof stackalloc static struct switch this throw try typeof unchecked unsafe using "
                "virtual volatile while var async await record init get set yield partial",
                "true false null",
                types="int long double float bool char byte short decimal object string void uint ulong"),
    "go": _Lang(_C_COMMENT, f"{_DQ}|{_SQ}|{_BT}",
                "break case chan const continue default defer else fallthrough for func go goto if import "
                "interface map package range return select struct switch type var",
                "true false nil iota",
                "fmt len cap make new append copy delete panic recover print println",
                "string int int64 float64 bool byte rune error"),
    "rust": _Lang(_C_COMMENT, _RUST_STR,
                  "as async await break const continue crate dyn else enum extern fn for if impl in let loop "
                  "match mod move mut pub ref return self Self static struct super trait type unsafe use "
                  "where while",
                  "true false None Some Ok Err", "println print vec format panic Box Vec String Option Result",
                  "i8 i16 i32 i64 i128 isize u8 u16 u32 u64 u128 usize f32 f64 bool char str",
                  deco=r"#!?\[[^\]\n]*\]"),
    "sql": _Lang(r"--[^\n]*|/\*[\s\S]*?(?:\*/|$)", _SQL_STR,
                 "select from where and or not in is like between join inner left right outer full cross on "
                 "group by order having limit offset insert into values update set delete create alter drop "
                 "table index view database primary key foreign references unique default check constraint "
                 "as distinct union all case when then else end exists asc desc count sum avg min max if "
                 "begin commit rollback transaction with returning autoincrement pragma",
                 "true false null",
                 types="int integer text varchar char real float double boolean date datetime timestamp blob "
                       "numeric decimal serial bigint smallint",
                 ci=True, caps=False, word=r"[A-Za-z_]\w*"),
    "bash": _Lang(_HASH, f"{_DQ}|{_SQ}",
                  "if then else elif fi for while until do done case esac in function select return exit "
                  "break continue local export readonly declare unset source alias",
                  "", "echo cd ls pip python python3 git sudo apt npm node cat grep sed awk rm mkdir cp mv "
                      "chmod curl wget",
                  caps=False, dollar=True, word=_SHELL_WORD),
    "ps": _Lang(r"<#[\s\S]*?(?:#>|$)|" + _HASH, _PS_STR,
                "if else elseif switch foreach for while do until break continue function param return try "
                "catch finally throw begin process end in trap exit filter class using",
                "$true $false $null", ci=True, caps=False, dollar=True, word=_SHELL_WORD,
                cmdlet=r"(?i)^(?:Get|Set|New|Remove|Add|Clear|Copy|Move|Test|Write|Read|Invoke|Start|Stop|Out|"
                       r"Select|Where|ForEach|Sort|Import|Export|Install|Join|Split|Rename|Resolve|Format|"
                       r"ConvertTo|ConvertFrom|Measure|Compare|Enable|Disable|Restart)-\w+$"),
    "yaml": _Lang(_HASH, f"{_DQ}|{_SQ}", "", "true false null yes no on off True False Null",
                  caps=False, key_word=True, word=r"[A-Za-z_][\w.-]*"),
}

_ALIASES = {
    "py": "python", "python": "python", "python3": "python", "py3": "python",
    "js": "js", "javascript": "js", "jsx": "js", "ts": "js", "typescript": "js", "tsx": "js", "node": "js",
    "json": "json", "jsonc": "json",
    "java": "java", "kotlin": "java", "kt": "java",
    "c": "c", "cpp": "c", "c++": "c", "cc": "c", "h": "c", "hpp": "c",
    "cs": "cs", "csharp": "cs", "c#": "cs",
    "go": "go", "golang": "go", "rust": "rust", "rs": "rust",
    "sql": "sql", "sqlite": "sql", "mysql": "sql", "postgresql": "sql", "postgres": "sql",
    "bash": "bash", "sh": "bash", "shell": "bash", "zsh": "bash", "console": "bash",
    "ps1": "ps", "powershell": "ps", "pwsh": "ps", "bat": "ps", "cmd": "ps",
    "yaml": "yaml", "yml": "yaml",
    "html": "html", "xml": "html", "svg": "html", "htm": "html",
    "css": "css", "scss": "css",
}


# ------------------------------------------------------------------ HTML / CSS
_HTML_TOP = re.compile(r"(?P<comment><!--[\s\S]*?(?:-->|$))|(?P<tag></?[A-Za-z!][^>]*>?)")
_HTML_IN = re.compile(
    r"""(?P<string>"[^"]*"?|'[^']*'?)|(?P<attr>[A-Za-z_:@][\w:.@-]*(?=\s*=))|"""
    r"""(?P<name>^</?[!A-Za-z][\w:-]*)|(?P<punct>/?>)"""
)
_HTML_MAP = {"string": "string", "attr": "attr", "name": "tag", "punct": "tag"}


def _html(code: str) -> List[Token]:
    out: List[Token] = []
    for m in _HTML_TOP.finditer(code):
        if m.lastgroup == "comment":
            out.append((m.start(), m.end(), "comment"))
            continue
        base, text = m.start(), m.group()
        for t in _HTML_IN.finditer(text):
            out.append((base + t.start(), base + t.end(), _HTML_MAP[t.lastgroup]))
    return out


_CSS = re.compile("|".join([
    r"(?P<comment>/\*[\s\S]*?(?:\*/|$))",
    r"""(?P<string>"(?:\\.|[^"\\\n])*"?|'(?:\\.|[^'\\\n])*'?)""",
    r"(?P<deco>@[\w-]+)",
    r"(?P<const>#[0-9a-fA-F]{3,8}(?![\w-]))",
    r"(?P<cls>[.#][A-Za-z_][\w-]*)",
    r"(?P<number>(?<![\w-])-?\d*\.?\d+(?:px|r?em|%|vh|vw|ms|s|deg|fr|pt)?(?!\w))",
    r"(?P<prop>-{0,2}[A-Za-z][\w-]*(?=\s*:[^;{}]*[;}]))",
    r"(?P<func>[A-Za-z-][\w-]*(?=\())",
]))


def _css(code: str) -> List[Token]:
    return [(m.start(), m.end(), m.lastgroup) for m in _CSS.finditer(code)]


# ------------------------------------------------------------------ public API
def guess_lang(code: str) -> Optional[str]:
    s = code.lstrip()[:600]
    if re.match(r"<(!doctype|html|div|span|body|head|svg|\?xml)", s, re.I):
        return "html"
    if s[:1] in "[{" and re.search(r'"\s*:', s):
        return "json"
    if re.search(r"^\s*(def \w+|class \w+.*:|from \w+ import|import \w+$|print\()", code, re.M):
        return "python"
    if re.search(r"\b(function|const|let|console\.log)\b|=>", code):
        return "js"
    if re.search(r"\b(SELECT|INSERT INTO|CREATE TABLE|UPDATE)\b", code):
        return "sql"
    if re.search(r"^\s*(\$\w+\s*=|Get-|Set-|Write-Host)", code, re.M):
        return "ps"
    return None


def highlight(code: str, lang: str = "") -> List[Token]:
    """Returns [(start, end, tag)] character ranges for `code`."""
    if not code or len(code) > MAX_CHARS:
        return []
    key = _ALIASES.get((lang or "").strip().lower()) or guess_lang(code)
    if key == "html":
        return _html(code)
    if key == "css":
        return _css(code)
    spec = _LANGS.get(key or "")
    return spec.tokenize(code) if spec else []


def tk_ranges(code: str, tokens: List[Token]) -> Iterator[Tuple[str, str, str]]:
    """Converts char offsets to Tk 'line.col' indices (fast: no '1.0+Nc' scans)."""
    starts = [0] + [i + 1 for i, ch in enumerate(code) if ch == "\n"]

    def idx(pos: int) -> str:
        ln = bisect_right(starts, pos) - 1
        return f"{ln + 1}.{pos - starts[ln]}"

    for s, e, tag in tokens:
        yield tag, idx(s), idx(e)