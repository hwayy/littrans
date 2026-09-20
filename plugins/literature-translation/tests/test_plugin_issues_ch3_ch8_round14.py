"""Round 14: the defects the chapter 3-8 extraction ledger reported (LT-049 … LT-074).

Each test states the rule that was missing. The ledger's pages were replayed on a scratch
copy of the project to settle the rules; the tests here are synthetic glyph lines, pages
and tiny PDFs only. They cover: the scaffold's ignore pair (049); boundary diagnostics on
padded crops and declared conditions (050, 054); degenerate vector paths and rule
retention in the precise export (051, 070); figure unit boxes (052); line-end hyphens
(053, 071); the printed start of a line after a tall operator (055); page-edge
continuation (056, 057); printed equation labels as never-notation and their binding
(058, 059, 069); ink-gap word spaces at asset boundaries (060, 066); dangling and
sibling parents (061, 062); the merge loop on a page full of drawings (063); words between
two formulas of a display (064); hash parity of the override channel (065); trailing
punctuation and hyphens at a native line end (067); coalesced assets in the override
channel (068); math accents (073); bold citation keys (074).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
from test_fidelity_source import project as fidelity_project
from test_source_structure_v8 import _line, _owned_text, _Page

import littrans.fidelity as fidelity
from littrans.fidelity import (
    _accent_glyph_ids,
    _bold_variable_ids,
    _boundary_diagnostics,
    _boundary_space,
    _cluster_drawings,
    _make_unit,
    _regions,
    _rejoin_line_breaks,
    _separate_display_units,
    _split_display_at_tags,
    _strip_display_prose,
    _tag_glyph_ids,
    _trim_prose_edges,
    build_source_review_packet,
    import_source_review,
    prepare_source,
)
from littrans.fidelity_models import FidelityAsset, FidelityFragment, load_assets
from littrans.glyph_export import _degenerate_path, build_owned_fragment
from littrans.models import SourceUnit, UnitKind
from littrans.rendering import _continues_paragraph
from littrans.scaffold import ensure_project_ignore
from littrans.source_structure import (
    MATH_OPERATORS,
    _line_starts,
    assemble_structure,
    plan_structure,
)
from littrans.storage import read_json, read_jsonl, write_json

project = fidelity_project


def _fonts(text: str, math: str) -> list[str]:
    return ["CMMI10" if c in math else "CMR10" for c in text]


def _plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {"omitted": {}, "notes": {}, "note_top": 1000.0, "first_x": {}, "margin": 58.7,
                            "font_size": 10.0, "indent_style": True, "display_blocks": [], "blocks": []}
    plan.update(overrides)
    return plan


def _unit(uid: str, text: str, bbox: list[float], page: int = 2, kind: str = "paragraph") -> SourceUnit:
    return _make_unit(page, uid, text, bbox, {}, kind=kind)


# --- LT-049 -------------------------------------------------------------------------------

def test_scaffold_recognises_the_slashed_spelling_of_its_ignore_pair(tmp_path: Path) -> None:
    ignore = tmp_path / ".gitignore"
    ignore.write_text("/.littrans/*\n!/.littrans/work/\noutput/\n", encoding="utf-8")
    assert ensure_project_ignore(tmp_path) is False
    assert ignore.read_text(encoding="utf-8") == "/.littrans/*\n!/.littrans/work/\noutput/\n"
    ignore.write_text("output/\n", encoding="utf-8")
    assert ensure_project_ignore(tmp_path) is True
    assert ensure_project_ignore(tmp_path) is False


# --- LT-050 / LT-054 ---------------------------------------------------------------------

def _asset_dict(aid: str, ids: list[str], bbox: list[float], **extra: Any) -> dict[str, Any]:
    return {"id": aid, "kind": "math", "display": True, "fragments": [{"bbox": bbox, "glyph_ids": ids}], **extra}


def test_a_descender_of_the_line_above_is_not_a_hole_in_the_display_below() -> None:
    above = _line("xn", ["CMMI10", "CMMI8"], line="b1-l0", y=100.0, x=100.0)
    above[1]["bbox"] = [106.0, 105.0, 111.0, 118.4]  # the subscript's box reaches into the padding below
    above[1]["baseline"] = 111.0
    below = _line("y=1", _fonts("y=1", "y"), line="b2-l0", y=118.0, x=100.0)
    glyphs = above + below
    assets = [_asset_dict("A", [g["id"] for g in above], [99.5, 99.5, 112.0, 110.5]),
              _asset_dict("B", [g["id"] for g in below], [99.5, 117.5, 118.5, 128.5])]
    assert [d["code"] for d in _boundary_diagnostics(glyphs, assets)] == []
    # A glyph on the display's own row owned by another asset is a hole.
    stray = _line("z", "CMMI10", line="b2-l1", y=118.0, x=112.0)
    assets[1]["fragments"][0]["bbox"] = [99.5, 117.5, 118.5, 128.5]
    assets.append(_asset_dict("C", [stray[0]["id"]], [111.5, 117.5, 117.5, 128.5]))
    codes = [d["code"] for d in _boundary_diagnostics(glyphs + stray, assets) if d["asset_id"] == "B"]
    assert codes == ["math-ink-outside-ownership"]


def test_a_bracketed_operator_word_the_asset_declares_is_no_prose_boundary() -> None:
    assert "mod" in MATH_OPERATORS
    text = "x (mod m)"
    glyphs = _line(text, ["CMMI10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMMI10", "CMR10"], line="b1-l0")
    ids = [g["id"] for g in glyphs]
    asset = _asset_dict("A", ids, [-0.5, 19.5, 60.0, 30.5])
    assert _boundary_diagnostics(glyphs, [asset]) == []
    # A bracketed language word ("if") the asset declares as a condition is accounted for.
    text = "x (if y)"
    glyphs = _line(text, ["CMMI10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMMI10", "CMR10"], line="b1-l0")
    ids = [g["id"] for g in glyphs]
    asset = _asset_dict("A", ids, [-0.5, 19.5, 60.0, 30.5])
    assert [d["code"] for d in _boundary_diagnostics(glyphs, [asset])] == ["prose-boundary-in-math"]
    asset["formula_conditions"] = [{"glyph_ids": [glyphs[3]["id"], glyphs[4]["id"]], "source_text": "if"}]
    assert _boundary_diagnostics(glyphs, [asset]) == []


# --- LT-051 / LT-070 ---------------------------------------------------------------------

def test_degenerate_paths_print_nothing() -> None:
    svg = "{http://www.w3.org/2000/svg}"
    assert _degenerate_path(ET.Element(f"{svg}path", {"d": ""}))
    assert _degenerate_path(ET.Element(f"{svg}path", {"d": "M280.53 492.3"}))
    assert _degenerate_path(ET.Element(f"{svg}path", {"d": "M1 2 m3 4"}))
    assert not _degenerate_path(ET.Element(f"{svg}path", {"d": "M0 0H10"}))
    assert not _degenerate_path(ET.Element(f"{svg}path", {"d": "M1 1L2 2Z"}))


def test_a_rule_inside_the_box_is_kept_whatever_its_distance_from_the_glyph_boxes(tmp_path: Path) -> None:
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((100, 100), "table")
        # A double rule under the word: the lower one is well beyond 2pt of the glyph box.
        page.draw_rect(fitz.Rect(99, 102.8, 124, 103.2), fill=(0, 0, 0), width=0)
        page.insert_text((300, 300), "x")  # keeps the two rules separate paths in the SVG
        page.draw_rect(fitz.Rect(99, 105.8, 124, 106.2), fill=(0, 0, 0), width=0)
        glyphs = [g for g in fidelity._native(page)[0] if g["bbox"][0] < 200]
        box = [min(g["bbox"][0] for g in glyphs) - 1, min(g["bbox"][1] for g in glyphs) - 1,
               max(g["bbox"][2] for g in glyphs) + 1, 108.0]
        _, geometry = build_owned_fragment(page, glyphs, box)
    assert geometry["retained_paths"] == 2


# --- LT-052 ------------------------------------------------------------------------------

def test_a_figure_unit_from_a_native_label_block_takes_the_figure_box(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans.models import ProjectConfig
    from littrans.storage import initialize_project_dirs, save_project, sha256_file

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), False)
    pixmap.clear_with(200)
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 60), "Prose above the figure.")
        page.insert_image(fitz.Rect(100, 100, 300, 250), pixmap=pixmap)
        page.insert_text((150, 240), "1")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="test", title="test", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    prepare_source(tmp_path, "1", allow_missing_layout=True)
    units = read_jsonl(tmp_path / "derived/units.jsonl", SourceUnit)
    figure = next(u for u in units if u.kind is UnitKind.FIGURE)
    assets = load_assets(tmp_path)
    box = assets[next(iter(figure.asset_content_hashes))].fragments[0].bbox
    assert figure.bbox == box
    assert figure.bbox[2] - figure.bbox[0] > 100


# --- LT-053 / LT-071 ---------------------------------------------------------------------

def test_line_end_hyphens_read_the_document_and_the_shape_of_the_halves() -> None:
    evidence = (Counter(), Counter({"finite": 3, "state": 5, "condition": 2, "conditioning": 1, "planck": 1}))
    # Both halves are words the document prints on their own: a compound.
    assert _rejoin_line_breaks("finite-\nstate chains", evidence) == "finite-state chains"
    # A half that is no word: a soft break.
    assert _rejoin_line_breaks("condi-\ntioning", evidence) == "conditioning"
    assert _rejoin_line_breaks("Lorent-\nzian", evidence) == "Lorentzian"
    # A capitalised second half is a name compound at a block or line seam.
    assert _rejoin_line_breaks("Fokker-\nPlanck equation", evidence) == "Fokker-Planck equation"
    # A conjunction after the hyphen is a suspended hyphen.
    assert _rejoin_line_breaks("left-\nand right-continuous", evidence) == "left- and right-continuous"
    # Notation before the hyphen makes a compound with the word after it.
    assert _rejoin_line_breaks("{{asset:a-p0001-abc}}-\nalgebra", evidence) == "{{asset:a-p0001-abc}}-algebra"
    # The document's own count still decides where it has an opinion.
    printed = (Counter({"well-known": 2}), Counter({"wellknown": 0, "well": 9, "known": 9}))
    assert _rejoin_line_breaks("well-\nknown", printed) == "well-known"
    joined = (Counter(), Counter({"wellknown": 1, "well": 9, "known": 9}))
    assert _rejoin_line_breaks("well-\nknown", joined) == "wellknown"


# --- LT-055 ------------------------------------------------------------------------------

def test_a_line_after_a_tall_operator_at_the_margin_is_not_an_indented_paragraph() -> None:
    prose = _line("we get two quantities:", "CMR10", line="b1-l0", y=100.0, x=58.7)
    operator = {"id": "b2-l0-0", "text": "\x0b", "font": "CMEX10", "bbox": [58.7, 112.0, 68.0, 122.0], "line": "b2-l0",
                "size": 10.0, "baseline": 115.0, "origin": [58.7, 115.0]}
    rest = _line("expected number of transitions", "CMR10", line="b3-l0", y=113.0, x=76.0)
    glyphs = prose + [operator] + rest
    blocks = [{"id": "b1", "bbox": [58.7, 100, 200, 110], "lines": [[g["id"] for g in prose]]},
              {"id": "b2", "bbox": [58.7, 112, 68, 122], "lines": [[operator["id"]]]},
              {"id": "b3", "bbox": [76, 113, 260, 123], "lines": [[g["id"] for g in rest]]}]
    ink = {operator["id"]: [58.7, 108.0, 68.0, 128.0]}
    starts = _line_starts(glyphs, blocks, 58.7, 10.0, ink)
    assert starts[rest[0]["id"]] == 58.7
    plan = plan_structure(glyphs, blocks, [], 800.0, ink=ink)
    assert plan["first_x"]["b3"] == 58.7
    # An indent that is really an indent keeps its x.
    indented = _line("A new paragraph starts here", "CMR10", line="b4-l0", y=126.0, x=76.0)
    blocks.append({"id": "b4", "bbox": [76, 126, 260, 136], "lines": [[g["id"] for g in indented]]})
    plan = plan_structure(glyphs + indented, blocks, [], 800.0, ink=ink)
    assert plan["first_x"]["b4"] == 76.0


def test_a_radical_split_row_keeps_the_list_items_of_the_lines_around_it() -> None:
    """MuPDF puts a radical's origin at its top, a text baseline away from its row.

    The row is decided by ink: the ``√`` and the ``2).`` after it belong to the ``(a)``
    row, not to the sentence above it, and the ``(b)`` item that follows still opens.
    """
    intro = _line("Let the function be f.", "CMR10", line="b1-l0", y=100.0, x=58.7)
    item_a = _line("(a) Prove that f(x) = f(x/", "CMR10", line="b2-l0", y=114.0, x=62.0)
    radical = {"id": "b3-l0-0", "text": "√", "font": "CMSY10", "bbox": [218.0, 105.0, 227.0, 116.0], "line": "b3-l0",
               "size": 10.0, "baseline": 105.5, "origin": [218.0, 105.5]}
    tail = _line("2).", "CMR10", line="b4-l0", y=114.0, x=227.0)
    item_b = _line("(b) Prove that f is the characteristic function of a", "CMR10", line="b4-l1", y=128.0, x=62.0)
    wrap = _line("random variable if f is smooth.", "CMR10", line="b5-l0", y=142.0, x=86.0)
    glyphs = intro + item_a + [radical] + tail + item_b + wrap
    blocks = [
        {"id": "b1", "bbox": [58.7, 100, 200, 110], "lines": [[g["id"] for g in intro]]},
        {"id": "b2", "bbox": [62, 114, 218, 124], "lines": [[g["id"] for g in item_a]]},
        {"id": "b3", "bbox": [218, 105, 227, 116], "lines": [[radical["id"]]]},
        {"id": "b4", "bbox": [62, 114, 400, 138], "lines": [[g["id"] for g in tail], [g["id"] for g in item_b]]},
        {"id": "b5", "bbox": [86, 142, 300, 152], "lines": [[g["id"] for g in wrap]]},
    ]
    ink = {radical["id"]: [218.5, 113.0, 226.8, 125.0]}
    starts = _line_starts(glyphs, blocks, 58.7, 10.0, ink)
    # The radical's row starts at the (a) label, not at the sentence above it.
    assert starts[radical["id"]] == 62.0 and starts[tail[0]["id"]] == 62.0
    plan = plan_structure(glyphs, blocks, [], 800.0, ink=ink)
    assert plan["list_items"]["b2"] == {"label": "(a)", "body_x": 86.0}
    assert plan["list_items"]["b4-s2"] == {"label": "(b)", "body_x": 86.0}
    assert plan["list_items"]["b5"] == {"continues": "b4-s2"}
    # A formula suffix far to the right keeps its own x; the row's start is not an indent.
    assert plan["first_x"]["b4"] == 227.0


def test_a_mid_row_prose_line_continues_the_item_whose_column_its_row_starts_at() -> None:
    label = _line("(a) Consider a random walk on a lattice", "CMR10", line="b1-l0", y=100.0, x=62.0)
    body = _line("with states 1 to N and", "CMR10", line="b2-l0", y=114.0, x=86.0)
    operator = {"id": "b2-l0-99", "text": "", "font": "CMEX10", "bbox": [226.0, 106.0, 238.0, 117.0], "line": "b2-l0",
                "size": 10.0, "baseline": 114.0, "origin": [226.0, 114.0]}
    limits = {"id": "b3-l0-0", "text": "|", "font": "CMSY8", "bbox": [238.0, 118.0, 240.0, 126.0], "line": "b3-l0",
              "size": 8.0, "baseline": 125.0, "origin": [238.0, 125.0]}
    rest = _line(" p = 1, where", "CMR10", line="b3-l0", y=114.0, x=250.0)
    for g in rest:
        g["id"] = g["id"].replace("b3-l0-", "b3-l0-1")
    wrap = _line("i and j are neighbours.", "CMR10", line="b3-l1", y=128.0, x=86.0)
    glyphs = label + body + [operator, limits] + rest + wrap
    blocks = [
        {"id": "b1", "bbox": [62, 100, 300, 110], "lines": [[g["id"] for g in label]]},
        {"id": "b2", "bbox": [86, 106, 238, 124], "lines": [[g["id"] for g in body] + [operator["id"]]]},
        {"id": "b3", "bbox": [238, 114, 400, 138], "lines": [[limits["id"], *(g["id"] for g in rest)], [g["id"] for g in wrap]]},
    ]
    ink = {operator["id"]: [226.5, 112.0, 237.5, 124.0], limits["id"]: [238.5, 119.0, 239.5, 127.0]}
    plan = plan_structure(glyphs, blocks, [], 800.0, ink=ink)
    assert plan["list_items"]["b2"] == {"continues": "b1"}
    assert plan["list_items"]["b3"] == {"continues": "b1"}


# --- LT-056 / LT-057 ---------------------------------------------------------------------

def test_a_flush_run_in_label_at_the_page_top_does_not_continue_the_previous_page() -> None:
    plan = _plan(first_x={"b1": 58.7, "b2": 58.7})
    label = _unit("p0002-b1", "**Example 2.3** (Cauchy distribution). Let the density be", [58.7, 90, 400, 100])
    assert not assemble_structure([label], {}, plan, _make_unit)[0].continues_from_previous
    plain = _unit("p0002-b2", "of the chain, which we now prove.", [58.7, 90, 400, 100])
    assert assemble_structure([plain], {}, plan, _make_unit)[0].continues_from_previous
    theorem = _unit("p0002-b1", "Theorem 5.2. Let the density be", [58.7, 90, 400, 100])
    assert not assemble_structure([theorem], {}, plan, _make_unit)[0].continues_from_previous


def test_rendering_reads_both_page_edge_flags() -> None:
    sender = _unit("p0001-b9", "the spectral gap of the", [58.7, 600, 400, 610], page=1)
    receiver = _unit("p0002-b1", "network corresponds to", [58.7, 90, 400, 100])
    # The sender's flag alone carries the sentence across the page (LT-057).
    assert _continues_paragraph(sender.model_copy(update={"continued_to_next": True}), receiver)
    assert not _continues_paragraph(sender, receiver)
    # A receiver flagged on geometry alone does not join a sentence the sender closed (LT-056).
    closed = _unit("p0001-b9", "the spectral gap of the chain.", [58.7, 600, 400, 610], page=1)
    flagged = receiver.model_copy(update={"continues_from_previous": True})
    assert not _continues_paragraph(closed, flagged)
    assert _continues_paragraph(sender, flagged)
    assert not _continues_paragraph(sender.model_copy(update={"continued_to_next": True}), receiver.model_copy(update={"kind": UnitKind.HEADING}))


# --- LT-058 / LT-069(b) ------------------------------------------------------------------

def test_a_printed_equation_label_never_continues_the_formula_above_it() -> None:
    prose = _line("in the limit h → 0+", ["CMR10"] * 13 + ["CMMI10", "CMR10", "CMSY10", "CMR10", "CMR10", "CMR10"], line="b1-l0", y=90.0, x=100.0)
    tag = _line("(3.11)", "CMR10", line="b2-l0", y=110.0, x=58.7)
    glyphs = prose + tag
    assert _tag_glyph_ids(glyphs) == {g["id"] for g in tag}
    regions = _regions(_Page(), glyphs, [])
    texts = _owned_text(regions, glyphs)
    assert "h → 0+" in texts
    assert not any("3.11" in text or "(" in text for text in texts)
    # A cross-reference in running prose is not a label line either way: no region.
    reference = _line("Equation (3.11) is a statement", "CMR10", line="b3-l0", y=130.0, x=58.7)
    assert _owned_text(_regions(_Page(), reference, []), reference) == []


# --- LT-059 ------------------------------------------------------------------------------

def test_a_detector_box_over_two_labelled_displays_is_cut_between_them() -> None:
    top = [{"id": f"t{i}", "text": c, "font": "CMMI10", "bbox": [160.0 + 6 * i, 370.0, 165.0 + 6 * i, 400.0], "line": "b1-l0",
            "size": 10.0, "baseline": 388.0, "origin": [160.0 + 6 * i, 388.0]} for i, c in enumerate("qij=")]
    bottom = [{"id": f"u{i}", "text": c, "font": "CMMI10", "bbox": [160.0 + 6 * i, 406.0, 165.0 + 6 * i, 438.0], "line": "b2-l0",
               "size": 10.0, "baseline": 425.0, "origin": [160.0 + 6 * i, 425.0]} for i, c in enumerate("qii=")]
    parts, cuts = _split_display_at_tags({"bbox": [151.5, 368.0, 321.5, 439.0]}, top + bottom, [388.1, 425.2])
    assert [len(part) for part in parts] == [4, 4] and 400.0 < cuts[0] < 406.0
    # Ink spanning both labels (one tall matrix) is not cut.
    tall = [{**g, "bbox": [g["bbox"][0], 370.0, g["bbox"][2], 438.0]} for g in top]
    assert _split_display_at_tags({"bbox": [151.5, 368.0, 321.5, 439.0]}, tall, [388.1, 425.2]) == ([tall], [])


# --- LT-060 / LT-066 ---------------------------------------------------------------------

def test_the_printed_ink_gap_decides_the_space_at_an_asset_boundary() -> None:
    left = {"id": "a", "text": "k", "size": 10.9, "baseline": 100.0}
    ink = {"a": [100.0, 92.0, 106.0, 100.0], "b": [109.5, 92.0, 114.0, 100.0], "c": [106.03, 98.0, 107.0, 100.0], "d": [108.9, 96.0, 112.0, 102.0]}
    word = {"id": "b", "text": "i", "size": 10.9, "baseline": 100.0}
    assert _boundary_space(left, word, ink) is True          # 3.5pt: a printed space (LT-060)
    period = {"id": "c", "text": ".", "size": 10.9, "baseline": 100.0}
    assert _boundary_space(left, period, ink) is False       # a kern before the period (LT-066)
    subscript = {"id": "d", "text": "h", "size": 8.0, "baseline": 103.0}
    assert _boundary_space(left, subscript, ink) is None     # another size and baseline: not comparable
    assert _boundary_space(left, {"id": "x", "text": "i", "size": 10.9, "baseline": 100.0}, ink) is None
    assert _boundary_space({"id": "b", "text": "(", "size": 10.9, "baseline": 100.0}, left, ink) is False


# --- LT-061 / LT-062 ---------------------------------------------------------------------

def test_an_enumerated_clause_after_white_space_stays_in_its_introducing_group() -> None:
    intro = _unit("p0002-b2", "Sampling can be used to:", [58.7, 100, 400, 110])
    first = _unit("p0002-b3", "(i) Compute expectations", [84.5, 120, 200, 130])
    second = _unit("p0002-b12", "(ii) Find the maximum of a function.", [81.5, 150, 400, 160])
    plan = _plan(margin=101.0, indent_style=False, first_x={"b2": 58.7, "b3": 84.5, "b12": 81.5},
                 blocks=[{"id": "b3", "paragraph_break": True}, {"id": "b12", "paragraph_break": True}])
    result = assemble_structure([intro, first, second], {}, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0002-b2", "p0002-b2", "p0002-b2"]


def test_a_parent_merged_away_is_redirected_to_the_survivor_group() -> None:
    head = _unit("p0002-b1", "The iteration formulas are intuitive. If we understand", [58.7, 100, 400, 110])
    # The rest of the printed line is a block of its own that the planner flagged as opening
    # after white space: it opens a group, then merges into the line it continues.
    same_line = _unit("p0002-b2", "as the number of transitions", [200.0, 100, 400, 110])
    child = _unit("p0002-b3", "is an empirical estimation", [100.0, 120, 300, 130], kind="equation")
    plan = _plan(first_x={"b1": 58.7, "b2": 200.0, "b3": 100.0}, indent_style=False, display_blocks=["b3"],
                 blocks=[{"id": "b2", "paragraph_break": True}])
    result = assemble_structure([head, same_line, child], {}, plan, _make_unit)
    ids = {u.unit_id for u in result}
    assert "p0002-b2" not in ids
    assert [u.parent_id for u in result] == ["p0002-b1", "p0002-b1"]


# --- LT-063 ------------------------------------------------------------------------------

class _DrawingPage(_Page):
    def __init__(self, rects: list[list[float]]) -> None:
        self.rects = rects

    def get_drawings(self) -> list[Any]:
        return [{"rect": fitz.Rect(*r)} for r in self.rects]


def test_hundreds_of_overlapping_drawings_merge_as_the_pairwise_loop_would(monkeypatch: pytest.MonkeyPatch) -> None:
    rects = [[100.0 + i * 0.7, 300.0 + (i % 7) * 3.0, 104.0 + i * 0.7, 303.0 + (i % 7) * 3.0] for i in range(400)]
    rects += [[400.0, 500.0, 420.0, 520.0]]  # a lone diagram far away
    glyphs = _line("f(x) = 1", _fonts("f(x) = 1", "fx"), line="b1-l0", y=280.0, x=100.0)
    page = _DrawingPage(rects)
    clustered = _regions(page, glyphs, [])
    monkeypatch.setattr(fidelity, "_cluster_drawings", lambda regions: regions)
    pairwise = _regions(page, glyphs, [])

    def key(region: dict[str, Any]) -> tuple[Any, ...]:
        return (region["kind"], region["display"], tuple(round(v, 3) for v in region["bbox"]), tuple(region.get("glyph_ids", [])), tuple(region["provenance"]), region["grouping_pending"])

    assert [key(r) for r in clustered] == [key(r) for r in pairwise]
    vectors = [r for r in clustered if "native-vector" in r["provenance"]]
    assert len(vectors) == 2
    assert {r["display"] for r in vectors} == {False, True}


def test_cluster_drawings_leaves_singletons_and_glyph_regions_alone() -> None:
    regions = [{"kind": "math", "bbox": [0, 0, 10, 10], "glyph_ids": ["a"], "provenance": ["native-math-glyphs"], "display": False, "grouping_pending": False},
               {"kind": "mixed-region", "bbox": [0, 0, 5, 5], "provenance": ["native-vector"], "display": True, "grouping_pending": True},
               {"kind": "mixed-region", "bbox": [4, 4, 8, 8], "provenance": ["native-vector"], "display": True, "grouping_pending": True},
               {"kind": "mixed-region", "bbox": [20, 20, 25, 25], "provenance": ["native-vector"], "display": True, "grouping_pending": True}]
    result = _cluster_drawings(regions)
    assert [r["bbox"] for r in result] == [[0, 0, 10, 10], [0, 0, 8, 8], [20, 20, 25, 25]]
    assert result[1]["display"] is False and result[2]["display"] is True


# --- LT-064 ------------------------------------------------------------------------------

def test_words_between_two_formulas_of_a_display_stay_inside_it() -> None:
    left = _line("=νQ,", ["CMR10", "CMMI10", "CMMI10", "CMMI10"], line="b1-l0", y=100.0, x=126.0)
    words = _line("or equivalently", "CMR10", line="b1-l1", y=100.0, x=173.0)
    right = _line("=QTνT", ["CMR10", "CMMI10", "CMMI10", "CMMI10", "CMMI10"], line="b1-l2", y=100.0, x=280.0)
    line = left + words + right
    prose = {g["id"] for g in words if g["text"].isalpha()}
    assert _strip_display_prose(line, prose) == line
    # A phrase set off beside the whole formula still returns to the paragraph.
    beside = _line("for all times", "CMR10", line="b1-l1", y=100.0, x=173.0)
    formula = _line("=νQ", ["CMR10", "CMMI10", "CMMI10"], line="b1-l0", y=100.0, x=100.0)
    prose = {g["id"] for g in beside if g["text"].isalpha()}
    assert _strip_display_prose(formula + beside, prose) == formula


# --- LT-065 ------------------------------------------------------------------------------

def test_a_unit_carried_over_as_recorded_keeps_the_hash_the_pipeline_gave_it(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    before = {u.unit_id: u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit) if u.page == 1}
    # The reviewer copies the record's fields, leaving out the ones at their defaults.
    pinned = []
    for u in before.values():
        item: dict[str, Any] = {"unit_id": u.unit_id, "kind": u.kind.value, "bbox": list(u.bbox), "source_markdown": u.source_markdown or u.source_text}
        for key in ("equation_number", "parent_id", "render_policy", "translatable", "continues_from_previous"):
            value = getattr(u, key)
            if value not in (None, False, True, "include") or key == "parent_id":
                item[key] = value
        if not u.translatable:
            item["translatable"] = False
        if u.render_policy.value == "omit":
            item["render_policy"] = "omit"
        pinned.append(item)
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    review["pages"] = [p for p in review["pages"] if p["page"] == 1]
    review["pages"][0]["override"] = {"units": pinned}
    write_json(project / "override.json", review)
    import_source_review(project, project / "override.json", True)
    after = {u.unit_id: u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit) if u.page == 1}
    assert {uid: u.source_hash for uid, u in after.items()} == {uid: u.source_hash for uid, u in before.items()}
    assert read_json(project / "derived/fidelity-pages/p0001.json")["fingerprint"] == read_json(project / "derived/fidelity-pages/p0001.json")["fingerprint"]


# --- LT-067 ------------------------------------------------------------------------------

def test_line_end_punctuation_reads_the_next_native_line() -> None:
    # A question mark closing the sentence is prose.
    run = _line("f(ξ)?", ["CMMI10", "CMR10", "CMMI10", "CMR10", "CMR10"], line="b1-l0")
    assert [g["text"] for g in _trim_prose_edges(list(run), run)] == list("f(ξ)")
    # A math-face comma closing a native line that continues on the same printed row stays.
    head = _line("E(W)2,", ["CMMI10", "CMR10", "CMMI10", "CMR10", "CMR7", "CMMI10"], line="b1-l0", y=100.0)
    tail = _line("t, s", ["CMMI10", "CMMI10", "CMR10", "CMMI10"], line="b1-l1", y=100.0, x=60.0)
    assert [g["text"] for g in _trim_prose_edges(list(head), head, following=tail[0])] == list("E(W)2,")
    # At a real line break the comma is the list's: trimmed.
    below = _line("t, s", ["CMMI10", "CMMI10", "CMR10", "CMMI10"], line="b1-l1", y=114.0, x=60.0)
    assert [g["text"] for g in _trim_prose_edges(list(head), head, following=below[0])] == list("E(W)2")
    # A text-face hyphen closing the line before a lowercase word is the prose compound's.
    sigma = _line("σ-", ["CMMI10", "CMR10"], line="b2-l0", y=100.0)
    algebra = _line("algebra", "CMR10", line="b2-l1", y=114.0)
    assert [g["text"] for g in _trim_prose_edges(list(sigma), sigma, following=algebra[0])] == ["σ"]
    assert [g["text"] for g in _trim_prose_edges(list(sigma), sigma)] == ["σ", "-"]


# --- LT-068 ------------------------------------------------------------------------------

def test_the_override_channel_coalesces_adjacent_inline_fragments_too(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans.models import ProjectConfig
    from littrans.storage import save_project, sha256_file

    pdf = project / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "Let a = 1 b = 2 hold.")
        doc.save(pdf)
    save_project(project, ProjectConfig(project_id="test", title="test", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    prepare_source(project, "1", allow_missing_layout=True)
    assets = load_assets(project)
    merged = [a for a in assets.values() if "adjacent-inline-fragments" in a.provenance]
    assert len(merged) == 1 and len(merged[0].fragments) == 2
    units = [u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit) if u.page == 1]
    pinned = [{"unit_id": u.unit_id, "kind": u.kind.value, "bbox": list(u.bbox), "source_markdown": u.source_markdown or u.source_text,
               "parent_id": u.parent_id, "render_policy": u.render_policy.value, "translatable": u.translatable} for u in units]
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    review["pages"] = [p for p in review["pages"] if p["page"] == 1]
    review["pages"][0]["override"] = {"units": pinned}
    write_json(project / "override.json", review)
    assert import_source_review(project, project / "override.json", True)["changed_pages"] == [1]
    assert merged[0].id in load_assets(project)
    replayed = [u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit) if u.page == 1]
    assert {u.unit_id: u.source_hash for u in replayed} == {u.unit_id: u.source_hash for u in units}


# --- LT-069(a) ---------------------------------------------------------------------------

def test_a_label_absorbed_into_the_paragraph_beside_a_display_binds_to_it() -> None:
    fragment = FidelityFragment(page=8, bbox=[100.0, 300.0, 300.0, 320.0], png_path="a.png", svg_path="a.svg", glyph_ids=["g1"], width=200.0, height=20.0, baseline=10.0, dpi=300, export_method="raw-region", file_sha256={"a.png": "0" * 64, "a.svg": "1" * 64})
    asset = FidelityAsset(id="a-p0008-display", kind="math", source_sha256="2" * 64, content_sha256="3" * 64, fragments=[fragment], display=True, grouping_pending=False, provenance=["test"])
    assets = {asset.id: asset}
    display = _make_unit(8, "p0008-b3", "{{asset:a-p0008-display}}", [100.0, 300.0, 300.0, 320.0], assets)
    before = _make_unit(8, "p0008-b2", "the operator is given by (8.50)", [58.7, 280.0, 400.0, 318.0], assets)
    after = _make_unit(8, "p0008-b4", "(8.32) (i) The first case follows.", [58.7, 305.0, 400.0, 340.0], assets)
    result = _separate_display_units([before, display, after], assets)
    assert result[0].source_text == "the operator is given by"
    assert result[1].equation_number == "8.50" and result[1].kind is UnitKind.EQUATION
    assert result[2].source_text == "(8.32) (i) The first case follows."
    # A label after an already numbered display stays where it is.
    result = _separate_display_units([display, after], assets)
    assert result[0].equation_number == "8.32" and result[1].source_text == "(i) The first case follows."
    # A paragraph far from the display keeps its cross-reference.
    far = _make_unit(8, "p0008-b1", "as shown in (8.50)", [58.7, 200.0, 400.0, 220.0], assets)
    assert _separate_display_units([far, display], assets)[0].source_text == "as shown in (8.50)"


# --- LT-073 ------------------------------------------------------------------------------

def test_a_text_face_accent_over_a_math_base_joins_its_run() -> None:
    glyphs = _line("we have ˆp or ¯θ here", ["CMR10"] * 8 + ["CMR10", "CMMI10"] + ["CMR10"] * 4 + ["CMR10", "CMMI10"] + ["CMR10"] * 5, line="b1-l0")
    hat, p = glyphs[8], glyphs[9]
    hat["bbox"] = [p["bbox"][0], p["bbox"][1] - 4, p["bbox"][2], p["bbox"][1]]
    bar, theta = glyphs[14], glyphs[15]
    bar["bbox"] = [theta["bbox"][0], theta["bbox"][1] - 4, theta["bbox"][2], theta["bbox"][1]]
    accents = _accent_glyph_ids({"b1-l0": glyphs}, lambda g: g["font"] == "CMMI10")
    assert accents == {hat["id"], bar["id"]}
    assert _owned_text(_regions(_Page(), glyphs, []), glyphs) == ["ˆp", "¯θ"]


# --- LT-074 ------------------------------------------------------------------------------

def test_bold_citation_keys_are_not_bold_variables() -> None:
    text = "see [KP92, Theorem 3] and [E11,EK86] but not v here"
    fonts = ["CMR10"] * len(text)
    for bold in ("KP92", "E11", "EK86", " v "):
        start = text.index(bold) + (1 if bold.startswith(" ") else 0)
        for index in range(start, start + len(bold.strip())):
            fonts[index] = "CMBX10"
    glyphs = _line(text, fonts, line="b1-l0")
    ids = _bold_variable_ids({"b1-l0": glyphs})
    assert {glyphs[i]["text"] for i in range(len(text)) if glyphs[i]["id"] in ids} == {"v"}


# --- LT-075 ------------------------------------------------------------------------------

def test_an_override_naming_a_moved_asset_is_refused_with_the_page_and_both_ids(project: Path) -> None:
    from littrans.models import ProjectConfig
    from littrans.storage import save_project, sha256_file

    pdf = project / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "Let a = 1 hold here.")
        doc.save(pdf)
    save_project(project, ProjectConfig(project_id="test", title="test", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=1, profile="technical-book"))
    prepare_source(project, "1", allow_missing_layout=True)
    assets = load_assets(project)
    (real,) = [a.id for a in assets.values()]
    stale = "a-p0001-000000000000"
    units = [u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit) if u.page == 1]
    pinned = [{"unit_id": u.unit_id, "kind": u.kind.value, "bbox": list(u.bbox),
               "source_markdown": (u.source_markdown or u.source_text).replace(real, stale)} for u in units]
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    review["pages"] = [p for p in review["pages"] if p["page"] == 1]
    review["pages"][0]["override"] = {"units": pinned}
    write_json(project / "override.json", review)
    with pytest.raises(ValueError) as refused:
        import_source_review(project, project / "override.json", True)
    message = str(refused.value)
    assert message.startswith("page 1: unit overrides must reference each page asset exactly once")
    assert f"not cut on this page any more: {stale}" in message and f"cut but unreferenced: {real}" in message
    # The same override recorded in the ledger is refused on replay, not lost in a KeyError.
    ledger_path = project / "derived/fidelity-pages/p0001.json"
    ledger = read_json(ledger_path)
    ledger["source_overrides"] = {"units": pinned}
    write_json(ledger_path, ledger)
    before = (project / "derived/units.jsonl").read_bytes()
    with pytest.raises(ValueError, match="page 1: the recorded source override cannot be replayed .*exactly once.*--discard-overrides"):
        prepare_source(project, "1", replace=True, allow_missing_layout=True)
    assert (project / "derived/units.jsonl").read_bytes() == before


# --- LT-076 ------------------------------------------------------------------------------

def test_an_enumerated_sibling_returns_to_the_parent_its_enumeration_opened_with() -> None:
    intro = _unit("p0124-b2", "There are two ways to think about stochastic processes:", [58.7, 268, 417, 322])
    first = _unit("p0124-b4", "(1) As random functions of one variable.", [82.1, 354, 417, 405])
    extension = _unit("p0124-b5", "The natural extension would be random fields.", [101.0, 408, 417, 446])
    second = _unit("p0124-b6", "(2) As a probability distribution on the path spaces.", [82.1, 462, 417, 541])
    closing = _unit("p0124-b8", "If we observe a stochastic process at finitely many points.", [101.0, 543, 417, 621])
    plan = _plan(margin=101.0, first_x={"b2": 58.7, "b4": 82.1, "b5": 119.0, "b6": 82.1, "b8": 119.0},
                 blocks=[{"id": "b2", "paragraph_break": True}, {"id": "b4", "paragraph_break": True}, {"id": "b6", "paragraph_break": True}])
    result = assemble_structure([intro, first, extension, second, closing], {}, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0124-b2", "p0124-b2", "p0124-b5", "p0124-b2", "p0124-b8"]


def test_a_heading_closes_an_enumeration_so_the_next_list_hangs_from_its_own_intro() -> None:
    intro = _unit("p0003-b1", "Sampling can be used to:", [58.7, 100, 400, 110])
    first = _unit("p0003-b2", "(i) Compute expectations.", [82.1, 120, 400, 130])
    heading = _unit("p0003-b3", "3.2 Variance reduction", [58.7, 150, 300, 162], kind="heading")
    intro2 = _unit("p0003-b4", "Two devices are common:", [58.7, 170, 400, 180])
    item = _unit("p0003-b5", "(i) Importance sampling.", [82.1, 190, 400, 200])
    plan = _plan(margin=101.0, first_x={"b1": 58.7, "b2": 82.1, "b3": 58.7, "b4": 58.7, "b5": 82.1},
                 blocks=[{"id": "b2", "paragraph_break": True}, {"id": "b5", "paragraph_break": True}])
    result = assemble_structure([intro, first, heading, intro2, item], {}, plan, _make_unit)
    assert [u.parent_id for u in result] == ["p0003-b1", "p0003-b1", "p0003-b3", "p0003-b4", "p0003-b4"]
