"""Round 13 of the pilot ledger: LT-046..LT-048, found in the chapter-2 formula repairs.

Inline notation lost its right half three ways (brackets balanced per native line, an
operator name breaking an open run, an operator name before a math-face space dropped);
an inline ``cases`` block whose CMEX brace decodes to a control character was cut into
one asset per row; and the region-override channel changed a page in ways the reviewer
did not ask for (an empty ``glyph_ids`` list forced the export fallback, a rule with
reviewer provenance became prose, string acceptances were ignored, a recorded ``units``
block was dropped without a word). Synthetic glyph lines and pages only.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
from test_fidelity_source import project as fidelity_project  # noqa: F401
from test_source_structure_v8 import _line, _owned_text, _Page

from littrans import fidelity
from littrans.fidelity import (
    _delimiter_piece,
    _regions,
    _trim_prose_edges,
    build_source_review_packet,
    import_source_review,
    page_review_findings,
    prepare_source,
)
from littrans.fidelity_models import load_assets
from littrans.models import SourceUnit, UnitKind
from littrans.storage import read_json, read_jsonl, write_json

project = fidelity_project


def _fonts(text: str, math: str) -> list[str]:
    """CMMI for the characters listed in ``math``, the text face otherwise."""
    return ["CMMI10" if c in math else "CMR10" for c in text]


# --- LT-046 ①: brackets balance over the expression, not the native line ------------------

def test_closing_bracket_on_a_second_native_line_is_kept_when_the_expression_balances() -> None:
    # "is O(n^{-1/2}). This" — MuPDF puts the superscript's second digit and the closing
    # bracket into a second block: the line group "2 )" has one closer and no opener.
    head = _line("is O(n", _fonts("is O(n", "On"), line="b16-l0", y=100.0)
    head[-2]["font"] = "CMMI10"
    tail = _line("2). This", ["CMR6", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10"], line="b17-l0", y=100.0, x=36.0)
    line = head + tail
    group = [tail[0], tail[1]]
    assert [g["text"] for g in _trim_prose_edges(list(group), tail)] == ["2"]
    assert [g["text"] for g in _trim_prose_edges(list(group), tail, balance=[head[4], head[5], *group])] == ["2", ")"]
    regions = _regions(_Page(), line, [{"label": "inline_formula", "bbox": [30, 190, 90, 230]}])
    assert _owned_text(regions, line) == ["O(n2)"]


def test_balance_still_trims_a_prose_bracket_that_closes_the_sentence() -> None:
    # "(the space L^p(Ω))" keeps the formula's own bracket and returns the prose one.
    text = "(the space Lq(Ω))."
    fonts = _fonts(text, "LqΩ")
    line = _line(text, fonts, line="b0-l0")
    regions = _regions(_Page(), line, [])
    assert _owned_text(regions, line) == ["Lq(Ω)"]


# --- LT-046 ②③: operator names inside and before an inline run -----------------------------

def test_operator_names_continue_an_open_run_and_survive_a_math_face_space() -> None:
    # "i.e., lim_{ε→0} log c_ε / log d_ε if" — TeX sets the operator names in the text face,
    # the space after "log" in the math face (CMMI), so the run used to start at the space.
    text = "i.e., limε→0 log cε/ log dε if"
    fonts = _fonts(text, "ε→cd/")
    line = _line(text, fonts, line="b24-l0")
    for glyph in line:
        if glyph["text"] == " " and line[line.index(glyph) + 1]["text"] in "cd":
            glyph["font"] = "CMMI10"
    regions = _regions(_Page(), line, [])
    assert _owned_text(regions, line) == ["limε→0 log cε/ log dε"]


def test_a_minus_sign_log_and_argument_are_one_run() -> None:
    # "as −log P(D)." with a math-face space between the name and its argument.
    text = "as −log P(D)."
    fonts = _fonts(text, "−PD")
    line = _line(text, fonts, line="b13-l0")
    line[7]["font"] = "MSBM10"  # the space TeX sets before the blackboard P
    regions = _regions(_Page(), line, [])
    assert _owned_text(regions, line) == ["−log P(D)"]


def test_a_prose_article_before_notation_is_still_not_a_prefix() -> None:
    line = _line("of a x and the log of y", _fonts("of a x and the log of y", "xy"), line="b0-l0")
    regions = _regions(_Page(), line, [])
    assert _owned_text(regions, line) == ["x", "y"]


# --- LT-047: a re-encoded CMEX brace and the rows it brackets -------------------------------

def test_delimiter_pieces_are_recognised_by_shape_when_the_subset_font_is_re_encoded() -> None:
    assert _delimiter_piece({"text": "\x05", "font": "CMEX10", "bbox": [230.0, 262.0, 238.6, 304.0]})
    assert _delimiter_piece({"text": "\x3a", "font": "CMEX10", "bbox": [0, 0, 5, 6]})
    # A big operator decoding to a control character is not a piece: it is as wide as tall.
    assert not _delimiter_piece({"text": "\x04", "font": "CMEX10", "bbox": [163.7, 331.5, 174.0, 342.4]})
    assert not _delimiter_piece({"text": "\x05", "font": "CMR10", "bbox": [0, 0, 2, 20]})
    assert not _delimiter_piece({"text": "A", "font": "CMR10", "bbox": [0, 0, 2, 20]})


def _cases_page() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(ii) Type II: G(x) = { 0, x ≤ 0, / e^{-x}, x > 0.`` as MuPDF reports it: the brace
    on a line of its own, each row in two native lines (value, condition)."""
    label = _line("(ii) Type II: ", "CMTI10", line="b9-l0", y=281.5, x=80.0)
    head = _line("G(x) =", _fonts("G(x) =", "Gx"), line="b9-l0", y=281.5, x=192.0)
    for g in head:
        g["id"] = "h" + g["id"]
    brace = [{"id": "b10-l0-0", "text": "\x05", "font": "CMEX10", "bbox": [230.0, 271.0, 238.0, 303.5], "line": "b10-l0",
              "size": 10.0, "baseline": 271.3, "origin": [230.0, 271.3]}]
    row1_value = _line("0,", ["CMR10", "CMMI10"], line="b11-l0", y=274.0, x=239.0)
    row1_condition = _line("x ≤ 0,", ["CMMI10", "CMSY10", "CMSY10", "CMR10", "CMR10", "CMMI10"], line="b11-l1", y=274.0, x=281.0)
    row2_value = _line("e−x,", ["CMMI10", "CMSY8", "CMMI8", "CMMI10"], line="b11-l2", y=290.0, x=239.0)
    row2_condition = _line("x > 0.", ["CMMI10", "CMR10", "CMMI10", "CMR10", "CMR10", "CMMI10"], line="b11-l3", y=290.0, x=281.0)
    prose = [g for row in range(3) for g in _line("Prose of the paragraph above the list item here", "CMR10", line=f"b1-l{row}", y=200.0 + 12 * row, x=80.0)]
    glyphs = prose + label + head + brace + row1_value + row1_condition + row2_value + row2_condition
    return glyphs, [row1_value, row1_condition, row2_value, row2_condition]


def test_inline_cases_rows_are_owned_by_the_region_of_their_brace() -> None:
    glyphs, rows = _cases_page()
    regions = _regions(_Page(), glyphs, [])
    cases = [r for r in regions if "stretched-delimiter-rows" in r["provenance"]]
    assert len(cases) == 1
    region = cases[0]
    assert not region["display"] and "fragments" not in region
    owned = "".join(g["text"] for g in glyphs if g["id"] in region["glyph_ids"])
    assert owned == "G(x) =\x050,x ≤ 0,e−x,x > 0"
    # The sentence period after the last row is prose; the commas inside the block are not.
    assert rows[3][-1]["id"] not in region["glyph_ids"]
    assert rows[0][-1]["id"] in region["glyph_ids"] and rows[1][-1]["id"] in region["glyph_ids"]
    assert [r for r in regions if r["kind"] == "math"] == cases
    assert region["bbox"][1] <= 271.0 and region["bbox"][3] >= 300.0


def test_a_big_delimiter_on_a_paragraph_line_absorbs_no_neighbouring_line() -> None:
    # A \\Big( in running text: its ink reaches neither adjacent baseline.
    above = _line("the line above the formula", "CMR10", line="b0-l0", y=88.0, x=60.0)
    line = _line("so f(x) holds", _fonts("so f(x) holds", "fx"), line="b0-l1", y=100.0, x=60.0)
    line[4].update({"text": "\x00", "font": "CMEX10", "bbox": [84.0, 93.0, 88.0, 111.0]})
    line[6].update({"text": "\x01", "font": "CMEX10", "bbox": [96.0, 93.0, 100.0, 111.0]})
    below = _line("and the line below it continues", "CMR10", line="b0-l2", y=112.0, x=60.0)
    glyphs = above + line + below
    regions = _regions(_Page(), glyphs, [])
    assert _owned_text(regions, glyphs) == ["f\x00x\x01"]
    assert not any("stretched-delimiter-rows" in r["provenance"] for r in regions)


def test_stacked_pieces_still_merge_but_integrals_on_two_display_lines_do_not() -> None:
    from test_plugin_issues_ch1_round2 import _glyph

    # Two display lines opening with a display-size integral at the same x.
    first = [_glyph("i1", "\x03", "CMEX10", 175.8, 221.3, width=9.6, line="b11-l0", size=24.2)]
    first_rest = _line("X dP", _fonts("X dP", "XP"), line="b11-l0", y=232.0, x=190.0)
    second = [_glyph("i2", "\x03", "CMEX10", 175.5, 275.2, width=9.6, line="b18-l0", size=24.2)]
    second_rest = _line("Y dP", _fonts("Y dP", "YP"), line="b18-l0", y=286.0, x=190.0)
    glyphs = first + first_rest + second + second_rest
    regions = _regions(_Page(), glyphs, [{"label": "display_formula", "bbox": [340, 430, 500, 500]},
                                         {"label": "display_formula", "bbox": [340, 540, 500, 610]}])
    displays = [r for r in regions if r["display"]]
    assert len(displays) == 2 and not any("stretched-delimiter-merged" in r["provenance"] for r in regions)


# --- LT-048 ②③: an empty glyph list is a raw crop and a reviewer's rule stays a rule -------

def _regions_review(root: Path, page: int, regions: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    packet = build_source_review_packet(root, str(page))
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    review["pages"] = [p for p in review["pages"] if p["page"] == page]
    review["pages"][0]["override"] = {"regions": regions}
    review["pages"][0].update(extra)
    write_json(root / "override.json", review)
    return import_source_review(root, root / "override.json", True)


def test_empty_glyph_ids_keep_the_declared_kind_and_a_reviewer_rule_is_omitted(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    before = load_assets(project)
    rule = next(a for a in before.values() if a.kind == "mixed-region" and not a.fragments[0].glyph_ids)
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    assert next(u for u in units if rule.id in u.source_text).render_policy == "omit"
    regions = []
    for asset in before.values():
        fragment = asset.fragments[0]
        region = {"id": asset.id, "kind": asset.kind, "display": asset.display, "grouping_pending": False,
                  "bbox": list(fragment.bbox), "glyph_ids": list(fragment.glyph_ids)}
        if asset.id == rule.id:
            region["provenance"] = ["visual-region-correction"]
        regions.append(region)
    result = _regions_review(project, 1, regions)
    assert result["changed_pages"] == [1]
    after = load_assets(project)
    assert after[rule.id].kind == "mixed-region" and not after[rule.id].grouping_pending
    assert after[rule.id].fragments[0].export_method == "raw-region"
    assert not any(p.startswith("precise-export-unavailable") for p in after[rule.id].provenance)
    unit = next(u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit) if rule.id in u.source_text)
    assert unit.kind is UnitKind.NOTE and unit.render_policy == "omit" and not unit.translatable
    assert read_json(project / "derived/fidelity-pages/p0001.json")["grouping_pending"] == []


def test_export_fallback_keeps_a_declared_figure(project: Path) -> None:
    from littrans.fidelity import _asset_impl

    with fitz.open(project / "source/book.pdf") as doc:
        glyphs = fidelity._native(doc[0])[0]
        broken = [{**g, "origin": [-1000.0, -1000.0]} for g in glyphs[:2]]
        asset = _asset_impl(project, doc, 1, "0" * 64, {"kind": "figure", "bbox": [55, 70, 200, 90], "glyph_ids": [g["id"] for g in broken]}, broken)
    assert asset.kind == "figure" and asset.grouping_pending
    assert asset.fragments[0].export_method == "raw-region"
    assert any(p.startswith("precise-export-unavailable") for p in asset.provenance)


# --- LT-048 ④: malformed acceptances are an error ------------------------------------------

def test_string_acceptances_are_refused_not_ignored(project: Path) -> None:
    page = {"assets": [{"id": "a1", "kind": "mixed-region", "grouping_pending": True, "fragments": [{"glyph_ids": []}]}],
            "ledger": {"glyphs": []}, "units": []}
    assert page_review_findings(page, {"accepted_grouping_pending": [{"asset_id": "a1", "reason": "a rule"}]}) == []
    with pytest.raises(ValueError, match="accepted_grouping_pending must be a list of objects"):
        page_review_findings(page, {"accepted_grouping_pending": ["a1"]})
    prepare_source(project, "1", allow_missing_layout=True)
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "r"
    review["pages"][0]["accepted_grouping_pending"] = ["a-p0001-anything"]
    write_json(project / "review.json", review)
    with pytest.raises(ValueError, match=r"page 1: accepted_grouping_pending must be a list of objects"):
        import_source_review(project, project / "review.json", True)


# --- LT-048 ①: a recorded override block is never dropped silently -------------------------

def test_an_override_that_omits_a_recorded_block_is_refused_unless_dropped_on_purpose(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    assets = load_assets(project)
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    regions = [{"preserve_asset_id": aid} for aid in assets]
    pinned = [{"unit_id": u.unit_id, "kind": u.kind.value, "bbox": list(u.bbox), "source_markdown": u.source_markdown,
               "parent_id": u.parent_id, "render_policy": u.render_policy, "translatable": u.translatable}
              for u in units if u.page == 1]
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    review["pages"] = [p for p in review["pages"] if p["page"] == 1]
    review["pages"][0]["override"] = {"regions": regions, "units": pinned}
    write_json(project / "override.json", review)
    assert import_source_review(project, project / "override.json", True)["changed_pages"] == [1]
    ledger = read_json(project / "derived/fidelity-pages/p0001.json")
    assert set(ledger["source_overrides"]) == {"regions", "units"}
    # The next correction names regions only: the recorded units block would vanish.
    with pytest.raises(ValueError, match=r"page 1: the recorded override carries units \(\d+ entries\) that this override omits"):
        _regions_review(project, 1, regions)
    assert read_json(project / "derived/fidelity-pages/p0001.json")["source_overrides"]["units"] == pinned
    # Dropping it on purpose is stated, and the ledger records the override without it.
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    review["pages"] = [p for p in review["pages"] if p["page"] == 1]
    review["pages"][0]["override"] = {"regions": regions, "units": None}
    write_json(project / "override.json", review)
    assert import_source_review(project, project / "override.json", True)["changed_pages"] == [1]
    assert read_json(project / "derived/fidelity-pages/p0001.json")["source_overrides"] == {"regions": regions}
    # An override of nothing but nulls is not a correction.
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    review["pages"] = [p for p in review["pages"] if p["page"] == 1]
    review["pages"][0]["override"] = {"regions": None}
    write_json(project / "override.json", review)
    with pytest.raises(ValueError, match="drops every block"):
        import_source_review(project, project / "override.json", True)
