"""PR #10 twenty-ninth review: footnote links across display splits, prose omission
beside assets, receipt-only resubmissions and agent-facing documentation."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pymupdf as fitz
import pytest
from test_fidelity_source import approve
from test_pr10_round8 import submit
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.batching import load_manifest
from littrans.fidelity import (
    _make_unit,
    _separate_display_units,
    build_source_review_packet,
    prepare_source,
    verify_fidelity,
)
from littrans.fidelity_models import FidelityAsset, FidelityFragment
from littrans.models import ProjectStatus, SourceUnit, TranslationRecord, UnitKind
from littrans.project import initialize_project, translation_map
from littrans.quality import qa_report_is_current, run_qa
from littrans.storage import read_json, read_jsonl, write_jsonl

project = workflow_project
PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _display_asset() -> dict[str, FidelityAsset]:
    digest = "a" * 64
    fragment = FidelityFragment(page=1, bbox=(100, 100, 200, 120), png_path="x.png", svg_path="x.svg",
                                pdf_path="x.pdf", width=100, height=20,
                                file_sha256={"x.png": digest, "x.svg": digest, "x.pdf": digest})
    return {"D": FidelityAsset(id="D", kind="math", source_sha256=digest, content_sha256=digest,
                               fragments=[fragment], display=True)}


def test_display_split_keeps_footnote_refs_on_the_calling_chunk() -> None:
    assets = _display_asset()
    block = _make_unit(1, "p0001-b0", "First call[^1] here {{asset:D}} second call[^2] there.",
                       (50, 50, 300, 150), assets, kind="paragraph",
                       footnote_refs=["p0001-b8", "p0001-b9"])
    notes = [_make_unit(1, uid, "note", (50, 700, 300, 720), assets, kind="footnote", footnote_number=number)
             for uid, number in (("p0001-b8", "1"), ("p0001-b9", "2"))]
    result = _separate_display_units([block, *notes], assets)
    by_id = {unit.unit_id: unit for unit in result}
    assert by_id["p0001-b0"].footnote_refs == ["p0001-b8"]
    assert by_id["p0001-b0-displaypart2"].kind is UnitKind.EQUATION
    assert by_id["p0001-b0-displaypart2"].footnote_refs == []
    assert by_id["p0001-b0-displaypart3"].footnote_refs == ["p0001-b9"]
    fidelity._validate_footnote_relationships(result)


def test_display_split_uses_plan_note_numbers_before_assembly() -> None:
    """During preparation the footnote unit is not numbered yet; the plan supplies the number."""
    assets = _display_asset()
    block = _make_unit(1, "p0001-b0", "{{asset:D}} which completes the argument[^1]",
                       (50, 50, 300, 150), assets, kind="paragraph", footnote_refs=["p0001-b2"])
    note = _make_unit(1, "p0001-b2", "A footnote.", (50, 700, 300, 720), assets, kind="footnote")
    result = _separate_display_units([block, note], assets, {"p0001-b2": "1"})
    assert {u.unit_id: u.footnote_refs for u in result}["p0001-b0-displaypart2"] == ["p0001-b2"]


def _footnote_display_pdf(tmp_path: Path) -> tuple[Path, Path]:
    """One native block: a display line, then prose ending in a footnote call."""
    source = tmp_path / "display-footnote.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        writer = fitz.TextWriter(page.rect)
        font = fitz.Font("helv")
        writer.append((50, 200), "The result holds as shown below", font, 11)
        writer.append((150, 214), "a = b", font, 11)
        writer.append((50, 228), "which completes the argument", font, 11)
        width = font.text_length("which completes the argument", 11)
        writer.append((50 + width + 1, 223), "1", font, 7)
        writer.append((50, 700), "1", font, 7)
        writer.append((58, 704), "A footnote about the result.", font, 11)
        writer.write_text(page)
        doc.save(source)
    root = tmp_path / "project"
    initialize_project(source, root, "technical-book")
    return source, root


def _stub_layout(root: Path):
    image = str((root / "evidence/pages/fidelity-p0001.png").resolve())

    def detect(images, output):
        return {"status": "ok", "fingerprint": "synthetic", "pages": {image: [
            {"label": "display_formula", "bbox": [140 * 2, 203 * 2, 200 * 2, 217 * 2]},
            {"label": "footnote", "bbox": [45 * 2, 690 * 2, 300 * 2, 710 * 2]},
        ]}}

    return detect


def test_prepare_keeps_footnote_link_when_block_holds_a_display(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _, root = _footnote_display_pdf(tmp_path)
    monkeypatch.setattr(fidelity, "detect_layout", _stub_layout(root))
    assert prepare_source(root)["prepared_pages"] == [1]
    units = {u.unit_id: u for u in read_jsonl(root / "derived/units.jsonl", SourceUnit)}
    caller = next(u for u in units.values() if "[^1]" in u.source_text)
    note = next(u for u in units.values() if u.kind is UnitKind.FOOTNOTE)
    assert caller.unit_id.endswith("-displaypart2")
    assert caller.footnote_refs == [note.unit_id] and note.footnote_number == "1"
    assert any(u.kind is UnitKind.EQUATION for u in units.values())
    build_source_review_packet(root)
    approve(root, "1")
    assert verify_fidelity(root)["passed"]


def test_prepare_rolls_back_an_inconsistent_footnote_graph(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A footnote validation failure must not publish unverifiable units."""
    _, root = _footnote_display_pdf(tmp_path)
    monkeypatch.setattr(fidelity, "detect_layout", _stub_layout(root))

    def broken(units, assets, note_numbers=None):
        return _separate_display_units(units, assets)  # legacy behaviour: numbers unknown

    monkeypatch.setattr(fidelity, "_separate_display_units", broken)
    with pytest.raises(ValueError, match="footnote call numbers"):
        prepare_source(root)
    assert not (root / "derived/fidelity-pages/p0001.json").exists()
    assert not (root / "derived/units.jsonl").exists() or not read_jsonl(root / "derived/units.jsonl", SourceUnit)
    monkeypatch.undo()
    monkeypatch.setattr(fidelity, "detect_layout", _stub_layout(root))
    assert prepare_source(root)["prepared_pages"] == [1]
    build_source_review_packet(root)


def _asset_only(text: str) -> str:
    return "".join(re.findall(r"\{\{asset:[^}]+\}\}", text))


def test_qa_rejects_prose_dropped_beside_an_asset(project: Path) -> None:
    bid = "sample-one-b001"

    def drop_prose(unit: SourceUnit, record: TranslationRecord) -> None:
        if "{{asset:" in unit.source_text:
            record.target_text = _asset_only(unit.source_text)

    submit(project, bid, drop_prose)
    report = run_qa(project, bid)
    assert not report.passed
    assert "empty-translation" in {error.code for error in report.errors}


def test_qa_accepts_asset_only_source_with_asset_only_target(project: Path) -> None:
    bid = "sample-one-b001"
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    manifest = load_manifest(project, bid)
    target = next(u for u in units if u.unit_id in manifest.translatable_unit_ids and "{{asset:" in u.source_text)
    target.source_text = target.source_markdown = _asset_only(target.source_text)
    write_jsonl(project / "derived/units.jsonl", units)
    approve(project, "all")

    def placeholder_only(unit: SourceUnit, record: TranslationRecord) -> None:
        record.target_text = _asset_only(unit.source_text)

    submit(project, bid, placeholder_only)
    report = run_qa(project, bid)
    assert "empty-translation" not in {error.code for error in report.errors}


def test_receipt_only_resubmission_updates_receipt_without_invalidating_audits(project: Path) -> None:
    bid = "sample-one-b001"
    submit(project, bid)
    assert run_qa(project, bid).passed
    invalidations = project / "evidence/audits" / f"{bid}.invalidations.json"
    before = read_json(invalidations) if invalidations.is_file() else {}
    manifest = load_manifest(project, bid)
    statuses = {unit_id: record.status for unit_id, record in translation_map(project).items()}

    def more_images(unit: SourceUnit, record: TranslationRecord) -> None:
        record.image_evidence = {**record.image_evidence, "evidence/pages/fidelity-p0002.png": "0" * 64}

    submit(project, bid, more_images)
    after = read_json(invalidations) if invalidations.is_file() else {}
    assert after == before, "a viewing-receipt change must not invalidate audit coverage"
    records = translation_map(project)
    for unit_id in manifest.translatable_unit_ids:
        assert records[unit_id].revision == 1
        assert records[unit_id].status is statuses[unit_id] is not ProjectStatus.REVISED
        assert "evidence/pages/fidelity-p0002.png" in records[unit_id].image_evidence
    # QA binds the receipt, so the cached pass is stale and reruns cleanly.
    assert not qa_report_is_current(project, bid)
    assert run_qa(project, bid).passed


def test_cli_batch_create_trims_unit_id_lists(project: Path) -> None:
    from typer.testing import CliRunner

    from littrans.cli import app

    units = [u.unit_id for u in read_jsonl(project / "derived/units.jsonl", SourceUnit) if u.page == 1]
    result = CliRunner().invoke(app, ["batch", "create", str(project), "--pages", "1", "--prefix", "trimmed",
                                      "--unit-ids", " " + ", ".join(units) + " ,"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)[0]["unit_ids"] == units


def test_fidelity_workflow_reference_documents_every_packet_stage_and_override_contract() -> None:
    text = (PLUGIN_ROOT / "references/fidelity-workflow.md").read_text(encoding="utf-8")
    flat = " ".join(text.split())
    stages_sentence = flat[flat.index("`workflow packet` stages are"):].split(".", 1)[0]
    for stage in ("source-review", "translate", "revise", "audit", "transcribe", "asset-audit"):
        assert f"`{stage}`" in stages_sentence
    assert "### Override contract" in text
    for key in ("regions", "units", "page_canvas_bbox", "preserve_asset_id", "glyph_ids", "fragments",
                "source_markdown", "footnote_refs", "exactly once"):
        assert key in text
    review = (PLUGIN_ROOT / "skills/prepare-literature-source/references/source-review.md").read_text(encoding="utf-8")
    assert "#narrow-original-glyph-corrections" in review
    assert "#override-contract" in review


def test_readme_lists_batch_creation_and_example_record_uses_real_evidence_paths() -> None:
    readme = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")
    assert "littrans batch create PROJECT --pages" in readme
    example = json.loads((PLUGIN_ROOT / "references/translation-record.example.jsonl").read_text(encoding="utf-8").strip())
    TranslationRecord.model_validate(example)
    assert any(path.startswith("evidence/pages/fidelity-p") for path in example["image_evidence"])
    assert any(path.startswith("derived/assets/fidelity/") for path in example["image_evidence"])
