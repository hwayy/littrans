"""A single evidence-first PDF preparation path, with explicit visual-review gates."""
from __future__ import annotations

import html
import json
import re
import shutil
from collections import Counter
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import pymupdf as fitz

from littrans.build_info import build_identity
from littrans.extractor import parse_page_spec, protected_tokens
from littrans.fidelity_models import (
    FidelityAsset,
    FidelityFragment,
    asset_reference_ids,
    load_assets,
)
from littrans.layout_detector import detect_layout, layout_page_items, layout_result_path
from littrans.models import AssetRef, SemanticStatus, SourceUnit, TranslationRecord, UnitKind
from littrans.source_structure import (
    BOLD_FONT,
    EQUATION_LABEL,
    LANGUAGE_TOKEN,
    MATH_FONT,
    MATH_OPERATORS,
    font_style,
    inked_glyph,
    is_bullet_line,
    language_words,
)
from littrans.storage import (
    atomic_write_bytes,
    atomic_write_text,
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    restore_files,
    sha256_file,
    sha256_text,
    snapshot_files,
    write_json,
    write_jsonl,
)

MATH_CHAR = re.compile(r"[\u0370-\u03ff\u2100-\u214f\u2190-\u22ff\u27c0-\u27ef=<>^_|]")
# Relations and binary/large operators: TeX breaks an inline formula across lines only
# after one of these, so a run that ends its line with one continues on the next line.
BREAK_OPERATORS = set("=<>≤≥≠≡≈∼≃≅∝∈∉∋⊂⊃⊆⊇∪∩+−-±∓×·∘→↦⇒⇔∑∏∫∮/")
OPENING_BRACKETS = "([{"
CLOSING_BRACKETS = ")]}"
# TeX-style spacing accents set as separate glyphs above a base letter.
SPACING_ACCENTS = {
    "\u02c6": "\u0302", "^": "\u0302", "\u02dc": "\u0303", "~": "\u0303", "\u00a8": "\u0308", "\u00b4": "\u0301",
    "`": "\u0300", "\u00af": "\u0304", "\u02d8": "\u0306", "\u02c7": "\u030c", "\u02d9": "\u0307",
    "\u02da": "\u030a", "\u02dd": "\u030b", "\u00b8": "\u0327",
}
LIGATURES = {"\ufb00": "ff", "\ufb01": "fi", "\ufb02": "fl", "\ufb03": "ffi", "\ufb04": "ffl", "\ufb05": "st", "\ufb06": "st"}


def _compose_accents(glyphs: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> None:
    """Fold a spacing accent glyph into the prose letter it is set above (IT \u02c6O -> IT\u00d4).

    The accent must overlap the base letter horizontally and sit above it; a kern
    that PyMuPDF reports as a narrow space between them is dropped as well. Only
    prose letters with a precomposed Unicode form are changed; mathematical accents
    stay separate glyphs for region ownership.
    """
    import unicodedata

    by_id = {g["id"]: g for g in glyphs}
    dropped: set[str] = set()
    for block in blocks:
        for line in block["lines"]:
            for index, gid in enumerate(line):
                accent = by_id[gid]
                mark = SPACING_ACCENTS.get(accent["text"])
                if mark is None or gid in dropped or MATH_FONT.search(accent["font"]):
                    continue
                ax0, ay0, ax1, ay1 = accent["bbox"]
                centre = (ax0 + ax1) / 2
                for step in (1, -1):
                    position = index + step
                    spacer: str | None = None
                    if 0 <= position < len(line) and by_id[line[position]]["text"].isspace():
                        spacer = line[position]
                        width = by_id[spacer]["bbox"][2] - by_id[spacer]["bbox"][0]
                        if width > accent["size"] * 0.3:
                            continue
                        position += step
                    if not 0 <= position < len(line):
                        continue
                    base = by_id[line[position]]
                    bx0, by0, bx1, by1 = base["bbox"]
                    # Font metrics give the accent a full line box: over a capital it is
                    # raised, over a lowercase letter it shares the letter's box. Either
                    # way it is set on the letter's horizontal extent, never beside it.
                    if not (base["text"].isalpha() and len(base["text"]) == 1 and not MATH_FONT.search(base["font"])
                            and bx0 - 0.5 <= centre <= bx1 + 0.5 and ay1 <= by1 + 0.5):
                        continue
                    composed = unicodedata.normalize("NFC", base["text"] + mark)
                    if len(composed) != 1:
                        continue
                    base["text"] = composed
                    base["bbox"] = (min(ax0, bx0), min(ay0, by0), max(ax1, bx1), max(ay1, by1))
                    dropped.add(gid)
                    if spacer is not None:
                        dropped.add(spacer)
                    # A kern on the other side of the accent (IT ˆO) is not a word space.
                    other = index - step
                    if 0 <= other < len(line) and by_id[line[other]]["text"].isspace():
                        kern = by_id[line[other]]
                        beyond = other - step
                        if (kern["bbox"][2] - kern["bbox"][0] <= accent["size"] * 0.3
                                and 0 <= beyond < len(line) and by_id[line[beyond]]["text"].isalpha()):
                            dropped.add(line[other])
                    break
    if dropped:
        _drop_glyphs(glyphs, blocks, dropped)


def _drop_glyphs(glyphs: list[dict[str, Any]], blocks: list[dict[str, Any]], dropped: set[str]) -> None:
    glyphs[:] = [g for g in glyphs if g["id"] not in dropped]
    for block in blocks:
        block["lines"] = [[gid for gid in line if gid not in dropped] for line in block["lines"]]
        block["lines"] = [line for line in block["lines"] if line]


def _compose_combining_marks(glyphs: list[dict[str, Any]], blocks: list[dict[str, Any]]) -> None:
    """Fold a zero-width combining mark into the glyph it negates (TeX's negation slash: U+0338 + U+2208 -> U+2209).

    MuPDF's text extraction never advances the pen for a nonspacing mark: the mark is
    appended at the end of the *previous* glyph with an empty box, although the page
    draws it (and the SVG places its <use>) at the origin of the relation it modifies.
    TeX sets the slash before the relation, after the thick space, so the base glyph follows
    the mark on the line; Unicode-ordered producers set it right after the base. The
    base keeps its own origin and box, which is where every path of the composite lives.
    Marks without a precomposed form keep the combining sequence as one glyph text.
    """
    import unicodedata

    by_id = {g["id"]: g for g in glyphs}
    dropped: set[str] = set()
    for block in blocks:
        for line in block["lines"]:
            for index, gid in enumerate(line):
                mark = by_id[gid]
                text = str(mark["text"])
                if len(text) != 1 or unicodedata.category(text) != "Mn" or mark["bbox"][2] - mark["bbox"][0] > 1e-3 or gid in dropped:
                    continue
                reach = mark["size"] * 1.5
                base = None
                for step in (1, -1):
                    position = index + step
                    while 0 <= position < len(line) and by_id[line[position]]["text"].isspace():
                        position += step
                    if not 0 <= position < len(line) or line[position] in dropped:
                        continue
                    candidate = by_id[line[position]]
                    if abs(candidate["origin"][0] - mark["origin"][0]) <= reach and str(candidate["text"]).strip():
                        base = candidate
                        break
                if base is None:
                    continue
                composed = unicodedata.normalize("NFC", base["text"] + text)
                base["text"] = composed if len(composed) == 1 else base["text"] + text
                dropped.add(gid)
    if dropped:
        _drop_glyphs(glyphs, blocks, dropped)
# Bare vector rules: thin relative to their width, no glyphs, no other drawing.
DECORATIVE_RULE_MAX_HEIGHT = 10.0
DECORATIVE_RULE_MIN_ASPECT = 3.0
# Prose words inside a displayed formula box up to this share of its inked glyphs
# are text operators/annotations of the formula, not a sentence beside it.
DISPLAY_PROSE_SHARE = 0.4
# Whitespace glyphs narrower than this fraction of the font size are kerns.
KERN_SPACE_EM = 0.2
# End-of-proof and similar tombstones are text symbols, not notation to crop.
QED_MARKERS = set("□■∎◻◼◇♦")


def _line_bullet_ids(lines: dict[str, list[dict[str, Any]]]) -> set[str]:
    """A bullet glyph that opens a line marks a list item, even in a symbol font."""
    ids: set[str] = set()
    for line in lines.values():
        inked = [g for g in line if inked_glyph(g)]
        if is_bullet_line(inked):
            ids.add(inked[0]["id"])
    return ids
# Prose punctuation that a detector rectangle or font-metric overlap can drag into a formula.
# The exclamation mark is absent on purpose: TeX sets the factorial in the same text face.
EDGE_PROSE_PUNCTUATION = set("\u201c\u201d\u2018\u2019\"',;:.? ")


def _bold_variable_ids(lines: dict[str, list[dict[str, Any]]]) -> set[str]:
    """Single bold letters inside ordinary prose are mathematical notation (vectors, matrices).

    A bold word of three or more letters, or any letter on a predominantly bold line
    (a heading or run-in label), stays prose.
    """
    ids: set[str] = set()
    for line in lines.values():
        # Bold phrases: bold letter runs separated only by spaces or punctuation
        # in the bold font ("modeling problems:"). A phrase containing a bold word
        # is emphasis or a heading; a phrase of isolated letters is notation.
        phrases: list[tuple[list[list[dict[str, Any]]], bool]] = []
        phrase: list[list[dict[str, Any]]] = []
        run: list[dict[str, Any]] = []
        key_like = False
        inked = [g for g in line if not g["text"].isspace()]
        for glyph in [*line, {"text": "\u0000", "font": ""}]:
            bold = bool(BOLD_FONT.search(glyph["font"]))
            if glyph["text"].isalpha() and bold:
                run.append(glyph)
                continue
            if run:
                # Bold letters flush against bold digits on the same baseline are a
                # citation key ("KP92", "E11"), not a vector with an index.
                if (glyph["text"].isdigit() and bold and abs(glyph.get("baseline", 0) - run[-1].get("baseline", 0)) < 1
                        and abs(glyph.get("size", 10) - run[-1].get("size", 10)) < 0.5):
                    key_like = True
                phrase.append(run)
                run = []
            if not (bold or glyph["text"].isspace()) or glyph["text"] == "\u0000":
                if phrase:
                    # A bold phrase inside square brackets is a citation list.
                    first, last = phrase[0][0], phrase[-1][-1]
                    position = {g["id"]: index for index, g in enumerate(inked)}
                    before = inked[position[first["id"]] - 1]["text"] if position.get(first["id"], 0) > 0 else ""
                    after = [g["text"] for g in inked[position[last["id"]] + 1:]]
                    closing = next((t for t in after if not (t.isdigit() or t in ",;")), "")
                    phrases.append((phrase, key_like or (before == "[" and closing == "]")))
                phrase = []
                key_like = False
        for phrase, key in phrases:
            if not key and all(len(word) <= 2 for word in phrase):
                ids.update(g["id"] for word in phrase for g in word)
    return ids


def _trim_prose_edges(run: list[dict[str, Any]], line: list[dict[str, Any]],
                      balance: list[dict[str, Any]] | None = None,
                      following: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Drop quotation marks, sentence punctuation, unbalanced prose brackets and hyphens
    joining prose words from the ends of a mathematical run; those glyphs belong to the
    surrounding prose.

    ``balance`` is the whole expression the run is part of when the run is only the part
    of it on one native line (a superscript MuPDF put in the next block splits ``O(n^{-1/2})``
    into two lines): brackets are balanced over the expression, neighbours are found on
    the line. ``following`` is the first inked glyph of the next native line of the same
    block: a comma closing the line stays in a formula that continues on that line when
    the two lines are one printed row, and a text-face hyphen closing the line before a
    lowercase word joins that word (a prose compound such as ``σ-algebra``), so it is
    trimmed to the prose.
    """
    positions = {g["id"]: index for index, g in enumerate(line)}
    last_inked = next((g["id"] for g in reversed(line) if inked_glyph(g)), None)
    expression = balance if balance is not None else run
    trimmed: set[str] = set()

    def neighbour(glyph: dict[str, Any], step: int) -> dict[str, Any] | None:
        index = positions.get(glyph["id"])
        if index is None or not 0 <= index + step < len(line):
            return None
        return line[index + step]

    def prose_side(other: dict[str, Any] | None) -> bool:
        # A bracket at the line edge may close or open an expression on the next line.
        return other is not None and (other["text"].isspace() or other["text"].isalpha() or other["text"] in ".,;:")

    def notation(glyph: dict[str, Any] | None) -> bool:
        return glyph is not None and bool(MATH_FONT.search(glyph["font"]) or MATH_CHAR.search(glyph["text"]))

    def prose_edge(glyph: dict[str, Any], step: int) -> bool:
        text = glyph["text"]
        if step == 1 and text in ".,;:":
            # Sentence punctuation closing the run belongs to the prose even when
            # the PDF sets it in the mathematical font. A comma or colon in the math
            # face closing a native line inside an expression that continues on the
            # next native line of the same printed row (MuPDF splits a row at a tall
            # glyph) is the expression's own; at a real line break it is a list comma.
            after = neighbour(glyph, 1)
            if (after is None and following is not None and text != "." and MATH_FONT.search(glyph["font"])
                    and notation(following)
                    and abs(following.get("baseline", 0) - glyph.get("baseline", 0)) < 0.5 * glyph.get("size", 10)):
                return False
            return after is None or after["text"].isspace() or after["text"].isalpha()
        if MATH_FONT.search(glyph["font"]) and not text.isspace():
            return False
        if text in EDGE_PROSE_PUNCTUATION:
            return True
        # TeX sets mathematical parentheses in the text face too, so only the balance of
        # the run tells a prose bracket ("(the space L^p(Ω))") from a formula's own.
        # Intervals count all bracket kinds together; a bracket of an expression that
        # continues from or onto another line is legitimately unbalanced.
        openers = sum(g["text"] in OPENING_BRACKETS for g in expression if g["id"] not in trimmed)
        closers = sum(g["text"] in CLOSING_BRACKETS for g in expression if g["id"] not in trimmed)
        if step == 1 and text in CLOSING_BRACKETS:
            return closers > openers and prose_side(neighbour(glyph, 1))
        if step == -1 and text in OPENING_BRACKETS:
            return openers > closers and run[-1]["id"] != last_inked and prose_side(neighbour(glyph, -1))
        if text == "-":
            other = neighbour(glyph, step)
            if other is None and step == 1 and following is not None:
                return bool(following["text"].isalpha() and following["text"].islower() and not MATH_FONT.search(following["font"]))
            return bool(other and other["text"].isalpha() and not MATH_FONT.search(other["font"]))
        return False

    changed = True
    while changed and run:
        changed = False
        if prose_edge(run[0], -1):
            trimmed.add(run[0]["id"])
            run = run[1:]
            changed = True
        if run and prose_edge(run[-1], 1):
            trimmed.add(run[-1]["id"])
            run = run[:-1]
            changed = True
    # Delimiters stay: intervals, function arguments and expressions continuing
    # on the next line are legitimately unbalanced within one crop.
    return run
def _operator_name(text: str) -> bool:
    """Whether a text-face prefix is a known operator name (``log``, ``limsup``, ``dim``)."""
    match = re.fullmatch(r"([A-Za-z]{2,})([0-9([{}.*+/-]*)", text)
    return match is not None and match[1].lower() in MATH_OPERATORS


def _operator_word_ids(lines: dict[str, list[dict[str, Any]]], mathematical: Any) -> set[str]:
    """Glyphs of text-face operator names that are applied to notation on their line.

    ``lim`` before ``ε → 0``, ``log`` before ``c_ε`` or ``max`` before ``{`` is part of the
    formula around it; ``the log of`` is prose. The name must be a known operator of two
    or more letters, and the next inked glyph after it (a TeX word space between them is
    allowed) must be notation or an opening bracket.
    """
    ids: set[str] = set()
    for line in lines.values():
        index = 0
        while index < len(line):
            glyph = line[index]
            if not (glyph["text"].isalpha() and not MATH_FONT.search(glyph["font"])):
                index += 1
                continue
            end = index
            while end < len(line) and line[end]["text"].isalpha() and not MATH_FONT.search(line[end]["font"]):
                end += 1
            word = line[index:end]
            following = next((g for g in line[end:] if not g["text"].isspace()), None)
            if (_operator_name("".join(g["text"] for g in word)) and following is not None
                    and (mathematical(following) or following["text"] in OPENING_BRACKETS)):
                ids.update(g["id"] for g in word)
            index = end
    return ids


def _operator_prefix(text: str) -> bool:
    """Whether a text-face prefix belongs to the notation that follows it.

    A single letter (``U(N)``), an operator name (``diag(``, ``limsup``) or any name set
    flush against its argument's opening bracket (``Cov(``, ``mean(``, ``Prob(``) is
    notation; digits, brackets and arithmetic signs may follow it (``2π``, ``(x``).
    """
    match = re.fullmatch(r"([A-Za-z]*)([0-9([{}.*+/-]*)", text)
    if match is None:
        return False
    name, tail = match[1], match[2]
    if len(name) <= 1:
        return True
    return name.lower() in MATH_OPERATORS or (bool(tail) and tail[0] in OPENING_BRACKETS)


KINDS = {"math", "figure", "table", "code", "mixed-region"}


def _hash(value: Any) -> str:
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _path(root: Path, relative: str) -> Path:
    result = (root / relative).resolve()
    if not result.is_relative_to(root.resolve()):
        raise ValueError("evidence path escapes project")
    return result


def _page_path(root: Path, page: int) -> Path:
    return root / f"derived/fidelity-pages/p{page:04d}.json"


@contextmanager
def _authority_transaction(root: Path, pages: list[int]) -> Iterator[None]:
    paths = [root / "derived/units.jsonl", root / "derived/fidelity-assets.jsonl"]
    paths += [root / "translations/current.jsonl", root / "translations/source-retired.jsonl"]
    paths += [root / f"evidence/pages/fidelity-p{p:04d}.png" for p in pages]
    paths += [path for p in pages for path in (root / "evidence/pages").glob(f"fidelity-p{p:04d}-overflow-*.png")]
    paths += [_page_path(root, p) for p in pages]
    paths += [root / f"evidence/pages/fidelity-p{p:04d}.review.json" for p in pages]
    paths += [root / "evidence/audits" / f"{p.parent.name}.invalidations.json" for p in (root / "batches").glob("*/manifest.yaml")]
    snapshots = snapshot_files(paths)
    try:
        yield
    except BaseException:
        restore_files(snapshots)
        raise


def _box(values: Any) -> tuple[float, float, float, float]:
    return tuple(round(float(v), 4) for v in values)  # type: ignore[return-value]


def _union(boxes: list[Any]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _inside(glyph: dict[str, Any], bbox: Any) -> bool:
    b = glyph["bbox"]
    return bool(bbox[0] <= (b[0] + b[2]) / 2 <= bbox[2] and bbox[1] <= (b[1] + b[3]) / 2 <= bbox[3])


def _intersects(a: Any, b: Any) -> bool:
    return bool(max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3]))


def _native(page: fitz.Page) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    glyphs, blocks = [], []
    for bi, block in enumerate(page.get_text("rawdict")["blocks"]):
        if block["type"] != 0:
            continue
        lines = []
        for li, line in enumerate(block["lines"]):
            ids = []
            for si, span in enumerate(line["spans"]):
                for ci, char in enumerate(span["chars"]):
                    gid = f"b{bi}-l{li}-s{si}-c{ci}"
                    # Typographic ligatures are one glyph but several letters of the text.
                    glyphs.append({"id": gid, "text": LIGATURES.get(char["c"], char["c"]), "bbox": _box(char["bbox"]), "font": span["font"], "size": span["size"], "origin": list(char["origin"]), "baseline": char["origin"][1], "line": f"b{bi}-l{li}"})
                    ids.append(gid)
            lines.append(ids)
        blocks.append({"id": f"b{bi}", "bbox": _box(block["bbox"]), "lines": lines})
    _compose_accents(glyphs, blocks)
    _compose_combining_marks(glyphs, blocks)
    return glyphs, blocks


def _prose_boundary_ids(glyphs: list[dict[str, Any]]) -> set[str]:
    """Protect evidenced prose delimiters/citation keys, not unbalanced math.

    Work across native lines in each block. A normal-font delimiter is not
    sufficient evidence alone: mathematical intervals and continued expressions
    commonly use the same font as prose.
    """
    return _prose_boundaries(glyphs)[0]


def _prose_boundaries(glyphs: list[dict[str, Any]]) -> tuple[set[str], list[tuple[set[str], set[str]]]]:
    """The protected glyph ids, and each protected bracket pair with the language glyphs it encloses."""
    protected: set[str] = set()
    pairs: list[tuple[set[str], set[str]]] = []
    separate_roman_math = sum(g["font"].upper().startswith("SF") for g in glyphs) > len(glyphs) * 0.2

    def math_font(glyph: dict[str, Any]) -> bool:
        return bool(MATH_FONT.search(glyph["font"]) or (separate_roman_math and re.match(r"CM(?:R|BX)\d", glyph["font"], re.I)))

    blocks: dict[str, list[dict[str, Any]]] = {}
    for glyph in glyphs:
        blocks.setdefault(glyph["line"].split("-l")[0], []).append(glyph)
    for block in blocks.values():
        text = "".join(g["text"] for g in block)
        # Native glyphs normally encode one character, but retain offset mapping
        # for ligatures/multi-character recovery values as well.
        offsets = [g for g in block for _ in g["text"]]
        for match in re.finditer(r"\[(?:[A-Za-z]{2,}\+?\d{2,4}[a-z]?)(?:[,;]\s*[A-Za-z]{2,}\+?\d{2,4}[a-z]?)*\]", text):
            candidate = offsets[match.start():match.end()]
            # A superscript citation '+' may use CMR even with SF prose.
            # Mathematical letters in a similar-looking expression veto this.
            if all(not math_font(g) for g in candidate if g["text"] != "+"):
                protected.update(g["id"] for g in candidate)
        stack: list[int] = []
        for index, glyph in enumerate(block):
            char = glyph["text"]
            if char in {"(", "[", "{"}:
                stack.append(index)
            elif char in {")", "]", "}"} and stack:
                start = stack.pop()
                opening = block[start]
                if opening["text"] != {")": "(", "]": "[", "}": "{"}[char]:
                    stack.clear()
                    continue
                # Attached function arguments, including textual labels such as
                # Cost(classical), remain mathematical candidate evidence.
                if start and not block[start - 1]["text"].isspace():
                    continue
                if math_font(opening) or math_font(glyph):
                    continue
                inside = "".join(g["text"] if not math_font(g) else " " for g in block[start + 1:index])
                if language_words(inside):
                    protected.update((opening["id"], glyph["id"]))
                    pairs.append(({opening["id"], glyph["id"]},
                                  {g["id"] for g in block[start + 1:index] if g["text"].isalpha() and not math_font(g)}))
    return protected, pairs


def _boundary_diagnostics(glyphs: list[dict[str, Any]], assets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    protected, pairs = _prose_boundaries(glyphs)
    diagnostics = []
    owners = {gid: asset["id"] for asset in assets for fragment in asset["fragments"] for gid in fragment["glyph_ids"]}
    for asset in assets:
        if asset["kind"] != "math":
            continue
        ids = {gid for fragment in asset["fragments"] for gid in fragment["glyph_ids"]}
        # A bracket pair whose words the asset declares as formula conditions ("(mod m)",
        # "(a.s.)") is language the record already accounts for, not a prose boundary.
        declared = {gid for condition in asset.get("formula_conditions") or [] for gid in condition["glyph_ids"]}
        accounted = {gid for delimiters, inner in pairs if inner and inner <= declared for gid in delimiters}
        contaminated = [g["id"] for g in glyphs if g["id"] in (ids & protected) - accounted]
        if contaminated:
            diagnostics.append({"code": "prose-boundary-in-math", "asset_id": asset["id"], "glyph_ids": contaminated,
                                "action": "Inspect original prose/citation context and correct glyph ownership before approval."})
        if not asset.get("display"):
            continue
        # A symbol-face glyph inside a displayed formula's crop but owned elsewhere leaves a
        # hole in the export (the explicit export draws owned paths only). The crop box is
        # padded ink; a glyph counts only inside the ink and on one of the asset's own rows
        # (its baseline near an owned glyph's), so the descender of the line above, whose
        # padding meets the padding of this crop, is not a hole.
        rows = [g.get("baseline", g["bbox"][3]) for g in glyphs if g["id"] in ids and inked_glyph(g)]
        holes = [g["id"] for g in glyphs
                 if g["id"] not in ids and owners.get(g["id"]) and inked_glyph(g) and MATH_FONT.search(g["font"])
                 and any(_inside(g, [b[0] + 0.5, b[1] + 0.5, b[2] - 0.5, b[3] - 0.5]) for b in (f["bbox"] for f in asset["fragments"]))
                 and any(abs(g.get("baseline", g["bbox"][3]) - row) <= 0.6 * g.get("size", 10) for row in rows)]
        if holes:
            diagnostics.append({"code": "math-ink-outside-ownership", "asset_id": asset["id"], "glyph_ids": holes,
                                "action": "Notation inside this display crop belongs to another asset; merge the regions or correct ownership before approval."})
    return diagnostics


def _strip_display_prose(owned: list[dict[str, Any]], prose_ids: set[str],
                         others: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Cut a prose phrase set off by a horizontal gap from a displayed formula box.

    "X(t) = ...   for all times t > 0." keeps the formula and returns the phrase
    (with its own inline notation) to the paragraph. Prose that touches the notation
    ("m-dimensional", "(= space of n x m matrices)") is left alone, so the caller
    treats the whole line as native text with inline assets.

    ``others`` are the notation glyphs of the box's other visual lines: a phrase set
    off from the formula sits beside all of it, whereas a fraction denominator or a
    case label ("vol(B)" under a fraction bar, "0, otherwise.") sits inside the
    formula's horizontal extent and is part of it however wide the gap before it.
    """
    prose = [g for g in owned if g["id"] in prose_ids]
    mathematical = [g["id"] for g in owned if MATH_FONT.search(g["font"]) or MATH_CHAR.search(g["text"])]
    if not prose or not mathematical:
        return owned
    sizes = sorted(g.get("size", 10) for g in owned)
    gap = sizes[len(sizes) // 2] * 1.2
    left_edge = min(g["bbox"][0] for g in prose)
    right_edge = max(g["bbox"][2] for g in prose)
    for side in ("head", "tail"):
        if side == "head":
            kept = [g for g in owned if g["bbox"][2] <= left_edge - gap]
            between = [g for g in owned if g not in kept and g["bbox"][0] < left_edge and g["id"] not in prose_ids]
        else:
            kept = [g for g in owned if g["bbox"][0] >= right_edge + gap]
            between = [g for g in owned if g not in kept and g["bbox"][2] > right_edge and g["id"] not in prose_ids]
        kept_math = sum(1 for g in kept if g["id"] in mathematical)
        if kept and not between and kept_math >= len(mathematical) * 0.6 and not any(g["id"] in prose_ids for g in kept):
            phrase = [g for g in owned if g not in kept]
            x0, x1 = min(g["bbox"][0] for g in phrase), max(g["bbox"][2] for g in phrase)
            if any(o["bbox"][0] < x1 and o["bbox"][2] > x0 for o in others or []):
                return owned
            # Notation on the far side of the words, set off from them by its own gap
            # ("= νQ,   or equivalently   ..."), makes the words a connective between two
            # formulas of one display, not a phrase beside it.
            beyond = [g for g in phrase if g["id"] in mathematical
                      and (g["bbox"][2] <= left_edge - gap if side == "tail" else g["bbox"][0] >= right_edge + gap)]
            if beyond:
                return owned
            return kept
    return owned


def _left_margin(glyphs: list[dict[str, Any]]) -> float:
    """The prose left margin: the most common pen origin of a native line opened by a text-face
    glyph.

    The origin, not the ink: a line's first ink starts where its first letter's side bearing
    puts it, so the ink x of flush prose lines scatters over a few tenths of a point while the
    pieces of one stretched brace, each a native line of its own, share an x exactly and would
    outvote the prose (LT-079). Lines opened by notation are left out for the same reason;
    a page without a text-opened line falls back to every line.
    """
    firsts: dict[str, dict[str, Any]] = {}
    for g in glyphs:
        if inked_glyph(g) and g["line"] not in firsts:
            firsts[g["line"]] = g
    text_opened = [g for g in firsts.values() if not MATH_FONT.search(g["font"])]
    starts = Counter(round((g.get("origin") or g["bbox"])[0], 1) for g in (text_opened or firsts.values()))
    return starts.most_common(1)[0][0] if starts else 0.0


def _rule_boxes(page: Any) -> list[list[float]]:
    """The page's horizontal rules (fraction bars, over- and underlines): thin, wide drawings."""
    if not hasattr(page, "get_drawings"):
        return []
    boxes: list[list[float]] = []
    for drawing in page.get_drawings():
        rect = drawing["rect"]
        height = float(rect.y1 - rect.y0)
        if height <= 1.5 and float(rect.x1 - rect.x0) >= 4 * max(height, 0.1):
            boxes.append([float(rect.x0), float(rect.y0), float(rect.x1), float(rect.y1)])
    return boxes


def _delimiter_columns(owned: list[dict[str, Any]]) -> list[list[float]]:
    """The ink columns of the stretched delimiters a box owns: pieces that overlap in x and
    touch in y are one delimiter, so a row bracketed by its extender pieces is bracketed by
    the whole (a case row's baseline need not fall inside any one piece)."""
    pieces = sorted((list(g["bbox"]) for g in owned if inked_glyph(g) and (_delimiter_piece(g) or "cmex" in g["font"].lower())),
                    key=lambda box: (box[0], box[1]))
    columns: list[list[float]] = []
    for box in pieces:
        for column in columns:
            if min(box[2], column[2]) - max(box[0], column[0]) > 0 and box[1] <= column[3] + 1.0 and box[3] >= column[1] - 1.0:
                column[:] = [min(column[0], box[0]), min(column[1], box[1]), max(column[2], box[2]), max(column[3], box[3])]
                break
        else:
            columns.append(box)
    return columns


def _visual_lines(glyphs: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Cluster glyphs by baseline; sub/superscripts stay with their line, sum limits do not."""
    lines: list[list[dict[str, Any]]] = []
    for g in sorted(glyphs, key=lambda g: (g.get("baseline", g["bbox"][3]), g["bbox"][0])):
        baseline = g.get("baseline", g["bbox"][3])
        if lines and abs(baseline - lines[-1][0].get("baseline", lines[-1][0]["bbox"][3])) <= g.get("size", 10) * 0.6:
            lines[-1].append(g)
        else:
            lines.append([g])
    return [sorted(line, key=lambda g: g["bbox"][0]) for line in lines]


def _display_box_owned(owned: list[dict[str, Any]], prose_ids: set[str], margin: float,
                       rules: list[list[float]] | tuple[()] = ()) -> tuple[list[dict[str, Any]], bool, set[str]]:
    """Decide what a detector display box owns when it also contains prose words.

    Works per visual line (baseline), because the detector rectangle may overshoot
    into the paragraph above or below. ``rules`` are the page's horizontal rules
    (``_rule_boxes``). Returns the formula glyphs, whether prose remains outside the
    formula, and the glyphs of lines that must stay displayed lines of native text
    around the formula:

    - a phrase set off by a gap ("X(t) = ...   for all times t > 0.") beside the
      whole formula returns to the line, which keeps its displayed position;
    - a row inside the vertical span of a stretched delimiter the box owns is a
      case of the formula ("0, otherwise.") and stays whole, its words declared
      as formula conditions; so is a row a fraction bar spans directly above or
      below it (the numerator "surface area(U)", a denominator "vol(B)");
    - a few words inside the notation ("sup over Y simple", "{terms of order ...
      and higher}") are text operators or annotations and stay in the formula;
    - a line that is mostly prose is not part of the formula: at the left margin
      it is the paragraph the rectangle overshot into; set off from the margin it
      is a displayed line of native text with inline notation.
    """
    kept: list[dict[str, Any]] = []
    displayed: set[str] = set()
    outside = False
    undecided: list[tuple[list[dict[str, Any]], int, int]] = []
    lines = _visual_lines(owned)

    def notation(glyph: dict[str, Any]) -> bool:
        return bool(MATH_FONT.search(glyph["font"]) or MATH_CHAR.search(glyph["text"]))

    # Ink columns of the delimiters the box owns: rows they bracket belong to the formula.
    spans = _delimiter_columns(owned)
    for index, line in enumerate(lines):
        inked = [g for g in line if inked_glyph(g)]
        prose = [g for g in inked if g["id"] in prose_ids]
        if not prose:
            kept.extend(line)
            continue
        others = [g for position, other in enumerate(lines) if position != index for g in other if notation(g)]
        stripped = _strip_display_prose(line, prose_ids, others)
        if len(stripped) < len(line):
            kept.extend(stripped)
            outside = True
            displayed.update(g["id"] for g in line)
            continue
        size = max(g.get("size", 10) for g in inked)
        if min(g["bbox"][0] for g in inked) <= margin + size * 2.8 and len(prose) > DISPLAY_PROSE_SHARE * len(inked):
            outside = True
            continue
        baseline = sorted(g.get("baseline", g["bbox"][3]) for g in inked)[len(inked) // 2]
        top, bottom = min(g["bbox"][1] for g in inked), max(g["bbox"][3] for g in inked)
        x0, x1 = min(g["bbox"][0] for g in inked), max(g["bbox"][2] for g in inked)
        barred = any(
            rule[0] - 1 <= x0 and x1 <= rule[2] + 1
            and (0 <= rule[1] - bottom <= size * 0.6 or 0 <= top - rule[3] <= size * 0.6)
            for rule in rules
        )
        if barred or any(span[1] <= baseline <= span[3] for span in spans):
            kept.extend(line)
            continue
        undecided.append((line, len(inked), len(prose)))
    # Words inside the notation are judged against the formula as a whole (a
    # multi-line derivation with one annotated line), then line by line.
    total = sum(1 for g in kept if inked_glyph(g)) + sum(n for _, n, _ in undecided)
    if undecided and sum(p for _, _, p in undecided) <= DISPLAY_PROSE_SHARE * total:
        for line, _, _ in undecided:
            kept.extend(line)
        undecided = []
    for line, n, p in undecided:
        if p <= DISPLAY_PROSE_SHARE * n:
            kept.extend(line)
        else:
            outside = True
            displayed.update(g["id"] for g in line)
    return kept, outside, displayed


def _prose_word_ids(glyphs: list[dict[str, Any]]) -> set[str]:
    """Glyphs of ordinary words: two or more letters in a non-mathematical font."""
    ids: set[str] = set()
    lines: dict[str, list[dict[str, Any]]] = {}
    for glyph in glyphs:
        lines.setdefault(glyph["line"], []).append(glyph)
    for line in lines.values():
        word: list[dict[str, Any]] = []
        for glyph in [*line, {"text": " ", "font": ""}]:
            if glyph["text"].isalpha() and not MATH_FONT.search(glyph["font"]):
                word.append(glyph)
            else:
                if len(word) >= 2 and "".join(g["text"] for g in word).lower() not in MATH_OPERATORS:
                    ids.update(g["id"] for g in word)
                word = []
    return ids


def _tag_glyph_ids(glyphs: list[dict[str, Any]]) -> set[str]:
    """Glyphs of native lines that are a printed equation label and nothing else.

    A label set beside a display (``(3.11)``, at either margin) is neither notation nor
    a continuation of the formula on the line above: its ink is owned by no asset and
    its number binds to the display as ``equation_number``.
    """
    lines: dict[str, list[dict[str, Any]]] = {}
    for glyph in glyphs:
        lines.setdefault(glyph["line"], []).append(glyph)
    ids: set[str] = set()
    for line in lines.values():
        text = "".join(g["text"] for g in line if not g["text"].isspace())
        if re.fullmatch(EQUATION_LABEL, text) and all(not MATH_FONT.search(g["font"]) for g in line if inked_glyph(g)):
            ids.update(g["id"] for g in line)
    return ids


def _accent_glyph_ids(lines: dict[str, list[dict[str, Any]]], mathematical: Any) -> set[str]:
    """Text-face accent glyphs (``ˆ``, ``¯``) set over a mathematical base on their line.

    TeX draws a math accent as a separate glyph before or after its base in the text
    layer; the base joins the run, so the accent must too, or the record keeps a stray
    ``ˆ`` in the prose next to a crop of the bare base.
    """
    ids: set[str] = set()
    for line in lines.values():
        for index, glyph in enumerate(line):
            if glyph["text"] not in SPACING_ACCENTS or MATH_FONT.search(glyph["font"]):
                continue
            x0, _, x1, _ = glyph["bbox"]
            for step in (1, -1):
                position = index + step
                while 0 <= position < len(line) and line[position]["text"].isspace():
                    position += step
                if not 0 <= position < len(line):
                    continue
                base = line[position]
                if mathematical(base) and base["bbox"][0] - 0.5 <= (x0 + x1) / 2 <= base["bbox"][2] + 0.5:
                    ids.add(glyph["id"])
                    break
    return ids


def _next_line_openers(lines: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    """The first inked glyph of the native line after each line of the same block."""
    openers: dict[str, dict[str, Any]] = {}
    for key in lines:
        match = re.fullmatch(r"(.*)-l(\d+)", key)
        if match is None:
            continue
        following = lines.get(f"{match[1]}-l{int(match[2]) + 1}")
        opener = next((g for g in following or [] if inked_glyph(g)), None)
        if opener is not None:
            openers[key] = opener
    return openers


def _split_display_at_tags(region: dict[str, Any], owned: list[dict[str, Any]],
                           tag_rows: list[float]) -> tuple[list[list[dict[str, Any]]], list[float]]:
    """Cut a display box that spans several labelled displays into one part per label.

    The detector may box two stacked displays, each with its own printed label, as one
    formula. Between two label rows the cut goes through the widest horizontal gap no
    owned ink crosses; a box whose ink spans both labels (one tall matrix) stays whole.
    Returns the parts and the y of each cut (no delimiter column is chained across one).
    """
    rows = sorted(t for t in tag_rows if region["bbox"][1] - 2 <= t <= region["bbox"][3] + 2)
    if len(rows) < 2:
        return [owned], []
    inked = sorted((g for g in owned if inked_glyph(g)), key=lambda g: g["bbox"][1])
    parts: list[list[dict[str, Any]]] = []
    cuts: list[float] = []
    remaining = list(owned)
    for upper, lower in zip(rows, rows[1:], strict=False):
        best: tuple[float, float] | None = None
        boxes = [g["bbox"] for g in inked if g["bbox"][3] > upper and g["bbox"][1] < lower]
        edges = sorted({b[3] for b in boxes} | {b[1] for b in boxes})
        for bottom, top in zip(edges, edges[1:], strict=False):
            if top - bottom <= 0.5 or not upper < (bottom + top) / 2 < lower:
                continue
            if any(b[1] < top and b[3] > bottom for b in boxes):
                continue
            if best is None or top - bottom > best[0]:
                best = (top - bottom, (bottom + top) / 2)
        if best is None:
            continue
        cut = best[1]
        above = [g for g in remaining if (g["bbox"][1] + g["bbox"][3]) / 2 < cut]
        remaining = [g for g in remaining if (g["bbox"][1] + g["bbox"][3]) / 2 >= cut]
        if above:
            parts.append(above)
            cuts.append(cut)
    if remaining:
        parts.append(remaining)
    return (parts, cuts) if len(parts) > 1 else ([owned], [])


def _display_line_glyph_ids(glyphs: list[dict[str, Any]], layout: list[dict[str, Any]],
                            rules: list[list[float]] | tuple[()] = ()) -> set[str]:
    """Glyphs of detector display formulas that also contain prose words.

    Such a line ("B : R^n -> M (= space of n x m matrices)", "X(t) = ...  for all
    times t > 0.") is one displayed unit of native text with inline or display
    assets; it keeps its own structural position instead of being merged into the
    surrounding paragraph. Only lines that carry notation qualify, so a prose line
    the detector rectangle overshoots into is left to its paragraph.
    """
    prose = _prose_word_ids(glyphs)
    margin = _left_margin(glyphs)
    tags = _tag_glyph_ids(glyphs)
    ids: set[str] = set()
    for item in layout:
        if item.get("label") != "display_formula":
            continue
        box = [float(v) / 2 for v in item["bbox"]]
        owned = [g for g in glyphs if inked_glyph(g) and _inside(g, box) and g["id"] not in tags]
        if owned and any(g["id"] in prose for g in owned):
            ids.update(_display_box_owned(owned, prose, margin, rules)[2])
    return ids


# Unicode bracket pieces (⎧ ⎪ ⎨ ⎩ ⎛ ⎜ ⎝ ...) and the CMEX10 slots 0x30-0x47 that hold
# delimiter halves and extenders; CMEX has no digits, so a "digit" in it is a piece.
_DELIMITER_PIECE = re.compile(r"[⎛-⎭⎰⎱]")
_CMEX_PIECE_SLOTS = set("0123456789:;<=>?@ABCDEFG")
# A delimiter or one of its pieces is tall and narrow; a big operator (∑, ∏, ∫) is not.
_DELIMITER_ASPECT = 1.5
# A delimiter whose ink spans at least this many font sizes brackets rows of its own
# (a cases block, a matrix), not just the line it stands on.
_ROW_DELIMITER_HEIGHT_EM = 2.0
# A horizontal gap this wide ends a bracketed row: what follows is an equation number or
# running text, not the row's condition.
_ROW_GAP_EM = 3.0


def _delimiter_piece(glyph: dict[str, Any]) -> bool:
    """A stretched delimiter or a piece of one.

    A subset font re-encoded by the PDF producer maps CMEX glyphs to arbitrary codes, so a
    piece may decode to a control character rather than a known slot; then only its shape
    (tall and narrow) tells it from a big operator in the same font.
    """
    text = str(glyph["text"])
    if len(text) != 1:
        return False
    if _DELIMITER_PIECE.fullmatch(text):
        return True
    if "cmex" not in glyph["font"].lower():
        return False
    if text in _CMEX_PIECE_SLOTS:
        return True
    x0, y0, x1, y1 = glyph["bbox"]
    return not text.isprintable() and (y1 - y0) >= _DELIMITER_ASPECT * max(x1 - x0, 1e-6)


def _merge_delimiter_pieces(regions: list[dict[str, Any]], glyphs: list[dict[str, Any]],
                            cuts: list[float] | None = None) -> list[dict[str, Any]]:
    """Keep every piece of one stretched delimiter in one region.

    TeX builds a tall brace or bracket from a top, extenders and a bottom on
    separate baselines. Native runs are scanned per PDF line, so the corner pieces
    land in the display box and the extenders in their own runs; the export then
    draws the brace in two crops and the reading order splits its cases. ``cuts``
    are the y values where a detector box was cut into separate labelled displays:
    the whole braces of two stacked cases blocks are not one column.
    """
    # A column is stacked pieces at one x: each piece starts where the previous one ends
    # (within a small gap or overlap). Two integral signs on consecutive display lines at
    # the same x are not a column, whatever the sort order puts next to them.
    pieces = sorted((g for g in glyphs if _delimiter_piece(g)), key=lambda g: (g["bbox"][1], g["bbox"][0]))
    columns: list[list[dict[str, Any]]] = []
    for glyph in pieces:
        size = glyph.get("size", 10)
        column = next((c for c in columns if abs(glyph["bbox"][0] - c[-1]["bbox"][0]) <= 1.5
                       and -0.2 * size <= glyph["bbox"][1] - c[-1]["bbox"][3] <= max(size, c[-1].get("size", 10)) * 0.6
                       and not any(c[-1]["bbox"][3] <= cut <= glyph["bbox"][1] for cut in cuts or [])), None)
        if column is not None:
            column.append(glyph)
        else:
            columns.append([glyph])
    for column in columns:
        if len(column) < 2:
            continue
        owner_index = {gid: i for i, region in enumerate(regions) for gid in region.get("glyph_ids", [])}
        owners = sorted({owner_index[g["id"]] for g in column if g["id"] in owner_index})
        if not owners or (len(owners) == 1 and all(g["id"] in owner_index for g in column)):
            continue
        target = regions[owners[0]]
        ids = set(target["glyph_ids"]) | {g["id"] for g in column}
        boxes = [target["bbox"], *(g["bbox"] for g in column)]
        for index in owners[1:]:
            other = regions[index]
            ids |= set(other["glyph_ids"])
            boxes.append(other["bbox"])
            target["provenance"] = sorted(set(target["provenance"] + other["provenance"]))
            target["display"] = target["display"] or other["display"]
        target["glyph_ids"] = [g["id"] for g in glyphs if g["id"] in ids]
        target["bbox"] = _union(boxes)
        if "stretched-delimiter-merged" not in target["provenance"]:
            target["provenance"] = [*target["provenance"], "stretched-delimiter-merged"]
        regions = [region for i, region in enumerate(regions) if i not in owners[1:]]
    return regions


def _spaced_text(glyphs: list[dict[str, Any]]) -> str:
    """Glyph texts with a word space wherever TeX left a gap but no space glyph."""
    parts: list[str] = []
    for previous, glyph in zip([None, *glyphs], glyphs, strict=False):
        gap = glyph["bbox"][0] - previous["bbox"][2] if previous is not None else 0.0
        if (previous is not None and not previous["text"].isspace() and not glyph["text"].isspace()
                and gap >= max(glyph.get("size", 10), previous.get("size", 10)) * 0.25):
            parts.append(" ")
        parts.append(glyph["text"])
    return "".join(parts)


def _auto_formula_conditions(region: dict[str, Any], glyph_by_id: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Declare the words a displayed formula keeps inside its crop as formula conditions.

    Case labels ("if x < 0,", "otherwise.", "for any fixed k") are language the
    translator must render; an undeclared word inside an image is neither
    translatable nor visible to QA. Each visual line is cut at notation glyphs and
    every remaining segment with a word becomes one condition in native order, the
    contract ``_formula_condition_glyphs`` verifies. An upright operator name set
    flush against its argument (``Prob(``, ``Var``) is notation, not a condition.
    """
    owned = [glyph_by_id[gid] for gid in region.get("glyph_ids", []) if gid in glyph_by_id]
    order = {gid: i for i, gid in enumerate(glyph_by_id)}
    conditions: list[dict[str, Any]] = []
    for line in _visual_lines(owned):
        segment: list[dict[str, Any]] = []
        for glyph in [*line, None]:
            if glyph is not None and not (MATH_FONT.search(glyph["font"]) or MATH_CHAR.search(glyph["text"]) or glyph["text"].isdigit()):
                segment.append(glyph)
                continue
            # Brackets and spaces at the edges belong to the surrounding notation, as does
            # the punctuation that closes the notation before a condition ("1, if").
            while segment and (segment[0]["text"].isspace() or segment[0]["text"] in "()[]{},;:."):
                segment.pop(0)
            while segment and (segment[-1]["text"].isspace() or segment[-1]["text"] in "()[]{}"):
                segment.pop()
            text = _spaced_text(segment)
            words = language_words(text)
            if words and all(inked_glyph(g) or g["text"].isspace() for g in segment):
                size = max(g.get("size", 10) for g in segment)
                last = segment[-1]["bbox"]
                # The argument of an operator may sit on another baseline (a \left( piece).
                following = [g for g in owned if g not in segment and not g["text"].isspace() and g["bbox"][0] >= last[2] - 0.5
                             and g["bbox"][3] >= last[1] - size and g["bbox"][1] <= last[3] + size]
                after = min(following, key=lambda g: g["bbox"][0]) if following else None
                # Flush against notation, or opening a (possibly stretched CMEX) delimiter.
                flush = after is not None and after["bbox"][0] - last[2] < size * 0.2
                applied = after is not None and (flush or after["text"] in OPENING_BRACKETS or "cmex" in after["font"].lower())
                # A name flush against its argument's bracket is an operator whatever its
                # case ("vol(B)", "mean("); a condition word keeps its text-mode space ("if (").
                operator = after is not None and applied and len(words) == 1 and text == words[0] and (
                    words[0][0].isupper() or words[0].lower() in MATH_OPERATORS
                    or (flush and after["text"] in OPENING_BRACKETS))
                if not operator:
                    # The validator compares against native page order, not visual order.
                    segment.sort(key=lambda g: order[g["id"]])
                    conditions.append({"glyph_ids": [g["id"] for g in segment], "source_text": _spaced_text(segment)})
            segment = []
    conditions.sort(key=lambda c: order[c["glyph_ids"][0]])
    return conditions


def _regions(page: fitz.Page, glyphs: list[dict[str, Any]], layout: list[dict[str, Any]],
             ink: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    # Use original visible paths when available: TeX accents and radicals often
    # have misleading native metric rectangles on an adjacent line. ``ink`` is the
    # page's measured glyph ink when the caller already has it.
    unmeasured: set[str] = set()
    if ink is None and hasattr(page, "get_svg_image"):
        from littrans.glyph_export import glyph_ink_boxes
        ink = glyph_ink_boxes(page, glyphs)
    if ink is not None:
        # A glyph without a measured path keeps its metric box; the region records
        # that so a truncating crop is traceable instead of a silent fallback.
        unmeasured = {g["id"] for g in glyphs if inked_glyph(g) and g["id"] not in ink}
        glyphs = [{**g, "bbox": ink.get(g["id"], g["bbox"])} for g in glyphs]
    regions: list[dict[str, Any]] = []
    rules = _rule_boxes(page)
    # A damaged character does not make the surrounding paragraph opaque.
    for glyph in glyphs:
        if "\ufffd" in glyph["text"] or (any(ord(c) < 32 and c not in "\t\r\n" for c in glyph["text"]) and not MATH_FONT.search(glyph["font"])):
            regions.append({"kind": "mixed-region", "bbox": list(glyph["bbox"]), "glyph_ids": [glyph["id"]], "provenance": ["invalid-native-text:recovery-required"], "display": False, "grouping_pending": True})
    for item in layout:
        label = item.get("label", "")
        kind = "math" if "formula" in label and "number" not in label else "figure" if label in {"image", "figure", "chart", "header_image", "footer_image"} else label if label in {"table", "code"} else None
        if kind:
            regions.append({"kind": kind, "bbox": [float(v) / 2 for v in item["bbox"]], "provenance": [f"PP-DocLayoutV2:{label}"], "display": label != "inline_formula", "grouping_pending": False})
    # Native font and character candidates retain small isolated variables.
    lines: dict[str, list[dict[str, Any]]] = {}
    # Some LaTeX builds use T1 SF fonts for prose and OT1 CMR fonts only in
    # mathematical mode; normal-font operators otherwise escape symbol rules.
    separate_roman_math = sum(g["font"].upper().startswith("SF") for g in glyphs) > len(glyphs) * 0.2
    for glyph in glyphs:
        lines.setdefault(glyph["line"], []).append(glyph)
    # A printed equation label is never notation: it is owned by no asset and binds to
    # its display as equation_number.
    tags = _tag_glyph_ids(glyphs)
    protected_prose = _prose_boundary_ids(glyphs) | tags
    tag_rows = sorted({g.get("baseline", g["bbox"][3]) for g in glyphs if g["id"] in tags})
    margin = _left_margin(glyphs)
    bold_variables = _bold_variable_ids(lines)
    bullets = _line_bullet_ids(lines)
    continuation = "0123456789()[]{}+-*/.,: "

    def mathematical_glyph(glyph: dict[str, Any]) -> bool:
        return bool(MATH_FONT.search(glyph["font"]) or MATH_CHAR.search(glyph["text"]) or glyph.get("id") in bold_variables or glyph.get("id") in accents or (separate_roman_math and re.match(r"CM(?:R|BX)\d", glyph["font"], re.I)))

    accents: set[str] = set()
    accents = _accent_glyph_ids(lines, mathematical_glyph)
    # A text-face operator name applied to notation ("log c_ε", "max{...}") continues an open
    # run: TeX sets it in the text face between the operands it belongs to.
    operator_words = _operator_word_ids(lines, mathematical_glyph)
    openers = _next_line_openers(lines)
    # The native run that closed the previous line after a relation or operator: TeX
    # breaks an inline formula only there, so the next line's opening run continues it.
    carry: str | None = None
    for key, line in lines.items():
        run: list[dict[str, Any]] = []
        line_inked = [g for g in line if inked_glyph(g)]
        first_inked = line_inked[0]["id"] if line_inked else None
        last_inked = line_inked[-1]["id"] if line_inked else None
        closing: str | None = None
        for position, glyph in enumerate([*line, {"text": "\u0000", "font": "", "bbox": [0, 0, 0, 0]}]):
            mathematical = mathematical_glyph(glyph)
            # A digit or bracket opening the line after a break operator ("f(λ) >" / "0") is
            # the continued expression, not prose.
            seeded = carry is not None and not run and glyph.get("id") == first_inked and glyph["text"] in continuation and not glyph["text"].isspace()
            if glyph.get("id") not in protected_prose and glyph.get("id") not in bullets and glyph["text"] not in QED_MARKERS and (mathematical or seeded or (run and (glyph["text"] in continuation or glyph.get("id") in operator_words))):
                # A run opened by a space glyph of the mathematical face ("log ␣c") holds no
                # notation yet: its prefix is still the text before the space.
                if mathematical and not any(inked_glyph(g) for g in run) and not glyph["text"].isspace():
                    # Normal-font prefixes are common in U(N), diag(...), 2π.
                    prefix: list[dict[str, Any]] = []
                    for previous in reversed(line[:position]):
                        if previous["id"] in protected_prose:
                            break
                        if previous["text"].isspace() and not prefix:
                            continue
                        if previous["text"].isspace() or not re.fullmatch(r"[A-Za-z0-9([{}.*+/-]", previous["text"]):
                            break
                        prefix.insert(0, previous)
                    prefix_text = "".join(g["text"] for g in prefix)
                    # A preceding prose article is not a mathematical prefix; an operator name
                    # ("log x", "dim V") is, whatever the space TeX set after it.
                    if prefix and line[position - 1]["text"].isspace() and glyph["text"] not in "=<>±×÷" and not _operator_name(prefix_text):
                        prefix = []
                        prefix_text = ""
                    if _operator_prefix(prefix_text):
                        run = [*prefix, *run]
                run.append(glyph)
            elif run:
                run = _trim_prose_edges(run, line, following=openers.get(key))
                if run:
                    run_id = f"run:{run[0]['id']}"
                    native: dict[str, Any] = {"kind": "math", "bbox": _union([g["bbox"] for g in run]), "glyph_ids": [g["id"] for g in run], "provenance": ["native-math-glyphs"], "display": False, "grouping_pending": False, "_runs": [run_id]}
                    if carry is not None and run[0]["id"] == first_inked:
                        native["_continues"] = [carry]
                    if run[-1]["id"] == last_inked and (run[-1]["text"] in BREAK_OPERATORS or "cmex" in run[-1]["font"].lower()):
                        closing = run_id
                    regions.append(native)
                run = []
        carry = closing
    for image in page.get_image_info():
        regions.append({"kind": "figure", "bbox": list(image["bbox"]), "provenance": ["native-image"], "display": True, "grouping_pending": False})
    # Vector-only diagrams, fraction bars and accents must not disappear from the ledger.
    for drawing in page.get_drawings():
        bbox = list(drawing["rect"])
        bbox = [bbox[0] - 0.5, bbox[1] - 0.5, bbox[2] + 0.5, bbox[3] + 0.5]
        regions.append({"kind": "mixed-region", "bbox": bbox, "provenance": ["native-vector"], "display": True, "grouping_pending": True})
    # Detector rectangles are proposals, not authority to consume prose. Font
    # metrics overlap adjacent words even when the visible ink is disjoint.
    native_ids = {gid for r in regions for gid in r.get("glyph_ids", [])}
    prose_ids: set[str] = set(protected_prose)
    for line in lines.values():
        word: list[dict[str, Any]] = []
        for glyph in [*line, {"text": " "}]:
            if glyph["text"].isalpha() and glyph.get("id") not in native_ids:
                word.append(glyph)
            else:
                if len(word) >= 2 and "".join(g["text"] for g in word).lower() not in MATH_OPERATORS:
                    prose_ids.update(g["id"] for g in word)
                word = []
    clean = []
    glyph_by_id = {g["id"]: g for g in glyphs}
    pending = list(regions)
    regions = []
    cuts: list[float] = []
    while pending:
        region = pending.pop(0)
        if "glyph_ids" not in region:
            owned = [g for g in glyphs if inked_glyph(g) and _inside(g, region["bbox"]) and g["id"] not in tags]
            whole_display = False
            if region["kind"] == "math" and region["display"] and owned and any(g["id"] in prose_ids for g in owned):
                owned = _display_box_owned(owned, prose_ids, margin, rules)[0]
                # Words that stay inside the formula are its text operators.
                whole_display = bool(owned)
            if region["kind"] == "math" and owned and all(p.startswith("PP-DocLayoutV2:") for p in region["provenance"]) and not any(
                MATH_FONT.search(g["font"]) or MATH_CHAR.search(g["text"]) or g["id"] in bold_variables for g in owned
            ) and re.fullmatch(r"[0-9.,;:%\s-]+", "".join(g["text"] for g in owned)):
                # A plain number in the text face ("see page 77") is not notation,
                # whatever the detector proposed.
                continue
            if region["kind"] == "math" and (not owned or (not whole_display and any(g["id"] in prose_ids for g in owned))):
                # Fall back to the finer native math runs, not the whole block; see
                # _display_line_glyph_ids for the structural handling of such lines.
                continue
            if region["kind"] == "math" and region["display"]:
                parts, part_cuts = _split_display_at_tags(region, owned, tag_rows)
                cuts.extend(part_cuts)
                if len(parts) > 1:
                    # One detector box over several labelled displays: one region each.
                    for part in reversed(parts):
                        pending.insert(0, {**region, "bbox": _union([g["bbox"] for g in part]), "glyph_ids": [g["id"] for g in part]})
                    continue
                region["bbox"] = _union([g["bbox"] for g in owned])
            if region["kind"] in {"math", "mixed-region"} and owned:
                region["glyph_ids"] = [g["id"] for g in owned if whole_display or g["id"] not in prose_ids]
                if not region["glyph_ids"]:
                    # A vector crossing ordinary text still needs original evidence.
                    if "native-vector" not in region["provenance"]:
                        continue
                    region.pop("glyph_ids")
        if region["kind"] == "math" and region.get("glyph_ids"):
            owned = [glyph_by_id[gid] for gid in region["glyph_ids"] if gid in glyph_by_id]
            if not region["display"]:
                # Neighbours are looked up per native line; brackets balance over the region,
                # since a superscript in the next block splits one expression across lines.
                by_line: dict[str, list[dict[str, Any]]] = {}
                for g in owned:
                    by_line.setdefault(g["line"], []).append(g)
                owned = [g for key, group in by_line.items() for g in _trim_prose_edges(group, lines.get(key, group), balance=owned, following=openers.get(key))]
            if not owned:
                continue
            region["glyph_ids"] = [g["id"] for g in owned]
            # Detector rectangles and font metrics overshoot the visible ink; the
            # owned glyph ink is the region. Rules and accents merge back below.
            region["bbox"] = _union([g["bbox"] for g in owned if inked_glyph(g)] or [g["bbox"] for g in owned])
        clean.append(region)
    regions = _merge_delimiter_pieces(clean, glyphs, cuts)
    regions = _cluster_drawings(regions)
    # Merge formula components and their rules, but never expand to a PDF text
    # block. Explicit ownership prevents overlapping font boxes stealing prose.
    # Each pair test reads the region's cached inked baselines and id set (kept on
    # the region under private keys and updated on merge): a page with hundreds of
    # vector drawings otherwise rescans every glyph for every pair.
    for region in regions:
        _cache_region_ink(region, glyph_by_id)
    changed = True
    while changed:
        changed = False
        for i, left in enumerate(regions):
            for j in range(i + 1, len(regions)):
                right = regions[j]
                shared = left["_ids"] & right["_ids"]
                overlap = _intersects(left["bbox"], right["bbox"])
                detector_math = left["kind"] == right["kind"] == "math" and any(p.startswith("PP-DocLayoutV2:") for p in left["provenance"] + right["provenance"])
                near_baseline = not left["_baselines"] or not right["_baselines"] or _nearest_gap(left["_baselines"], right["_baselines"]) < max(left["_size"], right["_size"]) * .7
                if shared or (overlap and not detector_math and near_baseline):
                    display = any(r["display"] and any(p != "native-vector" for p in r["provenance"]) for r in (left, right))
                    left["bbox"] = _union([left["bbox"], right["bbox"]])
                    if "glyph_ids" in left or "glyph_ids" in right:
                        ids = set(left.get("glyph_ids", [])) | set(right.get("glyph_ids", []))
                        left["glyph_ids"] = [g["id"] for g in glyphs if g["id"] in ids]
                    if left["kind"] != right["kind"]:
                        kinds = {left["kind"], right["kind"]}
                        left["kind"] = "table" if "table" in kinds else "figure" if "figure" in kinds else "math" if "math" in kinds and "native-vector" in left["provenance"] + right["provenance"] else "mixed-region"
                    if left["kind"] in {"figure", "table", "code"}:
                        left.pop("glyph_ids", None)
                    left["provenance"] = sorted(set(left["provenance"] + right["provenance"]))
                    for key in ("_runs", "_continues"):
                        if key in left or key in right:
                            left[key] = [*left.get(key, []), *right.get(key, [])]
                    left["grouping_pending"] = left["kind"] == "mixed-region"
                    # A fraction bar/accent alone does not turn inline math into display math.
                    left["display"] = display
                    _cache_region_ink(left, glyph_by_id)
                    regions.pop(j)
                    changed = True
                    break
            if changed:
                break
    for region in regions:
        for key in ("_ids", "_baselines", "_size"):
            region.pop(key, None)
    regions = _join_line_break_runs(regions)
    regions = _absorb_delimited_rows(regions, glyphs, margin)
    for region in regions:
        region.pop("_runs", None)
        region.pop("_continues", None)
        if region["kind"] == "math" and region.get("glyph_ids") and not region.get("formula_conditions"):
            # Language inside any math crop, displayed or inline ("i.o.", "a.s."), is declared.
            conditions = _auto_formula_conditions(region, glyph_by_id)
            if conditions:
                region["formula_conditions"] = conditions
                region["provenance"] = [*region["provenance"], "auto-formula-conditions"]
        if unmeasured.intersection(region.get("glyph_ids", [])) and "ink-bounds-unmeasured" not in region["provenance"]:
            region["provenance"] = [*region["provenance"], "ink-bounds-unmeasured"]
    return sorted(regions, key=lambda r: (r["bbox"][1], r["bbox"][0]))


def _cache_region_ink(region: dict[str, Any], glyph_by_id: dict[str, dict[str, Any]]) -> None:
    """Cache what the merge loop asks of a region: its id set, inked baselines, font size."""
    inked = [glyph_by_id[gid] for gid in region.get("glyph_ids", []) if gid in glyph_by_id and inked_glyph(glyph_by_id[gid])]
    region["_ids"] = frozenset(region.get("glyph_ids", []))
    region["_baselines"] = sorted(g.get("baseline", 0) for g in inked)
    region["_size"] = max((g.get("size", 10) for g in inked), default=10)


def _nearest_gap(left: list[float], right: list[float]) -> float:
    """The smallest distance between a value of ``left`` and one of ``right`` (both sorted)."""
    from bisect import bisect_left

    best = float("inf")
    for value in left:
        index = bisect_left(right, value)
        for candidate in (right[index - 1] if index else None, right[index] if index < len(right) else None):
            if candidate is not None:
                best = min(best, abs(value - candidate))
    return best


def _cluster_drawings(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge overlapping glyph-free vector regions ahead of the pairwise loop.

    Two ``native-vector`` regions that overlap always merge in the loop below (no glyph
    baseline separates them), and every region overlapping their union merges the same
    way whether the drawings were joined first or one at a time. Joining them here is
    the same result; a diagram of hundreds of segments no longer costs one restart of
    the quadratic scan per segment.
    """
    def bare(region: dict[str, Any]) -> bool:
        return region["kind"] == "mixed-region" and region["provenance"] == ["native-vector"] and "glyph_ids" not in region

    members = [index for index, region in enumerate(regions) if bare(region)]
    parent = {index: index for index in members}

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    changed = True
    clusters: dict[int, list[float]] = {index: list(regions[index]["bbox"]) for index in members}
    while changed:
        changed = False
        roots = sorted(set(find(index) for index in members))
        for a in range(len(roots)):
            for b in range(a + 1, len(roots)):
                ra, rb = roots[a], roots[b]
                if find(ra) != find(rb) and _intersects(clusters[find(ra)], clusters[find(rb)]):
                    root, other = sorted((find(ra), find(rb)))
                    parent[other] = root
                    clusters[root] = _union([clusters[root], clusters[other]])
                    changed = True
    result: list[dict[str, Any]] = []
    for index, region in enumerate(regions):
        if index not in parent:
            result.append(region)
            continue
        root = find(index)
        if root != index:
            continue
        size = sum(1 for member in members if find(member) == root)
        if size == 1:
            result.append(region)
            continue
        # What the pairwise merge of glyph-free drawings produces: the union box, the
        # single provenance, and a display flag no vector region can assert on its own.
        result.append({**region, "bbox": clusters[root], "display": False, "grouping_pending": True})
    return result


def _absorb_delimited_rows(regions: list[dict[str, Any]], glyphs: list[dict[str, Any]],
                           margin: float) -> list[dict[str, Any]]:
    """Give an inline formula the rows a stretched delimiter it owns brackets.

    A cases block or a matrix set inline (``G(x) = {`` with its rows to the right of a tall
    CMEX brace) is scanned per native line, so the brace lands in the first row's run and
    every other row becomes an asset of its own, strung together by the commas the runs
    trimmed. A display box owns such rows through ``_display_box_owned``; here the delimiter's
    own ink is the box: every visual line whose baseline lies inside it, on the side the
    delimiter opens towards, up to a wide gap or a second tall delimiter of the same region,
    belongs to the formula, condition words included (they are declared afterwards). The
    result is one region with one crop, provenance ``stretched-delimiter-rows``; only the
    sentence punctuation closing the last row is left to the prose.
    """
    by_id = {g["id"]: g for g in glyphs}
    order = {g["id"]: index for index, g in enumerate(glyphs)}
    inked = [g for g in glyphs if inked_glyph(g)]
    rows = _visual_lines(inked)
    prose_ids = _prose_word_ids(glyphs)
    result = list(regions)
    index = 0
    while index < len(result):
        region = result[index]
        index += 1
        if region["kind"] != "math" or region["display"] or not region.get("glyph_ids"):
            continue
        owned = [by_id[gid] for gid in region["glyph_ids"] if gid in by_id]
        size = max((g.get("size", 10) for g in owned if inked_glyph(g)), default=10.0)
        # Tall delimiter columns: pieces merged earlier share an x, a single big brace is one.
        columns: list[list[dict[str, Any]]] = []
        for g in sorted((g for g in owned if inked_glyph(g) and (_delimiter_piece(g) or "cmex" in g["font"].lower())), key=lambda g: (g["bbox"][0], g["bbox"][1])):
            if columns and abs(g["bbox"][0] - columns[-1][0]["bbox"][0]) <= 1.5:
                columns[-1].append(g)
            else:
                columns.append([g])
        spans = [_union([g["bbox"] for g in column]) for column in columns]
        spans = [span for span in spans if span[3] - span[1] >= _ROW_DELIMITER_HEIGHT_EM * size]
        if not spans:
            continue
        opening = spans[0]
        # A second tall delimiter to the right closes the rows (\left( ... \right)).
        closing = next((span for span in spans[1:] if span[0] > opening[2]), None)
        owner: dict[str, dict[str, Any]] = {gid: r for r in result for gid in r.get("glyph_ids", [])}
        bracketed: list[list[dict[str, Any]]] = []
        for row in rows:
            baselines = sorted(g.get("baseline", g["bbox"][3]) for g in row)
            baseline = baselines[len(baselines) // 2]
            if not opening[1] <= baseline <= opening[3]:
                continue
            candidates = [g for g in row if g["bbox"][0] >= opening[2] - 0.5 and (closing is None or g["bbox"][2] <= closing[0] + 0.5)]
            if not candidates:
                continue
            # A paragraph line the delimiter's ink happens to reach is not a row of it.
            prose = [g for g in candidates if g["id"] in prose_ids]
            if min(g["bbox"][0] for g in candidates) <= margin + size * 2.8 and len(prose) > DISPLAY_PROSE_SHARE * len(candidates):
                continue
            bracketed.append(candidates)
        absorbed: list[dict[str, Any]] = []
        for candidates in bracketed:
            kept: list[dict[str, Any]] = []
            for previous, g in zip([None, *candidates], candidates, strict=False):
                if g["text"] in QED_MARKERS:
                    break
                # A wide gap no other row bridges separates the block from what follows it
                # (an equation number); the aligned condition column of a cases block is
                # bridged by the rows whose first column is longer.
                if (previous is not None and g["bbox"][0] - previous["bbox"][2] >= _ROW_GAP_EM * size
                        and not any(o["bbox"][2] > previous["bbox"][2] + 0.5 and o["bbox"][0] < g["bbox"][0] - 0.5
                                    for other in bracketed if other is not candidates for o in other)):
                    break
                holder = owner.get(g["id"])
                if holder is not None and holder is not region and (holder["kind"] != "math" or holder["display"]):
                    break
                kept.append(g)
            absorbed.extend(kept)
        new_ids = {g["id"] for g in absorbed} - set(region["glyph_ids"])
        if not new_ids:
            continue
        # The sentence punctuation closing the last row belongs to the prose, as it does
        # after any inline run; punctuation inside the block is the block's own.
        last_row = max((row for row in rows if any(g["id"] in new_ids for g in row)), key=lambda row: row[0].get("baseline", row[0]["bbox"][3]))
        block = [g for g in last_row if g["id"] in new_ids or g["id"] in region["glyph_ids"]]
        trimmed = _trim_prose_edges(block, last_row, balance=owned + [g for g in absorbed if g["id"] in new_ids])
        new_ids -= {g["id"] for g in block} - {g["id"] for g in trimmed}
        if not new_ids:
            continue
        # Space glyphs between absorbed glyphs of one native line travel with them.
        lines_touched: dict[str, list[dict[str, Any]]] = {}
        for g in glyphs:
            if g["id"] in new_ids:
                lines_touched.setdefault(g["line"], []).append(g)
        for key, members in lines_touched.items():
            first, last = order[members[0]["id"]], order[members[-1]["id"]]
            for g in glyphs[first:last + 1]:
                if g["line"] == key and g["text"].isspace():
                    new_ids.add(g["id"])
        ids = set(region["glyph_ids"]) | new_ids
        merged: list[dict[str, Any]] = []
        for other in result:
            if other is region or not (set(other.get("glyph_ids", [])) & ids):
                continue
            if other["kind"] != "math" or other["display"]:
                ids -= set(other.get("glyph_ids", []))
                continue
            merged.append(other)
            ids |= set(other["glyph_ids"])
        span_rows = [g["id"] for g in glyphs if g["id"] in ids and opening[1] <= g.get("baseline", g["bbox"][3]) <= opening[3]]
        fragments = [dict(f) for f in region.get("fragments", [])] + [f for other in merged for f in other.get("fragments") or [{"bbox": other["bbox"], "glyph_ids": other["glyph_ids"]}]]
        outside = [f for f in fragments if not all(gid in span_rows for gid in f["glyph_ids"])]
        inside_ids = ids - {gid for f in outside for gid in f["glyph_ids"]}
        inside_box = _union([by_id[gid]["bbox"] for gid in inside_ids if inked_glyph(by_id[gid])])
        region["glyph_ids"] = [g["id"] for g in glyphs if g["id"] in ids]
        region["bbox"] = _union([inside_box, *(f["bbox"] for f in outside)])
        if outside:
            inside = {"bbox": inside_box, "glyph_ids": [gid for gid in region["glyph_ids"] if gid in inside_ids]}
            region["fragments"] = sorted([*outside, inside], key=lambda f: order[f["glyph_ids"][0]])
        else:
            region.pop("fragments", None)
        for other in merged:
            region["provenance"] = sorted(set(region["provenance"] + other["provenance"]))
            region["grouping_pending"] = region["grouping_pending"] or other["grouping_pending"]
            for key in ("_runs", "_continues"):
                if key in region or key in other:
                    region[key] = [*region.get(key, []), *other.get(key, [])]
        if "stretched-delimiter-rows" not in region["provenance"]:
            region["provenance"] = [*region["provenance"], "stretched-delimiter-rows"]
        result = [r for r in result if r is region or not any(r is other for other in merged)]
        index = next(i for i, r in enumerate(result) if r is region) + 1
    return result


def _join_line_break_runs(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Join the two halves of an inline formula TeX broke across lines into one asset.

    A native run that closed its line with a relation or operator (``f(λ) >``) and the
    run opening the next line (``0``) are one expression; the joined region keeps a
    fragment per line so the crop stays faithful to the printed layout. A half that a
    display region absorbed in the meantime is left where it is.
    """
    def joinable(region: dict[str, Any]) -> bool:
        return region["kind"] == "math" and not region["display"] and bool(region.get("glyph_ids"))

    result = list(regions)
    changed = True
    while changed:
        changed = False
        for index, region in enumerate(result):
            for run_id in region.get("_continues", []):
                previous = next((r for r in result if r is not region and run_id in r.get("_runs", [])), None)
                if previous is None or not joinable(previous) or not joinable(region):
                    continue
                first = previous.get("fragments") or [{"bbox": previous["bbox"], "glyph_ids": previous["glyph_ids"]}]
                second = region.get("fragments") or [{"bbox": region["bbox"], "glyph_ids": region["glyph_ids"]}]
                joined = {
                    "kind": "math", "display": False,
                    "grouping_pending": previous["grouping_pending"] or region["grouping_pending"],
                    "provenance": sorted({*previous["provenance"], *region["provenance"], "line-break-continued"}),
                    "fragments": [*first, *second],
                    "bbox": _union([previous["bbox"], region["bbox"]]),
                    "glyph_ids": [*previous["glyph_ids"], *region["glyph_ids"]],
                    "_runs": [*previous.get("_runs", []), *region.get("_runs", [])],
                    "_continues": [*previous.get("_continues", []), *(c for c in region["_continues"] if c != run_id)],
                }
                result = [r for r in result if r is not previous and r is not region]
                result.insert(min(index, len(result)), joined)
                changed = True
                break
            if changed:
                break
    return result


def _declare_override_conditions(page: fitz.Page, region: dict[str, Any], glyphs: list[dict[str, Any]],
                                 assets: dict[str, FidelityAsset] | None = None) -> dict[str, Any]:
    """Declare the language inside a reviewer's math region as preparation would.

    A region that names its glyphs but says nothing about ``formula_conditions`` gets the
    automatic declaration; an explicit list (even an empty one) is the reviewer's decision.
    A ``preserve_asset_id`` region owns the preserved asset's glyphs: it is declared the
    same way when that asset carries no declaration yet, so the gate that requires one
    is never asking a path that cannot answer.
    """
    if "formula_conditions" in region:
        return region
    ids: list[str] = []
    if "preserve_asset_id" in region:
        preserved = (assets or {}).get(region["preserve_asset_id"])
        if preserved is None or preserved.formula_conditions or region.get("kind", preserved.kind) != "math":
            return region
        ids = [gid for fragment in preserved.fragments for gid in fragment.glyph_ids]
    elif region.get("kind") == "math":
        # Ownership as `_asset_impl` resolves it: named glyphs, else the glyphs inside the box.
        for fragment in region.get("fragments") or [region]:
            if "glyph_ids" in fragment:
                ids.extend(fragment["glyph_ids"])
            elif "bbox" in fragment:
                ids.extend(g["id"] for g in glyphs if _inside(g, fragment["bbox"]))
    if not ids:
        return region
    if hasattr(page, "get_svg_image"):
        from littrans.glyph_export import glyph_ink_boxes
        ink = glyph_ink_boxes(page, glyphs)
        glyphs = [{**g, "bbox": ink.get(g["id"], g["bbox"])} for g in glyphs]
    conditions = _auto_formula_conditions({**region, "glyph_ids": ids}, {g["id"]: g for g in glyphs})
    if not conditions:
        return region
    provenance = [*region.get("provenance", ["visual-region-correction"]), "auto-formula-conditions"]
    return {**region, "formula_conditions": conditions, "provenance": provenance}


def _asset_content_identity(asset: FidelityAsset) -> str:
    fragments = [_hash({"source_sha256": asset.source_sha256, "page": f.page, "bbox": f.bbox, "glyph_ids": f.glyph_ids,
                       **({"export_method": f.export_method} if f.export_method != "raw-region" else {})}) for f in asset.fragments]
    content = fragments[0] if len(fragments) == 1 else _hash(fragments)
    if asset.formula_conditions:
        content = _hash({"original_content": content, "formula_conditions": [c.model_dump(mode="json") for c in asset.formula_conditions]})
    if asset.content_identity_version == 2:
        content = _hash({"original_content": content, "kind": asset.kind, "display": asset.display,
                         "grouping_pending": asset.grouping_pending})
    return content


def _asset(root: Path, doc: fitz.Document, page_number: int, source_hash: str, region: dict[str, Any], glyphs: list[dict[str, Any]]) -> FidelityAsset:
    asset = _asset_impl(root, doc, page_number, source_hash, region, glyphs)
    asset.content_identity_version = 2
    asset.content_sha256 = _asset_content_identity(asset)
    return asset


def _asset_impl(root: Path, doc: fitz.Document, page_number: int, source_hash: str, region: dict[str, Any], glyphs: list[dict[str, Any]]) -> FidelityAsset:
    for key in ("id", "preserve_asset_id"):
        if key in region and (not isinstance(region[key], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", region[key])):
            raise ValueError("invalid region asset ID")
    if "preserve_asset_id" in region:
        existing = load_assets(root)[region["preserve_asset_id"]]
        if existing.source_sha256 != source_hash or any(f.page != page_number for f in existing.fragments):
            raise ValueError("preserved asset belongs to different source/page")
        changes: dict[str, Any] = {k: region[k] for k in ("kind", "display", "grouping_pending", "formula_conditions") if k in region}
        if "auto-formula-conditions" in region.get("provenance", []) and "auto-formula-conditions" not in existing.provenance:
            changes["provenance"] = [*existing.provenance, "auto-formula-conditions"]
        if "formula_conditions" in changes:
            hashes = [_hash({"source_sha256": existing.source_sha256, "page": f.page, "bbox": f.bbox, "glyph_ids": f.glyph_ids,
                             **({"export_method": f.export_method} if f.export_method != "raw-region" else {})}) for f in existing.fragments]
            original = hashes[0] if len(hashes) == 1 else _hash(hashes)
            changes["content_sha256"] = _hash({"original_content": original, "formula_conditions": changes["formula_conditions"]}) if changes["formula_conditions"] else original
        return FidelityAsset.model_validate({**existing.model_dump(mode="json"), **changes})
    if "fragments" in region:
        children = []
        for fragment in region["fragments"]:
            if fragment.get("page", page_number) != page_number:
                raise ValueError("page review fragments must remain on their reviewed page; use continuation links across pages")
            child = {k: v for k, v in region.items() if k not in {"fragments", "id", "formula_conditions"}}
            child["bbox"] = fragment["bbox"]
            if "glyph_ids" in fragment:
                child["glyph_ids"] = fragment["glyph_ids"]
            children.append(_asset(root, doc, page_number, source_hash, child, glyphs))
        if not children:
            raise ValueError("region fragments must not be empty")
        content = children[0].content_sha256 if len(children) == 1 else _hash([a.content_sha256 for a in children])
        if region.get("formula_conditions"):
            content = _hash({"original_content": content, "formula_conditions": region["formula_conditions"]})
        return FidelityAsset(id=region.get("id") or f"a-p{page_number:04d}-{content[:12]}", kind=region["kind"], source_sha256=source_hash, content_sha256=content, fragments=[f for a in children for f in a.fragments], grouping_pending=region.get("grouping_pending", False), display=region.get("display", True), provenance=region.get("provenance", ["visual-multifragment-correction"]), formula_conditions=region.get("formula_conditions", []))
    page = doc[page_number - 1]
    rect = fitz.Rect(region["bbox"]) & page.rect
    if rect.is_empty:
        raise ValueError("empty or out-of-page source region")
    if "glyph_ids" in region:
        selected = region["glyph_ids"]
        if not isinstance(selected, list) or len(selected) != len(set(selected)) or any(gid not in {g["id"] for g in glyphs} for gid in selected):
            raise ValueError("explicit glyph ownership must contain unique existing page glyph IDs")
        owned = [g for g in glyphs if g["id"] in selected]
    else:
        owned = [g for g in glyphs if _inside(g, rect)]
    # The declared box is the target box; only owned glyph ink is padded. An exported
    # fragment box fed back through a region override therefore reproduces itself.
    rect = fitz.Rect(_union([list(rect), *[list(fitz.Rect(g["bbox"]) + (-0.5, -0.5, 0.5, 0.5)) for g in owned]])) & page.rect
    # A region that names glyphs exports their paths; one that names none ("glyph_ids": []
    # on a rule or a figure frame) owns nothing and is a raw crop of its box.
    export_method: Literal["raw-region", "explicit-glyph-paths-v2"] = "explicit-glyph-paths-v2" if region.get("glyph_ids") else "raw-region"
    owned_svg = None
    if export_method != "raw-region":
        from littrans.glyph_export import build_owned_fragment
        try:
            owned_svg, geometry = build_owned_fragment(page, owned, list(rect))
            rect = fitz.Rect(geometry["bbox"])
        except ValueError as exc:
            # Keep original evidence available, but require boundary correction.
            # Do not claim a raw crop is an isolated mathematical expression; a figure
            # or table the reviewer declared stays what they said it is.
            export_method = "raw-region"
            region = {**{k: v for k, v in region.items() if k != "formula_conditions"}, "grouping_pending": True,
                      "provenance": [*(p for p in region.get("provenance", []) if p != "auto-formula-conditions"), "precise-export-unavailable:" + str(exc)]}
            if region["kind"] == "math":
                region["kind"] = "mixed-region"
    identity = {"source_sha256": source_hash, "page": page_number, "bbox": _box(rect), "glyph_ids": [g["id"] for g in owned]}
    if export_method != "raw-region":
        identity["export_method"] = export_method
    content = _hash(identity)
    if region.get("formula_conditions"):
        content = _hash({"original_content": content, "formula_conditions": region["formula_conditions"]})
    aid = region.get("id") or f"a-p{page_number:04d}-{content[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", aid):
        raise ValueError("invalid region asset ID")
    base = root / f"derived/assets/fidelity/{content}"
    base.mkdir(parents=True, exist_ok=True)
    fragment_svg = base / "original.svg"
    fragment_png = base / "original.png"
    fragments = (fragment_svg, fragment_png)
    cache_receipt = base / "evidence.json"
    dpi = 450 if any(g["size"] < 8 for g in owned) else 300
    if cache_receipt.is_file():
        recorded = read_json(cache_receipt)
        if set(recorded["files"]) != {p.name for p in fragments}:
            # A receipt from a build with another representation set (per-region PDFs
            # before 0.6.1) is stale: re-export instead of silently keeping its files.
            for name in recorded["files"]:
                if name not in {p.name for p in fragments}:
                    (base / name).unlink(missing_ok=True)
            cache_receipt.unlink()
        else:
            for name, expected in recorded["files"].items():
                candidate = base / name
                if not candidate.is_file() or sha256_file(candidate) != expected:
                    raise ValueError(f"immutable original asset cache is corrupt: {candidate}")
    if not cache_receipt.is_file():
        if owned_svg is None:
            with fitz.open() as clipped:
                target = clipped.new_page(width=rect.width, height=rect.height)
                target.show_pdf_page(target.rect, doc, page_number - 1, clip=rect)
                atomic_write_text(fragment_svg, target.get_svg_image(text_as_path=True))
                target.get_pixmap(dpi=dpi, alpha=False).save(fragment_png)
        else:
            atomic_write_text(fragment_svg, owned_svg)
            with fitz.open("svg", owned_svg.encode()) as rendered:
                rendered[0].get_pixmap(dpi=dpi, alpha=False).save(fragment_png)
        write_json(cache_receipt, {"identity": identity, "files": {p.name: sha256_file(p) for p in fragments}})
    paths = {name: str(p.relative_to(root)).replace("\\", "/") for name, p in {"png": fragment_png, "svg": fragment_svg}.items()}
    box = _box(rect)
    fragment = FidelityFragment(page=page_number, bbox=box, png_path=paths["png"], svg_path=paths["svg"], glyph_ids=[g["id"] for g in owned], width=round(box[2] - box[0], 4), height=round(box[3] - box[1], 4), baseline=round(max(g["baseline"] for g in owned) - box[1], 4) if owned else None, dpi=dpi, export_method=export_method, file_sha256={paths[k]: sha256_file(root / paths[k]) for k in paths})
    return FidelityAsset(id=aid, kind=region["kind"], source_sha256=source_hash, content_sha256=content, fragments=[fragment], grouping_pending=region.get("grouping_pending", False), display=region.get("display", False), provenance=region.get("provenance", ["visual-region-correction"]), formula_conditions=region.get("formula_conditions", []))


def _make_unit(page: int, uid: str, text: str, bbox: Any, assets: dict[str, FidelityAsset], kind: str = "paragraph", **extra: Any) -> SourceUnit:
    refs = asset_reference_ids(text)
    hashes = {aid: assets[aid].content_sha256 for aid in refs}
    extra.setdefault("protected_tokens", protected_tokens(re.sub(r"\{\{asset:[^}]+\}\}", "", text), heading=kind == "heading"))
    extra.setdefault("translatable", True)
    payload = {"page": page, "text": text, "bbox": _box(bbox), "asset_content_hashes": hashes, "kind": kind, **extra}
    prose = re.sub(r"\{\{asset:[^}]+\}\}", "", text).strip()
    pure_math = kind != "footnote" and bool(refs) and not prose and all(assets[aid].kind == "math" for aid in refs)
    if pure_math:
        kind = "equation"
        payload["kind"] = kind
        extra["translatable"] = any(assets[aid].formula_conditions for aid in refs)
        payload["translatable"] = extra["translatable"]
    elif kind == "equation" and prose and extra.get("render_policy", "include") != "omit":
        # A displayed line that also carries prose ("... for all times t > 0.") keeps
        # translatable text even when it was rebuilt from a formula-only unit.
        extra["translatable"] = True
        payload["translatable"] = True
    return SourceUnit(unit_id=uid, page=page, kind=UnitKind(kind), bbox=_box(bbox), source_text=text, source_markdown=text, source_hash=_hash(payload), confidence=0, latex=None, asset_content_hashes=hashes, asset_refs=[AssetRef(kind="fidelity", path=f.png_path, bbox=f.bbox) for aid in dict.fromkeys(refs) for f in assets[aid].fragments], **extra)


def _separate_display_units(units: list[SourceUnit], assets: dict[str, FidelityAsset],
                            note_numbers: dict[str, str] | None = None) -> list[SourceUnit]:
    """Keep display formulas selectable and bind their printed equation labels.

    ``note_numbers`` maps footnote unit IDs to their printed numbers so that the
    footnote links of a split block follow the chunk that still carries the call.
    """
    from littrans.semantics import explicit_footnote_numbers

    result = []
    number_pattern = EQUATION_LABEL
    # A label may share its PDF block with the tombstone closing a proof ("□ (1.50)").
    tombstones = "[" + re.escape("".join(sorted(QED_MARKERS))) + r"\s]*"
    note_numbers = {**{u.unit_id: u.footnote_number for u in units if u.footnote_number},
                    **(note_numbers or {})}

    def chunk_refs(unit: SourceUnit, text: str) -> list[str]:
        """The block's footnote links that the rebuilt chunk still calls."""
        calls = explicit_footnote_numbers(text)
        return [ref for ref in unit.footnote_refs if note_numbers.get(ref) in calls]

    for unit in units:
        text = unit.source_markdown or unit.source_text
        refs = asset_reference_ids(text)
        displays = [aid for aid in refs if assets[aid].kind == "math" and assets[aid].display]
        plain = re.sub(r"\{\{asset:[^}]+\}\}", "", text).strip()
        label = re.fullmatch(number_pattern, plain)
        if len(refs) == 1 and displays and (not plain or label):
            # The unit covers the displayed formula, not only the PDF block it started in.
            box = _union([unit.bbox, *(f.bbox for f in assets[refs[0]].fragments)])
            result.append(_make_unit(unit.page, unit.unit_id, "{{asset:" + refs[0] + "}}", box, assets, equation_number=label[1] if label else None))
        elif displays and unit.kind is UnitKind.EQUATION:
            # A display line with translatable prose beside the formula is one unit;
            # a trailing printed label still binds as its equation number.
            tail = re.fullmatch(r"(.*?)\s*" + number_pattern, text, re.S)
            if tail and tail[1].strip():
                body = tail[1].strip()
                result.append(_make_unit(unit.page, unit.unit_id, body, unit.bbox, assets, kind="equation", equation_number=tail[2], footnote_refs=chunk_refs(unit, body)))
            else:
                result.append(unit)
        elif displays:
            chunks, pending = [], ""
            for part in re.split(r"(\{\{asset:[^}]+\}\})", text):
                part_ids = asset_reference_ids(part)
                if part_ids and part_ids[0] in displays:
                    if pending.strip():
                        chunks.append((pending.strip(), unit.bbox, unit.kind.value))
                    chunks.append((part, assets[part_ids[0]].fragments[0].bbox, "equation"))
                    pending = ""
                else:
                    pending += part
            if pending.strip():
                chunks.append((pending.strip(), unit.bbox, unit.kind.value))
            # Footnote links follow the chunk that still carries their call.
            result.extend(_make_unit(unit.page, unit.unit_id if i == 0 else f"{unit.unit_id}-displaypart{i + 1}", body, box, assets, kind=kind, footnote_refs=chunk_refs(unit, body)) for i, (body, box, kind) in enumerate(chunks))
        else:
            result.append(unit)
    removed = set()
    # A tombstone that closes the proof at the end of its display line reads after the
    # display, wherever MuPDF put its block (LT-082): (tombstone index, display index).
    after_display: list[tuple[int, int]] = []
    # A label MuPDF put in the text block before or after the display ("... given by
    # (8.50)", "(8.32) (i) ...") binds to the adjacent unnumbered display beside it and
    # leaves the paragraph.
    for index, unit in enumerate(result):
        if unit.kind is not UnitKind.PARAGRAPH or unit.equation_number:
            continue
        text = unit.source_text
        leading = re.match(r"\s*" + number_pattern + r"(?=\s|$)", text)
        trailing = re.search(r"(?<=\s)" + number_pattern + r"\s*$", text)
        for match, neighbour_index in ((leading, index - 1), (trailing, index + 1)):
            if match is None or not 0 <= neighbour_index < len(result):
                continue
            neighbour = result[neighbour_index]
            fragments = [f for aid in asset_reference_ids(neighbour.source_text) if assets[aid].display for f in assets[aid].fragments]
            if (neighbour.kind is not UnitKind.EQUATION or neighbour.equation_number or neighbour.page != unit.page
                    or not fragments or not any(min(f.bbox[3], unit.bbox[3]) - max(f.bbox[1], unit.bbox[1]) >= 3 for f in fragments)):
                continue
            body = (text[:match.start()] + " " + text[match.end():]).strip()
            if not body:
                break
            result[neighbour_index] = _make_unit(neighbour.page, neighbour.unit_id, neighbour.source_text, neighbour.bbox, assets, kind=neighbour.kind.value, equation_number=match[1], footnote_refs=neighbour.footnote_refs)
            result[index] = _make_unit(unit.page, unit.unit_id, body, unit.bbox, assets, kind=unit.kind.value, footnote_refs=unit.footnote_refs)
            if neighbour_index > index and all(c in QED_MARKERS or c.isspace() for c in body):
                after_display.append((index, neighbour_index))
            break
    for index, unit in enumerate(result):
        label = re.fullmatch(tombstones + number_pattern + tombstones, unit.source_text.strip())
        if not label:
            continue
        y = (unit.bbox[1] + unit.bbox[3]) / 2
        candidates = [(i, other) for i, other in enumerate(result) if other.kind == UnitKind.EQUATION and not other.equation_number and any(assets[aid].display and assets[aid].fragments[0].bbox[1] - 3 <= y <= assets[aid].fragments[0].bbox[3] + 3 for aid in asset_reference_ids(other.source_text))]
        if len(candidates) == 1:
            i, other = candidates[0]
            result[i] = _make_unit(other.page, other.unit_id, other.source_text, _union([unit.bbox, other.bbox]), assets, kind=other.kind.value, equation_number=label[1], footnote_refs=other.footnote_refs)
            residue = "".join(c for c in unit.source_text if c in QED_MARKERS)
            if residue:
                # The tombstone stays in the reading order, after the display it closes;
                # structure assembly attaches it to the paragraph the proof ends in.
                result[index] = _make_unit(unit.page, unit.unit_id, residue, unit.bbox, assets, kind=unit.kind.value, footnote_refs=unit.footnote_refs)
                if index < i:
                    after_display.append((index, i))
            else:
                removed.add(index)
    ordered = [unit for i, unit in enumerate(result) if i not in removed and i not in {t for t, _ in after_display}]
    for tombstone_index, display_index in after_display:
        display = result[display_index]
        ordered.insert(ordered.index(display) + 1, result[tombstone_index])
    return ordered


def _expanded_native(page: fitz.Page, original_glyphs: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Retain original glyph identities when a wider canvas changes PDF blocks."""
    from collections import defaultdict, deque
    def key(glyph: dict[str, Any]) -> tuple[Any, ...]:
        return (glyph["text"], glyph["font"], round(glyph["size"], 6),
                *(round(value, 6) for value in glyph["origin"]))
    available: dict[tuple[Any, ...], Any] = defaultdict(deque)
    for glyph in original_glyphs:
        available[key(glyph)].append(glyph)
    glyphs, blocks = _native(page)
    remap, old_ids = {}, {g["id"] for g in original_glyphs}
    for glyph in glyphs:
        previous = available[key(glyph)].popleft() if available[key(glyph)] else None
        old_id = glyph["id"]
        glyph["id"] = previous["id"] if previous else "overflow-" + old_id
        if previous:
            glyph["line"] = previous["line"]
        else:
            glyph["line"] = "overflow-" + glyph["line"]
        remap[old_id] = glyph["id"]
    if any(available.values()) or len({g["id"] for g in glyphs}) != len(glyphs):
        raise ValueError("expanded canvas cannot preserve original glyph identities")
    used_blocks: set[str] = set()
    for block in blocks:
        block["lines"] = [[remap[gid] for gid in line] for line in block["lines"]]
        originals = [gid for line in block["lines"] for gid in line if gid in old_ids]
        preferred = originals[0].split("-", 1)[0] if originals else "overflow-" + block["id"]
        block["id"] = preferred if preferred not in used_blocks else "overflow-" + block["id"]
        used_blocks.add(block["id"])
    return glyphs, blocks


_HYPHENATION_EVIDENCE: dict[str, tuple[Counter[str], Counter[str]]] = {}


def _hyphenation_evidence(doc: fitz.Document, source_hash: str) -> tuple[Counter[str], Counter[str]]:
    """Word forms the document itself prints: mid-line compounds and unbroken words.

    A hyphen at a line end is ambiguous between a soft break ("proba-/bility") and a
    compound TeX chose to break at ("well-/known"). The rest of the document is the
    only evidence available for that decision.
    """
    if source_hash not in _HYPHENATION_EVIDENCE:
        compounds: Counter[str] = Counter()
        words: Counter[str] = Counter()
        for page in doc:
            text = page.get_text()
            compounds.update(m.lower() for m in re.findall(r"[A-Za-z]+-[A-Za-z]+", text))
            # A word printed on its own: not a half of a hyphenated or line-broken word.
            words.update(w.lower() for w in re.findall(r"(?<![A-Za-z-])[A-Za-z]+(?![A-Za-z-])", text))
        _HYPHENATION_EVIDENCE[source_hash] = (compounds, words)
    return _HYPHENATION_EVIDENCE[source_hash]


# Conjunctions after a suspended hyphen ("left- and right-continuous").
SUSPENDED_HYPHEN_TAILS = {"and", "or", "nor"}


def _rejoin_line_breaks(text: str, evidence: tuple[Counter[str], Counter[str]]) -> str:
    """Join words hyphenated across a line end unless the document prints the compound.

    The hyphen stays when the document prints the compound more often than the joined
    word; when neither is printed elsewhere, it stays only if both halves are words the
    document prints on their own (``finite-state``, not ``pas-sion``). A capitalised
    second half is a name compound (``Fokker-Planck``), a conjunction is a suspended
    hyphen (``left- and``), and notation before the hyphen (an asset placeholder) makes
    a compound with the word after it (``σ-algebra``).
    """
    compounds, words = evidence

    def rejoin(match: re.Match[str]) -> str:
        head, tail = match[1], match[2]
        if head == "}}":
            return f"{head}-{tail}"
        if tail[0].isupper():
            return f"{head}-{tail}"
        if tail.lower() in SUSPENDED_HYPHEN_TAILS:
            return f"{head}- {tail}"
        printed = compounds[f"{head}-{tail}".lower()]
        joined = words[f"{head}{tail}".lower()]
        if printed > joined:
            return f"{head}-{tail}"
        if joined > printed:
            return head + tail
        if len(head) >= 3 and len(tail) >= 3 and words[head.lower()] and words[tail.lower()]:
            return f"{head}-{tail}"
        return head + tail

    return re.sub(r"(\}\}|[A-Za-z]*[a-z])-[ \t]*\n[ \t]*([A-Za-z][A-Za-z]*)", rejoin, text)


# Ink gaps at an asset boundary: at least this many ems is a printed word space.
BOUNDARY_SPACE_EM = 0.15
NO_SPACE_BEFORE = set(".,;:?!)]}\u201d\u2019")
NO_SPACE_AFTER = set("([{\u201c\u2018")


def _boundary_space(left: dict[str, Any] | None, right: dict[str, Any] | None,
                    ink: dict[str, Any]) -> bool | None:
    """Whether the page prints a space between two inked glyphs on one native line.

    The text layer is not evidence here: TeX sets the gap around notation with glue,
    which MuPDF reports as a space glyph or not by its own threshold, and reports a kern
    before a period as a space. The printed ink decides: a gap of ``BOUNDARY_SPACE_EM``
    is a word space, less is none. None when the pair is not comparable (a subscript,
    another baseline, no measured ink) and the text layer stays authoritative.
    """
    if left is None or right is None:
        return None
    if right["text"] in NO_SPACE_BEFORE or left["text"] in NO_SPACE_AFTER:
        # TeX never spaces punctuation or a closing bracket from what precedes it, nor
        # an opening bracket or quote from what follows it.
        return False
    lb, rb = ink.get(left["id"]), ink.get(right["id"])
    if lb is None or rb is None:
        return None
    size = max(left.get("size", 10), right.get("size", 10))
    if abs(left.get("baseline", 0) - right.get("baseline", 0)) > size * 0.25:
        return None
    if min(left.get("size", 10), right.get("size", 10)) < size * 0.8:
        return None
    return bool(rb[0] - lb[2] >= size * BOUNDARY_SPACE_EM)


def _page_prepare(root: Path, doc: fitz.Document, number: int, source_hash: str, layout: dict[str, Any], override: dict[str, Any] | None = None,
                  override_origin: dict[str, Any] | None = None) -> tuple[list[SourceUnit], list[FidelityAsset], dict[str, Any]]:
    page = doc[number - 1]
    original_page_bbox = list(page.rect)
    original_glyphs, original_blocks = _native(page)
    canvas = override.get("page_canvas_bbox") if override else None
    if canvas is not None:
        expanded = fitz.Rect(canvas)
        if (page.rotation or page.rect.x0 or page.rect.y0 or expanded.is_empty
                or expanded.x0 != 0 or expanded.y0 != 0
                or expanded.width < page.rect.width or expanded.height < page.rect.height):
            raise ValueError("page_canvas_bbox must expand an unrotated origin-zero source page")
        # Change only this in-memory PDF instance; the immutable source file is
        # never rewritten. This reveals existing content-stream ink, not new ink.
        page.set_mediabox(expanded)
        page.set_cropbox(expanded)
    glyphs, blocks = _expanded_native(page, original_glyphs) if canvas is not None else (original_glyphs, original_blocks)
    image_path = root / f"evidence/pages/fidelity-p{number:04d}.png"
    overflow_evidence = None
    if canvas is not None:
        if not any(g["bbox"][2] > original_page_bbox[2] or g["bbox"][3] > original_page_bbox[3] for g in glyphs):
            raise ValueError("expanded canvas must recover existing off-page native glyphs")
        overflow_path = root / f"evidence/pages/fidelity-p{number:04d}-overflow-{_hash(canvas)[:12]}.png"
        atomic_write_bytes(overflow_path, page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png"))
        overflow_evidence = {"path": overflow_path.relative_to(root).as_posix(), "sha256": sha256_file(overflow_path)}
    from littrans.source_structure import (
        assemble_structure,
        coalesce_inline_assets,
        plan_structure,
        styled_text,
    )
    image_sha256 = sha256_file(image_path)
    items = layout_page_items(layout, image_path, image_sha256) or []
    # The measured glyph ink serves the planner, the regions and the boundary spaces.
    ink: dict[str, Any] = {}
    if hasattr(page, "get_svg_image"):
        from littrans.glyph_export import glyph_ink_boxes
        ink = glyph_ink_boxes(page, glyphs)
    structure = plan_structure(glyphs, blocks, items, page.rect.height,
                               display_glyph_ids=_display_line_glyph_ids(glyphs, items, _rule_boxes(page)), ink=ink)
    from littrans.structure_profile import guidance_digest, structure_context
    profile_context = structure_context(root)
    if profile_context:
        # The guidance that applied to this page, not the profile file: extending the
        # profile for other pages leaves the page's ledger reproducible.
        structure["document_profile"] = {"path": profile_context["path"],
                                         "guidance_sha256": guidance_digest(profile_context["profile"], number),
                                         "authority": profile_context["authority"]}
    blocks = structure["blocks"]
    content_glyphs = [g for g in glyphs if g["id"] not in structure["markers"]]
    if override and "regions" in override:
        preserved = load_assets(root) if any("preserve_asset_id" in r for r in override["regions"]) else {}
        regions = [_declare_override_conditions(page, r, content_glyphs, preserved) for r in override["regions"]]
    else:
        regions = _regions(page, content_glyphs, items, ink=ink if hasattr(page, "get_svg_image") else None)
    if not glyphs and not regions and page.get_images():
        regions = [{"kind": "mixed-region", "bbox": list(page.rect), "grouping_pending": True, "display": True, "provenance": ["no-text-layer"]}]
    assets = [_asset(root, doc, number, source_hash, r, glyphs) for r in regions]
    for asset in assets:
        _formula_condition_glyphs(asset.model_dump(mode="json"), glyphs)
    by_id = {a.id: a for a in assets}
    if len(by_id) != len(assets):
        raise ValueError("duplicate region asset IDs")
    owners: dict[str, str] = {}
    for asset in assets:
        for gid in [gid for fragment in asset.fragments for gid in fragment.glyph_ids]:
            if gid in owners:
                raise ValueError(f"overlapping asset glyph ownership on page {number}: {gid}")
            owners[gid] = asset.id
    edge_spaces = {}
    for asset in assets:
        owned_glyphs = [g for g in glyphs if owners.get(g["id"]) == asset.id]
        edge_spaces[asset.id] = (bool(owned_glyphs and owned_glyphs[0]["text"].isspace()), bool(owned_glyphs and owned_glyphs[-1]["text"].isspace()))
    units, emitted = [], set()
    glyph_by_id = {g["id"]: g for g in glyphs}
    hyphenation = _hyphenation_evidence(doc, source_hash)
    for block in blocks:
        # Style the block as a whole: emphasis and hyphenated words cross line breaks.
        tokens: list[tuple[str, str]] = []
        footnote_refs = []
        last_inked: dict[str, Any] | None = None
        for line in block["lines"]:
            line_inked = [glyph_by_id[gid] for gid in line if inked_glyph(glyph_by_id[gid])]
            if tokens:
                same_row = (block["id"] in structure["display_blocks"] and last_inked is not None and line_inked
                            and abs(line_inked[0].get("baseline", 0) - last_inked.get("baseline", 0)) < 0.5 * last_inked.get("size", 10))
                # A line end is kept distinct from an in-line space until hyphens are resolved.
                # Two native lines of a displayed block on one baseline are one printed row.
                tokens.append((" " if same_row else "\n", ""))
            # The ink gap at each asset boundary on this line decides its space (LT-060/066):
            # the glyphs an asset owns on the line, and the unowned inked neighbours.
            boundary: dict[str, bool | None] = {}
            for position, gid in enumerate(line):
                aid = owners.get(gid)
                if not aid or (position and owners.get(line[position - 1]) == aid):
                    continue
                owned_here = [glyph_by_id[g] for g in line[position:] if owners.get(g) == aid and inked_glyph(glyph_by_id[g]) and g in ink]
                if not owned_here:
                    continue
                previous = next((glyph_by_id[g] for g in reversed(line[:position]) if owners.get(g) != aid and inked_glyph(glyph_by_id[g]) and g not in structure["markers"]), None)
                following = next((glyph_by_id[g] for g in line[position + 1:] if owners.get(g) != aid and inked_glyph(glyph_by_id[g]) and g not in structure["markers"]), None)
                # The asset's outermost ink on the line, not its first or last glyph in
                # native order (a superscript may be emitted last).
                leftmost = min(owned_here, key=lambda g: ink[g["id"]][0])
                rightmost = max(owned_here, key=lambda g: ink[g["id"]][2])
                boundary[f"before:{aid}"] = _boundary_space(previous, leftmost, ink)
                boundary[f"after:{aid}"] = _boundary_space(rightmost, following, ink)
            pending_space: tuple[str, str] | None = None
            after_asset: str | None = None
            for gid in line:
                aid = owners.get(gid)
                marker = structure["markers"].get(gid)
                style = font_style(glyph_by_id[gid]["font"])
                if marker:
                    if pending_space:
                        tokens.append(pending_space)
                    pending_space = after_asset = None
                    if not marker["definition"] and marker.get("emit", True):
                        tokens.append(("[^" + marker["number"] + "]", ""))
                        reference = f"p{number:04d}-" + marker["note_block"]
                        if reference not in footnote_refs:
                            footnote_refs.append(reference)
                elif aid:
                    if aid not in emitted:
                        before, after = edge_spaces[aid]
                        decided = boundary.get(f"before:{aid}")
                        if decided is not None:
                            before, pending_space = decided, None
                        elif pending_space:
                            tokens.append(pending_space)
                            pending_space = None
                        tokens.append(((" " if before else "") + "{{asset:" + aid + "}}", ""))
                        decided = boundary.get(f"after:{aid}")
                        if decided is None:
                            if after:
                                tokens.append((" ", ""))
                            after_asset = None
                        else:
                            after_asset = aid
                            if decided:
                                tokens.append((" ", ""))
                        emitted.add(aid)
                else:
                    glyph = glyph_by_id[gid]
                    if glyph["text"].isspace():
                        if after_asset is not None:
                            # The ink gap decided this boundary; the text layer's space is void.
                            continue
                        if glyph["bbox"][2] - glyph["bbox"][0] < glyph["size"] * KERN_SPACE_EM:
                            # A kern the PDF reports as a space glyph is not a word space.
                            continue
                        # A space before an asset waits for the ink decision at that boundary.
                        pending_space = (glyph["text"], style)
                        continue
                    if pending_space:
                        tokens.append(pending_space)
                        pending_space = None
                    after_asset = None
                    tokens.append((glyph["text"], style))
            if pending_space:
                tokens.append(pending_space)
            if line_inked:
                last_inked = line_inked[-1]
        text = _rejoin_line_breaks(styled_text(tokens).strip(), hyphenation)
        if block["id"] in structure["display_blocks"]:
            # A displayed block keeps its rows (a cases formula with native condition
            # words); the renderer stacks them instead of flowing them into one line.
            text = "\n".join(row for row in (re.sub(r" {2,}", " ", line.strip()) for line in text.split("\n")) if row)
        else:
            text = re.sub(r" {2,}", " ", re.sub(r"[ \t]*\n[ \t]*", " ", text))
        # A TeX math skip before sentence punctuation is not a textual space.
        text = re.sub(r"(\{\{asset:[^}]+\}\}) +([.,;:])", r"\1\2", text)
        if text:
            kind = "paragraph"
            semantic_labels = {"doc_title": "heading", "paragraph_title": "heading", "title": "heading", "figure_caption": "caption", "figure_title": "caption", "table_caption": "caption", "table_title": "caption", "vision_footnote": "caption", "footnote": "footnote", "reference": "bibliography", "list": "list_item"}
            for item in items:
                if item.get("label") in semantic_labels and _inside({"bbox": block["bbox"]}, [v / 2 for v in item["bbox"]]):
                    kind = semantic_labels[item["label"]]
                    break
            if kind == "paragraph" and is_bullet_line([glyph_by_id[gid] for line in block["lines"] for gid in line if inked_glyph(glyph_by_id[gid])]):
                kind = "list_item"
            if kind == "paragraph" and len(text.split()) <= 8:
                sizes = [glyph_by_id[gid]["size"] for line in block["lines"] for gid in line if glyph_by_id[gid]["text"].isalpha()]
                if sizes and sorted(sizes)[len(sizes) // 2] >= structure["font_size"] * 1.25:
                    kind = "heading"
            if kind == "heading":
                # The heading face carries the emphasis; whole-heading markers only add noise.
                whole = re.fullmatch(r"(\*{1,3})(.+)\1", text)
                if whole and whole[1] not in whole[2]:
                    text = whole[2]
            if kind == "paragraph" and block["id"] in structure["display_blocks"]:
                # A displayed line the detector labelled as a formula but that also
                # carries prose ("... for all times t > 0.") stays one display unit.
                kind = "equation"
            block_refs = asset_reference_ids(text)
            box = block["bbox"]
            if block_refs and not re.sub(r"\{\{asset:[^}]+\}\}", "", text).strip():
                # A block that only carries a whole figure/table (its internal labels are
                # native glyphs) is that element, not a prose paragraph, and its box is the
                # element's, not the labels' line.
                kinds = {by_id[aid].kind for aid in block_refs}
                if kinds == {"figure"}:
                    kind = "figure"
                elif kinds == {"table"}:
                    kind = "table"
                if kind in {"figure", "table"}:
                    box = _union([f.bbox for aid in block_refs for f in by_id[aid].fragments])
            units.append(_make_unit(number, f"p{number:04d}-{block['id']}", text, box, by_id, kind=kind, footnote_refs=list(dict.fromkeys(footnote_refs))))
    for asset in assets:
        if asset.id not in emitted:
            fragment = asset.fragments[0]
            # A rule is recognised by what it is (a glyph-free mixed region, thin and wide),
            # not by who proposed it: a reviewer's region qualifies like a native drawing,
            # a detector-labelled element or an embedded image never does.
            decorative = (
                asset.kind == "mixed-region"
                and not any(p.startswith("PP-DocLayoutV2:") or p == "native-image" for p in asset.provenance)
                and not fragment.glyph_ids
                and fragment.height <= DECORATIVE_RULE_MAX_HEIGHT
                and fragment.width >= DECORATIVE_RULE_MIN_ASPECT * fragment.height
            )
            if decorative:
                # A bare horizontal rule (chapter ornament, running-head line) is
                # layout, not content; it stays in the ledger but is not read.
                by_id[asset.id] = asset.model_copy(update={"grouping_pending": False})
                units.append(_make_unit(number, f"p{number:04d}-visual-{asset.id}", "{{asset:" + asset.id + "}}", fragment.bbox, by_id, kind="note", render_policy="omit", translatable=False))
                continue
            units.append(_make_unit(number, f"p{number:04d}-visual-{asset.id}", "{{asset:" + asset.id + "}}", fragment.bbox, by_id, kind="figure" if asset.kind == "figure" else "paragraph"))
    # Native block order preserves PDF column flow and inline continuations;
    # y-sorting interleaves columns and moves tall formula suffixes before prose.
    native_order = {f"p{number:04d}-{block['id']}": index for index, block in enumerate(blocks)}
    def order(unit: SourceUnit) -> float:
        if unit.unit_id in native_order:
            return float(native_order[unit.unit_id])
        # A visual element sits after the last body chunk that ends above it. Running
        # material never positions it: a detached page number is appended after the body
        # with its page-top bbox, and would otherwise anchor every figure to the page end.
        previous = [
            index for index, block in enumerate(blocks)
            if block["id"] not in structure["omitted"] and block["bbox"][3] <= unit.bbox[1]
        ]
        return max(previous, default=-1) + 0.5
    units.sort(key=order)
    units = _separate_display_units(units, by_id, {f"p{number:04d}-{bid}": n["number"] for bid, n in structure["notes"].items()})
    units = assemble_structure(units, by_id, structure, _make_unit, rejoin=lambda text: _rejoin_line_breaks(text, hyphenation))
    # Adjacent inline fragments coalesce in both channels: a reviewer's units may name
    # the coalesced asset the record shows, or its constituents; each merge is kept
    # only when the units reference it.
    constituents = dict(by_id)
    units = coalesce_inline_assets(units, by_id, _make_unit, _hash)
    if override and "units" in override:
        referenced = {aid for item in override["units"] for aid in asset_reference_ids(item["source_markdown"])}
        for aid in [aid for aid in by_id if aid not in constituents]:
            if aid in referenced:
                continue
            merged = by_id.pop(aid)
            parts = {tuple(f.glyph_ids) for f in merged.fragments}
            for old, asset in constituents.items():
                if old not in by_id and all(tuple(f.glyph_ids) in parts for f in asset.fragments):
                    by_id[old] = asset
    assets = list(by_id.values())
    owners = {gid:a.id for a in assets for f in a.fragments for gid in f.glyph_ids}
    if override and "units" in override:
        # Every page asset exactly once, checked before any unit is built: a recorded
        # override whose asset moved (a display that gained a space glyph) is refused
        # with both IDs named, not lost in a lookup error inside `_make_unit`.
        counts = Counter(aid for item in override["units"] for aid in asset_reference_ids(item["source_markdown"]))
        missing = sorted(aid for aid in counts if aid not in by_id)
        unreferenced = sorted(aid for aid in by_id if aid not in counts)
        repeated = sorted(aid for aid, n in counts.items() if n > 1)
        if missing or unreferenced or repeated:
            raise ValueError(
                "unit overrides must reference each page asset exactly once"
                + (f"; not cut on this page any more: {', '.join(missing)}" if missing else "")
                + (f"; cut but unreferenced: {', '.join(unreferenced)}" if unreferenced else "")
                + (f"; referenced more than once: {', '.join(repeated)}" if repeated else "")
            )
        units = []
        for item in override["units"]:
            if item.get("latex") is not None:
                raise ValueError("source preservation cannot import LaTeX")
            # The same optional keys the pipeline's structure assembly passes, so a unit
            # carried over as recorded keeps the hash the pipeline gave it.
            extra = {k: item.get(k, default) for k, default in OVERRIDE_UNIT_DEFAULTS.items()}
            units.append(_make_unit(number, item["unit_id"], item["source_markdown"], item["bbox"], by_id, item.get("kind", "paragraph"), **extra))
    # Structure assembly can change grouping or create coalesced assets after export.
    # Bind the final semantics before publishing either assets or their source units.
    for asset in assets:
        asset.content_identity_version = 2
        asset.content_sha256 = _asset_content_identity(asset)
    for unit in units:
        hashes = {aid: by_id[aid].content_sha256 for aid in asset_reference_ids(unit.source_markdown or unit.source_text)}
        if hashes != unit.asset_content_hashes:
            unit.asset_content_hashes = hashes
            unit.source_hash = _hash({"prepared_source_hash": unit.source_hash, "asset_content_hashes": hashes})
    ledger = {"schema_version": 6, "page": number, "source_sha256": source_hash, "width": page.rect.width, "height": page.rect.height, "page_image": str(image_path.relative_to(root)).replace("\\", "/"), "page_image_sha256": image_sha256, "layout_status": layout["status"], "layout_reason": layout.get("reason"), "layout_fingerprint": layout.get("fingerprint"), "glyphs": [{**g, "owner": owners.get(g["id"], "native-text")} for g in glyphs], "vector_regions": [_box(d["rect"]) for d in page.get_drawings()], "raster_regions": [_box(i["bbox"]) for i in page.get_image_info()], "asset_ids": list(by_id), "unit_ids": [u.unit_id for u in units], "grouping_pending": [a.id for a in assets if a.grouping_pending], "reading_order_review_required": True, "source_overrides": override, "structure": {k: v for k, v in structure.items() if k != "blocks"}}
    if override is not None and override_origin:
        # Where the correction came from, so a replayed override stays auditable.
        ledger["source_overrides_origin"] = override_origin
    if overflow_evidence:
        ledger["original_page_bbox"] = original_page_bbox
        ledger["overflow_evidence"] = overflow_evidence
    # The build identity is recorded but never fingerprinted: identical content keeps its
    # page fingerprint (and its review) across builds and reruns.
    ledger["fingerprint"] = _hash({"ledger": ledger, "units": [u.model_dump(mode="json", exclude={"verification_status"}) for u in units], "assets": [a.model_dump(mode="json") for a in assets]})
    ledger["generator"] = build_identity()
    return units, assets, ledger


def _invalidate(root: Path, old: list[SourceUnit], new: list[SourceUnit]) -> None:
    old_map = {u.unit_id: u.source_hash for u in old}
    new_map = {u.unit_id: u.source_hash for u in new}
    changed = {uid for uid in old_map.keys() | new_map.keys() if old_map.get(uid) != new_map.get(uid)}
    removed = old_map.keys() - new_map.keys()
    if removed:
        current_path = root / "translations/current.jsonl"
        current = read_jsonl(current_path, TranslationRecord)
        retired = [record for record in current if record.unit_id in removed]
        if retired:
            history_path = root / "translations/source-retired.jsonl"
            history = read_jsonl(history_path, TranslationRecord)
            write_jsonl(history_path, [*history, *retired])
            write_jsonl(current_path, [record for record in current if record.unit_id not in removed])
    from littrans.evidence import record_audit_invalidation
    from littrans.storage import read_yaml

    for path in (root / "batches").glob("*/manifest.yaml"):
        manifest = read_yaml(path)
        affected = set(manifest["unit_ids"]) & changed
        if affected:
            record_audit_invalidation(root, manifest["batch_id"], affected)


ASSET_DIRECTORY = "derived/assets/fidelity"


def _live_asset_directories(assets: Iterable[FidelityAsset]) -> set[str]:
    """Directory names the current fragment records point at.

    The directory name is the export identity of the crop; it is not the asset's
    ``content_sha256`` (which also covers kind, display and grouping state).
    """
    return {Path(relative).parent.name for asset in assets for fragment in asset.fragments
            for relative in (fragment.png_path, fragment.svg_path, fragment.pdf_path) if relative}


def prune_asset_directories(root: Path, assets: Iterable[FidelityAsset], *, apply: bool) -> dict[str, Any]:
    """List or remove crop directories that no current fragment record refers to.

    Every re-export under a changed geometry creates a new content-addressed directory
    and leaves the old one behind; orphans are indistinguishable by shape or name.
    """
    base = root / ASSET_DIRECTORY
    live = _live_asset_directories(assets)
    candidates: list[str] = []
    candidate_bytes = 0
    if base.is_dir():
        for directory in sorted(base.iterdir()):
            if not directory.is_dir() or directory.name in live:
                continue
            directory.resolve().relative_to(base.resolve())
            candidates.append(directory.name)
            candidate_bytes += sum(item.stat().st_size for item in directory.rglob("*") if item.is_file())
    removed: list[str] = []
    if apply:
        for name in candidates:
            shutil.rmtree(base / name)
            removed.append(name)
    return {"mode": "apply" if apply else "dry-run", "candidates": candidates, "candidate_bytes": candidate_bytes, "removed": removed}


def source_packet_liveness(root: Path) -> dict[str, list[str]]:
    """Which source review packets the page receipts depend on.

    A packet named by any receipt is live; other packets are unreferenced by receipts,
    which does not make them disposable: a review file not yet imported may name one.
    """
    referenced = {str(read_json(path).get("packet_id")) for path in (root / "evidence/pages").glob("fidelity-p[0-9][0-9][0-9][0-9].review.json")}
    packets = sorted(path.name for path in (root / "packets").glob("source-*") if path.is_dir())
    return {"live_source_packets": [name for name in packets if name in referenced],
            "unreferenced_source_packets": [name for name in packets if name not in referenced]}


def gc_asset_directories(root: Path, apply: bool = False) -> dict[str, Any]:
    """Reclaim crop directories orphaned by earlier `source prepare --replace` runs.

    Source review packets are only reported, never removed: the ones receipts name are
    review dependencies.
    """
    root = Path(root).resolve()
    with project_write_lock(root):
        return {**prune_asset_directories(root, load_assets(root).values(), apply=apply), **source_packet_liveness(root)}


def _dependent_pages(root: Path, pages: Iterable[int], units: list[SourceUnit]) -> list[int]:
    """Prepared pages outside ``pages`` whose receipt depends on a unit of ``pages``.

    A receipt fingerprints the continuation and container closure of its page, so a
    change to one page moves the fingerprint of every page that closure reaches.
    """
    from littrans.evidence import page_evidence_units

    targets = set(pages)
    prepared = {int(path.stem[1:]) for path in (root / "derived/fidelity-pages").glob("p[0-9][0-9][0-9][0-9].json")}
    return [page for page in sorted(prepared - targets)
            if any(unit.page in targets for unit in page_evidence_units(page, units))]


def _page_fingerprint(root: Path, page: int, units: list[SourceUnit], assets: dict[str, FidelityAsset]) -> str | None:
    """The current page fingerprint, or None when the page cannot be verified at all."""
    try:
        return str(_current_page(root, page, units, assets)["fingerprint"])
    except (OSError, ValueError, KeyError):
        return None


def _receipt_path(root: Path, page: int) -> Path:
    return root / f"evidence/pages/fidelity-p{page:04d}.review.json"


def _settle_receipts(root: Path, pages: Iterable[int], before: dict[int, str | None], units: list[SourceUnit],
                     assets: dict[str, FidelityAsset]) -> tuple[list[int], list[int]]:
    """Keep the receipts of pages whose fingerprint did not move; remove the others.

    Returns the pages whose receipt was retained and the pages whose receipt was
    removed because their content, or a dependency's, changed.
    """
    retained, invalidated = [], []
    for page in sorted(set(pages)):
        receipt_path = _receipt_path(root, page)
        if not receipt_path.is_file():
            continue
        current = _page_fingerprint(root, page, units, assets)
        recorded = read_json(receipt_path).get("fingerprint")
        if current is not None and current == recorded and current == before.get(page, current):
            retained.append(page)
        else:
            receipt_path.unlink()
            invalidated.append(page)
    return retained, invalidated


def prepare_source(root: Path, page_spec: str = "all", replace: bool = False,
                   allow_missing_layout: bool = False, discard_overrides: bool = False,
                   redetect: bool = False) -> dict[str, Any]:
    """Prepare pages from the source PDF.

    A page whose ledger records a reviewer's ``source_overrides`` is re-prepared by
    replaying that override (the human decision is reproduced, not re-derived) unless
    ``discard_overrides`` is set. A re-prepared page is cut on the detector result its
    ledger records whenever ``derived/fidelity-layout/`` still holds it — the result is
    evidence of the record, reproducible on no other runtime — so a rerun on another
    build or host reproduces the page; ``redetect`` runs the detector afresh instead.
    Receipts survive when the page content, and that of the pages depending on it, did
    not change.
    """
    from contextlib import ExitStack

    root = Path(root).resolve()
    config = load_project(root)
    source = config.source(root)
    digest = sha256_file(source)
    if digest != config.source_sha256:
        raise ValueError("source PDF changed; rebuild project before preparing")
    pages = parse_page_spec(page_spec, config.source_pages)
    from littrans.structure_profile import structure_context
    profile_context = structure_context(root)
    replayed: list[int] = []
    redetected: list[int] = []
    discarded: list[int] = []
    reused: list[int] = []
    with ExitStack() as stack:
        stack.enter_context(project_write_lock(root))
        old = read_jsonl(root / "derived/units.jsonl", SourceUnit)
        registry = load_assets(root)
        needed = [p for p in pages if replace or not _page_path(root, p).is_file()]
        if not needed:
            return {"pages": pages, "prepared_pages": [], "cached_pages": pages, "assets": len(registry), "requires_visual_review": True, "document_structure": profile_context}
        old_ledgers = {p: read_json(_page_path(root, p)) for p in needed if _page_path(root, p).is_file()}
        dependents = _dependent_pages(root, needed, old)
        stack.enter_context(_authority_transaction(root, [*pages, *dependents]))
        doc = stack.enter_context(fitz.open(source))
        before = {p: _page_fingerprint(root, p, old, registry) for p in [*needed, *dependents] if _receipt_path(root, p).is_file()}
        images = {}
        for number in needed:
            image = root / f"evidence/pages/fidelity-p{number:04d}.png"
            image.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_bytes(image, doc[number - 1].get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes("png"))
            images[number] = image
        # The recorded detector result is a correctness input of a rerun, not a cache: a
        # page is detected again only when its ledger records no usable result (or on
        # request), so a rerun without the runtime still reproduces the recorded pages.
        recorded_layouts: dict[int, dict[str, Any]] = {}
        if not redetect:
            for number in needed:
                recorded = old_ledgers.get(number)
                if recorded and recorded.get("layout_status") == "ok":
                    cached = _cached_layout(root, recorded)
                    if cached["status"] == "ok":
                        recorded_layouts[number] = cached
        fresh = [number for number in needed if number not in recorded_layouts]
        if fresh:
            layout = detect_layout([images[number] for number in fresh], root / "derived/fidelity-layout")
            if layout["status"] != "ok" and not allow_missing_layout:
                raise ValueError(
                    "Layout runtime unavailable: " + str(layout.get("reason")) + ". Run `littrans layout install` "
                    "or, only at the user's explicit request, rerun with --allow-missing-layout."
                )
        else:
            layout = {"status": "reused", "reason": None, "pages": {}}
        units = [u for u in old if u.page not in needed]
        registry = {aid: a for aid, a in registry.items() if not any(f.page in needed for f in a.fragments)}
        ledgers = []
        for number in needed:
            recorded = old_ledgers.get(number, {})
            override = recorded.get("source_overrides")
            page_layout = recorded_layouts.get(number)
            if page_layout is not None:
                reused.append(number)
            else:
                page_layout = layout
            if override and not discard_overrides:
                # The recorded detector result keeps the replay exact. When the ledger
                # records one that is gone (or `redetect` set it aside), the override is
                # replayed on this run's fresh detection and the page is named in
                # `redetected_override_pages`.
                if (number not in recorded_layouts and recorded.get("layout_status") == "ok"
                        and page_layout.get("fingerprint") != recorded.get("layout_fingerprint")):
                    redetected.append(number)
                try:
                    page_units, page_assets, ledger = _page_prepare(root, doc, number, digest, page_layout, override, recorded.get("source_overrides_origin"))
                except ValueError as exc:
                    raise ValueError(f"page {number}: the recorded source override cannot be replayed ({exc}); "
                                     "re-import its review file or rerun with --discard-overrides") from exc
                replayed.append(number)
            else:
                page_units, page_assets, ledger = _page_prepare(root, doc, number, digest, page_layout)
                if override:
                    discarded.append(number)
            units.extend(page_units)
            registry.update({a.id: a for a in page_assets})
            ledgers.append(ledger)
        # Reject an inconsistent footnote graph before publishing; a failure here
        # rolls the whole page set back instead of leaving unverifiable units.
        _validate_footnote_relationships(units)
        _invalidate(root, old, units)
        units.sort(key=lambda u: u.page)
        write_jsonl(root / "derived/units.jsonl", units)
        write_jsonl(root / "derived/fidelity-assets.jsonl", registry.values())
        for ledger in ledgers:
            write_json(_page_path(root, ledger["page"]), ledger)
        # A receipt outlives a rerun that reproduced the page byte for byte; a dependency
        # page whose fingerprint moved loses its receipt explicitly, not silently.
        retained, invalidated = _settle_receipts(root, [*needed, *dependents], before, units, registry)
        # A page whose receipt outlived the rerun is still verified: its units say so, as
        # they did before, instead of reading as fresh work until the next review import.
        if any(unit.page in retained for unit in units):
            for unit in units:
                if unit.page in retained:
                    unit.verification_status = SemanticStatus.VERIFIED
            write_jsonl(root / "derived/units.jsonl", units)
    # Crops the replaced pages no longer refer to are reclaimed only once the new
    # authority is committed; the transaction snapshots files, not directories.
    pruned = prune_asset_directories(root, registry.values(), apply=True)
    packet = build_source_review_packet(root, ",".join(map(str, pages)))
    return {"pages": pages, "prepared_pages": needed, "cached_pages": [p for p in pages if p not in needed], "assets": len(registry),
            "replayed_override_pages": replayed, "redetected_override_pages": redetected, "discarded_override_pages": discarded,
            "reused_layout_pages": reused, "detected_layout_pages": fresh,
            "retained_receipt_pages": retained, "invalidated_pages": [p for p in invalidated if p not in needed],
            "pruned_asset_directories": pruned["removed"], "layout_status": layout["status"], "requires_visual_review": True,
            "review_packet": packet["packet_path"], "visual_report": packet["visual_report"], "document_structure": profile_context, "generator": build_identity()}


def _cached_layout(root: Path, ledger: dict[str, Any]) -> dict[str, Any]:
    """Reuse the page's recorded detector result so corrections keep its layout evidence.

    The recorded result is a correctness input of every replay, not a performance cache:
    it is found by the ledger's ``layout_fingerprint`` and the page image's content, so a
    moved or cloned tree still reads its own evidence. A result the ledger records but
    ``derived/fidelity-layout/`` no longer holds comes back ``unavailable`` with a reason
    that names the missing fingerprint; callers decide whether that stops them.
    """
    fallback = {"status": ledger["layout_status"], "reason": ledger.get("layout_reason"), "pages": {}}
    fingerprint = ledger.get("layout_fingerprint")
    if ledger["layout_status"] != "ok" or not fingerprint:
        return fallback
    page_image = ledger.get("page_image")
    if not page_image:
        return {**fallback, "status": "unavailable", "reason": "ledger names no page image"}
    image = _path(root, page_image)
    store = root / "derived/fidelity-layout"
    # The content-addressed file first; results of earlier builds were named by page set.
    candidates = [layout_result_path(store, fingerprint), *sorted(store.glob("*.json"))]
    for path in dict.fromkeys(candidates):
        if path.name.endswith(".request.json") or not path.is_file():
            continue
        try:
            payload = read_json(path)
        except (OSError, ValueError):
            continue
        if (payload.get("fingerprint") == fingerprint and payload.get("status") == "ok"
                and layout_page_items(payload, image, ledger.get("page_image_sha256")) is not None):
            return payload
    return {**fallback, "status": "unavailable",
            "reason": f"the layout result the ledger records (fingerprint {fingerprint}) is missing from derived/fidelity-layout/"}


def _validate_footnote_relationships(units: list[SourceUnit]) -> None:
    from littrans.semantics import explicit_footnote_numbers

    unit_map = {u.unit_id: u for u in units}
    for unit in units:
        if len(unit.footnote_refs) != len(set(unit.footnote_refs)) or any(
            ref not in unit_map or unit_map[ref].kind != UnitKind.FOOTNOTE for ref in unit.footnote_refs
        ):
            raise ValueError(f"invalid or duplicated footnote relationship: {unit.unit_id}")
        numbers = [unit_map[ref].footnote_number for ref in unit.footnote_refs]
        text = unit.source_markdown or unit.source_text
        if unit.table:
            text = "\n".join(cell for row in unit.table.rows for cell in row)
        if (len(set(numbers)) != len(numbers) or any(not number for number in numbers)
                or explicit_footnote_numbers(text) != set(numbers)):
            raise ValueError(f"footnote call numbers do not match referenced definitions: {unit.unit_id}")


def _current_units(root: Path) -> list[SourceUnit]:
    """Load and validate the project's source units once for a multi-page check."""
    all_units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    if len({u.unit_id for u in all_units}) != len(all_units):
        raise ValueError("duplicate source unit IDs anywhere in project")
    _validate_footnote_relationships(all_units)
    return all_units


def _current_page(root: Path, number: int, all_units: list[SourceUnit] | None = None,
                  assets: dict[str, FidelityAsset] | None = None) -> dict[str, Any]:
    ledger = read_json(_page_path(root, number))
    if all_units is None:
        all_units = _current_units(root)
    if assets is None:
        assets = load_assets(root)
    units = [u for u in all_units if u.page == number]
    selected = [assets[aid] for aid in ledger["asset_ids"]]
    files = {ledger["page_image"]: sha256_file(_path(root, ledger["page_image"]))}
    if files[ledger["page_image"]] != ledger["page_image_sha256"]:
        raise ValueError("original page image changed")
    if ledger.get("overflow_evidence"):
        overflow = ledger["overflow_evidence"]
        files[overflow["path"]] = sha256_file(_path(root, overflow["path"]))
        if files[overflow["path"]] != overflow["sha256"]:
            raise ValueError("overflow canvas image changed")
    for asset in selected:
        if asset.source_sha256 != ledger["source_sha256"]:
            raise ValueError("asset PDF fingerprint does not match page")
        expected_content = _asset_content_identity(asset)
        if asset.content_sha256 != expected_content:
            raise ValueError("asset region content fingerprint does not match original provenance")
        for fragment in asset.fragments:
            for relative, expected in fragment.file_sha256.items():
                path = _path(root, relative)
                if not path.is_file() or sha256_file(path) != expected:
                    raise ValueError(f"missing or changed original crop: {relative}")
                files[relative] = expected
    from littrans.evidence import page_evidence_units

    dependencies = [u for u in page_evidence_units(number, all_units) if u.page != number]
    ledger = {key: value for key, value in ledger.items() if key != "generator"}
    payload = {"ledger": ledger, "units": [u.model_dump(mode="json", exclude={"verification_status"}) for u in units], "dependency_units": [u.model_dump(mode="json", exclude={"verification_status"}) for u in dependencies], "assets": [a.model_dump(mode="json") for a in selected], "files": files}
    return {"page": number, "fingerprint": _hash(payload), **payload}


def build_source_review_packet(root: Path, page_spec: str = "all") -> dict[str, Any]:
    root = Path(root).resolve()
    config = load_project(root)
    if sha256_file(config.source(root)) != config.source_sha256:
        raise ValueError("source PDF changed; rebuild before reviewing")
    pages = parse_page_spec(page_spec, config.source_pages)
    from littrans.evidence import page_evidence_units

    all_units = _current_units(root)
    assets = load_assets(root)
    pages = sorted(set(pages) | {u.page for page in pages for u in page_evidence_units(page, all_units)})
    payload: dict[str, Any] = {"schema_version": 6, "kind": "source-fidelity-review", "source_sha256": sha256_file(config.source(root)), "pages": [_current_page(root, p, all_units, assets) for p in pages]}
    # The writing build is recorded but is not part of the packet identity: identical
    # content keeps its packet ID (and its reviews) across builds and reruns.
    payload["generator"] = build_identity()
    from littrans.structure_profile import structure_context
    profile_context = structure_context(root)
    if profile_context:
        payload["document_structure"] = profile_context
    for page in payload["pages"]:
        page["boundary_diagnostics"] = _boundary_diagnostics(page["ledger"]["glyphs"], page["assets"])
    sections = []
    if profile_context:
        sections.append('<h2>Document-specific structure guidance</h2><pre>' + html.escape(json.dumps(profile_context, ensure_ascii=False, indent=2)) + '</pre>')
    for p in payload["pages"]:
        ledger = p["ledger"]
        if p["boundary_diagnostics"]:
            sections.append("<h3>Prose/formula boundary diagnostics</h3><pre>" + html.escape(json.dumps(p["boundary_diagnostics"], ensure_ascii=False, indent=2)) + "</pre>")
        boxes = "".join(f'<rect x="{f["bbox"][0]}" y="{f["bbox"][1]}" width="{f["width"]}" height="{f["height"]}" fill="none" stroke="red" stroke-width="0.6"><title>{html.escape(a["id"])}</title></rect>' for a in p["assets"] for f in a["fragments"])
        image_uri = "../../" + quote(ledger["page_image"], safe="/")
        if ledger.get("overflow_evidence"):
            sections.append(f'<p>PDF page {p["page"]}: <a href="{image_uri}">original page canvas</a>. The overlay below uses the separately preserved expanded content-stream canvas.</p>')
            image_uri = "../../" + quote(ledger["overflow_evidence"]["path"], safe="/")
        source = "\n\n".join(u["source_text"] for u in p["units"])
        sections.append(f'<section><h2>PDF page {p["page"]}</h2><p>Layout: {html.escape(ledger["layout_status"])}; visual review required.</p><svg viewBox="0 0 {ledger["width"]} {ledger["height"]}"><image href="{image_uri}" width="{ledger["width"]}" height="{ledger["height"]}"/>{boxes}</svg><pre>{html.escape(source)}</pre></section>')
    report_text = '<!doctype html><meta charset="utf-8"><title>Source fidelity review</title><style>body{font:16px sans-serif;max-width:1500px;margin:auto}svg{width:65%;vertical-align:top}pre{white-space:pre-wrap;display:inline-block;width:33%;font:14px sans-serif}</style><h1>Original page, region boundaries and reading sequence</h1>' + "".join(sections)
    report_files = {}
    for page in payload["pages"]:
        ledger = page["ledger"]
        report_files[ledger["page_image"]] = ledger["page_image_sha256"]
        if ledger.get("overflow_evidence"):
            relative = ledger["overflow_evidence"]["path"]
            report_files[relative] = sha256_file(_path(root, relative))
    payload["visual_report"] = {"path": "coverage.html", "sha256": sha256_text(report_text), "files": report_files}
    packet_id = "source-" + _source_packet_identity(payload)
    # Do not repair a previously reviewed artifact under the same identity, and do not
    # rewrite it either: its bytes are what existing reviews are bound to.
    directory = root / "packets" / packet_id
    packet = directory / "packet.json"
    while directory.exists():
        try:
            _load_source_packet(root, packet_id, sha256_file(packet))
            return {"packet_id": packet_id, "packet_path": str(packet), "packet_sha256": sha256_file(packet), "review_template": str(directory / "review-template.json"), "visual_report": str(directory / "coverage.html"), "pages": pages}
        except (OSError, ValueError, KeyError):
            payload["previous_visual_packet_id"] = packet_id
            packet_id = "source-" + _source_packet_identity(payload)
            directory = root / "packets" / packet_id
            packet = directory / "packet.json"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(packet, payload)
    # The template lists what the ledger knows (declared conditions, pending grouping
    # decisions, findings) so the reviewer confirms a list instead of guessing from crops.
    review_template = {"packet_id": packet_id, "packet_sha256": sha256_file(packet), "visual_report_sha256": payload["visual_report"]["sha256"], "reviewer": "", "pages": [
        {"page": p["page"], "fingerprint": p["fingerprint"], "viewed_original": False, "coverage_complete": False, "boundaries_complete": False, "reading_order_correct": False,
         "grouping_checked": False, "layout_fallback_checked": False, "formula_conditions_checked": False, "overflow_canvas_checked": False,
         "accepted_grouping_pending": [], "issues": [], "notes": "",
         "context": {"formula_conditions": _declared_conditions(p), "grouping_pending": [a["id"] for a in p["assets"] if a.get("grouping_pending")],
                     "findings": page_review_findings(p), "boundary_diagnostics": p["boundary_diagnostics"]}}
        for p in payload["pages"]]}
    write_json(directory / "review-template.json", review_template)
    report = directory / "coverage.html"
    atomic_write_text(report, report_text)
    return {"packet_id": packet_id, "packet_path": str(packet), "packet_sha256": sha256_file(packet), "review_template": str(directory / "review-template.json"), "visual_report": str(report), "pages": pages}


def _source_packet_identity(payload: dict[str, Any]) -> str:
    return _hash({k: v for k, v in payload.items() if k != "generator"})[:20]


def _load_source_packet(root: Path, packet_id: str, packet_sha256: str) -> dict[str, Any]:
    if not isinstance(packet_id, str) or not re.fullmatch(r"source-[a-f0-9]{20}", packet_id):
        raise ValueError("invalid source packet ID")
    packet_path = root / "packets" / packet_id / "packet.json"
    if not packet_path.is_file() or not (packet_path.parent / "coverage.html").is_file():
        # The packet a receipt names is a live dependency of that review, however many
        # newer packets exist; deleting it as a leftover voids the review.
        raise ValueError(f"source review packet {packet_id} is a live dependency of a page receipt but is missing from packets/; "
                         "restore the directory or import a fresh visual review")
    if sha256_file(packet_path) != packet_sha256:
        raise ValueError("source packet hash mismatch")
    packet = read_json(packet_path)
    if not isinstance(packet, dict) or "source-" + _source_packet_identity(packet) != packet_id:
        raise ValueError("source packet identity mismatch")
    if packet.get("kind") != "source-fidelity-review" or packet.get("schema_version") != 6:
        raise ValueError("invalid source packet contract")
    artifact = packet.get("visual_report", {})
    if artifact.get("path") != "coverage.html" or not artifact.get("sha256"):
        raise ValueError("source visual report manifest missing; create a new review packet")
    if sha256_file(packet_path.parent / "coverage.html") != artifact["sha256"]:
        raise ValueError("source visual report artifact changed")
    files = artifact.get("files")
    if not isinstance(files, dict) or not files:
        raise ValueError("source report image manifest missing")
    for relative, expected in files.items():
        if sha256_file(_path(root, relative)) != expected:
            raise ValueError("source report image changed")
    return packet


def _declared_conditions(page: dict[str, Any]) -> list[dict[str, Any]]:
    """The formula conditions the page's assets declare, for the reviewer to check as a list."""
    rows = []
    for asset in page["assets"]:
        for condition in asset.get("formula_conditions", []):
            fragment = next((f for f in asset["fragments"] if set(condition["glyph_ids"]) <= set(f["glyph_ids"])), asset["fragments"][0])
            rows.append({"asset_id": asset["id"], "source_text": condition["source_text"], "bbox": fragment["bbox"], "display": bool(asset.get("display"))})
    return rows


def _undeclared_formula_language(page: dict[str, Any]) -> dict[str, list[str]]:
    """Language inside a math crop that no formula condition declares, per asset."""
    glyph_by_id = {g["id"]: g for g in page["ledger"]["glyphs"]}
    result: dict[str, list[str]] = {}
    for asset in page["assets"]:
        if asset["kind"] != "math":
            continue
        owned = [gid for fragment in asset["fragments"] for gid in fragment["glyph_ids"]]
        declared = {gid for condition in asset.get("formula_conditions", []) for gid in condition["glyph_ids"]}
        missing = [condition["source_text"] for condition in _auto_formula_conditions({"glyph_ids": owned}, glyph_by_id)
                   if not set(condition["glyph_ids"]) <= declared]
        if missing:
            result[asset["id"]] = missing
    return result


def _accepted_grouping_items(decision: dict[str, Any]) -> list[dict[str, Any]]:
    """The ``accepted_grouping_pending`` entries of a decision, each ``{asset_id, reason}``.

    An entry in another shape (a bare asset ID string, say) is refused rather than
    skipped: silently ignoring it leaves the page rejected for ``grouping-pending`` with
    no hint that the acceptance never counted.
    """
    items = decision.get("accepted_grouping_pending") or []
    if not isinstance(items, list) or any(not isinstance(item, dict) or not isinstance(item.get("asset_id"), str) for item in items):
        raise ValueError('accepted_grouping_pending must be a list of objects {"asset_id": ..., "reason": ...}; got '
                         + json.dumps(items, ensure_ascii=False)[:200])
    return items


def page_review_findings(page: dict[str, Any], decision: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """What the ledger already knows still needs a reviewer's decision on a packet page.

    One predicate serves the approval gate, the review template and the checkpoint's
    attention list, so an approved page and a page needing no attention are the same
    thing. ``decision`` may accept pending grouping decisions by asset ID with a reason.
    """
    decision = decision or {}
    findings: list[dict[str, Any]] = []
    accepted = {item["asset_id"]: str(item.get("reason", "")).strip() for item in _accepted_grouping_items(decision)}
    pending = [asset["id"] for asset in page["assets"] if asset.get("grouping_pending")]
    unaccepted = [aid for aid in pending if not accepted.get(aid)]
    if unaccepted:
        findings.append({"code": "grouping-pending", "asset_ids": unaccepted,
                         "action": "Decide the grouping with an override, or list each asset in accepted_grouping_pending with a reason."})
    for aid, texts in _undeclared_formula_language(page).items():
        findings.append({"code": "undeclared-formula-language", "asset_id": aid, "source_texts": texts,
                         "action": "Language inside this math crop is not declared as a formula condition; re-prepare or declare it in a region override."})
    opaque = _opaque_prose_assets(page)
    if opaque:
        findings.append({"code": "recoverable-prose-in-image", "asset_ids": opaque,
                         "action": "Split the source region so the paragraph is text, then obtain a fresh packet."})
    return findings


def _source_decision_failures(page: dict[str, Any], decision: dict[str, Any]) -> list[str]:
    """Why a page decision does not approve the page; empty when it does."""
    fields = ["viewed_original", "coverage_complete", "boundaries_complete", "reading_order_correct", "grouping_checked"]
    if page["ledger"]["layout_status"] != "ok":
        fields.append("layout_fallback_checked")
    if page["ledger"].get("overflow_evidence"):
        fields.append("overflow_canvas_checked")
    if any(asset.get("formula_conditions") for asset in page["assets"]):
        fields.append("formula_conditions_checked")
    failures = [f"{key} is not attested" for key in fields if decision.get(key) is not True]
    if decision.get("override"):
        failures.append("override present: the page is re-prepared and needs a fresh packet")
    if decision.get("issues") != []:
        failures.append("issues are not empty")
    for finding in page_review_findings(page, decision):
        ids = finding.get("asset_ids") or [finding.get("asset_id")]
        failures.append(finding["code"] + ": " + ", ".join(str(aid) for aid in ids))
    return failures


def _source_decision_passes(page: dict[str, Any], decision: dict[str, Any]) -> bool:
    return not _source_decision_failures(page, decision)


def _verify_source_receipt(root: Path, current: dict[str, Any], receipt: Any, source_sha: str) -> None:
    if not isinstance(receipt, dict):
        raise ValueError("invalid source review receipt")
    payload = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    if receipt.get("receipt_sha256") != _hash(payload):
        raise ValueError("source review receipt digest missing or changed; import a fresh visual review")
    packet = _load_source_packet(root, receipt["packet_id"], receipt["packet_sha256"])
    from littrans.structure_profile import guidance_difference, structure_context
    difference = guidance_difference(packet.get("document_structure"), structure_context(root), current["page"])
    if difference:
        raise ValueError("source structure guidance changed since review " + difference)
    if receipt.get("visual_report_sha256") != packet["visual_report"]["sha256"]:
        raise ValueError("source receipt visual report mismatch")
    reviewer = receipt.get("reviewer")
    decision = receipt.get("decision")
    if (not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(decision, dict)
            or receipt.get("source_sha256") != source_sha or packet.get("source_sha256") != source_sha):
        raise ValueError("invalid source review provenance")
    pages = [page for page in packet["pages"] if page["page"] == current["page"]]
    if (len(pages) != 1 or pages[0]["fingerprint"] != current["fingerprint"]
            or receipt.get("fingerprint") != current["fingerprint"]
            or decision.get("page") != current["page"] or decision.get("fingerprint") != current["fingerprint"]
            or receipt.get("passed") is not True or not _source_decision_passes(pages[0], decision)):
        raise ValueError("page requires current visual coverage and boundary review")


OVERRIDE_BLOCKS = ("regions", "units", "page_canvas_bbox")
# The optional unit fields the pipeline's structure assembly always passes to _make_unit,
# with the model defaults an override item may leave out.
OVERRIDE_UNIT_DEFAULTS: dict[str, Any] = {
    "equation_number": None, "footnote_number": None, "footnote_refs": [], "parent_id": None,
    "continues_from_previous": False, "continued_to_next": False, "render_policy": "include", "translatable": True,
}


def _complete_override(root: Path, page: int, override: dict[str, Any]) -> dict[str, Any]:
    """An override replaces the page's recorded override as a whole; say so when it would not.

    A decision that corrects one formula with ``regions`` while the ledger holds a
    ``units`` block from an earlier review would silently retire that block (the units
    would be re-derived from the regions). Such a block must be carried forward or dropped
    on purpose with ``"units": null``; the ledger then records the override without it.
    """
    if not isinstance(override, dict):
        raise ValueError(f"page {page}: override must be an object")
    recorded: dict[str, Any] = {}
    path = _page_path(root, page)
    if path.is_file():
        recorded = read_json(path).get("source_overrides") or {}
    for block in OVERRIDE_BLOCKS:
        if recorded.get(block) is not None and block not in override:
            count = len(recorded[block]) if isinstance(recorded[block], list) else 1
            raise ValueError(f"page {page}: the recorded override carries {block} ({count} entries) that this override omits; "
                             f"carry it forward or set \"{block}\": null to drop it")
    complete = {key: value for key, value in override.items() if value is not None}
    if not complete:
        raise ValueError(f"page {page}: the override drops every block; to re-derive the page from the current rules run "
                         f"`source prepare --pages {page} --replace --discard-overrides` and review a new packet")
    return complete


def import_source_review(root: Path, input_file: Path, confirm_visual_review: bool = False) -> dict[str, Any]:
    from contextlib import ExitStack

    root = Path(root).resolve()
    if not confirm_visual_review:
        raise ValueError("source review import requires explicit confirmation of actual visual review")
    review = read_json(Path(input_file))
    packet_id = review["packet_id"]
    packet = _load_source_packet(root, packet_id, review["packet_sha256"])
    if review.get("visual_report_sha256") != packet["visual_report"]["sha256"]:
        raise ValueError("source review must identify the visual report inspected")
    if not review.get("reviewer", "").strip():
        raise ValueError("source review requires reviewer identity")
    from littrans.structure_profile import guidance_difference, structure_context
    guidance = structure_context(root)
    config = load_project(root)
    if sha256_file(config.source(root)) != packet["source_sha256"]:
        raise ValueError("source PDF changed since packet creation")
    by_page = {p["page"]: p for p in packet["pages"]}
    decisions = review["pages"]
    if len({d["page"] for d in decisions}) != len(decisions):
        raise ValueError("duplicate page review")
    for decision in decisions:
        p = decision["page"]
        difference = guidance_difference(packet.get("document_structure"), guidance, p)
        if difference:
            raise ValueError(f"source structure guidance changed since packet creation {difference}; create a new packet")
        if p not in by_page or decision["fingerprint"] != by_page[p]["fingerprint"] or _current_page(root, p)["fingerprint"] != decision["fingerprint"]:
            raise ValueError(f"stale or out-of-packet page review: {p}")
        try:
            _accepted_grouping_items(decision)
        except ValueError as exc:
            raise ValueError(f"page {p}: {exc}") from None
        if decision.get("override"):
            decision["override"] = _complete_override(root, p, decision["override"])
    changed, approved, deferred = [], [], []
    rejected: dict[int, list[str]] = {}
    with ExitStack() as stack:
        stack.enter_context(project_write_lock(root))
        for decision in decisions:
            if _current_page(root, decision["page"])["fingerprint"] != decision["fingerprint"]:
                raise ValueError("source page changed while waiting for project write lock")
        units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
        assets = load_assets(root)
        decision_pages = {d["page"] for d in decisions}
        # Pages outside this review whose receipt depends on a corrected page.
        dependents = _dependent_pages(root, [d["page"] for d in decisions if d.get("override")], units)
        dependents = [p for p in dependents if p not in decision_pages]
        stack.enter_context(_authority_transaction(root, [*decision_pages, *dependents]))
        before = {p: _page_fingerprint(root, p, units, assets) for p in dependents if _receipt_path(root, p).is_file()}
        claimed: set[str] = set()
        claimed_units: set[str] = set()
        unit_owners = {unit.unit_id: unit.page for unit in units}
        for decision in decisions:
            for item in decision.get("override", {}).get("units", []):
                uid = item["unit_id"]
                if not isinstance(uid, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", uid):
                    raise ValueError("invalid source unit ID; use letters, digits, dot, underscore or hyphen")
                if uid in claimed_units or (uid in unit_owners and unit_owners[uid] != decision["page"]):
                    raise ValueError("source override unit ID collision: " + uid)
                claimed_units.add(uid)
            for region in decision.get("override", {}).get("regions", []):
                aid = region.get("preserve_asset_id") or region.get("id")
                if aid and (aid in claimed or (aid in assets and any(f.page != decision["page"] for f in assets[aid].fragments))):
                    raise ValueError("source override asset ID collision: " + aid)
                if aid:
                    claimed.add(aid)
        for decision in decisions:
            p = decision["page"]
            if decision.get("override"):
                layout = _cached_layout(root, by_page[p]["ledger"])
                if layout["status"] != by_page[p]["ledger"]["layout_status"]:
                    # The reviewer corrected a page cut with the recorded layout evidence;
                    # cutting it again by the fallback rules would change what they reviewed.
                    raise ValueError(f"page {p}: {layout['reason']}; restore the cache, or re-detect with "
                                     f"`source prepare --pages {p} --replace` (the override is replayed on the fresh "
                                     "detection and reported in redetected_override_pages) and review a new packet")
                with fitz.open(config.source(root)) as doc:
                    try:
                        new_units, new_assets, ledger = _page_prepare(root, doc, p, config.source_sha256, layout, decision["override"],
                                                                      {"packet_id": packet_id, "reviewer": review["reviewer"]})
                    except ValueError as exc:
                        raise ValueError(f"page {p}: {exc}") from exc
                new_unit_ids = [unit.unit_id for unit in new_units]
                retained_unit_ids = {unit.unit_id for unit in units if unit.page != p}
                if len(new_unit_ids) != len(set(new_unit_ids)) or retained_unit_ids.intersection(new_unit_ids):
                    raise ValueError("source override unit ID collision")
                old_units = units
                units = [u for u in units if u.page != p] + new_units
                _invalidate(root, old_units, units)
                assets = {aid: a for aid, a in assets.items() if not any(f.page == p for f in a.fragments)}
                new_ids = [a.id for a in new_assets]
                if len(new_ids) != len(set(new_ids)) or set(new_ids).intersection(assets):
                    raise ValueError("source override asset ID collision")
                assets.update({a.id: a for a in new_assets})
                write_json(_page_path(root, p), ledger)
                (root / f"evidence/pages/fidelity-p{p:04d}.review.json").unlink(missing_ok=True)
                changed.append(p)
        _validate_footnote_relationships(units)
        # Publish the corrected authority before receipts are checked against it.
        write_jsonl(root / "derived/units.jsonl", units)
        write_jsonl(root / "derived/fidelity-assets.jsonl", assets.values())
        for decision in decisions:
            p = decision["page"]
            if decision.get("override"):
                continue
            if _current_page(root, p, units, assets)["fingerprint"] != decision["fingerprint"]:
                deferred.append(p)
                (root / f"evidence/pages/fidelity-p{p:04d}.review.json").unlink(missing_ok=True)
                continue
            failures = _source_decision_failures(by_page[p], decision)
            opaque = _opaque_prose_assets(by_page[p])
            if opaque:
                decision = {**decision, "extraction_issues": [{"code": "recoverable-prose-in-image", "assets": opaque}]}
            passed = not failures
            if passed:
                approved.append(p)
            else:
                rejected[p] = failures
            receipt = {"fingerprint": decision["fingerprint"], "source_sha256": config.source_sha256,
                       "passed": passed, "reviewer": review["reviewer"], "packet_id": packet_id,
                       "packet_sha256": review["packet_sha256"], "visual_report_sha256": review["visual_report_sha256"], "decision": decision,
                       **({"failures": failures} if failures else {})}
            write_json(root / f"evidence/pages/fidelity-p{p:04d}.review.json",
                       {**receipt, "receipt_sha256": _hash(receipt)})
        # A neighbour whose fingerprint moved with a corrected page loses its receipt
        # here, by name, rather than at the next verify.
        _, invalidated = _settle_receipts(root, dependents, before, units, assets)
        for unit in units:
            if unit.page in decision_pages:
                unit.verification_status = SemanticStatus.VERIFIED if unit.page in approved else SemanticStatus.UNVERIFIED
        units.sort(key=lambda u: u.page)
        write_jsonl(root / "derived/units.jsonl", units)
    pruned = prune_asset_directories(root, assets.values(), apply=True) if changed else {"removed": []}
    return {"approved_pages": approved, "rejected_pages": rejected, "changed_pages": changed, "deferred_pages": sorted(deferred),
            "invalidated_pages": invalidated, "requires_new_packet": bool(changed or deferred or invalidated),
            "pruned_asset_directories": pruned["removed"]}


def _formula_condition_glyphs(asset: dict[str, Any], glyphs: list[dict[str, Any]]) -> set[str]:
    """Validate explicit language ownership without recognizing/transcribing math."""
    conditions = asset.get("formula_conditions", [])
    if not conditions:
        return set()
    if asset["kind"] != "math":
        raise ValueError("formula_conditions require a math asset")
    owned = {gid for fragment in asset["fragments"] for gid in fragment["glyph_ids"]}
    declared: set[str] = set()
    for condition in conditions:
        ids = condition["glyph_ids"]
        selected = [g for g in glyphs if g["id"] in ids]
        if (not ids or len(ids) != len(set(ids)) or declared.intersection(ids)
                or not set(ids) <= owned or [g["id"] for g in selected] != ids):
            raise ValueError("formula condition glyph IDs must be unique, owned and in native order")
        if re.sub(r"\s+", "", "".join(g["text"] for g in selected)) != re.sub(r"\s+", "", condition["source_text"]):
            raise ValueError("formula condition source_text must exactly match its native glyphs")
        if not LANGUAGE_TOKEN.search(condition["source_text"]):
            raise ValueError("formula condition must identify native language")
        if max(g["baseline"] for g in selected) - min(g["baseline"] for g in selected) > max(g["size"] for g in selected) * .8:
            raise ValueError("each formula condition must stay on one visual line")
        declared.update(ids)
    return declared


def _opaque_prose_assets(current: dict[str, Any]) -> list[str]:
    """Image ownership is not textual coverage of recoverable paragraphs."""
    glyphs = current["ledger"]["glyphs"]
    separate_roman = sum(g["font"].upper().startswith("SF") for g in glyphs) > len(glyphs) * .2
    opaque = []
    for asset in current["assets"]:
        if asset["kind"] not in {"math", "mixed-region"}:
            continue
        ids = {gid for f in asset["fragments"] for gid in f["glyph_ids"]} - _formula_condition_glyphs(asset, glyphs)
        prose = "".join(g["text"] if g["id"] in ids and not MATH_FONT.search(g["font"]) and not (separate_roman and re.match(r"CM(?:R|BX)\d", g["font"], re.I)) else " " for g in glyphs)
        if len(language_words(prose)) >= 6:
            opaque.append(asset["id"])
    return opaque


def verify_fidelity(root: Path, page_spec: str = "all") -> dict[str, Any]:
    root = Path(root).resolve()
    config = load_project(root)
    pages = parse_page_spec(page_spec, config.source_pages)
    requested_pages = list(pages)
    from littrans.evidence import page_evidence_units

    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    required_pages = set(pages)
    for page in requested_pages:
        required_pages.update(unit.page for unit in page_evidence_units(page, units))
    pages = sorted(required_pages)
    errors: list[dict[str, Any]] = []
    verified = []
    # Packets the verified receipts depend on: live directories under packets/.
    receipt_packets: dict[str, list[int]] = {}
    source_current = sha256_file(config.source(root)) == config.source_sha256
    loaded: tuple[list[SourceUnit], dict[str, FidelityAsset]] | None = None
    for p in pages:
        try:
            if not source_current:
                raise ValueError("source PDF changed")
            if loaded is None:
                loaded = (_current_units(root), load_assets(root))
            current = _current_page(root, p, *loaded)
            opaque = _opaque_prose_assets(current)
            if opaque:
                raise ValueError("recoverable prose remains inside image assets; re-prepare or split source regions: " + ", ".join(opaque))
            receipt_path = root / f"evidence/pages/fidelity-p{p:04d}.review.json"
            receipt = read_json(receipt_path) if receipt_path.is_file() else {}
            _verify_source_receipt(root, current, receipt, config.source_sha256)
            refs = Counter(aid for u in current["units"] for aid in asset_reference_ids(u["source_markdown"] or u["source_text"]))
            if refs != Counter({a["id"]: 1 for a in current["assets"]}):
                raise ValueError("asset references are missing or duplicated")
            verified.append(p)
            receipt_packets.setdefault(str(receipt["packet_id"]), []).append(p)
        except (OSError, ValueError, KeyError) as exc:
            errors.append({"page": p, "code": "fidelity-source-unverified", "message": str(exc)})
    return {"passed": not errors, "errors": errors, "verified_pages": verified, "requested_pages": requested_pages, "dependency_pages": sorted(required_pages - set(requested_pages)),
            "receipt_packets": receipt_packets, "visual_report": None}
