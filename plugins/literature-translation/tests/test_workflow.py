from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import littrans.external_review as external_review
import littrans.extractor as extractor_module
import littrans.quality as quality_module
import littrans.rendering as rendering_module
import littrans.storage as storage_module
from littrans.batching import create_batches, load_manifest, refresh_batch
from littrans.evidence import (
    batch_source_fingerprint,
    batch_structure_fingerprint,
    batch_unit_fingerprints,
)
from littrans.external_review import (
    PROMPT_VERSION,
    _antigravity_prompt,
    _claude_prompt,
    _evidence_map,
    _packet_text,
    _parse_antigravity,
    _parse_claude,
    _release_reviewer_reservation,
    _require_machine_reviewed,
    _reserve_reviewer,
    _select_reviewer,
    _validate_issue_evidence,
    build_antigravity_command,
    build_claude_command,
    external_review_status,
    external_reviewer_usage,
)
from littrans.extractor import (
    _callout_kind,
    _is_caption,
    apply_layout_overrides,
    extract_source,
    inspect_source,
    protected_tokens,
)
from littrans.migration import migrate_translations
from littrans.models import (
    CalloutKind,
    ExternalReviewConfig,
    ExternalReviewerConfig,
    ExternalReviewRun,
    ExternalReviewVerdict,
    ExtractionIssue,
    FigureLabel,
    IssueStatus,
    IssueType,
    ProjectStatus,
    PromptDelivery,
    ReaderNote,
    RenderPolicy,
    ReviewIssue,
    ReviewScope,
    SemanticStatus,
    Severity,
    SidebarRole,
    SourceUnit,
    TableData,
    TranslationRecord,
    UnitKind,
)
from littrans.project import initialize_project, translation_map
from littrans.quality import (
    NUMBER_RE,
    UNIT_RE,
    _comparison_source_text,
    _semantic_comparison_text,
    _target_structure_error,
    _token_counts,
    approve_batch,
    batch_translation_fingerprint,
    import_review,
    resolve_issue,
    review_status,
    run_qa,
)
from littrans.rendering import (
    _continuation_separator,
    _continued_sidebar_markdown,
    _merge_continued_sidebar_html,
    _render_quality_errors,
    _render_target_text,
    _target_markdown,
    _unit_html,
    default_batch_output_name,
    render_project,
)
from littrans.semantics import (
    normalize_zh_caption,
    normalize_zh_figure_caption,
    normalize_zh_table_caption,
)
from littrans.storage import (
    append_jsonl,
    load_project,
    read_jsonl,
    save_project,
    sha256_text,
    write_jsonl,
    write_yaml,
)
from littrans.translation import submit_translation
from littrans.verification import (
    _semantic_context_units,
    _semantic_errors,
    verify_extraction,
)
from littrans.workflow import _audit_unit_text


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


def test_atomic_write_retries_transient_permission_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "atomic.txt"
    real_replace = storage_module.os.replace
    attempts = 0

    def flaky_replace(source: str | Path, destination: str | Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("temporarily locked")
        real_replace(source, destination)

    monkeypatch.setattr(storage_module.os, "replace", flaky_replace)
    monkeypatch.setattr(storage_module.time, "sleep", lambda seconds: None)

    storage_module.atomic_write_text(path, "durable\n")

    assert attempts == 3
    assert path.read_text(encoding="utf-8") == "durable\n"


def test_scaled_number_normalization_handles_chinese_readable_forms() -> None:
    source = _semantic_comparison_text("2 million particles")
    target = _semantic_comparison_text("200 万个粒子")
    assert _token_counts(NUMBER_RE, source) == _token_counts(NUMBER_RE, target)


@pytest.mark.parametrize(
    ("source_text", "target_text"),
    [("2-D drawing", "二维绘图"), ("3-D content", "三维内容")],
)
def test_dimension_terms_preserve_numeric_semantics(
    source_text: str, target_text: str
) -> None:
    source = _semantic_comparison_text(source_text)
    target = _semantic_comparison_text(target_text)
    assert _token_counts(NUMBER_RE, source) == _token_counts(NUMBER_RE, target)


def test_caption_detection_requires_caption_punctuation() -> None:
    assert _is_caption("Figure 3-2. The StackPanel in action")
    assert _is_caption("Table 3-3. Layout Properties")
    assert _is_caption("Figure 4 Velocity profiles")
    assert not _is_caption("Figure 3-2 shows the window that results.")
    assert not _is_caption("Table 3-3 lists the layout properties.")


def test_protected_urls_exclude_trailing_sentence_punctuation() -> None:
    text = (
        "Download it from http://tinyurl.com/8ea7r43. "
        "See https://example.com/reference, then continue."
    )
    tokens = protected_tokens(text)
    assert "http://tinyurl.com/8ea7r43" in tokens
    assert "https://example.com/reference" in tokens
    assert "http://tinyurl.com/8ea7r43." not in tokens
    assert "https://example.com/reference," not in tokens


def test_continuation_separator_does_not_split_hyphenated_urls() -> None:
    assert _continuation_separator("http://shazzam-", "tool.com") == ""
    assert _continuation_separator("ordinary", "words") == " "


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("图 9-5。使用命令的菜单项", "图 9-5 使用命令的菜单项"),
        ("图 1-2：WPF 的架构", "图 1-2 WPF 的架构"),
        ("图 3 - 12   对话框", "图 3-12 对话框"),
        ("图 4速度曲线", "图 4 速度曲线"),
        ("表 3-3。布局属性", "表 3-3。布局属性"),
        ("Figure 3-2. The StackPanel in action", "Figure 3-2. The StackPanel in action"),
    ],
)
def test_chinese_figure_caption_separator_is_normalized(
    raw: str, expected: str
) -> None:
    assert normalize_zh_figure_caption(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("表 12-1。Shape 属性", "表 12-1 Shape 属性"),
        ("表 3-3：布局属性", "表 3-3 布局属性"),
        ("表 4 - 2   画刷属性", "表 4-2 画刷属性"),
        ("表 5值列表", "表 5 值列表"),
        ("图 3-3。布局示例", "图 3-3。布局示例"),
        ("Table 3-3. Layout Properties", "Table 3-3. Layout Properties"),
    ],
)
def test_chinese_table_caption_separator_is_normalized(
    raw: str, expected: str
) -> None:
    assert normalize_zh_table_caption(raw) == expected


def test_chinese_caption_normalizer_handles_figures_and_tables() -> None:
    assert normalize_zh_caption("图 1-2。架构") == "图 1-2 架构"
    assert normalize_zh_caption("表 1-2。属性") == "表 1-2 属性"


def test_only_caption_target_views_use_the_chinese_caption_separator() -> None:
    source = "Figure 11-5. Actions in the Asset Library"
    caption = SourceUnit(
        unit_id="p0001-u001-caption",
        kind=UnitKind.CAPTION,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text=source,
        source_hash=sha256_text(source),
        confidence=1,
    )
    raw_target = "图 11-5。资源库中的操作"
    normalized = _render_target_text(caption, raw_target)
    assert normalized == "图 11-5 资源库中的操作"
    assert _target_markdown(caption, normalized) == "*图 11-5 资源库中的操作*"
    assert _unit_html(caption, normalized, source_view=False) == (
        "<figcaption>图 11-5 资源库中的操作</figcaption>"
    )
    assert _unit_html(caption, source, source_view=True) == (
        f"<figcaption>{source}</figcaption>"
    )

    table_target = "表 11-5。资源属性"
    normalized_table = _render_target_text(caption, table_target)
    assert normalized_table == "表 11-5 资源属性"
    assert _target_markdown(caption, normalized_table) == "*表 11-5 资源属性*"
    assert _unit_html(caption, normalized_table, source_view=False) == (
        "<figcaption>表 11-5 资源属性</figcaption>"
    )

    paragraph = caption.model_copy(update={"kind": UnitKind.PARAGRAPH})
    assert _render_target_text(paragraph, raw_target) == raw_target


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


def test_equation_asset_failure_does_not_replace_authoritative_units(
    prepared_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    units_path = prepared_project / "derived" / "units.jsonl"
    before = units_path.read_bytes()
    unit = next(
        item
        for item in read_jsonl(units_path, SourceUnit)
        if item.kind is UnitKind.PARAGRAPH
    )
    write_yaml(
        prepared_project / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unit.unit_id,
                    "kind": "equation",
                    "latex": "a=b",
                    "verified": True,
                    "reason": "Verified display equation against the PDF.",
                }
            ]
        },
    )
    real_replace = Path.replace

    def fail_asset_replace(source: Path, destination: Path) -> Path:
        if "equation-override" in destination.name:
            raise PermissionError("seeded asset replace failure")
        return real_replace(source, destination)

    monkeypatch.setattr(Path, "replace", fail_asset_replace)
    with pytest.raises(PermissionError, match="seeded asset replace failure"):
        apply_layout_overrides(prepared_project)

    assert units_path.read_bytes() == before


def test_late_override_interruption_restores_ledgers_and_published_asset(
    prepared_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    units_path = prepared_project / "derived" / "units.jsonl"
    translations_path = prepared_project / "translations" / "current.jsonl"
    issues_path = prepared_project / "derived" / "extraction-issues.jsonl"
    project_path = prepared_project / "project.yaml"
    unit = next(
        item
        for item in read_jsonl(units_path, SourceUnit)
        if item.kind is UnitKind.PARAGRAPH and item.translatable
    )
    write_jsonl(
        translations_path,
        [
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text="事务回滚测试译文。",
                source_hash=unit.source_hash,
                status=ProjectStatus.EXTERNAL_REVIEWED,
            )
        ],
    )
    config = load_project(prepared_project)
    config.status = ProjectStatus.EXTERNAL_REVIEWED
    save_project(prepared_project, config)
    write_jsonl(
        issues_path,
        [
            ExtractionIssue(
                issue_id="layout-transaction",
                page=unit.page,
                unit_id=unit.unit_id,
                severity=Severity.MINOR,
                code="math-needs-verification",
                message="Verify the reclassified display equation.",
            )
        ],
    )
    write_yaml(
        prepared_project / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unit.unit_id,
                    "kind": "equation",
                    "latex": "a=b",
                    "verified": True,
                    "reason": "Verified display equation against the PDF.",
                }
            ]
        },
    )
    asset_path = (
        prepared_project
        / "derived"
        / "assets"
        / f"page-{unit.page:04}-equation-override-{unit.unit_id}.png"
    )
    tracked = [units_path, translations_path, project_path, issues_path]
    before = {path: path.read_bytes() for path in tracked}
    assert not asset_path.exists()
    original_write_jsonl = extractor_module.write_jsonl

    def interrupt_issue_write(path: Path, records: object) -> None:
        if path == issues_path:
            raise KeyboardInterrupt("seeded late override interruption")
        original_write_jsonl(path, records)  # type: ignore[arg-type]

    monkeypatch.setattr(extractor_module, "write_jsonl", interrupt_issue_write)
    with pytest.raises(KeyboardInterrupt, match="seeded late override interruption"):
        apply_layout_overrides(prepared_project)

    assert {path: path.read_bytes() for path in tracked} == before
    assert not asset_path.exists()


def test_duplicate_unit_overrides_compose_in_file_order(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "derived").mkdir()
    (root / "overrides").mkdir()
    unit = SourceUnit(
        unit_id="p0001-u001-sidebar",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text="SIDEBAR",
        source_hash=sha256_text("SIDEBAR"),
        confidence=1,
    )
    write_jsonl(root / "derived" / "units.jsonl", [unit])
    write_yaml(
        root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unit.unit_id,
                    "kind": "heading",
                    "verified": True,
                    "reason": "Verified title.",
                },
                {
                    "unit_id": unit.unit_id,
                    "sidebar_id": "p0001-sidebar-001",
                    "sidebar_role": "title",
                    "verified": True,
                    "reason": "Verified sidebar membership.",
                },
            ]
        },
    )

    updated = apply_layout_overrides(root)[0]
    assert updated.kind is UnitKind.HEADING
    assert updated.sidebar_id == "p0001-sidebar-001"
    assert updated.sidebar_role is SidebarRole.TITLE


def test_layout_override_can_insert_a_verified_unit_idempotently(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "derived").mkdir()
    (root / "overrides").mkdir()
    first = SourceUnit(
        unit_id="p0001-u001-first",
        kind=UnitKind.TABLE,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text="Name | Description",
        source_hash=sha256_text("Name | Description"),
        confidence=1,
    )
    second = SourceUnit(
        unit_id="p0001-u002-second",
        kind=UnitKind.NOTE,
        page=1,
        bbox=(0, 20, 10, 30),
        source_text="NOTE A note.",
        source_hash=sha256_text("NOTE A note."),
        confidence=1,
    )
    write_jsonl(root / "derived" / "units.jsonl", [first, second])
    inserted_id = "p0001-u001-inserted"
    write_yaml(
        root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": inserted_id,
                    "insert_after": first.unit_id,
                    "kind": "paragraph",
                    "page": 1,
                    "bbox": [0, 11, 10, 19],
                    "source_text": "A paragraph recovered from a table region.",
                    "verified": True,
                    "reason": "Compared with the source PDF.",
                }
            ]
        },
    )

    first_pass = apply_layout_overrides(root)
    second_pass = apply_layout_overrides(root)
    assert [unit.unit_id for unit in first_pass] == [first.unit_id, inserted_id, second.unit_id]
    assert [unit.unit_id for unit in second_pass] == [first.unit_id, inserted_id, second.unit_id]
    inserted = second_pass[1]
    assert inserted.verification_status.value == "verified"
    assert inserted.source_hash == sha256_text(inserted.source_text)


def test_bbox_selector_is_not_a_bbox_update(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "derived").mkdir()
    (root / "overrides").mkdir()
    unit = SourceUnit(
        unit_id="p0001-u001-box",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 20, 20),
        source_text="Body text.",
        source_hash=sha256_text("Body text."),
        confidence=1,
    )
    write_jsonl(root / "derived" / "units.jsonl", [unit])
    write_yaml(
        root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "bbox": [0, 0, 10, 10],
                    "verified": True,
                    "reason": "Select the overlapping source region.",
                }
            ]
        },
    )
    assert apply_layout_overrides(root)[0].bbox == unit.bbox

    write_yaml(
        root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unit.unit_id,
                    "set_bbox": [1, 2, 19, 18],
                    "verified": True,
                    "reason": "Compared the exact region with the source PDF.",
                }
            ]
        },
    )
    assert apply_layout_overrides(root)[0].bbox == (1, 2, 19, 18)


def test_unit_specific_override_does_not_match_other_units_by_bbox(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "derived").mkdir()
    (root / "overrides").mkdir()
    (root / "translations").mkdir()
    first = SourceUnit(
        unit_id="p0001-u001-first",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 20, 20),
        source_text="Old text.",
        source_hash=sha256_text("Old text."),
        confidence=1,
    )
    second = first.model_copy(
        update={
            "unit_id": "p0002-u001-second",
            "page": 2,
            "source_text": "Other text.",
            "source_hash": sha256_text("Other text."),
        }
    )
    corrected = "Corrected text."
    write_jsonl(root / "derived" / "units.jsonl", [first, second])
    write_jsonl(
        root / "translations" / "current.jsonl",
        [
            TranslationRecord(
                unit_id=first.unit_id,
                target_text="修正后的译文。",
                source_hash=sha256_text(corrected),
            )
        ],
    )
    write_yaml(
        root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": first.unit_id,
                    "bbox": [0, 0, 20, 20],
                    "source_text": corrected,
                    "verified": True,
                    "reason": "Compared with the source PDF.",
                }
            ]
        },
    )

    revised = apply_layout_overrides(root)
    assert revised[0].source_text == corrected
    assert revised[1].source_text == "Other text."
    assert read_jsonl(root / "translations" / "current.jsonl", TranslationRecord)


def test_generic_and_unit_specific_overrides_compose_in_file_order(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "derived").mkdir()
    (root / "overrides").mkdir()
    unit = SourceUnit(
        unit_id="p0082-u001-running-header",
        kind=UnitKind.PARAGRAPH,
        page=82,
        bbox=(0, 0, 10, 10),
        source_text="CHAPTER 2 ■ XAML",
        source_hash=sha256_text("CHAPTER 2 ■ XAML"),
        confidence=1,
        continued_to_next=True,
    )
    write_jsonl(root / "derived" / "units.jsonl", [unit])
    write_yaml(
        root / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "page": 82,
                    "current_kind": "paragraph",
                    "text_regex": r"^CHAPTER 2",
                    "render_policy": "omit",
                    "verified": True,
                    "reason": "Verified running header.",
                },
                {
                    "unit_id": unit.unit_id,
                    "continued_to_next": False,
                    "verified": True,
                    "reason": "Running headers do not continue into body prose.",
                },
            ]
        },
    )

    updated = apply_layout_overrides(root)[0]
    assert updated.render_policy is RenderPolicy.OMIT
    assert updated.translatable is False
    assert updated.continued_to_next is False


def test_layout_overrides_only_invalidate_affected_translations(
    prepared_project: Path,
) -> None:
    units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    affected = next(
        unit for unit in units if unit.translatable and unit.kind is UnitKind.PARAGRAPH
    )
    unaffected = next(
        unit for unit in units if unit.translatable and unit.unit_id != affected.unit_id
    )
    translated_units = [affected, unaffected]
    records = [
        TranslationRecord(
            unit_id=unit.unit_id,
            target_text=f"译文：{unit.source_text}",
            source_hash=unit.source_hash,
            status=ProjectStatus.EXTERNAL_REVIEWED,
        )
        for unit in translated_units
    ]
    write_jsonl(prepared_project / "translations" / "current.jsonl", records)
    config = load_project(prepared_project)
    config.status = ProjectStatus.EXTERNAL_REVIEWED
    save_project(prepared_project, config)

    untranslated = next(unit for unit in units if not unit.translatable)
    write_yaml(
        prepared_project / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": untranslated.unit_id,
                    "verified": True,
                    "reason": "Visually verified a source-only structural unit.",
                }
            ]
        },
    )
    apply_layout_overrides(prepared_project)
    assert load_project(prepared_project).status is ProjectStatus.EXTERNAL_REVIEWED
    assert all(
        record.status is ProjectStatus.EXTERNAL_REVIEWED
        for record in read_jsonl(
            prepared_project / "translations" / "current.jsonl", TranslationRecord
        )
    )

    write_yaml(
        prepared_project / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": affected.unit_id,
                    "kind": "heading",
                    "verified": True,
                    "reason": "Visually verified this unit as a heading.",
                }
            ]
        },
    )
    apply_layout_overrides(prepared_project)
    current = {
        record.unit_id: record
        for record in read_jsonl(
            prepared_project / "translations" / "current.jsonl", TranslationRecord
        )
    }
    assert current[affected.unit_id].status is ProjectStatus.DRAFT
    assert current[unaffected.unit_id].status is ProjectStatus.EXTERNAL_REVIEWED
    assert load_project(prepared_project).status is ProjectStatus.DRAFT

    write_yaml(
        prepared_project / "overrides" / "layout.yaml",
        {
            "overrides": [
                {
                    "unit_id": unaffected.unit_id,
                    "protected_tokens": ["ReviewedIdentifier"],
                    "verified": True,
                    "reason": "Verified the exact identifier against the source.",
                }
            ]
        },
    )
    apply_layout_overrides(prepared_project)
    current = {
        record.unit_id: record
        for record in read_jsonl(
            prepared_project / "translations" / "current.jsonl", TranslationRecord
        )
    }
    assert current[unaffected.unit_id].status is ProjectStatus.DRAFT


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


@pytest.mark.parametrize(
    ("raw_target", "normalized_target"),
    [
        ("图 1-1。合成架构", "图 1-1 合成架构"),
        ("表 1-1。合成属性", "表 1-1 合成属性"),
    ],
)
def test_external_review_packet_normalizes_chinese_captions(
    prepared_project: Path,
    raw_target: str,
    normalized_target: str,
) -> None:
    units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    target_unit = next(
        unit
        for unit in units
        if unit.page == 1 and unit.kind is UnitKind.PARAGRAPH and unit.translatable
    )
    units = [
        unit.model_copy(update={"kind": UnitKind.CAPTION})
        if unit.unit_id == target_unit.unit_id
        else unit
        for unit in units
    ]
    write_jsonl(prepared_project / "derived" / "units.jsonl", units)
    manifest = create_batches(
        prepared_project, "1", max_words=5000, prefix="caption-packet"
    )[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    current = read_jsonl(
        prepared_project / "translations" / "current.jsonl", TranslationRecord
    )
    current = [
        record.model_copy(update={"target_text": raw_target})
        if record.unit_id == target_unit.unit_id
        else record
        for record in current
    ]
    write_jsonl(prepared_project / "translations" / "current.jsonl", current)

    packet, _ = _packet_text(prepared_project, manifest.batch_id)
    evidence = _evidence_map(prepared_project, manifest.batch_id)
    assert normalized_target in packet
    assert raw_target not in packet
    assert normalized_target in evidence[target_unit.unit_id][1]
    assert "figure and table captions" in packet


def test_audit_packet_normalizes_chinese_caption_separator() -> None:
    unit = SourceUnit(
        unit_id="p0001-u001-caption",
        kind=UnitKind.CAPTION,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text="Figure 1-1. Architecture",
        source_hash=sha256_text("Figure 1-1. Architecture"),
        confidence=1,
    )
    record = TranslationRecord(
        unit_id=unit.unit_id,
        target_text="图 1-1　架构",
        source_hash=unit.source_hash,
    )

    packet = _audit_unit_text(unit, record)

    assert "图 1-1 架构" in packet
    assert "图 1-1　架构" not in packet


def test_audit_packet_does_not_duplicate_structured_table_rows() -> None:
    source_rows = [["Property", "Description"], ["RowStyle", "Styles rows"]]
    target_rows = [["属性", "说明"], ["`RowStyle`", "设置行的样式"]]
    source_text = "\n".join(" | ".join(row) for row in source_rows)
    unit = SourceUnit(
        unit_id="p0001-u001-table",
        kind=UnitKind.TABLE,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text=source_text,
        source_hash=sha256_text(source_text),
        table=TableData(rows=source_rows, header_rows=1, column_count=2),
        confidence=1,
    )
    record = TranslationRecord(
        unit_id=unit.unit_id,
        target_text="",
        target_table=TableData(rows=target_rows, header_rows=1, column_count=2),
        source_hash=unit.source_hash,
    )

    packet = _audit_unit_text(unit, record)

    assert packet.count("Property | Description") == 1
    assert packet.count("属性 | 说明") == 1


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


def test_reader_note_on_continued_paragraph_is_emitted_after_full_chain(
    prepared_project: Path,
) -> None:
    units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    paragraphs = [
        unit
        for unit in units
        if unit.page == 1 and unit.kind is UnitKind.PARAGRAPH and unit.translatable
    ]
    first, second = paragraphs[:2]
    revised = []
    for unit in units:
        if unit.unit_id == second.unit_id:
            unit = unit.model_copy(update={"continues_from_previous": True})
        revised.append(unit)
    write_jsonl(prepared_project / "derived" / "units.jsonl", revised)

    manifest = create_batches(
        prepared_project, "1", max_words=5000, prefix="continued-note"
    )[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    current = read_jsonl(
        prepared_project / "translations" / "current.jsonl", TranslationRecord
    )
    current = [
        record.model_copy(
            update={
                "reader_note": ReaderNote(
                    text="读者注：The source contains a documented technical error.",
                )
            }
        )
        if record.unit_id == first.unit_id
        else record
        for record in current
    ]
    write_jsonl(prepared_project / "translations" / "current.jsonl", current)
    assert run_qa(prepared_project, manifest.batch_id).passed
    empty_review = prepared_project / "reviews" / "continued-note.input.jsonl"
    empty_review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, empty_review)
    assert approve_batch(prepared_project, manifest.batch_id, "machine")

    outputs = render_project(
        prepared_project, None, "continued-note", batch_id=manifest.batch_id
    )
    markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
    assert markdown.index(f'<a id="{second.unit_id}"></a>') < markdown.index(
        "> **读者注：**"
    )
    assert "读者注：** 读者注：" not in markdown
    assert "> 来源：" not in markdown
    html = Path(outputs["html"]).read_text(encoding="utf-8")
    assert "读者注：The source" not in html
    assert "访问日期 未记录" not in html


def test_reader_notes_from_all_continued_table_fragments_are_rendered(
    prepared_project: Path,
) -> None:
    units_path = prepared_project / "derived" / "units.jsonl"
    units = read_jsonl(units_path, SourceUnit)
    candidates = [
        unit
        for unit in units
        if unit.page == 1 and unit.kind is UnitKind.PARAGRAPH and unit.translatable
    ][:2]
    assert len(candidates) == 2
    replacements: dict[str, SourceUnit] = {}
    records: list[TranslationRecord] = []
    for index, unit in enumerate(candidates):
        source_table = TableData(
            rows=[["Name", f"Part {index + 1}"]],
            header_rows=1 if index == 0 else 0,
            column_count=2,
        )
        replacements[unit.unit_id] = unit.model_copy(
            update={
                "kind": UnitKind.TABLE,
                "table": source_table,
                "verification_status": SemanticStatus.VERIFIED,
                "continues_from_previous": index == 1,
                "continued_to_next": index == 0,
            }
        )
        records.append(
            TranslationRecord(
                unit_id=unit.unit_id,
                target_text=f"表格片段 {index + 1}",
                target_table=TableData(
                    rows=[["名称", f"部分 {index + 1}"]],
                    header_rows=1 if index == 0 else 0,
                    column_count=2,
                ),
                reader_note=ReaderNote(text=f"第 {index + 1} 个片段注记。"),
                source_hash=unit.source_hash,
            )
        )
    write_jsonl(
        units_path,
        [replacements.get(unit.unit_id, unit) for unit in units],
    )
    write_jsonl(prepared_project / "translations" / "current.jsonl", records)

    outputs = render_project(
        prepared_project, "1", "continued-table-notes", allow_draft=True
    )
    markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
    rendered_html = Path(outputs["html"]).read_text(encoding="utf-8")
    for index in (1, 2):
        assert f"第 {index} 个片段注记。" in markdown
        assert f"第 {index} 个片段注记。" in rendered_html


def test_continued_list_item_renders_as_one_item(prepared_project: Path) -> None:
    units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    paragraphs = [
        unit
        for unit in units
        if unit.page == 1 and unit.kind is UnitKind.PARAGRAPH and unit.translatable
    ]
    first, second = paragraphs[:2]
    revised = []
    for unit in units:
        if unit.unit_id == first.unit_id:
            unit = unit.model_copy(
                update={
                    "kind": UnitKind.LIST_ITEM,
                    "continued_to_next": True,
                }
            )
        elif unit.unit_id == second.unit_id:
            unit = unit.model_copy(
                update={
                    "kind": UnitKind.LIST_ITEM,
                    "continues_from_previous": True,
                }
            )
        revised.append(unit)
    write_jsonl(prepared_project / "derived" / "units.jsonl", revised)

    manifest = create_batches(
        prepared_project, "1", max_words=5000, prefix="continued-list"
    )[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    empty_review = prepared_project / "reviews" / "continued-list.input.jsonl"
    empty_review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, empty_review)
    assert approve_batch(prepared_project, manifest.batch_id, "machine")

    outputs = render_project(
        prepared_project, None, "continued-list", batch_id=manifest.batch_id
    )
    markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
    first_body = first.source_text.lstrip("• ")
    assert markdown.count(f"- 译文：{first_body}") == 1
    assert f"\n- 译文：{second.source_text}" not in markdown
    assert f'<a id="{second.unit_id}"></a>译文：{second.source_text}' in markdown
    html = Path(outputs["html"]).read_text(encoding="utf-8")
    assert f"</li></ul><ul><li>{second.source_text}" not in html
    assert f'<a id="{second.unit_id}"></a>{second.source_text}' in html
    assert (
        f'<a href="#{second.unit_id}" aria-label="continued unit"></a>'
        f"译文：{second.source_text}"
        in html
    )


def test_render_policy_omit_is_persistent_and_excluded_from_batches(
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
                    "render_policy": "omit",
                    "verified": True,
                    "reason": "Synthetic running matter is intentionally omitted from delivery.",
                }
            ]
        },
    )
    revised = apply_layout_overrides(prepared_project)
    omitted = next(item for item in revised if item.unit_id == unit.unit_id)
    assert omitted.render_policy is RenderPolicy.OMIT
    assert not omitted.translatable
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="omit")[0]
    assert unit.unit_id not in manifest.unit_ids


@pytest.mark.parametrize(
    ("kind", "target"),
    [
        (UnitKind.HEADING, "## 标题"),
        (UnitKind.LIST_ITEM, "• 项目"),
        (UnitKind.NOTE, "> [!NOTE]\n> 正文"),
        (UnitKind.NOTE, "注意：正文"),
        (UnitKind.NOTE, "新增内容：正文"),
    ],
)
def test_target_structure_contract_rejects_renderer_owned_markup(
    kind: UnitKind, target: str
) -> None:
    unit = SourceUnit(
        unit_id="p0001-u001-test",
        kind=kind,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="Source",
        source_hash=sha256_text("Source"),
        confidence=1.0,
    )
    assert _target_structure_error(unit, target)


def test_bilingual_note_labels_are_localized_by_the_renderer() -> None:
    assert _callout_kind("■What’s New WPF 4.5 changes this behavior.") is CalloutKind.WHATS_NEW
    unit = SourceUnit(
        unit_id="p0001-u001-note",
        kind=UnitKind.NOTE,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="■Note Source note body.",
        source_hash=sha256_text("■Note Source note body."),
        confidence=1.0,
    )
    assert "<strong>Note</strong>" in _unit_html(
        unit, unit.source_text, source_view=True
    )
    assert "<strong>注意</strong>" in _unit_html(
        unit, "中文提示正文。", source_view=False
    )

    tip = unit.model_copy(
        update={
            "source_text": "■Tip Source tip body.",
            "source_hash": sha256_text("■Tip Source tip body."),
        }
    )
    assert "<strong>Tip</strong>" in _unit_html(tip, tip.source_text, source_view=True)
    assert "<strong>提示</strong>" in _unit_html(
        tip, "中文提示正文。", source_view=False
    )
    assert _target_markdown(tip, "中文提示正文。").startswith("> [!TIP]\n")

    whats_new = unit.model_copy(
        update={
            "source_text": "■What’s New Source update body.",
            "source_hash": sha256_text("■What’s New Source update body."),
            "callout_kind": CalloutKind.WHATS_NEW,
        }
    )
    assert "<strong>What's New</strong>" in _unit_html(
        whats_new, whats_new.source_text, source_view=True
    )
    assert "<strong>新增内容</strong>" in _unit_html(
        whats_new, "中文新增正文。", source_view=False
    )
    assert _target_markdown(whats_new, "中文新增正文。").startswith(
        "> **新增内容**\n"
    )

    with pytest.raises(ValueError, match="valid only for note"):
        SourceUnit(
            unit_id="p0001-u002-invalid-callout",
            kind=UnitKind.PARAGRAPH,
            page=1,
            bbox=(0, 0, 1, 1),
            source_text="Source",
            source_hash=sha256_text("Source"),
            callout_kind=CalloutKind.TIP,
            confidence=1.0,
        )


def test_figure_label_view_is_explicit_even_when_body_text_matches() -> None:
    unit = SourceUnit(
        unit_id="p0001-u001-figure",
        kind=UnitKind.FIGURE,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="same body",
        source_hash=sha256_text("same body"),
        translatable=False,
        figure_labels=[FigureLabel(source="Open", target="打开")],
        confidence=1.0,
    )
    source = _unit_html(unit, unit.source_text, source_view=True)
    target = _unit_html(unit, unit.source_text, source_view=False)
    assert "<li>Open</li>" in source
    assert "<li>打开</li>" in target


def test_sidebar_structure_is_explicit_and_renderer_owned() -> None:
    title = SourceUnit(
        unit_id="p0001-u001-sidebar",
        kind=UnitKind.HEADING,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="DPI SCALING",
        source_hash=sha256_text("DPI SCALING"),
        sidebar_id="p0001-sidebar-001",
        sidebar_role=SidebarRole.TITLE,
        confidence=1.0,
    )
    body = SourceUnit(
        unit_id="p0001-u002-sidebar",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 1, 1, 2),
        source_text="Body paragraph.",
        source_hash=sha256_text("Body paragraph."),
        sidebar_id="p0001-sidebar-001",
        sidebar_role=SidebarRole.BODY,
        confidence=1.0,
    )
    assert _target_markdown(title, "DPI 缩放") == "> **DPI 缩放**"
    assert _target_markdown(body, "侧栏正文。") == "> 侧栏正文。"
    assert 'class="sidebar-fragment sidebar-title"' in _unit_html(
        title, "DPI 缩放", source_view=False
    )
    assert 'class="sidebar-fragment sidebar-body"' in _unit_html(
        body, "侧栏正文。", source_view=False
    )

    with pytest.raises(ValueError, match="must be set together"):
        body.model_copy(update={"sidebar_role": None}).__class__.model_validate(
            body.model_copy(update={"sidebar_role": None}).model_dump()
        )


def test_sidebar_contiguity_ignores_omitted_running_headers(tmp_path: Path) -> None:
    title = SourceUnit(
        unit_id="p0001-u001-sidebar",
        kind=UnitKind.HEADING,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text="SIDEBAR",
        source_hash=sha256_text("SIDEBAR"),
        sidebar_id="p0001-sidebar-001",
        sidebar_role=SidebarRole.TITLE,
        verification_status="verified",
        confidence=1,
    )
    running_header = SourceUnit(
        unit_id="p0002-u001-header",
        kind=UnitKind.PARAGRAPH,
        page=2,
        bbox=(0, 0, 10, 10),
        source_text="CHAPTER 1",
        source_hash=sha256_text("CHAPTER 1"),
        translatable=False,
        render_policy=RenderPolicy.OMIT,
        verification_status="verified",
        confidence=1,
    )
    body = SourceUnit(
        unit_id="p0002-u002-sidebar",
        kind=UnitKind.PARAGRAPH,
        page=2,
        bbox=(0, 20, 10, 30),
        source_text="Body.",
        source_hash=sha256_text("Body."),
        sidebar_id="p0001-sidebar-001",
        sidebar_role=SidebarRole.BODY,
        verification_status="verified",
        confidence=1,
    )

    assert not any(
        error["code"] == "noncontiguous-sidebar"
        for error in _semantic_errors(tmp_path, [title, running_header, body])
    )

    context = _semantic_context_units([title, running_header, body], {2})
    assert context == [title, running_header, body]
    assert not any(
        error["code"] == "invalid-sidebar-title"
        for error in _semantic_errors(tmp_path, context)
    )


def test_continued_sidebar_fragment_removes_duplicate_container_shell() -> None:
    anchor = '<a id="continued"></a>'
    assert _continued_sidebar_markdown("> ，可在不同模式之间切换。") == (
        "，可在不同模式之间切换。"
    )
    assert _merge_continued_sidebar_html(
        '<aside class="sidebar-fragment sidebar-body"><p>某种拆分按钮</p></aside>',
        '<aside class="sidebar-fragment sidebar-body"><p>，可在不同模式之间切换。</p></aside>',
        anchor,
    ) == (
        '<aside class="sidebar-fragment sidebar-body"><p>某种拆分按钮'
        f"{anchor}，可在不同模式之间切换。</p></aside>"
    )


def test_numbered_code_preserves_indentation_semantics(tmp_path: Path) -> None:
    source = '1 <Grid>\n2     <Button />\n3 </Grid>'
    code = SourceUnit(
        unit_id="p0001-u001-code",
        kind=UnitKind.CODE,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text=source,
        source_hash=sha256_text(source),
        translatable=False,
        code_language="xaml",
        confidence=1,
    )

    assert not any(
        error["code"] == "code-indentation-suspect"
        for error in _semantic_errors(tmp_path, [code])
    )


def test_empty_brace_code_does_not_require_an_indented_body(tmp_path: Path) -> None:
    source = "private void Handle()\n{\n}"
    code = SourceUnit(
        unit_id="p0001-u001-code",
        kind=UnitKind.CODE,
        page=1,
        bbox=(0, 0, 10, 10),
        source_text=source,
        source_hash=sha256_text(source),
        translatable=False,
        code_language="csharp",
        confidence=1,
    )

    assert not any(
        error["code"] == "code-indentation-suspect"
        for error in _semantic_errors(tmp_path, [code])
    )


def test_ordered_list_marker_is_renderer_owned() -> None:
    unit = SourceUnit(
        unit_id="p0001-u001-list",
        kind=UnitKind.LIST_ITEM,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="2. Choose 96 dpi.",
        source_hash=sha256_text("2. Choose 96 dpi."),
        confidence=1.0,
    )
    assert _comparison_source_text(unit) == "Choose 96 dpi."
    assert _target_markdown(unit, "选择 96 dpi。").startswith("2. 选择 96 dpi。")
    assert '<ol start="2"><li>选择 96 dpi。</li></ol>' == _unit_html(
        unit, "选择 96 dpi。", source_view=False
    )


def test_batch_scoped_render_excludes_other_units_on_the_same_pages(
    prepared_project: Path,
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="exact")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    review = prepared_project / "reviews" / "exact-empty.jsonl"
    review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, review)
    approve_batch(prepared_project, manifest.batch_id, "machine")

    outputs = render_project(
        prepared_project,
        None,
        "exact-batch",
        batch_id=manifest.batch_id,
    )
    markdown = Path(outputs["markdown"]).read_text(encoding="utf-8")
    report = json.loads(Path(outputs["render_qa"]).read_text(encoding="utf-8"))
    all_units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    outside = next(unit for unit in all_units if unit.unit_id not in manifest.unit_ids)
    assert f'<a id="{outside.unit_id}"></a>' not in markdown
    assert report["passed"]
    assert report["unit_ids"] == manifest.unit_ids


def test_single_batch_render_defaults_to_short_name_and_overwrites(
    prepared_project: Path,
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="shortname")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    review = prepared_project / "reviews" / "shortname-empty.jsonl"
    review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, review)
    approve_batch(prepared_project, manifest.batch_id, "machine")

    assert default_batch_output_name(manifest.batch_id) == "b001"
    with pytest.raises(ValueError, match="Specify name unless rendering a single batch_id"):
        render_project(prepared_project, "1")

    outputs = render_project(prepared_project, None, batch_id=manifest.batch_id)
    markdown_path = Path(outputs["markdown"])
    assert markdown_path.name == "b001.zh.md"
    first = markdown_path.read_text(encoding="utf-8")
    markdown_path.write_text(first + "\n<!-- stale -->\n", encoding="utf-8")
    outputs_again = render_project(prepared_project, None, batch_id=manifest.batch_id)
    assert Path(outputs_again["markdown"]) == markdown_path
    assert markdown_path.read_text(encoding="utf-8") == first

    render_qa_path = Path(outputs_again["render_qa"])
    legacy_report = json.loads(render_qa_path.read_text(encoding="utf-8"))
    legacy_report["selection"].pop("batch_ids")
    render_qa_path.write_text(json.dumps(legacy_report), encoding="utf-8")
    legacy_outputs = render_project(prepared_project, None, batch_id=manifest.batch_id)
    assert Path(legacy_outputs["markdown"]) == markdown_path


def test_first_default_render_rolls_back_interrupted_publication(
    prepared_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = create_batches(
        prepared_project, "1", max_words=300, prefix="interrupted-render"
    )[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    review = prepared_project / "reviews" / "interrupted-render-empty.jsonl"
    review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, review)
    approve_batch(prepared_project, manifest.batch_id, "machine")

    original_atomic_write = rendering_module.atomic_write_text

    def interrupt_html(path: Path, text: str) -> None:
        if path.name == "b001.bilingual.html":
            raise KeyboardInterrupt("seeded render publication interruption")
        original_atomic_write(path, text)

    monkeypatch.setattr(rendering_module, "atomic_write_text", interrupt_html)
    with pytest.raises(
        KeyboardInterrupt, match="seeded render publication interruption"
    ):
        render_project(prepared_project, None, batch_id=manifest.batch_id)
    assert not list((prepared_project / "output").glob("b001.*"))

    monkeypatch.setattr(
        rendering_module, "atomic_write_text", original_atomic_write
    )
    outputs = render_project(prepared_project, None, batch_id=manifest.batch_id)
    render_qa_path = Path(outputs["render_qa"])
    markdown_path = Path(outputs["markdown"])
    assert render_qa_path.is_file()
    published_markdown = markdown_path.read_text(encoding="utf-8")

    monkeypatch.setattr(
        rendering_module,
        "_render_quality_errors",
        lambda *args, **kwargs: ["seeded structural error"],
    )
    with pytest.raises(ValueError, match="seeded structural error"):
        render_project(prepared_project, None, batch_id=manifest.batch_id)
    assert markdown_path.read_text(encoding="utf-8") == published_markdown


def test_single_batch_default_render_rejects_a_different_batch_owner(
    prepared_project: Path,
) -> None:
    first = create_batches(
        prepared_project, "1", max_words=300, prefix="first-chapter"
    )[0]
    second = create_batches(
        prepared_project, "2", max_words=300, prefix="second-chapter"
    )[0]
    assert default_batch_output_name(first.batch_id) == "b001"
    assert default_batch_output_name(second.batch_id) == "b001"

    for manifest in (first, second):
        _submit_identity_translations(prepared_project, manifest.batch_id)
        assert run_qa(prepared_project, manifest.batch_id).passed
        review = prepared_project / "reviews" / f"{manifest.batch_id}-empty.jsonl"
        review.write_text("", encoding="utf-8")
        import_review(prepared_project, manifest.batch_id, review)
        approve_batch(prepared_project, manifest.batch_id, "machine")

    first_outputs = render_project(prepared_project, None, batch_id=first.batch_id)
    first_markdown = Path(first_outputs["markdown"])
    first_text = first_markdown.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="already owned by batch"):
        render_project(prepared_project, None, batch_id=second.batch_id)
    assert first_markdown.read_text(encoding="utf-8") == first_text

    second_outputs = render_project(
        prepared_project,
        None,
        name="second-b001",
        batch_id=second.batch_id,
    )
    assert Path(second_outputs["markdown"]).name == "second-b001.zh.md"


def test_concurrent_default_renders_keep_one_batch_owner(
    prepared_project: Path,
) -> None:
    first = create_batches(
        prepared_project, "1", max_words=300, prefix="concurrent-first"
    )[0]
    second = create_batches(
        prepared_project, "2", max_words=300, prefix="concurrent-second"
    )[0]
    for manifest in (first, second):
        _submit_identity_translations(prepared_project, manifest.batch_id)
        assert run_qa(prepared_project, manifest.batch_id).passed
        review = prepared_project / "reviews" / f"{manifest.batch_id}-empty.jsonl"
        review.write_text("", encoding="utf-8")
        import_review(prepared_project, manifest.batch_id, review)
        approve_batch(prepared_project, manifest.batch_id, "machine")

    barrier = threading.Barrier(2)
    outcomes: list[tuple[str, str]] = []

    def render(manifest_id: str) -> None:
        barrier.wait()
        try:
            render_project(prepared_project, None, batch_id=manifest_id)
        except ValueError as exc:
            outcomes.append((manifest_id, str(exc)))
        else:
            outcomes.append((manifest_id, "ok"))

    threads = [
        threading.Thread(target=render, args=(manifest.batch_id,))
        for manifest in (first, second)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert sorted(result for _, result in outcomes).count("ok") == 1
    rejected = [result for _, result in outcomes if result != "ok"]
    assert len(rejected) == 1
    assert "already owned by batch" in rejected[0]
    render_qa = json.loads(
        (prepared_project / "output" / "b001.render-qa.json").read_text(
            encoding="utf-8"
        )
    )
    owner = render_qa["selection"]["batch_id"]
    owner_manifest = first if owner == first.batch_id else second
    assert render_qa["unit_ids"] == owner_manifest.unit_ids


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


def test_independent_audit_lenses_accumulate(prepared_project: Path) -> None:
    manifest = create_batches(prepared_project, "2", max_words=300, prefix="lenses")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    review = prepared_project / "reviews" / "lenses-empty.jsonl"
    review.write_text("", encoding="utf-8")

    import_review(prepared_project, manifest.batch_id, review, ["fidelity"])
    assert not review_status(prepared_project, manifest.batch_id)["audit_lenses_complete"]
    import_review(prepared_project, manifest.batch_id, review, ["technical"])
    assert not review_status(prepared_project, manifest.batch_id)["audit_lenses_complete"]
    import_review(prepared_project, manifest.batch_id, review, ["chinese-style"])
    assert review_status(prepared_project, manifest.batch_id)["audit_lenses_complete"]


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


def test_batch_refresh_includes_new_units_inside_existing_boundaries(
    prepared_project: Path,
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="inserted")[0]
    units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    first_id = manifest.unit_ids[0]
    first_index = next(index for index, unit in enumerate(units) if unit.unit_id == first_id)
    anchor = units[first_index]
    inserted = SourceUnit(
        unit_id="p0001-u001-recovered",
        kind=UnitKind.PARAGRAPH,
        page=anchor.page,
        bbox=anchor.bbox,
        source_text="Recovered body text.",
        source_hash=sha256_text("Recovered body text."),
        confidence=1,
        verification_status="verified",
    )
    units.insert(first_index + 1, inserted)
    write_jsonl(prepared_project / "derived" / "units.jsonl", units)

    refreshed = refresh_batch(prepared_project, manifest.batch_id)
    assert inserted.unit_id in refreshed.unit_ids
    assert inserted.unit_id in refreshed.translatable_unit_ids


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


def test_external_review_config_validation_and_legacy_compatibility(
    prepared_project: Path,
) -> None:
    assert load_project(prepared_project).external_review is None
    with pytest.raises(ValueError, match="at least one reviewer"):
        ExternalReviewConfig(reviewers=[])
    base = {
        "id": "reviewer",
        "driver": "claude-code",
        "command": "claude",
        "model": "claude-sonnet-5",
        "fast": False,
    }
    with pytest.raises(ValueError, match="unique"):
        ExternalReviewConfig(reviewers=[base, base])
    with pytest.raises(ValueError, match="reviewers.0.driver"):
        ExternalReviewConfig.model_validate(
            {"reviewers": [{**base, "driver": "unknown"}]}
        )
    with pytest.raises(ValueError, match="must not enable fast mode"):
        ExternalReviewerConfig.model_validate({**base, "fast": True})
    with pytest.raises(ValueError, match="cannot set effort"):
        ExternalReviewerConfig.model_validate(
            {
                "id": "agy",
                "driver": "antigravity",
                "command": "agy",
                "model": "gemini-3.6-flash-high",
                "fallbacks": [{"model": "claude-sonnet-4-6", "effort": "high"}],
            }
        )


def test_external_driver_commands_preserve_model_constraints(tmp_path: Path) -> None:
    claude = ExternalReviewerConfig(
        id="claude",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        effort="high",
        fast=False,
    )
    claude_command = build_claude_command(claude, "review")
    assert claude_command[:5] == [
        "claude",
        "-p",
        "--safe-mode",
        "--model",
        "claude-sonnet-5",
    ]
    assert claude_command[claude_command.index("--effort") + 1] == "high"
    assert "--no-session-persistence" in claude_command

    agy = ExternalReviewerConfig(
        id="agy",
        driver="antigravity",
        command="agy",
        model="claude-sonnet-4-6",
    )
    agy_command = build_antigravity_command(agy, "review", tmp_path / "agy.log")
    assert "--effort" not in agy_command
    assert agy_command[-2:] == ["--print", "review"]


def test_external_driver_timeout_becomes_an_auditable_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(
        id="claude",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        effort="high",
        fast=False,
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)

    def raise_timeout(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(
            args[0], kwargs.get("timeout", 330), output="partial output"
        )

    monkeypatch.setattr(external_review.subprocess, "run", raise_timeout)

    with pytest.raises(external_review.ExternalInvocationError) as exc_info:
        external_review._invoke(reviewer, packet, work_dir, {})

    assert exc_info.value.attempts == 1
    assert exc_info.value.raw == "partial output"
    assert exc_info.value.prompt_delivery is PromptDelivery.FILE
    assert exc_info.value.duration_seconds >= 0
    assert "timed out after 330 seconds" in str(exc_info.value)

    with pytest.raises(external_review.ExternalInvocationError) as stdin_exc:
        external_review._invoke(
            reviewer,
            packet,
            work_dir,
            {},
            forced_delivery=PromptDelivery.STDIN,
        )
    assert stdin_exc.value.prompt_delivery is PromptDelivery.STDIN


def test_external_driver_timeout_continues_to_fallback_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fallback_model = "claude-sonnet-4-6"
    reviewer = ExternalReviewerConfig.model_validate(
        {
            "id": "claude",
            "driver": "claude-code",
            "command": "claude",
            "model": "claude-sonnet-5",
            "effort": "high",
            "fast": False,
            "fallbacks": [{"model": fallback_model, "effort": "high"}],
        }
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    calls: list[list[str]] = []

    def timeout_then_succeed(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(
                command, kwargs.get("timeout", 330), output="primary timed out"
            )
        payload = {
            "verdict": "accepted",
            "summary": "No substantive defects.",
            "issues": [],
        }
        requested_model = command[command.index("--model") + 1]
        raw = json.dumps(
            {
                "result": json.dumps(payload),
                "modelUsage": {requested_model: {"inputTokens": 1}},
                "fast_mode_state": "off",
            }
        )
        return subprocess.CompletedProcess(command, 0, raw, "")

    monkeypatch.setattr(external_review.subprocess, "run", timeout_then_succeed)

    result = external_review._invoke(reviewer, packet, work_dir, {})

    assert result[2] == fallback_model
    assert result[6] == 2
    assert fallback_model not in calls[0]
    assert fallback_model in calls[1]

    calls.clear()
    monkeypatch.setattr(
        external_review, "CLAUDE_STDIN_PROMPT_DELIVERY_ENABLED", True
    )
    delivery_result = external_review._invoke(reviewer, packet, work_dir, {})
    assert delivery_result[2] == reviewer.model
    assert delivery_result[6] == 2
    assert delivery_result[7] is PromptDelivery.FILE


def test_external_driver_accumulates_usage_across_format_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(
        id="claude",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        effort="high",
        fast=False,
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    calls = 0

    def invalid_then_succeed(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        usage = (
            {
                "inputTokens": 10,
                "cacheCreationInputTokens": 2,
                "cacheReadInputTokens": 3,
                "outputTokens": 4,
                "costUSD": 0.1,
            }
            if calls == 1
            else {
                "inputTokens": 20,
                "cacheCreationInputTokens": 5,
                "cacheReadInputTokens": 7,
                "outputTokens": 8,
                "costUSD": 0.2,
            }
        )
        payload = (
            {"verdict": "accepted"}
            if calls == 1
            else {
                "verdict": "accepted",
                "summary": "No substantive defects found.",
                "issues": [],
            }
        )
        raw = json.dumps(
            {
                "result": json.dumps(payload),
                "modelUsage": {reviewer.model: usage},
                "num_turns": calls,
                "fast_mode_state": "off",
            }
        )
        return subprocess.CompletedProcess(command, 0, raw, "")

    monkeypatch.setattr(external_review.subprocess, "run", invalid_then_succeed)

    result = external_review._invoke(reviewer, packet, work_dir, {})
    usage = result[9]

    assert result[6] == 2
    assert usage.input_tokens == 30
    assert usage.cache_creation_input_tokens == 7
    assert usage.cache_read_input_tokens == 10
    assert usage.output_tokens == 12
    assert usage.provider_turns == 3
    assert result[10] == pytest.approx(0.3)


def test_least_used_assignment_counts_primary_batches_not_retries(
    prepared_project: Path,
) -> None:
    config = load_project(prepared_project)
    config.external_review = ExternalReviewConfig(
        reviewers=[
            ExternalReviewerConfig(
                id="claude",
                driver="claude-code",
                command="claude",
                model="claude-sonnet-5",
                fast=False,
            ),
            ExternalReviewerConfig(
                id="agy",
                driver="antigravity",
                command="agy",
                model="gemini-3.6-flash-high",
            ),
        ]
    )
    save_project(prepared_project, config)
    base = {
        "batch_id": "batch-one",
        "requested_model": "claude-sonnet-5",
        "actual_model": "claude-sonnet-5",
        "model_verified": True,
        "translation_fingerprint": "f" * 64,
        "packet_sha256": "a" * 64,
        "prompt_version": "test",
        "verdict": "accepted",
        "summary": "accepted",
    }
    runs = [
        ExternalReviewRun(
            run_id="primary-1",
            reviewer_id="claude",
            driver="claude-code",
            role="primary",
            **base,
        ),
        ExternalReviewRun(
            run_id="primary-retry",
            reviewer_id="claude",
            driver="claude-code",
            role="primary",
            **base,
        ),
        ExternalReviewRun(
            run_id="second-1",
            reviewer_id="agy",
            driver="antigravity",
            role="second-opinion",
            requested_model="gemini-3.6-flash-high",
            actual_model="Gemini 3.6 Flash (High)",
            **{key: value for key, value in base.items() if key not in {"requested_model", "actual_model"}},
        ),
    ]
    append_jsonl(prepared_project / "reviews" / "batch-one.external-runs.jsonl", runs)
    usage = external_reviewer_usage(prepared_project)
    assert usage["claude"]["assigned_primary_batches"] == 1
    assert usage["claude"]["successful_calls"] == 2
    assert usage["agy"]["second_opinion_calls"] == 1
    assert _select_reviewer(prepared_project, None).id == "agy"


def test_assignment_epoch_and_reservations_balance_concurrent_calls(
    prepared_project: Path,
) -> None:
    config = load_project(prepared_project)
    config.external_review = ExternalReviewConfig(
        assignment_since="2026-01-01T00:00:00+00:00",
        reviewers=[
            ExternalReviewerConfig(
                id="claude",
                driver="claude-code",
                command="claude",
                model="claude-sonnet-5",
                fast=False,
            ),
            ExternalReviewerConfig(
                id="agy",
                driver="antigravity",
                command="agy",
                model="gemini-3.6-flash-high",
            ),
            ExternalReviewerConfig(
                id="cursor",
                driver="cursor-cli",
                command="agent.cmd",
                model="cursor-grok-4.6-high-fast",
                fallbacks=[
                    {"model": "claude-sonnet-5-high"},
                    {"model": "auto"},
                ],
            ),
        ],
    )
    save_project(prepared_project, config)
    append_jsonl(
        prepared_project / "reviews" / "old.external-runs.jsonl",
        [
            ExternalReviewRun(
                run_id=f"old-{index}",
                batch_id=f"old-{index}",
                reviewer_id="claude" if index < 20 else "agy",
                driver="claude-code" if index < 20 else "antigravity",
                role="primary",
                requested_model="old",
                actual_model="old",
                model_verified=True,
                translation_fingerprint="f" * 64,
                packet_sha256="a" * 64,
                prompt_version="test",
                verdict="accepted",
                summary="Historical accepted review.",
                reviewed_at="2020-01-01T00:00:00+00:00",
            )
            for index in range(30)
        ],
    )

    reservations: list[str] = []
    selected: list[str] = []
    for _ in range(3):
        reviewer, reservation_id = _reserve_reviewer(
            prepared_project, None, None, reserve=True
        )
        selected.append(reviewer.id)
        assert reservation_id is not None
        reservations.append(reservation_id)

    assert selected == ["claude", "agy", "cursor"]
    for reservation_id in reservations:
        _release_reviewer_reservation(prepared_project, reservation_id)


def test_cursor_host_dry_run_reservations_balance_assignments(
    prepared_project: Path,
) -> None:
    config = load_project(prepared_project)
    config.external_review = ExternalReviewConfig(
        reviewers=[
            ExternalReviewerConfig(
                id="cursor-a",
                driver="cursor-cli",
                command="agent.cmd",
                model="cursor-grok-4.6-high",
            ),
            ExternalReviewerConfig(
                id="cursor-b",
                driver="cursor-cli",
                command="agent.cmd",
                model="claude-sonnet-5-high",
            ),
        ]
    )
    save_project(prepared_project, config)

    reservations: list[str] = []
    selected: list[str] = []
    for _ in range(2):
        reviewer, reservation_id = _reserve_reviewer(
            prepared_project,
            None,
            None,
            reserve=False,
            reserve_cursor_dry_run=True,
        )
        selected.append(reviewer.id)
        assert reservation_id is not None
        reservations.append(reservation_id)

    assert selected == ["cursor-a", "cursor-b"]
    claimed = external_review._claim_reviewer_reservation(
        prepared_project, reservations[0], config.external_review.reviewers[0]
    )
    assert claimed == reservations[0]
    with pytest.raises(ValueError, match="already being imported"):
        external_review._claim_reviewer_reservation(
            prepared_project, reservations[0], config.external_review.reviewers[0]
        )
    external_review._restore_reviewer_reservation(
        prepared_project, reservations[0], config.external_review.reviewers[0]
    )
    assert (
        external_review._claim_reviewer_reservation(
            prepared_project, reservations[0], config.external_review.reviewers[0]
        )
        == reservations[0]
    )
    for reservation_id in reservations:
        _release_reviewer_reservation(prepared_project, reservation_id)


def test_external_persistence_lock_serializes_one_batch(
    prepared_project: Path,
) -> None:
    with external_review._external_persistence_lock(prepared_project, "batch-1"):
        with pytest.raises(TimeoutError, match="external persistence lock"):
            with external_review._external_persistence_lock(
                prepared_project, "batch-1", timeout_seconds=0.01
            ):
                pass
        with external_review._external_persistence_lock(
            prepared_project, "batch-2"
        ):
            pass

    with external_review._external_persistence_lock(prepared_project, "batch-1"):
        pass


def test_external_output_parsers_accept_wrapping_and_verify_metadata() -> None:
    result = {"verdict": "accepted", "summary": "No defects.", "issues": []}
    claude_outer = {
        "result": json.dumps(result),
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}},
        "fast_mode_state": "off",
    }
    parsed, model, fast = _parse_claude(json.dumps(claude_outer))
    assert parsed == result
    assert model == "claude-sonnet-5"
    assert fast == "off"
    log = (
        'Propagating selected model override model="gemini-3.6-flash-high" '
        'label="Gemini 3.6 Flash (High)"'
    )
    parsed, label = _parse_antigravity(
        "Review complete\n```json\n" + json.dumps(result) + "\n```", log
    )
    assert parsed == result
    assert label == "Gemini 3.6 Flash (High)"

    envelope = {
        "conversation_id": "conversation-1",
        "status": "SUCCESS",
        "response": "Review complete.",
        "duration_seconds": 12.5,
        "num_turns": 1,
        "structured_output": result,
        "json_schema": {"type": "object"},
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }
    parsed, label = _parse_antigravity(json.dumps(envelope), log)
    assert parsed == result
    assert label == "Gemini 3.6 Flash (High)"

    # Antigravity 1.1.12 does not identify the actual model in its JSON envelope.
    # Do not infer verification from the requested model or unrelated envelope fields.
    parsed, label = _parse_antigravity(json.dumps(envelope), "")
    assert parsed == result
    assert label is None

    with pytest.raises(json.JSONDecodeError):
        _parse_antigravity('{"verdict": "accepted"', log)
    with pytest.raises(ValueError, match="too short to be auditable"):
        _parse_claude(
            json.dumps(
                {
                    "structured_output": {
                        "verdict": "accepted",
                        "summary": "test",
                        "issues": [],
                    },
                    "modelUsage": {"claude-sonnet-5": {"inputTokens": 1}},
                    "fast_mode_state": "off",
                }
            )
        )


@pytest.mark.parametrize(
    "structured_output",
    [pytest.param(None, id="missing"), pytest.param([], id="non-object")],
)
def test_antigravity_success_envelope_requires_structured_object(
    structured_output: object,
) -> None:
    envelope: dict[str, object] = {"status": "SUCCESS"}
    if structured_output is not None:
        envelope["structured_output"] = structured_output

    with pytest.raises(
        ValueError,
        match="SUCCESS result structured_output must be a JSON object",
    ):
        _parse_antigravity(json.dumps(envelope), "")


def test_antigravity_error_envelope_is_not_treated_as_review_output() -> None:
    envelope = {
        "status": "ERROR",
        "response": "Authentication failed while contacting the provider.",
        "structured_output": {
            "verdict": "accepted",
            "summary": "This must never be accepted.",
            "issues": [],
        },
    }

    with pytest.raises(RuntimeError, match="Authentication failed"):
        _parse_antigravity(json.dumps(envelope), "")


@pytest.mark.parametrize("enveloped", [False, True], ids=["legacy", "envelope"])
def test_antigravity_rejects_extra_business_fields(enveloped: bool) -> None:
    result = {
        "verdict": "accepted",
        "summary": "No substantive defects found.",
        "issues": [],
        "model": "gemini-3.6-flash-high",
    }
    output: object = (
        {"status": "SUCCESS", "structured_output": result} if enveloped else result
    )

    with pytest.raises(ValueError, match="External result fields must be exactly"):
        _parse_antigravity(json.dumps(output), "")


def test_antigravity_error_status_stops_without_format_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(
        id="agy",
        driver="antigravity",
        command="agy",
        model="gemini-3.6-flash-high",
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)
    calls = 0

    def fail_authentication(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        raw = json.dumps(
            {"status": "ERROR", "response": "Authentication token expired."}
        )
        return subprocess.CompletedProcess(command, 0, raw, "")

    monkeypatch.setattr(external_review.subprocess, "run", fail_authentication)

    with pytest.raises(external_review.ExternalInvocationError) as exc_info:
        external_review._invoke(reviewer, packet, work_dir, {})

    assert calls == 1
    assert exc_info.value.attempts == 1
    assert "Authentication token expired" in str(exc_info.value)


def test_antigravity_success_envelope_integrates_with_model_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewer = ExternalReviewerConfig(
        id="agy",
        driver="antigravity",
        command="agy",
        model="gemini-3.6-flash-high",
    )
    packet = tmp_path / "review-packet.md"
    packet.write_text("Review packet.", encoding="utf-8")
    work_dir = tmp_path / "work"
    work_dir.mkdir()
    monkeypatch.setattr(external_review.shutil, "which", lambda command: command)

    def return_success_envelope(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        log_path = Path(command[command.index("--log-file") + 1])
        log_path.write_text(
            'Selected model override model="gemini-3.6-flash-high" '
            'label="Gemini 3.6 Flash (High)"',
            encoding="utf-8",
        )
        raw = json.dumps(
            {
                "conversation_id": "conversation-1",
                "status": "SUCCESS",
                "response": "Review complete.",
                "duration_seconds": 1.5,
                "num_turns": 1,
                "structured_output": {
                    "verdict": "accepted",
                    "summary": "No substantive defects found.",
                    "issues": [],
                },
                "json_schema": {"type": "object"},
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
        )
        return subprocess.CompletedProcess(command, 0, raw, "")

    monkeypatch.setattr(external_review.subprocess, "run", return_success_envelope)

    result = external_review._invoke(reviewer, packet, work_dir, {})

    assert result[0]["verdict"] == "accepted"
    assert result[4] == "Gemini 3.6 Flash (High)"
    assert result[6] == 1
    assert result[9].input_tokens == 10
    assert result[9].output_tokens == 5


def test_external_packet_is_isolated_and_external_gate_is_strict(
    prepared_project: Path,
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="external")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    current = read_jsonl(
        prepared_project / "translations" / "current.jsonl", TranslationRecord
    )
    current[0] = current[0].model_copy(
        update={
            "reader_note": ReaderNote(
                text="Documented correction note.",
                sources=["https://example.com/reference"],
                accessed_at="2026-07-28",
            )
        }
    )
    write_jsonl(prepared_project / "translations" / "current.jsonl", current)
    assert run_qa(prepared_project, manifest.batch_id).passed
    review_input = prepared_project / "reviews" / "historical.jsonl"
    historical = ReviewIssue(
        issue_id="historical-r001",
        batch_id=manifest.batch_id,
        unit_id=manifest.translatable_unit_ids[0],
        severity=Severity.SUGGESTION,
        type=IssueType.STYLE,
        explanation="SECRET PRIOR REVIEW OPINION",
        reviewer="test",
    )
    write_jsonl(review_input, [historical])
    import_review(prepared_project, manifest.batch_id, review_input)
    assert approve_batch(prepared_project, manifest.batch_id, "machine")

    config = load_project(prepared_project)
    config.external_review = ExternalReviewConfig(
        reviewers=[
            ExternalReviewerConfig(
                id="claude",
                driver="claude-code",
                command="claude",
                model="claude-sonnet-5",
                effort="high",
                fast=False,
            )
        ]
    )
    save_project(prepared_project, config)
    packet, _ = _packet_text(prepared_project, manifest.batch_id)
    assert "SECRET PRIOR REVIEW OPINION" not in packet
    assert "Reader note (separate from translated body)" in packet
    assert "Documented correction note." in packet
    assert "https://example.com/reference" in packet
    with pytest.raises(ValueError, match="Formal rendering is blocked"):
        render_project(
            prepared_project, None, "before-external", batch_id=manifest.batch_id
        )

    fingerprint = batch_translation_fingerprint(prepared_project, manifest.batch_id)
    run = ExternalReviewRun(
        run_id="accepted-run",
        batch_id=manifest.batch_id,
        reviewer_id="claude",
        driver="claude-code",
        role="primary",
        requested_model="claude-sonnet-5",
        actual_model="claude-sonnet-5",
        model_verified=True,
        effort="high",
        fast_mode="off",
        translation_fingerprint=fingerprint,
        packet_sha256="0" * 64,
        prompt_version="test",
        verdict=ExternalReviewVerdict.ACCEPTED,
        summary="No substantive defects.",
        covered_unit_ids=list(manifest.unit_ids),
        unit_fingerprints=batch_unit_fingerprints(
            prepared_project, manifest.batch_id
        ),
        source_fingerprint=batch_source_fingerprint(
            prepared_project, manifest.batch_id
        ),
        structure_fingerprint=batch_structure_fingerprint(
            prepared_project, manifest.batch_id
        ),
        context_fingerprint=external_review._external_review_context_fingerprint(
            prepared_project,
            manifest.batch_id,
            list(manifest.unit_ids),
            ReviewScope.FULL,
        ),
    )
    append_jsonl(
        prepared_project / "reviews" / f"{manifest.batch_id}.external-runs.jsonl",
        [run],
    )
    status = external_review_status(prepared_project, manifest.batch_id)
    assert status["external_approvable"]
    assert (
        approve_batch(prepared_project, manifest.batch_id, "external")
        is ProjectStatus.EXTERNAL_REVIEWED
    )
    outputs = render_project(
        prepared_project, None, "after-external", batch_id=manifest.batch_id
    )
    assert Path(outputs["markdown"]).is_file()
    external_summary = Path(outputs["external_review"])
    assert external_summary.is_file()
    assert "External approval gate: PASS" in external_summary.read_text(encoding="utf-8")


def test_external_issue_does_not_block_second_opinion_gate(
    prepared_project: Path,
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="second")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    empty_review = prepared_project / "reviews" / "second-opinion-internal.jsonl"
    empty_review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, empty_review)
    assert approve_batch(prepared_project, manifest.batch_id, "machine")
    external_issue = ReviewIssue(
        issue_id="external-major-r001",
        batch_id=manifest.batch_id,
        unit_id=manifest.translatable_unit_ids[0],
        severity=Severity.MAJOR,
        type=IssueType.TECHNICAL,
        explanation="External primary finding awaiting a second opinion.",
        reviewer="external:claude",
    )
    external_input = prepared_project / "reviews" / "external-major.jsonl"
    write_jsonl(external_input, [external_issue])
    import_review(
        prepared_project,
        manifest.batch_id,
        external_input,
        lenses=["external:claude"],
        preserve_status=True,
    )

    _require_machine_reviewed(
        prepared_project, manifest.batch_id, allow_external_issues=True
    )


def test_failed_external_runs_persist_serially_across_drivers(
    prepared_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="race")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    empty_review = prepared_project / "reviews" / "race-internal.jsonl"
    write_jsonl(empty_review, [])
    import_review(prepared_project, manifest.batch_id, empty_review)
    assert approve_batch(prepared_project, manifest.batch_id, "machine")

    config = load_project(prepared_project)
    config.external_review = ExternalReviewConfig(
        reviewers=[
            ExternalReviewerConfig(
                id="claude",
                driver="claude-code",
                command="claude",
                model="claude-sonnet-5",
            ),
            ExternalReviewerConfig(
                id="antigravity",
                driver="antigravity",
                command="agy",
                model="gemini-3.6-flash-high",
            ),
        ]
    )
    save_project(prepared_project, config)

    invocation_barrier = threading.Barrier(2)

    def fail_together(*args: object, **kwargs: object) -> None:
        invocation_barrier.wait(timeout=5)
        raise external_review.ExternalInvocationError(
            "seeded provider failure",
            attempts=1,
            raw="provider failure",
            failure_type="provider",
        )

    monkeypatch.setattr(external_review, "_invoke", fail_together)
    monkeypatch.setattr(external_review, "_command_version", lambda command: "test")
    original_append = external_review.append_jsonl
    append_guard = threading.Lock()
    active_appends = 0
    maximum_active_appends = 0

    def delayed_append(path: Path, records: object) -> None:
        nonlocal active_appends, maximum_active_appends
        if path.name.endswith(".external-runs.jsonl"):
            with append_guard:
                active_appends += 1
                maximum_active_appends = max(maximum_active_appends, active_appends)
            time.sleep(0.05)
            try:
                original_append(path, records)  # type: ignore[arg-type]
            finally:
                with append_guard:
                    active_appends -= 1
            return
        original_append(path, records)  # type: ignore[arg-type]

    monkeypatch.setattr(external_review, "append_jsonl", delayed_append)
    errors: list[BaseException] = []

    def run_failed(reviewer_id: str, other_id: str) -> None:
        try:
            external_review.run_external_review(
                prepared_project,
                manifest.batch_id,
                reviewer_id=reviewer_id,
                _attempted_reviewer_ids=frozenset({other_id}),
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [
        threading.Thread(target=run_failed, args=("claude", "antigravity")),
        threading.Thread(target=run_failed, args=("antigravity", "claude")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert maximum_active_appends == 1
    runs = read_jsonl(
        prepared_project / "reviews" / f"{manifest.batch_id}.external-runs.jsonl",
        ExternalReviewRun,
    )
    assert {run.reviewer_id for run in runs} == {"claude", "antigravity"}
    assert all(not run.success for run in runs)


def test_cursor_host_subagent_from_result_skips_cli(
    prepared_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="host")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    empty_review = prepared_project / "reviews" / "host-internal.jsonl"
    empty_review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, empty_review)
    assert approve_batch(prepared_project, manifest.batch_id, "machine")

    config = load_project(prepared_project)
    config.external_review = ExternalReviewConfig(
        reviewers=[
            ExternalReviewerConfig(
                id="cursor-cli-grok46",
                driver="cursor-cli",
                command="reviewer-cli",
                model="cursor-grok-4.6-high",
                fallbacks=[{"model": "claude-sonnet-5-high"}],
            )
        ]
    )
    save_project(prepared_project, config)

    original_packet_text = external_review._packet_text

    def fail_packet_snapshot(*args: object, **kwargs: object) -> None:
        raise RuntimeError("seeded packet snapshot failure")

    monkeypatch.setattr(external_review, "_packet_text", fail_packet_snapshot)
    with pytest.raises(RuntimeError, match="seeded packet snapshot failure"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            dry_run=True,
        )
    assignment_root = prepared_project / ".littrans" / "external-assignments"
    assert not list(assignment_root.glob("*.json"))
    monkeypatch.setattr(external_review, "_packet_text", original_packet_text)

    original_render_packet = external_review._render_packet

    def fail_packet_render(*args: object, **kwargs: object) -> None:
        raise RuntimeError("seeded packet render failure")

    monkeypatch.setattr(external_review, "_render_packet", fail_packet_render)
    with pytest.raises(RuntimeError, match="seeded packet render failure"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            dry_run=True,
        )
    assert not list(assignment_root.glob("*.json"))
    monkeypatch.setattr(external_review, "_render_packet", original_render_packet)

    dry_run = external_review.run_external_review(
        prepared_project,
        manifest.batch_id,
        reviewer_id="cursor-cli-grok46",
        dry_run=True,
    )
    dry_run_path = Path(dry_run["dry_run_path"])
    assert dry_run_path.is_file()
    assert dry_run["schema_version"] == external_review.CURSOR_HOST_DRY_RUN_SCHEMA_VERSION
    assert dry_run["role"] == "primary"
    assert dry_run["requested_model"] == "cursor-grok-4.6-high"
    reservation_path = (
        prepared_project
        / ".littrans"
        / "external-assignments"
        / f"{dry_run['reservation_id']}.json"
    )
    assert reservation_path.is_file()
    assert len(dry_run["review_binding"]) == 64
    packet_text = Path(dry_run["packet_path"]).read_text(encoding="utf-8")
    assert dry_run["review_binding"] in packet_text
    assert dry_run["review_binding"] in dry_run["prompt"]

    result_path = prepared_project / "reviews" / "host-result.json"
    result_path.write_text(
        json.dumps(
            {
                "review_binding": dry_run["review_binding"],
                "verdict": "accepted",
                "summary": "No substantive defects found.",
                "issues": [],
            }
        ),
        encoding="utf-8",
    )

    def fail_if_invoked(*args: object, **kwargs: object) -> None:
        raise AssertionError("cursor-cli must not be invoked for --from-result")

    monkeypatch.setattr(external_review.subprocess, "run", fail_if_invoked)
    with pytest.raises(
        ValueError, match="from-result and from-dry-run must be provided together"
    ):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
        )

    obsolete_path = prepared_project / "reviews" / "obsolete-dry-run.json"
    obsolete_path.write_text(
        json.dumps({"reviewer_id": "cursor-cli-grok46"}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="obsolete or incomplete; regenerate it"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
            from_dry_run=obsolete_path,
            host_actual_model="Cursor Grok 4.6 High",
        )

    original_dry_run = json.loads(dry_run_path.read_text(encoding="utf-8"))
    legacy_path = prepared_project / "reviews" / "legacy-dry-run.json"
    legacy_path.write_text(
        json.dumps({**original_dry_run, "schema_version": 1}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Unsupported Cursor host dry-run schema"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
            from_dry_run=legacy_path,
            host_actual_model="Cursor Grok 4.6 High",
        )

    for field, wrong_value in (
        ("batch_id", "other-batch"),
        ("requested_model", "cursor-wrong-model"),
        ("translation_fingerprint", "0" * 64),
        ("base_packet_sha256", "2" * 64),
        ("packet_sha256", "1" * 64),
        ("review_binding", "3" * 64),
        ("base_run_id", "wrong-base-run"),
    ):
        invalid_path = dry_run_path.parent / f"invalid-{field}.json"
        invalid_record = {**original_dry_run, field: wrong_value}
        invalid_path.write_text(json.dumps(invalid_record), encoding="utf-8")
        with pytest.raises(ValueError, match="does not match the current external review"):
            external_review.run_external_review(
                prepared_project,
                manifest.batch_id,
                reviewer_id="cursor-cli-grok46",
                from_result=result_path,
                from_dry_run=invalid_path,
                host_actual_model="Cursor Grok 4.6 High",
            )

    page_path = next((dry_run_path.parent / "packet" / "pages").glob("*.png"))
    original_page = page_path.read_bytes()
    page_path.write_bytes(original_page + b"tampered")
    with pytest.raises(ValueError, match="page evidence has changed"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
            from_dry_run=dry_run_path,
            host_actual_model="Cursor Grok 4.6 High",
        )
    page_path.write_bytes(original_page)

    wrong_reviewer_path = prepared_project / "reviews" / "invalid-reviewer.json"
    wrong_reviewer_path.write_text(
        json.dumps({**original_dry_run, "reviewer_id": "other-reviewer"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Requested reviewer does not match"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
            from_dry_run=wrong_reviewer_path,
            host_actual_model="Cursor Grok 4.6 High",
        )

    with pytest.raises(ValueError, match="requires a non-empty actual-model"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
            from_dry_run=dry_run_path,
        )
    with pytest.raises(ValueError, match="only supported with from-result"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            host_actual_model="Cursor Grok 4.6 High",
        )
    with pytest.raises(ValueError, match="actual model does not match"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
            from_dry_run=dry_run_path,
            host_actual_model="Cursor Composer 2",
        )

    mismatched_result_path = prepared_project / "reviews" / "mismatched-result.json"
    mismatched_result_path.write_text(
        json.dumps(
            {
                "review_binding": "f" * 64,
                "verdict": "accepted",
                "summary": "No substantive defects found in another packet.",
                "issues": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="result binding does not match"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=mismatched_result_path,
            from_dry_run=dry_run_path,
            host_actual_model="Cursor Grok 4.6 High",
        )

    original_snapshot_text_files = external_review._snapshot_text_files

    def fail_persistence_snapshot(*args: object, **kwargs: object) -> None:
        assert (prepared_project / ".littrans-write-lock").is_dir()
        raise KeyboardInterrupt("seeded persistence snapshot interruption")

    monkeypatch.setattr(
        external_review, "_snapshot_text_files", fail_persistence_snapshot
    )
    with pytest.raises(
        KeyboardInterrupt, match="seeded persistence snapshot interruption"
    ):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
            from_dry_run=dry_run_path,
            host_actual_model="Sonnet 5 1M High",
        )
    snapshot_reservation = json.loads(reservation_path.read_text(encoding="utf-8"))
    assert snapshot_reservation["status"] == "reserved"
    monkeypatch.setattr(
        external_review, "_snapshot_text_files", original_snapshot_text_files
    )

    original_convert_issues = external_review._convert_issues

    def fail_issue_persistence(*args: object, **kwargs: object) -> None:
        raise RuntimeError("seeded issue persistence failure")

    monkeypatch.setattr(external_review, "_convert_issues", fail_issue_persistence)
    with pytest.raises(RuntimeError, match="seeded issue persistence failure"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=result_path,
            from_dry_run=dry_run_path,
            host_actual_model="Sonnet 5 1M High",
        )
    reservation_record = json.loads(reservation_path.read_text(encoding="utf-8"))
    assert reservation_record["status"] == "reserved"
    monkeypatch.setattr(external_review, "_convert_issues", original_convert_issues)

    status = external_review.run_external_review(
        prepared_project,
        manifest.batch_id,
        reviewer_id="cursor-cli-grok46",
        from_result=result_path,
        from_dry_run=dry_run_path,
        host_actual_model="Sonnet 5 1M High",
    )
    assert status["verdict"] == "accepted"
    assert status["external_approvable"] is True
    primary = status["primary"]
    assert primary["cli_version"] == external_review.CURSOR_HOST_SUBAGENT_VERSION
    assert primary["requested_model"] == "claude-sonnet-5-high"
    assert primary["actual_model"] == "Sonnet 5 1M High"
    assert primary["model_verified"] is True
    assert primary["packet_sha256"] == dry_run["packet_sha256"]
    assert primary["packet_sha256"] != dry_run["base_packet_sha256"]
    assert primary["attempt_log_path"] is None
    assert not (
        prepared_project / "reviews" / f"{manifest.batch_id}.external-attempts.jsonl"
    ).exists()
    assert not reservation_path.exists()

    repeat_dry_run = external_review.run_external_review(
        prepared_project,
        manifest.batch_id,
        reviewer_id="cursor-cli-grok46",
        dry_run=True,
    )
    unit_id = manifest.translatable_unit_ids[0]
    source, target = external_review._evidence_map(
        prepared_project, manifest.batch_id
    )[unit_id]
    changes_path = prepared_project / "reviews" / "host-changes-result.json"
    changes_path.write_text(
        json.dumps(
            {
                "review_binding": repeat_dry_run["review_binding"],
                "verdict": "changes-requested",
                "summary": "A substantive defect remains in the current translation.",
                "issues": [
                    {
                        "unit_id": unit_id,
                        "severity": "major",
                        "type": "meaning",
                        "source_span": source,
                        "target_span": target,
                        "explanation": "The translated meaning must be corrected.",
                        "suggested_revision": f"{target} revised",
                        "confidence": 0.95,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    repeat_reservation_path = (
        prepared_project
        / ".littrans"
        / "external-assignments"
        / f"{repeat_dry_run['reservation_id']}.json"
    )
    issue_path = prepared_project / "reviews" / f"{manifest.batch_id}.issues.jsonl"
    issues_before_failure = issue_path.read_text(encoding="utf-8")
    original_append_jsonl = external_review.append_jsonl

    def fail_run_append(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt("seeded external run append interruption")

    monkeypatch.setattr(external_review, "append_jsonl", fail_run_append)
    with pytest.raises(KeyboardInterrupt, match="seeded external run append interruption"):
        external_review.run_external_review(
            prepared_project,
            manifest.batch_id,
            reviewer_id="cursor-cli-grok46",
            from_result=changes_path,
            from_dry_run=Path(repeat_dry_run["dry_run_path"]),
            host_actual_model="Cursor Grok 4.6 High",
        )
    assert issue_path.read_text(encoding="utf-8") == issues_before_failure
    repeat_reservation = json.loads(
        repeat_reservation_path.read_text(encoding="utf-8")
    )
    assert repeat_reservation["status"] == "reserved"
    monkeypatch.setattr(external_review, "append_jsonl", original_append_jsonl)

    changed = external_review.run_external_review(
        prepared_project,
        manifest.batch_id,
        reviewer_id="cursor-cli-grok46",
        from_result=changes_path,
        from_dry_run=Path(repeat_dry_run["dry_run_path"]),
        host_actual_model="Cursor Grok 4.6 High",
    )
    assert changed["external_approvable"] is False
    assert changed["primary"]["run_id"] != primary["run_id"]
    assert changed["primary"]["verdict"] == "changes-requested"


def test_cursor_host_second_opinion_uses_a_separate_import(
    prepared_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="host-second")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    empty_review = prepared_project / "reviews" / "host-second-internal.jsonl"
    empty_review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, empty_review)
    assert approve_batch(prepared_project, manifest.batch_id, "machine")

    config = load_project(prepared_project)
    config.external_review = ExternalReviewConfig(
        reviewers=[
            ExternalReviewerConfig(
                id="cursor-primary",
                driver="cursor-cli",
                command="cursor-primary-cli",
                model="cursor-grok-4.6-high",
            ),
            ExternalReviewerConfig(
                id="cursor-second",
                driver="cursor-cli",
                command="cursor-second-cli",
                model="cursor-composer-2",
            ),
        ]
    )
    save_project(prepared_project, config)

    def fail_if_invoked(*args: object, **kwargs: object) -> None:
        raise AssertionError("cursor-cli must not be invoked for host result imports")

    monkeypatch.setattr(external_review.subprocess, "run", fail_if_invoked)
    primary_dry_run = external_review.run_external_review(
        prepared_project,
        manifest.batch_id,
        reviewer_id="cursor-primary",
        dry_run=True,
    )
    unit_id = manifest.translatable_unit_ids[0]
    source, target = external_review._evidence_map(
        prepared_project, manifest.batch_id
    )[unit_id]
    primary_result = prepared_project / "reviews" / "host-primary-result.json"
    primary_result.write_text(
        json.dumps(
            {
                "review_binding": primary_dry_run["review_binding"],
                "verdict": "accepted",
                "summary": "Accepted with one low-confidence style suggestion.",
                "issues": [
                    {
                        "unit_id": unit_id,
                        "severity": "suggestion",
                        "type": "style",
                        "source_span": source,
                        "target_span": target,
                        "explanation": "A low-confidence style point needs confirmation.",
                        "suggested_revision": f"{target} revised",
                        "confidence": 0.5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    pending = external_review.run_external_review(
        prepared_project,
        manifest.batch_id,
        reviewer_id="cursor-primary",
        from_result=primary_result,
        from_dry_run=Path(primary_dry_run["dry_run_path"]),
        host_actual_model="Cursor Grok 4.6 High",
    )
    assert pending["second_opinion_required"] is True
    assert pending["second_opinion"] is None

    second_dry_run = external_review.run_external_review(
        prepared_project,
        manifest.batch_id,
        reviewer_id="cursor-second",
        second_opinion=True,
        dry_run=True,
    )
    assert second_dry_run["role"] == "second-opinion"
    assert second_dry_run["base_run_id"] == pending["primary"]["run_id"]
    second_result = prepared_project / "reviews" / "host-second-result.json"
    second_result.write_text(
        json.dumps(
            {
                "review_binding": second_dry_run["review_binding"],
                "verdict": "accepted",
                "summary": "No additional substantive defects found.",
                "issues": [],
            }
        ),
        encoding="utf-8",
    )
    complete = external_review.run_external_review(
        prepared_project,
        manifest.batch_id,
        reviewer_id="cursor-second",
        second_opinion=True,
        from_result=second_result,
        from_dry_run=Path(second_dry_run["dry_run_path"]),
        host_actual_model="Cursor Composer 2",
    )
    assert complete["verdict"] == "accepted"
    assert complete["external_approvable"] is True
    assert complete["second_opinion"]["reviewer_id"] == "cursor-second"


def test_external_issue_evidence_accepts_structured_source_and_target_spans() -> None:
    payload = {
        "issues": [
            {
                "unit_id": "figure-unit",
                "source_span": "Figure label sources:\n- A Button Stack",
                "target_span": "Figure label translations:\n- 按钮堆栈",
            },
            {
                "unit_id": "table-unit",
                "source_span": "Width and Height",
                "target_span": "Width 和 Height",
            },
        ]
    }
    evidence = {
        "figure-unit": (
            "Figure label sources:\n- A Button Stack",
            "Figure label translations:\n- 按钮堆栈",
        ),
        "table-unit": (
            "Name | Description\nWidth and Height | Explicit dimensions",
            "名称 | 说明\nWidth 和 Height | 显式尺寸",
        ),
    }

    _validate_issue_evidence(payload, evidence)


def test_batch_identifiers_reject_path_traversal(prepared_project: Path) -> None:
    existing = {path.name for path in (prepared_project / "batches").iterdir()}
    for prefix in ("../escaped", r"..\escaped", "/absolute", r"C:\absolute"):
        with pytest.raises(ValueError, match="batch ID must match"):
            create_batches(prepared_project, "1", max_words=300, prefix=prefix)
    with pytest.raises(ValueError, match="batch ID must match"):
        create_batches(prepared_project, "1", max_words=300, prefix="x" * 128)
    assert {path.name for path in (prepared_project / "batches").iterdir()} == existing
    for batch_id in ("../escaped", r"..\escaped", "/absolute", r"C:\absolute"):
        with pytest.raises(ValueError, match="batch ID must match"):
            load_manifest(prepared_project, batch_id)


def test_closed_review_issues_require_resolution_evidence(
    prepared_project: Path,
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="evidence")[0]
    unit_id = manifest.translatable_unit_ids[0]
    base = {
        "issue_id": "evidence-r001",
        "batch_id": manifest.batch_id,
        "unit_id": unit_id,
        "severity": Severity.MAJOR,
        "type": IssueType.TECHNICAL,
        "explanation": "Synthetic issue",
        "reviewer": "test",
    }
    with pytest.raises(ValueError, match="non-empty resolution"):
        ReviewIssue(**base, status=IssueStatus.RESOLVED)

    invalid_input = prepared_project / "reviews" / "invalid-closed.jsonl"
    invalid_input.write_text(
        json.dumps({**base, "status": "rejected"}) + "\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="non-empty resolution"):
        import_review(prepared_project, manifest.batch_id, invalid_input)

    open_issue = ReviewIssue(**base)
    valid_input = prepared_project / "reviews" / "open-issue.jsonl"
    write_jsonl(valid_input, [open_issue])
    import_review(
        prepared_project, manifest.batch_id, valid_input, preserve_status=True
    )
    with pytest.raises(ValueError, match="must not be empty"):
        resolve_issue(
            prepared_project,
            manifest.batch_id,
            open_issue.issue_id,
            IssueStatus.REJECTED,
            "   ",
        )


def test_external_review_domain_is_project_specific(prepared_project: Path) -> None:
    reviewer = ExternalReviewerConfig(
        id="claude",
        driver="claude-code",
        command="claude",
        model="claude-sonnet-5",
        fast=False,
    )
    with pytest.raises(ValueError, match="domain_expertise must not be empty"):
        ExternalReviewConfig(reviewers=[reviewer], domain_expertise="   ")

    config = load_project(prepared_project)
    config.external_review = ExternalReviewConfig(
        reviewers=[reviewer],
        domain_expertise="WPF, .NET Framework 4.5, C#, and XAML",
    )
    save_project(prepared_project, config)
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="domain")[0]
    packet, _ = _packet_text(prepared_project, manifest.batch_id)
    assert "# Required subject-matter expertise" in packet
    assert "WPF, .NET Framework 4.5, C#, and XAML" in packet
    for prompt in (
        _claude_prompt(Path("review-packet.md")),
        _antigravity_prompt(Path("review-packet.md")),
    ):
        assert "subject-matter expertise declared in the review packet" in prompt
        assert ".NET/WPF" not in prompt
    assert PROMPT_VERSION == "external-review-v3"


def test_approval_cannot_stamp_a_revision_with_stale_evidence(
    prepared_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = create_batches(prepared_project, "1", max_words=300, prefix="race")[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    assert run_qa(prepared_project, manifest.batch_id).passed
    empty_review = prepared_project / "reviews" / "race-empty.jsonl"
    empty_review.write_text("", encoding="utf-8")
    import_review(prepared_project, manifest.batch_id, empty_review)

    current = translation_map(prepared_project)
    revised_records = [
        current[unit_id].model_copy(
            update={"target_text": current[unit_id].target_text + " revised"}
        )
        for unit_id in manifest.translatable_unit_ids
    ]
    revision_input = prepared_project / "batches" / manifest.batch_id / "revision-race.jsonl"
    write_jsonl(revision_input, revised_records)

    fingerprint_seen = threading.Event()
    release_approval = threading.Event()
    original_fingerprint = quality_module.batch_translation_fingerprint

    def paused_fingerprint(root: Path, batch_id: str) -> str:
        value = original_fingerprint(root, batch_id)
        if threading.current_thread().name == "approval-thread":
            fingerprint_seen.set()
            if not release_approval.wait(5):
                raise TimeoutError("test did not release approval")
        return value

    monkeypatch.setattr(
        quality_module, "batch_translation_fingerprint", paused_fingerprint
    )
    errors: list[BaseException] = []

    def approve() -> None:
        try:
            approve_batch(prepared_project, manifest.batch_id, "machine")
        except BaseException as exc:  # noqa: BLE001 - propagate thread failure
            errors.append(exc)

    def revise() -> None:
        try:
            submit_translation(prepared_project, manifest.batch_id, revision_input)
        except BaseException as exc:  # noqa: BLE001 - propagate thread failure
            errors.append(exc)

    approval_thread = threading.Thread(target=approve, name="approval-thread")
    revision_thread = threading.Thread(target=revise, name="revision-thread")
    approval_thread.start()
    assert fingerprint_seen.wait(5)
    revision_thread.start()
    time.sleep(0.2)
    release_approval.set()
    approval_thread.join(5)
    revision_thread.join(5)
    assert not approval_thread.is_alive()
    assert not revision_thread.is_alive()
    assert not errors
    assert {
        translation_map(prepared_project)[unit_id].status
        for unit_id in manifest.translatable_unit_ids
    } == {ProjectStatus.REVISED}
    with pytest.raises(ValueError, match="QA report is stale"):
        approve_batch(prepared_project, manifest.batch_id, "machine")


def test_qa_cannot_promote_a_revision_it_did_not_check(
    prepared_project: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = create_batches(
        prepared_project, "1", max_words=300, prefix="qa-race"
    )[0]
    _submit_identity_translations(prepared_project, manifest.batch_id)
    current = translation_map(prepared_project)
    revised_records = [
        current[unit_id].model_copy(
            update={"target_text": current[unit_id].target_text + " revised"}
        )
        for unit_id in manifest.translatable_unit_ids
    ]
    revision_input = (
        prepared_project / "batches" / manifest.batch_id / "qa-race-revision.jsonl"
    )
    write_jsonl(revision_input, revised_records)

    fingerprint_seen = threading.Event()
    release_qa = threading.Event()
    original_fingerprint = quality_module.batch_translation_fingerprint

    def paused_fingerprint(root: Path, batch_id: str) -> str:
        value = original_fingerprint(root, batch_id)
        if threading.current_thread().name == "qa-thread":
            fingerprint_seen.set()
            if not release_qa.wait(5):
                raise TimeoutError("test did not release QA")
        return value

    monkeypatch.setattr(
        quality_module, "batch_translation_fingerprint", paused_fingerprint
    )
    reports: list[object] = []
    errors: list[BaseException] = []

    def qa() -> None:
        try:
            reports.append(run_qa(prepared_project, manifest.batch_id))
        except BaseException as exc:  # noqa: BLE001 - propagate thread failure
            errors.append(exc)

    def revise() -> None:
        try:
            submit_translation(prepared_project, manifest.batch_id, revision_input)
        except BaseException as exc:  # noqa: BLE001 - propagate thread failure
            errors.append(exc)

    qa_thread = threading.Thread(target=qa, name="qa-thread")
    revision_thread = threading.Thread(target=revise, name="qa-revision-thread")
    qa_thread.start()
    assert fingerprint_seen.wait(5)
    revision_thread.start()
    time.sleep(0.2)
    assert revision_thread.is_alive()
    release_qa.set()
    qa_thread.join(5)
    revision_thread.join(5)

    assert not qa_thread.is_alive()
    assert not revision_thread.is_alive()
    assert not errors
    assert len(reports) == 1
    assert {
        translation_map(prepared_project)[unit_id].status
        for unit_id in manifest.translatable_unit_ids
    } == {ProjectStatus.REVISED}
    assert not quality_module.qa_report_is_current(
        prepared_project, manifest.batch_id
    )


def test_grouped_code_secondary_ids_are_unique_in_bilingual_html(
    prepared_project: Path,
) -> None:
    units = read_jsonl(prepared_project / "derived" / "units.jsonl", SourceUnit)
    candidates = [
        unit
        for unit in units
        if unit.page == 1 and unit.render_policy is RenderPolicy.INCLUDE
    ][:2]
    assert len(candidates) == 2
    replacements: list[SourceUnit] = []
    for index, unit in enumerate(candidates):
        source = "first line" if index == 0 else "    second line"
        replacements.append(
            SourceUnit.model_validate(
                {
                    **unit.model_dump(mode="json"),
                    "kind": "code",
                    "source_text": source,
                    "source_markdown": None,
                    "source_hash": sha256_text(source),
                    "translatable": False,
                    "protected_tokens": [],
                    "asset_refs": [],
                    "table": None,
                    "code_language": "text",
                    "figure_labels": [],
                    "sidebar_id": None,
                    "sidebar_role": None,
                    "callout_kind": None,
                    "continues_from_previous": index == 1,
                    "continued_to_next": index == 0,
                }
            )
        )
    replacement_map = {unit.unit_id: unit for unit in replacements}
    write_jsonl(
        prepared_project / "derived" / "units.jsonl",
        [replacement_map.get(unit.unit_id, unit) for unit in units],
    )
    outputs = render_project(
        prepared_project,
        "1",
        "grouped-code-ids",
        allow_draft=True,
    )
    rendered_html = Path(outputs["html"]).read_text(encoding="utf-8")
    for unit in replacements:
        assert rendered_html.count(f'id="{unit.unit_id}"') == 1
    report = json.loads(Path(outputs["render_qa"]).read_text(encoding="utf-8"))
    assert report["passed"]


def test_render_qa_does_not_treat_data_attributes_as_element_ids() -> None:
    rendered_html = (
        '<article id="u1" data-sidebar-id="shared"></article>'
        '<article id="u2" data-sidebar-id="shared"></article>'
    )
    assert _render_quality_errors(
        '<a id="u1"></a>\n<a id="u2"></a>',
        rendered_html,
        [
            SourceUnit(
                unit_id=unit_id,
                kind=UnitKind.PARAGRAPH,
                page=1,
                bbox=(0, 0, 1, 1),
                source_text="source",
                source_hash=sha256_text("source"),
                confidence=1.0,
            )
            for unit_id in ("u1", "u2")
        ],
    ) == []


def test_render_qa_allows_blank_blockquote_lines_in_sidebar_code() -> None:
    unit = SourceUnit(
        unit_id="u1",
        kind=UnitKind.CODE,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="<Grid />",
        source_hash=sha256_text("<Grid />"),
        confidence=1.0,
        translatable=False,
        code_language="xaml",
    )
    fence = chr(96) * 3
    markdown = f'<a id="u1"></a>\n> {fence}xaml\n>\n> <Grid />\n> {fence}'
    rendered_html = '<article id="u1"><pre><code>&lt;Grid /&gt;</code></pre></article>'

    assert _render_quality_errors(markdown, rendered_html, [unit]) == []


def test_render_qa_still_rejects_a_nested_blockquote_marker() -> None:
    unit = SourceUnit(
        unit_id="u1",
        kind=UnitKind.NOTE,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="source",
        source_hash=sha256_text("source"),
        confidence=1.0,
    )
    errors = _render_quality_errors(
        '<a id="u1"></a>\n> > [!NOTE]',
        '<article id="u1"><blockquote>note</blockquote></article>',
        [unit],
    )

    assert "nested-admonition-marker" in errors
