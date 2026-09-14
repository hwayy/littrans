from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest

from littrans.fidelity import (
    build_source_review_packet,
    import_source_review,
    prepare_source,
    verify_fidelity,
)
from littrans.fidelity_models import asset_reference_ids, load_assets
from littrans.models import ProjectConfig, SourceUnit
from littrans.storage import (
    initialize_project_dirs,
    read_json,
    read_jsonl,
    save_project,
    sha256_file,
    write_json,
    write_jsonl,
)


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import littrans.fidelity as fidelity

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "Let x = 1. Then the result holds.")
        page.draw_line((100, 100), (150, 100))
        page = doc.new_page()
        page.insert_text((60, 80), "The second page remains unreviewed.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="test", title="test", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=2, profile="technical-book"))
    return tmp_path


def approve(root: Path, pages: str = "1") -> dict:
    packet = build_source_review_packet(root, pages)
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "independent-test-reviewer"
    for page in review["pages"]:
        for key in ("viewed_original", "coverage_complete", "boundaries_complete", "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
            page[key] = True
    path = root / "review.json"
    write_json(path, review)
    return import_source_review(root, path, confirm_visual_review=True)


def test_source_assets_and_review_gate(project: Path) -> None:
    result = prepare_source(project, allow_missing_layout=True)
    assert result["prepared_pages"] == [1, 2]
    assert not verify_fidelity(project)["passed"]
    assets = load_assets(project)
    assert assets
    assert all("formula_conditions" not in asset.model_dump(mode="json") for asset in assets.values())
    for asset in assets.values():
        fragment = asset.fragments[0]
        assert fragment.dpi in (300, 450)
        assert all((project / path).is_file() for path in fragment.file_sha256)
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    assert all(u.latex is None for u in units)
    assert any(asset_reference_ids(u.source_text) for u in units)
    assert approve(project)["approved_pages"] == [1]
    assert verify_fidelity(project, "1")["passed"]
    assert not verify_fidelity(project, "2")["passed"]
    assert prepare_source(project, allow_missing_layout=True)["prepared_pages"] == []
    assert verify_fidelity(project, "1")["passed"]


def test_missing_and_changed_crop_cannot_pass(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    approve(project)
    asset = next(iter(load_assets(project).values()))
    path = project / asset.fragments[0].png_path
    path.write_bytes(b"changed")
    assert not verify_fidelity(project, "1")["passed"]
    with pytest.raises(ValueError, match="changed original crop"):
        build_source_review_packet(project, "1")
    with pytest.raises(ValueError, match="cache is corrupt"):
        prepare_source(project, "1", replace=True, allow_missing_layout=True)


def test_review_bound_to_packet_and_page(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    review["pages"].append(review["pages"][0])
    write_json(project / "bad.json", review)
    with pytest.raises(ValueError, match="duplicate"):
        import_source_review(project, project / "bad.json", True)
    review["pages"].pop()
    review["packet_sha256"] = "0" * 64
    write_json(project / "bad.json", review)
    with pytest.raises(ValueError, match="hash"):
        import_source_review(project, project / "bad.json", True)


def test_review_override_needs_fresh_visual_evidence(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    payload = read_json(Path(packet["packet_path"]))
    review["pages"][0]["override"] = {"regions": [{"preserve_asset_id": a["id"], "bbox": a["fragments"][0]["bbox"], "kind": a["kind"], "grouping_pending": False} for a in payload["pages"][0]["assets"]]}
    write_json(project / "override.json", review)
    result = import_source_review(project, project / "override.json", True)
    assert result["requires_new_packet"]
    assert not verify_fidelity(project, "1")["passed"]
    with pytest.raises(ValueError, match="stale"):
        import_source_review(project, project / "override.json", True)


def test_multifragment_asset_keeps_order_without_filling_gap(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    review["pages"][0]["override"] = {"regions": [{"id": "split-formula", "kind": "math", "fragments": [{"page": 1, "bbox": [60, 68, 90, 86]}, {"page": 1, "bbox": [100, 98, 151, 102]}]}]}
    write_json(project / "split.json", review)
    import_source_review(project, project / "split.json", True)
    asset = load_assets(project)["split-formula"]
    assert len(asset.fragments) == 2
    assert asset.fragments[0].bbox[1] < asset.fragments[1].bbox[1]
    approve(project)
    assert verify_fidelity(project, "1")["passed"]


def test_source_mutation_invalidates_visual_receipt(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    approve(project)
    path = project / "derived/units.jsonl"
    units = read_jsonl(path, SourceUnit)
    units[0].source_markdown = "Dropped formula"
    write_jsonl(path, units)
    assert not verify_fidelity(project, "1")["passed"]


def test_review_import_rolls_back_if_later_override_invalid(project: Path) -> None:
    prepare_source(project, allow_missing_layout=True)
    packet = build_source_review_packet(project)
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    for page in review["pages"]:
        for key in ("viewed_original", "coverage_complete", "boundaries_complete", "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
            page[key] = True
    review["pages"][1]["override"] = {"regions": [{"bbox": [-20, -20, -10, -10], "kind": "math"}]}
    write_json(project / "bad-second-page.json", review)
    with pytest.raises(ValueError, match="empty"):
        import_source_review(project, project / "bad-second-page.json", True)
    assert not (project / "evidence/pages/fidelity-p0001.review.json").is_file()


def test_page_image_and_region_provenance_are_bound(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    assets = load_assets(project)
    next(iter(assets.values())).content_sha256 = "0" * 64
    write_jsonl(project / "derived/fidelity-assets.jsonl", assets.values())
    with pytest.raises(ValueError, match="provenance"):
        build_source_review_packet(project, "1")


def test_verify_is_read_only_and_safe_under_project_lock(project: Path) -> None:
    from littrans.storage import project_write_lock

    prepare_source(project, "1", allow_missing_layout=True)
    approve(project)
    before = (project / "derived/units.jsonl").read_bytes()
    with project_write_lock(project):
        assert verify_fidelity(project, "1")["passed"]
    assert (project / "derived/units.jsonl").read_bytes() == before


def test_partial_verification_rejects_global_duplicate_unit_ids(project: Path) -> None:
    prepare_source(project, allow_missing_layout=True)
    approve(project)
    path = project / "derived/units.jsonl"
    units = read_jsonl(path, SourceUnit)
    second = next(u for u in units if u.page == 2)
    second.unit_id = units[0].unit_id
    write_jsonl(path, units)
    result = verify_fidelity(project, "1")
    assert not result["passed"]
    assert "duplicate source unit" in result["errors"][0]["message"]


def test_parser_box_cutting_one_prose_word_preserves_native_text() -> None:
    from littrans.fidelity import _regions

    class Page:
        def get_text(self, mode: str) -> dict:
            return {"blocks": [{"type": 0, "bbox": [0, 20, 80, 30]}]}

        def get_image_info(self) -> list:
            return []

        def get_drawings(self) -> list:
            return []

    glyphs = [{"id": str(i), "text": c, "font": "SFRM1000", "bbox": [i * 6, 20, i * 6 + 5, 30], "line": "line"} for i, c in enumerate("norm")]
    boxes = [{"label": "inline_formula", "bbox": [0, 30, 11, 45]}]
    regions = _regions(Page(), glyphs, boxes)
    assert regions == []


def test_roman_math_font_rule_does_not_capture_plain_cmr_prose() -> None:
    from littrans.fidelity import _regions

    class Page:
        def get_text(self, mode: str) -> dict:
            return {"blocks": []}

        def get_image_info(self) -> list:
            return []

        def get_drawings(self) -> list:
            return []

    def glyph(i: int, text: str, font: str) -> dict:
        return {"id": str(i), "text": text, "font": font, "bbox": [i * 6, 10, i * 6 + 5, 20], "line": "line"}

    normal = [glyph(i, c, "CMR10") for i, c in enumerate("Plain roman prose")]
    assert _regions(Page(), normal, []) == []
    mixed = [glyph(i, c, "SFRM1000") for i, c in enumerate("Plain roman prose ")]
    mixed += [glyph(i + len(mixed), c, "CMR10") for i, c in enumerate("SU(2)")]
    found = _regions(Page(), mixed, [])
    assert len(found) == 1
    assert found[0]["kind"] == "math"


def test_adjacent_native_math_lines_do_not_merge() -> None:
    from littrans.fidelity import _native, _regions
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((100, 100), "x = 1", fontsize=11)
        page.insert_text((100, 112), "y = 2", fontsize=11)
        glyphs, _ = _native(page)
        regions = _regions(page, glyphs, [])
    assert len(regions) == 2
    assert not set(regions[0]["glyph_ids"]) & set(regions[1]["glyph_ids"])


def test_table_container_keeps_all_original_text() -> None:
    from littrans.fidelity import _native, _regions
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((100, 100), "Label = 1")
        glyphs, _ = _native(page)
        regions = _regions(page, glyphs, [{"label": "table", "bbox": [180, 160, 400, 230]}])
    assert len(regions) == 1
    assert regions[0]["kind"] == "table"
    assert "glyph_ids" not in regions[0]


def test_roman_operator_does_not_discard_display_formula() -> None:
    from littrans.fidelity import _native, _regions
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((100, 100), "sin x = 1")
        glyphs, _ = _native(page)
        regions = _regions(page, glyphs, [{"label": "display_formula", "bbox": [190, 175, 310, 210]}])
    assert len(regions) == 1 and regions[0]["display"]
    assert {g["id"] for g in glyphs if g["text"].strip()} <= set(regions[0]["glyph_ids"])


def test_display_equation_is_separate_and_numbered(project: Path) -> None:
    from littrans.fidelity import _make_unit, _separate_display_units
    prepare_source(project, "1", allow_missing_layout=True)
    assets = load_assets(project)
    asset = next(a for a in assets.values() if a.kind == "math")
    assets[asset.id] = asset.model_copy(update={"display": True})
    marker = "{{asset:" + asset.id + "}}"
    units = [_make_unit(1, "p1-b1", "For a polynomial, take " + marker + "Then continue.", [60, 68, 200, 86], assets),
             _make_unit(1, "p1-b2", "(10.20)", [10, 68, 40, 84], assets)]
    split = _separate_display_units(units, assets)
    assert [u.kind.value for u in split] == ["paragraph", "equation", "paragraph"]
    assert split[1].equation_number == "10.20"
    assert split[1].source_text == marker
    assert not split[1].translatable


def test_display_line_with_prose_stays_translatable(project: Path) -> None:
    from littrans.fidelity import _make_unit
    prepare_source(project, "1", allow_missing_layout=True)
    assets = load_assets(project)
    asset = next(a for a in assets.values() if a.kind == "math")
    assets[asset.id] = asset.model_copy(update={"display": True})
    marker = "{{asset:" + asset.id + "}}"
    formula_only = _make_unit(1, "p1-b8", marker, [60, 68, 200, 86], assets, kind="equation")
    assert not formula_only.translatable
    # Rebuilding the unit with the prose set beside the formula carries the old flag in.
    rebuilt = _make_unit(1, "p1-b8", marker + " for all times " + marker + ".", [60, 68, 200, 86],
                         assets, kind="equation", translatable=formula_only.translatable)
    assert rebuilt.translatable
    assert rebuilt.source_hash != formula_only.source_hash


def test_recoverable_paragraph_cannot_pass_as_image() -> None:
    from littrans.fidelity import _opaque_prose_assets
    text = "For an arbitrary polynomial the matrix function takes the form"
    glyphs = [{"id": str(i), "text": c, "font": "SFRM1000"} for i, c in enumerate(text)]
    current = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "opaque", "kind": "mixed-region", "fragments": [{"glyph_ids": [g["id"] for g in glyphs]}]}]}
    assert _opaque_prose_assets(current) == ["opaque"]


def test_unsupported_vector_keeps_unreviewed_original(project: Path) -> None:
    from littrans.fidelity import _asset, _native, _regions
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((100, 100), "x = 1")
        page.draw_line((119, 85), (119, 105))
        glyphs, _ = _native(page)
        region = next(r for r in _regions(page, glyphs, []) if r.get("glyph_ids"))
        asset = _asset(project, doc, 1, "a" * 64, region, glyphs)
    assert asset.kind == "mixed-region" and asset.grouping_pending
    assert asset.fragments[0].export_method == "raw-region"
    assert (project / asset.fragments[0].png_path).is_file()


def test_roman_math_operators_are_not_opaque_prose() -> None:
    from littrans.fidelity import _opaque_prose_assets
    text = "sin x + cos x + tan x + log x + exp x + max x"
    glyphs = [{"id": str(i), "text": c, "font": "Helvetica"} for i, c in enumerate(text)]
    current = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "formula", "kind": "math", "fragments": [{"glyph_ids": [g["id"] for g in glyphs]}]}]}
    assert _opaque_prose_assets(current) == []


def test_prose_article_before_math_font_space_stays_text() -> None:
    from littrans.fidelity import _regions
    class Page:
        def get_image_info(self): return []
        def get_drawings(self): return []
    text = "This is a L2"
    glyphs = [{"id": str(i), "text": c, "font": "SFRM1000" if i < 9 else "CMR10", "bbox": [i * 6, 10, i * 6 + 5, 20], "line": "line"} for i, c in enumerate(text)]
    regions = _regions(Page(), glyphs, [])
    assert len(regions) == 1
    assert "8" not in regions[0]["glyph_ids"]
    assert {"10", "11"} <= set(regions[0]["glyph_ids"])


def test_declared_formula_conditions_need_specific_review_and_translation(project):
    from littrans.models import AssetTranslation
    from littrans.representations import validate_asset_translations
    pdf = project / 'source/book.pdf'
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), 'x = 1 and n is odd')
        page.insert_text((60, 100), 'x = 2 and n is even')
        doc.new_page()
        doc.save(pdf)
    config = ProjectConfig(project_id='test', title='test', source_path='source/book.pdf', source_sha256=sha256_file(pdf), source_pages=2, profile='technical-book')
    save_project(project, config)
    prepare_source(project, '1', allow_missing_layout=True)
    packet = build_source_review_packet(project, '1')
    payload = read_json(Path(packet['packet_path']))['pages'][0]
    conditions = []
    glyphs = payload['ledger']['glyphs']
    for baseline in (80, 100):
        line = [g for g in glyphs if g['baseline'] == baseline]
        start = next(i for i, g in enumerate(line) if g['text'] == 'a')
        selected = line[start:]
        conditions.append({'glyph_ids': [g['id'] for g in selected], 'source_text': ''.join(g['text'] for g in selected)})
    review = read_json(Path(packet['review_template']))
    review['reviewer'] = 'synthetic-formula-oracle'
    review['pages'][0]['override'] = {'regions': [{'id': 'cases', 'kind': 'math', 'display': True, 'bbox': [58, 65, 200, 104], 'formula_conditions': conditions}]}
    write_json(project/'formula-override.json', review)
    assert import_source_review(project, project/'formula-override.json', True)['changed_pages'] == [1]
    assert approve(project)['approved_pages'] == []  # Generic review is insufficient.
    packet = build_source_review_packet(project, '1')
    review = read_json(Path(packet['review_template']))
    review['reviewer'] = 'synthetic-formula-oracle'
    for key in ('viewed_original', 'coverage_complete', 'boundaries_complete', 'reading_order_correct', 'grouping_checked', 'layout_fallback_checked', 'formula_conditions_checked'):
        review['pages'][0][key] = True
    write_json(project/'formula-review.json', review)
    assert import_source_review(project, project/'formula-review.json', True)['approved_pages'] == [1]
    assert verify_fidelity(project, '1')['passed']
    unit = next(u for u in read_jsonl(project/'derived/units.jsonl', SourceUnit) if 'cases' in u.source_text)
    assert unit.translatable
    assert validate_asset_translations(project, unit.source_text, [])
    assert validate_asset_translations(project, unit.source_text, [AssetTranslation(asset_id='cases', language_present=False, notes='Not acceptable')])
    assert not validate_asset_translations(project, unit.source_text, [AssetTranslation(asset_id='cases', target_text='\u7b2c\u4e00\u884c\u4e3a\u5947\u6570\uff0c\u7b2c\u4e8c\u884c\u4e3a\u5076\u6570\u3002')])


def test_canvas_override_recovers_existing_ink_without_changing_pdf(project):
    pdf = project/'source/book.pdf'
    with fitz.open() as doc:
        page = doc.new_page(width=140, height=150)
        page.insert_text((90, 60), 'x=1)')
        page.set_mediabox(fitz.Rect(0, 0, 100, 150))
        page.set_cropbox(fitz.Rect(0, 0, 100, 150))
        doc.new_page()
        doc.save(pdf)
    digest = sha256_file(pdf)
    save_project(project, ProjectConfig(project_id='test', title='test', source_path='source/book.pdf', source_sha256=digest, source_pages=2, profile='technical-book'))
    prepare_source(project, '1', allow_missing_layout=True)
    original_glyphs = read_json(project/'derived/fidelity-pages/p0001.json')['glyphs']
    packet = build_source_review_packet(project, '1')
    review = read_json(Path(packet['review_template']))
    review['reviewer'] = 'synthetic-offpage-oracle'
    review['pages'][0]['override'] = {'page_canvas_bbox': [0, 0, 140, 150], 'regions': [{'id': 'full-equation', 'kind': 'math', 'display': True, 'bbox': [88, 45, 130, 65]}]}
    write_json(project/'canvas-override.json', review)
    assert import_source_review(project, project/'canvas-override.json', True)['changed_pages'] == [1]
    ledger = read_json(project/'derived/fidelity-pages/p0001.json')
    assert any(g['text'] == ')' and g['bbox'][2] > 100 for g in ledger['glyphs'])
    mapped = {g['id']: g for g in ledger['glyphs']}
    for old in original_glyphs:
        assert (mapped[old['id']]['text'], mapped[old['id']]['origin']) == (old['text'], old['origin'])
    assert any(g['id'].startswith('overflow-') for g in ledger['glyphs'])
    assert ledger['original_page_bbox'] == [0, 0, 100, 150]
    assert (project/ledger['overflow_evidence']['path']).is_file()
    assert sha256_file(pdf) == digest
    assert approve(project)['approved_pages'] == []
    assert build_source_review_packet(project, '1')['packet_id']
