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
FORMAT_CONTROLS = {chr(9), chr(10), chr(13)}


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
    integrals) decode to control characters, which Python counts as whitespace."""
    text = str(glyph["text"])
    return bool(text.strip()) or any(ord(c) < 32 and c not in FORMAT_CONTROLS for c in text)


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


def plan_structure(
    glyphs: list[dict[str, Any]], blocks: list[dict[str, Any]], layout: list[dict[str, Any]], height: float,
    display_glyph_ids: set[str] | None = None,
) -> dict[str, Any]:
    gm = {g["id"]: g for g in glyphs}
    usable = [g for g in glyphs if math.isfinite(g["size"]) and round(g["size"], 1) > 0]
    sizes = (Counter(round(g["size"], 1) for g in usable if g["text"].isalpha())
             or Counter(round(g["size"], 1) for g in usable))
    font_size = sizes.most_common(1)[0][0] if sizes else 10
    starts = Counter(
        round(gm[line[0]]["origin"][0], 1) for b in blocks for line in b["lines"] if line
    )
    margin = starts.most_common(1)[0][0] if starts else 0
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
    # The page's usual baseline pitch; a gap well beyond it is paragraph white space.
    pitches = sorted(
        gm[b["lines"][i + 1][0]]["origin"][1] - gm[b["lines"][i][0]]["origin"][1]
        for b in blocks
        for i in range(len(b["lines"]) - 1)
        if 0 < gm[b["lines"][i + 1][0]]["origin"][1] - gm[b["lines"][i][0]]["origin"][1] < font_size * 3
    )
    pitch = pitches[len(pitches) // 2] if pitches else font_size * 1.2
    gap_threshold = max(font_size * 1.75, pitch * 1.45)
    for b in blocks:
        gs = [gm[gid] for line in b["lines"] for gid in line]
        # A wrapped heading keeps its continuation line even though the wrap is indented.
        heading_block = bool(gs) and any(all(_contains(g, box) for g in gs) for box in title_boxes)
        chunks: list[list[list[str]]] = []
        current: list[list[str]] = []
        last_y: float | None = None
        previous_display = False
        bullet_x: float | None = None
        for line in b["lines"]:
            g = gm[line[0]]
            x, y = g["origin"]
            indent = margin + font_size * 0.8 < x < margin + font_size * 2.8
            inked = [gid for gid in line if inked_glyph(gm[gid])]
            display_line = bool(inked) and sum(gid in display_glyph_ids for gid in inked) >= len(inked) * 0.8
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
            if current and not heading_block and (
                (indent and new_line)
                or gap
                or run_in
                or display_line != previous_display
                or bullet_line
                or list_end
            ):
                chunks.append(current)
                current = []
                bullet_x = None
            if bullet_line:
                bullet_x = gm[inked[0]]["origin"][0]
            current.append(line)
            last_y = y
            previous_display = display_line
        if current:
            chunks.append(current)
        for i, lines in enumerate(chunks):
            bid = b["id"] if i == 0 else b["id"] + f"-s{i + 1}"
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
                gm[line[0]]["origin"][0]
                for line in lines
                if gm[line[0]]["origin"][0] < margin + font_size * 2.8
            ]
            first_x[bid] = near[0] if near else gg[0]["origin"][0]
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
    }


def assemble_structure(units: list[SourceUnit], assets: dict[str, FidelityAsset], plan: dict[str, Any], make_unit: Callable[..., SourceUnit]) -> list[SourceUnit]:
    """Use parent_id for a paragraph containing independently numbered displays."""
    from littrans.fidelity_models import asset_reference_ids
    from littrans.models import RenderPolicy

    result: list[SourceUnit] = []
    group: str | None = None
    active_note: str | None = None
    statement = None
    display_blocks = set(plan.get("display_blocks", ()))

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
            indented = (
                plan["margin"] + plan["font_size"] * 0.8
                < x
                < plan["margin"] + plan["font_size"] * 2.8
            )
            starts_statement = _starts_statement(u.source_text)
            enumerated = bool(re.match(r"[*\s]*\((?:[a-z]|[ivxlcdm]+|\d+)\)", u.source_text, re.I))
            proof = bool(re.match(r"[*\s]*Proof\b", u.source_text))
            # A bold run-in label ("2.1.4. Stochastic processes.") opens a paragraph.
            run_in = bool(RUN_IN_LABEL.match(u.source_text))
            boundary = u.kind.value in {"heading", "list_item", "figure", "table", "caption"} or starts_statement or proof or run_in
            if starts_statement:
                statement = u.unit_id
            elif (
                u.kind.value == "heading"
                or proof
                or (indented and not enumerated and u.kind.value != "equation")
            ):
                statement = None
            previous_body = next((r for r in reversed(result) if r.render_policy != RenderPolicy.OMIT), None)
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
            if figure_pair and previous_body is not None:
                # A figure/table and its adjacent caption form one element.
                group = previous_body.parent_id or previous_body.unit_id
            elif list_member and previous_body is not None:
                # List items belong to the paragraph that introduces them and to
                # each other; the list is one structure, not scattered elements.
                group = previous_body.parent_id or previous_body.unit_id
            elif statement and (starts_statement or enumerated or u.kind.value == "equation"):
                group = statement
            elif group is None or after_heading or (u.kind.value != "equation" and (indented or boundary)):
                group = u.unit_id
            u = rebuild(u, parent_id=group)
        # Merge prose fragments within one paragraph, retaining display children.
        previous = result[-1] if result else None
        display = any(assets[aid].display for aid in refs) or bid in display_blocks
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
            and not display
            and not previous_display
            and previous.kind.value not in {"heading", "caption", "figure", "table", "list_item"}
            and u.kind.value not in {"heading", "caption", "figure", "table", "list_item"}
            and not re.match(r"[*\s]*\((?:[a-z]|[ivxlcdm]+|\d+)\)", u.source_text, re.I)
        )):
            joined = previous.source_text.rstrip() + " " + u.source_text.lstrip()
            joined = re.sub(r"\s+([,.;:”’)\]])", r"\1", joined)
            joined = re.sub(r"([a-z])-\s+([a-z])", r"\1\2", joined)
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
        else:
            result.append(u)
    body = [
        i
        for i, u in enumerate(result)
        if u.render_policy.value == "include" and u.kind.value not in {"footnote", "note"}
    ]
    if body:
        first, last = body[0], body[-1]
        bid = result[first].unit_id.split("-", 1)[1]
        if (
            plan.get("indent_style")
            and result[first].page > 1
            and result[first].kind.value == "paragraph"
            and abs(plan["first_x"].get(bid, 0) - plan["margin"]) < 2
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
    pattern = r"\{\{asset:[^}]+\}\}(?:\s*\{\{asset:[^}]+\}\})+"
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
