from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from latex2mathml.converter import convert as latex_to_mathml
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound

from littrans.batching import load_manifest
from littrans.extractor import parse_page_spec
from littrans.models import (
    IssueStatus,
    ProjectStatus,
    RenderPolicy,
    ReviewIssue,
    Severity,
    SidebarRole,
    SourceUnit,
    UnitKind,
)
from littrans.project import load_terms, translation_map
from littrans.semantics import (
    escape_markdown_prose,
    fenced_code,
    table_to_html,
    table_to_markdown,
)
from littrans.storage import load_project, plugin_root, read_jsonl
from littrans.verification import require_verified_extraction

LEGACY_PUBLISHABLE = {
    ProjectStatus.MACHINE_REVIEWED,
    ProjectStatus.EXTERNAL_REVIEWED,
    ProjectStatus.HUMAN_APPROVED,
}
PYGMENTS_LANGUAGE_ALIASES = {"xaml": "xml"}


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    if not cleaned:
        raise ValueError("Output name must include a letter or digit")
    return cleaned


def _asset_markdown(unit: SourceUnit) -> str:
    return "\n".join(
        f"![{unit.kind} - PDF page {unit.page}](../{asset.path.replace('\\', '/')})"
        for asset in unit.asset_refs
    )


def _note_body(text: str) -> str:
    return re.sub(
        r"^(?:[■▪●]\s*)?(?:note|tip|warning|caution|注意|提示|警告)\s*[:：]?\s*",
        "",
        text,
        count=1,
        flags=re.I,
    )


def _note_variant(text: str) -> str:
    match = re.match(r"^(?:[■▪●]\s*)?(note|tip|warning|caution)\b", text, re.I)
    return match.group(1).lower() if match else "note"


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


def _target_markdown(unit: SourceUnit, target: str | None) -> str:
    text = target or unit.source_text
    safe_text = escape_markdown_prose(text)
    if unit.sidebar_role is SidebarRole.TITLE:
        return f"> [!NOTE]\n> **{safe_text}**"
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
        marker = _note_variant(unit.source_text).upper()
        return f"> [!{marker}]\n" + "\n".join(f"> {line}" for line in lines)
    if unit.kind is UnitKind.CODE:
        return fenced_code(unit.source_text, unit.code_language)
    if unit.kind is UnitKind.EQUATION:
        number = f" \\tag{{{unit.equation_number}}}" if unit.equation_number else ""
        return f"$$\n{unit.latex or unit.source_text}{number}\n$$"
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


INLINE_MATH_RE = re.compile(r"\$(?!\$)(.+?)(?<!\\)\$")


def _mathml(latex: str, display: str) -> str:
    try:
        return latex_to_mathml(latex, display=display)
    except Exception:
        return f'<code class="math-fallback">{html.escape(latex)}</code>'


def _inline_html(text: str) -> str:
    parts: list[str] = []
    position = 0
    for match in INLINE_MATH_RE.finditer(text):
        parts.append(html.escape(text[position : match.start()]).replace("\n", " "))
        parts.append('<span class="math inline">' + _mathml(match.group(1), "inline") + "</span>")
        position = match.end()
    parts.append(html.escape(text[position:]).replace("\n", " "))
    return "".join(parts)


def _unit_html(unit: SourceUnit, target: str | None, target_table: Any = None) -> str:
    text = target if target is not None else (unit.source_markdown or unit.source_text)
    if unit.sidebar_role is SidebarRole.TITLE:
        return '<aside class="sidebar-fragment sidebar-title"><h3>' + _inline_html(text) + "</h3></aside>"
    if unit.sidebar_role is SidebarRole.BODY:
        plain_unit = unit.model_copy(update={"sidebar_id": None, "sidebar_role": None})
        return '<aside class="sidebar-fragment sidebar-body">' + _unit_html(
            plain_unit, target, target_table
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
        source_view = target == (unit.source_markdown or unit.source_text)
        variant = _note_variant(unit.source_text)
        source_labels = {
            "note": "Note",
            "tip": "Tip",
            "warning": "Warning",
            "caution": "Caution",
        }
        target_labels = {
            "note": "注意",
            "tip": "提示",
            "warning": "警告",
            "caution": "注意",
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
        source_view = target == (unit.source_markdown or unit.source_text)
        labels = "".join(
            f"<li>{_inline_html(label.source if source_view else (label.target or label.source))}</li>"
            for label in unit.figure_labels
        )
        return "<ul class=figure-labels>" + labels + "</ul>"
    return "<p>" + _inline_html(text) + "</p>"


def render_project(
    root: Path,
    page_spec: str | None,
    name: str,
    allow_draft: bool = False,
    batch_id: str | None = None,
) -> dict[str, str]:
    config = load_project(root)
    publishable = (
        {ProjectStatus.EXTERNAL_REVIEWED, ProjectStatus.HUMAN_APPROVED}
        if config.external_review and config.external_review.enabled
        else LEGACY_PUBLISHABLE
    )
    if (page_spec is None) == (batch_id is None):
        raise ValueError("Specify exactly one of page_spec or batch_id")
    manifest = load_manifest(root, batch_id) if batch_id else None
    pages = (
        set(manifest.pages)
        if manifest
        else set(parse_page_spec(page_spec or "", config.source_pages))
    )
    if not allow_draft:
        require_verified_extraction(root, pages)
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    if manifest:
        unit_map = {unit.unit_id: unit for unit in all_units}
        missing_manifest_units = [unit_id for unit_id in manifest.unit_ids if unit_id not in unit_map]
        if missing_manifest_units:
            raise ValueError(f"Batch references missing source units: {missing_manifest_units}")
        units = [unit_map[unit_id] for unit_id in manifest.unit_ids]
    else:
        units = [unit for unit in all_units if unit.page in pages]
    units = [unit for unit in units if unit.render_policy is RenderPolicy.INCLUDE]
    render_units, grouped_code_ids = _coalesce_code_units(units)
    translations = translation_map(root)
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
    open_severe: list[str] = []
    selected_ids = {unit.unit_id for unit in units}
    for issue_path in (root / "reviews").glob("*.issues.jsonl"):
        for issue in read_jsonl(issue_path, ReviewIssue):
            if (
                issue.unit_id in selected_ids
                and issue.status is IssueStatus.OPEN
                and issue.severity in {Severity.BLOCKER, Severity.MAJOR}
            ):
                open_severe.append(issue.issue_id)
    if not allow_draft and (missing or unapproved or open_severe):
        raise ValueError(
            "Formal rendering is blocked; "
            f"missing={missing}, not_publishable={unapproved}, open_severe={open_severe}"
        )

    output_name = _safe_name(name)
    output = root / "output"
    output.mkdir(parents=True, exist_ok=True)
    markdown_path = output / f"{output_name}.zh.md"
    html_path = output / f"{output_name}.bilingual.html"
    qa_path = output / f"{output_name}.quality.md"
    unresolved_path = output / f"{output_name}.unresolved.md"

    markdown: list[str] = [
        f"# {config.title}",
        "",
        f"> 翻译状态：{config.status}；用途：{config.rights_status}。",
        "",
    ]
    rows: list[dict[str, Any]] = []
    previous_page: int | None = None
    previous_unit: SourceUnit | None = None
    for unit in render_units:
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
        target = record.target_text if record else None
        if target is not None:
            bilingual_target = target
        elif not unit.translatable and unit.source_text:
            bilingual_target = unit.source_text
        elif not unit.translatable and unit.asset_refs:
            bilingual_target = "[保留原始公式或图像]"
        else:
            bilingual_target = "[尚未翻译]"
        render_unit = unit
        if unit.kind is UnitKind.TABLE and record and record.target_table:
            render_unit = unit.model_copy(update={"table": record.target_table})
        rendered = _target_markdown(render_unit, target)
        anchor = "".join(
            f'<a id="{unit_id}"></a>'
            for unit_id in grouped_code_ids.get(unit.unit_id, [unit.unit_id])
        )
        if (
            unit.continues_from_previous
            and previous_unit is not None
            and previous_unit.kind is UnitKind.PARAGRAPH
            and markdown
        ):
            while markdown and markdown[-1] == "":
                markdown.pop()
            markdown[-1] += f" {anchor}{rendered}"
            markdown.append("")
        else:
            markdown.extend([anchor, rendered, ""])
        if record and record.reader_note:
            sources = "；".join(record.reader_note.sources)
            markdown.extend(
                [
                    f"> **读者注：** {record.reader_note.text}",
                    f"> 来源：{sources}（访问日期：{record.reader_note.accessed_at or '未记录'}）",
                    "",
                ]
            )
        assets = (
            [f"../{asset.path.replace('\\', '/')}" for asset in unit.asset_refs]
            if unit.kind is UnitKind.FIGURE
            else []
        )
        source_html = _unit_html(unit, unit.source_markdown or unit.source_text)
        target_html = _unit_html(
            unit,
            bilingual_target,
            record.target_table if record else None,
        )
        if unit.unit_id in grouped_code_ids:
            extra_anchors = "".join(
                f'<span id="{html.escape(unit_id)}"></span>'
                for unit_id in grouped_code_ids[unit.unit_id][1:]
            )
            source_html = extra_anchors + source_html
            target_html = extra_anchors + target_html
        if (
            unit.continues_from_previous
            and rows
            and rows[-1]["unit"].kind is UnitKind.PARAGRAPH
            and source_html.startswith("<p>")
            and rows[-1]["source_html"].endswith("</p>")
        ):
            inline_source = source_html.removeprefix("<p>").removesuffix("</p>")
            inline_target = target_html.removeprefix("<p>").removesuffix("</p>")
            rows[-1]["source_html"] = (
                rows[-1]["source_html"].removesuffix("</p>")
                + f' <a id="{html.escape(unit.unit_id)}"></a>{inline_source}</p>'
            )
            rows[-1]["target_html"] = (
                rows[-1]["target_html"].removesuffix("</p>")
                + f' <a href="#{html.escape(unit.unit_id)}" aria-label="continued unit"></a>{inline_target}</p>'
            )
            if record and record.reader_note:
                rows[-1]["reader_notes"].append(record.reader_note)
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
                    "reader_notes": [record.reader_note]
                    if record and record.reader_note
                    else [],
                }
            )
        previous_page = unit_last_page
        previous_unit = unit
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
    markdown_path.write_text(markdown_text, encoding="utf-8")

    environment = Environment(
        loader=FileSystemLoader(plugin_root() / "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = environment.get_template("bilingual.html.j2")
    html_path.write_text(
        template.render(
            config=config,
            rows=rows,
            pages=f"{min(pages)}–{max(pages)}",
            pdf_uri=config.source(root).as_uri(),
            allow_draft=allow_draft,
        ),
        encoding="utf-8",
    )

    _write_quality_summary(qa_path, root, units, missing, unapproved, open_severe)
    _write_unresolved(unresolved_path, root, selected_ids)
    render_qa_path = output / f"{output_name}.render-qa.json"
    render_errors = _render_quality_errors(markdown_text, units)
    render_qa_path.write_text(
        json.dumps(
            {
                "passed": not render_errors,
                "selection": {"batch_id": batch_id, "pages": sorted(pages)},
                "unit_ids": [unit.unit_id for unit in units],
                "errors": render_errors,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
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
    if config.external_review and config.external_review.enabled and batch_id:
        external_path = output / f"{output_name}.external-review.md"
        _write_external_review_summary(external_path, root, batch_id)
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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _render_quality_errors(markdown: str, units: list[SourceUnit]) -> list[str]:
    errors: list[str] = []
    if re.search(r"(?m)^-\s+[•▪■●]\s+", markdown):
        errors.append("duplicated-list-marker")
    if re.search(r"(?m)^>\s+>\s+", markdown):
        errors.append("nested-admonition-marker")
    for unit in units:
        anchor = f'<a id="{unit.unit_id}"></a>'
        if markdown.count(anchor) != 1:
            errors.append(f"unit-anchor-count:{unit.unit_id}:{markdown.count(anchor)}")
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
    path.write_text("\n".join(lines), encoding="utf-8")


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
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
