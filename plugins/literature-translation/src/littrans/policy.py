"""Policy consumers shared by submission, QA, approval and rendering."""
from pathlib import Path
from typing import Any

from littrans.configuration import read_settings
from littrans.models import SourceUnit, TranslationRecord


def record_errors(root: Path, unit: SourceUnit, record: TranslationRecord) -> list[str]:
    from functools import cache

    from littrans.fidelity_models import asset_reference_ids, load_assets
    policy = read_settings(root).translation
    # Loaded at most once per record, and only when an asset check needs it.
    assets = cache(lambda: load_assets(root))
    errors = []
    note = record.reader_note
    if note:
        if not policy.reader_notes.allow_modernization:
            errors.append("Reader notes are disabled by project policy")
        if policy.reader_notes.require_primary_https_sources and not note.sources:
            errors.append("Reader notes require primary HTTPS sources and independent source review")
    if unit.kind == "caption" and not policy.figures.translate_caption and record.target_text != unit.source_text:
        errors.append("Caption policy requires preserving source text")
    if policy.figures.internal_labels == "preserve" and (record.figure_labels or any(a.figure_labels for a in record.asset_translations)):
        errors.append("Figure-label policy preserves original labels")
    if policy.figures.internal_labels == "preserve" and record.asset_translations:
        for item in record.asset_translations:
            if item.asset_id in assets() and assets()[item.asset_id].kind == "figure" and (item.target_text or item.target_table):
                errors.append("Figure policy preserves internal image text")
    if policy.tables.translation == "cells":
        if unit.kind == "table" and record.target_table is None:
            errors.append("Table policy requires translated cells")
        for item in record.asset_translations:
            if item.asset_id in assets() and assets()[item.asset_id].kind == "table" and item.language_present and item.target_table is None:
                errors.append(f"Table {item.asset_id} requires translated cells")
    for annotation in record.code_annotations:
        enabled = policy.code.translate_comments if annotation.kind == "comment" else policy.code.translate_string_literals
        if not enabled:
            errors.append(f"Code {annotation.kind} annotations are disabled")
        if annotation.asset_id:
            if (annotation.asset_id not in asset_reference_ids(unit.source_markdown or unit.source_text)
                    or annotation.asset_id not in assets() or assets()[annotation.asset_id].kind != "code"):
                errors.append("Code annotation must reference an original code asset in this unit")
        elif unit.kind != "code":
            errors.append("Code annotations require a code source unit")
        elif annotation.source not in unit.source_text:
            errors.append("Code annotation source must occur in preserved source code")
        if not annotation.source.strip() or not annotation.target.strip():
            errors.append("Code annotations require source and target text")
    return errors


def wants_transcription(root: Path, asset: dict[str, Any]) -> bool:
    policy = read_settings(root).translation
    if asset["kind"] == "math":
        mode = policy.equations.display if asset.get("display", False) else policy.equations.inline
        return mode == "reviewed-transcription"
    if asset["kind"] == "table":
        return policy.tables.presentation == "reviewed-transcription"
    return True


def required_transcriptions(root: Path, ids: list[str]) -> list[str]:
    from littrans.fidelity_models import load_assets
    settings = read_settings(root)
    assets = load_assets(root)
    required_ids = []
    for key in ids:
        asset = assets[key]
        required = settings.verification.block_unfinished_transcription and asset.kind in {"math", "table", "code"}
        if asset.kind == "math":
            required |= wants_transcription(root, asset.model_dump(mode="json"))
        if asset.kind == "table":
            required |= (settings.translation.tables.presentation == "reviewed-transcription"
                         and not settings.translation.tables.image_fallback_in_final)
        if required:
            required_ids.append(key)
    return required_ids


def delivery_errors(root: Path, ids: list[str]) -> list[str]:
    from littrans.representations import representation_status
    states = representation_status(root, ids)["assets"]
    return [f"Project policy requires a reviewed transcription for {key}"
            for key in required_transcriptions(root, ids) if states[key]["state"] != "verified"]
