"""Recover book structure from line geometry after preserving native glyphs."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from littrans.fidelity_models import FidelityAsset
from littrans.models import SourceUnit

# Bold faces: style names and the TeX families (CMBX, CMB, SFBX, SFBI, ECBX, CMSSBX ...).
BOLD_FONT = re.compile(r"bx|bold|heavy|black|semibold|demi|(?<![a-z])(?:cm|sf|ec|ae|lm|tc)(?:ss|tt)?b(?:i|x)?(?:ti|sl)?\d", re.I)
# Text italic/slanted faces: PostScript/OpenType style names and the TeX families
# (CMTI, CMSL, CMBXTI, SFTI, SFBI, ECTI ...). Math italic (CMMI) is notation, not emphasis.
ITALIC_FONT = re.compile(r"ital|oblique|slant|(?<![a-z])(?:cm|sf|ec|ae|lm|tc)(?:bx|b|ss|tt)?(?:ti|sl|it|bi|ri)\d", re.I)
# Small-capital faces: the TeX families (CMCSC, SFCC, ECCC) and style names.
SMALL_CAPS_FONT = re.compile(r"csc\d|small.?caps|(?<![a-z])(?:sf|ec|tc|lm)cc\d", re.I)
# A caption's printed label closed by punctuation ("Figure 3.", "Fig. 2.1:", "Table IV."), as
# opposed to a sentence that mentions the figure ("Figure 3 shows ...").
CLOSED_CAPTION_LABEL = re.compile(r"[*_\s]*[A-Z][A-Za-z]*\.?\s*[A-Z0-9][\w.\-]*?[.:](?:\s|\*|$)")
FORMAT_CONTROLS = {chr(9), chr(10), chr(13)}
MATH_FONT = re.compile(r"cmmi|cmsy|cmex|msam|msbm|math|symbol|stix|cm[a-z]*sy", re.I)
# Upright operator names TeX sets in the text face; they are notation, never prose words
# or formula conditions. English words that double as operators (mean, area, mod) are
# recognised only when set flush against their argument's opening bracket.
MATH_OPERATORS = {
    "sin", "cos", "tan", "cot", "sec", "csc", "sinh", "cosh", "tanh", "arcsin", "arccos", "arctan",
    "log", "ln", "lg", "exp", "lim", "limsup", "liminf", "sup", "inf", "max", "min", "argmax", "argmin",
    "det", "rank", "diag", "span", "arg", "dim", "ker", "poly", "tr", "cov", "var", "corr", "prob",
    "sgn", "gcd", "lcm", "supp", "ess", "vol", "mod",
}
# A token of native language: a word of two or more letters, or a letter-dot abbreviation
# (i.o., a.s., i.e.) that a word pattern would split into single letters.
LANGUAGE_TOKEN = re.compile(r"[A-Za-z](?:\.[A-Za-z])+\.?|[A-Za-z]{2,}")
# Printed equation labels: (1), (2.3a), (A.4); named tags such as (ODE), (SDE); starred (*).
EQUATION_LABEL = r"\(((?:[A-Z]\.)?\d+(?:\.\d+)*(?:[a-z])?|[A-Z]{2,6}\d?|\*{1,3})\)"
# Printed list labels: a bracketed clause marker ((a), (iv), (2)) or a closed number set
# in the margin of an exercise or enumerated item (1., 1.11., 3.2.1., 2)). A bare "1.1"
# without closing punctuation is as likely a section reference wrapped onto a new line.
LIST_LABEL = re.compile(r"\((?:[a-z]|[ivxlcdm]+|\d+)\)|\d{1,3}(?:\.\d{1,3})*[.)]", re.I)
# The same label opening a unit's text; a number must be followed by a space (after any
# emphasis marker) so "1.5" inside a formula fragment never counts.
LIST_LABEL_START = re.compile(
    r"[*\s]*(?:\((?:[a-z]|[ivxlcdm]+|\d+)\)|\d{1,3}(?:\.\d{1,3})*[.)](?=\**\s))", re.I
)
# How far right of the margin a list item's text column may sit: a label such as
# ``(iii)`` or ``12.`` and the gap after it. A row starting beyond it is a display.
LIST_TEXT_COLUMN_EM = 4.5
# A line closing a sentence: terminal punctuation, then any closing quotes or brackets.
TERMINAL_PUNCTUATION = re.compile(r"[.!?:;。！？：；][”’\"')\]）】〕]*$")
# Unit kinds that never join prose, nor prose them.
NOTE_KINDS = {"footnote", "bibliography"}


def language_words(text: str) -> list[str]:
    """Language tokens of ``text`` that are not operator names."""
    return [token for token in LANGUAGE_TOKEN.findall(text) if token.lower() not in MATH_OPERATORS]


def font_style(font: str) -> str:
    """Markdown emphasis markers for a text face: bold, italic, or both."""
    bold = bool(BOLD_FONT.search(font))
    italic = bool(ITALIC_FONT.search(font))
    return "***" if bold and italic else "**" if bold else "*" if italic else ""


def _bold_run_in(glyphs: list[dict[str, Any]], previous: list[dict[str, Any]]) -> bool:
    """A line opening with a bold label ("EXAMPLE 1.", "Proof.", "2.1.4. Stochastic
    processes.") followed by ordinary text on the same line. A bold phrase that
    merely wraps from the previous line ("modeling / problems:") is not a label."""
    previous_ink = [g for g in previous if inked_glyph(g)]
    if previous_ink and BOLD_FONT.search(previous_ink[-1]["font"]):
        return False
    letters, closed = 0, False
    for g in glyphs:
        text = str(g["text"])
        if not text.strip():
            continue
        bold = bool(BOLD_FONT.search(g["font"]))
        if closed:
            return not bold and letters >= 3
        if not bold:
            return False
        if text.isalpha():
            letters += len(text)
        elif text == ".":
            closed = bool(letters)
        elif not (text.isdigit() or text in "-:,()"):
            return False
    return False


def inked_glyph(glyph: dict[str, Any]) -> bool:
    """Whether a native glyph prints ink. Large TeX operators (CMEX braces, sums,
    integrals) decode to control characters, which Python counts as whitespace; in a
    mathematical face even CR (the CMEX integral) is a glyph, not a line format."""
    text = str(glyph["text"])
    if text.strip():
        return True
    controls = [c for c in text if ord(c) < 32]
    if not controls:
        return False
    if MATH_FONT.search(str(glyph.get("font", ""))):
        return True
    return any(c not in FORMAT_CONTROLS for c in controls)


def _contains(g: dict[str, Any], box: Sequence[float]) -> bool:
    b = g["bbox"]
    return bool(box[0] <= (b[0] + b[2]) / 2 <= box[2] and box[1] <= (b[1] + b[3]) / 2 <= box[3])


TITLE_LABELS = {"doc_title", "paragraph_title", "title"}
# Bullet glyphs; base-14 fonts report the bullet as a middle dot, which is a list
# marker only when it opens a line and is followed by a spaced word.
LIST_BULLETS = set("\u2022\u25e6\u25aa\u25a0\u25cf\u00b7")


def is_bullet_line(inked: list[dict[str, Any]]) -> bool:
    """A list item line: a bullet glyph opening the line, then spaced content."""
    if len(inked) < 2 or inked[0]["text"] not in LIST_BULLETS:
        return False
    following = str(inked[1]["text"])[:1]
    return bool(following) and (following.isalnum() or (inked[0]["text"] != "\u00b7" and following not in LIST_BULLETS))


def list_label(line: list[dict[str, Any]], font_size: float) -> tuple[str, float] | None:
    """The printed label opening a line ("1.11.", "(b)") and the x where its text starts.

    The label is the leading run of text-face glyphs up to the first space or gap; a run
    with no text after it on the line ("1.32." wrapped alone to the margin) is not a label.
    """
    glyphs = [g for g in line if inked_glyph(g) or str(g["text"]).isspace()]
    text, last, rest = "", None, len(glyphs)
    for index, g in enumerate(glyphs):
        if str(g["text"]).isspace() or (last is not None and g["origin"][0] - last["bbox"][2] > 0.2 * font_size):
            rest = index
            break
        if MATH_FONT.search(str(g.get("font", ""))):
            return None
        text += str(g["text"])
        last = g
    if not LIST_LABEL.fullmatch(text):
        return None
    body = next((g for g in glyphs[rest:] if inked_glyph(g)), None)
    return (text, float(body["origin"][0])) if body else None


STATEMENT_NAMES = (
    "Theorem|Lemma|Proposition|Definition|Corollary|Claim|Example|Remark|Notation|Exercise|"
    "Assumption|Conjecture|Problem|Warning|Hypothesis|Axiom|Fact|Observation|Convention"
)
# The classic capitalised label, or a bold/small-caps run-in label that may carry
# qualifiers ("IMPORTANT REMARK.", "**Example 2.**", "WARNING ABOUT NOTATION.").
STATEMENT_START = re.compile(r"\**(?:Theorem|Lemma|Proposition|Definition|Corollary|Claim)\b")
STATEMENT_RUN_IN = re.compile(rf"\*{{2,3}}(?:[A-Za-z0-9.]+\s+){{0,3}}(?:{STATEMENT_NAMES})s?\b", re.I)
STATEMENT_CAPS = re.compile(rf"(?:[A-Z]+\s+){{0,3}}(?:{STATEMENT_NAMES.upper()})S?\b")


RUN_IN_LABEL = re.compile(r"\*{2,3}[^*]*[A-Za-z]{3,}[^*]*\*{2,3}")


def _starts_statement(text: str) -> bool:
    return bool(STATEMENT_START.match(text) or STATEMENT_RUN_IN.match(text) or STATEMENT_CAPS.match(text))


def _emphasised_share(text: str) -> float:
    """The share of a unit's letters set in the italic face (single-star emphasis), the bold
    run-in label left out: a theorem body set in italic reads near 1, upright prose near 0."""
    body = re.sub(r"\*{2,3}[^*]+\*{2,3}", " ", text)
    body = re.sub(r"\{\{asset:[^}]+\}\}", " ", body)
    italic = sum(len(re.findall(r"[^\W\d_]", run)) for run in re.findall(r"\*([^*]+)\*", body))
    letters = len(re.findall(r"[^\W\d_]", body))
    return italic / letters if letters else 0.0


# The statement body face is decided once, when the statement opens.
STATEMENT_ITALIC_SHARE = 0.6


def _mark_paragraph_breaks(
    split: list[dict[str, Any]], gm: dict[str, dict[str, Any]], omitted: dict[str, str],
    markers: dict[str, dict[str, Any]], display_blocks: set[str], margin: float, font_size: float,
    gap_threshold: float, list_items: dict[str, dict[str, Any]],
) -> None:
    """Flag each chunk that opens after paragraph white space.

    A document that spaces its paragraphs instead of indenting them shows a paragraph only
    as vertical white space, inside a PDF block or between two blocks. White space alone is
    not a break: a line a tall inline formula pushed down keeps its paragraph together. The
    line above must also close — end in terminal punctuation or stop short of the running
    text's right edge — and be prose: a formula row (``∫ X dP``, a limit, a label) says
    nothing about where a paragraph ends, so a flush "provided ..." clause after a display
    stays in its paragraph. A text line starts in a text column: the margin, a paragraph
    indent or the text column of a list item (its label plus the gap after it, within about
    four and a half ems), and a chunk the planner placed as a labelled item or its
    continuation (``list_items``) is text wherever its column sits, so the paragraph that
    follows a list opens after the item's last line. The flag lives on the chunk, which the
    ledger never records, so a page without a break keeps a ledger identical to one prepared
    before breaks were recognised.
    """
    def inked(line: list[str]) -> list[dict[str, Any]]:
        return [gm[gid] for gid in line if gid not in markers and inked_glyph(gm[gid])]

    def prose(glyphs: list[dict[str, Any]]) -> bool:
        # Mostly letters of language words in a text face; ``dP`` in a math face, an
        # operator name or a lone variable is notation.
        text = "".join(
            str(g["text"]) if str(g["text"]).isalpha() and not MATH_FONT.search(str(g.get("font", ""))) else " "
            for g in glyphs
        )
        return sum(len(word) for word in language_words(text)) >= 0.5 * len(glyphs)

    body = [chunk for chunk in split if chunk["id"] not in omitted]
    ends = [
        (gm[line[0]]["origin"][0], max(g["bbox"][2] for g in glyphs))
        for chunk in body
        for line in chunk["lines"]
        if (glyphs := inked(line))
    ]
    flush = [right for x, right in ends if abs(x - margin) <= font_size * 0.15]
    text_right = max(flush or [right for _, right in ends], default=margin)
    previous: dict[str, Any] | None = None
    for chunk in body:
        # Only text opens a paragraph: a tombstone, a label or a formula fragment set beside
        # a display stands between the prose and the display it belongs to.
        opens = bool(language_words("".join(str(g["text"]) for g in inked(chunk["lines"][0]))))
        if opens and previous is not None and previous["id"] not in display_blocks:
            last = inked(previous["lines"][-1])
            tail = "".join(str(g["text"]) for g in last)
            # A text line starts where text starts: at the margin, a paragraph indent or a
            # list item's text column. A row set further right is a display, whatever it
            # says, unless the planner already read the chunk as a list item.
            text_start = (
                gm[previous["lines"][-1][0]]["origin"][0] < margin + font_size * LIST_TEXT_COLUMN_EM
                or previous["id"] in list_items
            )
            closed = bool(last) and text_start and prose(last) and (
                TERMINAL_PUNCTUATION.search(tail.rstrip()) is not None
                or max(g["bbox"][2] for g in last) < text_right - font_size * 1.5
            )
            gap = gm[chunk["lines"][0][0]]["origin"][1] - gm[previous["lines"][-1][0]]["origin"][1]
            if closed and gap > gap_threshold:
                chunk["paragraph_break"] = True
        previous = chunk


def _line_starts(
    glyphs: list[dict[str, Any]], blocks: list[dict[str, Any]], margin: float, font_size: float,
    ink: dict[str, Any] | None,
) -> dict[str, float]:
    """Where the printed row of a native line that starts mid-row begins, by first glyph id.

    A tall operator (a sum with limits, a big union) at the start of a printed line is a
    block of its own, and the text after it opens another block whose first glyph sits an
    em or two right of the margin — where a paragraph indent would be; the limits of an
    inline sum open a block of their own the same way, far to the right. Such a line
    continues its printed row, and the row starts at the leftmost ink before the line: a
    glyph whose ink shares the vertical extent of the line's first glyph (a word on the
    same baseline, a tall operator spanning it), walking left while the row is unbroken
    (no gap wider than 1.5 ems: a label or a column far to the left is another row's
    business). The ink decides the row, not the origins: MuPDF puts a radical's origin
    at its top, a text baseline away from the row it is set on. A line that opens its own
    row is not listed; the planner reads a listed start only where it is a text start (the
    margin or an open item's column), since a row that starts an em or two in may itself
    be indented. Metric boxes of CMEX glyphs sit near their top, so the measured ink
    (``ink``) is used when available.
    """
    gm = {g["id"]: g for g in glyphs}

    def measured(g: dict[str, Any]) -> list[float]:
        return list(ink.get(g["id"], g["bbox"]) if ink else g["bbox"])

    inked = [(g, measured(g)) for g in glyphs if inked_glyph(g) and g["bbox"][0] >= margin - 1]
    starts: dict[str, float] = {}
    for block in blocks:
        for line in block["lines"]:
            first = gm.get(line[0]) if line else None
            if first is None:
                continue
            x = first["origin"][0]
            own_box = measured(first)
            if own_box[3] - own_box[1] > font_size * 1.5:
                continue
            own = set(line)
            left = sorted(
                ((g, box) for g, box in inked
                 if g["id"] not in own and box[2] <= x + 0.5 and box[0] < x - font_size * 0.3),
                key=lambda pair: pair[1][2], reverse=True,
            )
            # The row grows leftwards from the line's first glyph: a glyph joins it when
            # its ink shares most of the shorter height with the row so far (a subscript
            # opening the line joins through the operator it hangs from, a period through
            # the word before it); a glyph on another row is passed over. The start is
            # the row's first glyph origin, as every other line start is measured.
            start, top, bottom = x, own_box[1], own_box[3]
            for g, box in left:
                if start - box[2] > font_size * 1.5:
                    break
                shared = min(box[3], bottom) - max(box[1], top)
                if shared <= 0.5 * max(0.5, min(box[3] - box[1], bottom - top)):
                    continue
                start, top, bottom = min(start, g["origin"][0]), min(top, box[1]), max(bottom, box[3])
            if start < x:
                starts[line[0]] = start
    return starts


def body_font(glyphs: list[dict[str, Any]]) -> str:
    """The running text face of a page: the font most of its text-face letters are set in."""
    fonts = Counter(
        str(g.get("font", "")) for g in glyphs
        if str(g["text"]).isalpha() and not MATH_FONT.search(str(g.get("font", "")))
    )
    return fonts.most_common(1)[0][0] if fonts else ""


def _letters(glyphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [g for g in glyphs if str(g["text"]).isalpha() and not MATH_FONT.search(str(g.get("font", "")))]


def _running_text_face(letters: list[dict[str, Any]], font_size: float, body: str) -> bool:
    """Letters set like the page's running text: body size, mostly in the body face or its
    (non-bold) italic, neither capitals nor small capitals throughout."""
    if not letters:
        return False
    size = sorted(g["size"] for g in letters)[len(letters) // 2]
    if abs(size - font_size) > font_size * 0.05:
        return False
    running = sum(
        str(g["font"]) == body or (bool(ITALIC_FONT.search(str(g["font"]))) and not BOLD_FONT.search(str(g["font"])))
        for g in letters
    )
    if running < 0.6 * len(letters):
        return False
    if len(letters) >= 2 and sum(str(g["text"]).isupper() for g in letters) >= 0.8 * len(letters):
        return False
    return sum(bool(SMALL_CAPS_FONT.search(str(g["font"]))) for g in letters) < 0.5 * len(letters)


def detector_role_holds(
    kind: str, glyphs: list[dict[str, Any]], first_x: float, margin: float, font_size: float, body: str, text: str,
) -> bool:
    """Whether a chunk's typography bears out a detector's heading or caption label.

    A layout detector labels boxes by their look, and it labels a body-size italic step line
    ("Step 2. Infinite-dimensional analog.") or a run-in theorem line as a title, and a sentence
    that mentions a figure ("Figure 10.2 shows ...") as its caption (LT-086). The label stands when
    the chunk is set apart from running text; set like running text — body size, the body face or
    its italic, starting at the margin or a paragraph indent — it is prose. A heading is set apart
    by size, a bold face, capitals or small capitals, or by standing clear of the text start (a
    centred title); a caption by a smaller size, a label in a face of its own (``**Figure 1.1.**``,
    small capitals), standing clear of the text start (a centred or indented caption), or opening
    with a closed label (``Figure 3.`` / ``Table 2:``).
    """
    letters = _letters(glyphs)
    if not letters:
        return True
    at_text_start = margin - font_size * 0.3 <= first_x < margin + font_size * 2.8
    if not at_text_start:
        return True
    if kind == "heading":
        return not _running_text_face(letters, font_size, body)
    size = sorted(g["size"] for g in letters)[len(letters) // 2]
    if size <= font_size * 0.95:
        return True
    faces = Counter(str(g["font"]) for g in letters)
    dominant = faces.most_common(1)[0][0]
    if str(letters[0]["font"]) != dominant or dominant != body:
        return True
    return CLOSED_CAPTION_LABEL.match(text) is not None


def _prose_margin(
    starts: Counter[float], blocks: list[dict[str, Any]], gm: dict[str, dict[str, Any]],
    omitted: dict[str, str], font_size: float,
) -> float:
    """The page's prose margin: the most common line start, unless that start is a list's text column.

    On a page set mostly as a list the continuation lines of the items outnumber the prose lines,
    and the items' text column would read as the margin: a paragraph indent left of it would then
    be no indent at all and two paragraphs would run together (LT-086). The mode is replaced only
    when it is the text column of the labelled lines on the page and prose — at least two text lines
    that open in a text face, carry a language word and are not labels or running material — starts
    left of those labels; a page of exercises with no prose beside them keeps the mode.
    """
    margin = starts.most_common(1)[0][0] if starts else 0
    align = font_size * 0.15
    label_x: list[float] = []
    prose: Counter[float] = Counter()
    for b in blocks:
        if b["id"] in omitted:
            continue
        for line in b["lines"]:
            glyphs = [gm[gid] for gid in line]
            inked = [g for g in glyphs if inked_glyph(g)]
            if not inked:
                continue
            x = gm[line[0]]["origin"][0]
            label = list_label(glyphs, font_size)
            if label is not None:
                if abs(label[1] - margin) <= align and label[1] > x + align:
                    label_x.append(x)
                continue
            text = "".join(str(g["text"]) for g in glyphs if not MATH_FONT.search(str(g.get("font", ""))))
            if not MATH_FONT.search(str(inked[0].get("font", ""))) and language_words(text):
                prose[round(x, 1)] += 1
    if not label_x:
        return margin
    left = [(count, -x) for x, count in prose.items() if count >= 2 and x < min(label_x) - align]
    return -max(left)[1] if left else margin


def plan_structure(
    glyphs: list[dict[str, Any]], blocks: list[dict[str, Any]], layout: list[dict[str, Any]], height: float,
    display_glyph_ids: set[str] | None = None, ink: dict[str, Any] | None = None,
) -> dict[str, Any]:
    gm = {g["id"]: g for g in glyphs}
    usable = [g for g in glyphs if math.isfinite(g["size"]) and round(g["size"], 1) > 0]
    sizes = (Counter(round(g["size"], 1) for g in usable if g["text"].isalpha())
             or Counter(round(g["size"], 1) for g in usable))
    font_size = sizes.most_common(1)[0][0] if sizes else 10
    starts = Counter(
        round(gm[line[0]]["origin"][0], 1) for b in blocks for line in b["lines"] if line
    )
    labels = [(item["label"], [v / 2 for v in item["bbox"]]) for item in layout]
    notes, omitted = {}, {}
    display_glyph_ids = display_glyph_ids or set()
    # Page numbers detected at the page edge are running material even when the PDF
    # merges them into the neighbouring paragraph's text block.
    page_number_boxes = [
        box for label, box in labels
        if label == "number" and ((box[1] + box[3]) / 2 < height * 0.12 or (box[1] + box[3]) / 2 > height * 0.88)
    ]
    detached: list[dict[str, Any]] = []
    if page_number_boxes:
        for b in blocks:
            moved = [gid for line in b["lines"] for gid in line if any(_contains(gm[gid], box) for box in page_number_boxes)]
            if not moved or len(moved) == sum(len(line) for line in b["lines"]):
                continue
            b["lines"] = [[gid for gid in line if gid not in set(moved)] for line in b["lines"]]
            b["lines"] = [line for line in b["lines"] if line]
            b["bbox"] = [
                min(gm[gid]["bbox"][0] for line in b["lines"] for gid in line),
                min(gm[gid]["bbox"][1] for line in b["lines"] for gid in line),
                max(gm[gid]["bbox"][2] for line in b["lines"] for gid in line),
                max(gm[gid]["bbox"][3] for line in b["lines"] for gid in line),
            ]
            detached.append({"id": b["id"] + "-pagenum", "bbox": [
                min(gm[gid]["bbox"][0] for gid in moved), min(gm[gid]["bbox"][1] for gid in moved),
                max(gm[gid]["bbox"][2] for gid in moved), max(gm[gid]["bbox"][3] for gid in moved),
            ], "lines": [moved]})
        blocks = blocks + detached
    for b in blocks:
        gs = [gm[gid] for line in b["lines"] for gid in line]
        if not gs:
            continue
        if b["id"].endswith("-pagenum") or (
            page_number_boxes and all(any(_contains(g, box) for box in page_number_boxes) for g in gs)
        ):
            omitted[b["id"]] = "page-number"
        if any(
            label in {"header", "footer"}
            and any(_contains(g, box) for g in gs)
            and all(box[1] - 2 <= (g["bbox"][1] + g["bbox"][3]) / 2 <= box[3] + 2 for g in gs)
            for label, box in labels
        ):
            omitted[b["id"]] = "running-header-or-footer"
        if any(label == "footnote" and any(_contains(g, box) for g in gs) for label, box in labels):
            match = re.match(r"(\d+)(?!\d)(?=\s|[^\W\d_]|[.)：:、．\]）])", "".join(g["text"] for g in gs))
            if match:
                notes[b["id"]] = {
                    "number": match[1],
                    "glyph_ids": [g["id"] for g in gs[: len(match[1])]],
                    "top": b["bbox"][1],
                }
    markers = {
        gid: {"number": n["number"], "note_block": bid, "definition": True}
        for bid, n in notes.items()
        for gid in n["glyph_ids"]
    }
    margin = _prose_margin(starts, blocks, gm, omitted, font_size)
    line_starts = _line_starts(glyphs, blocks, margin, font_size, ink)
    note_top = min((n["top"] for n in notes.values()), default=height + 1)
    note_numbers = {n["number"]: bid for bid, n in notes.items()}
    for block in blocks:
        for line in block["lines"]:
            runs: list[list[dict[str, Any]]] = []
            digit_run: list[dict[str, Any]] = []
            for gid in line:
                g = gm[gid]
                eligible = (g["text"].isdigit() and g["size"] < font_size * 0.8
                            and height * 0.12 < g["bbox"][1] < note_top
                            and not re.search(r"cmmi|cmsy|cmex", g["font"], re.I))
                adjacent = not digit_run or (
                    abs(g["origin"][1] - digit_run[-1]["origin"][1]) <= font_size * 0.15
                    and abs(g["size"] - digit_run[-1]["size"]) <= font_size * 0.1
                    and -1 <= g["bbox"][0] - digit_run[-1]["bbox"][2] <= font_size * 0.25)
                if not eligible or not adjacent:
                    if digit_run:
                        runs.append(digit_run)
                        digit_run = []
                if eligible:
                    digit_run.append(g)
            if digit_run:
                runs.append(digit_run)
            for run in runs:
                number = "".join(g["text"] for g in run)
                if number in note_numbers:
                    if any(re.search(r"cmr", g["font"], re.I) for g in run):
                        first = run[0]
                        bases = [g for g in glyphs if g["size"] >= font_size * .8
                                 and -1 <= first["bbox"][0] - g["bbox"][2] <= font_size * 2
                                 and 0 < g["origin"][1] - first["origin"][1] < font_size]
                        base = max(bases, key=lambda g: g["bbox"][2]) if bases else None
                        if base is None or re.search(r"cmmi|cmsy|cmex", base["font"], re.I) or base["text"].isdigit():
                            continue
                    for index, g in enumerate(run):
                        markers[g["id"]] = {"number": number, "note_block": note_numbers[number],
                                            "definition": False, "emit": index == 0}
    # Split a native block at a new first-line indent, but not at glyph fragments
    # on the same visual line. PDF blocks may span multiple author paragraphs.
    split, first_x, display_blocks = [], {}, set()
    title_boxes = [box for label, box in labels if label in TITLE_LABELS]
    page_font = body_font(glyphs)
    display_boxes = [box for label, box in labels if label == "display_formula"]
    # The page's usual baseline pitch; a gap well beyond it is paragraph white space.
    pitches = sorted(
        gm[b["lines"][i + 1][0]]["origin"][1] - gm[b["lines"][i][0]]["origin"][1]
        for b in blocks
        for i in range(len(b["lines"]) - 1)
        if 0 < gm[b["lines"][i + 1][0]]["origin"][1] - gm[b["lines"][i][0]]["origin"][1] < font_size * 3
    )
    pitch = pitches[len(pitches) // 2] if pitches else font_size * 1.2
    gap_threshold = max(font_size * 1.75, pitch * 1.45)
    # Open labelled items, innermost last: (chunk id, label x, text column). A label's text
    # column is where its continuation lines start, whatever the page's dominant margin is;
    # the labels themselves may be right-aligned ("1.9." / "1.10.") and share no x.
    items: list[tuple[str, float, float]] = []
    list_items: dict[str, dict[str, Any]] = {}
    align = font_size * 0.15
    # Where each line starts for the planner: its own x, or its printed row's start.
    line_x: dict[str, float] = {}
    for b in blocks:
        gs = [gm[gid] for line in b["lines"] for gid in line]
        # A wrapped heading keeps its continuation line even though the wrap is indented; a
        # title box over a block set like running text is the detector's mistake (LT-086).
        heading_block = bool(gs) and any(all(_contains(g, box) for g in gs) for box in title_boxes) and detector_role_holds(
            "heading", gs, gm[b["lines"][0][0]]["origin"][0], margin, font_size, page_font, ""
        )
        chunks: list[list[list[str]]] = []
        current: list[list[str]] = []
        chunk_roles: list[dict[str, Any]] = [{}]
        last_y: float | None = None
        last_x: float | None = None
        previous_display = False
        previous_label = False
        bullet_x: float | None = None
        for line in b["lines"]:
            g = gm[line[0]]
            x, y = g["origin"]
            inked = [gid for gid in line if inked_glyph(gm[gid])]
            display_line = bool(inked) and sum(gid in display_glyph_ids for gid in inked) >= len(inked) * 0.8
            # A line of prose that starts mid-row on a row that starts where text starts
            # (the margin, an open item's column) continues that row: the row's first line
            # has already opened or closed the items it opens or closes, and where the
            # line's own x could be read as a text start (an indent after a tall operator)
            # the row's start is its x. A line without a language word (the limits of a
            # sum, a piece of a display) keeps its own x: it is notation the regions own,
            # not a sentence the planner could break; so does a line that starts far to
            # the right, which reads as the formula suffix it is.
            row_start = line_starts.get(line[0])
            mid_row = (
                row_start is not None
                and not display_line
                and not any(_contains(g, box) for box in display_boxes)
                and bool(language_words("".join(gm[gid]["text"] for gid in line if not MATH_FONT.search(gm[gid]["font"]))))
                and (row_start < margin + font_size * 0.8 or any(abs(row_start - column) <= align for _, _, column in items))
            )
            if mid_row and row_start is not None and x < margin + font_size * 2.8 and abs(x - row_start) > align:
                x = row_start
            line_x[line[0]] = x
            indent = margin + font_size * 0.8 < x < margin + font_size * 2.8
            bullet_line = is_bullet_line([gm[gid] for gid in inked])
            # Prose returning left of the bullet column ends the list item.
            list_end = bullet_x is not None and not bullet_line and x < bullet_x - font_size * 0.5
            # Vertical white space, or a bold run-in label at the margin, opens a new
            # paragraph even when the PDF block and the indent do not show it.
            new_line = last_y is not None and y - last_y > font_size * 0.7
            gap = last_y is not None and y - last_y > gap_threshold
            run_in = new_line and x < margin + font_size * 0.8 and _bold_run_in(
                [gm[gid] for gid in line], [gm[gid] for gid in current[-1]] if current else []
            )
            label = None
            if not (heading_block or display_line or bullet_line or b["id"] in omitted):
                label = list_label([gm[gid] for gid in line], font_size)
            # The innermost open item whose text column this line (or its printed row) starts at.
            column_x = row_start if mid_row and row_start is not None else x
            owner = next((i for i in range(len(items) - 1, -1, -1) if abs(column_x - items[i][2]) <= align), None)
            # A label opens an item only where the geometry shows one: its text column is an
            # open item's (a sibling returning to the label column), or it starts its block,
            # follows another label, or opens right of the running text or of the open
            # item's label (a nested item). A number opening a line at the text column of
            # wrapped prose is a sentence, not an item.
            label_line = label is not None and (
                any(abs(label[1] - column) <= align for _, _, column in items)
                or (owner is None and (
                    last_x is None or previous_label or x > last_x + font_size * 0.3
                    or (bool(items) and x > items[-1][1] + font_size * 0.3)
                ))
            )
            continues: str | None = None
            resumes = False
            # A paragraph indent measured from an open item's text column is a new paragraph of
            # that item ("The natural extension ..." under "(1) As random functions ..."): it
            # opens a chunk that stays in the item's group and is never merged into it (LT-086).
            item_paragraph = (
                not label_line and owner is None and not display_line and not mid_row and bool(items)
                and (last_y is None or new_line)
                and items[-1][2] + font_size * 0.8 < x < items[-1][2] + font_size * 2.8
                and bool(language_words("".join(gm[gid]["text"] for gid in line if not MATH_FONT.search(gm[gid]["font"]))))
            )
            if item_paragraph:
                continues = items[-1][0]
            elif label_line and label is not None:
                while items and items[-1][2] >= label[1] - align:
                    items.pop()
            else:
                # Aligned with an open item's text column: its continuation. Deeper items are
                # closed, and prose resuming after a nested list starts its own chunk.
                if owner is not None:
                    continues = items[owner][0]
                    resumes = owner < len(items) - 1
                    del items[owner + 1:]
                elif not display_line and not mid_row:
                    # Text back at or left of the label column closes the item; whether it
                    # is a new paragraph or the item's own wrap is left to the rules above.
                    while items and x <= items[-1][1] + font_size * 0.3:
                        items.pop()
            if current and not heading_block and (
                (indent and new_line and continues is None)
                or item_paragraph
                or gap
                or run_in
                or display_line != previous_display
                or bullet_line
                or list_end
                or label_line
                or resumes
            ):
                chunks.append(current)
                current = []
                chunk_roles.append({})
                bullet_x = None
            if bullet_line:
                bullet_x = gm[inked[0]]["origin"][0]
            if not current:
                if label_line and label is not None:
                    chunk_roles[-1] = {"label": label[0], "body_x": round(label[1], 1)}
                elif continues is not None:
                    chunk_roles[-1] = {"continues": continues, **({"paragraph": True} if item_paragraph else {})}
            if label_line and label is not None:
                chunk_id = b["id"] if not chunks else b["id"] + f"-s{len(chunks) + 1}"
                items.append((chunk_id, x, label[1]))
            current.append(line)
            last_y = y
            last_x = x
            previous_display = display_line
            previous_label = label_line
        if current:
            chunks.append(current)
        for i, lines in enumerate(chunks):
            bid = b["id"] if i == 0 else b["id"] + f"-s{i + 1}"
            if chunk_roles[i]:
                list_items[bid] = chunk_roles[i]
            gg = [gm[gid] for line in lines for gid in line]
            box = [
                min(g["bbox"][0] for g in gg),
                min(g["bbox"][1] for g in gg),
                max(g["bbox"][2] for g in gg),
                max(g["bbox"][3] for g in gg),
            ]
            split.append({"id": bid, "bbox": box, "lines": lines})
            inked_ids = [gid for line in lines for gid in line if inked_glyph(gm[gid])]
            if inked_ids and sum(gid in display_glyph_ids for gid in inked_ids) >= len(inked_ids) * 0.8:
                display_blocks.add(bid)
            # Ignore a formula suffix far to the right when finding the prose indent.
            near = [
                line_x.get(line[0], gm[line[0]]["origin"][0])
                for line in lines
                if line_x.get(line[0], gm[line[0]]["origin"][0]) < margin + font_size * 2.8
            ]
            first_x[bid] = near[0] if near else line_x.get(lines[0][0], gg[0]["origin"][0])
    _mark_paragraph_breaks(split, gm, omitted, markers, display_blocks, margin, font_size, gap_threshold, list_items)
    return {
        "blocks": split,
        "margin": margin,
        "font_size": font_size,
        "indent_style": sum(
            count
            for x, count in starts.items()
            if margin + font_size * 0.8 < x < margin + font_size * 2.8
        )
        >= 2,
        "notes": notes,
        "markers": markers,
        "omitted": omitted,
        "note_top": note_top,
        "first_x": first_x,
        "display_blocks": sorted(display_blocks),
        # Present only on pages with labelled items: a page without them keeps a ledger
        # identical to one prepared before labels were recognised.
        **({"list_items": list_items} if list_items else {}),
    }


def assemble_structure(
    units: list[SourceUnit], assets: dict[str, FidelityAsset], plan: dict[str, Any], make_unit: Callable[..., SourceUnit],
    rejoin: Callable[[str], str] | None = None,
) -> list[SourceUnit]:
    """Use parent_id for a paragraph containing independently numbered displays.

    ``rejoin`` resolves a word hyphenated across the seam of two merged fragments
    (``"condi-\\ntioning"``); without it the halves are always joined. A chunk the plan
    flags as opening after paragraph white space (``paragraph_break``) is treated like
    an indented paragraph: it starts a group and is never merged into the unit before it.
    """
    from littrans.fidelity_models import asset_reference_ids
    from littrans.models import RenderPolicy

    result: list[SourceUnit] = []
    group: str | None = None
    active_note: str | None = None
    statement = None
    statement_italic = False
    # The open enumeration: the label x and the parent of its last bracketed item that
    # hangs from an introducing paragraph, so a later sibling returns to that parent
    # whatever group an item's own continuation paragraph opened in between; a parent of
    # None records an item that opened a group of its own (at a page top), whose siblings
    # open their own too rather than hang from it.
    enumeration: tuple[float, str | None] | None = None
    # A unit merged into the one before it keeps no id of its own; a parent set to that
    # id before the merge is redirected to the survivor at the end.
    merged_into: dict[str, str] = {}
    display_blocks = set(plan.get("display_blocks", ()))
    list_items: dict[str, dict[str, Any]] = plan.get("list_items", {})
    breaks = {chunk["id"] for chunk in plan.get("blocks", ()) if chunk.get("paragraph_break")}

    def rebuild(u: SourceUnit, text: str | None = None, **changes: Any) -> SourceUnit:
        extra = {
            k: getattr(u, k)
            for k in (
                "equation_number",
                "footnote_number",
                "footnote_refs",
                "parent_id",
                "continues_from_previous",
                "continued_to_next",
                "render_policy",
                "translatable",
            )
        }
        extra.update(changes)
        box = extra.pop("bbox", u.bbox)
        return make_unit(
            u.page,
            u.unit_id,
            u.source_text if text is None else text,
            box,
            assets,
            kind=extra.pop("kind", u.kind.value),
            **extra,
        )

    for u in units:
        bid = u.unit_id.split("-", 1)[1]
        refs = asset_reference_ids(u.source_text)
        display = any(assets[aid].display for aid in refs) or bid in display_blocks
        # An indented paragraph that stays in its group (an italic continuation of a
        # statement) is still a paragraph: it is never merged into the one before it.
        opens_paragraph = False
        if bid in plan["omitted"] or (
            u.bbox[1] < plan["note_top"]
            and u.bbox[3] >= plan["note_top"] - 20
            and refs
            and all(not f.glyph_ids and f.height < 4 for aid in refs for f in assets[aid].fragments)
        ):
            for aid in refs:
                assets[aid] = assets[aid].model_copy(update={"grouping_pending": False})
            result.append(
                rebuild(u, kind="note", render_policy=RenderPolicy.OMIT, translatable=False)
            )
            continue
        if u.render_policy == RenderPolicy.OMIT:
            # Running material the page carries but nobody reads (a decorative rule) is
            # placed in the open group but opens none: the prose after it starts its own
            # (LT-080). At the page top it is its own parent, as recorded before.
            result.append(rebuild(u, parent_id=group or u.unit_id))
            continue
        if bid in plan["notes"]:
            active_note = plan["notes"][bid]["number"]
            group = u.unit_id
        footnote = u.bbox[1] >= plan["note_top"] and active_note is not None
        if footnote:
            u = rebuild(
                u, kind="footnote", footnote_number=active_note, parent_id=group, translatable=True
            )
        else:
            x = plan["first_x"].get(bid, plan["margin"])
            # A chunk continuing a labelled item sits at the item's text column, not at a
            # paragraph indent, whatever the page's dominant margin makes of that x.
            indented = "continues" not in list_items.get(bid, {}) and (
                plan["margin"] + plan["font_size"] * 0.8
                < x
                < plan["margin"] + plan["font_size"] * 2.8
            )
            # A paragraph indented from an item's text column opens a paragraph of that item.
            opens_paragraph = indented or bool(list_items.get(bid, {}).get("paragraph"))
            starts_statement = _starts_statement(u.source_text)
            label = LIST_LABEL_START.match(u.source_text)
            # A bracketed clause ((a), (ii)) belongs to the paragraph or statement that
            # introduces it; a closed number (1.11.) opens an exercise or item of its own.
            enumerated = label is not None and label[0].lstrip("* \t").startswith("(")
            numbered = label is not None and not enumerated
            proof = bool(re.match(r"[*\s]*Proof\b", u.source_text))
            # A bold run-in label ("2.1.4. Stochastic processes.") opens a paragraph.
            run_in = bool(RUN_IN_LABEL.match(u.source_text))
            # Paragraph white space above a closed line is the indent of a document that
            # spaces its paragraphs; a displayed element after it stays a child of its paragraph.
            paragraph_break = bid in breaks and not display
            boundary = u.kind.value in {"heading", "list_item", "figure", "table", "caption", *NOTE_KINDS} or starts_statement or proof or run_in or numbered
            previous_body = next((r for r in reversed(result) if r.render_policy != RenderPolicy.OMIT), None)
            # Prose resuming after a statement's enumerated clauses is the statement's
            # conclusion, even across the white space that closes the list.
            concludes = (
                paragraph_break
                and statement is not None
                and previous_body is not None
                and previous_body.parent_id == statement
                and LIST_LABEL_START.match(previous_body.source_text) is not None
            )
            # An indented paragraph set in the statement's own italic face continues the
            # statement ("Conversely, if ..." after a theorem's enumerated clauses); upright
            # prose ends it, as before (LT-081).
            italic_continuation = (
                statement is not None
                and statement_italic
                and label is None
                and not run_in
                and not proof
                and u.kind.value == "paragraph"
                and _emphasised_share(u.source_text) >= STATEMENT_ITALIC_SHARE
            )
            if starts_statement:
                statement = u.unit_id
                statement_italic = _emphasised_share(u.source_text) >= STATEMENT_ITALIC_SHARE
            elif (
                u.kind.value == "heading"
                or proof
                or ((indented or (paragraph_break and not concludes)) and label is None and u.kind.value != "equation"
                    and not italic_continuation)
            ):
                statement = None
            # What closes the list closes its enumeration: a heading, a statement, a proof, a
            # run-in or numbered label, or prose back at the margin after white space. An
            # item's own indented continuation paragraph does not.
            at_margin = x < plan["margin"] + plan["font_size"] * 0.8
            if (
                u.kind.value == "heading" or starts_statement or proof or run_in or numbered
                or (paragraph_break and not enumerated and at_margin and u.kind.value != "equation")
            ):
                enumeration = None
            # Headings, list items and figure/table elements close their group;
            # the prose that follows starts a new logical paragraph.
            after_heading = previous_body is not None and (
                previous_body.kind.value in {"heading", "caption", "figure", "table"}
                or (previous_body.kind.value == "list_item" and u.kind.value != "list_item")
            )
            figure_pair = (
                previous_body is not None
                and {previous_body.kind.value, u.kind.value} <= {"figure", "table", "caption"}
                and previous_body.kind.value != u.kind.value
                and previous_body.render_policy != RenderPolicy.OMIT
            )
            list_member = (
                u.kind.value == "list_item"
                and previous_body is not None
                and previous_body.render_policy != RenderPolicy.OMIT
                and previous_body.kind.value in {"list_item", "paragraph", "equation"}
            )
            # The item this chunk continues, as the planner placed it (its text column).
            continued_item = None
            continued = list_items.get(bid, {}).get("continues")
            if continued is not None:
                item_id = f"p{u.page:04d}-{continued}"
                while item_id in merged_into:
                    item_id = merged_into[item_id]
                continued_item = next((r for r in result if r.unit_id == item_id), None)
            if figure_pair and previous_body is not None:
                # A figure/table and its adjacent caption form one element.
                group = previous_body.parent_id or previous_body.unit_id
            elif list_member and previous_body is not None:
                # List items belong to the paragraph that introduces them and to
                # each other; the list is one structure, not scattered elements.
                group = previous_body.parent_id or previous_body.unit_id
            elif statement and (starts_statement or label is not None or u.kind.value == "equation" or concludes or italic_continuation):
                group = statement
            elif continued_item is not None:
                # Prose the planner read as an item's continuation returns to the item's
                # group whatever opened in between (a displayed row of the item) (LT-081).
                group = continued_item.parent_id or continued_item.unit_id
            elif (
                enumerated
                and enumeration is not None
                and not after_heading
                and abs(x - enumeration[0]) <= plan["font_size"] * 0.5
            ):
                # A sibling clause returns to the parent its enumeration opened with, or
                # opens its own group as the first item did.
                group = enumeration[1] or u.unit_id
            elif group is None or after_heading or (
                u.kind.value != "equation" and ((indented and not enumerated) or (paragraph_break and not enumerated) or boundary)
            ):
                # A bracketed clause ((a), (ii)) set off by white space or by its own indent
                # is still a clause of the paragraph that introduces it, never a group of
                # its own (LT-062, LT-081).
                group = u.unit_id
            if enumerated and group is not None:
                # Only an enumeration that hangs from an introducing paragraph or statement
                # binds its siblings; items that opened a group of their own leave their
                # siblings their own. The column is the planner's line start, not the bbox
                # a display widens.
                enumeration = (x, group if group != u.unit_id else None)
            elif after_heading:
                enumeration = None
            u = rebuild(u, parent_id=group)
        # Merge prose fragments within one paragraph, retaining display children.
        previous = result[-1] if result else None
        previous_display = previous and (
            any(assets[aid].display for aid in asset_reference_ids(previous.source_text))
            or previous.unit_id.split("-", 1)[1] in display_blocks
        )
        # A PDF block boundary inside one visual line (a tall inline integral splits
        # the line into blocks) continues the previous fragment, whatever its kind.
        # Likewise the prose set beside a displayed formula on its line ("for all
        # times t > 0.") completes that display unit rather than starting a paragraph.
        same_line = bool(
            previous
            and previous.render_policy != RenderPolicy.OMIT
            and u.bbox[0] > previous.bbox[0]
            and min(previous.bbox[3], u.bbox[3]) - max(previous.bbox[1], u.bbox[1])
            > 0.5 * min(previous.bbox[3] - previous.bbox[1], u.bbox[3] - u.bbox[1])
            and (
                (
                    not display
                    and not previous_display
                    and u.kind.value in {"paragraph", "list_item"}
                    and previous.kind.value in {"paragraph", "list_item"}
                )
                or (
                    bid in display_blocks
                    and previous_display
                    and previous.kind.value == "equation"
                    and u.kind.value == "equation"
                )
            )
        )
        if same_line and previous is not None:
            u = rebuild(u, parent_id=previous.parent_id)
        if previous is not None and (same_line or (
            previous.render_policy != RenderPolicy.OMIT
            and previous.parent_id == u.parent_id
            # Paragraph white space or an indent is a hard seam, whatever the group says.
            and bid not in breaks
            and not opens_paragraph
            and not display
            and not previous_display
            and previous.kind.value not in {"heading", "caption", "figure", "table", "list_item"}
            and u.kind.value not in {"heading", "caption", "figure", "table", "list_item"}
            # A note or reference entry never joins prose, nor prose it; the fragments
            # of one wrapped footnote still do.
            and (previous.kind.value == u.kind.value or not {previous.kind.value, u.kind.value} & NOTE_KINDS)
            and not LIST_LABEL_START.match(u.source_text)
            # A printed equation label preparation could not bind stays its own unit.
            and not re.match(r"[*\s]*" + EQUATION_LABEL, u.source_text)
        )):
            # The seam is a line end until hyphenation is resolved, as inside a block.
            joined = previous.source_text.rstrip() + "\n" + u.source_text.lstrip()
            joined = re.sub(r"\s+([,.;:”’)\]])", r"\1", joined)
            joined = rejoin(joined) if rejoin else re.sub(r"([a-z])-\s+([a-z])", r"\1\2", joined)
            joined = joined.replace("\n", " ")
            merged = rebuild(
                previous,
                joined,
                footnote_refs=list(dict.fromkeys(previous.footnote_refs + u.footnote_refs)),
            )
            # Geometry must include every original fragment, not just the first block.
            box = [
                min(previous.bbox[0], u.bbox[0]),
                min(previous.bbox[1], u.bbox[1]),
                max(previous.bbox[2], u.bbox[2]),
                max(previous.bbox[3], u.bbox[3]),
            ]
            merged = rebuild(merged, bbox=box)
            result[-1] = merged
            merged_into[u.unit_id] = previous.unit_id
            # The group the merged text opened now lives in the surviving unit's group.
            if group == u.unit_id:
                group = merged.parent_id or merged.unit_id
            if statement == u.unit_id:
                statement = merged.unit_id
        else:
            result.append(u)
    by_id = {u.unit_id: u for u in result}
    for index, u in enumerate(result):
        if u.parent_id is None or u.parent_id in by_id:
            continue
        # A parent that never became a unit was merged into another one: the group is
        # the survivor's group. Anything else resolves to the unit itself.
        parent, seen = u.parent_id, set()
        while parent in merged_into and parent not in seen:
            seen.add(parent)
            parent = merged_into[parent]
        survivor = by_id.get(parent)
        result[index] = rebuild(u, parent_id=(survivor.parent_id or survivor.unit_id) if survivor else u.unit_id)
    body = [
        i
        for i, u in enumerate(result)
        if u.render_policy.value == "include" and u.kind.value not in {"footnote", "note"}
    ]
    if body:
        first, last = body[0], body[-1]
        bid = result[first].unit_id.split("-", 1)[1]
        opening = result[first].source_text
        # A flush first line continues the paragraph the previous page ended in, unless
        # its text opens something of its own: a run-in label, a statement, a proof, a
        # list item. Whether the previous page's last sentence closed is judged where
        # both pages are in hand (rendering), from the sender's continued_to_next.
        opens_own = bool(
            RUN_IN_LABEL.match(opening) or _starts_statement(opening)
            or re.match(r"[*\s]*Proof\b", opening) or LIST_LABEL_START.match(opening)
        )
        # A first line that opens with a capital letter opens a sentence, not a continuation
        # of one ("This gives", "Then"): the flag means the sentence continues, and a sender
        # whose last line ends mid-sentence carries that on its own flag (LT-080). Scripts
        # without case keep the geometric reading.
        first_letter = next((c for c in re.sub(r"\{\{asset:[^}]+\}\}", "", opening) if c.isalpha()), "")
        if (
            plan.get("indent_style")
            and result[first].page > 1
            and result[first].kind.value == "paragraph"
            and abs(plan["first_x"].get(bid, 0) - plan["margin"]) < 2
            and not opens_own
            and not first_letter.isupper()
        ):
            result[first] = rebuild(result[first], continues_from_previous=True)
        tail = re.sub(r"[*\s]+$", "", result[last].source_text)
        if result[last].kind.value == "paragraph" and tail and tail[-1].isalpha():
            result[last] = rebuild(result[last], continued_to_next=True)
    return result


def styled_text(tokens: list[tuple[str, str]]) -> str:
    from itertools import groupby

    # Whitespace between two runs of the same style joins them (*Brownian motion*),
    # whichever face the PDF assigned to the space glyph itself.
    tokens = list(tokens)
    for index, (text, style) in enumerate(tokens):
        if text.isspace() and 0 < index < len(tokens) - 1:
            before = tokens[index - 1][1]
            after = next((s for t, s in tokens[index + 1:] if not t.isspace()), "")
            if before == after and before != style:
                tokens[index] = (text, before)
    parts = []
    for style, group in groupby(tokens, key=lambda item: item[1]):
        text = "".join(item[0] for item in group)
        core = text.strip()
        if not core or not style or not any(c.isalnum() for c in core):
            # Punctuation alone carries no emphasis worth marking.
            parts.append(text)
        else:
            leading = text[: len(text) - len(text.lstrip())]
            trailing = text[len(text.rstrip()) :]
            parts.append(leading + style + core + style + trailing)
    return "".join(parts)


def coalesce_inline_assets(units: list[SourceUnit], assets: dict[str, FidelityAsset], make_unit: Callable[..., SourceUnit], hash_value: Callable[[Any], str]) -> list[SourceUnit]:
    """Adjacent fragments of inline notation form one ordered multi-fragment asset."""
    # Placeholders separated by spaces or tabs only: a newline (a row break of a displayed
    # block) is a boundary. Fragments of one wrapped expression may sit on different visual
    # lines; region preparation joins the halves it can identify (line-break-continued).
    pattern = r"\{\{asset:[^}]+\}\}(?:[ \t]*\{\{asset:[^}]+\}\})+"
    result = []
    for unit in units:
        text = unit.source_markdown or unit.source_text
        for match in list(re.finditer(pattern, text)):
            ids = re.findall(r"\{\{asset:([^}]+)\}\}", match[0])
            if len(set(ids)) != len(ids) or any(
                assets[aid].kind != "math" or assets[aid].display for aid in ids
            ):
                continue
            fragments = [f for aid in ids for f in assets[aid].fragments]
            hashes = [
                hash_value(
                    {
                        "source_sha256": assets[ids[0]].source_sha256,
                        "page": f.page,
                        "bbox": f.bbox,
                        "glyph_ids": f.glyph_ids,
                        **(
                            {"export_method": f.export_method}
                            if f.export_method != "raw-region"
                            else {}
                        ),
                    }
                )
                for f in fragments
            ]
            content = hash_value(hashes)
            aid = f"a-p{unit.page:04d}-{content[:12]}"
            assets[aid] = FidelityAsset(
                id=aid,
                kind="math",
                source_sha256=assets[ids[0]].source_sha256,
                content_sha256=content,
                fragments=fragments,
                display=False,
                grouping_pending=False,
                provenance=["adjacent-inline-fragments"],
            )
            text = text.replace(match[0], "{{asset:" + aid + "}}", 1)
            for old in ids:
                assets.pop(old)
        extra = {
            k: getattr(unit, k)
            for k in (
                "equation_number",
                "footnote_number",
                "footnote_refs",
                "parent_id",
                "continues_from_previous",
                "continued_to_next",
                "render_policy",
                "translatable",
            )
        }
        result.append(
            make_unit(
                unit.page, unit.unit_id, text, unit.bbox, assets, kind=unit.kind.value, **extra
            )
        )
    return result
