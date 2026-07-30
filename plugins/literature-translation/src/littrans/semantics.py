from __future__ import annotations

import html
import re
import statistics
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from littrans.models import TableData

LIGATURES = str.maketrans({"ﬁ": "fi", "ﬂ": "fl", "ﬀ": "ff", "ﬃ": "ffi", "ﬄ": "ffl"})
MATH_FONT_MARKERS = (
    "math",
    "symbol",
    "mtsy",
    "rmti",
    "mtex",
    "cmmi",
    "cmsy",
    "cmex",
    "stix",
)
MATH_SIGNAL_RE = re.compile(r"[=∑∏∫√∂∇±≤≥∞≠≈∝⟨⟩ρτλσνεημχ′·×]")
TERMINAL_RE = re.compile(r"[.!?。！？:：;；][\"'”’）)\]]*$")
ZH_FIGURE_CAPTION_RE = re.compile(
    r"^\s*图\s*(?P<number>\d+(?:\s*[-–—]\s*\d+)*)\s*"
    r"(?:[。.．:：]+\s*)?(?P<title>\S(?:.*\S)?)\s*$",
    re.DOTALL,
)

LATEX_CHARS = {
    "∑": r"\sum ",
    "∏": r"\prod ",
    "∫": r"\int ",
    "√": r"\sqrt{}",
    "∂": r"\partial ",
    "∇": r"\nabla ",
    "±": r"\pm ",
    "≤": r"\le ",
    "≥": r"\ge ",
    "≠": r"\ne ",
    "≈": r"\approx ",
    "∝": r"\propto ",
    "∞": r"\infty ",
    "·": r"\cdot ",
    "×": r"\times ",
    "−": "-",
    "ρ": r"\rho ",
    "τ": r"\tau ",
    "λ": r"\lambda ",
    "σ": r"\sigma ",
    "ν": r"\nu ",
    "ε": r"\varepsilon ",
    "η": r"\eta ",
    "μ": r"\mu ",
    "χ": r"\chi ",
    "α": r"\alpha ",
    "β": r"\beta ",
    "γ": r"\gamma ",
    "δ": r"\delta ",
    "θ": r"\theta ",
    "κ": r"\kappa ",
    "π": r"\pi ",
    "ω": r"\omega ",
    "⟨": r"\langle ",
    "⟩": r"\rangle ",
    "′": "'",
}
SUPERSCRIPTS = str.maketrans("⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻", "0123456789+-")
SUBSCRIPTS = str.maketrans("₀₁₂₃₄₅₆₇₈₉₊₋", "0123456789+-")


def _line_text(line: dict[str, Any]) -> str:
    return "".join(str(span.get("text", "")) for span in line.get("spans", []))


def logical_lines(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Return ordered PDF lines while dropping duplicate overlay fragments."""
    lines = [line for line in block.get("lines", []) if _line_text(line).strip()]
    lines.sort(key=lambda line: (round(float(line["bbox"][1]), 1), float(line["bbox"][0])))
    kept: list[dict[str, Any]] = []
    for line in lines:
        x0, y0, x1, y1 = (float(value) for value in line["bbox"])
        width = max(x1 - x0, 0.1)
        duplicate = False
        for other in kept:
            ox0, oy0, ox1, oy1 = (float(value) for value in other["bbox"])
            vertical = min(y1, oy1) - max(y0, oy0)
            if vertical <= 0:
                continue
            overlap = max(0.0, min(x1, ox1) - max(x0, ox0))
            if overlap / width > 0.9 and width < (ox1 - ox0) * 0.35:
                duplicate = True
                break
        if not duplicate:
            kept.append(line)
    return kept


def normalize_prose(text: str) -> str:
    text = text.translate(LIGATURES).replace("\u00ad", "")
    # Some embedded book fonts expose list bullets and menu arrows as the C0
    # control U+0002.  Leaving it in the semantic text both corrupts Markdown
    # and looks like a one-character maths span to the formula detector.
    text = re.sub(r"(?m)^\x02[ \t]*", "• ", text)
    text = re.sub(r"[ \t]*\x02[ \t]*", " → ", text)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
    text = re.sub(r"(?<=[A-Za-z])-[ \t]*\n[ \t]*(?=[a-z])", "", text)
    text = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", text)
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    return _join_wrapped_lines(line for line in lines if line)


def normalize_zh_figure_caption(text: str) -> str:
    """Use one ASCII space between a Chinese figure number and its title."""
    match = ZH_FIGURE_CAPTION_RE.match(text)
    if not match:
        return text
    number = re.sub(r"\s+", "", match.group("number"))
    return f"图 {number} {match.group('title').strip()}"


def prose_from_block(block: dict[str, Any]) -> str:
    return normalize_prose("\n".join(_line_text(line) for line in logical_lines(block)))


def _join_wrapped_lines(lines: Iterable[str]) -> str:
    output = ""
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not output:
            output = line
        elif output.endswith("-") and re.match(r"^[a-z]", line):
            output = output[:-1] + line
        elif re.match(r"^[,.;:!?)}\]]", line):
            output += line
        else:
            output += " " + line
    return output.strip()


def code_from_block(block: dict[str, Any]) -> str:
    lines = logical_lines(block)
    if not lines:
        return ""
    x_origin = min(float(line["bbox"][0]) for line in lines)
    widths: list[float] = []
    for line in lines:
        text = _line_text(line)
        if text.strip():
            widths.append((float(line["bbox"][2]) - float(line["bbox"][0])) / len(text))
    char_width = statistics.median(widths) if widths else 5.0
    output: list[str] = []
    for line in lines:
        text = _line_text(line).translate(LIGATURES).replace("\u00ad", "").rstrip()
        if text and not text.startswith((" ", "\t")):
            inferred = max(0, round((float(line["bbox"][0]) - x_origin) / max(char_width, 1.0)))
            text = " " * inferred + text
        output.append(text)
    while output and not output[-1]:
        output.pop()
    return "\n".join(output)


def detect_code_language(text: str) -> str:
    stripped = text.lstrip()
    if re.search(r"<\/?[A-Za-z][^>]*>", text) and (
        "xmlns" in text or "x:Class" in text or re.search(r"<(Grid|Window|Button)\b", text)
    ):
        return "xaml"
    if stripped.startswith("<") and re.search(r"<\/?[A-Za-z][^>]*>", text):
        return "xml"
    if re.search(r"\b(namespace|using|public|private|protected|internal|class)\b", text) and re.search(
        r"[;{}]", text
    ):
        return "csharp"
    if re.search(r"^\s*(def|class|from|import)\s+", text, re.MULTILINE):
        return "python"
    if re.search(r"^\s*(SELECT|INSERT|UPDATE|CREATE)\b", text, re.I | re.MULTILINE):
        return "sql"
    if re.search(r"^\s*(Get-|Set-|New-|\$[A-Za-z_])", text, re.MULTILINE):
        return "powershell"
    if stripped.startswith(("{", "[")) and re.search(r'"[^"\n]+"\s*:', text):
        return "json"
    return "text"


def unicode_math_to_latex(text: str) -> str:
    result: list[str] = []
    superscript: list[str] = []
    subscript: list[str] = []

    def flush_scripts() -> None:
        if superscript:
            result.append("^{" + "".join(superscript) + "}")
            superscript.clear()
        if subscript:
            result.append("_{" + "".join(subscript) + "}")
            subscript.clear()

    for char in text.translate(LIGATURES):
        if char in "⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻":
            superscript.append(char.translate(SUPERSCRIPTS))
            continue
        if char in "₀₁₂₃₄₅₆₇₈₉₊₋":
            subscript.append(char.translate(SUBSCRIPTS))
            continue
        flush_scripts()
        result.append(LATEX_CHARS.get(char, char))
    flush_scripts()
    value = "".join(result).replace("\n", " ")
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"\s+([,.;:)])", r"\1", value)
    value = re.sub(r"([(])\s+", r"\1", value)
    return value


def _span_is_strong_math(span: dict[str, Any]) -> bool:
    font = str(span.get("font", "")).casefold()
    text = str(span.get("text", ""))
    return any(marker in font for marker in MATH_FONT_MARKERS) or (
        bool(MATH_SIGNAL_RE.search(text)) and len(text.split()) <= 8
    )


def _plain_inline_math(text: str) -> str:
    polynomial = re.search(
        r"(?<![A-Za-z0-9])"
        r"([A-Za-zρτλσνεημχ](?:\^[-+]?\d+)?"
        r"(?:\s*[+\-*/]\s*[A-Za-z0-9ρτλσνεημχ](?:\^[-+]?\d+)?)+"
        r"\s*=\s*[A-Za-z0-9ρτλσνεημχ](?:\^[-+]?\d+)?)",
        text,
    )
    if polynomial:
        return (
            text[: polynomial.start()]
            + "$"
            + unicode_math_to_latex(polynomial.group(1))
            + "$"
            + text[polynomial.end() :]
        )
    pattern = re.compile(
        r"(?<![A-Za-z0-9])([A-Za-zρτλσνεημχ∇][A-Za-z0-9ρτλσνεημχ∇′_{}]*)"
        r"\s*(=|≤|≥|≈|∝)\s*"
        r"([A-Za-z0-9ρτλσνεημχ∇′_{}()+\-*/^. ]{1,48})"
    )
    match = pattern.search(text)
    if not match:
        return text
    right = re.split(
        r"\s+(?:where|when|which|and|or|is|are|in|for|with)\b",
        match.group(3),
        maxsplit=1,
    )[0]
    expression = f"{match.group(1)} {match.group(2)} {right.strip()}"
    return (
        text[: match.start()]
        + "$"
        + unicode_math_to_latex(expression)
        + "$"
        + match.group(3)[len(right) :]
        + text[match.end() :]
    )


def inline_math_markdown(block: dict[str, Any], prose_text: str) -> str:
    """Produce conservative inline LaTeX; uncertain blocks remain review-gated."""
    lines = logical_lines(block)
    spans = [span for line in lines for span in line.get("spans", [])]
    if not any(_span_is_strong_math(span) for span in spans):
        return _plain_inline_math(prose_text) if MATH_SIGNAL_RE.search(prose_text) else prose_text
    sizes = [float(span.get("size", 0)) for span in spans if str(span.get("text", "")).strip()]
    base_size = statistics.median(sizes) if sizes else 10.0
    output: list[str] = []
    math: list[tuple[str, str]] = []

    def flush_math() -> None:
        if not math:
            return
        rendered: list[str] = []
        scripts: list[str] = []
        for text, script in math:
            latex = unicode_math_to_latex(text.strip())
            if not latex:
                continue
            if script:
                scripts.append(latex)
            else:
                if scripts:
                    rendered.append("^{" + "".join(scripts) + "}")
                    scripts.clear()
                rendered.append(latex)
        if scripts:
            rendered.append("^{" + "".join(scripts) + "}")
        if rendered:
            prefix = " " if math[0][0][:1].isspace() else ""
            suffix = " " if math[-1][0][-1:].isspace() else ""
            latex = re.sub(r"\s+", " ", " ".join(rendered)).strip()
            latex = re.sub(r"\s+([,.;:)])", r"\1", latex)
            latex = re.sub(r"([(])\s+", r"\1", latex)
            output.append(prefix + "$" + latex + "$" + suffix)
        math.clear()

    for line_index, line in enumerate(lines):
        for span in line.get("spans", []):
            text = str(span.get("text", "")).translate(LIGATURES).replace("\u00ad", "")
            font = str(span.get("font", "")).casefold()
            stripped = text.strip()
            short_italic = "italic" in font and bool(
                re.fullmatch(r"[A-Za-z]{1,3}", stripped)
            )
            small = float(span.get("size", base_size)) < base_size * 0.82
            is_math = _span_is_strong_math(span) or short_italic or (small and bool(stripped))
            if is_math:
                script = "^" if small else ""
                math.append((text, script))
            else:
                flush_math()
                output.append(text)
        flush_math()
        if line_index + 1 < len(lines):
            output.append("\n")
    flush_math()
    candidate = normalize_prose("".join(output))
    return candidate if "$" in candidate else prose_text


def table_from_rows(
    rows: Sequence[Sequence[str | None]], header_rows: int = 1
) -> TableData | None:
    cleaned = [[normalize_prose(str(cell or "")) for cell in row] for row in rows]
    cleaned = [row for row in cleaned if any(cell for cell in row)]
    if not cleaned:
        return None
    width = max(len(row) for row in cleaned)
    padded = [row + [""] * (width - len(row)) for row in cleaned]
    return TableData(rows=padded, header_rows=min(header_rows, len(padded)), column_count=width)


def looks_like_continuation(previous: str, current: str) -> bool:
    previous = previous.rstrip()
    current = current.lstrip()
    if not previous or not current or TERMINAL_RE.search(previous):
        return False
    return bool(re.match(r"^(?:[a-z]|and\b|or\b|but\b|which\b|that\b|with\b)", current))


def escape_markdown_prose(text: str) -> str:
    """Escape raw HTML without touching LaTeX delimiters or backslashes."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fenced_code(text: str, language: str | None) -> str:
    longest = max((len(match.group(0)) for match in re.finditer(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{language or 'text'}\n{text}\n{fence}"


def table_to_markdown(table: TableData) -> str:
    def cell(value: str) -> str:
        return escape_markdown_prose(value).replace("|", r"\|").replace("\n", "<br>")

    rows = table.rows
    header_count = max(1, table.header_rows)
    header = [" / ".join(filter(None, (rows[index][col] for index in range(header_count)))) for col in range(table.column_count)]
    body = rows[header_count:]
    lines = ["| " + " | ".join(cell(value) for value in header) + " |"]
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    lines.extend("| " + " | ".join(cell(value) for value in row) + " |" for row in body)
    return "\n".join(lines)


def table_to_html(
    table: TableData, render_cell: Callable[[str], str] = html.escape
) -> str:
    header_count = max(1, table.header_rows)
    head = [
        " / ".join(filter(None, (table.rows[index][col] for index in range(header_count))))
        for col in range(table.column_count)
    ]
    parts = ["<table><thead><tr>"]
    parts.extend(f"<th>{render_cell(value)}</th>" for value in head)
    parts.append("</tr></thead><tbody>")
    for row in table.rows[header_count:]:
        parts.append("<tr>")
        parts.extend(f"<td>{render_cell(value)}</td>" for value in row)
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)
