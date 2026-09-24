"""Regressions for PLUGIN-ISSUES.md LT-016..018 and the zero-width negation slash."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
from test_asset_representation import candidate_input, review_input
from test_asset_representation import project as representation_project
from test_fidelity_source import project as source_project

from littrans import fidelity
from littrans.fidelity import _compose_combining_marks, _native
from littrans.fidelity_models import FidelityFragment
from littrans.glyph_export import build_owned_fragment, glyph_ink_boxes
from littrans.project import load_project
from littrans.storage import read_json, sha256_file, write_json

asset_project = representation_project
fidelity_project = source_project

SVG = "{http://www.w3.org/2000/svg}"
XLINK = "{http://www.w3.org/1999/xlink}"
COMBINING_FONTS = [Path(r"C:\Windows\Fonts\times.ttf"), Path(r"C:\Windows\Fonts\arial.ttf"),
                   Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/Library/Fonts/Arial.ttf")]


def _glyph(gid: str, text: str, x: float, width: float, font: str = "CMSY10", size: float = 10.9) -> dict[str, Any]:
    return {"id": gid, "text": text, "bbox": [x, 390, x + width, 402], "font": font, "size": size,
            "origin": [x, 400], "baseline": 400, "line": "b0-l0"}


def _line(glyphs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"id": "b0", "bbox": [0, 390, 300, 402], "lines": [[g["id"] for g in glyphs]]}]


def test_tex_negation_slash_folds_into_the_following_relation() -> None:
    # rawdict: ω, then the slash at ω's end pen (zero width), a synthesized thick space, then ∈.
    omega = _glyph("w", "ω", 270.0, 7.4, "CMMI10")
    slash = _glyph("n", "\u0338", 277.4, 0.0)
    space = _glyph("s", " ", 277.4, 3.5, "CMMI10")
    member = _glyph("m", "\u2208", 280.8, 6.4)
    tail = _glyph("b", "B", 290.0, 7.0, "CMMI10")
    glyphs = [omega, slash, space, member, tail]
    blocks = _line(glyphs)
    _compose_combining_marks(glyphs, blocks)
    assert [g["text"] for g in glyphs] == ["ω", " ", "\u2209", "B"]
    assert member["origin"] == [280.8, 400] and member["bbox"] == [280.8, 390, 287.2, 402]
    assert blocks[0]["lines"] == [["w", "s", "m", "b"]]


def test_unicode_ordered_mark_folds_backwards_and_uncomposable_marks_keep_the_sequence() -> None:
    equals = _glyph("e", "=", 100.0, 7.8, "CMR10")
    slash = _glyph("n", "\u0338", 107.8, 0.0)
    glyphs = [equals, slash]
    blocks = _line(glyphs)
    _compose_combining_marks(glyphs, blocks)
    assert [g["text"] for g in glyphs] == ["\u2260"]
    forall = _glyph("f", "\u2200", 100.0, 7.0)
    slash = _glyph("n", "\u0338", 100.0, 0.0)
    glyphs = [slash, forall]
    blocks = _line(glyphs)
    _compose_combining_marks(glyphs, blocks)
    assert [g["text"] for g in glyphs] == ["\u2200\u0338"] and blocks[0]["lines"] == [["f"]]


def test_marks_with_ink_or_far_from_any_base_are_left_alone() -> None:
    # A spacing (non-zero-width) mark is not this defect; a mark with no base within reach stays.
    wide = _glyph("a", "\u0308", 100.0, 5.0, "CMR10")
    base = _glyph("o", "o", 105.0, 5.0, "CMR10")
    glyphs = [wide, base]
    _compose_combining_marks(glyphs, _line(glyphs))
    assert [g["text"] for g in glyphs] == ["\u0308", "o"]
    far = _glyph("n", "\u0338", 100.0, 0.0)
    distant = _glyph("m", "\u2208", 140.0, 6.4)
    glyphs = [far, distant]
    _compose_combining_marks(glyphs, _line(glyphs))
    assert [g["text"] for g in glyphs] == ["\u0338", "\u2208"]


def test_native_extraction_composes_negated_relations() -> None:
    class _Raw:
        def get_text(self, mode: str) -> dict[str, Any]:
            def span(font: str, chars: list[tuple[str, float, float]]) -> dict[str, Any]:
                return {"font": font, "size": 10.9, "chars": [{"c": c, "bbox": [x, 390, x + w, 402], "origin": [x, 400]} for c, x, w in chars]}
            return {"blocks": [{"type": 0, "bbox": [0, 390, 300, 402], "lines": [{"spans": [
                span("CMMI10", [("ω", 270.0, 7.4)]), span("CMSY10", [("\u0338", 277.4, 0.0)]),
                span("CMMI10", [(" ", 277.4, 3.5)]), span("CMSY10", [("\u2208", 280.8, 6.4)]),
            ]}]}]}

    glyphs, blocks = _native(_Raw())
    assert "".join(g["text"] for g in glyphs) == "ω \u2209"
    assert blocks[0]["lines"] == [["b0-l0-s0-c0", "b0-l0-s2-c0", "b0-l0-s3-c0"]]


def test_ink_boxes_union_every_use_node_sharing_a_glyph_origin() -> None:
    svg = ET.Element(SVG + "svg", {"width": "200", "height": "200"})
    defs = ET.SubElement(svg, SVG + "defs")
    ET.SubElement(defs, SVG + "path", {"id": "f_slash", "d": "M0 -1 L1 -1 L1 11 L0 11 Z"})
    ET.SubElement(defs, SVG + "path", {"id": "f_base", "d": "M-2 2 L6 2 L6 6 L-2 6 Z"})
    for href in ("#f_slash", "#f_base"):
        ET.SubElement(svg, SVG + "use", {XLINK + "href": href, "transform": "matrix(1,0,0,-1,100,100)"})

    class _Page:
        rect = fitz.Rect(0, 0, 200, 200)

        def get_svg_image(self, **kwargs: Any) -> str:
            return ET.tostring(svg, encoding="unicode")

    boxes = glyph_ink_boxes(_Page(), [{"id": "m", "text": "\u2209", "origin": [100, 100], "bbox": [98, 92, 106, 102]}])
    x0, y0, x1, y1 = boxes["m"]
    assert x0 <= 98 and x1 >= 101 and y0 <= 89.5 and y1 >= 101


def _combining_font() -> Path:
    for candidate in COMBINING_FONTS:
        if candidate.is_file():
            return candidate
    pytest.skip("no TrueType font with U+0338 available")


def test_negated_relation_exports_precisely_end_to_end(tmp_path: Path) -> None:
    font = _combining_font()
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 80), "a", fontsize=20, fontname="F0", fontfile=str(font))
        # TeX order: the slash is set after a positional move, before the relation it negates.
        page.insert_text((70, 80), "\u0338=", fontsize=20, fontname="F0", fontfile=str(font))
        glyphs, _ = _native(page)
        assert [g["text"] for g in glyphs if g["text"].strip()] == ["a", "\u2260"]
        composite = next(g for g in glyphs if g["text"] == "\u2260")
        assert composite["origin"][0] == pytest.approx(70)
        boxes = glyph_ink_boxes(page, glyphs)
        assert composite["id"] in boxes
        svg, geometry = build_owned_fragment(page, [composite])
        assert geometry["matched_glyphs"] == 1
        assert sum(node.tag == SVG + "use" for node in ET.fromstring(svg)) == 2
        assert fitz.Rect(geometry["bbox"]).contains(fitz.Rect(boxes[composite["id"]]))


# ---------------------------------------------------------------- LT-018: no per-fragment PDF


def _old_fragment_record(root: Path) -> dict[str, Any]:
    files = {name: sha256_file(root / name) for name in ("original.png", "original.svg", "original.pdf")}
    return {"page": 1, "bbox": [0, 0, 80, 40], "png_path": "original.png", "svg_path": "original.svg", "pdf_path": "original.pdf",
            "width": 80, "height": 40, "file_sha256": files}


def test_fragments_written_with_a_pdf_still_validate_and_dump_unchanged(asset_project: Path) -> None:
    record = _old_fragment_record(asset_project)
    fragment = FidelityFragment.model_validate(record)
    assert fragment.pdf_path == "original.pdf"
    dumped = fragment.model_dump(mode="json")
    assert dumped["pdf_path"] == "original.pdf" and set(dumped["file_sha256"]) == set(record["file_sha256"])
    modern = FidelityFragment.model_validate({k: v for k, v in record.items() if k != "pdf_path"} | {"file_sha256": {k: v for k, v in record["file_sha256"].items() if not k.endswith(".pdf")}})
    assert "pdf_path" not in modern.model_dump(mode="json")
    with pytest.raises(ValueError, match="require hashes"):
        FidelityFragment.model_validate({k: v for k, v in record.items() if k != "pdf_path"})


def test_prepared_fragments_carry_only_svg_and_png(fidelity_project: Path) -> None:
    config = load_project(fidelity_project)
    with fitz.open(config.source(fidelity_project)) as doc:
        asset = fidelity._asset(fidelity_project, doc, 1, config.source_sha256, {"kind": "math", "bbox": [45, 70, 120, 95]}, [])
    fragment = asset.fragments[0]
    folder = fidelity_project / Path(fragment.png_path).parent
    assert fragment.pdf_path is None
    assert {p.name for p in folder.iterdir()} == {"original.svg", "original.png", "evidence.json"}
    assert set(fragment.file_sha256) == {fragment.png_path, fragment.svg_path}
    assert "pdf_path" not in asset.model_dump(mode="json")["fragments"][0]


def test_receipt_listing_a_pdf_is_stale_and_reexported_in_place(fidelity_project: Path) -> None:
    config = load_project(fidelity_project)
    region = {"kind": "math", "bbox": [45, 70, 120, 95]}
    with fitz.open(config.source(fidelity_project)) as doc:
        first = fidelity._asset(fidelity_project, doc, 1, config.source_sha256, region, [])
        folder = fidelity_project / Path(first.fragments[0].png_path).parent
        receipt = read_json(folder / "evidence.json")
        # Simulate a 0.6.0 directory: a PDF on disk and listed in the receipt.
        (folder / "original.pdf").write_bytes(b"%PDF-1.4 legacy")
        receipt["files"]["original.pdf"] = sha256_file(folder / "original.pdf")
        write_json(folder / "evidence.json", receipt)
        (folder / "original.png").write_bytes(b"stale")
        second = fidelity._asset(fidelity_project, doc, 1, config.source_sha256, region, [])
    assert second.id == first.id and Path(second.fragments[0].png_path).parent == Path(first.fragments[0].png_path).parent
    assert not (folder / "original.pdf").exists()
    assert set(read_json(folder / "evidence.json")["files"]) == {"original.svg", "original.png"}
    assert (folder / "original.png").read_bytes() != b"stale"
    assert second.fragments[0].file_sha256 == first.fragments[0].file_sha256


def test_original_links_point_at_the_vector_svg(asset_project: Path) -> None:
    from littrans.representations import import_asset_review, resolve_asset_html, submit_candidates

    candidate_input(asset_project)
    submit_candidates(asset_project, asset_project / "candidate.json")
    review_input(asset_project)
    import_asset_review(asset_project, asset_project / "review.json", True)
    output = asset_project / "output"
    linked = resolve_asset_html(asset_project, "{{asset:a1}}", output, originals_only=True)
    assert 'class="original-image-link" href="original-assets/' in linked and ".svg\"" in linked and ".pdf" not in linked
    listed = resolve_asset_html(asset_project, "{{asset:a2}}", output)
    assert "查看原式 1" in listed and ".pdf" not in listed and not list((output / "original-assets").glob("*.pdf"))


# ---------------------------------------------------------------- LT-017: generator identity


def test_provenance_records_the_writing_build(tmp_path: Path) -> None:
    import littrans
    from littrans.project import initialize_project, rebuild_project

    pdf = tmp_path / "book.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((60, 80), "Let x = 1.")
        doc.save(pdf)
    initialize_project(pdf, tmp_path / "old", "technical-book", title="t")
    generator = read_json(tmp_path / "old/derived/provenance.json")["generator"]
    assert generator["plugin_version"] == littrans.__version__
    assert len(generator["build_digest"]) == 16 and generator["generated_at"].endswith("+00:00")
    rebuild_project(tmp_path / "old", tmp_path / "new")
    rebuilt = read_json(tmp_path / "new/derived/provenance.json")
    assert rebuilt["source_is_copied"] and rebuilt["generator"]["build_digest"] == generator["build_digest"]


def test_source_packet_identity_ignores_the_generator_and_reuse_does_not_rewrite(fidelity_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans import build_info
    from littrans.fidelity import _load_source_packet, build_source_review_packet, prepare_source

    monkeypatch.setattr(build_info, "utc_now", lambda: "2026-09-15T00:00:00+00:00")
    result = prepare_source(fidelity_project, "1", allow_missing_layout=True)
    assert result["generator"]["plugin_version"]
    ledger = read_json(fidelity_project / "derived/fidelity-pages/p0001.json")
    assert ledger["generator"]["generated_at"] == "2026-09-15T00:00:00+00:00"
    first = build_source_review_packet(fidelity_project, "1")
    packet_path = Path(first["packet_path"])
    assert read_json(packet_path)["generator"]["generated_at"] == "2026-09-15T00:00:00+00:00"
    monkeypatch.setattr(build_info, "utc_now", lambda: "2026-09-16T00:00:00+00:00")
    second = build_source_review_packet(fidelity_project, "1")
    assert second["packet_id"] == first["packet_id"] and second["packet_sha256"] == first["packet_sha256"]
    assert read_json(packet_path)["generator"]["generated_at"] == "2026-09-15T00:00:00+00:00"
    # A packet written by a build without generator metadata keeps validating.
    legacy = {k: v for k, v in read_json(packet_path).items() if k != "generator"}
    write_json(packet_path, legacy)
    assert _load_source_packet(fidelity_project, first["packet_id"], sha256_file(packet_path))["kind"] == "source-fidelity-review"


# ---------------------------------------------------------------- LT-016: orphaned crop directories


def _asset_directories(root: Path) -> set[str]:
    return {d.name for d in (root / "derived/assets/fidelity").iterdir() if d.is_dir()}


def _referenced_directories(root: Path) -> set[str]:
    from littrans.fidelity import _live_asset_directories
    from littrans.fidelity_models import load_assets

    return _live_asset_directories(load_assets(root).values())


def test_replace_reclaims_directories_the_new_authority_no_longer_references(fidelity_project: Path) -> None:
    from littrans.fidelity import _current_page, prepare_source

    prepare_source(fidelity_project, "1", allow_missing_layout=True)
    orphan = fidelity_project / "derived/assets/fidelity" / ("f" * 64)
    orphan.mkdir()
    (orphan / "original.png").write_bytes(b"stale crop")
    live_before = _referenced_directories(fidelity_project)
    result = prepare_source(fidelity_project, "1", replace=True, allow_missing_layout=True)
    assert result["pruned_asset_directories"] == [orphan.name]
    assert not orphan.exists()
    assert _asset_directories(fidelity_project) == _referenced_directories(fidelity_project) == live_before
    assert _current_page(fidelity_project, 1)["fingerprint"]  # every live crop is still present and intact


def test_source_gc_lists_before_it_deletes(fidelity_project: Path) -> None:
    from typer.testing import CliRunner

    from littrans.cli import app
    from littrans.fidelity import gc_asset_directories, prepare_source

    prepare_source(fidelity_project, "1", allow_missing_layout=True)
    orphan = fidelity_project / "derived/assets/fidelity" / ("e" * 64)
    orphan.mkdir()
    (orphan / "original.svg").write_bytes(b"<svg/>")
    listed = gc_asset_directories(fidelity_project)
    packet_ids = listed.pop("unreferenced_source_packets")
    assert len(packet_ids) == 1 and packet_ids[0].startswith("source-") and listed.pop("live_source_packets") == []
    assert listed == {"mode": "dry-run", "candidates": [orphan.name], "candidate_bytes": 6, "removed": []}
    assert orphan.exists()
    runner = CliRunner()
    assert runner.invoke(app, ["source", "gc", str(fidelity_project)]).exit_code != 0
    assert runner.invoke(app, ["source", "gc", str(fidelity_project), "--dry-run"]).exit_code == 0
    assert orphan.exists()
    applied = gc_asset_directories(fidelity_project, apply=True)
    assert applied["mode"] == "apply" and applied["removed"] == [orphan.name] and not orphan.exists()
    assert _asset_directories(fidelity_project) == _referenced_directories(fidelity_project)


def test_interrupted_replace_keeps_every_directory(fidelity_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans.fidelity import prepare_source

    prepare_source(fidelity_project, "1", allow_missing_layout=True)
    before = _asset_directories(fidelity_project)
    orphan = fidelity_project / "derived/assets/fidelity" / ("d" * 64)
    orphan.mkdir()
    original = fidelity.write_json

    def interrupted(path: Path, payload: Any) -> None:
        if Path(path).name.startswith("p0001"):
            raise KeyboardInterrupt
        original(path, payload)

    monkeypatch.setattr(fidelity, "write_json", interrupted)
    with pytest.raises(KeyboardInterrupt):
        prepare_source(fidelity_project, "1", replace=True, allow_missing_layout=True)
    assert orphan.exists() and before <= _asset_directories(fidelity_project)
