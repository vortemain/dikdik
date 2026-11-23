"""LaTeX to Office Math (OMML): real, editable equations in Word documents, stdlib only.

Covers what explanations, theses and lessons use: fractions, powers and indices, roots,
sums/products/integrals with limits, accents (vectors, hats, bars), Greek letters and
operators, \\text/\\mathrm/\\mathbf, \\left…\\right delimiters, matrices and aligned lines
(each line of an aligned block becomes its own equation). Anything unknown is kept as
plain text inside the equation rather than dropped.
"""

from __future__ import annotations

import re
from xml.sax.saxutils import escape

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"

SYMBOLS = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ", "pi": "π",
    "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "ϕ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    "times": "×", "cdot": "⋅", "div": "÷", "pm": "±", "mp": "∓", "ast": "∗", "star": "⋆", "circ": "∘", "bullet": "∙",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠", "approx": "≈", "sim": "∼", "simeq": "≃",
    "equiv": "≡", "propto": "∝", "ll": "≪", "gg": "≫", "in": "∈", "notin": "∉", "ni": "∋", "subset": "⊂", "supset": "⊃",
    "subseteq": "⊆", "supseteq": "⊇", "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅", "forall": "∀",
    "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨", "wedge": "∧", "vee": "∨", "infty": "∞", "partial": "∂",
    "nabla": "∇", "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ", "degree": "°", "angle": "∠",
    "perp": "⊥", "parallel": "∥", "therefore": "∴", "because": "∵", "to": "→", "rightarrow": "→", "leftarrow": "←",
    "Rightarrow": "⇒", "Leftarrow": "⇐", "leftrightarrow": "↔", "Leftrightarrow": "⇔", "implies": "⟹", "iff": "⟺",
    "mapsto": "↦", "uparrow": "↑", "downarrow": "↓", "rightleftharpoons": "⇌", "ldots": "…", "cdots": "⋯",
    "vdots": "⋮", "ddots": "⋱", "dots": "…", "prime": "′", "langle": "⟨", "rangle": "⟩", "lfloor": "⌊", "rfloor": "⌋",
    "lceil": "⌈", "rceil": "⌉", "{": "{", "}": "}", "%": "%", "$": "$", "&": "&", "#": "#", "_": "_", "|": "‖",
    "mid": "∣", "setminus": "∖", "oplus": "⊕", "otimes": "⊗", "dagger": "†",
}
FUNCTIONS = {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "log",
             "ln", "lg", "exp", "lim", "limsup", "liminf", "max", "min", "sup", "inf", "det", "dim", "ker", "gcd",
             "deg", "arg", "Pr", "mod"}
NARY = {"sum": "∑", "prod": "∏", "coprod": "∐", "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮",
        "bigcup": "⋃", "bigcap": "⋂"}
ACCENTS = {"hat": "\u0302", "widehat": "\u0302", "tilde": "\u0303", "widetilde": "\u0303", "vec": "\u20d7",
           "dot": "\u0307", "ddot": "\u0308", "check": "\u030c", "acute": "\u0301", "grave": "\u0300", "breve": "\u0306"}
SPACES = {",": "\u2009", ":": "\u205f", ";": "\u2004", "quad": "\u2003", "qquad": "\u2003\u2003", " ": " ", "!": ""}
BLACKBOARD = {"R": "ℝ", "N": "ℕ", "Z": "ℤ", "Q": "ℚ", "C": "ℂ", "P": "ℙ", "E": "𝔼"}
_TOKEN = re.compile(r"\\[A-Za-z]+\*?|\\.|[{}^_&]|\s+|.", re.S)


def _run(text: str, plain: bool = False, bold: bool = False) -> str:
    if not text:
        return ""
    style = '<m:rPr><m:sty m:val="p"/></m:rPr>' if plain and not bold else '<m:rPr><m:sty m:val="b"/></m:rPr>' if bold else ""
    return (f'<m:r>{style}<w:rPr><w:rFonts w:ascii="Cambria Math" w:hAnsi="Cambria Math"/></w:rPr>'
            f'<m:t xml:space="preserve">{escape(text)}</m:t></m:r>')


class _Parser:
    def __init__(self, tex: str):
        # Sizing commands only (\left( \right) \bigl …), never \rightarrow or \leftarrow.
        tex = re.sub(r"\\(?:left|right)\.", "", tex)  # \left. and \right. are invisible delimiters
        cleaned = re.sub(r"\\(?:left|right|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr|big|Big|bigg|Bigg|displaystyle|textstyle|limits|nolimits)(?![A-Za-z])",
                         "", tex)
        self.tokens = list(_TOKEN.findall(cleaned))
        self.pos = 0
        self.plain = False  # upright text, inside \mathrm{…}

    def peek(self) -> str | None:
        while self.pos < len(self.tokens) and self.tokens[self.pos].isspace():
            self.pos += 1
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def take(self) -> str | None:
        token = self.peek()
        if token is not None:
            self.pos += 1
        return token

    def group(self) -> str:
        """The next argument: a {…} group or a single atom, as OMML."""
        token = self.peek()
        if token == "{":
            self.take()
            return self.sequence(stop="}")
        return self.atom() or ""

    def raw_group(self) -> str:
        """The next {…} argument as raw text (for \\text and friends)."""
        if self.peek() != "{":
            return self.take() or ""
        self.take()
        depth, out = 1, []
        while self.pos < len(self.tokens):
            token = self.tokens[self.pos]
            self.pos += 1
            if token == "{":
                depth += 1
            elif token == "}":
                depth -= 1
                if depth == 0:
                    break
            out.append(token[1:] if token.startswith("\\") and len(token) == 2 else token)
        return "".join(out)

    def scripts(self, base: str) -> str:
        sub = sup = None
        while self.peek() in ("^", "_"):
            mark = self.take()
            value = self.group()
            if mark == "^":
                sup = value
            else:
                sub = value
        if sub is not None and sup is not None:
            return f"<m:sSubSup><m:e>{base}</m:e><m:sub>{sub}</m:sub><m:sup>{sup}</m:sup></m:sSubSup>"
        if sup is not None:
            return f"<m:sSup><m:e>{base}</m:e><m:sup>{sup}</m:sup></m:sSup>"
        if sub is not None:
            return f"<m:sSub><m:e>{base}</m:e><m:sub>{sub}</m:sub></m:sSub>"
        return base

    def atom(self) -> str:
        token = self.take()
        if token is None or token == "}":
            return ""
        if token == "{":
            return self.sequence(stop="}")
        if token in ("^", "_"):  # a script with no base
            self.pos -= 1
            return self.scripts(_run("\u200b"))
        if not token.startswith("\\"):
            return _run(token if token != "'" else "′", plain=self.plain)
        name = token[1:]
        if name in ("frac", "dfrac", "tfrac", "cfrac"):
            numerator, denominator = self.group(), self.group()
            return f"<m:f><m:num>{numerator}</m:num><m:den>{denominator}</m:den></m:f>"
        if name == "binom":
            top, bottom = self.group(), self.group()
            return (f'<m:d><m:dPr><m:begChr m:val="("/><m:endChr m:val=")"/></m:dPr><m:e><m:f><m:fPr><m:type m:val="noBar"/></m:fPr>'
                    f"<m:num>{top}</m:num><m:den>{bottom}</m:den></m:f></m:e></m:d>")
        if name == "sqrt":
            degree = ""
            if self.peek() == "[":
                self.take()
                parts = []
                while self.peek() not in (None, "]"):
                    parts.append(self.atom())
                self.take()
                degree = "".join(parts)
            body = self.group()
            hide = "" if degree else '<m:radPr><m:degHide m:val="1"/></m:radPr>'
            return f"<m:rad>{hide}<m:deg>{degree}</m:deg><m:e>{body}</m:e></m:rad>"
        if name in NARY:
            sub = sup = ""
            while self.peek() in ("^", "_"):
                mark = self.take()
                value = self.group()
                if mark == "^":
                    sup = value
                else:
                    sub = value
            operand = self.atom_with_scripts() if self.peek() not in (None, "}", "&") else ""
            hide = ("" if sub else '<m:subHide m:val="1"/>') + ("" if sup else '<m:supHide m:val="1"/>')
            limits = '<m:limLoc m:val="undOvr"/>' if name not in ("int", "iint", "iiint", "oint") else '<m:limLoc m:val="subSup"/>'
            return (f'<m:nary><m:naryPr><m:chr m:val="{NARY[name]}"/>{limits}{hide}</m:naryPr>'
                    f"<m:sub>{sub}</m:sub><m:sup>{sup}</m:sup><m:e>{operand}</m:e></m:nary>")
        if name in ACCENTS:
            return f'<m:acc><m:accPr><m:chr m:val="{ACCENTS[name]}"/></m:accPr><m:e>{self.group()}</m:e></m:acc>'
        if name in ("bar", "overline"):
            return f'<m:bar><m:barPr><m:pos m:val="top"/></m:barPr><m:e>{self.group()}</m:e></m:bar>'
        if name == "underline":
            return f'<m:bar><m:barPr><m:pos m:val="bot"/></m:barPr><m:e>{self.group()}</m:e></m:bar>'
        if name in ("text", "textrm", "mathrm", "operatorname", "textit", "mbox", "textnormal", "mathit", "mathsf", "mathtt"):
            text = self.raw_group()
            if name in ("mathrm", "operatorname") and re.search(r"[\^_]", text):
                inner = _Parser(text)  # \mathrm{H_2O}: upright letters with real subscripts
                inner.plain = True
                return inner.sequence()
            return _run(text, plain=name != "mathit" and name != "textit")
        if name in ("mathbf", "textbf", "boldsymbol", "bm"):
            return _run(self.raw_group(), bold=True)
        if name == "mathbb":
            text = self.raw_group()
            return _run("".join(BLACKBOARD.get(char, char) for char in text))
        if name in ("mathcal", "mathscr", "mathfrak"):
            return _run(self.raw_group())
        if name in ("begin", "end"):
            self.raw_group()
            return ""
        if name == "\\":
            return "\n"
        if name in SPACES:
            return _run(SPACES[name]) if SPACES[name] else ""
        if name in ("lim", "limsup", "liminf", "max", "min", "sup", "inf", "argmax", "argmin") and self.peek() == "_":
            self.take()
            below = self.group()  # lim_{x\u21920}: the limit goes underneath, as in textbooks
            label = {"limsup": "lim sup", "liminf": "lim inf", "argmax": "arg max", "argmin": "arg min"}.get(name, name)
            return f"<m:limLow><m:e>{_run(label, plain=True)}</m:e><m:lim>{below}</m:lim></m:limLow>" + _run("\u2061")
        if name in FUNCTIONS:
            return _run(name, plain=True) + _run("\u2061")
        if name in SYMBOLS:
            return _run(SYMBOLS[name])
        if name in ("R", "N", "Z", "Q", "C"):
            return _run(BLACKBOARD[name])
        return _run(name, plain=True)  # unknown command: keep its name visible

    def atom_with_scripts(self) -> str:
        return self.scripts(self.atom())

    def sequence(self, stop: str | None = None) -> str:
        out = []
        while True:
            token = self.peek()
            if token is None or token == stop:
                if token == stop:
                    self.take()
                break
            if token == "&":
                self.take()
                continue  # alignment points: Word aligns each line itself
            out.append(self.atom_with_scripts())
        return "".join(out)


def _lines(tex: str) -> list[str]:
    body = re.sub(r"\\begin\{(aligned|align\*?|gather\*?|equation\*?|split|multline\*?|eqnarray\*?|alignat\*?)\}(\{\d+\})?", "", tex)
    body = re.sub(r"\\end\{(aligned|align\*?|gather\*?|equation\*?|split|multline\*?|eqnarray\*?|alignat\*?)\}", "", body)
    body = re.sub(r"\\(label|tag|nonumber|notag)(\{[^{}]*\})?", "", body)
    if re.search(r"\\begin\{[pbvBV]?matrix\}|\\begin\{cases\}|\\begin\{array\}", body):
        return [body]
    return [line for line in re.split(r"\\\\(?:\[[^\[\]]*\])?", body) if line.strip()] or [body]


def _matrices(tex: str) -> str:
    """Matrices and cases as OMML matrices inside delimiters."""
    def build(match: re.Match) -> str:
        kind, content = match.group(1), match.group(2)
        rows = [row for row in re.split(r"\\\\", content) if row.strip()]
        grid = "".join("<m:mr>" + "".join(f"<m:e>{_Parser(cell).sequence()}</m:e>" for cell in row.split("&")) + "</m:mr>" for row in rows)
        matrix = f"<m:m>{grid}</m:m>"
        delimiters = {"pmatrix": ("(", ")"), "bmatrix": ("[", "]"), "Bmatrix": ("{", "}"), "vmatrix": ("|", "|"),
                      "Vmatrix": ("‖", "‖"), "cases": ("{", "")}.get(kind)
        if not delimiters:
            _SLOTS.append(matrix)
            return f"\x00{len(_SLOTS) - 1}\x00"
        open_, close = delimiters
        wrapped = (f'<m:d><m:dPr><m:begChr m:val="{escape(open_)}"/><m:endChr m:val="{escape(close)}"/></m:dPr>'
                   f"<m:e>{matrix}</m:e></m:d>")
        _SLOTS.append(wrapped)
        return f"\x00{len(_SLOTS) - 1}\x00"
    # A scan for each \\end{...} instead of a lazy .*? across the whole equation (quadratic on crafted input).
    out, position = [], 0
    for opening in re.finditer(r"\\begin\{(matrix|pmatrix|bmatrix|Bmatrix|vmatrix|Vmatrix|cases|array)\}(?:\{[^{}]*\})?", tex):
        if opening.start() < position:
            continue
        closing = tex.find("\\end{" + opening.group(1) + "}", opening.end())
        if closing < 0:
            continue
        out.append(tex[position:opening.start()])
        out.append(build(_Span(opening.group(1), tex[opening.end():closing])))
        position = closing + len("\\end{" + opening.group(1) + "}")
    out.append(tex[position:])
    return "".join(out)


class _Span:
    """The two groups _matrices' builder reads, like a re.Match."""

    def __init__(self, kind: str, content: str):
        self._groups = (kind, content)

    def group(self, index: int) -> str:
        return self._groups[index - 1]


_SLOTS: list[str] = []


def to_omml(tex: str) -> list[str]:
    """One <m:oMath> per equation line."""
    _SLOTS.clear()
    tex = _matrices(tex.strip())
    out = []
    for line in _lines(tex):
        pieces = re.split(r"\x00(\d+)\x00", line)
        xml = []
        for index, piece in enumerate(pieces):
            if index % 2:
                xml.append(_SLOTS[int(piece)])
            elif piece.strip():
                xml.append(_Parser(piece).sequence())
        out.append(f"<m:oMath>{''.join(xml)}</m:oMath>")
    return out


def inline(tex: str) -> str:
    """An inline equation inside a paragraph."""
    return "".join(to_omml(tex)[:1])


def display(tex: str) -> str:
    """A display equation: its own centred paragraph (one per aligned line)."""
    return "".join(f'<w:p><w:pPr><w:jc w:val="center"/></w:pPr><m:oMathPara>{math}</m:oMathPara></w:p>' for math in to_omml(tex))
