from __future__ import annotations

from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from littrans.batching import create_batches, refresh_batch
from littrans.extractor import apply_layout_overrides, extract_source, inspect_source
from littrans.migration import migrate_translations
from littrans.models import (
    IssueStatus,
    IssueType,
    ProjectStatus,
    ReviewIssue,
    Severity,
    SourceUnit,
    TranslationRecord,
    UnitKind,
)
from littrans.project import initialize_project, translation_map
from littrans.quality import (
    NUMBER_RE,
    UNIT_RE,
    _semantic_comparison_text,
    _token_counts,
    approve_batch,
    import_review,
    resolve_issue,
    run_qa,
)
from littrans.rendering import render_project
from littrans.storage import read_jsonl, sha256_text, write_jsonl, write_yaml
from littrans.translation import submit_translation
from littrans.verification import verify_extraction


def make_pdf(path: Path) -> None:
    pdf = canvas.Canvas(str(path), pagesize=letter)
    width, height = letter
    for page in range(1, 4):
        pdf.setFont("Helvetica", 8)
        pdf.drawString(72, height - 30, "Synthetic Technical Document")
        pdf.setFont("Helvetica-Bold", 18)
        pdf.drawString(72, height - 100, f"Section {page}")
        pdf.setFont("Helvetica", 11)
        pdf.drawString(
            72, height - 135, "DependencyObject processes 25 ms events through API version 4.5."
        )
        pdf.drawString(72, height - 155, "The result is preserved for reference [1].")
        pdf.drawString(
            72,
            height - 170,
            "Developers spent years using the same display technology.",
        )
        pdf.setFont("Courier", 9)
        pdf.drawString(82, height - 190, '<Grid Name="RootGrid">')
        pdf.drawString(82, height - 205, "</Grid>")
        pdf.setFont("Helvetica", 11)
        pdf.drawCentredString(width / 2, height - 235, "a = b + 3")
        pdf.setFont("Helvetica-Oblique", 9)
        pdf.drawString(72, 30, f"Page {page}")
        pdf.showPage()
    pdf.save()


def test_scaled_number_normalization_handles_chinese_readable_forms() -> None:
    source = _semantic_comparison_text("2 million particles")
    target = _semantic_comparison_text("200 万个粒子")
    assert _token_counts(NUMBER_RE, source) == _token_counts(NUMBER_RE, target)


@pytest.fixture()
def prepared_project(tmp_path: Path) -> Path:
    source = tmp_path / "synthetic.pdf"
    root = tmp_path / "project"
    make_pdf(source)
    initialize_project(source, root, "technical-book", "Synthetic")
    inspect_source(root, "1-3")
    units = extract_source(root, "1-3")
    write_yaml(
        root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unit.unit_id,
                    "latex": "a = b + 3",
                    "verified": True,
                    "reason": "Compared with the generated PDF equation.",
                }
                for unit in units
                if unit.kind == "equation"
            ]
        },
    )
    apply_layout_overrides(root)
    assert verify_extraction(root, "1-3")["passed"]
    return root


def test_extraction_is_stable_and_preserves_structure(prepared_project: Path) -> None:
    first = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    second = extract_source(prepared_project, "1-3", replace=True)
    assert [(unit.unit_id, unit.source_hash) for unit in first] == [
        (unit.unit_id, unit.source_hash) for unit in second
    ]
    assert any(unit.kind == "heading" for unit in first)
    assert any(unit.kind == "code" and not unit.translatable for unit in first)
    assert any(
        "years using" in unit.source_text and unit.kind == "paragraph" and unit.translatable
        for unit in first
    )
    assert any(unit.kind == "equation" and unit.asset_refs and unit.latex for unit in first)
    assert all("Synthetic Technical Document" not in unit.source_text for unit in first)


def test_reviewed_protected_token_override_can_correct_a_source_typo(
    prepared_project: Path,
) -> None:
    unit = next(
        item
        for item in read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
        if item.translatable
    )
    write_yaml(
        prepared_project / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unit.unit_id,
                    "protected_tokens": ["CorrectedApiName"],
                    "verified": True,
                    "reason": "Verified source typo and official API spelling.",
                }
            ]
        },
    )
    revised = apply_layout_overrides(prepared_project)
    updated = next(item for item in revised if item.unit_id == unit.unit_id)
    assert updated.source_text == unit.source_text
    assert updated.protected_tokens == ["CorrectedApiName"]


def _submit_identity_translations(root: Path, batch_id: str) -> None:
    manifest_path = root / "batches" / batch_id / "manifest.yaml"
    import yaml

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    units = {
        unit.unit_id: unit for unit in read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    }
    records = [
        TranslationRecord(
            unit_id=unit_id,
            target_text=f"译文：{units[unit_id].source_text}",
            source_hash=units[unit_id].source_hash,
        )
        for unit_id in manifest["translatable_unit_ids"]
    ]
    input_path = root / "batches" / batch_id / "agent-output.jsonl"
    write_jsonl(input_path, records)
    submit_translation(root, batch_id, input_path)


def test_end_to_end_gate_and_render(prepared_project: Path) -> None:
    manifests = create_batches(prepared_project, "1-3", max_words=300, prefix="synthetic")
    assert manifests
    for manifest in manifests:
        _submit_identity_translations(prepared_project, manifest.batch_id)
        report = run_qa(prepared_project, manifest.batch_id)
        assert report.passed, report.errors
        empty_review = prepared_project / "reviews" / f"{manifest.batch_id}.input.jsonl"
        empty_review.write_text("", encoding="utf-8")
        import_review(prepared_project, manifest.batch_id, empty_review)
        assert (
            approve_batch(prepared_project, manifest.batch_id, "machine")
            == ProjectStatus.MACHINE_REVIEWED
        )

    outputs = render_project(prepared_project, "1-3", "synthetic")
    assert Path(outputs["markdown"]).is_file()
    html = Path(outputs["html"]).read_text(encoding="utf-8")
    assert "双语译本" in html
    assert "DependencyObject" in html
    assert "machine-reviewed" in html
    assert '<math xmlns="http://www.w3.org/1998/Math/MathML"' in html
    assert 'class="language-xaml"' in html
    assert '<span class="nt">&lt;Grid</span>' in html
    assert '<Grid Name="RootGrid">' not in html
    markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
    assert "```xaml" in markdown
    assert "$$\na = b + 3" in markdown


def test_qa_rejects_protected_token_and_human_gate(prepared_project: Path) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="qa")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    current = translation_map(prepared_project)
    target = next(
        unit_id
        for unit_id in manifest.translatable_unit_ids
        if "DependencyObject" in current[unit_id].target_text
    )
    current[target] = current[target].model_copy(
        update={"target_text": current[target].target_text.replace("DependencyObject", "依赖对象")}
    )
    write_jsonl(prepared_project / "translations" / "current.jsonl", current.values())
    report = run_qa(prepared_project, manifest.batch_id)
    assert not report.passed
    assert any(item.code == "protected-token-missing" for item in report.errors)
    with pytest.raises(ValueError, match="passing QA"):
        approve_batch(prepared_project, manifest.batch_id, "human", confirm_user_approved=True)


def test_latex_aware_numeric_and_identifier_comparison() -> None:
    semantic = _semantic_comparison_text(
        r"$T_L$; $L = 0.07\,\mathrm{m}$; $10^{-5}\,\mathrm{s}$; $SF_6$"
    )
    assert "TL" in semantic
    assert "0.07 m" in semantic
    assert "10^-5 s" in semantic
    assert "SF6" in semantic
    assert _token_counts(UNIT_RE, semantic)["10^-5s"] == 1
    assert "5s" not in _token_counts(UNIT_RE, semantic)
    assert _token_counts(NUMBER_RE, semantic)["10^-5"] == 1


def test_human_approval_requires_explicit_confirmation(prepared_project: Path) -> None:
    manifest = create_batches(prepared_project, "2", max_words=300, prefix="human")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    review = prepared_project / "reviews" / "empty.jsonl"
    review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, review)
    with pytest.raises(ValueError, match="explicit user confirmation"):
        approve_batch(prepared_project, manifest.batch_id, "human")
    assert (
        approve_batch(prepared_project, manifest.batch_id, "human", confirm_user_approved=True)
        == ProjectStatus.HUMAN_APPROVED
    )


def test_revision_invalidates_prior_audit(prepared_project: Path) -> None:
    manifest = create_batches(prepared_project, "3", max_words=300, prefix="stale")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    review = prepared_project / "reviews" / "stale-empty.jsonl"
    review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, review)

    current = translation_map(prepared_project)
    unit_id = manifest.translatable_unit_ids[0]
    current[unit_id] = current[unit_id].model_copy(
        update={
            "target_text": current[unit_id].target_text + " 修订",
            "revision": current[unit_id].revision + 1,
        }
    )
    write_jsonl(prepared_project / "translations" / "current.jsonl", current.values())
    assert run_qa(prepared_project, manifest.batch_id).passed
    with pytest.raises(ValueError, match="audit is stale"):
        approve_batch(prepared_project, manifest.batch_id, "machine")
    import_review(prepared_project, manifest.batch_id, review)
    assert (
        approve_batch(prepared_project, manifest.batch_id, "machine")
        == ProjectStatus.MACHINE_REVIEWED
    )


def test_nontranslatable_source_revision_invalidates_prior_audit(
    prepared_project: Path,
) -> None:
    manifest = create_batches(prepared_project, "3", max_words=300, prefix="source-stale")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    review = prepared_project / "reviews" / "source-stale-empty.jsonl"
    review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, review)

    units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    code = next(
        unit
        for unit in units
        if unit.kind is UnitKind.CODE and unit.unit_id in manifest.unit_ids
    )
    corrected = code.source_text.replace("</Grid>", "  </Grid>")
    units = [
        unit.model_copy(
            update={"source_text": corrected, "source_hash": sha256_text(corrected)}
        )
        if unit.unit_id == code.unit_id
        else unit
        for unit in units
    ]
    write_jsonl(prepared_project / "derived" / "units.jsonl", units)
    assert run_qa(prepared_project, manifest.batch_id).passed
    with pytest.raises(ValueError, match="audit is stale"):
        approve_batch(prepared_project, manifest.batch_id, "machine")


def test_batch_refresh_and_review_history_are_safe(prepared_project: Path) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="refresh")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    unit_id = manifest.translatable_unit_ids[0]
    issue = ReviewIssue(
        issue_id="refresh-r001",
        batch_id=manifest.batch_id,
        unit_id=unit_id,
        severity=Severity.MINOR,
        type=IssueType.STYLE,
        explanation="Synthetic issue",
        reviewer="test",
    )
    issue_input = prepared_project / "reviews" / "refresh-input.jsonl"
    write_jsonl(issue_input, [issue])
    import_review(prepared_project, manifest.batch_id, issue_input)
    resolve_issue(
        prepared_project,
        manifest.batch_id,
        issue.issue_id,
        IssueStatus.RESOLVED,
        "Fixed in revision",
    )
    empty = prepared_project / "reviews" / "refresh-empty.jsonl"
    empty.write_text("", encoding="utf-8")
    merged = import_review(prepared_project, manifest.batch_id, empty)
    assert merged[0].status is IssueStatus.RESOLVED

    units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    units = [
        unit.model_copy(update={"translatable": False}) if unit.unit_id == unit_id else unit
        for unit in units
    ]
    write_jsonl(prepared_project / "derived" / "units.jsonl", units)
    refreshed = refresh_batch(prepared_project, manifest.batch_id)
    assert unit_id not in refreshed.translatable_unit_ids
    assert unit_id not in translation_map(prepared_project)


def test_migration_never_carries_approval(prepared_project: Path) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="migrate")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    target_root = prepared_project.parent / "migrated"
    source_pdf = prepared_project.parent / "synthetic.pdf"
    initialize_project(source_pdf, target_root, "technical-book", "Migrated")
    units = extract_source(target_root, "1")
    write_yaml(
        target_root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unit.unit_id,
                    "latex": "a = b + 3",
                    "verified": True,
                    "reason": "Compared with the generated PDF equation.",
                }
                for unit in units
                if unit.kind == "equation"
            ]
        },
    )
    apply_layout_overrides(target_root)
    report = migrate_translations(prepared_project, target_root, "1")
    assert report["migrated"] == len(manifest.translatable_unit_ids)
    assert all(
        record.status is ProjectStatus.DRAFT
        for record in translation_map(target_root).values()
    )
