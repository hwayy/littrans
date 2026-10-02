"""Source gates and translation contracts exercised through public entry points."""
from pathlib import Path

import pymupdf as fitz
import pytest
from fidelity_fixtures import confirm_structure_checks, original_image_evidence
from test_fidelity_source import approve
from test_paragraph_model import old_workspace, paragraph

from littrans import fidelity
from littrans.batching import create_batches
from littrans.models import (
    ParagraphData,
    ParagraphLabel,
    ParagraphLabelEvidence,
    ParagraphLabelPart,
    SourceUnit,
    TranslationRecord,
)
from littrans.paragraphs import source_binding
from littrans.project import initialize_project
from littrans.quality import _target_structure_error, run_qa
from littrans.rendering import _target_markdown, _unit_html
from littrans.storage import read_json, read_jsonl, write_json, write_jsonl
from littrans.translation import submit_translation
from littrans.upgrade import upgrade_project


@pytest.fixture
def chapter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fidelity, "detect_layout", lambda *args: {
        "status": "unavailable", "reason": "Synthetic label-only heading", "pages": {}})
    pdf = tmp_path / "chapter.pdf"
    with fitz.open() as document:
        document.new_page().insert_text((72, 72), "Chapter 6", fontsize=18)
        document.save(pdf)
    root = tmp_path / "project"
    initialize_project(pdf, root, "technical-book")
    fidelity.prepare_source(root, allow_missing_layout=True)
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    assert len(units) == 1
    ledger = read_json(root / "derived/fidelity-pages/p0001.json")
    glyphs = [g for g in ledger["glyphs"] if g["text"].strip()]
    box = (min(g["bbox"][0] for g in glyphs), min(g["bbox"][1] for g in glyphs),
           max(g["bbox"][2] for g in glyphs), max(g["bbox"][3] for g in glyphs))
    units[0].paragraph = ParagraphData(type="labelled", body_text="", outline_level=1,
        label=ParagraphLabel(parts=[
            ParagraphLabelPart(part_id="word", source_text="Chapter", policy="translatable"),
            ParagraphLabelPart(part_id="number", source_text=" 6")],
            evidence=ParagraphLabelEvidence(page=1, bbox=box,
                                           glyph_ids=[g["id"] for g in glyphs])))
    units[0].section_id = units[0].unit_id
    units[0].source_hash = source_binding(units[0])
    write_jsonl(root / "derived/units.jsonl", units)
    return root


@pytest.mark.parametrize("problem", ["missing_confirmation", "uncertain_outline"])
def test_unresolved_paragraph_structure_blocks_batching(chapter: Path, problem: str) -> None:
    packet = fidelity.build_source_review_packet(chapter)
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "synthetic-paragraph-oracle"
    row = review["pages"][0]
    for field in ("viewed_original", "coverage_complete", "boundaries_complete",
                  "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
        row[field] = True
    confirm_structure_checks(row)
    if problem == "missing_confirmation":
        row["confirmed_paragraphs"] = []
    else:
        row["paragraph_uncertainties"] = [{"unit_id": row["confirmed_paragraphs"][0]["unit_id"],
                                          "question": "Confirm the outline level"}]
    path = chapter / "review.json"
    write_json(path, review)
    assert not fidelity.import_source_review(chapter, path, True)["approved_pages"]
    with pytest.raises(ValueError, match="verification failed"):
        create_batches(chapter, "1", prefix="pending")


def test_label_only_heading_submits_empty_body_and_separate_translation(chapter: Path) -> None:
    assert approve(chapter)["approved_pages"] == [1]
    batch = create_batches(chapter, "1", prefix="chapter")[0]
    unit = read_jsonl(chapter / "derived/units.jsonl", SourceUnit)[0]
    record = TranslationRecord(unit_id=unit.unit_id, source_hash=unit.source_hash,
        target_text="", label_translations={"word": "章"},
        image_evidence=original_image_evidence(chapter, unit))
    path = chapter / "submission.jsonl"
    write_jsonl(path, [record])
    submit_translation(chapter, batch.batch_id, path)
    assert run_qa(chapter, batch.batch_id).passed
    assert _target_markdown(unit, "", record.label_translations) == "# 章 6"
    record.label_translations["number"] = "7"
    write_jsonl(path, [record])
    with pytest.raises(ValueError, match="mapping mismatch"):
        submit_translation(chapter, batch.batch_id, path)


def test_existing_batch_cannot_submit_against_unreviewed_structure(chapter: Path) -> None:
    approve(chapter)
    batch = create_batches(chapter, "1", prefix="chapter")[0]
    units = read_jsonl(chapter / "derived/units.jsonl", SourceUnit)
    units[0].paragraph.outline_level = 2
    units[0].source_hash = source_binding(units[0])
    write_jsonl(chapter / "derived/units.jsonl", units)
    record = TranslationRecord(unit_id=units[0].unit_id, source_hash=units[0].source_hash,
        target_text="", label_translations={"word": "章"},
        image_evidence=original_image_evidence(chapter, units[0]))
    path = chapter / "submission.jsonl"
    write_jsonl(path, [record])
    with pytest.raises(ValueError, match="verification failed"):
        submit_translation(chapter, batch.batch_id, path)


def test_label_glyph_binding_rejects_missing_wrong_and_out_of_order_glyphs(chapter: Path) -> None:
    units = read_jsonl(chapter / "derived/units.jsonl", SourceUnit)
    label = units[0].paragraph.label
    label.evidence.glyph_ids.reverse()
    write_jsonl(chapter / "derived/units.jsonl", units)
    with pytest.raises(ValueError, match="native order"):
        fidelity.build_source_review_packet(chapter)
    label.evidence.glyph_ids = []
    label.evidence.source_range = (0, 9)
    write_jsonl(chapter / "derived/units.jsonl", units)
    with pytest.raises(ValueError, match="available native glyph evidence"):
        fidelity.build_source_review_packet(chapter)


def test_sidebar_title_shares_labels_without_implicit_heading_semantics() -> None:
    unit = paragraph(body="Context", label="☞", sidebar_id="box", sidebar_role="title")
    markup = _unit_html(unit, "背景", source_view=False)
    assert markup.count("☞") == 1 and "<h" not in markup and 'role="heading"' not in markup
    assert _target_markdown(unit, "背景") == "> ☞ 背景"


def test_label_cannot_borrow_matching_glyphs_outside_its_source_unit(chapter: Path) -> None:
    units = read_jsonl(chapter / "derived/units.jsonl", SourceUnit)
    units[0].bbox = (200, 200, 250, 250)
    write_jsonl(chapter / "derived/units.jsonl", units)
    with pytest.raises(ValueError, match="belong to their source unit"):
        fidelity.build_source_review_packet(chapter)


def test_upgrade_retains_raster_evidence_but_archives_all_approvals(tmp_path: Path) -> None:
    old = old_workspace(tmp_path)
    image = old / "evidence/pages/fidelity-p0001.png"
    image.parent.mkdir(exist_ok=True)
    image.write_bytes(b"immutable-original-raster")
    write_json(old / "reviews/audit.json", {"approved": True})
    new = tmp_path / "new"
    upgrade_project(old, new)
    assert (new / image.relative_to(old)).read_bytes() == image.read_bytes()
    assert not (new / "reviews/audit.json").exists()
    assert (new / "history/paragraph-upgrade-v7/reviews/audit.json").exists()


def test_75_numbered_paragraphs_do_not_add_circles_and_five_bullets_survive() -> None:
    numbered = [paragraph(str(i), body="Condition", label=f"({i + 1})") for i in range(75)]
    bullets = [paragraph("bullet" + str(i), body="Original", label="•") for i in range(5)]
    markup = "".join(_unit_html(unit, unit.paragraph.body_text, source_view=True)
                     for unit in [*numbered, *bullets])
    assert "<ul" not in markup and "<li" not in markup
    assert markup.count("•") == 5 and markup.count("paragraph-label") == 80


def test_qa_reports_an_explicit_duplicate_label_without_stripping_target_text() -> None:
    unit = paragraph(body="A condition", label="☞甲Ⅻ❖")
    target = "☞甲Ⅻ❖ 一个条件"
    assert "repeats its explicit label" in _target_structure_error(unit, target)
    assert target == "☞甲Ⅻ❖ 一个条件"
    # A reference printed at the start of the source body is still body content.
    reference = paragraph(body="(ii) is referred to here", label="(ii)")
    assert _target_structure_error(reference, "(ii) 在此被引用") is None
