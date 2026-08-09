from __future__ import annotations

import html
import re
from collections import Counter
from pathlib import Path
from typing import Any

import fitz

from littrans.evidence import page_evidence_fingerprints, page_evidence_units
from littrans.extractor import parse_page_spec
from littrans.models import (
    ExtractionIssue,
    IssueStatus,
    PageVerificationReceipt,
    RenderPolicy,
    SemanticStatus,
    Severity,
    SidebarRole,
    SourceUnit,
    UnitKind,
)
from littrans.semantics import looks_like_continuation, normalize_prose
from littrans.storage import (
    read_json,
    read_jsonl,
    require_current_project_schema,
    sha256_file,
    sha256_text,
    write_json,
)

TOKEN_RE = re.compile(r"[A-Za-z0-9]+|[\u0370-\u03ff]|[\u2200-\u22ff]")
VERIFIER_VERSION = "source-verifier-v4"


def _tokens(text: str) -> Counter[str]:
    return Counter(token.casefold() for token in TOKEN_RE.findall(text))


def _coverage(page_text: str, units: list[SourceUnit]) -> float:
    source = _tokens(page_text)
    if not source:
        return 1.0
    captured = _tokens(" ".join(unit.source_text for unit in units))
    return sum(min(count, captured[token]) for token, count in source.items()) / sum(
        source.values()
    )


def _semantic_context_units(
    all_units: list[SourceUnit], page_set: set[int]
) -> list[SourceUnit]:
    """Include complete sidebars that intersect a selected page range."""
    selected_indices = {
        index for index, unit in enumerate(all_units) if unit.page in page_set
    }
    sidebar_ids = {
        all_units[index].sidebar_id
        for index in selected_indices
        if all_units[index].sidebar_id is not None
    }
    for sidebar_id in sidebar_ids:
        member_indices = [
            index
            for index, unit in enumerate(all_units)
            if unit.sidebar_id == sidebar_id
        ]
        if member_indices:
            selected_indices.update(
                range(min(member_indices), max(member_indices) + 1)
            )
    return [all_units[index] for index in sorted(selected_indices)]


def _semantic_errors(root: Path, units: list[SourceUnit]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    seen: set[str] = set()
    sidebar_members: dict[str, list[tuple[int, SourceUnit]]] = {}
    for index, unit in enumerate(units):
        if unit.unit_id in seen:
            errors.append({"code": "duplicate-unit-id", "unit_id": unit.unit_id})
        seen.add(unit.unit_id)
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", unit.source_text):
            errors.append({"code": "source-control-character", "unit_id": unit.unit_id})
        if unit.kind is UnitKind.EQUATION:
            if not (unit.latex or "").strip():
                errors.append({"code": "missing-latex", "unit_id": unit.unit_id})
            if unit.math_status is not SemanticStatus.VERIFIED:
                errors.append({"code": "unverified-display-math", "unit_id": unit.unit_id})
            if not unit.asset_refs:
                errors.append({"code": "missing-equation-evidence", "unit_id": unit.unit_id})
        if unit.source_markdown and "$" in unit.source_markdown:
            if unit.math_status is not SemanticStatus.VERIFIED:
                errors.append({"code": "unverified-inline-math", "unit_id": unit.unit_id})
        if unit.kind is UnitKind.TABLE:
            if unit.table is None:
                errors.append({"code": "missing-table-structure", "unit_id": unit.unit_id})
            if unit.verification_status is not SemanticStatus.VERIFIED:
                errors.append({"code": "unverified-table", "unit_id": unit.unit_id})
        if unit.kind is UnitKind.CODE:
            if not unit.code_language or unit.code_language == "text":
                errors.append({"code": "unknown-code-language", "unit_id": unit.unit_id})
            code_lines = unit.source_text.splitlines()
            indentation_lines = [
                re.sub(r"^\s*\d+\s?", "", line) for line in code_lines
            ]
            significant_lines = [line.strip() for line in indentation_lines if line.strip()]
            empty_brace_block = (
                len(significant_lines) >= 3
                and significant_lines[-2:] == ["{", "}"]
            )
            if (
                len(code_lines) > 2
                and re.search(r"[<{]", unit.source_text)
                and not empty_brace_block
                and not any(
                    line.startswith((" ", "\t")) for line in indentation_lines[1:]
                )
            ):
                errors.append({"code": "code-indentation-suspect", "unit_id": unit.unit_id})
        if unit.kind is UnitKind.FIGURE:
            if unit.visual_text_status is not SemanticStatus.VERIFIED:
                errors.append({"code": "unverified-figure-text", "unit_id": unit.unit_id})
            if any(not (label.target or "").strip() for label in unit.figure_labels):
                errors.append({"code": "untranslated-figure-label", "unit_id": unit.unit_id})
        for asset in unit.asset_refs:
            if not root.joinpath(asset.path).is_file():
                errors.append(
                    {"code": "missing-asset", "unit_id": unit.unit_id, "path": asset.path}
                )
        if unit.sidebar_id:
            sidebar_members.setdefault(unit.sidebar_id, []).append((index, unit))
            if unit.verification_status is not SemanticStatus.VERIFIED:
                errors.append({"code": "unverified-sidebar", "unit_id": unit.unit_id})
    for sidebar_id, members in sidebar_members.items():
        indices = [index for index, _ in members]
        titles = [unit for _, unit in members if unit.sidebar_role is SidebarRole.TITLE]
        if len(titles) != 1 or members[0][1].sidebar_role is not SidebarRole.TITLE:
            errors.append({"code": "invalid-sidebar-title", "sidebar_id": sidebar_id})
        intervening = units[indices[0] : indices[-1] + 1]
        if any(
            unit.sidebar_id != sidebar_id
            and not (
                unit.render_policy is RenderPolicy.OMIT and not unit.translatable
            )
            for unit in intervening
        ):
            errors.append({"code": "noncontiguous-sidebar", "sidebar_id": sidebar_id})
    return errors


def _overlap(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    left, top = max(a[0], b[0]), max(a[1], b[1])
    right, bottom = min(a[2], b[2]), min(a[3], b[3])
    if right <= left or bottom <= top:
        return 0.0
    intersection = (right - left) * (bottom - top)
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return intersection / max(smaller, 1.0)


def _write_visual_report(
    root: Path, document: fitz.Document, pages: list[int], by_page: dict[int, list[SourceUnit]]
) -> str:
    report_dir = root / "derived" / "verification"
    report_dir.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    colors = {
        "heading": "#7c3aed",
        "paragraph": "#2563eb",
        "code": "#059669",
        "equation": "#dc2626",
        "table": "#d97706",
        "figure": "#0891b2",
        "note": "#ca8a04",
    }
    for page_number in pages:
        page = document[page_number - 1]
        image_path = report_dir / f"page-{page_number:04}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(image_path)
        boxes: list[str] = []
        for unit in by_page.get(page_number, []):
            x0, y0, x1, y1 = unit.bbox
            left = x0 / page.rect.width * 100
            top = y0 / page.rect.height * 100
            width = (x1 - x0) / page.rect.width * 100
            height = (y1 - y0) / page.rect.height * 100
            color = colors.get(unit.kind.value, "#64748b")
            boxes.append(
                f'<a class="box" href="#{html.escape(unit.unit_id)}" title="{html.escape(unit.unit_id)}" '
                f'style="left:{left:.3f}%;top:{top:.3f}%;width:{width:.3f}%;height:{height:.3f}%;border-color:{color}"></a>'
            )
        inventory = "".join(
            f'<li id="{html.escape(unit.unit_id)}"><code>{html.escape(unit.unit_id)}</code> '
            f'{html.escape(unit.kind.value)} - {html.escape(unit.source_text[:180])}</li>'
            for unit in by_page.get(page_number, [])
        )
        sections.append(
            f'<section><h2>PDF p.{page_number}</h2><div class="page"><img src="verification/{image_path.name}">'
            + "".join(boxes)
            + f"</div><ol>{inventory}</ol></section>"
        )
    output = root / "derived" / "extraction-report.html"
    output.write_text(
        "<!doctype html><html><head><meta charset='utf-8'><title>Extraction verification</title>"
        "<style>body{font:14px/1.5 system-ui;margin:2rem auto;max-width:1400px}section{margin:3rem 0}.page{position:relative;display:inline-block;max-width:900px}.page img{display:block;width:100%;height:auto}.box{position:absolute;border:2px solid;box-sizing:border-box;background:#fff2}li{margin:.35rem 0}code{overflow-wrap:anywhere}</style>"
        "</head><body><h1>Extraction verification</h1>"
        + "".join(sections)
        + "</body></html>",
        encoding="utf-8",
    )
    return str(output)


def _receipt_path(root: Path, page: int) -> Path:
    return root / "evidence" / "pages" / f"page-{page:04}.json"


def _error_pages(
    error: dict[str, Any], page_dependencies: dict[int, list[SourceUnit]]
) -> set[int]:
    """Return receipt pages affected by an anchored verification error."""
    affected: set[int] = set()
    anchored = False
    page = error.get("page")
    if isinstance(page, int):
        anchored = True
        if page in page_dependencies:
            affected.add(page)

    unit_ids = {
        value
        for key, value in error.items()
        if key.endswith("unit_id") and isinstance(value, str) and value
    }
    if unit_ids:
        anchored = True
        affected.update(
            receipt_page
            for receipt_page, dependencies in page_dependencies.items()
            if any(unit.unit_id in unit_ids for unit in dependencies)
        )

    sidebar_id = error.get("sidebar_id")
    if isinstance(sidebar_id, str) and sidebar_id:
        anchored = True
        affected.update(
            receipt_page
            for receipt_page, dependencies in page_dependencies.items()
            if any(unit.sidebar_id == sidebar_id for unit in dependencies)
        )

    return affected if anchored else set(page_dependencies)


def _receipt_key(
    source_sha256: str, unit_fingerprint: str, asset_fingerprint: str
) -> str:
    return sha256_text(
        "|".join(
            (source_sha256, unit_fingerprint, asset_fingerprint, VERIFIER_VERSION)
        )
    )


def verify_extraction(
    root: Path, page_spec: str = "all", force: bool = False
) -> dict[str, Any]:
    config = require_current_project_schema(root, "Source verification")
    actual_source_sha256 = sha256_file(config.source(root))
    if actual_source_sha256 != config.source_sha256:
        raise ValueError("Source PDF hash changed after project initialization")
    document = fitz.open(config.source(root))
    requested_pages = parse_page_spec(page_spec, document.page_count)
    all_units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    extraction_issues = read_jsonl(
        root / "derived" / "extraction-issues.jsonl", ExtractionIssue
    )
    page_dependencies = {
        page: page_evidence_units(page, all_units) for page in requested_pages
    }
    semantic_errors = [
        error
        for error in _semantic_errors(root, all_units)
        if _error_pages(error, page_dependencies)
    ]
    blocking_issue_errors = [
        {
            "page": issue.page,
            "unit_id": issue.unit_id,
        }
        for issue in extraction_issues
        if issue.status is IssueStatus.OPEN
        and issue.severity in {Severity.BLOCKER, Severity.MAJOR}
    ]
    blocking_issue_pages: set[int] = set()
    for error in blocking_issue_errors:
        blocking_issue_pages.update(_error_pages(error, page_dependencies))
    cached: dict[int, PageVerificationReceipt] = {}
    expected_keys: dict[int, tuple[str, str, str]] = {}
    for page in requested_pages:
        unit_fingerprint, asset_fingerprint = page_evidence_fingerprints(
            root, page, all_units
        )
        key = _receipt_key(
            actual_source_sha256, unit_fingerprint, asset_fingerprint
        )
        expected_keys[page] = (key, unit_fingerprint, asset_fingerprint)
        path = _receipt_path(root, page)
        if force or page in blocking_issue_pages or not path.is_file():
            continue
        try:
            receipt = PageVerificationReceipt.model_validate(read_json(path))
        except (OSError, ValueError):
            continue
        if receipt.receipt_key == key and receipt.passed:
            cached[page] = receipt

    pages = [page for page in requested_pages if page not in cached]
    if not pages:
        document.close()
        payload = {
            "passed": not semantic_errors,
            "source_sha256": config.source_sha256,
            "pages": [
                {
                    "page": page,
                    "unit_count": len(
                        [unit for unit in all_units if unit.page == page]
                    ),
                    "token_coverage": cached[page].token_coverage,
                    "cached": True,
                }
                for page in requested_pages
            ],
            "errors": semantic_errors,
            "visual_report": str(root / "derived" / "extraction-report.html"),
            "cached_pages": requested_pages,
            "verified_pages": [],
            "validator_version": VERIFIER_VERSION,
            "instruction": "Cached page receipts match the current PDF and validator; project-wide semantic checks must also pass.",
        }
        write_json(root / "derived" / "verification.json", payload)
        return payload
    page_set = set(pages)
    units = [unit for unit in all_units if unit.page in page_set]
    by_page = {page: [unit for unit in units if unit.page == page] for page in pages}
    errors = list(semantic_errors)
    for issue in extraction_issues:
        if issue.status is not IssueStatus.OPEN or issue.severity not in {
            Severity.BLOCKER,
            Severity.MAJOR,
        }:
            continue
        error = {
            "code": "open-extraction-issue",
            "page": issue.page,
            "issue_id": issue.issue_id,
            "issue_code": issue.code,
            "unit_id": issue.unit_id,
        }
        if _error_pages(error, page_dependencies) & page_set:
            errors.append(error)
    page_results: list[dict[str, Any]] = []
    for page_number in pages:
        page = document[page_number - 1]
        page_units = by_page[page_number]
        if not page_units:
            errors.append({"code": "empty-page-inventory", "page": page_number})
        for index, unit in enumerate(page_units):
            if index and (
                page_units[index - 1].kind is UnitKind.PARAGRAPH
                and unit.kind is UnitKind.PARAGRAPH
                and looks_like_continuation(page_units[index - 1].source_text, unit.source_text)
                and not unit.continues_from_previous
            ):
                errors.append(
                    {
                        "code": "unresolved-paragraph-continuation",
                        "page": page_number,
                        "unit_id": unit.unit_id,
                    }
                )
            for other in page_units[index + 1 :]:
                if UnitKind.FIGURE in {unit.kind, other.kind}:
                    continue
                if _overlap(unit.bbox, other.bbox) > 0.72:
                    errors.append(
                        {
                            "code": "overlapping-units",
                            "page": page_number,
                            "unit_id": unit.unit_id,
                            "other_unit_id": other.unit_id,
                        }
                    )
        raw = page.get_text("dict", sort=True)
        full_page_text = normalize_prose(page.get_text("text", sort=True))
        for unit in page_units:
            if unit.kind is not UnitKind.FIGURE:
                continue
            if not unit.source_text or not any(
                "vector-figure" in asset.path for asset in unit.asset_refs
            ):
                continue
            captured_labels = normalize_prose(
                page.get_text("text", clip=fitz.Rect(unit.bbox), sort=True)
            )
            for label in unit.figure_labels:
                source_label = normalize_prose(label.source)
                if (
                    source_label
                    and source_label in full_page_text
                    and source_label not in captured_labels
                ):
                    errors.append(
                        {
                            "code": "figure-label-outside-bbox",
                            "page": page_number,
                            "unit_id": unit.unit_id,
                            "label": label.source,
                        }
                    )
        image_boxes = [
            (
                float(block["bbox"][0]),
                float(block["bbox"][1]),
                float(block["bbox"][2]),
                float(block["bbox"][3]),
            )
            for block in raw.get("blocks", [])
            if block.get("type") == 1
            and (float(block["bbox"][2]) - float(block["bbox"][0]))
            * (float(block["bbox"][3]) - float(block["bbox"][1]))
            > page.rect.width * page.rect.height * 0.003
        ]
        accounted_visual_boxes = [
            unit.bbox
            for unit in page_units
            if unit.asset_refs
            and unit.kind in {UnitKind.FIGURE, UnitKind.EQUATION, UnitKind.TABLE}
        ]
        for image_bbox in image_boxes:
            if not any(
                _overlap(image_bbox, visual_bbox) > 0.7
                for visual_bbox in accounted_visual_boxes
            ):
                errors.append(
                    {"code": "unaccounted-image", "page": page_number, "bbox": image_bbox}
                )
        coverage = _coverage(page.get_text("text", sort=True), by_page[page_number])
        if coverage < 0.72:
            errors.append(
                {"code": "low-text-coverage", "page": page_number, "coverage": round(coverage, 4)}
            )
        page_results.append(
            {
                "page": page_number,
                "unit_count": len(by_page[page_number]),
                "token_coverage": round(coverage, 4),
            }
        )
    report_path = _write_visual_report(root, document, pages, by_page)
    error_page_sets = [
        (error, _error_pages(error, page_dependencies)) for error in errors
    ]
    for page_result in page_results:
        page = int(page_result["page"])
        page_errors = [
            error for error, affected_pages in error_page_sets if page in affected_pages
        ]
        key, unit_fingerprint, asset_fingerprint = expected_keys[page]
        receipt = PageVerificationReceipt(
            page=page,
            source_sha256=actual_source_sha256,
            unit_fingerprint=unit_fingerprint,
            asset_fingerprint=asset_fingerprint,
            validator_version=VERIFIER_VERSION,
            receipt_key=key,
            passed=not page_errors,
            token_coverage=float(page_result["token_coverage"]),
            errors=page_errors,
        )
        write_json(_receipt_path(root, page), receipt.model_dump(mode="json"))
    combined_pages = [
        (
            {
                "page": page,
                "unit_count": len([unit for unit in all_units if unit.page == page]),
                "token_coverage": cached[page].token_coverage,
                "cached": True,
            }
            if page in cached
            else next(result for result in page_results if result["page"] == page)
        )
        for page in requested_pages
    ]
    payload = {
        "passed": not errors,
        "source_sha256": config.source_sha256,
        "pages": combined_pages,
        "errors": errors,
        "visual_report": report_path,
        "cached_pages": sorted(cached),
        "verified_pages": pages,
        "validator_version": VERIFIER_VERSION,
        "instruction": "Open the visual report and compare every box with the PDF before marking semantic overrides verified.",
    }
    write_json(root / "derived" / "verification.json", payload)
    return payload


def require_verified_extraction(root: Path, pages: set[int]) -> None:
    payload = verify_extraction(root, ",".join(str(page) for page in sorted(pages)))
    if not payload["passed"]:
        codes = sorted({str(item.get("code")) for item in payload["errors"]})
        raise ValueError(f"Extraction verification failed: {codes}")
