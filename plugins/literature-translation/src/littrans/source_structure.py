"""Recover book structure from line geometry after preserving native glyphs."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Sequence
from typing import Any

from littrans.fidelity_models import FidelityAsset
from littrans.models import SourceUnit


def _contains(g: dict[str, Any], box: Sequence[float]) -> bool:
    b = g["bbox"]
    return bool(box[0] <= (b[0] + b[2]) / 2 <= box[2] and box[1] <= (b[1] + b[3]) / 2 <= box[3])


TITLE_LABELS = {"doc_title", "paragraph_title", "title"}
LIST_BULLETS = set("\u2022\u25e6\u25aa\u25a0\u25cf")


def plan_structure(
    glyphs: list[dict[str, Any]], blocks: list[dict[str, Any]], layout: list[dict[str, Any]], height: float,
    display_glyph_ids: set[str] | None = None,
) -> dict[str, Any]:
    gm = {g["id"]: g for g in glyphs}
    font_size = (
        Counter(round(g["size"], 1) for g in glyphs if g["text"].isalpha()).most_common(1)[0][0]
        if glyphs
        else 10
    )
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
            match = re.match(r"(\d+)(?=[A-Za-z])", "".join(g["text"] for g in gs))
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
    for g in glyphs:
        if (
            g["text"] in {n["number"] for n in notes.values()}
            and g["size"] < font_size * 0.8
            and g["bbox"][1] < note_top
            and g["bbox"][1] > height * 0.12
            and not re.search(r"cmmi|cmsy|cmr|cmex", g["font"], re.I)
        ):
            bid = next(bid for bid, n in notes.items() if n["number"] == g["text"])
            markers[g["id"]] = {"number": g["text"], "note_block": bid, "definition": False}
    # Split a native block at a new first-line indent, but not at glyph fragments
    # on the same visual line. PDF blocks may span multiple author paragraphs.
    split, first_x, display_blocks = [], {}, set()
    title_boxes = [box for label, box in labels if label in TITLE_LABELS]
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
            inked = [gid for gid in line if gm[gid]["text"].strip()]
            display_line = bool(inked) and sum(gid in display_glyph_ids for gid in inked) >= len(inked) * 0.8
            bullet_line = len(inked) >= 2 and gm[inked[0]]["text"] in LIST_BULLETS
            # Prose returning left of the bullet column ends the list item.
            list_end = bullet_x is not None and not bullet_line and x < bullet_x - font_size * 0.5
            if current and not heading_block and (
                (indent and last_y is not None and y - last_y > font_size * 0.7)
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
            inked_ids = [gid for line in lines for gid in line if gm[gid]["text"].strip()]
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
            starts_statement = bool(
                re.match(
                    r"\**(?:Theorem|Lemma|Proposition|Definition|Corollary|Claim)\b", u.source_text
                )
            )
            enumerated = bool(re.match(r"[*\s]*\((?:[a-z]|[ivxlcdm]+|\d+)\)", u.source_text, re.I))
            proof = bool(re.match(r"[*\s]*Proof\b", u.source_text))
            boundary = u.kind.value in {"heading", "list_item", "figure", "table", "caption"} or starts_statement or proof
            if starts_statement:
                statement = u.unit_id
            elif (
                u.kind.value == "heading"
                or proof
                or (indented and not enumerated and u.kind.value != "equation")
            ):
                statement = None
            previous_body = result[-1] if result else None
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
            if figure_pair and previous_body is not None:
                # A figure/table and its adjacent caption form one element.
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
        same_line = bool(
            previous
            and previous.render_policy != RenderPolicy.OMIT
            and not display
            and not previous_display
            and u.kind.value in {"paragraph", "list_item"}
            and previous.kind.value in {"paragraph", "list_item"}
            and u.bbox[0] > previous.bbox[0]
            and min(previous.bbox[3], u.bbox[3]) - max(previous.bbox[1], u.bbox[1])
            > 0.5 * min(previous.bbox[3] - previous.bbox[1], u.bbox[3] - u.bbox[1])
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
            joined = re.sub(r"\s+([,.;])", r"\1", joined)
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

    parts = []
    for style, group in groupby(tokens, key=lambda item: item[1]):
        text = "".join(item[0] for item in group)
        core = text.strip()
        if not core or not style:
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
