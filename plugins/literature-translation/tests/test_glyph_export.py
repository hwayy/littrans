import xml.etree.ElementTree as ET
from pathlib import Path

import pymupdf as fitz
import pytest

from littrans.glyph_export import export_owned_fragment


def test_explicit_original_glyphs_exclude_neighbor_ink(tmp_path: Path) -> None:
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 80), "x+y")
        chars = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"]
        glyph = {"id": "plus", "text": chars[1]["c"], "bbox": chars[1]["bbox"], "origin": chars[1]["origin"]}
        result = export_owned_fragment(page, [glyph], tmp_path / "formula.svg", tmp_path / "formula.png", 450)
        assert result["owned_glyphs"] == result["matched_glyphs"] == 1
        assert sum(node.tag.endswith("}use") for node in ET.parse(tmp_path / "formula.svg").getroot()) == 1
        assert fitz.Pixmap(str(tmp_path / "formula.png")).width > 0
        with pytest.raises(ValueError, match="Unmapped"):
            export_owned_fragment(page, [{**glyph, "origin": [0, 0]}], tmp_path / "bad.svg", tmp_path / "bad.png", 300)


@pytest.mark.parametrize("center, intersects", [((53, 76), True), ((70, 90), False)])
def test_unknown_vector_is_rejected_only_when_it_intersects_fragment(tmp_path: Path, center: tuple, intersects: bool) -> None:
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 80), "x")
        page.draw_circle(center, 3)
        char = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"][0]
        glyph = {"id": "x", "text": "x", "bbox": char["bbox"], "origin": char["origin"]}
        if intersects:
            with pytest.raises(ValueError, match="Unsupported"):
                export_owned_fragment(page, [glyph], tmp_path / "x.svg", tmp_path / "x.png", 300)
        else:
            result = export_owned_fragment(page, [glyph], tmp_path / "x.svg", tmp_path / "x.png", 300)
            assert result["matched_glyphs"] == 1
            assert result["retained_paths"] == 0


@pytest.mark.parametrize("symbol", ["^", "f"])
def test_original_ink_survives_inaccurate_reported_font_bbox(tmp_path: Path, symbol: str) -> None:
    """Font metrics may place an accent's box below all of its visible paths."""
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 80), symbol, fontsize=28, fontname="tiit")
        char = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"][0]
        misleading_box = [50, 90, 51, 92]
        glyph = {"id": "selected", "text": char["c"], "bbox": misleading_box, "origin": char["origin"]}
        original_svg = page.get_svg_image(text_as_path=True)
        with fitz.open("svg", original_svg.encode()) as original:
            ink = fitz.Rect(original[0].get_drawings()[0]["rect"])
            assert ink.y1 < misleading_box[1]
            svg_path, png_path = tmp_path / "ink.svg", tmp_path / "ink.png"
            result = export_owned_fragment(page, [glyph], svg_path, png_path, 450, misleading_box)
            preserved = fitz.Rect(result["bbox"])
            assert preserved.contains(ink)
            assert preserved.y0 < misleading_box[1] - 5
            # Independently render the original page's vector paths inside the final box.
            expected = original[0].get_pixmap(clip=preserved, dpi=450, alpha=False)
        actual = fitz.Pixmap(str(png_path))
        def dark_pixels(pixmap: fitz.Pixmap) -> list[tuple[int, int]]:
            pixels = pixmap.samples
            return [(index % pixmap.width, index // pixmap.width)
                    for index in range(pixmap.width * pixmap.height)
                    if min(pixels[index * pixmap.n:index * pixmap.n + 3]) < 180]
        expected_ink, actual_ink = dark_pixels(expected), dark_pixels(actual)
        assert len(expected_ink) > 20
        assert .9 <= len(actual_ink) / len(expected_ink) <= 1.1
        assert min(y for _, y in actual_ink) > 0
        assert max(y for _, y in actual_ink) < actual.height - 1
        with fitz.open("svg", svg_path.read_bytes()) as preserved_svg:
            assert preserved_svg[0].rect.height == pytest.approx(result["height"], abs=.01)
            assert preserved_svg[0].get_drawings()


@pytest.mark.parametrize("offset, intersects", [(0, True), (150, False)])
def test_grouped_drawing_is_measured_before_excluding(tmp_path, offset, intersects):
    from littrans.glyph_export import build_owned_fragment
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 80), "x")
        char = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"][0]
        glyph = {"id": "x", "text": "x", "bbox": char["bbox"], "origin": char["origin"]}
        svg = ET.fromstring(page.get_svg_image(text_as_path=True))
        ns = "{http://www.w3.org/2000/svg}"
        group = ET.SubElement(svg, ns + "g", {"transform": f"matrix(1 0 0 1 {offset} 0)"})
        ET.SubElement(group, ns + "path", {"d": "M50 70H55V80H50Z", "fill": "black"})
        class PageWithGroup:
            rect = page.rect
            def get_svg_image(self, **kwargs):
                return ET.tostring(svg, encoding="unicode")
        if intersects:
            with pytest.raises(ValueError, match="intersecting"):
                build_owned_fragment(PageWithGroup(), [glyph])
        else:
            result, metadata = build_owned_fragment(PageWithGroup(), [glyph])
            assert metadata["matched_glyphs"] == 1
            assert not ET.fromstring(result).findall(ns + "g")


def test_visible_pdf_path_survives_whitespace_unicode_mapping(tmp_path):
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 80), "(", fontsize=24)
        char = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"][0]
        glyph = {"id": "delimiter", "text": " ", "font": "CMEX10", "bbox": char["bbox"], "origin": char["origin"]}
        result = export_owned_fragment(page, [glyph], tmp_path / "delimiter.svg", tmp_path / "delimiter.png", 300)
        assert result["matched_glyphs"] == 1
        with fitz.open("svg", (tmp_path / "delimiter.svg").read_bytes()) as preserved:
            assert preserved[0].get_drawings()


def test_cropbox_clip_group_is_unwrapped_for_ink_and_export(tmp_path: Path) -> None:
    """MuPDF wraps a page in <g clip-path> whenever CropBox differs from MediaBox."""
    from littrans.glyph_export import glyph_ink_boxes

    with fitz.open() as document:
        page = document.new_page(width=500, height=700)
        page.insert_text((50, 80), "(", fontsize=24)
        page.set_cropbox(fitz.Rect(10, 10, 480, 690))
        svg = ET.fromstring(page.get_svg_image(text_as_path=True))
        assert [n.tag.split("}")[-1] for n in svg] == ["defs", "g"]
        char = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"][0]
        glyph = {"id": "paren", "text": char["c"], "bbox": char["bbox"], "origin": char["origin"]}
        ink = glyph_ink_boxes(page, [glyph])
        assert set(ink) == {"paren"}
        misleading_box = [char["bbox"][0], char["bbox"][1], char["bbox"][2], char["bbox"][1] + 3]
        result = export_owned_fragment(page, [{**glyph, "bbox": misleading_box}], tmp_path / "paren.svg", tmp_path / "paren.png", 300, misleading_box)
        assert result["matched_glyphs"] == 1
        assert fitz.Rect(result["bbox"]).contains(fitz.Rect(ink["paren"]))
        assert result["height"] > 10
        with fitz.open("svg", (tmp_path / "paren.svg").read_bytes()) as preserved:
            assert preserved[0].get_drawings()


def test_smaller_clip_group_still_fails_closed() -> None:
    from littrans.glyph_export import build_owned_fragment

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 80), "x")
        char = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"][0]
        glyph = {"id": "x", "text": "x", "bbox": char["bbox"], "origin": char["origin"]}
        svg = ET.fromstring(page.get_svg_image(text_as_path=True))
        ns = "{http://www.w3.org/2000/svg}"
        clip = ET.SubElement(svg.find(ns + "defs"), ns + "clipPath", {"id": "clip_9"})
        ET.SubElement(clip, ns + "path", {"transform": "matrix(1,0,0,1,0,0)", "d": "M40 60H120V100H40Z"})
        group = ET.SubElement(svg, ns + "g", {"clip-path": "url(#clip_9)"})
        ET.SubElement(group, ns + "path", {"d": "M50 70H55V80H50Z", "fill": "black"})

        class PageWithGroup:
            rect = page.rect

            def get_svg_image(self, **kwargs):
                return ET.tostring(svg, encoding="unicode")

        with pytest.raises(ValueError, match="intersecting"):
            build_owned_fragment(PageWithGroup(), [glyph])
