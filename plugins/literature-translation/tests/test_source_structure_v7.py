"""Structural extraction rules introduced after the Claude-host pilot.

Synthetic glyph streams and generated PDFs exercise each rule in isolation; none of
this is evidence about a particular book.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import fitz
import pytest

from littrans.fidelity import (
    _bold_variable_ids,
    _display_line_glyph_ids,
    _regions,
    _strip_display_prose,
    _trim_prose_edges,
    prepare_source,
)
from littrans.models import ProjectConfig, RenderPolicy, SourceUnit, UnitKind
from littrans.source_structure import plan_structure
from littrans.storage import (
    initialize_project_dirs,
    read_json,
    read_jsonl,
    save_project,
    sha256_file,
)


class _Page:
    def get_text(self, mode: str) -> dict[str, Any]:
        return {"blocks": []}

    def get_image_info(self) -> list[Any]:
        return []

    def get_drawings(self) -> list[Any]:
        return []


def _line(text: str, fonts: str | list[str], line: str = "b0-l0", y: float = 20.0, size: float = 10.0) -> list[dict[str, Any]]:
    if isinstance(fonts, str):
        fonts = [fonts] * len(text)
    return [
        {"id": f"{line}-{i}", "text": c, "font": fonts[i], "bbox": [i * 6.0, y, i * 6.0 + 5.0, y + 10.0],
         "line": line, "size": size, "baseline": y + 8.0, "origin": [i * 6.0, y + 8.0]}
        for i, c in enumerate(text)
    ]


def _owned_text(regions: list[dict[str, Any]], glyphs: list[dict[str, Any]]) -> list[str]:
    by_id = {g["id"]: g["text"] for g in glyphs}
    return ["".join(by_id[gid] for gid in r["glyph_ids"]) for r in regions if r.get("glyph_ids")]


def test_bold_single_letters_in_prose_are_variables_but_bold_words_are_not() -> None:
    text = "vector field b and matrix B but modeling problems:"
    fonts = ["CMR10"] * len(text)
    b, capital = text.index(" b ") + 1, text.index(" B ") + 1
    fonts[b] = fonts[capital] = "CMBX10"
    for index in range(text.index("modeling"), len(text)):
        fonts[index] = "CMBX10"
    glyphs = _line(text, fonts)
    ids = _bold_variable_ids({"b0-l0": glyphs})
    assert {glyphs[b]["id"], glyphs[capital]["id"]} == ids
    regions = _regions(_Page(), glyphs, [])
    assert _owned_text(regions, glyphs) == ["b", "B"]


def test_bold_letters_inside_a_display_line_still_count_as_notation() -> None:
    text = "b(X(s)) ds"
    fonts = ["CMBX10", "CMR10", "CMBX10", "CMR10", "CMMI10", "CMR10", "CMR10", "CMR10", "CMMI10", "CMMI10"]
    glyphs = _line(text, fonts)
    assert _owned_text(_regions(_Page(), glyphs, []), glyphs) == ["b(X(s)) ds"]


def test_quotes_hyphens_and_math_font_periods_are_trimmed_from_formula_edges() -> None:
    text = "the term “dt” and m-dimensional noise t > 0."
    fonts = ["CMR10"] * len(text)
    for fragment in ("dt", "m-", "t >", "0."):
        start = text.index(fragment)
        fonts[start] = "CMMI10"
        if fragment == "dt":
            fonts[start + 1] = "CMMI10"
        if fragment == "t >":
            fonts[start + 2] = "CMMI10"
        if fragment == "0.":
            fonts[start + 1] = "CMMI10"  # the period set in the math font
    glyphs = _line(text, fonts)
    assert _owned_text(_regions(_Page(), glyphs, []), glyphs) == ["dt", "m", "t > 0"]


def test_detector_inline_box_does_not_keep_quotation_marks() -> None:
    text = "“dX”"
    glyphs = _line(text, ["CMR10", "CMMI10", "CMBX10", "CMR10"])
    boxes = [{"label": "inline_formula", "bbox": [0, 30, 2 * 25, 2 * 32]}]
    regions = _regions(_Page(), glyphs, boxes)
    assert _owned_text(regions, glyphs) == ["dX"]
    assert regions[0]["bbox"][0] >= glyphs[1]["bbox"][0]


def test_list_bullet_in_symbol_font_is_not_a_formula() -> None:
    text = "• Define x."
    fonts = ["CMSY10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMMI10", "CMR10"]
    glyphs = _line(text, fonts)
    assert _owned_text(_regions(_Page(), glyphs, []), glyphs) == ["x"]


def test_trim_keeps_delimiters_and_run_interior() -> None:
    glyphs = _line("(a, b)", ["CMR10", "CMMI10", "CMR10", "CMR10", "CMMI10", "CMR10"])
    kept = _trim_prose_edges(list(glyphs), glyphs)
    assert "".join(g["text"] for g in kept) == "(a, b)"


def test_display_prose_tail_is_stripped_only_across_a_gap() -> None:
    formula = _line("X(t) = x", ["CMBX10", "CMR10", "CMMI10", "CMR10", "CMR10", "CMR10", "CMR10", "CMMI10"])
    phrase_x = formula[-1]["bbox"][2] + 30
    phrase = [{**g, "id": "p" + g["id"], "bbox": [g["bbox"][0] + phrase_x, g["bbox"][1], g["bbox"][2] + phrase_x, g["bbox"][3]]}
              for g in _line("for all t", ["CMR10"] * 8 + ["CMMI10"])]
    prose_ids = {g["id"] for g in phrase[:7] if g["text"].isalpha()}
    kept = _strip_display_prose(formula + phrase, prose_ids)
    assert "".join(g["text"] for g in kept) == "X(t) = x"
    touching = _line("m-dimensional", ["CMMI10"] + ["CMR10"] * 12)
    prose_ids = {g["id"] for g in touching[2:]}
    assert _strip_display_prose(touching, prose_ids) == touching


def test_display_lines_with_embedded_prose_split_the_paragraph() -> None:
    glyphs = _line("where", "CMR10", line="b0-l0", y=0)
    line = _line("B : R (= space of matrices)", ["CMBX10", "CMR10", "CMR10", "CMR10", "MSBM10"] + ["CMR10"] * 22, line="b0-l1", y=14)
    glyphs += line
    glyphs += _line("and so on", "CMR10", line="b0-l2", y=28)
    layout = [{"label": "display_formula", "bbox": [0, 2 * 13, 2 * 170, 2 * 25]}]
    display_ids = _display_line_glyph_ids(glyphs, layout)
    assert display_ids == {g["id"] for g in line if g["text"].strip()}
    blocks = [{"id": "b0", "bbox": [0, 0, 170, 38], "lines": [[g["id"] for g in glyphs if g["line"] == f"b0-l{i}"] for i in range(3)]}]
    plan = plan_structure(glyphs, blocks, layout, 100, display_glyph_ids=display_ids)
    assert [b["id"] for b in plan["blocks"]] == ["b0", "b0-s2", "b0-s3"]
    assert plan["display_blocks"] == ["b0-s2"]


def test_wrapped_headings_and_page_numbers_are_planned_structurally() -> None:
    glyphs = _line("1.1. LONG HEADING", "CMBX12", line="b0-l0", y=0, size=12)
    glyphs += [{**g, "bbox": [g["bbox"][0] + 20, g["bbox"][1], g["bbox"][2] + 20, g["bbox"][3]], "origin": [g["origin"][0] + 20, g["origin"][1]]}
               for g in _line("CONTINUED", "CMBX12", line="b0-l1", y=14, size=12)]
    body = _line("Body prose starts here.", "CMR10", line="b1-l0", y=40)
    body += [{**g, "bbox": [g["bbox"][0] + 400, 700, g["bbox"][2] + 400, 710], "origin": [g["origin"][0] + 400, 708.0]}
             for g in _line("7", "CMR10", line="b1-l1", y=700)]
    glyphs += body
    blocks = [
        {"id": "b0", "bbox": [0, 0, 120, 24], "lines": [[g["id"] for g in glyphs if g["line"] == "b0-l0"], [g["id"] for g in glyphs if g["line"] == "b0-l1"]]},
        {"id": "b1", "bbox": [0, 40, 410, 710], "lines": [[g["id"] for g in glyphs if g["line"] == "b1-l0"], [g["id"] for g in glyphs if g["line"] == "b1-l1"]]},
    ]
    layout = [
        {"label": "paragraph_title", "bbox": [0, 0, 2 * 125, 2 * 25]},
        {"label": "number", "bbox": [2 * 398, 2 * 699, 2 * 408, 2 * 712]},
    ]
    plan = plan_structure(glyphs, blocks, layout, 720)
    ids = [b["id"] for b in plan["blocks"]]
    assert ids == ["b0", "b1", "b1-pagenum"]  # the heading is not split at its wrapped line
    assert plan["omitted"]["b1-pagenum"] == "page-number"
    assert len(next(b for b in plan["blocks"] if b["id"] == "b1")["lines"]) == 1


@pytest.fixture
def figure_project(tmp_path: Path) -> Path:
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page(width=300, height=400)
        page.insert_text((40, 40), "A heading line")
        page.insert_text((40, 70), "Prose before the figure.")
        page.draw_line((60, 100), (240, 160), width=1.5)
        page.draw_line((60, 160), (240, 100), width=1.5)
        page.insert_text((80, 185), "Caption of the drawing")
        page.insert_text((40, 220), "Prose after the figure.")
        page.draw_line((40, 380), (260, 380), width=0.8)  # decorative rule
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="fig", title="fig", source_path="source/book.pdf",
                                         source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    return tmp_path


def test_figures_own_their_captions_and_rules_are_omitted(figure_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity

    def fake_detect(images: list[Path], output: Path) -> dict[str, Any]:
        page = str(images[0].resolve())
        return {"status": "ok", "fingerprint": "figure-test", "pages": {page: [
            {"label": "paragraph_title", "bbox": [70, 55, 300, 90], "score": 0.9},
            {"label": "image", "bbox": [110, 190, 490, 330], "score": 0.9},
            {"label": "figure_title", "bbox": [150, 350, 420, 385], "score": 0.9},
        ]}}

    monkeypatch.setattr(fidelity, "detect_layout", fake_detect)
    prepare_source(figure_project)
    units = read_jsonl(figure_project / "derived/units.jsonl", SourceUnit)
    by_kind = {u.kind: u for u in units}
    figure = next(u for u in units if u.kind is UnitKind.FIGURE)
    caption = next(u for u in units if u.kind is UnitKind.CAPTION)
    assert caption.parent_id == figure.unit_id
    assert "Caption of the drawing" in caption.source_text
    after = next(u for u in units if "after the figure" in u.source_text)
    assert after.parent_id == after.unit_id  # prose after the caption starts its own group
    heading = by_kind[UnitKind.HEADING]
    before = next(u for u in units if "before the figure" in u.source_text)
    assert before.parent_id != heading.unit_id  # a heading never owns the following prose
    rules = [u for u in units if u.kind is UnitKind.NOTE and u.render_policy is RenderPolicy.OMIT and "{{asset:" in u.source_text]
    assert rules, "the bare horizontal rule is kept in the ledger but omitted from reading"
    ledger = read_json(figure_project / "derived/fidelity-pages/p0001.json")
    assert ledger["layout_fingerprint"] == "figure-test"


def test_source_render_writes_a_checkpoint(figure_project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity
    from littrans.source_render import render_source_review

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "ok", "fingerprint": "x", "pages": {}})
    prepare_source(figure_project)
    result = render_source_review(figure_project, "1")
    page = Path(result["html"]).read_text(encoding="utf-8")
    assert result["source_verified"] is False and result["attention"] >= 1
    assert "source checkpoint" in page and "Prose before the figure." in page
    assert 'class="unit kind-' in page and "omitted running-material" not in page or "omitted" in page
    assert (figure_project / "output" / "original-assets").is_dir()
