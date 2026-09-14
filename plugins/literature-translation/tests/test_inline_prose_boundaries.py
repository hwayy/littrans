from __future__ import annotations

from littrans.fidelity import _boundary_diagnostics, _prose_boundary_ids, _regions


class EmptyPage:
    def get_image_info(self):
        return []

    def get_drawings(self):
        return []


def glyphs(parts):
    result = []
    for text, font, line in parts:
        for char in text:
            index = len(result)
            result.append({"id": str(index), "text": char, "font": font,
                           "line": line, "bbox": [index * 5, 10, index * 5 + 4, 20],
                           "baseline": 20, "size": 10})
    return result


def owned_text(gs, layout=None):
    regions = _regions(EmptyPage(), gs, layout or [])
    ids = {gid for region in regions for gid in region.get("glyph_ids", [])}
    return "".join(g["text"] for g in gs if g["id"] in ids).strip()


def test_prose_parenthesis_after_math_is_not_cropped_even_by_detector():
    gs = glyphs([("The speedup (as a function of system size ", "SFRM1000", "b3-l0"),
                 ("n", "CMMI10", "b3-l0"), (") is useful.", "SFRM1000", "b3-l0")])
    assert owned_text(gs) == "n"
    n = next(g for g in gs if g["font"] == "CMMI10")
    assert owned_text(gs, [{"label": "inline_formula", "bbox": [n["bbox"][0]*2, 20, (n["bbox"][2]+6)*2, 40]}]) == "n"


def test_superscript_plus_in_citation_does_not_start_math():
    gs = glyphs([("The algorithm [JSW", "SFRM1000", "b1-l0"),
                 ("+", "CMR7", "b1-l0"), ("25] solves problems.", "SFRM1000", "b1-l0")])
    assert owned_text(gs) == ""
    ids = [g["id"] for g in gs if g["text"] in "+25]"]
    diagnostics = _boundary_diagnostics(gs, [{"id": "old-citation", "kind": "math", "fragments": [{"glyph_ids": ids}]}])
    assert diagnostics[0]["code"] == "prose-boundary-in-math"
    assert diagnostics[0]["asset_id"] == "old-citation"


def test_prose_parenthesis_can_span_native_lines():
    gs = glyphs([("(the size is ", "Times-Roman", "b1-l0"),
                 ("n", "CMMI10", "b1-l1"), (")", "Times-Roman", "b1-l1")])
    assert owned_text(gs) == "n"


def test_math_delimiters_and_cross_line_closer_are_preserved():
    gs = glyphs([("f(", "CMR10", "b1-l0"), ("n", "CMMI10", "b1-l0"),
                 (")", "CMR10", "b1-l0")])
    assert owned_text(gs) == "f(n)"
    gs = glyphs([("f(", "CMR10", "b1-l0"), ("n", "CMMI10", "b1-l1"),
                 (")", "CMR10", "b1-l1")])
    assert not _prose_boundary_ids(gs)
    assert owned_text(gs) == "n)"


def test_unmatched_and_math_font_delimiters_are_not_blindly_stripped():
    gs = glyphs([("n", "CMMI10", "b1-l0"), (")", "CMR10", "b1-l0")])
    assert owned_text(gs) == "n)"
    gs = glyphs([("(rank ", "CMR10", "b1-l0"), ("n", "CMMI10", "b1-l0"), (")", "CMSY10", "b1-l0")])
    assert not _prose_boundary_ids(gs)


def test_normal_font_numeric_interval_is_not_a_citation():
    gs = glyphs([("x", "CMMI10", "b1-l0"), (" [0,1]", "Times-Roman", "b1-l0")])
    assert not _prose_boundary_ids(gs)
    assert owned_text(gs) == "x [0,1]"


def test_math_with_roman_subscript_or_textual_argument_is_not_prose():
    gs = glyphs([("The error is ", "SFRM1000", "b1-l0"),
                 ("O", "CMSY10", "b1-l0"), ("(", "CMR10", "b1-l0"),
                 ("x", "CMMI10", "b1-l0"), ("mach", "CMR7", "b1-l0"),
                 (")", "CMR10", "b1-l0")])
    assert not _prose_boundary_ids(gs)
    gs = glyphs([("Cost(classical)", "SFRM1000", "b1-l0")])
    assert not _prose_boundary_ids(gs)


def test_italic_prose_abbreviation_keeps_parentheses_outside_math():
    gs = glyphs([("normal (i.e., ", "SFTI1000", "b1-l0"),
                 ("A", "CMMI10", "b1-l0"), ("=", "CMR10", "b1-l0"),
                 ("A", "CMMI10", "b1-l0"), (")", "SFTI1000", "b1-l0")])
    assert owned_text(gs) == "A=A"
    ids = [g["id"] for g in gs if g["text"] in "()"]
    assert set(ids) <= _prose_boundary_ids(gs)
    assert _boundary_diagnostics(gs, [{"id": "italic-prose", "kind": "math", "fragments": [{"glyph_ids": ids}]}])
