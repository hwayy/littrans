"""Shared original-image context, independent of transcription candidates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from littrans.models import SourceUnit
from littrans.storage import load_project, read_json, read_jsonl, sha256_file


def adjacent_source_units(root: Path, units: list[SourceUnit]) -> list[SourceUnit]:
    """One bounded source seam on each side, without exposing candidate output."""
    selected = {u.unit_id for u in units}
    all_units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    positions = {u.unit_id: i for i, u in enumerate(all_units)}
    neighbors: set[int] = set()
    for unit in units:
        position = positions.get(unit.unit_id)
        if position is None:
            continue
        for index in (position - 1, position + 1):
            if 0 <= index < len(all_units):
                other = all_units[index]
                if other.unit_id not in selected and abs(other.page - unit.page) <= 1:
                    neighbors.add(index)
    return [all_units[index] for index in sorted(neighbors)]


TARGET_TEXT_CONTRACTS = (
    "Contracts (writers follow them; auditors must not report them as defects): "
    "list_item targets carry no leading bullet or number, heading targets carry no '#' "
    "and note targets carry no admonition shell because the renderer owns those markers; "
    "each {{asset:ID}} placeholder stays verbatim with no space between Chinese text and "
    "the placeholder; Chinese prose uses full-width punctuation (，。；：！？) except inside "
    "code, identifiers and formulas; an image containing only mathematical or technical "
    "notation is declared with language_present=false plus notes instead of an invented "
    "companion text."
)


def original_context(root: Path, units: list[SourceUnit], role: str = "translate", *, include_adjacent: bool = False) -> dict[str, Any]:
    from littrans.fidelity_models import asset_reference_ids, load_assets
    assets = load_assets(root)
    adjacent = adjacent_source_units(root, units) if include_adjacent else []
    context_units = units + adjacent
    selected = list(dict.fromkeys(a for u in context_units for a in asset_reference_ids(u.source_markdown or u.source_text)))
    images: dict[str, str] = {}
    for aid in selected:
        if aid not in assets:
            raise ValueError(f"Unknown source asset: {aid}")
        for fragment in assets[aid].fragments:
            p = (root / fragment.png_path).resolve()
            p.relative_to(root.resolve())
            images[fragment.png_path] = sha256_file(p)
    for page in sorted({u.page for u in context_units}):
        path = f"evidence/pages/fidelity-p{page:04d}.png"
        if (root / path).exists():
            images[path] = sha256_file(root / path)
        ledger_path = root / f"derived/fidelity-pages/p{page:04d}.json"
        overflow = read_json(ledger_path).get("overflow_evidence") if ledger_path.is_file() else None
        if overflow:
            images[overflow["path"]] = sha256_file(root / overflow["path"])
    config = load_project(root)
    return {
        "source_sha256": config.source_sha256,
        "role": role,
        "model_policy": config.agent_models,
        "fresh_context": True,
        "candidate_access": False,
        "instructions": (
            "Read original English and actually view the referenced source images before writing. "
            "Preserve Markdown emphasis and [^number] footnote calls. Units sharing parent_id form one logical paragraph, including intervening display equations. "
            "Preserve each {{asset:ID}} in its corresponding unit; source formulas remain images. "
            "Keep each inline mathematical referent explicit at its corresponding location; do not replace it with a vague verbal category. "
            "If recoverable prose and formulas are trapped in a mixed-region, request source splitting before translating its companion. "
            "Do not read transcription candidates. Record viewed source image hashes in image_evidence "
            "on translation records. If mathematical meaning is unclear, record uncertainties. "
            "For table, mixed-region and figure assets include asset_translations: translated text, "
            "table cells or figure labels. If an image has only mathematical/technical notation, "
            "set language_present=false and explain in notes; the technical auditor must verify this. "
            "The v6 asset-reference contract overrides incompatible historical style instructions. "
            + TARGET_TEXT_CONTRACTS
        ) + (" Displayed math with formula_conditions contains source-native language: translate these conditions in an asset_translations companion; language_present=false is forbidden." if any(assets[aid].formula_conditions for aid in selected) else ""),
        "units": [{"unit_id": u.unit_id, "source_hash": u.source_hash,
                   "source": u.source_markdown or u.source_text, "page": u.page,
                   "equation_number": u.equation_number, "parent_id": u.parent_id, "footnote_number": u.footnote_number, "footnote_refs": u.footnote_refs} for u in units],
        "read_only_context": [{"unit_id": u.unit_id, "source": u.source_markdown or u.source_text,
                               "page": u.page, "source_hash": u.source_hash} for u in adjacent],
        "assets": [assets[aid].model_dump(mode="json") for aid in selected],
        "required_images": images,
    }


def validate_translation_images(root: Path, unit: SourceUnit, receipt: dict[str, str]) -> None:
    expected = original_context(root, [unit])["required_images"]
    for path, digest in expected.items():
        if receipt.get(path) != digest:
            raise ValueError(f"Missing/stale original-image viewing receipt for {unit.unit_id}: {path}")
