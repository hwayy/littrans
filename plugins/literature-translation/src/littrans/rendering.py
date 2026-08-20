from __future__ import annotations

import html
import json
import re
from importlib.resources import files
from pathlib import Path
from typing import Any

from jinja2 import Environment, select_autoescape
from latex2mathml.converter import convert as latex_to_mathml
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from littrans.batching import load_manifest
from littrans.evidence import (
    dependency_closure,
    effective_figure_labels,
    equation_markdown,
)
from littrans.extractor import parse_page_spec
from littrans.hosts import WAVE_BATCH_SET_MAX
from littrans.models import (
    BatchManifest,
    CalloutKind,
    IssueStatus,
    ProjectStatus,
    RenderPolicy,
    ReviewIssue,
    Severity,
    SidebarRole,
    SourceUnit,
    TableData,
    UnitKind,
)
from littrans.project import load_terms, translation_map
from littrans.quality import audit_coverage, qa_report_is_current
from littrans.semantics import (
    escape_markdown_prose,
    fenced_code,
    normalize_zh_caption,
    table_to_html,
    table_to_markdown,
)
from littrans.storage import (
    atomic_write_text,
    load_project,
    project_write_lock,
    read_jsonl,
)
from littrans.verification import require_verified_extraction

LEGACY_PUBLISHABLE = {
    ProjectStatus.MACHINE_REVIEWED,
    ProjectStatus.EXTERNAL_REVIEWED,
    ProjectStatus.HUMAN_APPROVED,
}


def _manifest_cover(
    selected_ids: set[str], manifests: list[BatchManifest]
) -> list[BatchManifest] | None:
    """Choose a deterministic evidence cover without requiring redundant batches."""
    remaining = set(selected_ids)
    cover: list[BatchManifest] = []
    candidates = list(manifests)
    while remaining:
        ranked = sorted(
            (
                (len(remaining & set(manifest.unit_ids)), manifest)
                for manifest in candidates
                if remaining & set(manifest.unit_ids)
            ),
            key=lambda item: (-item[0], item[1].created_at, item[1].batch_id),
        )
        if not ranked:
            return None
        _, selected = ranked[0]
        cover.append(selected)
        remaining.difference_update(selected.unit_ids)
        candidates.remove(selected)
    return cover


def _is_cjk_character(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _continuation_separator(left: str, right: str) -> str:
    """Return a word separator only when a continued fragment needs one."""
    left_markup = html.unescape(left).rstrip()
    right_markup = html.unescape(right).lstrip()
    left_text = re.sub(r"<[^>]+>", "", left_markup).rstrip()
    right_text = re.sub(r"<[^>]+>", "", right_markup).lstrip()
    if not left_text or not right_text:
        return ""
    right_body = re.sub(r"^(?:<a\b[^>]*></a>)*", "", right_markup).lstrip()
    right_starts_code = right_text.startswith("`") or right_body.startswith("<code")
    left_ends_code = left_text.endswith("`") or left_markup.endswith("</code>")
    if (_is_cjk_character(left_text[-1]) and right_starts_code) or (
        left_ends_code and _is_cjk_character(right_text[0])
    ):
        return " "
    if left_text[-1] in "-‐‑" and right_text[0].isalnum():
        return ""
    if _is_cjk_character(left_text[-1]) or _is_cjk_character(right_text[0]):
        return ""
    return " "


def _continued_note_markdown(rendered: str, anchor: str) -> str:
    lines = rendered.splitlines()
    if lines and (lines[0].startswith("> [!") or lines[0].startswith("> **")):
        lines = lines[1:]
    if not lines:
        return f"> {anchor}"
    if lines[0].startswith("> "):
        lines[0] = f"> {anchor}{lines[0][2:]}"
    else:
        lines[0] = f"> {anchor}{lines[0]}"
    return "\n".join(lines)


def _reader_note_text(value: str) -> str:
    """Normalize note content because the renderer supplies its own label."""
    return re.sub(r"^\s*(?:读者注|译者注)\s*[:：]\s*", "", value, count=1).strip()


def _reader_note_markdown(reader_note: Any) -> list[str]:
    lines = [f"> **读者注：** {_reader_note_text(reader_note.text)}"]
    sources = "；".join(reader_note.sources)
    if sources and reader_note.accessed_at:
        lines.append(f"> 来源：{sources}（访问日期：{reader_note.accessed_at}）")
    elif sources:
        lines.append(f"> 来源：{sources}")
    elif reader_note.accessed_at:
        lines.append(f"> 访问日期：{reader_note.accessed_at}")
    return lines


def _merge_continued_note_html(left: str, right: str, anchor: str) -> str | None:
    if not left.endswith("</p></aside>"):
        return None
    match = re.fullmatch(
        r'<aside class="source-note"><strong>.*?</strong><p>(.*)</p></aside>',
        right,
        flags=re.S,
    )
    if not match:
        return None
    body = match.group(1)
    separator = _continuation_separator(left, body)
    return left.removesuffix("</p></aside>") + separator + anchor + body + "</p></aside>"


def _merge_continued_list_html(left: str, right: str, anchor: str) -> str | None:
    """Join physical fragments of one logical list item without adding a new marker."""
    left_match = re.fullmatch(
        r'(?P<open><(?P<tag>[uo]l)(?: start="\d+")?>)<li>(?P<body>.*)</li></(?P=tag)>',
        left,
        flags=re.S,
    )
    right_match = re.fullmatch(
        r'<[uo]l(?: start="\d+")?><li>(?P<body>.*)</li></[uo]l>',
        right,
        flags=re.S,
    )
    if not left_match or not right_match:
        return None
    right_body = right_match.group("body")
    separator = _continuation_separator(left_match.group("body"), right_body)
    tag = left_match.group("tag")
    return (
        left_match.group("open")
        + "<li>"
        + left_match.group("body")
        + separator
        + anchor
        + right_body
        + f"</li></{tag}>"
    )


def _merge_continued_sidebar_html(left: str, right: str, anchor: str) -> str | None:
    """Join paragraph fragments inside one sidebar without nesting another aside."""
    if not left.endswith("</p></aside>"):
        return None
    match = re.fullmatch(
        r'<aside class="sidebar-fragment sidebar-body"><p>(.*)</p></aside>',
        right,
        flags=re.S,
    )
    if match is None:
        return None
    body = match.group(1)
    separator = _continuation_separator(left, body)
    return left.removesuffix("</p></aside>") + separator + anchor + body + "</p></aside>"


def _continued_sidebar_markdown(rendered: str) -> str:
    """Remove the renderer-owned blockquote prefix from a continued fragment."""
    return rendered.removeprefix("> ")


PYGMENTS_LANGUAGE_ALIASES = {"xaml": "xml"}


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not cleaned:
        raise ValueError("Output name must include a letter or digit")
    return cleaned


def default_batch_output_name(batch_id: str) -> str:
    """Use the short batch key (`bNNN`) as the canonical single-batch output stem."""
    return _safe_name(batch_id.rsplit("-", 1)[-1])


def _require_default_output_owner(output: Path, output_name: str, batch_id: str) -> None:
    existing = list(output.glob(f"{output_name}.*"))
    if not existing:
        return
    render_qa_path = output / f"{output_name}.render-qa.json"
    try:
        payload = json.loads(render_qa_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ValueError(
            f"Default output name {output_name!r} is already in use without an "
            "ownership report; pass --name explicitly"
        ) from None
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Default output name {output_name!r} has an invalid ownership report; "
            "pass --name explicitly"
        ) from exc
    selection = payload.get("selection") if isinstance(payload, dict) else None
    owned_by_batch = (
        isinstance(selection, dict)
        and selection.get("batch_id") == batch_id
        and selection.get("batch_ids") in (None, [batch_id])
    )
    if not owned_by_batch:
        owner = selection.get("batch_id") if isinstance(selection, dict) else None
        raise ValueError(
            f"Default output name {output_name!r} is already owned by batch "
            f"{owner or 'unknown'}; pass --name explicitly"
        )


def _render_target_text(unit: SourceUnit, target: str | None) -> str | None:
    if target is not None and unit.kind is UnitKind.CAPTION:
        return normalize_zh_caption(target)
    return target


def _asset_markdown(unit: SourceUnit) -> str:
    return "\n".join(
        f"![{unit.kind} - PDF page {unit.page}](../{asset.path.replace('\\', '/')})"
        for asset in unit.asset_refs
    )


def _note_body(text: str) -> str:
    return re.sub(
        r"^(?:[■▪●]\s*)?(?:note|tip|warning|caution|what[’']s new|注意|提示|警告|新增内容)\s*[:：]?\s*",
        "",
        text,
        count=1,
        flags=re.I,
    )


def _note_variant(text: str, explicit: CalloutKind | None = None) -> str:
    if explicit is not None:
        return explicit.value
    match = re.match(
        r"^(?:[■▪●]\s*)?(note|tip|warning|caution|what[’']s new)\b",
        text,
        re.I,
    )
    if not match:
        return "note"
    return match.group(1).lower().replace("’", "'").replace("what's new", "whats-new")


def _list_body(text: str) -> str:
    return re.sub(r"^\s*(?:[-+*•▪■●]\s+|\d+[.)]\s+)", "", text, count=1)


def _list_ordinal(text: str) -> int | None:
    match = re.match(r"^\s*(\d+)[.)]\s+", text)
    return int(match.group(1)) if match else None


def _coalesce_code_units(
    units: list[SourceUnit],
) -> tuple[list[SourceUnit], dict[str, list[str]]]:
    rendered: list[SourceUnit] = []
    grouped_ids: dict[str, list[str]] = {}
    index = 0
    while index < len(units):
        first = units[index]
        group = [first]
        cursor = index + 1
        while (
            first.kind is UnitKind.CODE
            and group[-1].continued_to_next
            and cursor < len(units)
            and units[cursor].kind is UnitKind.CODE
            and units[cursor].continues_from_previous
            and units[cursor].code_language == first.code_language
        ):
            group.append(units[cursor])
            cursor += 1
        if len(group) == 1:
            rendered.append(first)
        else:
            combined = first.model_copy(
                update={
                    "source_text": "\n".join(part.source_text for part in group),
                    "continued_to_next": False,
                    "continues_from_previous": False,
                    "fragments": [fragment for part in group for fragment in part.fragments],
                    "asset_refs": [asset for part in group for asset in part.asset_refs],
                }
            )
            rendered.append(combined)
            grouped_ids[first.unit_id] = [part.unit_id for part in group]
        index = cursor
    return rendered, grouped_ids


def _merge_continued_table_data(tables: list[TableData]) -> TableData:
    """Join complementary half-rows without moving their stable-unit text."""
    if not tables:
        raise ValueError("At least one table is required")
    column_count = tables[0].column_count
    rows = [list(row) for row in tables[0].rows]
    for table in tables[1:]:
        if table.column_count != column_count:
            raise ValueError("Continued table fragments must have matching columns")
        incoming = [list(row) for row in table.rows]
        if rows and incoming:
            left = rows[-1]
            right = incoming[0]
            complementary = (
                any(not cell for cell in left)
                and any(not cell for cell in right)
                and all(not left[index] or not right[index] for index in range(column_count))
            )
            if complementary:
                rows[-1] = [left[index] or right[index] for index in range(column_count)]
                incoming = incoming[1:]
        rows.extend(incoming)
    return TableData(
        rows=rows,
        header_rows=tables[0].header_rows,
        column_count=column_count,
    )


def _coalesce_table_units(
    units: list[SourceUnit],
) -> tuple[list[SourceUnit], dict[str, list[str]]]:
    """Render explicitly continued table fragments as one logical table."""
    rendered: list[SourceUnit] = []
    grouped_ids: dict[str, list[str]] = {}
    index = 0
    while index < len(units):
        first = units[index]
        group = [first]
        cursor = index + 1
        while (
            first.kind is UnitKind.TABLE
            and first.table is not None
            and group[-1].continued_to_next
            and cursor < len(units)
            and units[cursor].kind is UnitKind.TABLE
            and units[cursor].table is not None
            and units[cursor].continues_from_previous
        ):
            group.append(units[cursor])
            cursor += 1
        if len(group) == 1:
            rendered.append(first)
        else:
            combined = first.model_copy(
                update={
                    "table": _merge_continued_table_data(
                        [part.table for part in group if part.table is not None]
                    ),
                    "continued_to_next": False,
                    "continues_from_previous": False,
                    "fragments": [fragment for part in group for fragment in part.fragments],
                    "asset_refs": [asset for part in group for asset in part.asset_refs],
                }
            )
            rendered.append(combined)
            grouped_ids[first.unit_id] = [part.unit_id for part in group]
        index = cursor
    return rendered, grouped_ids


def _target_markdown(unit: SourceUnit, target: str | None) -> str:
    text = target or unit.source_text
    safe_text = escape_markdown_prose(text)
    if unit.sidebar_role is SidebarRole.TITLE:
        return f"> **{safe_text}**"
    if unit.sidebar_role is SidebarRole.BODY:
        plain_unit = unit.model_copy(update={"sidebar_id": None, "sidebar_role": None})
        inner = _target_markdown(plain_unit, target)
        return "\n".join(">" if not line else f"> {line}" for line in inner.splitlines())
    if unit.kind is UnitKind.HEADING:
        return f"## {safe_text}"
    if unit.kind is UnitKind.LIST_ITEM:
        marker = f"{_list_ordinal(unit.source_text)}." if _list_ordinal(unit.source_text) else "-"
        return f"{marker} {escape_markdown_prose(_list_body(text))}"
    if unit.kind is UnitKind.NOTE:
        lines = escape_markdown_prose(_note_body(text)).splitlines() or [safe_text]
        variant = _note_variant(unit.source_text, unit.callout_kind)
        if variant == "whats-new":
            label = "新增内容" if target is not None else "What's New"
            return f"> **{label}**\n" + "\n".join(f"> {line}" for line in lines)
        marker = variant.upper()
        return f"> [!{marker}]\n" + "\n".join(f"> {line}" for line in lines)
    if unit.kind is UnitKind.CODE:
        return fenced_code(unit.source_text, unit.code_language)
    if unit.kind is UnitKind.EQUATION:
        return equation_markdown(unit)
    if unit.kind is UnitKind.FIGURE:
        asset = _asset_markdown(unit) or f"`[figure: PDF page {unit.page}]`"
        labels = [
            f"- {label.source if '$' in label.source else f'`{label.source}`'}：{label.target}"
            for label in unit.figure_labels
            if label.target
        ]
        return asset + ("\n\n**图中文字：**\n\n" + "\n".join(labels) if labels else "")
    if unit.kind is UnitKind.TABLE:
        return table_to_markdown(unit.table) if unit.table else safe_text
    if unit.kind is UnitKind.CAPTION:
        return f"*{safe_text}*"
    if unit.kind is UnitKind.FOOTNOTE:
        return f"> **脚注：** {safe_text}"
    return safe_text


INLINE_TOKEN_RE = re.compile(
    r"(?P<code>(?<!\\)(?P<fence>`+)(?P<code_text>.+?)(?P=fence))"
    r"|(?P<math>\$(?!\$)(?P<math_text>.+?)(?<!\\)\$)"
    r"|(?P<emphasis>(?<!\\)(?<!\*)\*(?!\*)(?P<emphasis_text>[^*\n]+?)(?<!\\)\*(?!\*))"
)


def _mathml(latex: str, display: str) -> str:
    try:
        return latex_to_mathml(latex, display=display)
    except Exception:
        return f'<code class="math-fallback">{html.escape(latex)}</code>'


def _inline_html(text: str) -> str:
    parts: list[str] = []
    position = 0
    for match in INLINE_TOKEN_RE.finditer(text):
        parts.append(html.escape(text[position : match.start()]).replace("\n", " "))
        if match.group("code") is not None:
            code_text = match.group("code_text").replace("\n", " ")
            if (
                len(code_text) >= 2
                and code_text.startswith(" ")
                and code_text.endswith(" ")
                and code_text.strip()
            ):
                code_text = code_text[1:-1]
            parts.append("<code>" + html.escape(code_text) + "</code>")
        elif match.group("math") is not None:
            parts.append(
                '<span class="math inline">'
                + _mathml(match.group("math_text"), "inline")
                + "</span>"
            )
        else:
            # Emphasis may legitimately contain inline code or math. Parse its
            # body through the same safe inline renderer so Markdown such as
            # ``*set the `Opacity` property*`` does not leak raw backticks into
            # bilingual HTML.
            parts.append("<em>" + _inline_html(match.group("emphasis_text")) + "</em>")
        position = match.end()
    parts.append(html.escape(text[position:]).replace("\n", " "))
    return "".join(parts)


def _unit_html(
    unit: SourceUnit,
    target: str | None,
    target_table: Any = None,
    *,
    source_view: bool,
) -> str:
    text = target if target is not None else (unit.source_markdown or unit.source_text)
    if unit.sidebar_role is SidebarRole.TITLE:
        return '<aside class="sidebar-fragment sidebar-title"><h3>' + _inline_html(text) + "</h3></aside>"
    if unit.sidebar_role is SidebarRole.BODY:
        plain_unit = unit.model_copy(update={"sidebar_id": None, "sidebar_role": None})
        return '<aside class="sidebar-fragment sidebar-body">' + _unit_html(
            plain_unit, target, target_table, source_view=source_view
        ) + "</aside>"
    if unit.kind is UnitKind.CODE:
        language = html.escape(unit.code_language or "text")
        try:
            lexer = get_lexer_by_name(
                PYGMENTS_LANGUAGE_ALIASES.get(
                    unit.code_language or "text", unit.code_language or "text"
                )
            )
            highlighted = highlight(
                unit.source_text, lexer, HtmlFormatter(nowrap=True)
            ).rstrip("\n")
        except ClassNotFound:
            highlighted = html.escape(unit.source_text)
        return f'<pre><code class="language-{language}">{highlighted}</code></pre>'
    if unit.kind is UnitKind.EQUATION:
        number = (
            f'<span class="equation-number">({html.escape(unit.equation_number)})</span>'
            if unit.equation_number
            else ""
        )
        return '<div class="math display">' + _mathml(
            unit.latex or unit.source_text, "block"
        ) + number + "</div>"
    if unit.kind is UnitKind.TABLE:
        table = target_table or unit.table
        return table_to_html(table, _inline_html) if table else _inline_html(text)
    if unit.kind is UnitKind.NOTE:
        variant = _note_variant(unit.source_text, unit.callout_kind)
        source_labels = {
            "note": "Note",
            "tip": "Tip",
            "warning": "Warning",
            "caution": "Caution",
            "whats-new": "What's New",
        }
        target_labels = {
            "note": "注意",
            "tip": "提示",
            "warning": "警告",
            "caution": "注意",
            "whats-new": "新增内容",
        }
        label = source_labels[variant] if source_view else target_labels[variant]
        return (
            f'<aside class="source-note"><strong>{label}</strong><p>'
            + _inline_html(_note_body(text))
            + "</p></aside>"
        )
    if unit.kind is UnitKind.HEADING:
        return "<h2>" + _inline_html(text) + "</h2>"
    if unit.kind is UnitKind.LIST_ITEM:
        ordinal = _list_ordinal(unit.source_text)
        body = _inline_html(_list_body(text))
        if ordinal is not None:
            return f'<ol start="{ordinal}"><li>{body}</li></ol>'
        return "<ul><li>" + body + "</li></ul>"
    if unit.kind is UnitKind.CAPTION:
        return "<figcaption>" + _inline_html(text) + "</figcaption>"
    if unit.kind is UnitKind.FOOTNOTE:
        return '<aside class="footnote">' + _inline_html(text) + "</aside>"
    if unit.kind is UnitKind.FIGURE and unit.figure_labels:
        labels = "".join(
            f"<li>{_inline_html(label.source if source_view else (label.target or label.source))}</li>"
            for label in unit.figure_labels
        )
        return "<ul class=figure-labels>" + labels + "</ul>"
    return "<p>" + _inline_html(text) + "</p>"


def render_project(
    root: Path,
    page_spec: str | None,
    name: str | None = None,
    allow_draft: bool = False,
    batch_id: str | None = None,
    batch_ids: list[str] | None = None,
) -> dict[str, str]:
    config = load_project(root)
    publishable = (
        {ProjectStatus.EXTERNAL_REVIEWED, ProjectStatus.HUMAN_APPROVED}
        if config.external_review and config.external_review.enabled
        else LEGACY_PUBLISHABLE
    )
    selectors = sum(value is not None for value in (page_spec, batch_id, batch_ids))
    if selectors != 1:
        raise ValueError("Specify exactly one of page_spec, batch_id, or batch_ids")
    if name is not None:
        output_name = _safe_name(name)
    elif batch_id:
        output_name = default_batch_output_name(batch_id)
    else:
        raise ValueError("Specify name unless rendering a single batch_id")
    if batch_ids is not None and (
        not batch_ids or len(batch_ids) > WAVE_BATCH_SET_MAX
    ):
        raise ValueError(
            f"batch_ids must contain 1 to {WAVE_BATCH_SET_MAX} exact batch IDs"
        )
    if batch_ids is not None:
        from littrans.workflow import _validate_batch_set

        _validate_batch_set(root, batch_ids)
    selected_batch_ids = batch_ids or ([batch_id] if batch_id else [])
    manifests = [load_manifest(root, value) for value in selected_batch_ids]
    pages = (
        {page for manifest in manifests for page in manifest.pages}
        if manifests
        else set(parse_page_spec(page_spec or "", config.source_pages))
    )
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    if manifests:
        unit_map = {unit.unit_id: unit for unit in all_units}
        selected_manifest_unit_ids = [
            unit_id for manifest in manifests for unit_id in manifest.unit_ids
        ]
        if len(selected_manifest_unit_ids) != len(set(selected_manifest_unit_ids)):
            raise ValueError("Selected batches contain overlapping source units")
        positions = {unit.unit_id: index for index, unit in enumerate(all_units)}
        first_positions = [
            min(positions.get(unit_id, 10**12) for unit_id in manifest.unit_ids)
            for manifest in manifests
        ]
        if first_positions != sorted(first_positions):
            raise ValueError("batch_ids must follow source order")
        missing_manifest_units = [
            unit_id for unit_id in selected_manifest_unit_ids if unit_id not in unit_map
        ]
        if missing_manifest_units:
            raise ValueError(f"Batch references missing source units: {missing_manifest_units}")
        selected_set = set(
            dependency_closure(
                root,
                [manifest.batch_id for manifest in manifests],
                selected_manifest_unit_ids,
                all_units=all_units,
            )
        )
        units = [unit for unit in all_units if unit.unit_id in selected_set]
    else:
        units = [unit for unit in all_units if unit.page in pages]
    units = [unit for unit in units if unit.render_policy is RenderPolicy.INCLUDE]
    pages |= {unit.page for unit in units}
    if not allow_draft:
        require_verified_extraction(root, pages)
    render_units, grouped_code_ids = _coalesce_code_units(units)
    render_units, grouped_table_ids = _coalesce_table_units(render_units)
    grouped_unit_ids = {**grouped_code_ids, **grouped_table_ids}
    translations = translation_map(root)
    selected_ids = {unit.unit_id for unit in units}
    content_manifests = manifests
    if manifests:
        covered_by_selected_manifests = {
            unit_id for manifest in manifests for unit_id in manifest.unit_ids
        }
        if selected_ids - covered_by_selected_manifests:
            dependency_manifests = [
                manifest
                for path in (root / "batches").iterdir()
                if path.is_dir()
                and (path / "manifest.yaml").is_file()
                for manifest in [load_manifest(root, path.name)]
                if selected_ids & set(manifest.unit_ids)
            ]
            content_manifests = (
                _manifest_cover(selected_ids, dependency_manifests)
                or dependency_manifests
            )
    missing = [
        unit.unit_id for unit in units if unit.translatable and unit.unit_id not in translations
    ]
    unapproved = [
        unit.unit_id
        for unit in units
        if unit.translatable
        and unit.unit_id in translations
        and translations[unit.unit_id].status not in publishable
    ]
    stale_qa: list[str] = []
    incomplete_audit: list[str] = []
    stale_external: list[str] = []
    unbatched_units: list[str] = []
    if not allow_draft:
        relevant_manifests = content_manifests
        gate_status: dict[str, tuple[bool, bool, bool]] = {}
        if not relevant_manifests:
            candidate_manifests = [
                manifest
                for path in (root / "batches").iterdir()
                if path.is_dir()
                and (path / "manifest.yaml").is_file()
                for manifest in [load_manifest(root, path.name)]
                if selected_ids & set(manifest.unit_ids)
            ]
        current_unit_ids = {unit.unit_id for unit in all_units}
        if not relevant_manifests:
            if config.external_review and config.external_review.enabled:
                from littrans.external_review import external_review_status

            for manifest in candidate_manifests:
                has_removed_units = any(
                    unit_id not in current_unit_ids for unit_id in manifest.unit_ids
                )
                qa_current = not has_removed_units and qa_report_is_current(
                    root, manifest.batch_id
                )
                audit_complete = bool(
                    not has_removed_units
                    and audit_coverage(root, manifest.batch_id)["complete"]
                )
                external_current = bool(
                    not has_removed_units
                    and (
                        not config.external_review
                        or not config.external_review.enabled
                        or external_review_status(root, manifest.batch_id)[
                            "external_approvable"
                        ]
                    )
                )
                gate_status[manifest.batch_id] = (
                    qa_current,
                    audit_complete,
                    external_current,
                )
            eligible_manifests = [
                manifest
                for manifest in candidate_manifests
                if all(gate_status[manifest.batch_id])
            ]
            relevant_manifests = (
                _manifest_cover(selected_ids, eligible_manifests)
                or candidate_manifests
            )
        removed_manifest_units = {
            manifest.batch_id: [
                unit_id
                for unit_id in manifest.unit_ids
                if unit_id not in current_unit_ids
            ]
            for manifest in relevant_manifests
            if any(
                unit_id not in current_unit_ids
                for unit_id in manifest.unit_ids
            )
        }
        if removed_manifest_units:
            raise ValueError(
                "Formal rendering is blocked because manifests reference removed "
                "source units; recreate the affected batches: "
                f"removed_units={removed_manifest_units}"
            )
        covered_manifest_units = {
            unit_id
            for manifest in relevant_manifests
            for unit_id in manifest.unit_ids
        }
        unbatched_units = sorted(selected_ids - covered_manifest_units)
        stale_qa = [
            manifest.batch_id
            for manifest in relevant_manifests
            if not (
                gate_status[manifest.batch_id][0]
                if manifest.batch_id in gate_status
                else qa_report_is_current(root, manifest.batch_id)
            )
        ]
        incomplete_audit = [
            manifest.batch_id
            for manifest in relevant_manifests
            if not (
                gate_status[manifest.batch_id][1]
                if manifest.batch_id in gate_status
                else audit_coverage(root, manifest.batch_id)["complete"]
            )
        ]
        if config.external_review and config.external_review.enabled:
            from littrans.external_review import external_review_status

            stale_external = [
                manifest.batch_id
                for manifest in relevant_manifests
                if not (
                    gate_status[manifest.batch_id][2]
                    if manifest.batch_id in gate_status
                    else external_review_status(root, manifest.batch_id)[
                        "external_approvable"
                    ]
                )
            ]
    open_severe: list[str] = []
    for issue_path in (root / "reviews").glob("*.issues.jsonl"):
        for issue in read_jsonl(issue_path, ReviewIssue):
            if (
                issue.unit_id in selected_ids
                and issue.status is IssueStatus.OPEN
                and issue.severity in {Severity.BLOCKER, Severity.MAJOR}
            ):
                open_severe.append(issue.issue_id)
    if not allow_draft and (
        missing
        or unapproved
        or unbatched_units
        or stale_qa
        or incomplete_audit
        or stale_external
        or open_severe
    ):
        raise ValueError(
            "Formal rendering is blocked; "
            f"missing={missing}, not_publishable={unapproved}, "
            f"unbatched_units={unbatched_units}, stale_qa={stale_qa}, "
            f"incomplete_audit={incomplete_audit}, "
            f"stale_external={stale_external}, "
            f"open_severe={open_severe}"
        )

    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    markdown_path = output / f"{output_name}.zh.md"
    html_path = output / f"{output_name}.bilingual.html"
    qa_path = output / f"{output_name}.quality.md"
    unresolved_path = output / f"{output_name}.unresolved.md"
    render_qa_path = output / f"{output_name}.render-qa.json"

    markdown: list[str] = [
        f"# {config.title}",
        "",
        f"> 翻译状态：{config.status}；用途：{config.rights_status}。",
        "",
    ]
    rows: list[dict[str, Any]] = []
    pending_markdown_reader_notes: list[Any] = []
    previous_page: int | None = None
    previous_unit: SourceUnit | None = None
    for unit_index, unit in enumerate(render_units):
        unit_last_page = max((fragment.page for fragment in unit.fragments), default=unit.page)
        if previous_page is not None and unit.page - previous_page > 1:
            markdown.extend(
                [
                    "---",
                    "",
                    f"> 选定试译范围在此从 PDF 第 {previous_page} 页跳至第 {unit.page} 页。",
                    "",
                ]
            )
        record = translations.get(unit.unit_id)
        target = _render_target_text(unit, record.target_text if record else None)
        if target is not None:
            bilingual_target = target
        elif not unit.translatable and unit.source_text:
            bilingual_target = unit.source_text
        elif not unit.translatable and unit.asset_refs:
            bilingual_target = "[保留原始公式或图像]"
        else:
            bilingual_target = "[尚未翻译]"
        render_unit = unit
        target_table = record.target_table if record else None
        reader_notes = [record.reader_note] if record and record.reader_note else []
        if unit.unit_id in grouped_table_ids:
            table_records = [translations.get(unit_id) for unit_id in grouped_table_ids[unit.unit_id]]
            if all(item and item.target_table for item in table_records):
                target_table = _merge_continued_table_data(
                    [item.target_table for item in table_records if item and item.target_table]
                )
            reader_notes = [
                item.reader_note for item in table_records if item and item.reader_note
            ]
        if unit.kind is UnitKind.TABLE and target_table:
            render_unit = unit.model_copy(update={"table": target_table})
        rendered_figure_labels = effective_figure_labels(unit, record)
        if rendered_figure_labels:
            render_unit = unit.model_copy(
                update={"figure_labels": rendered_figure_labels}
            )
        rendered = _target_markdown(render_unit, target)
        anchor = "".join(
            f'<a id="{unit_id}"></a>'
            for unit_id in grouped_unit_ids.get(unit.unit_id, [unit.unit_id])
        )
        if (
            unit.continues_from_previous
            and previous_unit is not None
            and previous_unit.kind is UnitKind.NOTE
            and unit.kind is UnitKind.NOTE
            and markdown
        ):
            while markdown and markdown[-1] == "":
                markdown.pop()
            markdown[-1] += "\n" + _continued_note_markdown(rendered, anchor)
            markdown.append("")
        elif (
            unit.continues_from_previous
            and previous_unit is not None
            and previous_unit.kind is UnitKind.LIST_ITEM
            and unit.kind is UnitKind.LIST_ITEM
            and markdown
        ):
            while markdown and markdown[-1] == "":
                markdown.pop()
            continued_body = _list_body(rendered)
            separator = _continuation_separator(markdown[-1], continued_body)
            markdown[-1] += f"{separator}{anchor}{continued_body}"
            markdown.append("")
        elif (
            unit.continues_from_previous
            and previous_unit is not None
            and previous_unit.kind is UnitKind.PARAGRAPH
            and unit.kind is UnitKind.PARAGRAPH
            and previous_unit.sidebar_role is SidebarRole.BODY
            and unit.sidebar_role is SidebarRole.BODY
            and markdown
        ):
            while markdown and markdown[-1] == "":
                markdown.pop()
            continued_body = _continued_sidebar_markdown(rendered)
            separator = _continuation_separator(markdown[-1], continued_body)
            markdown[-1] += f"{separator}{anchor}{continued_body}"
            markdown.append("")
        elif (
            unit.continues_from_previous
            and previous_unit is not None
            and previous_unit.kind is UnitKind.PARAGRAPH
            and markdown
        ):
            while markdown and markdown[-1] == "":
                markdown.pop()
            separator = _continuation_separator(markdown[-1], rendered)
            markdown[-1] += f"{separator}{anchor}{rendered}"
            markdown.append("")
        else:
            markdown.extend([anchor, rendered, ""])
        pending_markdown_reader_notes.extend(reader_notes)
        # A reader note attached to any fragment of a continued paragraph or
        # callout belongs after the complete logical unit.  Emitting it here
        # would interrupt the sentence at a physical page boundary.
        next_unit = (
            render_units[unit_index + 1]
            if unit_index + 1 < len(render_units)
            else None
        )
        next_continues_this_unit = bool(
            next_unit
            and next_unit.continues_from_previous
            and next_unit.kind is unit.kind
            and unit.kind in {UnitKind.PARAGRAPH, UnitKind.NOTE, UnitKind.LIST_ITEM}
        )
        if not unit.continued_to_next and not next_continues_this_unit:
            for reader_note in pending_markdown_reader_notes:
                markdown.extend([*_reader_note_markdown(reader_note), ""])
            pending_markdown_reader_notes.clear()
        assets = (
            [f"../{asset.path.replace('\\', '/')}" for asset in unit.asset_refs]
            if unit.kind is UnitKind.FIGURE
            else []
        )
        source_html = _unit_html(
            unit,
            unit.source_markdown or unit.source_text,
            source_view=True,
        )
        target_html = _unit_html(
            render_unit,
            bilingual_target,
            target_table,
            source_view=False,
        )
        if unit.unit_id in grouped_unit_ids:
            extra_anchors = "".join(
                f'<span id="{html.escape(unit_id)}"></span>'
                for unit_id in grouped_unit_ids[unit.unit_id][1:]
            )
            source_html = extra_anchors + source_html
        if (
            unit.continues_from_previous
            and rows
            and rows[-1]["unit"].kind is UnitKind.NOTE
            and unit.kind is UnitKind.NOTE
        ):
            source_note = _merge_continued_note_html(
                rows[-1]["source_html"],
                source_html,
                f'<a id="{html.escape(unit.unit_id)}"></a>',
            )
            target_note = _merge_continued_note_html(
                rows[-1]["target_html"],
                target_html,
                f'<a href="#{html.escape(unit.unit_id)}" aria-label="continued unit"></a>',
            )
            if source_note is not None and target_note is not None:
                rows[-1]["source_html"] = source_note
                rows[-1]["target_html"] = target_note
                rows[-1]["reader_notes"].extend(reader_notes)
                rows[-1]["last_page"] = unit_last_page
            else:
                rows.append(
                    {
                        "unit": unit,
                        "last_page": unit_last_page,
                        "source_html": source_html,
                        "target_html": target_html,
                        "assets": assets,
                        "record": record,
                        "reader_notes": list(reader_notes),
                    }
                )
        elif (
            unit.continues_from_previous
            and rows
            and rows[-1]["unit"].kind is UnitKind.LIST_ITEM
            and unit.kind is UnitKind.LIST_ITEM
        ):
            source_list = _merge_continued_list_html(
                rows[-1]["source_html"],
                source_html,
                f'<a id="{html.escape(unit.unit_id)}"></a>',
            )
            target_list = _merge_continued_list_html(
                rows[-1]["target_html"],
                target_html,
                f'<a href="#{html.escape(unit.unit_id)}" aria-label="continued unit"></a>',
            )
            if source_list is not None and target_list is not None:
                rows[-1]["source_html"] = source_list
                rows[-1]["target_html"] = target_list
                rows[-1]["reader_notes"].extend(reader_notes)
                rows[-1]["last_page"] = unit_last_page
            else:
                rows.append(
                    {
                        "unit": unit,
                        "last_page": unit_last_page,
                        "source_html": source_html,
                        "target_html": target_html,
                        "assets": assets,
                        "record": record,
                        "reader_notes": list(reader_notes),
                    }
                )
        elif (
            unit.continues_from_previous
            and rows
            and rows[-1]["unit"].kind is UnitKind.PARAGRAPH
            and unit.kind is UnitKind.PARAGRAPH
            and rows[-1]["unit"].sidebar_role is SidebarRole.BODY
            and unit.sidebar_role is SidebarRole.BODY
        ):
            source_sidebar = _merge_continued_sidebar_html(
                rows[-1]["source_html"],
                source_html,
                f'<a id="{html.escape(unit.unit_id)}"></a>',
            )
            target_sidebar = _merge_continued_sidebar_html(
                rows[-1]["target_html"],
                target_html,
                f'<a href="#{html.escape(unit.unit_id)}" aria-label="continued unit"></a>',
            )
            if source_sidebar is not None and target_sidebar is not None:
                rows[-1]["source_html"] = source_sidebar
                rows[-1]["target_html"] = target_sidebar
                rows[-1]["reader_notes"].extend(reader_notes)
                rows[-1]["last_page"] = unit_last_page
            else:
                rows.append(
                    {
                        "unit": unit,
                        "last_page": unit_last_page,
                        "source_html": source_html,
                        "target_html": target_html,
                        "assets": assets,
                        "record": record,
                        "reader_notes": list(reader_notes),
                    }
                )
        elif (
            unit.continues_from_previous
            and rows
            and rows[-1]["unit"].kind is UnitKind.PARAGRAPH
            and source_html.startswith("<p>")
            and rows[-1]["source_html"].endswith("</p>")
        ):
            inline_source = source_html.removeprefix("<p>").removesuffix("</p>")
            inline_target = target_html.removeprefix("<p>").removesuffix("</p>")
            source_separator = _continuation_separator(
                rows[-1]["source_html"], inline_source
            )
            target_separator = _continuation_separator(
                rows[-1]["target_html"], inline_target
            )
            rows[-1]["source_html"] = (
                rows[-1]["source_html"].removesuffix("</p>")
                + f'{source_separator}<a id="{html.escape(unit.unit_id)}"></a>{inline_source}</p>'
            )
            rows[-1]["target_html"] = (
                rows[-1]["target_html"].removesuffix("</p>")
                + f'{target_separator}<a href="#{html.escape(unit.unit_id)}" aria-label="continued unit"></a>{inline_target}</p>'
            )
            rows[-1]["reader_notes"].extend(reader_notes)
            rows[-1]["last_page"] = unit_last_page
        else:
            rows.append(
                {
                    "unit": unit,
                    "last_page": unit_last_page,
                    "source_html": source_html,
                    "target_html": target_html,
                    "assets": assets,
                    "record": record,
                    "reader_notes": list(reader_notes),
                }
            )
        previous_page = unit_last_page
        previous_unit = unit
    for reader_note in pending_markdown_reader_notes:
        markdown.extend([*_reader_note_markdown(reader_note), ""])
    for index, row in enumerate(rows):
        sidebar_id = row["unit"].sidebar_id
        row["sidebar_start"] = bool(
            sidebar_id and (index == 0 or rows[index - 1]["unit"].sidebar_id != sidebar_id)
        )
        row["sidebar_end"] = bool(
            sidebar_id
            and (index == len(rows) - 1 or rows[index + 1]["unit"].sidebar_id != sidebar_id)
        )
    markdown_text = "\n".join(markdown).rstrip() + "\n"

    environment = Environment(
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    environment.filters["inline_html"] = _inline_html
    environment.filters["reader_note_text"] = _reader_note_text
    template_text = (
        files("littrans").joinpath("templates", "bilingual.html.j2").read_text(encoding="utf-8")
    )
    template = environment.from_string(template_text)
    html_text = template.render(
        config=config,
        rows=rows,
        pages=f"{min(pages)}–{max(pages)}",
        pdf_uri=config.source(root).as_uri(),
        allow_draft=allow_draft,
    )
    render_errors = _render_quality_errors(markdown_text, html_text, units)
    with project_write_lock(root):
        if name is None and batch_id is not None:
            _require_default_output_owner(output, output_name, batch_id)
        atomic_write_text(markdown_path, markdown_text)
        atomic_write_text(html_path, html_text)
        _write_quality_summary(qa_path, root, units, missing, unapproved, open_severe)
        _write_unresolved(unresolved_path, root, selected_ids)
        atomic_write_text(
            render_qa_path,
            json.dumps(
                {
                    "passed": not render_errors,
                    "selection": {
                        "batch_id": batch_id,
                        "batch_ids": selected_batch_ids or None,
                        "pages": sorted(pages),
                    },
                    "unit_ids": [unit.unit_id for unit in units],
                    "errors": render_errors,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )
        if render_errors:
            raise ValueError(f"Rendered output failed structural QA: {render_errors}")
        outputs = {
            "markdown": str(markdown_path),
            "html": str(html_path),
            "quality": str(qa_path),
            "unresolved": str(unresolved_path),
            "render_qa": str(render_qa_path),
        }
        review_batch_ids = [manifest.batch_id for manifest in content_manifests]
        if config.external_review and config.external_review.enabled and review_batch_ids:
            external_path = output / f"{output_name}.external-review.md"
            _write_external_review_summary_set(external_path, root, review_batch_ids)
            outputs["external_review"] = str(external_path)
    return outputs


def _write_external_review_summary(path: Path, root: Path, batch_id: str) -> None:
    from littrans.external_review import external_review_status

    status = external_review_status(root, batch_id)
    lines = [
        f"# External review: {batch_id}",
        "",
        f"- Verdict: **{status['verdict']}**",
        f"- Translation fingerprint: `{status['translation_fingerprint']}`",
        f"- External approval gate: {'PASS' if status['external_approvable'] else 'FAIL'}",
        "",
    ]
    for heading, key in (("Primary review", "primary"), ("Second opinion", "second_opinion")):
        run = status[key]
        lines.extend([f"## {heading}", ""])
        if run is None:
            lines.extend(["Not required or not run.", ""])
            continue
        lines.extend(
            [
                f"- Reviewer: `{run['reviewer_id']}`",
                f"- Driver: `{run['driver']}`",
                f"- Requested model: `{run['requested_model']}`",
                f"- Actual model: `{run['actual_model_label'] or run['actual_model'] or 'unverified'}`",
                f"- Model verified: `{str(run['model_verified']).lower()}`",
                f"- CLI version: `{run['cli_version'] or 'unknown'}`",
                f"- Prompt version: `{run['prompt_version']}`",
                f"- Verdict: **{run['verdict']}**",
                "",
                run["summary"],
                "",
            ]
        )
    lines.extend(["## Open substantive issues", ""])
    lines.extend(
        [f"- `{issue_id}`" for issue_id in status["open_substantive_issues"]]
        or ["None."]
    )
    atomic_write_text(path, "\n".join(lines) + "\n")


def _write_external_review_summary_set(
    path: Path, root: Path, batch_ids: list[str]
) -> None:
    if len(batch_ids) == 1:
        _write_external_review_summary(path, root, batch_ids[0])
        return
    from littrans.external_review import external_review_status

    lines = ["# External review set", ""]
    for batch_id in batch_ids:
        status = external_review_status(root, batch_id)
        lines.extend(
            [
                f"## {batch_id}",
                "",
                f"- Verdict: **{status['verdict']}**",
                f"- External approval gate: {'PASS' if status['external_approvable'] else 'FAIL'}",
                f"- Translation fingerprint: `{status['translation_fingerprint']}`",
                "",
            ]
        )
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def _render_quality_errors(
    markdown: str, rendered_html: str, units: list[SourceUnit]
) -> list[str]:
    errors: list[str] = []
    if re.search(r"(?m)^-\s+[•▪■●]\s+", markdown):
        errors.append("duplicated-list-marker")
    # Match a genuinely nested blockquote marker on one physical line.
    # Using \s here also consumes newlines and falsely flags the valid blank
    # quote line emitted inside a sidebar code fence.
    if re.search(r"(?m)^>[ \t]+>[ \t]+", markdown):
        errors.append("nested-admonition-marker")
    for unit in units:
        anchor = f'<a id="{unit.unit_id}"></a>'
        if markdown.count(anchor) != 1:
            errors.append(f"unit-anchor-count:{unit.unit_id}:{markdown.count(anchor)}")
    html_ids = re.findall(r'(?<![A-Za-z0-9_-])id="([^"]+)"', rendered_html)
    for html_id in sorted(set(html_ids)):
        count = html_ids.count(html_id)
        if count > 1:
            errors.append(f"duplicate-html-id:{html_id}:{count}")
    return errors


def _write_quality_summary(
    path: Path,
    root: Path,
    units: list[SourceUnit],
    missing: list[str],
    unapproved: list[str],
    open_severe: list[str],
) -> None:
    translations = translation_map(root)
    translated = sum(unit.unit_id in translations for unit in units if unit.translatable)
    translatable = sum(unit.translatable for unit in units)
    qa_reports = [
        json.loads(item.read_text(encoding="utf-8")) for item in (root / "qa").glob("*.json")
    ]
    review_issues = [
        issue
        for issue_path in (root / "reviews").glob("*.issues.jsonl")
        for issue in read_jsonl(issue_path, ReviewIssue)
        if issue.unit_id in {unit.unit_id for unit in units}
    ]
    lines = [
        "# Translation quality summary",
        "",
        f"- Source units: {len(units)}",
        f"- Translatable units: {translatable}",
        f"- Translated units: {translated}",
        f"- Coverage: {(translated / translatable * 100) if translatable else 100:.1f}%",
        f"- Missing translations: {len(missing)}",
        f"- Below configured release gate: {len(unapproved)}",
        f"- Open blocker/major issues: {len(open_severe)}",
        f"- QA reports: {len(qa_reports)} ({sum(bool(report.get('passed')) for report in qa_reports)} passing)",
        f"- QA errors/warnings: {sum(len(report.get('errors', [])) for report in qa_reports)}/{sum(len(report.get('warnings', [])) for report in qa_reports)}",
        f"- Review issues: {len(review_issues)} ({sum(issue.status is IssueStatus.OPEN for issue in review_issues)} open)",
        "",
    ]
    atomic_write_text(path, "\n".join(lines))


def _write_unresolved(path: Path, root: Path, selected_ids: set[str]) -> None:
    candidate_terms = load_terms(root, "candidates.yaml")
    translations = translation_map(root)
    issues: list[ReviewIssue] = []
    for issue_path in (root / "reviews").glob("*.issues.jsonl"):
        issues.extend(
            issue
            for issue in read_jsonl(issue_path, ReviewIssue)
            if issue.unit_id in selected_ids and issue.status is IssueStatus.OPEN
        )
    lines = ["# Unresolved translation decisions", "", "## Candidate terms", ""]
    lines.extend(
        f"- `{term.get('source', '')}` → {term.get('target', '') or '[undecided]'}"
        for term in candidate_terms
    )
    if not candidate_terms:
        lines.append("None.")
    lines.extend(["", "## Translator uncertainties", ""])
    uncertainty_lines = [
        f"- `{unit_id}`: {uncertainty}"
        for unit_id, record in translations.items()
        if unit_id in selected_ids
        for uncertainty in record.uncertainties
    ]
    lines.extend(uncertainty_lines or ["None."])
    lines.extend(["", "## Open review issues", ""])
    lines.extend(
        f"- `{issue.issue_id}` ({issue.severity}, {issue.unit_id}): {issue.explanation}"
        for issue in issues
    )
    if not issues:
        lines.append("None.")
    atomic_write_text(path, "\n".join(lines) + "\n")
