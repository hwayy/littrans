from __future__ import annotations

import hashlib
import json
import re
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import fitz
import yaml
from rapidfuzz.fuzz import ratio

from littrans.models import (
    AssetRef,
    CalloutKind,
    ExtractionIssue,
    FigureLabel,
    IssueStatus,
    MathCandidate,
    MathReviewDecision,
    MathStructuralAction,
    MathStructuralOverrideDecision,
    ProjectStatus,
    RenderPolicy,
    SemanticStatus,
    Severity,
    SidebarRole,
    SourceFragment,
    SourceUnit,
    TableData,
    TranslationRecord,
    UnitKind,
    canonical_math_review_representation_sha256,
    canonical_math_review_unit_guard_sha256,
)
from littrans.semantics import (
    code_from_block,
    detect_code_language,
    inline_math_markdown,
    looks_like_continuation,
    looks_like_program_code,
    normalize_prose,
    prose_from_block,
    split_mixed_pdf_block,
    table_from_rows,
    unicode_math_to_latex,
)
from littrans.storage import (
    atomic_write_text,
    load_project,
    project_write_lock,
    read_jsonl,
    restore_files,
    save_project,
    sha256_file,
    sha256_text,
    snapshot_files,
    write_json,
    write_jsonl,
)

CAPTION_RE = re.compile(r"^(figure|fig\.|table)\s*\d+(?:[-–.]\d+)?[.:]?", re.I)
PROSE_FIGURE_TABLE_RE = re.compile(
    r"^(?:figure|fig\.|table)\s*\d+(?:[-–.]\d+)?\s+"
    r"(?:and|compares|demonstrates|illustrates|includes|lists|provides|sheds|shows|summarizes)\b",
    re.I,
)
LIST_RE = re.compile(r"^(?:[•▪■●○◦–—-]|\(?\d+[.)]|[a-z][.)])\s+", re.I)
CODE_RE = re.compile(
    r"(?:^\s*using\s+[\w.]+\s*;\s*$|"
    r"^\s*(?:(?:public|private|protected|internal)\s+)?(?:class|interface|enum|namespace)\s+\w+|"
    r"^\s*(?:public|private|protected|internal)\s+[^.!?\n]+[;{]\s*$|"
    r"^\s*(?:while|for|foreach|if)\s*\(|"
    r"^\s*(?:xmlns[:A-Za-z0-9]*|x:(?:Class|TypeArguments))\s*=)",
    re.MULTILINE,
)
EQUATION_RE = re.compile(r"[=∑∫√∂∇±≤≥∞ρτλσνε]|\b(?:Re|PDF)\s*[=<>]")
PROTECTED_PATTERNS = (
    re.compile(r"https?://[^\s)>]+"),
    re.compile(r"\b(?:[A-Za-z_][\w]*\.)+[A-Za-z_][\w]*\b"),
    re.compile(r"\b[A-Z][a-z]+(?:[A-Z][A-Za-z0-9]*)+\b"),
    re.compile(r"\b[A-Z]{2,8}(?:[A-Z0-9_.-]*[A-Z0-9])?\b"),
    re.compile(r"\b\d+(?:\.\d+)?\s?(?:%|ms|s|kg|m|cm|mm|Hz|kHz|MHz|Pa|K|bar)\b"),
    re.compile(r"\[(?:\d+(?:\s*[-,]\s*\d+)*)\]"),
)
PROTECTED_STOPWORDS = {"EG", "IE", "E.G", "I.E", "THE", "AND", "USING", "MORE"}


def _fonts_are_monospace(fonts: set[str]) -> bool:
    lowered = " ".join(fonts).casefold()
    return bool(
        "courier" in lowered
        or "consol" in lowered
        or "typewriter" in lowered
        or "lucida console" in lowered
        or re.search(r"mono(?:space(?:d)?|[-_ ]|$)", lowered)
    )


@dataclass
class BlockCandidate:
    bbox: tuple[float, float, float, float]
    text: str
    kind: UnitKind
    confidence: float
    translatable: bool
    font_size: float
    font_names: set[str]
    asset_path: str | None = None
    source_markdown: str | None = None
    latex: str | None = None
    equation_number: str | None = None
    code_language: str | None = None
    table: TableData | None = None
    math_status: SemanticStatus | None = None
    visual_text_status: SemanticStatus | None = None
    figure_labels: list[FigureLabel] | None = None
    callout_kind: CalloutKind | None = None


def _callout_kind(text: str) -> CalloutKind | None:
    match = re.match(
        r"^(?:[■▪●]\s*)?(note|tip|warning|caution|what[’']s new)\b",
        " ".join(text.splitlines()),
        re.I,
    )
    if not match:
        return None
    value = match.group(1).casefold().replace("’", "'")
    return {
        "note": CalloutKind.NOTE,
        "tip": CalloutKind.TIP,
        "warning": CalloutKind.WARNING,
        "caution": CalloutKind.CAUTION,
        "what's new": CalloutKind.WHATS_NEW,
    }[value]


def _bbox(values: Any) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = values
    return float(x0), float(y0), float(x1), float(y1)


def parse_page_spec(spec: str | None, total_pages: int) -> list[int]:
    if not spec or spec.strip().lower() == "all":
        return list(range(1, total_pages + 1))
    pages: set[int] = set()
    for part in spec.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError(f"Invalid descending page range: {token}")
            pages.update(range(start, end + 1))
        else:
            pages.add(int(token))
    if not pages or min(pages) < 1 or max(pages) > total_pages:
        raise ValueError(f"Pages must fall between 1 and {total_pages}")
    return sorted(pages)


def normalize_text(text: str) -> str:
    return normalize_prose(text)


def _is_caption(text: str) -> bool:
    return bool(CAPTION_RE.match(text) and not PROSE_FIGURE_TABLE_RE.match(text))


ACRONYM_PATTERN = PROTECTED_PATTERNS[3]


def _is_all_caps_text(text: str, minimum_words: int = 2) -> bool:
    """Small-caps or uppercase display headings style every word, not acronyms."""
    words = re.findall(r"[A-Za-z][A-Za-z'’]*", re.sub(r"\{\{asset:[^}]+\}\}", " ", text))
    return len(words) >= minimum_words and all(word.upper() == word for word in words)


def protected_tokens(text: str, *, heading: bool = False) -> list[str]:
    found: list[str] = []
    all_caps = _is_all_caps_text(text, minimum_words=1 if heading else 2)
    for pattern in PROTECTED_PATTERNS:
        if all_caps and pattern is ACRONYM_PATTERN:
            continue
        found.extend(match.group(0) for match in pattern.finditer(text))
    found = [
        token.rstrip(".,;:!?") if token.lower().startswith(("http://", "https://")) else token
        for token in found
    ]
    return [
        token
        for token in dict.fromkeys(found)
        if token
        if token.upper().rstrip(".") not in PROTECTED_STOPWORDS
    ]


def _block_text(block: dict[str, Any]) -> tuple[str, float, set[str]]:
    sizes: list[float] = []
    fonts: set[str] = set()
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            if span.get("text", "").strip():
                sizes.append(float(span.get("size", 0)))
                fonts.add(str(span.get("font", "")))
    return prose_from_block(block), statistics.median(sizes) if sizes else 0.0, fonts


def _rect_overlap(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    area = max((a[2] - a[0]) * (a[3] - a[1]), 1.0)
    return intersection / area


def _crop_asset(page: fitz.Page, bbox: tuple[float, float, float, float], path: Path) -> None:
    rect = fitz.Rect(bbox) & page.rect
    if rect.is_empty:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False)
    pixmap.save(path)


def _outline_by_page(document: fitz.Document) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for _level, title, page, *_rest in document.get_toc(simple=False):
        if page > 0:
            result.setdefault(int(page), []).append(normalize_text(str(title)))
    return result


def _repeated_marginal_text(document: fitz.Document, pages: list[int]) -> set[str]:
    candidates: Counter[str] = Counter()
    for page_number in pages:
        page = document[page_number - 1]
        height = page.rect.height
        for block in page.get_text("blocks", sort=True):
            x0, y0, x1, y1, text, *_rest = block
            del x0, x1
            cleaned = normalize_text(str(text))
            if cleaned and (y1 < height * 0.11 or y0 > height * 0.89):
                normalized = re.sub(r"\d+", "#", cleaned.casefold())
                if len(normalized) < 220:
                    candidates[normalized] += 1
    threshold = max(2, (len(pages) + 1) // 2)
    return {text for text, count in candidates.items() if count >= threshold}


def _is_marginal(
    bbox: tuple[float, float, float, float], text: str, page: fitz.Page, repeated: set[str]
) -> bool:
    x0, y0, x1, y1 = bbox
    normalized = re.sub(r"\d+", "#", text.casefold())
    block_height = y1 - y0
    if normalized in repeated and (y1 < page.rect.height * 0.14 or y0 > page.rect.height * 0.86):
        return True
    # Do not discard a unique single line merely because body text begins near
    # a page edge: it may be the continuation of a paragraph across pages.
    if (
        y1 < page.rect.height * 0.08
        and block_height < 20
        and re.fullmatch(r"[■▪●]?\s*[A-Z][A-Z0-9 .&:;–—-]{2,}", text.strip())
    ):
        return True
    if (
        y0 > page.rect.height * 0.92
        and block_height < 28
        and re.fullmatch(r"(?:(?:page\s*)?\d+|[ivxlcdm]+)", text.strip(), re.I)
    ):
        return True
    width, height = x1 - x0, y1 - y0
    return x0 < page.rect.width * 0.09 and height > page.rect.height * 0.35 and width < 45


def _looks_like_equation(
    text: str, bbox: tuple[float, float, float, float], page: fitz.Page
) -> bool:
    words = text.split()
    # Equation numbers are frequently positioned at the right margin in the
    # same PDF block, shifting the block midpoint well away from the formula.
    centered = abs(((bbox[0] + bbox[2]) / 2) - page.rect.width / 2) < page.rect.width * 0.28
    symbols = len(EQUATION_RE.findall(text))
    prose_words = re.findall(r"\b[A-Za-z]{4,}\b", text)
    math_density = symbols / max(len(words), 1)
    formula_syntax = bool(
        re.search(r"[A-Za-zρτλσνεημχ](?:\s*\^\s*[-+]?\d+)?\s*=", text)
        or re.search(r"[A-Za-z](?:\s*\^\s*[-+]?\d+)?(?:\s*[+\-*/]\s*[A-Za-z0-9])", text)
    )
    return (
        centered
        and len(words) <= 35
        and (symbols >= 1 or formula_syntax)
        and len(prose_words) <= 10
        and math_density >= 0.08
        and not text.endswith(".")
    )


def _merge_note_fragments(candidates: list[BlockCandidate]) -> list[BlockCandidate]:
    ordered = sorted(candidates, key=lambda item: (item.bbox[1], item.bbox[0]))
    result: list[BlockCandidate] = []
    index = 0
    while index < len(ordered):
        current = ordered[index]
        if current.kind is not UnitKind.NOTE:
            result.append(current)
            index += 1
            continue
        parts = [current]
        cursor = index + 1
        while cursor < len(ordered):
            following = ordered[cursor]
            gap = following.bbox[1] - parts[-1].bbox[3]
            aligned = abs(following.bbox[0] - current.bbox[0]) <= 18
            if following.kind is not UnitKind.PARAGRAPH or not aligned or not (-1 <= gap <= 12):
                break
            parts.append(following)
            cursor += 1
        if len(parts) == 1:
            result.append(current)
        else:
            result.append(
                BlockCandidate(
                    bbox=(
                        min(part.bbox[0] for part in parts),
                        min(part.bbox[1] for part in parts),
                        max(part.bbox[2] for part in parts),
                        max(part.bbox[3] for part in parts),
                    ),
                    text="\n".join(part.text for part in parts),
                    kind=UnitKind.NOTE,
                    confidence=min(part.confidence for part in parts),
                    translatable=True,
                    font_size=current.font_size,
                    font_names=set().union(*(part.font_names for part in parts)),
                    callout_kind=current.callout_kind,
                )
            )
        index = cursor
    return sorted(result, key=lambda item: (round(item.bbox[1], 1), item.bbox[0]))


def _classify_text(
    text: str,
    bbox: tuple[float, float, float, float],
    page: fitz.Page,
    font_size: float,
    median_size: float,
    fonts: set[str],
    outline_titles: list[str],
) -> tuple[UnitKind, float, bool]:
    one_line = " ".join(text.splitlines())
    if _is_caption(one_line):
        return UnitKind.CAPTION, 0.97, True
    if re.match(
        r"^(?:[■▪●]\s*)?(tip|note|warning|caution|what[’']s new)\b",
        one_line,
        re.I,
    ):
        return UnitKind.NOTE, 0.95, True
    if (
        _fonts_are_monospace(fonts)
        or text.lstrip().startswith("<")
        or CODE_RE.search(text)
        or looks_like_program_code(text)
    ):
        return UnitKind.CODE, 0.96, False
    if bbox[1] > page.rect.height * 0.80 and font_size < median_size * 0.83:
        return UnitKind.FOOTNOTE, 0.78, True
    if _looks_like_equation(text, bbox, page):
        return UnitKind.EQUATION, 0.88, False
    if LIST_RE.match(one_line):
        return UnitKind.LIST_ITEM, 0.92, True
    outline_match = max(
        (ratio(one_line.casefold(), title.casefold()) for title in outline_titles), default=0
    )
    if outline_match >= 70 or (
        len(one_line) < 130 and font_size >= max(median_size * 1.28, median_size + 1.5)
    ):
        return UnitKind.HEADING, min(0.99, 0.78 + outline_match / 500), True
    if re.match(r"^(references|literature cited|bibliography)$", one_line, re.I):
        return UnitKind.HEADING, 0.98, True
    return UnitKind.PARAGRAPH, 0.90, True


def _looks_like_figure_labels(candidate: BlockCandidate) -> bool:
    text = candidate.text
    lines = text.splitlines()
    words = text.split()
    if candidate.kind not in {UnitKind.PARAGRAPH, UnitKind.LIST_ITEM, UnitKind.HEADING}:
        return False
    if len(words) > 55 or re.search(r"[.!?]\s+[A-Z]", " ".join(lines)):
        return False
    numeric = sum(char.isdigit() for char in text)
    return len(lines) >= 2 and (numeric >= 2 or (bool(words) and len(words) / len(lines) < 4.5))


def _merge_vector_figures(
    candidates: list[BlockCandidate], page: fitz.Page, assets: Path, page_number: int
) -> list[BlockCandidate]:
    result = list(candidates)
    captions = [
        candidate
        for candidate in result
        if candidate.kind is UnitKind.CAPTION
        and re.match(r"^(?:figure|fig\.)\b", candidate.text, re.I)
    ]
    for caption_index, caption in enumerate(captions, 1):
        image_candidates = [
            candidate
            for candidate in result
            if candidate.kind is UnitKind.FIGURE
            and candidate.bbox[3] <= caption.bbox[1] + 3
            and caption.bbox[1] - candidate.bbox[1] < page.rect.height * 0.7
            and not any(
                other is not caption
                and other.kind is UnitKind.CAPTION
                and candidate.bbox[3] <= other.bbox[1] < caption.bbox[1]
                for other in result
            )
        ]
        if image_candidates:
            top = min(candidate.bbox[1] for candidate in image_candidates)
            figure_blocks = [
                candidate
                for candidate in result
                if candidate is not caption
                and candidate.bbox[1] >= top - 12
                and candidate.bbox[3] <= caption.bbox[1] + 3
                and candidate.bbox[2] >= page.rect.width * 0.04
                and candidate.bbox[0] <= page.rect.width * 0.96
            ]
            if figure_blocks:
                left = min(candidate.bbox[0] for candidate in figure_blocks)
                right = max(candidate.bbox[2] for candidate in figure_blocks)
                bottom = max(candidate.bbox[3] for candidate in figure_blocks)
                asset_name = f"page-{page_number:04}-figure-{caption_index:02}.png"
                _crop_asset(page, (left, top, right, bottom), assets / asset_name)
                result = [candidate for candidate in result if candidate not in figure_blocks]
                result.append(
                    BlockCandidate(
                        bbox=(left, top, right, bottom),
                        text="",
                        kind=UnitKind.FIGURE,
                        confidence=0.94,
                        translatable=False,
                        font_size=0,
                        font_names=set(),
                        asset_path=f"derived/assets/{asset_name}",
                        visual_text_status=SemanticStatus.UNVERIFIED,
                    )
                )
                continue
        # Charts and diagrams are often entirely vector-based and therefore
        # have no PDF image object.  Detect a substantial cluster of drawing
        # primitives immediately above a figure caption, retain one local crop,
        # and move plot-axis/legend text into explicit translatable labels.
        drawing_rects = [
            drawing["rect"]
            for drawing in page.get_drawings()
            if drawing["rect"].x1 > 0
            and drawing["rect"].x0 < page.rect.width
            and drawing["rect"].y1 > 0
            and drawing["rect"].y0 < caption.bbox[1]
            and caption.bbox[1] - drawing["rect"].y0 < page.rect.height * 0.65
            and (drawing["rect"].width > 0.1 or drawing["rect"].height > 0.1)
        ]
        if len(drawing_rects) >= 5:
            draw_left = min(rect.x0 for rect in drawing_rects)
            draw_top = min(rect.y0 for rect in drawing_rects)
            draw_right = max(rect.x1 for rect in drawing_rects)
            draw_bottom = max(rect.y1 for rect in drawing_rects)
            if (
                draw_right - draw_left >= page.rect.width * 0.30
                and draw_bottom - draw_top >= page.rect.height * 0.07
            ):
                labels = [
                    candidate
                    for candidate in result
                    if candidate is not caption
                    and candidate.kind
                    in {UnitKind.PARAGRAPH, UnitKind.LIST_ITEM, UnitKind.HEADING, UnitKind.EQUATION}
                    and candidate.bbox[1] >= draw_top - 8
                    and candidate.bbox[3] <= caption.bbox[1] + 3
                    and len(candidate.text.split()) <= 55
                ]
                left = max(0.0, min([draw_left, *(item.bbox[0] for item in labels)]) - 4)
                top = max(0.0, min([draw_top, *(item.bbox[1] for item in labels)]) - 4)
                right = min(
                    page.rect.width,
                    max([draw_right, *(item.bbox[2] for item in labels)]) + 4,
                )
                bottom = min(page.rect.height, caption.bbox[1] - 3)
                asset_name = f"page-{page_number:04}-vector-figure-{caption_index:02}.png"
                _crop_asset(page, (left, top, right, bottom), assets / asset_name)
                result = [candidate for candidate in result if candidate not in labels]
                result.append(
                    BlockCandidate(
                        bbox=(left, top, right, bottom),
                        text="\n".join(candidate.text for candidate in labels),
                        kind=UnitKind.FIGURE,
                        confidence=0.86,
                        translatable=False,
                        font_size=0,
                        font_names=set(),
                        asset_path=f"derived/assets/{asset_name}",
                        visual_text_status=SemanticStatus.UNVERIFIED,
                        figure_labels=[FigureLabel(source=candidate.text) for candidate in labels],
                    )
                )
                continue
        nearby = [
            candidate
            for candidate in result
            if candidate.bbox[3] <= caption.bbox[1] + 2
            and caption.bbox[1] - candidate.bbox[1] < page.rect.height * 0.58
            and _looks_like_figure_labels(candidate)
        ]
        if len(nearby) < 2:
            continue
        top = min(candidate.bbox[1] for candidate in nearby)
        left = min(candidate.bbox[0] for candidate in nearby)
        right = max(candidate.bbox[2] for candidate in nearby)
        bottom = max(candidate.bbox[3] for candidate in nearby)
        asset_name = f"page-{page_number:04}-vector-figure-{caption_index:02}.png"
        _crop_asset(page, (left, top, right, bottom), assets / asset_name)
        result = [candidate for candidate in result if candidate not in nearby]
        result.append(
            BlockCandidate(
                bbox=(left, top, right, bottom),
                text="\n".join(candidate.text for candidate in nearby),
                kind=UnitKind.FIGURE,
                confidence=0.74,
                translatable=False,
                font_size=0,
                font_names=set(),
                asset_path=f"derived/assets/{asset_name}",
                visual_text_status=SemanticStatus.UNVERIFIED,
                figure_labels=[FigureLabel(source=candidate.text) for candidate in nearby],
            )
        )
    return sorted(result, key=lambda item: (round(item.bbox[1], 1), round(item.bbox[0], 1)))


def _merge_table_regions(
    candidates: list[BlockCandidate], page: fitz.Page, assets: Path, page_number: int
) -> list[BlockCandidate]:
    result = list(candidates)
    table_captions = [
        candidate
        for candidate in result
        if candidate.kind is UnitKind.CAPTION and candidate.text.casefold().startswith("table")
    ]
    for table_index, caption in enumerate(table_captions, 1):
        # Ruled tables in technical books often share a page with ordinary prose
        # and code below them.  A broad "until the next heading" window silently
        # swallowed that material into the table.  Prefer the table's last
        # horizontal rule as a hard semantic boundary when the PDF exposes one.
        clear_boundaries = [
            candidate.bbox[1]
            for candidate in result
            if candidate.bbox[1] > caption.bbox[3]
            and candidate.kind
            in {
                UnitKind.HEADING,
                UnitKind.CAPTION,
                UnitKind.NOTE,
                UnitKind.CODE,
                UnitKind.FIGURE,
            }
        ]
        clear_boundary = min(clear_boundaries, default=page.rect.height - 35.0)
        horizontal_rules = sorted(
            {
                float(drawing["rect"].y1)
                for drawing in page.get_drawings()
                if drawing["rect"].width >= max(100.0, page.rect.width * 0.25)
                and drawing["rect"].height <= 3.0
                and caption.bbox[3] < drawing["rect"].y1
                and drawing["rect"].y1 < clear_boundary
            }
        )
        if len(horizontal_rules) == 2 and horizontal_rules[1] - horizontal_rules[0] <= 45.0:
            # A top rule followed closely by a header separator, with no later
            # bottom rule, is an open table segment that continues to the end of
            # the physical page (and possibly onto the next page).
            table_bottom = page.rect.height - 35.0
        else:
            table_bottom = max(
                horizontal_rules,
                default=min(clear_boundary, caption.bbox[3] + page.rect.height * 0.48),
            )
        content = [
            candidate
            for candidate in result
            if candidate.bbox[1] >= caption.bbox[3]
            and candidate.bbox[1] < table_bottom + 2.0
            and candidate.kind in {UnitKind.PARAGRAPH, UnitKind.LIST_ITEM, UnitKind.TABLE}
            and not any(
                boundary.kind in {UnitKind.HEADING, UnitKind.CAPTION}
                and caption.bbox[3] < boundary.bbox[1] < candidate.bbox[1]
                for boundary in result
            )
        ]
        if not content:
            continue
        left = min(candidate.bbox[0] for candidate in content)
        top = min(candidate.bbox[1] for candidate in content)
        right = max(candidate.bbox[2] for candidate in content)
        bottom = max(candidate.bbox[3] for candidate in content)
        asset_name = f"page-{page_number:04}-table-region-{table_index:02}.png"
        _crop_asset(page, (left, top, right, bottom), assets / asset_name)
        table = _structured_table_from_region(page, (left, top, right, bottom))
        table_text = (
            "\n".join(" | ".join(row) for row in table.rows)
            if table
            else "\n".join(candidate.text for candidate in content)
        )
        result = [candidate for candidate in result if candidate not in content]
        result.append(
            BlockCandidate(
                bbox=(left, top, right, bottom),
                text=table_text,
                kind=UnitKind.TABLE,
                confidence=0.8,
                translatable=True,
                font_size=0,
                font_names=set(),
                asset_path=f"derived/assets/{asset_name}",
                table=table,
            )
        )
    return sorted(result, key=lambda item: (round(item.bbox[1], 1), round(item.bbox[0], 1)))


def _structured_table_from_region(
    page: fitz.Page, bbox: tuple[float, float, float, float]
) -> TableData | None:
    words = [
        (float(x0), float(y0), float(x1), float(y1), str(text))
        for x0, y0, x1, y1, text, *_rest in page.get_text("words", clip=fitz.Rect(bbox), sort=True)
        if str(text).strip()
    ]
    if len(words) < 4:
        return None
    by_line: list[list[tuple[float, float, float, float, str]]] = []
    for word in words:
        matching = next(
            (
                line
                for line in by_line
                if abs(statistics.median(item[1] for item in line) - word[1]) < 3.2
            ),
            None,
        )
        if matching is None:
            by_line.append([word])
        else:
            matching.append(word)
    by_line.sort(key=lambda line: min(item[1] for item in line))
    first_line = sorted(by_line[0], key=lambda item: item[0])
    gaps = [
        (first_line[index + 1][0] - first_line[index][2], index)
        for index in range(len(first_line) - 1)
    ]
    if not gaps:
        return None
    gap, index = max(gaps)
    if gap < 12:
        return None
    split = (first_line[index][2] + first_line[index + 1][0]) / 2
    left_margin = min(item[0] for item in words)
    row_starts = sorted(
        {
            round(line[0][1], 1)
            for line in (sorted(value, key=lambda item: item[0]) for value in by_line)
            if line and line[0][0] <= left_margin + 8
        }
    )
    if len(row_starts) < 2:
        return None
    rows: list[list[str]] = []
    for row_index, start in enumerate(row_starts):
        end = row_starts[row_index + 1] if row_index + 1 < len(row_starts) else bbox[3] + 1
        row_words = [word for word in words if start - 3 <= word[1] < end - 3]
        left = normalize_text(" ".join(word[4] for word in row_words if word[0] < split))
        right = normalize_text(" ".join(word[4] for word in row_words if word[0] >= split))
        if left or right:
            rows.append([left, right])
    return table_from_rows(rows, header_rows=1)


def _merge_equation_regions(
    candidates: list[BlockCandidate], page: fitz.Page, assets: Path, page_number: int
) -> list[BlockCandidate]:
    result = list(candidates)
    equations = [candidate for candidate in result if candidate.kind is UnitKind.EQUATION]
    groups: list[list[BlockCandidate]] = []
    for candidate in sorted(equations, key=lambda item: (item.bbox[1], item.bbox[0])):
        group = next(
            (
                value
                for value in groups
                if min(item.bbox[3] for item in value) - 5 <= candidate.bbox[3]
                and candidate.bbox[1] <= max(item.bbox[3] for item in value) + 5
            ),
            None,
        )
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    for index, group in enumerate(groups, 1):
        top = min(item.bbox[1] for item in group)
        bottom = max(item.bbox[3] for item in group)
        neighbors = [
            item
            for item in result
            if item not in group
            and item.kind is UnitKind.PARAGRAPH
            and item.bbox[1] <= bottom + 3
            and item.bbox[3] >= top - 3
            and len(item.text.split()) <= 20
            and any(
                marker in " ".join(item.font_names).casefold()
                for marker in ("mtsy", "rmti", "mtex", "cmmi", "cmsy", "math", "symbol")
            )
        ]
        group.extend(neighbors)
        left = min(item.bbox[0] for item in group)
        right = max(item.bbox[2] for item in group)
        top = min(item.bbox[1] for item in group)
        bottom = max(item.bbox[3] for item in group)
        ordered = sorted(group, key=lambda item: (round(item.bbox[1], 1), item.bbox[0]))
        source = " ".join(item.text for item in ordered if item.text).strip()
        number_match = re.search(r"\((\d+[a-z]?)\)\s*$", source, re.I)
        equation_number = number_match.group(1) if number_match else None
        if number_match:
            source = source[: number_match.start()].rstrip(" ,")
        asset_name = f"page-{page_number:04}-equation-{index:02}.png"
        _crop_asset(page, (left, top, right, bottom), assets / asset_name)
        merged = BlockCandidate(
            bbox=(left, top, right, bottom),
            text=source,
            kind=UnitKind.EQUATION,
            confidence=min(0.72, min(item.confidence for item in group)),
            translatable=False,
            font_size=statistics.median(item.font_size for item in group),
            font_names=set().union(*(item.font_names for item in group)),
            asset_path=f"derived/assets/{asset_name}",
            latex=unicode_math_to_latex(source),
            equation_number=equation_number,
            math_status=SemanticStatus.UNVERIFIED,
        )
        result = [item for item in result if item not in group]
        result.append(merged)
    return sorted(result, key=lambda item: (round(item.bbox[1], 1), round(item.bbox[0], 1)))


def _merge_footnote_fragments(candidates: list[BlockCandidate]) -> list[BlockCandidate]:
    result = list(candidates)
    footnotes = [candidate for candidate in result if candidate.kind is UnitKind.FOOTNOTE]
    groups: list[list[BlockCandidate]] = []
    for candidate in sorted(footnotes, key=lambda item: (item.bbox[1], item.bbox[0])):
        group = next(
            (
                value
                for value in groups
                if candidate.bbox[1] <= max(item.bbox[3] for item in value) + 4
                and candidate.bbox[3] >= min(item.bbox[1] for item in value) - 4
            ),
            None,
        )
        if group is None:
            groups.append([candidate])
        else:
            group.append(candidate)
    for group in groups:
        if len(group) < 2:
            continue
        primary = max(group, key=lambda item: len(item.text))
        fragments = sorted(
            (item for item in group if item is not primary), key=lambda item: item.bbox[0]
        )
        source = primary.text.rstrip() + " " + "".join(item.text for item in fragments)
        merged = BlockCandidate(
            bbox=(
                min(item.bbox[0] for item in group),
                min(item.bbox[1] for item in group),
                max(item.bbox[2] for item in group),
                max(item.bbox[3] for item in group),
            ),
            text=normalize_text(source),
            kind=UnitKind.FOOTNOTE,
            confidence=min(item.confidence for item in group),
            translatable=True,
            font_size=statistics.median(item.font_size for item in group),
            font_names=set().union(*(item.font_names for item in group)),
            source_markdown=primary.source_markdown,
            math_status=primary.math_status,
        )
        result = [item for item in result if item not in group]
        result.append(merged)
    return sorted(result, key=lambda item: (round(item.bbox[1], 1), round(item.bbox[0], 1)))


def _merge_caption_fragments(candidates: list[BlockCandidate]) -> list[BlockCandidate]:
    result = list(candidates)
    for caption in list(result):
        if (
            caption.kind is not UnitKind.CAPTION
            or not caption.text.casefold().startswith(("figure", "fig."))
            or len(caption.text.split()) > 5
        ):
            continue
        following = [
            candidate
            for candidate in result
            if candidate.kind is UnitKind.PARAGRAPH
            and 0 <= candidate.bbox[1] - caption.bbox[3] < 14
        ]
        if not following:
            continue
        fragment = min(following, key=lambda candidate: candidate.bbox[1])
        merged = BlockCandidate(
            bbox=(
                min(caption.bbox[0], fragment.bbox[0]),
                caption.bbox[1],
                max(caption.bbox[2], fragment.bbox[2]),
                fragment.bbox[3],
            ),
            text=f"{caption.text}. {fragment.text}",
            kind=UnitKind.CAPTION,
            confidence=min(caption.confidence, fragment.confidence),
            translatable=True,
            font_size=caption.font_size,
            font_names=caption.font_names | fragment.font_names,
        )
        result = [
            candidate
            for candidate in result
            if candidate is not caption and candidate is not fragment
        ]
        result.append(merged)
    return sorted(result, key=lambda item: (round(item.bbox[1], 1), round(item.bbox[0], 1)))


def _merge_code_fragments(candidates: list[BlockCandidate]) -> list[BlockCandidate]:
    ordered = sorted(candidates, key=lambda item: (round(item.bbox[1], 1), item.bbox[0]))
    result: list[BlockCandidate] = []
    for candidate in ordered:
        if (
            result
            and candidate.kind is UnitKind.CODE
            and result[-1].kind is UnitKind.CODE
            and 0 <= candidate.bbox[1] - result[-1].bbox[3] < 16
            and abs(candidate.bbox[0] - result[-1].bbox[0]) < 30
        ):
            previous = result.pop()
            text = previous.text.rstrip() + "\n" + candidate.text.lstrip("\n")
            result.append(
                BlockCandidate(
                    bbox=(
                        min(previous.bbox[0], candidate.bbox[0]),
                        previous.bbox[1],
                        max(previous.bbox[2], candidate.bbox[2]),
                        candidate.bbox[3],
                    ),
                    text=text,
                    kind=UnitKind.CODE,
                    confidence=min(previous.confidence, candidate.confidence),
                    translatable=False,
                    font_size=statistics.median([previous.font_size, candidate.font_size]),
                    font_names=previous.font_names | candidate.font_names,
                    code_language=detect_code_language(text),
                )
            )
        else:
            result.append(candidate)
    return result


def _reading_order(candidates: list[BlockCandidate], page: fitz.Page) -> list[BlockCandidate]:
    midpoint = page.rect.width / 2
    left = [
        item for item in candidates if item.bbox[2] < midpoint + 8 and item.bbox[0] < midpoint - 25
    ]
    right = [
        item for item in candidates if item.bbox[0] > midpoint - 8 and item.bbox[2] > midpoint + 25
    ]
    if len(left) < 2 or len(right) < 2:
        return sorted(candidates, key=lambda item: (round(item.bbox[1], 1), item.bbox[0]))
    column_items = set(id(item) for item in left + right)
    full = sorted(
        (item for item in candidates if id(item) not in column_items), key=lambda item: item.bbox[1]
    )
    ordered: list[BlockCandidate] = []
    boundary = float("-inf")
    for separator in [*full, None]:
        limit = separator.bbox[1] if separator is not None else float("inf")
        segment_left = [item for item in left if boundary <= item.bbox[1] < limit]
        segment_right = [item for item in right if boundary <= item.bbox[1] < limit]
        ordered.extend(sorted(segment_left, key=lambda item: item.bbox[1]))
        ordered.extend(sorted(segment_right, key=lambda item: item.bbox[1]))
        if separator is not None:
            ordered.append(separator)
            boundary = separator.bbox[3]
    missing = [item for item in candidates if item not in ordered]
    ordered.extend(sorted(missing, key=lambda item: (item.bbox[1], item.bbox[0])))
    return ordered


def _load_overrides(project_root: Path) -> list[dict[str, Any]]:
    path = project_root / "overrides" / "layout.yaml"
    if not path.exists():
        return []
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict) or not isinstance(payload.get("overrides", []), list):
        raise ValueError(f"{path} must contain an overrides list")
    return [item for item in payload["overrides"] if isinstance(item, dict)]


def _merge_overrides(overrides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    unit_positions: dict[str, int] = {}
    for override in overrides:
        unit_id = override.get("unit_id")
        if not isinstance(unit_id, str):
            merged.append(override)
            continue
        if unit_id not in unit_positions:
            unit_positions[unit_id] = len(merged)
            merged.append(override)
            continue
        position = unit_positions[unit_id]
        merged[position] = {**merged[position], **override}
    return merged


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MATH_REVIEW_GUARD_FIELDS = {
    "schema_version",
    "decision_id",
    "unit_id",
    "page",
    "source_pdf_sha256",
    "source_hash",
    "unit_guard_sha256",
    "before_representation_sha256",
    "after_representation_sha256",
    "crop_sha256",
    "page_image_sha256",
    "reviewed_against_pdf",
}
_APPLIED_MATH_OVERRIDE_RECEIPTS = Path("evidence/math/applied-overrides.jsonl")
_APPLIED_MATH_OVERRIDE_CHAINS = Path("evidence/math/applied-override-chains.jsonl")
_APPLIED_RECEIPT_FIELDS = {
    "schema_version",
    "override_sha256",
    "decision_id",
    "unit_id",
    "source_pdf_sha256",
    "outcome",
    "unit_record_sha256",
}


def _canonical_json_sha256(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(encoded).hexdigest()


def _guarded_override_sha256(override: dict[str, Any]) -> str:
    return _canonical_json_sha256(override)


def _source_unit_record_sha256(unit: SourceUnit) -> str:
    return _canonical_json_sha256(unit.model_dump(mode="json"))


def _load_applied_math_override_receipts(project_root: Path) -> dict[str, dict[str, Any]]:
    path = project_root / _APPLIED_MATH_OVERRIDE_RECEIPTS
    if not path.is_file():
        return {}
    receipts: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            receipt = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid applied math override receipt at {path}:{line_number}"
            ) from exc
        if not isinstance(receipt, dict) or set(receipt) != _APPLIED_RECEIPT_FIELDS:
            raise ValueError(f"Invalid applied math override receipt at {path}:{line_number}")
        digest_fields = ("override_sha256", "source_pdf_sha256")
        if any(
            not isinstance(receipt.get(field), str) or not _SHA256_RE.fullmatch(str(receipt[field]))
            for field in digest_fields
        ):
            raise ValueError(f"Invalid applied math override receipt at {path}:{line_number}")
        outcome = receipt.get("outcome")
        unit_record_sha256 = receipt.get("unit_record_sha256")
        if (
            outcome not in {"absent", "present"}
            or (outcome == "absent" and unit_record_sha256 is not None)
            or (
                outcome == "present"
                and (
                    not isinstance(unit_record_sha256, str)
                    or not _SHA256_RE.fullmatch(unit_record_sha256)
                )
            )
        ):
            raise ValueError(f"Invalid applied math override receipt at {path}:{line_number}")
        if (
            receipt.get("schema_version") != 1
            or not isinstance(receipt.get("decision_id"), str)
            or not str(receipt["decision_id"]).strip()
            or not isinstance(receipt.get("unit_id"), str)
            or not str(receipt["unit_id"]).strip()
        ):
            raise ValueError(f"Invalid applied math override receipt at {path}:{line_number}")
        override_sha256 = str(receipt["override_sha256"])
        previous = receipts.get(override_sha256)
        if previous is not None and previous != receipt:
            raise ValueError(f"Conflicting applied math override receipt: {override_sha256}")
        receipts[override_sha256] = receipt
    return receipts


def _structural_reviews_by_id(project_root: Path) -> dict[str, MathStructuralOverrideDecision]:
    path = project_root / "evidence" / "math" / "structural-reviews.jsonl"
    records = read_jsonl(path, MathStructuralOverrideDecision)
    by_id: dict[str, MathStructuralOverrideDecision] = {}
    for record in records:
        if record.decision_id in by_id:
            raise ValueError(f"Duplicate existing structural decision_id: {record.decision_id}")
        by_id[record.decision_id] = record
    return by_id


def _structural_packet_units(
    project_root: Path,
    source_pdf_sha256: str,
    decision: MathStructuralOverrideDecision,
    manifest_candidates: list[Path] | None = None,
) -> dict[str, SourceUnit]:
    candidates = manifest_candidates
    if candidates is None:
        candidates = [
            project_root
            / "packets"
            / "math-pilot"
            / decision.packet_id
            / "packet"
            / "manifest.json",
            project_root
            / ".littrans"
            / "work"
            / "math-review-packets"
            / decision.packet_id
            / "packet"
            / "manifest.json",
        ]
        candidates.extend(
            (project_root / "packets").glob(f"**/{decision.packet_id}/packet/manifest.json")
        )
    root_resolved = project_root.resolve()
    manifests: dict[Path, Path] = {}
    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("Structural packet manifest escapes the project root") from exc
        manifests[resolved] = resolved
    if len(manifests) != 1:
        raise ValueError(
            f"Applied structural review {decision.decision_id} requires exactly one bound packet"
        )
    manifest_path = next(iter(manifests))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid structural packet manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid structural packet manifest: {manifest_path}")
    payload_value = manifest.get("payload")
    if isinstance(payload_value, dict):
        payload = payload_value
        recorded_hash = manifest.get("payload_sha256")
    else:
        payload = {
            key: value
            for key, value in manifest.items()
            if key not in {"packet_id", "packet_sha256", "manifest_sha256"}
        }
        recorded_hash = manifest.get("packet_sha256")
    payload_sha256 = _canonical_json_sha256(payload)
    source_binding = payload.get("source_pdf")
    if (
        recorded_hash != payload_sha256
        or decision.packet_payload_sha256 != payload_sha256
        or not isinstance(source_binding, dict)
        or source_binding.get("sha256") != source_pdf_sha256
    ):
        raise ValueError(f"Applied structural review {decision.decision_id} packet is stale")
    raw_units = payload.get("units")
    if not isinstance(raw_units, list):
        raise ValueError(f"Applied structural review {decision.decision_id} packet has no units")
    units: dict[str, SourceUnit] = {}
    for raw_entry in raw_units:
        if not isinstance(raw_entry, dict):
            raise ValueError(f"Applied structural review {decision.decision_id} packet is invalid")
        raw_unit = raw_entry.get("unit", raw_entry.get("source_unit"))
        if not isinstance(raw_unit, dict):
            raise ValueError(f"Applied structural review {decision.decision_id} packet is invalid")
        unit = SourceUnit.model_validate(raw_unit)
        record_hash = raw_entry.get(
            "unit_record_sha256", raw_entry.get("source_unit_record_sha256")
        )
        if (
            raw_entry.get("source_hash", raw_unit.get("source_hash")) != unit.source_hash
            or (record_hash is not None and record_hash != _canonical_json_sha256(raw_unit))
            or unit.unit_id in units
        ):
            raise ValueError(f"Applied structural review {decision.decision_id} packet is invalid")
        units[unit.unit_id] = unit
    return units


def _legacy_structural_states(
    project_root: Path,
    source_pdf_sha256: str,
    override: dict[str, Any],
    decision: MathStructuralOverrideDecision,
) -> tuple[SourceUnit, SourceUnit | None] | None:
    """Reconstruct exact pre/post states for a pre-receipt flat structural entry."""

    unit_id = override.get("unit_id")
    if (
        not isinstance(unit_id, str)
        or decision.source_pdf_sha256 != source_pdf_sha256
        or override.get("reason") != decision.reason.strip()
        or override.get("math_review_decision_id") != decision.decision_id
        or override.get("reviewed_against_pdf") is not True
    ):
        return None
    final_values = decision.final_values.model_dump(mode="json", exclude_unset=True)
    if "set_bbox" in final_values and final_values["set_bbox"] is not None:
        final_values["set_bbox"] = list(final_values["set_bbox"])
    if decision.action is MathStructuralAction.IGNORE:
        expected_selector = decision.unit_id
        expected_fields = {"ignore": True}
    elif decision.action is MathStructuralAction.MERGE and unit_id == decision.unit_id:
        expected_selector = decision.unit_id
        expected_fields = {"ignore": True}
    elif decision.action is MathStructuralAction.MERGE:
        expected_selector = decision.target_unit_ids[0]
        expected_fields = final_values
    else:
        expected_selector = decision.unit_id
        expected_fields = final_values
    if unit_id != expected_selector or any(
        override.get(field) != value for field, value in expected_fields.items()
    ):
        return None
    packet_unit = _structural_packet_units(project_root, source_pdf_sha256, decision).get(unit_id)
    if packet_unit is None:
        return None
    if override.get("source_hash") != packet_unit.source_hash:
        return None
    expected = _apply_override(packet_unit, [override])
    if expected is not None and expected.kind is UnitKind.EQUATION and not expected.asset_refs:
        asset_name = f"page-{expected.page:04}-equation-override-{expected.unit_id}.png"
        expected = expected.model_copy(
            update={
                "asset_refs": [
                    AssetRef(
                        kind=UnitKind.EQUATION,
                        path=f"derived/assets/{asset_name}",
                        bbox=expected.bbox,
                    )
                ]
            }
        )
    validated = (
        SourceUnit.model_validate(expected.model_dump(mode="python"))
        if expected is not None
        else None
    )
    return packet_unit, validated


def _legacy_structural_result_matches(
    project_root: Path,
    source_pdf_sha256: str,
    override: dict[str, Any],
    current_units: list[SourceUnit],
    decision: MathStructuralOverrideDecision,
) -> bool:
    states = _legacy_structural_states(project_root, source_pdf_sha256, override, decision)
    if states is None:
        return False
    _, expected = states
    if expected is None:
        # Absence alone cannot distinguish a legitimately consumed selector
        # from an arbitrary deletion.  A durable receipt or an authenticated
        # merge target is required for that case.
        return False
    return len(current_units) == 1 and current_units[0] == expected


def _legacy_consumed_merge_matches(
    project_root: Path,
    source_pdf_sha256: str,
    source_override: dict[str, Any],
    units_by_id: dict[str, list[SourceUnit]],
    overrides: list[dict[str, Any]],
    authenticated_chains: dict[str, dict[str, Any]],
    decision: MathStructuralOverrideDecision,
) -> bool:
    """Prove an absent pre-receipt merge source from its exact target outcome."""

    if (
        decision.action is not MathStructuralAction.MERGE
        or source_override.get("unit_id") != decision.unit_id
        or units_by_id.get(decision.unit_id)
        or len(decision.target_unit_ids) != 1
    ):
        return False
    target_id = decision.target_unit_ids[0]
    target_chain = authenticated_chains.get(target_id)
    structural_identity = f"structural:{decision.decision_id}"
    if target_chain is not None and structural_identity in target_chain["decision_ids"]:
        return True
    target_entries = [
        entry
        for entry in overrides
        if entry.get("unit_id") == target_id
        and entry.get("math_review_decision_id") == decision.decision_id
    ]
    return len(target_entries) == 1 and _legacy_structural_result_matches(
        project_root,
        source_pdf_sha256,
        target_entries[0],
        units_by_id.get(target_id, []),
        decision,
    )


def _legacy_structural_input_matches(
    project_root: Path,
    source_pdf_sha256: str,
    override: dict[str, Any],
    current_units: list[SourceUnit],
    decision: MathStructuralOverrideDecision,
) -> bool:
    states = _legacy_structural_states(project_root, source_pdf_sha256, override, decision)
    return states is not None and len(current_units) == 1 and current_units[0] == states[0]


def _flat_math_review_result_matches(unit: SourceUnit, override: dict[str, Any]) -> bool:
    if override.get("verified") is not True:
        return False
    if unit.verification_status is not SemanticStatus.VERIFIED or unit.confidence != 1.0:
        return False
    for field in ("latex", "source_markdown", "equation_number"):
        if field in override and getattr(unit, field) != override[field]:
            return False
    effective_markdown = override.get("source_markdown", unit.source_markdown)
    if (unit.kind is UnitKind.EQUATION or effective_markdown) and (
        unit.math_status is not SemanticStatus.VERIFIED
    ):
        return False
    return True


def _math_reviews_by_id(project_root: Path) -> dict[str, MathReviewDecision]:
    path = project_root / "evidence" / "math" / "reviews.jsonl"
    records = read_jsonl(path, MathReviewDecision)
    by_id: dict[str, MathReviewDecision] = {}
    for record in records:
        if record.decision_id in by_id:
            raise ValueError(f"Duplicate existing math decision_id: {record.decision_id}")
        by_id[record.decision_id] = record
    return by_id


def _math_candidates_by_id(project_root: Path) -> dict[str, MathCandidate]:
    path = project_root / "evidence" / "math" / "candidates.jsonl"
    records = read_jsonl(path, MathCandidate)
    by_id: dict[str, MathCandidate] = {}
    for record in records:
        if record.candidate_id in by_id:
            raise ValueError(f"Duplicate math candidate ID: {record.candidate_id}")
        by_id[record.candidate_id] = record
    return by_id


def _review_packet_unit(
    project_root: Path,
    source_pdf_sha256: str,
    decision: MathReviewDecision,
    manifest_candidates: list[Path] | None = None,
) -> SourceUnit:
    if decision.packet_id is None or decision.packet_sha256 is None:
        raise ValueError(
            f"Supersession chain decision {decision.decision_id} has no immutable packet binding"
        )
    candidates = manifest_candidates
    if candidates is None:
        candidates = [
            project_root
            / ".littrans"
            / "work"
            / "math-review-packets"
            / decision.packet_id
            / "packet"
            / "manifest.json",
            project_root
            / "packets"
            / "math-pilot"
            / decision.packet_id
            / "packet"
            / "manifest.json",
        ]
        candidates.extend(
            (project_root / "packets").glob(f"**/{decision.packet_id}/packet/manifest.json")
        )
    root_resolved = project_root.resolve()
    manifests: dict[Path, Path] = {}
    for candidate in candidates:
        if not candidate.is_file():
            continue
        resolved = candidate.resolve()
        try:
            resolved.relative_to(root_resolved)
        except ValueError as exc:
            raise ValueError("Math review packet manifest escapes the project root") from exc
        manifests[resolved] = resolved
    if len(manifests) != 1:
        raise ValueError(
            f"Supersession chain decision {decision.decision_id} requires one immutable packet"
        )
    manifest_path = next(iter(manifests))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid math review packet: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Invalid math review packet: {manifest_path}")
    payload_value = manifest.get("payload")
    if isinstance(payload_value, dict):
        payload = payload_value
        recorded_hash = manifest.get("payload_sha256")
    else:
        payload = {
            key: value
            for key, value in manifest.items()
            if key not in {"packet_id", "packet_sha256", "manifest_sha256"}
        }
        recorded_hash = manifest.get("packet_sha256")
        manifest_core = {
            **payload,
            "packet_id": manifest.get("packet_id"),
            "packet_sha256": recorded_hash,
        }
        if (
            decision.manifest_sha256 is None
            or manifest.get("manifest_sha256") != decision.manifest_sha256
            or decision.manifest_sha256 != _canonical_json_sha256(manifest_core)
        ):
            raise ValueError(f"Math review packet manifest for {decision.decision_id} is stale")
    payload_sha256 = _canonical_json_sha256(payload)
    source_binding = payload.get("source_pdf")
    if (
        recorded_hash != payload_sha256
        or decision.packet_sha256 != payload_sha256
        or not isinstance(source_binding, dict)
        or source_binding.get("sha256") != source_pdf_sha256
    ):
        raise ValueError(f"Math review packet for {decision.decision_id} is stale")
    raw_units = payload.get("units")
    if not isinstance(raw_units, list):
        raise ValueError(f"Math review packet for {decision.decision_id} has no units")
    matches: list[SourceUnit] = []
    for raw_entry in raw_units:
        if not isinstance(raw_entry, dict):
            continue
        raw_unit = raw_entry.get("unit", raw_entry.get("source_unit"))
        if not isinstance(raw_unit, dict) or raw_unit.get("unit_id") != decision.unit_id:
            continue
        unit = SourceUnit.model_validate(raw_unit)
        record_hash = raw_entry.get(
            "unit_record_sha256", raw_entry.get("source_unit_record_sha256")
        )
        if raw_entry.get("source_hash", raw_unit.get("source_hash")) != unit.source_hash or (
            record_hash is not None and record_hash != _canonical_json_sha256(raw_unit)
        ):
            raise ValueError(f"Math review packet unit for {decision.decision_id} is stale")
        matches.append(unit)
    if len(matches) != 1:
        raise ValueError(
            f"Math review packet for {decision.decision_id} does not bind one source unit"
        )
    return matches[0]


def _expected_equation_asset(unit: SourceUnit | None) -> SourceUnit | None:
    if unit is None or unit.kind is not UnitKind.EQUATION or unit.asset_refs:
        return unit
    asset_name = f"page-{unit.page:04}-equation-override-{unit.unit_id}.png"
    return unit.model_copy(
        update={
            "asset_refs": [
                AssetRef(
                    kind=UnitKind.EQUATION,
                    path=f"derived/assets/{asset_name}",
                    bbox=unit.bbox,
                )
            ]
        }
    )


def _math_review_transition(
    project_root: Path,
    source_pdf_sha256: str,
    override: dict[str, Any],
    decision: MathReviewDecision,
    candidates: dict[str, MathCandidate],
    manifest_candidates: list[Path] | None = None,
) -> tuple[SourceUnit, SourceUnit | None, str] | None:
    if (
        override.get("math_review_decision_id") != decision.decision_id
        or override.get("unit_id") != decision.unit_id
        or override.get("source_pdf_sha256") != decision.source_pdf_sha256
        or override.get("source_hash") != decision.source_hash
        or override.get("crop_sha256") != decision.crop_sha256
        or override.get("page_image_sha256") != decision.page_image_sha256
        or override.get("candidate_id") != decision.candidate_id
        or override.get("reason") != decision.reason.strip()
        or override.get("reviewed_against_pdf") is not True
        or decision.source_pdf_sha256 != source_pdf_sha256
        or override.get("verified") is not True
    ):
        return None
    latex = decision.final_latex
    source_markdown = decision.final_source_markdown
    equation_number = decision.equation_number
    if decision.candidate_id is not None:
        candidate = candidates.get(decision.candidate_id)
        if candidate is None or candidate.unit_id != decision.unit_id:
            return None
        latex = latex if latex is not None else candidate.latex
        source_markdown = (
            source_markdown if source_markdown is not None else candidate.source_markdown
        )
        equation_number = (
            equation_number if equation_number is not None else candidate.equation_number
        )
    if (
        ("latex" in override and override.get("latex") != latex)
        or ("source_markdown" in override and override.get("source_markdown") != source_markdown)
        or ("equation_number" in override and override.get("equation_number") != equation_number)
    ):
        return None
    before = _review_packet_unit(
        project_root,
        source_pdf_sha256,
        decision,
        manifest_candidates,
    )
    after = _expected_equation_asset(_apply_override(before, [override]))
    return before, after, f"math:{decision.decision_id}"


_STRUCTURAL_MUTATION_FIELDS = {
    "ignore",
    "kind",
    "source_text",
    "source_markdown",
    "latex",
    "equation_number",
    "set_bbox",
    "parent_id",
    "continues_from_previous",
    "continued_to_next",
}


def _structural_transition(
    project_root: Path,
    source_pdf_sha256: str,
    override: dict[str, Any],
    decision: MathStructuralOverrideDecision,
    manifest_candidates: list[Path] | None = None,
) -> tuple[SourceUnit, SourceUnit | None, str] | None:
    unit_id = override.get("unit_id")
    if (
        not isinstance(unit_id, str)
        or decision.source_pdf_sha256 != source_pdf_sha256
        or override.get("reason") != decision.reason.strip()
    ):
        return None
    final_values = decision.final_values.model_dump(mode="json", exclude_unset=True)
    if "set_bbox" in final_values and final_values["set_bbox"] is not None:
        final_values["set_bbox"] = list(final_values["set_bbox"])
    if decision.action is MathStructuralAction.IGNORE:
        expected_selector = decision.unit_id
        expected_fields = {"ignore": True}
    elif decision.action is MathStructuralAction.MERGE and unit_id == decision.unit_id:
        expected_selector = decision.unit_id
        expected_fields = {"ignore": True}
    elif decision.action is MathStructuralAction.MERGE:
        expected_selector = decision.target_unit_ids[0]
        expected_fields = final_values
    else:
        expected_selector = decision.unit_id
        expected_fields = final_values
    actual_mutations = {
        field: override[field] for field in _STRUCTURAL_MUTATION_FIELDS if field in override
    }
    if unit_id != expected_selector or actual_mutations != expected_fields:
        return None
    if "math_review_decision_id" in override and (
        override.get("math_review_decision_id") != decision.decision_id
        or override.get("source_pdf_sha256") != decision.source_pdf_sha256
        or override.get("reviewed_against_pdf") is not True
    ):
        return None
    before = _structural_packet_units(
        project_root,
        source_pdf_sha256,
        decision,
        manifest_candidates,
    ).get(unit_id)
    if before is None:
        return None
    after = _expected_equation_asset(_apply_override(before, [override]))
    return before, after, f"structural:{decision.decision_id}"


def _load_chain_receipts(project_root: Path) -> dict[str, dict[str, Any]]:
    path = project_root / _APPLIED_MATH_OVERRIDE_CHAINS
    if not path.is_file():
        return {}
    receipts: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            receipt = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Invalid applied override chain receipt at {path}:{line_number}"
            ) from exc
        required = {
            "schema_version",
            "unit_id",
            "source_pdf_sha256",
            "entry_sha256s",
            "decision_ids",
            "outcome",
            "unit_record_sha256",
        }
        if not isinstance(receipt, dict) or set(receipt) != required:
            raise ValueError(f"Invalid applied override chain receipt at {path}:{line_number}")
        unit_id = receipt.get("unit_id")
        entry_hashes = receipt.get("entry_sha256s")
        decision_ids = receipt.get("decision_ids")
        if (
            receipt.get("schema_version") != 1
            or not isinstance(unit_id, str)
            or not unit_id
            or not isinstance(entry_hashes, list)
            or not entry_hashes
            or any(
                not isinstance(value, str) or not _SHA256_RE.fullmatch(value)
                for value in entry_hashes
            )
            or not isinstance(decision_ids, list)
            or len(decision_ids) != len(entry_hashes)
            or len(set(decision_ids)) != len(decision_ids)
            or any(not isinstance(value, str) or not value for value in decision_ids)
            or not isinstance(receipt.get("source_pdf_sha256"), str)
            or not _SHA256_RE.fullmatch(str(receipt["source_pdf_sha256"]))
            or unit_id in receipts
        ):
            raise ValueError(f"Invalid applied override chain receipt at {path}:{line_number}")
        outcome = receipt.get("outcome")
        state_hash = receipt.get("unit_record_sha256")
        if (
            outcome not in {"absent", "present"}
            or (outcome == "absent" and state_hash is not None)
            or (
                outcome == "present"
                and (not isinstance(state_hash, str) or not _SHA256_RE.fullmatch(state_hash))
            )
        ):
            raise ValueError(f"Invalid applied override chain receipt at {path}:{line_number}")
        receipts[unit_id] = receipt
    return receipts


def _state_matches_units(state: SourceUnit | None, units: list[SourceUnit]) -> bool:
    if state is None:
        return not units
    return len(units) == 1 and units[0] == state


def _preflight_authenticated_chains(
    project_root: Path,
    source_pdf_sha256: str,
    units_by_id: dict[str, list[SourceUnit]],
    overrides: list[dict[str, Any]],
) -> tuple[set[int], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    existing_receipts = _load_chain_receipts(project_root)
    entries_by_unit: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for index, override in enumerate(overrides):
        unit_id = override.get("unit_id")
        if isinstance(unit_id, str):
            entries_by_unit.setdefault(unit_id, []).append((index, override))
    math_reviews = _math_reviews_by_id(project_root)
    structural_reviews = _structural_reviews_by_id(project_root)
    chain_unit_ids = {
        unit_id
        for unit_id, entries in entries_by_unit.items()
        if len(entries) > 1
        and any(
            (
                isinstance(entry.get("math_review_decision_id"), str)
                and (
                    entry["math_review_decision_id"] in structural_reviews
                    or (
                        entry["math_review_decision_id"] in math_reviews
                        and math_reviews[entry["math_review_decision_id"]].packet_id is not None
                    )
                )
            )
            for _, entry in entries
        )
    } | set(existing_receipts)
    if not chain_unit_ids:
        return set(), {}, existing_receipts

    candidates = _math_candidates_by_id(project_root)
    packet_manifests: dict[str, list[Path]] = {}
    packet_roots = (
        project_root / "packets",
        project_root / ".littrans" / "work" / "math-review-packets",
    )
    for packet_root in packet_roots:
        for manifest_path in packet_root.glob("**/packet/manifest.json"):
            packet_manifests.setdefault(manifest_path.parent.parent.name, []).append(manifest_path)
    structural_by_reason: dict[str, list[MathStructuralOverrideDecision]] = {}
    for structural in structural_reviews.values():
        structural_by_reason.setdefault(structural.reason.strip(), []).append(structural)
    skipped: set[int] = set()
    authenticated: dict[str, dict[str, Any]] = {}
    for unit_id in sorted(chain_unit_ids):
        entries = entries_by_unit.get(unit_id, [])
        if not entries:
            raise ValueError(f"Authenticated override chain for {unit_id} was removed")
        transitions: list[tuple[SourceUnit, SourceUnit | None, str]] = []
        entry_hashes: list[str] = []
        decision_ids: list[str] = []
        for index, entry in entries:
            transition_matches: list[tuple[SourceUnit, SourceUnit | None, str]] = []
            decision_id = entry.get("math_review_decision_id")
            if isinstance(decision_id, str):
                guarded_structural = structural_reviews.get(decision_id)
                if guarded_structural is not None:
                    transition = _structural_transition(
                        project_root,
                        source_pdf_sha256,
                        entry,
                        guarded_structural,
                        packet_manifests.get(guarded_structural.packet_id, []),
                    )
                    if transition is not None:
                        transition_matches.append(transition)
                math_review = math_reviews.get(decision_id)
                if math_review is not None:
                    transition = _math_review_transition(
                        project_root,
                        source_pdf_sha256,
                        entry,
                        math_review,
                        candidates,
                        packet_manifests.get(math_review.packet_id or "", []),
                    )
                    if transition is not None:
                        transition_matches.append(transition)
            else:
                reason = entry.get("reason")
                reason_matches = (
                    structural_by_reason.get(reason, []) if isinstance(reason, str) else []
                )
                for structural in reason_matches:
                    transition = _structural_transition(
                        project_root,
                        source_pdf_sha256,
                        entry,
                        structural,
                        packet_manifests.get(structural.packet_id, []),
                    )
                    if transition is not None:
                        transition_matches.append(transition)
                if len(transition_matches) > 1 and index > 0:
                    previous_entry = overrides[index - 1]
                    paired: list[tuple[SourceUnit, SourceUnit | None, str]] = []
                    for transition in transition_matches:
                        structural = structural_reviews[transition[2].removeprefix("structural:")]
                        if (
                            _structural_transition(
                                project_root,
                                source_pdf_sha256,
                                previous_entry,
                                structural,
                                packet_manifests.get(structural.packet_id, []),
                            )
                            is not None
                        ):
                            paired.append(transition)
                    transition_matches = paired
            if len(transition_matches) != 1:
                raise ValueError(
                    f"Override chain entry {index + 1} for {unit_id} is unauthenticated or ambiguous"
                )
            transition = transition_matches[0]
            transitions.append(transition)
            entry_hashes.append(_guarded_override_sha256(entry))
            decision_ids.append(transition[2])
            skipped.add(index)
        if len(decision_ids) != len(set(decision_ids)):
            raise ValueError(f"Override chain for {unit_id} contains duplicate decision IDs")
        base_state = transitions[0][0]
        state: SourceUnit | None = base_state
        prefix_states: list[SourceUnit | None] = []
        for (_, entry), (before, _after, _) in zip(entries, transitions, strict=True):
            prior_states = [base_state, *prefix_states]
            if state is None or before not in prior_states:
                raise ValueError(
                    f"Override chain for {unit_id} is reordered or has a stale packet transition"
                )
            state = _expected_equation_asset(_apply_override(state, [entry]))
            prefix_states.append(state)
        current_units = units_by_id.get(unit_id, [])
        receipt = existing_receipts.get(unit_id)
        if receipt is not None:
            old_hashes = receipt["entry_sha256s"]
            old_decisions = receipt["decision_ids"]
            if (
                receipt.get("source_pdf_sha256") != source_pdf_sha256
                or entry_hashes[: len(old_hashes)] != old_hashes
                or decision_ids[: len(old_decisions)] != old_decisions
            ):
                raise ValueError(
                    f"Authenticated override chain for {unit_id} was removed, reordered, or altered"
                )
            receipt_matches = (receipt.get("outcome") == "absent" and not current_units) or (
                receipt.get("outcome") == "present"
                and len(current_units) == 1
                and receipt.get("unit_record_sha256")
                == _source_unit_record_sha256(current_units[0])
            )
            if not receipt_matches:
                raise ValueError(f"Authenticated override chain state for {unit_id} is stale")
        else:
            allowed_states = [transitions[0][0], *prefix_states]
            if not any(
                _state_matches_units(candidate, current_units) for candidate in allowed_states
            ):
                raise ValueError(
                    f"Authenticated override chain terminal state for {unit_id} is stale"
                )
        authenticated[unit_id] = {
            "entry_sha256s": entry_hashes,
            "decision_ids": decision_ids,
            "terminal": state,
        }
    return skipped, authenticated, existing_receipts


def _preflight_math_review_overrides(
    project_root: Path,
    units: list[SourceUnit],
    overrides: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Reject stale guarded review entries before staging any source mutation."""

    guarded_indexes = {
        index
        for index, override in enumerate(overrides)
        if "math_review_decision_id" in override or "math_review_guard" in override
    }
    existing_chain_receipts = _load_chain_receipts(project_root)
    if not guarded_indexes and not existing_chain_receipts:
        return _load_applied_math_override_receipts(project_root), {}, {}

    config = load_project(project_root)
    source = config.source(project_root)
    if not source.is_file():
        raise ValueError(f"Guarded math review source PDF is missing: {source}")
    source_sha256 = sha256_file(source)
    if source_sha256 != config.source_sha256:
        raise ValueError("Guarded math review source PDF changed after project initialization")

    units_by_id: dict[str, list[SourceUnit]] = {}
    for unit in units:
        units_by_id.setdefault(unit.unit_id, []).append(unit)
    receipts = _load_applied_math_override_receipts(project_root)
    chain_indexes, authenticated_chains, existing_chain_receipts = _preflight_authenticated_chains(
        project_root,
        source_sha256,
        units_by_id,
        overrides,
    )
    structural_reviews: dict[str, MathStructuralOverrideDecision] | None = None

    for index, override in enumerate(overrides):
        if index in chain_indexes:
            continue
        if index not in guarded_indexes:
            continue
        decision_id = override.get("math_review_decision_id")
        unit_id = override.get("unit_id")
        if not isinstance(decision_id, str) or not decision_id.strip():
            raise ValueError("Guarded math review override has no decision ID")
        if not isinstance(unit_id, str) or not unit_id.strip():
            raise ValueError(f"Guarded math review {decision_id} has no exact unit ID")
        flat_source_pdf_sha256 = override.get("source_pdf_sha256")
        flat_source_hash = override.get("source_hash")
        if (
            not isinstance(flat_source_pdf_sha256, str)
            or not _SHA256_RE.fullmatch(flat_source_pdf_sha256)
            or flat_source_pdf_sha256 != source_sha256
        ):
            raise ValueError(
                f"Guarded math review {decision_id} source PDF hash is stale or invalid"
            )
        if not isinstance(flat_source_hash, str) or not _SHA256_RE.fullmatch(flat_source_hash):
            raise ValueError(
                f"Guarded math review {decision_id} unit source hash is stale or invalid"
            )
        if override.get("reviewed_against_pdf") is not True:
            raise ValueError(
                f"Guarded math review {decision_id} lacks PDF visual-review attestation"
            )
        if any(
            not isinstance(override.get(field), str)
            or not _SHA256_RE.fullmatch(str(override[field]))
            for field in ("crop_sha256", "page_image_sha256")
        ):
            raise ValueError(
                f"Guarded math review {decision_id} has malformed visual evidence hashes"
            )

        guard = override.get("math_review_guard")
        if guard is not None:
            if not isinstance(guard, dict) or set(guard) != _MATH_REVIEW_GUARD_FIELDS:
                raise ValueError(f"Guarded math review {decision_id} has an invalid selector guard")
            digest_fields = (
                "source_pdf_sha256",
                "source_hash",
                "unit_guard_sha256",
                "before_representation_sha256",
                "after_representation_sha256",
                "crop_sha256",
                "page_image_sha256",
            )
            if any(
                not isinstance(guard.get(field), str) or not _SHA256_RE.fullmatch(str(guard[field]))
                for field in digest_fields
            ):
                raise ValueError(
                    f"Guarded math review {decision_id} contains a malformed SHA-256 binding"
                )
            if (
                guard.get("schema_version") != 1
                or guard.get("decision_id") != decision_id
                or guard.get("unit_id") != unit_id
                or guard.get("source_pdf_sha256") != flat_source_pdf_sha256
                or guard.get("source_hash") != flat_source_hash
                or guard.get("crop_sha256") != override.get("crop_sha256")
                or guard.get("page_image_sha256") != override.get("page_image_sha256")
                or guard.get("reviewed_against_pdf") is not True
            ):
                raise ValueError(f"Guarded math review {decision_id} selector bindings conflict")

        matches = units_by_id.get(unit_id, [])
        override_sha256 = _guarded_override_sha256(override)
        receipt = receipts.get(override_sha256)
        if receipt is not None:
            if (
                receipt.get("decision_id") != decision_id
                or receipt.get("unit_id") != unit_id
                or receipt.get("source_pdf_sha256") != source_sha256
            ):
                raise ValueError(f"Guarded math review {decision_id} applied receipt conflicts")
            if receipt.get("outcome") == "absent":
                if matches:
                    raise ValueError(
                        f"Guarded math review {decision_id} applied absent state is stale"
                    )
            elif len(matches) != 1 or receipt.get(
                "unit_record_sha256"
            ) != _source_unit_record_sha256(matches[0]):
                raise ValueError(
                    f"Guarded math review {decision_id} applied SourceUnit state is stale"
                )
            continue

        if structural_reviews is None:
            structural_reviews = _structural_reviews_by_id(project_root)
        structural = structural_reviews.get(decision_id)
        if structural is not None and (
            _legacy_structural_result_matches(
                project_root,
                source_sha256,
                override,
                matches,
                structural,
            )
            or _legacy_structural_input_matches(
                project_root,
                source_sha256,
                override,
                matches,
                structural,
            )
            or _legacy_consumed_merge_matches(
                project_root,
                source_sha256,
                override,
                units_by_id,
                overrides,
                authenticated_chains,
                structural,
            )
        ):
            continue

        if guard is None:
            if structural is None and len(matches) == 1:
                unit = matches[0]
                source_matches = (
                    flat_source_hash == unit.source_hash
                    and sha256_text(unit.source_text) == unit.source_hash
                )
                already_verified = (
                    unit.verification_status is SemanticStatus.VERIFIED
                    or unit.math_status is SemanticStatus.VERIFIED
                )
                if source_matches and (
                    not already_verified or _flat_math_review_result_matches(unit, override)
                ):
                    continue
        elif len(matches) == 1:
            unit = matches[0]
            source_matches = (
                flat_source_hash == unit.source_hash
                and sha256_text(unit.source_text) == unit.source_hash
            )
            guard_matches = (
                guard.get("page") == unit.page
                and guard.get("unit_guard_sha256") == canonical_math_review_unit_guard_sha256(unit)
                and canonical_math_review_representation_sha256(unit)
                in {
                    guard.get("before_representation_sha256"),
                    guard.get("after_representation_sha256"),
                }
            )
            if source_matches and guard_matches:
                continue

        if len(matches) != 1:
            raise ValueError(
                f"Guarded math review {decision_id} requires exactly one current "
                f"SourceUnit for {unit_id}; found {len(matches)}"
            )
        if (
            flat_source_hash != matches[0].source_hash
            or sha256_text(matches[0].source_text) != matches[0].source_hash
        ):
            raise ValueError(
                f"Guarded math review {decision_id} unit source hash is stale or invalid"
            )
        raise ValueError(f"Guarded math review {decision_id} current SourceUnit state is stale")
    return receipts, authenticated_chains, existing_chain_receipts


def _code_text_from_page(page: fitz.Page, bbox: tuple[float, float, float, float]) -> str:
    """Rebuild listing whitespace from the original PDF block for a code override."""
    padded = (
        bbox[0] - 1,
        bbox[1] - 1,
        bbox[2] + 1,
        bbox[3] + 1,
    )
    raw = page.get_text("dict", clip=fitz.Rect(padded), sort=True)
    chunks: list[str] = []
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        text = code_from_block(block)
        if text.strip():
            chunks.append(text)
    return "\n".join(chunks).rstrip()


def _apply_override(
    unit: SourceUnit,
    overrides: list[dict[str, Any]],
) -> SourceUnit | None:
    revised = unit
    for override in overrides:
        if "page" in override and int(override["page"]) != revised.page:
            continue
        override_unit_id = override.get("unit_id")
        id_matches = override_unit_id == revised.unit_id
        bbox_value = override.get("bbox")
        bbox_matches = False
        if isinstance(bbox_value, list) and len(bbox_value) == 4:
            bbox_matches = _rect_overlap(revised.bbox, _bbox(bbox_value)) > 0.5
        kind_matches = (
            "current_kind" in override and str(override["current_kind"]) == revised.kind.value
        )
        text_matches = "text_regex" in override and bool(
            re.search(str(override["text_regex"]), revised.source_text, re.MULTILINE)
        )
        rule_matches = kind_matches and ("text_regex" not in override or text_matches)
        if isinstance(override_unit_id, str):
            if not id_matches:
                continue
        elif not bbox_matches and not rule_matches:
            continue
        if override.get("ignore") is True:
            if not str(override.get("reason", "")).strip():
                raise ValueError("An ignore override requires a reason")
            return None
        updates: dict[str, Any] = {}
        if "kind" in override:
            updates["kind"] = UnitKind(str(override["kind"]))
        if "translatable" in override:
            updates["translatable"] = bool(override["translatable"])
        if "render_policy" in override:
            updates["render_policy"] = RenderPolicy(str(override["render_policy"]))
            if updates["render_policy"] is RenderPolicy.OMIT:
                updates["translatable"] = False
        if "source_text" in override:
            if not str(override.get("reason", "")).strip():
                raise ValueError("A source_text override requires a reason")
            corrected = (
                str(override["source_text"]).replace("\r\n", "\n").rstrip()
                if updates.get("kind", revised.kind) is UnitKind.CODE
                else normalize_text(str(override["source_text"]))
            )
            updates["source_text"] = corrected
            updates["source_hash"] = sha256_text(corrected)
            updates["protected_tokens"] = protected_tokens(corrected)
        if "protected_tokens" in override:
            tokens = override["protected_tokens"]
            if not isinstance(tokens, list) or any(
                not isinstance(token, str) or not token.strip() for token in tokens
            ):
                raise ValueError("protected_tokens override must be a list of non-empty strings")
            if not str(override.get("reason", "")).strip():
                raise ValueError("A protected_tokens override requires a reason")
            updates["protected_tokens"] = tokens
        for field in (
            "source_markdown",
            "latex",
            "equation_number",
            "code_language",
            "parent_id",
            "sidebar_id",
            "continues_from_previous",
            "continued_to_next",
        ):
            if field in override:
                updates[field] = override[field]
        if "set_bbox" in override:
            value = override["set_bbox"]
            if not isinstance(value, list) or len(value) != 4:
                raise ValueError("set_bbox override must contain four values")
            updates["bbox"] = _bbox(value)
        if "fragments" in override:
            updates["fragments"] = [
                SourceFragment.model_validate(value) for value in (override["fragments"] or [])
            ]
        if "sidebar_role" in override:
            updates["sidebar_role"] = (
                SidebarRole(str(override["sidebar_role"]))
                if override["sidebar_role"] is not None
                else None
            )
        if "callout_kind" in override:
            updates["callout_kind"] = (
                CalloutKind(str(override["callout_kind"]))
                if override["callout_kind"] is not None
                else None
            )
        if "table" in override:
            updates["table"] = TableData.model_validate(override["table"])
        if "figure_labels" in override:
            updates["figure_labels"] = [
                FigureLabel.model_validate(value) for value in override["figure_labels"]
            ]
        if "asset_refs" in override:
            updates["asset_refs"] = [
                AssetRef.model_validate(value) for value in (override["asset_refs"] or [])
            ]
        if "math_status" in override:
            updates["math_status"] = (
                SemanticStatus(str(override["math_status"]))
                if override["math_status"] is not None
                else None
            )
        if "visual_text_status" in override:
            updates["visual_text_status"] = SemanticStatus(str(override["visual_text_status"]))
        if "verification_status" in override:
            updates["verification_status"] = SemanticStatus(str(override["verification_status"]))
        if override.get("verified") is True:
            if not str(override.get("reason", "")).strip():
                raise ValueError("A verified semantic override requires a reason")
            updates["verification_status"] = SemanticStatus.VERIFIED
            effective_kind = updates.get("kind", revised.kind)
            effective_markdown = updates.get("source_markdown", revised.source_markdown)
            if effective_kind is UnitKind.EQUATION or effective_markdown:
                updates["math_status"] = SemanticStatus.VERIFIED
            if effective_kind is UnitKind.FIGURE:
                updates["visual_text_status"] = SemanticStatus.VERIFIED
        updates["confidence"] = 1.0
        revised = revised.model_copy(update=updates)
    return revised


def _inserted_unit_from_override(override: dict[str, Any]) -> SourceUnit:
    """Build a reviewed source unit inserted between two extracted units."""
    reason = str(override.get("reason", "")).strip()
    if not reason:
        raise ValueError("An insert_after override requires a reason")
    required = ("unit_id", "insert_after", "kind", "page", "bbox", "source_text")
    missing = [field for field in required if field not in override]
    if missing:
        raise ValueError(f"An insert_after override is missing required fields: {missing}")
    bbox_value = override["bbox"]
    if not isinstance(bbox_value, list) or len(bbox_value) != 4:
        raise ValueError("An insert_after override requires a four-value bbox")
    kind = UnitKind(str(override["kind"]))
    source_text = (
        str(override["source_text"]).replace("\r\n", "\n").rstrip()
        if kind is UnitKind.CODE
        else normalize_text(str(override["source_text"]))
    )
    page = int(override["page"])
    bbox = _bbox(bbox_value)
    payload = {
        field: value for field, value in override.items() if field in SourceUnit.model_fields
    }
    payload.update(
        {
            "kind": kind,
            "page": page,
            "bbox": bbox,
            "source_text": source_text,
            "source_hash": sha256_text(source_text),
            "protected_tokens": override.get("protected_tokens", protected_tokens(source_text)),
            "fragments": override.get("fragments", [{"page": page, "bbox": bbox}]),
            "confidence": float(override.get("confidence", 1.0)),
        }
    )
    if override.get("verified") is True:
        payload["verification_status"] = SemanticStatus.VERIFIED
        if kind is UnitKind.EQUATION or payload.get("source_markdown"):
            payload["math_status"] = SemanticStatus.VERIFIED
        if kind is UnitKind.FIGURE:
            payload["visual_text_status"] = SemanticStatus.VERIFIED
    return SourceUnit.model_validate(payload)


def apply_layout_overrides(project_root: Path) -> list[SourceUnit]:
    """Apply reviewed structural overrides as one validated project mutation."""
    with project_write_lock(project_root):
        return _apply_layout_overrides_locked(project_root)


def _apply_layout_overrides_locked(project_root: Path) -> list[SourceUnit]:
    """Build and validate the complete override result before replacing project files."""
    units_path = project_root / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    if not units:
        raise ValueError("No extracted units exist")
    override_entries = _load_overrides(project_root)
    if not override_entries:
        raise ValueError("No layout overrides exist")
    # Every guarded entry is checked in its original form.  Merging first could
    # allow a later duplicate selector to conceal a stale earlier review guard.
    (
        applied_receipts,
        authenticated_chains,
        existing_chain_receipts,
    ) = _preflight_math_review_overrides(project_root, units, override_entries)
    overrides = _merge_overrides(override_entries)
    translations = read_jsonl(project_root / "translations" / "current.jsonl", TranslationRecord)
    translation_by_id = {record.unit_id: record for record in translations}
    translated_ids = set(translation_by_id)
    removed_translation_ids: set[str] = set()
    invalidated_translation_ids: set[str] = set()
    existing_ids = {unit.unit_id for unit in units}
    insertions_by_anchor: dict[str, list[dict[str, Any]]] = {}
    for override in overrides:
        anchor = override.get("insert_after")
        if not isinstance(anchor, str):
            continue
        inserted_id = override.get("unit_id")
        if not isinstance(inserted_id, str) or not inserted_id.strip():
            raise ValueError("An insert_after override requires a unit_id")
        insertions_by_anchor.setdefault(anchor, []).append(override)
    missing_anchors = sorted(set(insertions_by_anchor) - existing_ids)
    if missing_anchors:
        raise ValueError(f"insert_after references missing units: {missing_anchors}")
    translation_affecting_fields = (
        "kind",
        "source_hash",
        "source_markdown",
        "parent_id",
        "sidebar_id",
        "sidebar_role",
        "callout_kind",
        "translatable",
        "render_policy",
        "protected_tokens",
        "latex",
        "equation_number",
        "code_language",
        "table",
        "continues_from_previous",
        "continued_to_next",
        "figure_labels",
    )
    issues_path = project_root / "derived" / "extraction-issues.jsonl"
    issues = read_jsonl(issues_path, ExtractionIssue)
    project_config = None
    document = None
    updated: list[SourceUnit] = []
    pending_assets: list[tuple[Path, Path]] = []
    with TemporaryDirectory(prefix=".littrans-overrides-", dir=project_root) as temp_name:
        temp_root = Path(temp_name)
        try:
            for unit in units:
                revised = _apply_override(unit, overrides)
                if revised is None:
                    if unit.unit_id in translated_ids:
                        removed_translation_ids.add(unit.unit_id)
                    continue
                if (
                    revised.kind is UnitKind.CODE
                    and unit.kind is not UnitKind.CODE
                    and revised.source_text == unit.source_text
                ):
                    if project_config is None:
                        project_config = load_project(project_root)
                    if document is None:
                        document = fitz.open(project_config.source(project_root))
                    restored = _code_text_from_page(document[revised.page - 1], revised.bbox)
                    if restored.strip() and restored != revised.source_text:
                        revised = revised.model_copy(
                            update={
                                "source_text": restored,
                                "source_hash": sha256_text(restored),
                                "protected_tokens": protected_tokens(restored),
                                "code_language": revised.code_language
                                or detect_code_language(restored),
                                "translatable": False,
                                "source_markdown": None,
                            }
                        )
                translation_changed = unit.unit_id in translated_ids and any(
                    getattr(revised, field) != getattr(unit, field)
                    for field in translation_affecting_fields
                )
                if translation_changed:
                    record = translation_by_id[unit.unit_id]
                    if not revised.translatable:
                        removed_translation_ids.add(unit.unit_id)
                    elif revised.source_hash != unit.source_hash:
                        if record.source_hash != revised.source_hash:
                            removed_translation_ids.add(unit.unit_id)
                    else:
                        invalidated_translation_ids.add(unit.unit_id)
                if revised.kind is UnitKind.EQUATION and not revised.asset_refs:
                    if project_config is None:
                        project_config = load_project(project_root)
                    if document is None:
                        document = fitz.open(project_config.source(project_root))
                    asset_name = f"page-{revised.page:04}-equation-override-{revised.unit_id}.png"
                    temporary_asset = temp_root / asset_name
                    _crop_asset(document[revised.page - 1], revised.bbox, temporary_asset)
                    destination = project_root / "derived" / "assets" / asset_name
                    pending_assets.append((temporary_asset, destination))
                    revised = revised.model_copy(
                        update={
                            "asset_refs": [
                                AssetRef(
                                    kind=UnitKind.EQUATION,
                                    path=f"derived/assets/{asset_name}",
                                    bbox=revised.bbox,
                                )
                            ]
                        }
                    )
                # model_copy intentionally skips validation. Re-validate the staged
                # object so a malformed override cannot partially update the ledger.
                updated.append(SourceUnit.model_validate(revised.model_dump(mode="python")))
                for insertion in insertions_by_anchor.get(unit.unit_id, []):
                    inserted_id = str(insertion["unit_id"])
                    if inserted_id in existing_ids:
                        continue
                    inserted = _inserted_unit_from_override(insertion)
                    if inserted.unit_id in {item.unit_id for item in updated}:
                        raise ValueError(f"Duplicate inserted unit_id: {inserted.unit_id}")
                    updated.append(inserted)
                    existing_ids.add(inserted.unit_id)

            staged_translations = [
                (
                    record.model_copy(update={"status": ProjectStatus.DRAFT})
                    if record.unit_id in invalidated_translation_ids
                    else record
                )
                for record in translations
                if record.unit_id not in removed_translation_ids
            ]
            staged_translations = [
                TranslationRecord.model_validate(record.model_dump(mode="python"))
                for record in staged_translations
            ]
            unit_by_id = {unit.unit_id: unit for unit in updated}
            guarded_entries = [
                override
                for override in override_entries
                if "math_review_decision_id" in override or "math_review_guard" in override
            ]
            staged_receipts = dict(applied_receipts)
            for guarded_entry in guarded_entries:
                unit_id = str(guarded_entry["unit_id"])
                decision_id = str(guarded_entry["math_review_decision_id"])
                current = unit_by_id.get(unit_id)
                override_sha256 = _guarded_override_sha256(guarded_entry)
                staged_receipts[override_sha256] = {
                    "schema_version": 1,
                    "override_sha256": override_sha256,
                    "decision_id": decision_id,
                    "unit_id": unit_id,
                    "source_pdf_sha256": str(guarded_entry["source_pdf_sha256"]),
                    "outcome": "present" if current is not None else "absent",
                    "unit_record_sha256": (
                        _source_unit_record_sha256(current) if current is not None else None
                    ),
                }
            staged_chain_receipts = dict(existing_chain_receipts)
            for unit_id, chain in authenticated_chains.items():
                current = unit_by_id.get(unit_id)
                terminal = chain["terminal"]
                if not _state_matches_units(terminal, [current] if current is not None else []):
                    raise ValueError(
                        f"Authenticated override chain for {unit_id} did not produce its terminal state"
                    )
                staged_chain_receipts[unit_id] = {
                    "schema_version": 1,
                    "unit_id": unit_id,
                    "source_pdf_sha256": load_project(project_root).source_sha256,
                    "entry_sha256s": chain["entry_sha256s"],
                    "decision_ids": chain["decision_ids"],
                    "outcome": "present" if current is not None else "absent",
                    "unit_record_sha256": (
                        _source_unit_record_sha256(current) if current is not None else None
                    ),
                }
            reconciled: list[ExtractionIssue] = []
            for issue in issues:
                candidate = unit_by_id.get(issue.unit_id or "")
                resolved = (
                    candidate is None
                    or (
                        issue.code == "math-needs-verification"
                        and candidate.math_status is SemanticStatus.VERIFIED
                    )
                    or (
                        issue.code == "table-needs-verification"
                        and candidate.verification_status is SemanticStatus.VERIFIED
                    )
                    or (
                        issue.code == "figure-text-needs-verification"
                        and candidate.visual_text_status is SemanticStatus.VERIFIED
                    )
                )
                staged_issue = (
                    issue.model_copy(update={"status": IssueStatus.RESOLVED}) if resolved else issue
                )
                reconciled.append(
                    ExtractionIssue.model_validate(staged_issue.model_dump(mode="python"))
                )

            if translations and (invalidated_translation_ids or removed_translation_ids):
                if project_config is None:
                    project_config = load_project(project_root)
                project_config.status = ProjectStatus.DRAFT

            # No authoritative file is replaced until the complete staged snapshot,
            # including derived issue state and generated crops, has validated. Keep
            # exact pre-mutation bytes so interruption or any later write failure
            # restores the entire project view, including overwritten/new assets.
            translations_path = project_root / "translations" / "current.jsonl"
            mutation_paths = [units_path]
            if translations:
                mutation_paths.append(translations_path)
            if project_config is not None and (
                invalidated_translation_ids or removed_translation_ids
            ):
                mutation_paths.append(project_root / "project.yaml")
            if issues:
                mutation_paths.append(issues_path)
            receipts_path = project_root / _APPLIED_MATH_OVERRIDE_RECEIPTS
            chain_receipts_path = project_root / _APPLIED_MATH_OVERRIDE_CHAINS
            if guarded_entries:
                mutation_paths.append(receipts_path)
            if authenticated_chains:
                mutation_paths.append(chain_receipts_path)
            mutation_paths.extend(destination for _, destination in pending_assets)
            snapshots = snapshot_files(mutation_paths)
            try:
                for temporary_asset, destination in pending_assets:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    temporary_asset.replace(destination)
                write_jsonl(units_path, updated)
                if translations:
                    write_jsonl(translations_path, staged_translations)
                if project_config is not None and (
                    invalidated_translation_ids or removed_translation_ids
                ):
                    save_project(project_root, project_config)
                if issues:
                    write_jsonl(issues_path, reconciled)
                if guarded_entries:
                    receipts_path.parent.mkdir(parents=True, exist_ok=True)
                    receipt_text = "".join(
                        json.dumps(staged_receipts[key], ensure_ascii=False, sort_keys=True) + "\n"
                        for key in sorted(staged_receipts)
                    )
                    atomic_write_text(receipts_path, receipt_text)
                if authenticated_chains:
                    chain_receipts_path.parent.mkdir(parents=True, exist_ok=True)
                    chain_receipt_text = "".join(
                        json.dumps(staged_chain_receipts[key], ensure_ascii=False, sort_keys=True)
                        + "\n"
                        for key in sorted(staged_chain_receipts)
                    )
                    atomic_write_text(chain_receipts_path, chain_receipt_text)
            except BaseException:
                restore_files(snapshots)
                raise
        finally:
            if document is not None:
                document.close()
    return updated


def inspect_source(project_root: Path, page_spec: str | None = None) -> dict[str, Any]:
    config = load_project(project_root)
    document = fitz.open(config.source(project_root))
    pages = parse_page_spec(page_spec, document.page_count)
    details: list[dict[str, Any]] = []
    for page_number in pages:
        page = document[page_number - 1]
        text = normalize_text(page.get_text("text", sort=True))
        details.append(
            {
                "page": page_number,
                "text_characters": len(text),
                "image_count": len(page.get_images(full=True)),
                "requires_manual_review": len(text) < 20,
            }
        )
    report = {
        "source": str(config.source(project_root)),
        "source_sha256": config.source_sha256,
        "page_count": document.page_count,
        "selected_pages": pages,
        "outline_entries": len(document.get_toc()),
        "pages": details,
        "scanned_or_empty_pages": [
            item["page"] for item in details if item["requires_manual_review"]
        ],
    }
    write_json(project_root / "derived" / "inspection.json", report)
    return report


def _table_candidates(page: fitz.Page, assets: Path, page_number: int) -> list[BlockCandidate]:
    candidates: list[BlockCandidate] = []
    try:
        tables = page.find_tables().tables
    except Exception:
        return candidates
    for index, table in enumerate(tables, 1):
        bbox = _bbox(table.bbox)
        rows = table.extract()
        row_count = len(rows)
        column_count = max((len(row) for row in rows), default=0)
        nonempty = sum(bool(normalize_text(str(cell or ""))) for row in rows for cell in row)
        cell_count = max(row_count * column_count, 1)
        # Plot grids are a common false positive from find_tables(). A genuine
        # local table needs repeated populated cells, not one legend label in a
        # large otherwise-empty grid.
        if (
            row_count < 2
            or column_count < 2
            or nonempty < max(4, row_count + column_count - 2)
            or nonempty / cell_count < 0.35
        ):
            continue
        table_data = table_from_rows(rows)
        text = "\n".join(
            " | ".join(normalize_text(str(cell or "")) for cell in row) for row in rows
        ).strip()
        asset_name = f"page-{page_number:04}-table-{index:02}.png"
        _crop_asset(page, bbox, assets / asset_name)
        candidates.append(
            BlockCandidate(
                bbox=bbox,
                text=text,
                kind=UnitKind.TABLE,
                confidence=0.86 if text else 0.62,
                translatable=bool(text),
                font_size=0,
                font_names=set(),
                asset_path=f"derived/assets/{asset_name}",
                table=table_data,
            )
        )
    return candidates


def extract_source(
    project_root: Path, page_spec: str | None = None, replace: bool = False
) -> list[SourceUnit]:
    config = load_project(project_root)
    units_path = project_root / "derived" / "units.jsonl"
    existing_units = read_jsonl(units_path, SourceUnit)
    if existing_units and not replace:
        raise ValueError(
            "units.jsonl already exists; pass --replace for an explicit safe replacement"
        )

    source_path = config.source(project_root)
    document = fitz.open(source_path)
    pages = parse_page_spec(page_spec, document.page_count)
    outline = _outline_by_page(document)
    repeated = _repeated_marginal_text(document, pages)
    overrides = _merge_overrides(_load_overrides(project_root))
    assets = project_root / "derived" / "assets"
    extracted: list[SourceUnit] = []
    issues: list[ExtractionIssue] = []

    for page_number in pages:
        page = document[page_number - 1]
        raw = page.get_text("dict", sort=True)
        text_blocks = [block for block in raw.get("blocks", []) if block.get("type") == 0]
        sizes = [
            float(span.get("size", 0))
            for block in text_blocks
            for line in block.get("lines", [])
            for span in line.get("spans", [])
            if span.get("text", "").strip()
        ]
        median_size = statistics.median(sizes) if sizes else 10.0
        candidates = _table_candidates(page, assets, page_number)
        occupied = [candidate.bbox for candidate in candidates]

        image_index = 0
        for block in raw.get("blocks", []):
            bbox = _bbox(block.get("bbox", (0, 0, 0, 0)))
            if block.get("type") == 1:
                image_index += 1
                asset_name = f"page-{page_number:04}-image-{image_index:02}.png"
                _crop_asset(page, bbox, assets / asset_name)
                candidates.append(
                    BlockCandidate(
                        bbox=bbox,
                        text="",
                        kind=UnitKind.FIGURE,
                        confidence=0.92,
                        translatable=False,
                        font_size=0,
                        font_names=set(),
                        asset_path=f"derived/assets/{asset_name}",
                        visual_text_status=SemanticStatus.UNVERIFIED,
                    )
                )
                occupied.append(bbox)
                continue
            if block.get("type") != 0 or any(
                _rect_overlap(bbox, region) > 0.55 for region in occupied
            ):
                continue
            for subblock in split_mixed_pdf_block(block):
                bbox = _bbox(subblock.get("bbox", block.get("bbox", (0, 0, 0, 0))))
                text, font_size, fonts = _block_text(subblock)
                if not text or _is_marginal(bbox, text, page, repeated):
                    continue
                kind, confidence, translatable = _classify_text(
                    text,
                    bbox,
                    page,
                    font_size,
                    median_size,
                    fonts,
                    outline.get(page_number, []),
                )
                source_markdown: str | None = None
                latex: str | None = None
                math_status: SemanticStatus | None = None
                code_language: str | None = None
                if kind is UnitKind.CODE:
                    text = code_from_block(subblock)
                    code_language = detect_code_language(text)
                elif kind in {
                    UnitKind.PARAGRAPH,
                    UnitKind.CAPTION,
                    UnitKind.FOOTNOTE,
                    UnitKind.NOTE,
                }:
                    marked = inline_math_markdown(subblock, text)
                    source_markdown = marked if marked != text else None
                    if source_markdown:
                        math_status = SemanticStatus.UNVERIFIED
                elif kind is UnitKind.EQUATION:
                    latex = unicode_math_to_latex(text)
                    math_status = SemanticStatus.UNVERIFIED
                asset_path: str | None = None
                candidates.append(
                    BlockCandidate(
                        bbox=bbox,
                        text=text,
                        kind=kind,
                        confidence=confidence,
                        translatable=translatable,
                        font_size=font_size,
                        font_names=fonts,
                        asset_path=asset_path,
                        source_markdown=source_markdown,
                        latex=latex,
                        math_status=math_status,
                        code_language=code_language,
                        callout_kind=_callout_kind(text) if kind is UnitKind.NOTE else None,
                    )
                )

        candidates = _merge_code_fragments(candidates)
        candidates = _merge_note_fragments(candidates)
        candidates = _merge_caption_fragments(candidates)
        candidates = _merge_table_regions(candidates, page, assets, page_number)
        candidates = _merge_footnote_fragments(candidates)
        candidates = _merge_equation_regions(candidates, page, assets, page_number)
        candidates = _merge_vector_figures(candidates, page, assets, page_number)
        candidates = _reading_order(candidates, page)
        if not any(candidate.text for candidate in candidates):
            issues.append(
                ExtractionIssue(
                    page=page_number,
                    severity=Severity.BLOCKER,
                    code="no-text-layer",
                    message="No usable text layer was found; OCR is outside the first-round scope.",
                )
            )

        parent_id: str | None = None
        for ordinal, candidate in enumerate(candidates, 1):
            source_hash = sha256_text(candidate.text)
            fingerprint = sha256_text(
                f"{page_number}|{candidate.kind}|{candidate.bbox}|{candidate.text}"
            )[:8]
            unit_id = f"p{page_number:04}-u{ordinal:03}-{fingerprint}"
            refs = (
                [AssetRef(kind=candidate.kind, path=candidate.asset_path, bbox=candidate.bbox)]
                if candidate.asset_path
                else []
            )
            unit = SourceUnit(
                unit_id=unit_id,
                kind=candidate.kind,
                page=page_number,
                bbox=_bbox(round(value, 2) for value in candidate.bbox),
                source_text=candidate.text,
                source_hash=source_hash,
                source_markdown=candidate.source_markdown,
                parent_id=parent_id,
                callout_kind=candidate.callout_kind,
                translatable=candidate.translatable,
                protected_tokens=protected_tokens(candidate.text),
                asset_refs=refs,
                fragments=[SourceFragment(page=page_number, bbox=candidate.bbox)],
                latex=candidate.latex,
                equation_number=candidate.equation_number,
                math_status=candidate.math_status,
                code_language=candidate.code_language,
                table=candidate.table,
                figure_labels=candidate.figure_labels or [],
                visual_text_status=candidate.visual_text_status,
                verification_status=(
                    SemanticStatus.UNVERIFIED
                    if candidate.kind in {UnitKind.EQUATION, UnitKind.FIGURE, UnitKind.TABLE}
                    or candidate.math_status is SemanticStatus.UNVERIFIED
                    else SemanticStatus.AUTO
                ),
                confidence=candidate.confidence,
            )
            overridden = _apply_override(unit, overrides)
            if overridden is None:
                continue
            unit = overridden
            if unit.kind is UnitKind.EQUATION and not unit.asset_refs:
                asset_name = f"page-{page_number:04}-equation-override-{unit.unit_id}.png"
                _crop_asset(page, unit.bbox, assets / asset_name)
                unit = unit.model_copy(
                    update={
                        "asset_refs": [
                            AssetRef(
                                kind=UnitKind.EQUATION,
                                path=f"derived/assets/{asset_name}",
                                bbox=unit.bbox,
                            )
                        ]
                    }
                )
            extracted.append(unit)
            if unit.kind is UnitKind.HEADING:
                parent_id = unit.unit_id
            if unit.confidence < 0.65:
                issues.append(
                    ExtractionIssue(
                        page=page_number,
                        severity=Severity.MAJOR,
                        code="low-confidence-unit",
                        message="Unit classification or extraction requires manual confirmation.",
                        unit_id=unit_id,
                        details={"confidence": unit.confidence, "kind": unit.kind},
                    )
                )
            if unit.math_status is not SemanticStatus.VERIFIED and (
                unit.kind is UnitKind.EQUATION or unit.source_markdown
            ):
                issues.append(
                    ExtractionIssue(
                        issue_id=f"extract-{unit.unit_id}-math",
                        page=page_number,
                        severity=Severity.BLOCKER,
                        code="math-needs-verification",
                        message="Compare the crop with the LaTeX transcription and mark it verified.",
                        unit_id=unit.unit_id,
                        details={"latex": unit.latex, "source_markdown": unit.source_markdown},
                    )
                )
            if (
                unit.kind is UnitKind.TABLE
                and unit.verification_status is not SemanticStatus.VERIFIED
            ):
                issues.append(
                    ExtractionIssue(
                        issue_id=f"extract-{unit.unit_id}-table",
                        page=page_number,
                        severity=Severity.BLOCKER if unit.table is None else Severity.MAJOR,
                        code="table-needs-verification",
                        message="Verify table boundaries, row/column structure, cell order, and source text.",
                        unit_id=unit.unit_id,
                        details={"structured": unit.table is not None},
                    )
                )
            if (
                unit.kind is UnitKind.FIGURE
                and unit.visual_text_status is not SemanticStatus.VERIFIED
            ):
                issues.append(
                    ExtractionIssue(
                        issue_id=f"extract-{unit.unit_id}-visual",
                        page=page_number,
                        severity=Severity.MAJOR,
                        code="figure-text-needs-verification",
                        message="Inspect the figure or screenshot and translate every meaningful internal label.",
                        unit_id=unit.unit_id,
                    )
                )

    for index in range(1, len(extracted)):
        previous, current = extracted[index - 1], extracted[index]
        if (
            previous.kind is UnitKind.PARAGRAPH
            and current.kind is UnitKind.PARAGRAPH
            and current.page == previous.page
            and looks_like_continuation(previous.source_text, current.source_text)
        ):
            extracted[index - 1] = previous.model_copy(update={"continued_to_next": True})
            extracted[index] = current.model_copy(update={"continues_from_previous": True})
    selected_pages = sorted({unit.page for unit in extracted})
    index_by_id = {unit.unit_id: index for index, unit in enumerate(extracted)}
    for page_number, next_page in zip(selected_pages, selected_pages[1:], strict=False):
        if next_page != page_number + 1:
            continue
        previous_candidates = [
            unit
            for unit in extracted
            if unit.page == page_number and unit.kind is UnitKind.PARAGRAPH
        ]
        next_candidates = [
            unit for unit in extracted if unit.page == next_page and unit.kind is UnitKind.PARAGRAPH
        ]
        if not previous_candidates or not next_candidates:
            continue
        previous = max(previous_candidates, key=lambda unit: unit.bbox[3])
        current = min(next_candidates, key=lambda unit: unit.bbox[1])
        if looks_like_continuation(previous.source_text, current.source_text):
            previous_index = index_by_id[previous.unit_id]
            current_index = index_by_id[current.unit_id]
            extracted[previous_index] = extracted[previous_index].model_copy(
                update={"continued_to_next": True}
            )
            extracted[current_index] = extracted[current_index].model_copy(
                update={"continues_from_previous": True}
            )

    if existing_units and replace:
        old = {unit.unit_id: unit.source_hash for unit in existing_units}
        new = {unit.unit_id: unit.source_hash for unit in extracted}
        translations = project_root / "translations" / "current.jsonl"
        if translations.exists() and old != new:
            raise ValueError(
                "Extraction would change unit identities while translations exist; create a new project "
                "or migrate explicitly instead of overwriting"
            )

    write_jsonl(units_path, extracted)
    write_jsonl(project_root / "derived" / "extraction-issues.jsonl", issues)
    write_json(
        project_root / "derived" / "document.json",
        {
            "source_sha256": config.source_sha256,
            "extractor_version": config.extractor_version,
            "pages": pages,
            "unit_count": len(extracted),
            "translatable_unit_count": sum(unit.translatable for unit in extracted),
            "outline": [
                {"level": level, "title": title, "page": page}
                for level, title, page, *_rest in document.get_toc(simple=False)
            ],
        },
    )
    config.status = ProjectStatus.EXTRACTED
    save_project(project_root, config)
    return extracted
