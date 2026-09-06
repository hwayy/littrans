from __future__ import annotations

from pathlib import Path

import fitz
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
    result = prepare_source(project)
    assert result["prepared_pages"] == [1, 2]
    assert not verify_fidelity(project)["passed"]
    assets = load_assets(project)
    assert assets
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
    assert prepare_source(project)["prepared_pages"] == []
    assert verify_fidelity(project, "1")["passed"]


def test_missing_and_changed_crop_cannot_pass(project: Path) -> None:
    prepare_source(project, "1")
    approve(project)
    asset = next(iter(load_assets(project).values()))
    path = project / asset.fragments[0].png_path
    path.write_bytes(b"changed")
    assert not verify_fidelity(project, "1")["passed"]
    with pytest.raises(ValueError, match="changed original crop"):
        build_source_review_packet(project, "1")
    with pytest.raises(ValueError, match="cache is corrupt"):
        prepare_source(project, "1", replace=True)


def test_review_bound_to_packet_and_page(project: Path) -> None:
    prepare_source(project, "1")
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
    prepare_source(project, "1")
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    payload = read_json(Path(packet["packet_path"]))
    review["pages"][0]["override"] = {"regions": [{"id": a["id"], "bbox": a["fragments"][0]["bbox"], "kind": a["kind"], "grouping_pending": False} for a in payload["pages"][0]["assets"]]}
    write_json(project / "override.json", review)
    result = import_source_review(project, project / "override.json", True)
    assert result["requires_new_packet"]
    assert not verify_fidelity(project, "1")["passed"]
    with pytest.raises(ValueError, match="stale"):
        import_source_review(project, project / "override.json", True)


def test_multifragment_asset_keeps_order_without_filling_gap(project: Path) -> None:
    prepare_source(project, "1")
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
    prepare_source(project, "1")
    approve(project)
    path = project / "derived/units.jsonl"
    units = read_jsonl(path, SourceUnit)
    units[0].source_markdown = "Dropped formula"
    write_jsonl(path, units)
    assert not verify_fidelity(project, "1")["passed"]


def test_review_import_rolls_back_if_later_override_invalid(project: Path) -> None:
    prepare_source(project)
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
    prepare_source(project, "1")
    assets = load_assets(project)
    next(iter(assets.values())).content_sha256 = "0" * 64
    write_jsonl(project / "derived/fidelity-assets.jsonl", assets.values())
    with pytest.raises(ValueError, match="provenance"):
        build_source_review_packet(project, "1")


def test_verify_is_read_only_and_safe_under_project_lock(project: Path) -> None:
    from littrans.storage import project_write_lock

    prepare_source(project, "1")
    approve(project)
    before = (project / "derived/units.jsonl").read_bytes()
    with project_write_lock(project):
        assert verify_fidelity(project, "1")["passed"]
    assert (project / "derived/units.jsonl").read_bytes() == before


def test_partial_verification_rejects_global_duplicate_unit_ids(project: Path) -> None:
    prepare_source(project)
    approve(project)
    path = project / "derived/units.jsonl"
    units = read_jsonl(path, SourceUnit)
    second = next(u for u in units if u.page == 2)
    second.unit_id = units[0].unit_id
    write_jsonl(path, units)
    result = verify_fidelity(project, "1")
    assert not result["passed"]
    assert "duplicate source unit" in result["errors"][0]["message"]


def test_parser_box_cutting_one_prose_word_promotes_complete_block() -> None:
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
    assert regions[0]["kind"] == "mixed-region"
    assert regions[0]["grouping_pending"]
    assert regions[0]["bbox"][2] >= 80


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
