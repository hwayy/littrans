"""Read-only glossary queries: which entries a batch, a page range or a text will need."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from littrans.evidence import (
    fold_term_text,
    project_units,
    reference_terms_by_kind,
    select_relevant,
    term_matches,
    term_source_text,
    without_quoted_titles,
)
from littrans.extractor import parse_page_spec
from littrans.models import SourceUnit
from littrans.project import load_reference_terms, load_terms
from littrans.storage import load_project


def _selected_units(
    root: Path,
    *,
    batch_id: str | None,
    pages: str | None,
    unit_ids: list[str] | None,
) -> tuple[list[SourceUnit], dict[str, Any]]:
    all_units = project_units(root)
    if batch_id is not None:
        from littrans.batching import load_manifest

        manifest = load_manifest(root, batch_id)
        by_id = {unit.unit_id: unit for unit in all_units}
        missing = [unit_id for unit_id in manifest.unit_ids if unit_id not in by_id]
        if missing:
            raise ValueError(f"Batch {batch_id} names units that are not prepared: {missing}")
        units = [by_id[unit_id] for unit_id in manifest.unit_ids]
        return units, {"batch_id": batch_id, "unit_count": len(units)}
    if pages is not None:
        wanted = set(parse_page_spec(pages, load_project(root).source_pages))
        units = [unit for unit in all_units if unit.page in wanted]
        return units, {"pages": sorted(wanted), "unit_count": len(units)}
    if unit_ids is not None:
        by_id = {unit.unit_id: unit for unit in all_units}
        missing = [unit_id for unit_id in unit_ids if unit_id not in by_id]
        if missing:
            raise ValueError(f"Unknown source unit IDs: {missing}")
        units = [by_id[unit_id] for unit_id in unit_ids]
        return units, {"unit_ids": list(unit_ids), "unit_count": len(units)}
    raise ValueError("Choose exactly one selector: --batch-id, --pages, --unit-ids or --text")


def glossary_lookup(
    root: Path,
    *,
    batch_id: str | None = None,
    pages: str | None = None,
    unit_ids: list[str] | None = None,
    text: Path | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    """List the approved (gated) and reference (not gated) entries a selection needs.

    Selection by batch, page range or unit ids applies the same scope and folding rules as
    the packets, so the answer is exactly what a translate or audit packet would carry. A
    text file is matched without scope (its pages are unknown) and says so.
    """
    root = Path(root).resolve()
    selectors = [value for value in (batch_id, pages, unit_ids, text) if value is not None]
    if len(selectors) != 1:
        raise ValueError("Choose exactly one selector: --batch-id, --pages, --unit-ids or --text")
    approved_terms = load_terms(root)
    reference_terms = load_reference_terms(root)
    if text is not None:
        if not text.is_file():
            raise FileNotFoundError(str(text))
        folded = fold_term_text(without_quoted_titles(text.read_text(encoding="utf-8")))
        approved = [term for term in approved_terms if term_matches(term, folded)]
        reference = [term for term in reference_terms if term_matches(term, folded)]
        selection: dict[str, Any] = {"text": str(text), "scope_applied": False}
    else:
        units, selection = _selected_units(root, batch_id=batch_id, pages=pages, unit_ids=unit_ids)
        selection["scope_applied"] = True
        approved = select_relevant(approved_terms, units)
        reference = select_relevant(reference_terms, units)
    if kind is not None:
        reference = [term for term in reference if str(term.get("kind")) == kind]
    return {
        "selection": selection,
        "approved": approved,
        "reference": reference_terms_by_kind(reference),
        "approved_total": len(approved),
        "reference_total": len(reference),
    }


def glossary_check(root: Path) -> dict[str, Any]:
    """Load every glossary file and report entries that match no prepared unit.

    Loading alone surfaces schema errors (unknown match mode, invalid regex, a gated entry
    in ``reference.yaml``). An approved entry matching nothing is the same finding QA
    reports as ``approved-term-never-matched``; a reference entry matching nothing is
    harmless but usually a spelling or a form the source never uses.
    """
    root = Path(root).resolve()
    approved_terms = load_terms(root)
    reference_terms = load_reference_terms(root)
    candidate_terms = load_terms(root, "candidates.yaml", enforced_only=False)
    folded_units = [term_source_text(unit) for unit in project_units(root)]

    def unmatched(terms: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            term for term in terms
            if not any(term_matches(term, folded) for folded in folded_units)
        ]

    unmatched_reference = unmatched(reference_terms)
    return {
        "prepared_units": len(folded_units),
        "approved": {"total": len(approved_terms), "never_matched": unmatched(approved_terms)},
        "reference": {
            "total": len(reference_terms),
            "by_kind": {kind: len(terms) for kind, terms in reference_terms_by_kind(reference_terms).items()},
            "never_matched": reference_terms_by_kind(unmatched_reference),
        },
        "candidates": {"total": len(candidate_terms)},
    }
