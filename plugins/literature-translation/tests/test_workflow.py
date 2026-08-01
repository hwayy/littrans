from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

import littrans.external_review as external_review
from littrans.batching import create_batches, refresh_batch
from littrans.external_review import (
    _evidence_map,
    _packet_text,
    _parse_antigravity,
    _parse_claude,
    _require_machine_reviewed,
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
)
from littrans.migration import migrate_translations
from littrans.models import (
    CalloutKind,
    ExternalReviewConfig,
    ExternalReviewerConfig,
    ExternalReviewRun,
    ExternalReviewVerdict,
    IssueStatus,
    IssueType,
    ProjectStatus,
    ReaderNote,
    RenderPolicy,
    ReviewIssue,
    Severity,
    SidebarRole,
    SourceUnit,
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
    _continued_sidebar_markdown,
    _merge_continued_sidebar_html,
    _render_target_text,
    _target_markdown,
    _unit_html,
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
    assert _unit_html(caption, normalized) == "<figcaption>图 11-5 资源库中的操作</figcaption>"
    assert _unit_html(caption, source) == f"<figcaption>{source}</figcaption>"

    table_target = "表 11-5。资源属性"
    normalized_table = _render_target_text(caption, table_target)
    assert normalized_table == "表 11-5 资源属性"
    assert _target_markdown(caption, normalized_table) == "*表 11-5 资源属性*"
    assert _unit_html(caption, normalized_table) == "<figcaption>表 11-5 资源属性</figcaption>"

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
    assert "<strong>Note</strong>" in _unit_html(unit, unit.source_text)
    assert "<strong>注意</strong>" in _unit_html(unit, "中文提示正文。")

    tip = unit.model_copy(
        update={
            "source_text": "■Tip Source tip body.",
            "source_hash": sha256_text("■Tip Source tip body."),
        }
    )
    assert "<strong>Tip</strong>" in _unit_html(tip, tip.source_text)
    assert "<strong>提示</strong>" in _unit_html(tip, "中文提示正文。")
    assert _target_markdown(tip, "中文提示正文。").startswith("> [!TIP]\n")

    whats_new = unit.model_copy(
        update={
            "source_text": "■What’s New Source update body.",
            "source_hash": sha256_text("■What’s New Source update body."),
            "callout_kind": CalloutKind.WHATS_NEW,
        }
    )
    assert "<strong>What's New</strong>" in _unit_html(
        whats_new, whats_new.source_text
    )
    assert "<strong>新增内容</strong>" in _unit_html(whats_new, "中文新增正文。")
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
    assert 'class="sidebar-fragment sidebar-title"' in _unit_html(title, "DPI 缩放")
    assert 'class="sidebar-fragment sidebar-body"' in _unit_html(body, "侧栏正文。")

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
        unit, "选择 96 dpi。"
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
    assert "timed out after 330 seconds" in str(exc_info.value)


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

    _require_machine_reviewed(prepared_project, manifest.batch_id)


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
