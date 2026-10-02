"""Shared paragraph, label and outline behavior. No marker-pattern inference."""
from __future__ import annotations

import html
import json
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from littrans.models import ParagraphData, RenderPolicy, SourceUnit, UnitKind
from littrans.storage import sha256_text

STRUCTURE_KEYS = (
    "paragraph", "paragraph_id", "item_id", "parent_item_id", "container_role", "section_id",
)


def body_text(unit: SourceUnit, *, markdown: bool = False) -> str:
    if unit.paragraph is not None:
        return (unit.paragraph.body_markdown or unit.paragraph.body_text) if markdown else unit.paragraph.body_text
    return (unit.source_markdown or unit.source_text) if markdown else unit.source_text


def label_text(unit: SourceUnit, translations: Mapping[str, str] | None = None,
               *, markdown: bool = False) -> str:
    label = unit.paragraph.label if unit.paragraph else None
    if label is None:
        return ""
    return "".join(
        translations.get(part.part_id, part.source_text)
        if translations is not None and part.policy == "translatable"
        else (part.source_markdown or part.source_text) if markdown else part.source_text
        for part in label.parts
    )


def label_translation_errors(unit: SourceUnit, translations: Mapping[str, str]) -> list[str]:
    label = unit.paragraph.label if unit.paragraph else None
    expected = {part.part_id for part in label.parts if part.policy == "translatable"} if label else set()
    errors = []
    if set(translations) != expected:
        errors.append(f"Label translation mapping mismatch: expected={sorted(expected)}, supplied={sorted(translations)}")
    if any(not text.strip() for text in translations.values()):
        errors.append("Label translations must not be empty")
    return errors


def label_markdown(unit: SourceUnit, translations: Mapping[str, str] | None = None) -> str:
    """Plain label characters are literal; inline formatting must be declared."""
    label = unit.paragraph.label if unit.paragraph else None
    pieces = []
    for part in label.parts if label else []:
        translated = translations is not None and part.policy == "translatable"
        value = (translations or {}).get(part.part_id, part.source_text) if translated else part.source_text
        escaped = re.sub(r"([\\`*_\[\]<>&$])", r"\\\1", value)
        if part.source_markdown and not translated:
            pieces.append(part.source_markdown)
        elif part.source_markdown and part.source_text in part.source_markdown:
            pieces.append(part.source_markdown.replace(part.source_text, escaped, 1))
        else:
            pieces.append(escaped)
    return "".join(pieces)


def label_html(unit: SourceUnit, inline: Callable[[str], str],
               translations: Mapping[str, str] | None = None) -> str:
    label = unit.paragraph.label if unit.paragraph else None
    pieces = []
    for part in label.parts if label else []:
        translated = translations is not None and part.policy == "translatable"
        value = (translations or {}).get(part.part_id, part.source_text) if translated else part.source_text
        if part.source_markdown and not translated:
            pieces.append(inline(part.source_markdown))
        elif part.source_markdown and part.source_text in part.source_markdown:
            token = "LITTRANS_LABEL_TRANSLATION"
            while token in part.source_markdown:
                token += "_"
            formatted = inline(part.source_markdown.replace(part.source_text, token, 1))
            pieces.append(formatted.replace(token, html.escape(value))
                          if token in formatted else html.escape(value))
        else:
            pieces.append(html.escape(value))
    return "".join(pieces)


def language_text(unit: SourceUnit, *, markdown: bool = False) -> str:
    """Only material translated by the language worker, excluding fixed labels."""
    pieces = [body_text(unit, markdown=markdown)]
    if unit.paragraph and unit.paragraph.label:
        pieces.extend((part.source_markdown or part.source_text) if markdown else part.source_text
                      for part in unit.paragraph.label.parts if part.policy == "translatable")
    return "\n".join(pieces)


def source_binding(unit: SourceUnit) -> str:
    """Bind content and explicit structure without approval/status or prior hashes."""
    payload = unit.model_dump(mode="json", exclude={
        "source_hash", "confidence", "status", "verification_status", "schema_version",
        "source_markdown", "latex", "math_status", "visual_text_status",
    }, exclude_none=True)
    if "paragraph" in payload:
        payload["paragraph"].pop("body_markdown", None)
    return sha256_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def outline(units: Sequence[SourceUnit]) -> list[dict[str, Any]]:
    stack: list[dict[str, Any]] = []
    rows = []
    seen: set[str] = set()
    for unit in units:
        if not unit.is_heading or unit.render_policy is RenderPolicy.OMIT:
            continue
        anchor = unit.paragraph_id or unit.unit_id
        if anchor in seen:
            continue
        seen.add(anchor)
        assert unit.paragraph is not None
        level = unit.paragraph.outline_level
        assert level is not None
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        row = {"unit_id": anchor, "level": level, "title": unit.source_text,
               "page": unit.page, "parent_id": stack[-1]["unit_id"] if stack else None,
               "level_jump": level > (stack[-1]["level"] + 1 if stack else 1)}
        rows.append(row)
        stack.append(row)
    return rows


def toc_entries(units: Sequence[SourceUnit], targets: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Assemble complete titles, including reviewed cross-page fragments, once."""
    by_id = {unit.unit_id: unit for unit in units}
    entries = []
    for row in outline(units):
        head = by_id.get(row["unit_id"])
        if head is None:
            continue
        record = (targets or {}).get(head.unit_id)
        label = label_text(head, record.label_translations if record else None)
        bodies = []
        for unit in units:
            if unit.paragraph_id != head.unit_id:
                continue
            translated = (targets or {}).get(unit.unit_id)
            bodies.append(translated.target_text if translated else body_text(unit))
        entries.append({**row, "title": " ".join(piece for piece in [label, *bodies] if piece)})
    return entries


def toc_html(entries: Sequence[Mapping[str, Any]]) -> str:
    if not entries:
        return ""
    return '<nav aria-label="Contents" class="contents"><ul style="list-style:none;padding:0">' + "".join(
        f'<li data-outline-level="{row["level"]}" style="margin-left:{row["level"] - 1}em">'
        f'<a href="#{html.escape(row["unit_id"])}">{html.escape(row["title"])}</a></li>'
        for row in entries
    ) + '</ul></nav>'


def bind_sections(units: Sequence[SourceUnit]) -> list[SourceUnit]:
    section: str | None = None
    result = []
    for unit in units:
        if unit.render_policy is RenderPolicy.INCLUDE:
            if unit.is_heading:
                section = unit.paragraph_id or unit.unit_id
            if unit.section_id != section:
                unit = unit.model_copy(update={"section_id": section})
                unit.source_hash = source_binding(unit)
        result.append(unit)
    return result


def relation_neighbors(units: Sequence[SourceUnit]) -> dict[str, set[str]]:
    """Atomic paragraph/item/container edges; sections are context, not atomic groups."""
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    ids = {unit.unit_id for unit in units}
    for unit in units:
        for field in ("paragraph_id", "item_id", "parent_id"):
            anchor = getattr(unit, field)
            if anchor:
                groups[field, anchor].add(unit.unit_id)
                if anchor in ids:
                    groups[field, anchor].add(anchor)
        if unit.parent_item_id:
            groups["item_id", unit.parent_item_id].add(unit.unit_id)
    neighbors: dict[str, set[str]] = defaultdict(set)
    for members in groups.values():
        for uid in members:
            neighbors[uid].update(members - {uid})
    return {uid: members for uid, members in neighbors.items() if members}


def structure_errors(units: Sequence[SourceUnit]) -> list[tuple[str, str]]:
    by_id = {unit.unit_id: unit for unit in units}
    errors: list[tuple[str, str]] = []
    positions = {unit.unit_id: index for index, unit in enumerate(units)}
    for unit in units:
        for field in ("paragraph_id", "item_id", "parent_item_id", "parent_id", "section_id"):
            anchor = getattr(unit, field)
            if anchor and anchor not in by_id:
                errors.append((unit.unit_id, f"Unknown {field}: {anchor}"))
        if unit.paragraph_id and unit.paragraph_id in by_id:
            head = by_id[unit.paragraph_id]
            if head.kind is not UnitKind.PARAGRAPH or head.paragraph_id != head.unit_id:
                errors.append((unit.unit_id, "paragraph_id must reference a paragraph head"))
            elif unit.paragraph and head.paragraph and (
                unit.paragraph.type != head.paragraph.type
                or unit.paragraph.outline_level != head.paragraph.outline_level
            ):
                errors.append((unit.unit_id, "Paragraph fragments disagree on type or outline level"))
            if head.unit_id != unit.unit_id and any(getattr(unit, field) != getattr(head, field)
                    for field in ("item_id", "parent_item_id", "parent_id", "container_role")):
                errors.append((unit.unit_id, "Paragraph fragments disagree on item or container membership"))
            if positions[head.unit_id] > positions[unit.unit_id]:
                errors.append((unit.unit_id, "A paragraph fragment cannot precede its head"))
        for field in ("item_id", "parent_item_id"):
            anchor = getattr(unit, field)
            if anchor in by_id and by_id[anchor].item_id != anchor:
                errors.append((unit.unit_id, f"{field} must reference an item head"))
        if unit.container_role and not unit.parent_id:
            errors.append((unit.unit_id, "container_role requires an explicit container parent_id"))
        if unit.section_id in by_id and not by_id[unit.section_id].is_heading:
            errors.append((unit.unit_id, "section_id must reference an outline paragraph"))
        for field in ("parent_id", "parent_item_id"):
            visited = {unit.unit_id}
            anchor = getattr(unit, field)
            while anchor in by_id and anchor != unit.unit_id:
                if anchor in visited:
                    errors.append((unit.unit_id, f"Cyclic {field}"))
                    break
                visited.add(anchor)
                next_anchor = getattr(by_id[anchor], field)
                if next_anchor == anchor:
                    break
                anchor = next_anchor
            if anchor == unit.unit_id and getattr(unit, field) != unit.unit_id:
                errors.append((unit.unit_id, f"Cyclic {field}"))
        if unit.parent_item_id == unit.unit_id:
            errors.append((unit.unit_id, "An item cannot be its own parent item"))
    return errors


def paragraph_html(unit: SourceUnit, text: str, inline: Callable[[str], str],
                   translations: Mapping[str, str] | None = None) -> str:
    assert unit.paragraph is not None
    data = unit.paragraph
    layout = data.layout
    size = layout.font_size_pt
    styles = [f"margin-left:{layout.left_indent_pt / size:g}em",
              f"margin-right:{layout.right_indent_pt / size:g}em",
              f"text-align:{layout.alignment}",
              f"text-indent:{layout.first_line_offset_pt / size:g}em"]
    if layout.space_before_pt:
        styles.append(f"margin-top:{layout.space_before_pt / size:g}em")
    if layout.space_after_pt:
        styles.append(f"margin-bottom:{layout.space_after_pt / size:g}em")
    label = label_text(unit, translations, markdown=translations is None)
    content = inline(text)
    if label:
        label_left = layout.label_indent_pt if layout.label_indent_pt is not None else (
            layout.left_indent_pt + layout.first_line_offset_pt)
        width = max(0, layout.left_indent_pt - label_left - layout.label_gap_pt) / size
        content = (f'<span class="paragraph-label" style="min-width:{width:g}em;'
                   f'text-align:{layout.label_alignment}">'
                   + label_html(unit, inline, translations)
                   + '</span> <span class="paragraph-body">' + content + "</span>")
        styles[0] = f"margin-left:{label_left / size:g}em"
        styles[3] = "text-indent:0"
        styles.extend(["display:grid", "grid-template-columns:max-content minmax(0,1fr)",
                       f"column-gap:{layout.label_gap_pt / size:g}em", "align-items:baseline"])
    level = data.outline_level if unit.paragraph_id == unit.unit_id else None
    tag = f"h{level}" if level is not None and level <= 6 else "p"
    attrs = f' role="heading" aria-level="{level}"' if level is not None and level > 6 else ""
    return (f'<{tag} class="paragraph paragraph-{html.escape(data.type.value)}"'
            f' style="{";".join(styles)}"{attrs}>' + content + f"</{tag}>")


def plain_paragraph(text: str, markdown: str | None = None, **values: Any) -> ParagraphData:
    return ParagraphData(body_text=text, body_markdown=markdown, **values)
