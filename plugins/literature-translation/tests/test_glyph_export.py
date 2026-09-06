import xml.etree.ElementTree as ET
from pathlib import Path

import fitz
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


def test_unknown_vector_structure_fails_instead_of_omitting_it(tmp_path: Path) -> None:
    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((50, 80), "x")
        page.draw_circle((70, 90), 3)
        char = page.get_text("rawdict")["blocks"][0]["lines"][0]["spans"][0]["chars"][0]
        with pytest.raises(ValueError, match="Unsupported"):
            export_owned_fragment(page, [{"id": "x", "text": "x", "bbox": char["bbox"], "origin": char["origin"]}], tmp_path / "x.svg", tmp_path / "x.png", 300)


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
