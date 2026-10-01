from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
from config_fixtures import save_project

from littrans import fidelity
from littrans.fidelity_models import load_assets
from littrans.models import ProjectConfig, SourceUnit
from littrans.source_render import render_source_review
from littrans.storage import initialize_project_dirs, read_json, read_jsonl, sha256_file, write_json

# Six panels in four rows, with a detector chart spanning both third-row images.
PANELS = [[94, 86, 235, 192], [240, 86, 380, 192], [167, 202, 308, 306],
          [94, 314, 235, 420], [240, 314, 380, 420], [168, 428, 306, 532]]
CAPTION = [91, 540, 384, 570]
OUTLINE = [94, 86, 380, 532]


@pytest.fixture
def figure_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=476, height=720)
        for panel in PANELS:
            box = fitz.Rect(panel)
            page.draw_rect(box, color=(0, 0, 0))
            page.draw_line(box.bl + (5, -10), box.tr + (-5, 10), color=(1, 0, 0))
            page.insert_text(box.tl + (8, 14), "h=0", fontsize=8)
        page.insert_text((95, 552), "Figure 11.1. Six panels.", fontsize=10)
        page.insert_text((60, 620), "Ordinary body text stays outside the figure.", fontsize=10)
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="figures", title="Synthetic figures",
                                        profile="technical-book", source_path="source/book.pdf",
                                        source_sha256=sha256_file(pdf), source_pages=1))
    items = [{"label": "image", "bbox": [v * 2 for v in box]} for box in PANELS]
    items += [{"label": "chart", "bbox": [188, 628, 760, 840]},
              {"label": "figure_caption", "bbox": [v * 2 for v in CAPTION]}]
    def detect(images: list[Path], store: Path) -> dict[str, Any]:
        result = {"status": "ok", "fingerprint": "synthetic-six-panels",
                  "pages": {sha256_file(images[0]): items}}
        write_json(store / "synthetic-six-panels.json", result)
        return result
    monkeypatch.setattr(fidelity, "detect_layout", detect)
    return tmp_path


def test_six_panels_export_one_raw_image_with_caption_text(figure_project: Path) -> None:
    fidelity.prepare_source(figure_project)
    asset = next(a for a in load_assets(figure_project).values() if a.kind == "figure")
    assert len(asset.fragments) == 1
    fragment = asset.fragments[0]
    assert fragment.export_method == "raw-region"
    assert fitz.Rect(fragment.bbox).contains(fitz.Rect(OUTLINE))
    assert all(fitz.Rect(fragment.bbox).contains(fitz.Rect(box)) for box in PANELS)
    assert fragment.bbox[3] < CAPTION[1]
    assert fragment.png_path and fragment.svg_path
    # Match the complete PDF crop pixel for pixel, including all graphics and gaps.
    with fitz.open(figure_project / "source/book.pdf") as doc, fitz.open() as clipped:
        rect = fitz.Rect(fragment.bbox)
        page = clipped.new_page(width=rect.width, height=rect.height)
        page.show_pdf_page(page.rect, doc, 0, clip=rect)
        expected = page.get_pixmap(dpi=fragment.dpi, alpha=False)
        actual = fitz.Pixmap(str(figure_project / fragment.png_path))
        assert (actual.width, actual.height, actual.samples) == (
            expected.width, expected.height, expected.samples)
    units = read_jsonl(figure_project / "derived/units.jsonl", SourceUnit)
    figure = next(u for u in units if u.kind == "figure")
    caption = next(u for u in units if u.kind == "caption")
    assert "Six panels" in caption.source_text and "{{asset:" not in caption.source_text
    assert caption.parent_id == figure.unit_id
    assert any("Ordinary body text" in u.source_text for u in units)
    rendered = render_source_review(figure_project)
    markup = Path(rendered["html"]).read_text(encoding="utf-8")
    assert markup.count(f'alt="原式 {asset.id}"') == 1
    assert "Six panels" in markup


def test_figure_join_discards_member_fragments_and_glyph_only_export() -> None:
    regions = [{"kind": "figure", "bbox": box, "glyph_ids": [f"g{i}"],
                "fragments": [{"bbox": box, "glyph_ids": [f"g{i}"]}],
                "display": True, "grouping_pending": False, "provenance": ["test"]}
               for i, box in enumerate(PANELS)]
    joined = fidelity._join_figure_panels(regions, [], [
        {"label": "figure_caption", "bbox": [v * 2 for v in CAPTION]}])
    assert len(joined) == 1 and joined[0]["bbox"] == OUTLINE
    assert "fragments" not in joined[0] and "glyph_ids" not in joined[0]


def test_whole_outline_with_prose_in_its_empty_corner_stays_separate() -> None:
    # Pairwise unions can be clear while the whole outline encloses unrelated text.
    boxes = [[0, 0, 40, 40], [45, 0, 85, 40], [45, 45, 85, 85]]
    regions: list[dict[str, Any]] = [
        {"kind": "figure", "bbox": b, "display": True, "grouping_pending": False,
         "provenance": []} for b in boxes]
    prose = [{"id": "prose", "text": "body", "bbox": [10, 60, 30, 70], "size": 10}]
    caption = {"label": "figure_caption", "bbox": [0, 180, 170, 200]}
    assert len(fidelity._join_figure_panels(regions, prose, [caption])) == 3


def test_legacy_fragments_require_explicit_region_correction(figure_project: Path) -> None:
    fidelity.prepare_source(figure_project)
    asset = next(a for a in load_assets(figure_project).values() if a.kind == "figure")
    packet = fidelity.build_source_review_packet(figure_project)
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "synthetic-region-correction"
    # Reproduce a legacy reviewed page: normal replacement must replay its decision.
    region = {"id": asset.id, "kind": "figure", "bbox": OUTLINE,
              "fragments": [{"bbox": box} for box in PANELS], "display": True,
              "grouping_pending": False, "provenance": ["synthetic-legacy"]}
    review["pages"][0]["override"] = {"regions": [region]}
    path = figure_project / "correction.json"
    write_json(path, review)
    assert fidelity.import_source_review(figure_project, path, True)["approved_pages"] == []
    fidelity.prepare_source(figure_project, replace=True)
    assert len(load_assets(figure_project)[asset.id].fragments) == 6
    # A supported regions override replaces all fragments, retaining the asset ID.
    packet = fidelity.build_source_review_packet(figure_project)
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "synthetic-region-correction"
    region.pop("fragments")
    review["pages"][0]["override"] = {"regions": [region]}
    write_json(path, review)
    changed = fidelity.import_source_review(figure_project, path, True)
    assert changed["changed_pages"] == [1] and changed["approved_pages"] == []
    assert len(load_assets(figure_project)[asset.id].fragments) == 1
    fidelity.prepare_source(figure_project, replace=True)
    assert len(load_assets(figure_project)[asset.id].fragments) == 1
