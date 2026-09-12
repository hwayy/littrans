"""Typography and structure rules from the second extraction review.

Synthetic glyph streams exercise each rule in isolation: text faces, accents,
ligatures, kerns, list grouping, displayed lines with prose, statement labels
and tombstones. None of this is evidence about a particular book.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest

from littrans.fidelity import (
    _compose_accents,
    _display_box_owned,
    _display_line_glyph_ids,
    _make_unit,
    _regions,
    prepare_source,
)
from littrans.fidelity_models import FidelityAsset, FidelityFragment
from littrans.models import ProjectConfig, SourceUnit, UnitKind
from littrans.source_structure import (
    _bold_run_in,
    assemble_structure,
    font_style,
    inked_glyph,
    plan_structure,
    styled_text,
)
from littrans.storage import initialize_project_dirs, read_jsonl, save_project, sha256_file


class _Page:
    def get_text(self, mode: str) -> dict[str, Any]:
        return {"blocks": []}

    def get_image_info(self) -> list[Any]:
        return []

    def get_drawings(self) -> list[Any]:
        return []


def _line(text: str, fonts: str | list[str], line: str = "b0-l0", y: float = 20.0, size: float = 10.0, x: float = 0.0) -> list[dict[str, Any]]:
    if isinstance(fonts, str):
        fonts = [fonts] * len(text)
    return [
        {"id": f"{line}-{i}", "text": c, "font": fonts[i], "bbox": [x + i * 6.0, y, x + i * 6.0 + 5.0, y + 10.0],
         "line": line, "size": size, "baseline": y + 8.0, "origin": [x + i * 6.0, y + 8.0]}
        for i, c in enumerate(text)
    ]


def _owned_text(regions: list[dict[str, Any]], glyphs: list[dict[str, Any]]) -> list[str]:
    by_id = {g["id"]: g["text"] for g in glyphs}
    return ["".join(by_id[gid] for gid in r["glyph_ids"]) for r in regions if r.get("glyph_ids")]


def _asset(aid: str, bbox: tuple[float, float, float, float], display: bool = True) -> FidelityAsset:
    return FidelityAsset(
        id=aid, kind="math", source_sha256="a" * 64, content_sha256="b" * 64, display=display,
        fragments=[FidelityFragment(page=1, bbox=bbox, width=bbox[2] - bbox[0], height=bbox[3] - bbox[1],
                                    png_path="x.png", svg_path="x.svg", pdf_path="x.pdf",
                                    file_sha256={name: "c" * 64 for name in ("x.png", "x.svg", "x.pdf")})],
    )


def test_text_faces_map_to_emphasis_but_math_italic_does_not() -> None:
    assert font_style("CMTI10") == "*" and font_style("CMSL10") == "*"
    assert font_style("CMBX12") == "**" and font_style("CMBXTI10") == "***"
    assert font_style("SFBI1000") == "***" and font_style("Times-Italic") == "*"
    assert font_style("CMMI10") == "" and font_style("CMR10") == "" and font_style("Geneva") == ""


def test_styled_text_joins_runs_across_spaces_and_skips_bare_punctuation() -> None:
    tokens = [("Brownian", "*"), (" ", ""), ("motion", "*"), (" ", ""), ("then", ""), (" ", ""), ("by", "*"), (" ", ""), ("X", ""), (".", "*")]
    assert styled_text(tokens) == "*Brownian motion* then *by* X."


def test_spacing_accents_compose_with_prose_letters_only() -> None:
    def glyph(gid: str, text: str, bbox: list[float], font: str = "CMBX12") -> dict[str, Any]:
        return {"id": gid, "text": text, "bbox": bbox, "font": font, "size": 12.0, "origin": [bbox[0], bbox[3]], "baseline": bbox[3], "line": "b0-l0"}

    # Capital: the accent is raised above the letter box; a kern precedes it.
    glyphs = [glyph("t", "T", [105, 418, 115, 430]), glyph("k", " ", [115, 418, 116.5, 430]), glyph("a", "ˆ", [116.5, 415, 123, 427]),
              glyph("o", "O", [115, 418, 125, 430]), glyph("s", "S", [125, 418, 134, 430])]
    blocks = [{"id": "b0", "bbox": [105, 415, 134, 430], "lines": [[g["id"] for g in glyphs]]}]
    _compose_accents(glyphs, blocks)
    assert "".join(g["text"] for g in glyphs) == "TÔS"
    assert blocks[0]["lines"] == [["t", "o", "s"]]
    # Lowercase: the accent shares the letter's metric box.
    glyphs = [glyph("i", "t", [10, 0, 14, 10], "CMTI10"), glyph("a", "ˆ", [14, 0, 20, 10], "CMTI10"), glyph("o", "o", [14, 0, 20, 10], "CMTI10")]
    blocks = [{"id": "b0", "bbox": [10, 0, 20, 10], "lines": [[g["id"] for g in glyphs]]}]
    _compose_accents(glyphs, blocks)
    assert "".join(g["text"] for g in glyphs) == "tô"
    # A mathematical base letter keeps its separate accent glyph for region ownership.
    glyphs = [glyph("a", "ˆ", [14, -3, 20, 8], "CMR10"), glyph("y", "Y", [13, 0, 21, 10], "CMMI10")]
    blocks = [{"id": "b0", "bbox": [13, -3, 21, 10], "lines": [[g["id"] for g in glyphs]]}]
    _compose_accents(glyphs, blocks)
    assert [g["text"] for g in glyphs] == ["ˆ", "Y"]
    # An accent character set beside a letter, not over it, is left alone.
    glyphs = [glyph("a", "ˆ", [0, 0, 6, 10], "CMR10"), glyph("o", "o", [7, 0, 13, 10], "CMR10")]
    blocks = [{"id": "b0", "bbox": [0, 0, 13, 10], "lines": [[g["id"] for g in glyphs]]}]
    _compose_accents(glyphs, blocks)
    assert [g["text"] for g in glyphs] == ["ˆ", "o"]


def test_ligature_glyphs_expand_to_their_letters() -> None:
    from littrans.fidelity import _native

    class _Raw:
        def get_text(self, mode: str) -> dict[str, Any]:
            chars = [{"c": c, "bbox": [i * 6, 0, i * 6 + 5, 10], "origin": [i * 6, 8]} for i, c in enumerate("diﬀer")]
            return {"blocks": [{"type": 0, "bbox": [0, 0, 40, 10], "lines": [{"spans": [{"font": "CMR10", "size": 10, "chars": chars}]}]}]}

    glyphs, blocks = _native(_Raw())
    assert "".join(g["text"] for g in glyphs) == "differ" and len(glyphs) == 5


def test_large_operators_in_control_characters_are_ink() -> None:
    assert inked_glyph({"text": "\x0b", "font": "CMEX10"})
    assert not inked_glyph({"text": " ", "font": "CMR10"}) and not inked_glyph({"text": "\n", "font": "CMR10"})


def test_tombstone_and_plain_numbers_stay_text() -> None:
    glyphs = _line("x. □", ["CMMI10", "CMR10", "CMR10", "MSAM10"])
    assert _owned_text(_regions(_Page(), glyphs, []), glyphs) == ["x"]
    page = _line("see page 77.", "CMR10")
    box = {"label": "inline_formula", "bbox": [2 * page[9]["bbox"][0] - 1, 2 * 19, 2 * page[10]["bbox"][2] + 1, 2 * 31]}
    assert _owned_text(_regions(_Page(), page, [box]), page) == []


def test_display_box_keeps_formula_words_but_returns_prose_lines() -> None:
    formula = _line("X = 1", ["CMMI10", "CMR10", "CMR10", "CMR10", "CMR10"], line="b0-l0", y=20, x=100)
    tail = [{**g, "id": "t" + g["id"]} for g in _line("for all t", ["CMR10"] * 8 + ["CMMI10"], line="b1-l0", y=20, x=100 + 5 * 6 + 30)]
    below = _line("is a random variable.", "CMR10", line="b2-l0", y=40, x=0)
    prose = {g["id"] for g in tail[:7] if g["text"].isalpha()} | {g["id"] for g in below if g["text"].isalpha()}
    kept, outside, displayed = _display_box_owned(formula + tail + below, prose, margin=0.0)
    assert "".join(g["text"] for g in kept) == "X = 1"
    assert outside and displayed == {g["id"] for g in formula + tail}
    # Words inside the notation ("sup over Y simple") stay in the formula when the
    # box is mostly notation, judged over all of its lines.
    limits = _line("Y simple", ["CMMI10", "CMR10"] + ["CMR10"] * 6, line="b3-l0", y=32, x=100)
    long_formula = _line("X = 1 + 2 + 3 + 4 + 5 + 6", ["CMMI10"] + ["CMR10"] * 24, line="b0-l0", y=20, x=100)
    prose = {g["id"] for g in limits[2:]}
    kept, outside, displayed = _display_box_owned(long_formula + limits, prose, margin=0.0)
    assert len(kept) == len(long_formula) + len(limits) and not outside and not displayed
    ids = _display_line_glyph_ids(long_formula + limits, [{"label": "display_formula", "bbox": [2 * 95, 2 * 15, 2 * 300, 2 * 45]}])
    assert ids == set()


def test_bold_run_in_labels_open_paragraphs_but_wrapped_bold_phrases_do_not() -> None:
    label = _line("EXAMPLE 1. Let", ["CMBX10"] * 10 + ["CMR10"] * 4)
    assert _bold_run_in(label, _line("previous line.", "CMR10"))
    assert not _bold_run_in(label, _line("these modeling", ["CMR10"] * 6 + ["CMBX10"] * 8))
    assert not _bold_run_in(_line("problems:", "CMBX10"), _line("plain", "CMR10"))
    assert not _bold_run_in(_line("X is bold", ["CMBX10"] + ["CMR10"] * 8), _line("plain", "CMR10"))
    numbered = _line("2.1.4. Processes. We", ["CMBX10"] * 17 + ["CMR10"] * 3)
    assert _bold_run_in(numbered, _line("plain", "CMR10"))


def test_plan_splits_at_vertical_gaps_and_run_in_labels() -> None:
    glyphs = _line("First paragraph ends here.", "CMR10", line="b0-l0", y=0)
    glyphs += _line("second line of it.", "CMR10", line="b0-l1", y=12)
    glyphs += _line("EXAMPLE 1. Starts here", ["CMBX10"] * 10 + ["CMR10"] * 12, line="b0-l2", y=24)
    glyphs += _line("and continues.", "CMR10", line="b0-l3", y=36)
    glyphs += _line("After white space.", "CMR10", line="b0-l4", y=66)
    lines = [[g["id"] for g in glyphs if g["line"] == f"b0-l{i}"] for i in range(5)]
    blocks = [{"id": "b0", "bbox": [0, 0, 160, 76], "lines": lines}]
    plan = plan_structure(glyphs, blocks, [], 700)
    assert [len(b["lines"]) for b in plan["blocks"]] == [2, 2, 1]


def test_list_items_join_their_introducing_paragraph_and_display_lines_complete_their_formula() -> None:
    assets = {"a1": _asset("a1", (100, 90, 150, 100)), "t": _asset("t", (200, 92, 210, 99), display=False)}
    units = [
        _make_unit(1, "p0001-b1", "We then have these problems:", [50, 70, 200, 80], assets),
        _make_unit(1, "p0001-b2", "• first item", [70, 82, 200, 88], assets, kind="list_item"),
        _make_unit(1, "p0001-b3", "• second item", [70, 89, 200, 95], assets, kind="list_item"),
        _make_unit(1, "p0001-b4", "A new paragraph.", [50, 100, 200, 110], assets),
        _make_unit(1, "p0001-b5", "{{asset:a1}}", [100, 90, 150, 100], assets),
        _make_unit(1, "p0001-b6", "for all {{asset:t}}.", [160, 91, 220, 100], assets, kind="equation"),
        _make_unit(1, "p0001-b7", "Closing prose.", [50, 120, 200, 130], assets),
    ]
    plan = {"omitted": {}, "notes": {}, "note_top": 999, "margin": 50, "font_size": 10, "display_blocks": ["b6"],
            "first_x": {"b1": 50, "b2": 70, "b3": 70, "b4": 50, "b5": 100, "b6": 160, "b7": 50}}
    result = assemble_structure(units, assets, plan, _make_unit)
    parents = {u.unit_id: u.parent_id for u in result}
    assert parents["p0001-b2"] == parents["p0001-b3"] == "p0001-b1"
    assert parents["p0001-b4"] == "p0001-b4"
    display = next(u for u in result if u.unit_id == "p0001-b5")
    assert display.kind is UnitKind.EQUATION and display.source_text == "{{asset:a1}} for all {{asset:t}}."
    assert display.bbox[2] >= 220 and not any(u.unit_id == "p0001-b6" for u in result)


@pytest.fixture
def list_project(tmp_path: Path) -> Path:
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=300)
        page.insert_text((40, 40), "We then have these problems:")
        page.insert_text((52, 56), "• Define the noise.")
        page.insert_text((52, 70), "• Solve the equation.")
        page.insert_text((40, 90), "This book develops the theory.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="list", title="list", source_path="source/book.pdf",
                                         source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    return tmp_path


def test_prepared_lists_are_one_structure(list_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "ok", "fingerprint": "list", "pages": {}})
    prepare_source(list_project)
    units = [u for u in read_jsonl(list_project / "derived/units.jsonl", SourceUnit) if u.render_policy.value == "include"]
    intro = next(u for u in units if u.source_text.startswith("We then"))
    items = [u for u in units if u.kind is UnitKind.LIST_ITEM]
    after = next(u for u in units if u.source_text.startswith("This book"))
    assert len(items) == 2 and all(u.parent_id == intro.unit_id for u in items)
    assert after.parent_id == after.unit_id
