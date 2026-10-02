"""Explicit paragraph contracts, without a catalogue of supported marker patterns."""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf as fitz
import pytest
from pydantic import ValidationError

from littrans.evidence import (
    source_unit_fingerprint,
    structure_unit_fingerprint,
    translation_unit_fingerprint,
)
from littrans.models import (
    ParagraphData,
    ParagraphLabel,
    ParagraphLabelEvidence,
    ParagraphLabelPart,
    ParagraphLayout,
    ProjectStatus,
    SourceUnit,
    TranslationRecord,
)
from littrans.paragraphs import (
    bind_sections,
    label_translation_errors,
    outline,
    relation_neighbors,
    source_binding,
    structure_errors,
    toc_entries,
)
from littrans.project import initialize_project
from littrans.quality import _comparison_source_text
from littrans.rendering import _continues_paragraph, _target_markdown, _unit_html
from littrans.storage import read_jsonl, read_yaml, write_json, write_yaml
from littrans.upgrade import upgrade_project


def paragraph(uid: str = "p1", body: str = "Body", label: str | None = None,
              level: int | None = None, **values) -> SourceUnit:
    text = (label + " " if label and body else label or "") + body
    data = ParagraphData(
        type="numbered" if label else "plain", body_text=body, outline_level=level,
        label=ParagraphLabel(parts=[ParagraphLabelPart(part_id="marker", source_text=label)],
            evidence=ParagraphLabelEvidence(page=values.get("page", 1), source_range=(0, len(label))))
        if label else None,
    )
    unit = SourceUnit(unit_id=uid, kind="paragraph", bbox=(10, 10, 100, 30),
                      source_text=text, source_hash="unbound", confidence=1,
                      **{"paragraph": data, "page": 1, **values})
    unit.source_hash = source_binding(unit)
    return unit


@pytest.mark.parametrize("label", ["(1)", "(i)", "(ii)", "(iii)", "6.1.", "6.10.", "a)",
                                    "•", "◦", "▪", "☞甲Ⅻ❖"])
def test_arbitrary_labels_are_restored_exactly_once_without_list_shell(label: str) -> None:
    unit = paragraph(body="Choose 96 dpi.", label=label)
    assert _comparison_source_text(unit) == "Choose 96 dpi."
    markup = _unit_html(unit, "选择 96 dpi。", source_view=False)
    assert markup.count(label) == 1
    assert "<ul" not in markup and "<ol" not in markup and "<li" not in markup
    assert "选择 96 dpi。" in markup
    assert not outline([unit])


@pytest.mark.parametrize("label", ["*marker*", "_marker_", "<b>&copy;</b>", "[marker]", "$marker$"])
def test_literal_label_characters_do_not_become_inline_markup(label: str) -> None:
    import html
    unit = paragraph(body="Body", label=label)
    markup = _unit_html(unit, "正文", source_view=False)
    assert html.escape(label) in markup
    assert "<em>marker</em>" not in markup and "<b>" not in markup
    assert "\\" in _target_markdown(unit, "正文")
    markdown = pytest.importorskip("markdown_it")
    assert markdown.MarkdownIt().render(_target_markdown(unit, "正文")) == (
        f"<p>{html.escape(label)} 正文</p>\n")


def test_declared_inline_label_formatting_survives_translation() -> None:
    data = ParagraphData(type="labelled", body_text="Body",
        label=ParagraphLabel(parts=[ParagraphLabelPart(part_id="word", source_text="Choice",
            source_markdown="**Choice**", policy="translatable")],
            evidence=ParagraphLabelEvidence(page=1, source_range=(0, 6))))
    unit = paragraph(body="Choice Body", paragraph=data)
    assert "<strong>选择</strong>" in _unit_html(unit, "正文", source_view=False,
        label_translations={"word": "选择"})
    assert _target_markdown(unit, "正文", {"word": "选择"}) == "**选择** 正文"


@pytest.mark.parametrize("text", ["(x + y) is positive", "2026 was a year", "See 6.1.", "1. is printed in prose"])
def test_prose_remains_prose_until_a_label_is_recorded(text: str) -> None:
    unit = paragraph(body=text)
    assert not unit.is_labelled
    assert _comparison_source_text(unit) == text
    assert "paragraph-label" not in _unit_html(unit, text, source_view=True)


@pytest.mark.parametrize(("text", "escaped"), [
    ("2) Body", "2\\) Body"), ("17. Body", "17\\. Body"),
    ("# Printed text", "\\# Printed text"), ("> Printed text", "&gt; Printed text"),
])
def test_markdown_block_syntax_cannot_reclassify_a_plain_paragraph(text: str, escaped: str) -> None:
    assert _target_markdown(paragraph(body=text), text) == escaped


@pytest.mark.parametrize("level", [1, 2, 6, 7, 12])
def test_outline_level_is_independent_of_numbering_and_has_correct_html_semantics(level: int) -> None:
    unit = paragraph(body="Motion", label="6.10.", level=level)
    markup = _unit_html(unit, "运动", source_view=False)
    markdown = _target_markdown(unit, "运动")
    if level <= 6:
        assert markup.startswith(f"<h{level} ")
        assert markdown.startswith("#" * level + " 6.10. 运动")
    else:
        assert f'role="heading" aria-level="{level}"' in markup
        assert f'aria-level="{level}"' in markdown
    assert toc_entries([unit])[0]["title"] == "6.10. Motion"


def test_language_label_is_translated_separately_including_label_only_title() -> None:
    data = ParagraphData(type="labelled", outline_level=1, body_text="",
        label=ParagraphLabel(parts=[
            ParagraphLabelPart(part_id="word", source_text="Chapter", policy="translatable"),
            ParagraphLabelPart(part_id="number", source_text=" 6"),
        ], evidence=ParagraphLabelEvidence(page=1, source_range=(0, 9))))
    unit = paragraph(body="Chapter 6", paragraph=data)
    record = TranslationRecord(unit_id=unit.unit_id, source_hash=unit.source_hash,
                               target_text="", label_translations={"word": "章"})
    assert label_translation_errors(unit, record.label_translations) == []
    assert label_translation_errors(unit, {})
    assert label_translation_errors(unit, {"word": "章", "number": "7"})
    assert label_translation_errors(unit, {"word": " "})
    assert toc_entries([unit], {unit.unit_id: record})[0]["title"] == "章 6"
    assert _target_markdown(unit, "", record.label_translations) == "# 章 6"
    assert _comparison_source_text(unit).strip() == "Chapter"


def test_outline_jumps_are_kept_and_sections_do_not_form_an_atomic_chapter() -> None:
    units = bind_sections([paragraph("chapter", level=1), paragraph("body"),
                           paragraph("deep", level=8), paragraph("later")])
    rows = outline(units)
    assert rows[1]["level"] == 8 and rows[1]["level_jump"]
    assert rows[1]["parent_id"] == "chapter"
    assert [u.section_id for u in units] == ["chapter", "chapter", "deep", "deep"]
    assert relation_neighbors(units) == {}
    assert structure_errors(units) == []


def test_item_contains_new_paragraphs_equations_and_nested_items() -> None:
    head = paragraph("item", label="(ii)", item_id="item")
    prose = paragraph("prose", item_id="item", parent_id="theorem", container_role="body")
    nested = paragraph("nested", label="☞", item_id="nested", parent_item_id="item")
    theorem = paragraph("theorem", parent_id="theorem", container_role="theorem")
    equation = SourceUnit(unit_id="formula", kind="equation", page=1, bbox=(10, 40, 100, 60),
                          source_text="x=y", source_hash="formula", confidence=1, item_id="item")
    units = [theorem, head, prose, equation, nested]
    assert structure_errors(units) == []
    neighbors = relation_neighbors(units)
    assert {"prose", "formula", "nested"} <= neighbors["item"]
    assert structure_errors([head, prose.model_copy(update={"item_id": "missing"})])
    assert structure_errors([head, nested.model_copy(update={"parent_item_id": "nested"})])


def test_cross_page_title_owns_one_label_and_toc_entry() -> None:
    head = paragraph("head", body="Brownian", label="6.1.", level=2)
    continuation = paragraph("continued", body="Motion", page=2, paragraph_id="head",
        paragraph=ParagraphData(type="numbered", outline_level=2, body_text="Motion"))
    units = bind_sections([head, continuation])
    assert structure_errors(units) == []
    assert _continues_paragraph(head, continuation)
    assert toc_entries(units)[0]["title"] == "6.1. Brownian Motion"
    assert len(toc_entries(units)) == 1
    assert "6.1." not in _unit_html(continuation, "运动", source_view=False)
    assert not _target_markdown(continuation, "运动").startswith("#")


def test_continuation_uses_identity_and_not_punctuation_or_flags() -> None:
    first = paragraph("first", body="A complete sentence.", continued_to_next=True)
    distinct = paragraph("distinct", body="another paragraph", continues_from_previous=True)
    assert not _continues_paragraph(first, distinct)
    same = distinct.model_copy(update={"paragraph_id": first.unit_id})
    assert _continues_paragraph(first, same)


def test_layout_records_hanging_indent_and_expands_label_area() -> None:
    unit = paragraph(label="(ii)")
    assert unit.paragraph
    unit.paragraph.layout = ParagraphLayout(left_indent_pt=24, first_line_offset_pt=-24,
        label_indent_pt=0, label_alignment="right", label_gap_pt=4, font_size_pt=12)
    markup = _unit_html(unit, "正文", source_view=False)
    assert "grid-template-columns:max-content minmax(0,1fr)" in markup
    assert "margin-left:0em" in markup
    assert "min-width:1.66667em" in markup
    assert "<p " in _target_markdown(unit, "正文")


def test_invalid_label_partition_and_nonpositive_outline_are_rejected() -> None:
    with pytest.raises(ValidationError, match="partition"):
        paragraph(body="Body", label="(ii)",
                  paragraph=ParagraphData(type="numbered", body_text="(ii) Body",
                    label=ParagraphLabel(parts=[ParagraphLabelPart(part_id="n", source_text="(ii)")],
                      evidence=ParagraphLabelEvidence(page=1, source_range=(0, 4)))))
    for level in [0, -1, True]:
        with pytest.raises(ValidationError):
            ParagraphData(body_text="Title", outline_level=level)
    with pytest.raises(ValidationError):
        SourceUnit(unit_id="old", kind="list_item", page=1, bbox=(0, 0, 1, 1),
                   source_text="(ii) Body", source_hash="old", confidence=1)


def test_structure_and_label_translations_change_their_fingerprints() -> None:
    unit = paragraph(label="(ii)")
    assert unit.paragraph
    changed = unit.model_copy(update={"paragraph": unit.paragraph.model_copy(update={"outline_level": 3})})
    assert source_unit_fingerprint(changed) != source_unit_fingerprint(unit)
    assert structure_unit_fingerprint(changed) != structure_unit_fingerprint(unit)
    record = TranslationRecord(unit_id="p1", source_hash=unit.source_hash, target_text="正文")
    altered = record.model_copy(update={"label_translations": {"word": "条件"}})
    assert translation_unit_fingerprint(unit, record) != translation_unit_fingerprint(unit, altered)


def old_workspace(tmp_path: Path) -> Path:
    pdf = tmp_path / "old.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Chapter 6")
        doc.save(pdf)
    root = tmp_path / "old"
    initialize_project(pdf, root, "technical-book")
    manifest = read_yaml(root / "project.yaml")
    manifest["schema_version"] = 7
    write_yaml(root / "project.yaml", manifest)
    raw = paragraph(body="Chapter 6").model_dump(mode="json")
    for field in ("paragraph", "paragraph_id", "item_id", "parent_item_id", "container_role", "section_id"):
        raw.pop(field)
    raw.update(kind="heading", schema_version=3)
    (root / "derived/units.jsonl").write_text(json.dumps(raw) + "\n", encoding="utf-8")
    record = TranslationRecord(unit_id="p1", source_hash="historical", target_text="第6章",
                               status=ProjectStatus.HUMAN_APPROVED).model_dump(mode="json")
    record.update(schema_version=2)
    record.pop("label_translations")
    (root / "translations/current.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    write_json(root / "evidence/approval.json", {"approved": True})
    return root


def test_upgrade_dry_run_preserves_old_and_does_not_create_destination(tmp_path: Path) -> None:
    old = old_workspace(tmp_path)
    before = (old / "project.yaml").read_bytes()
    new = tmp_path / "new"
    result = upgrade_project(old, new, dry_run=True)
    assert result["pending_paragraph_unit_ids"] == ["p1"]
    assert not new.exists()
    assert (old / "project.yaml").read_bytes() == before


def test_upgrade_preserves_raw_translation_and_archives_approval_without_inventing_level(tmp_path: Path) -> None:
    old = old_workspace(tmp_path)
    new = tmp_path / "new"
    upgrade_project(old, new)
    unit = read_jsonl(new / "derived/units.jsonl", SourceUnit)[0]
    record = read_jsonl(new / "translations/current.jsonl", TranslationRecord)[0]
    assert not unit.is_heading and unit.verification_status == "unverified"
    assert record.target_text == "第6章" and record.source_hash == "historical"
    assert record.status == "draft" and record.uncertainties
    assert not (new / "evidence/approval.json").exists()
    assert (new / "history/paragraph-upgrade-v7/evidence/approval.json").exists()
    assert (old / "evidence/approval.json").exists()
    with pytest.raises(ValueError, match="non-existing"):
        upgrade_project(old, new)


def test_upgrade_uses_explicit_outline_evidence_and_rolls_back_failed_validation(tmp_path: Path, monkeypatch) -> None:
    old = old_workspace(tmp_path)
    write_json(old / "derived/document.json", {"outline": [{"title": "Chapter 6", "page": 1, "level": 3}]})
    new = tmp_path / "new"
    import littrans.storage as storage
    original = storage.load_project
    def fail(path):
        raise ValueError("synthetic validation failure")
    monkeypatch.setattr(storage, "load_project", fail)
    with pytest.raises(ValueError, match="synthetic"):
        upgrade_project(old, new)
    assert not new.exists()
    assert not list(tmp_path.glob(".littrans-upgrade-*"))
    monkeypatch.setattr(storage, "load_project", original)
    upgrade_project(old, new)
    assert read_jsonl(new / "derived/units.jsonl", SourceUnit)[0].paragraph.outline_level == 3
