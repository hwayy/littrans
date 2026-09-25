"""LitTrans 0.7.5-dev.1: review fixes to the 0.7.5 candidate (LT-098, LT-099).

Codex review of the 0.7.5 release candidate reported two defects in the page region
heuristics: the band check that rejoins a display around its own label line exempted
every equation label on the page, not only the line being processed, so an unrelated
label between the rows no longer prevented the merge (LT-098); and a proximity cluster
holding the panels of two separately captioned figures was abandoned whole instead of
being partitioned by caption ownership (LT-099). Synthetic glyph lines and detector
boxes only.
"""
from __future__ import annotations

from typing import Any

from test_source_structure_v8 import _line, _Page

from littrans.fidelity import _regions


def _box(bbox: list[float]) -> list[float]:
    """A detector item's box (the detector works at twice the PDF scale)."""
    return [v * 2 for v in bbox]


# --- LT-098: only the glyphs of the label line being processed are exempt ----------------

def _tagged_display(extra: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Two display rows with the label ``(11.60)`` on a line of its own between them."""
    upper = _line("Z=abcdefgh", "CMMI10", line="b1-l0", y=130.0, x=59.0)
    label = _line("(11.60)", "CMR10", line="b2-l0", y=145.0, x=59.0)
    lower = _line("=def", "CMMI10", line="b3-l0", y=160.0, x=104.0)
    glyphs = [*upper, *label, *lower, *(extra or [])]
    items = [{"label": "display_formula", "bbox": _box([58.0, 129.0, 120.0, 141.0])},
             {"label": "formula_number", "bbox": _box([58.0, 144.0, 102.0, 156.0])},
             {"label": "display_formula", "bbox": _box([103.0, 159.0, 128.0, 171.0])}]
    return glyphs, items


def _displays(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in regions if r["kind"] == "math" and r["display"]]


def test_an_unrelated_label_line_between_the_rows_prevents_the_merge() -> None:
    # A second label set on a line of its own in the gap numbers another display: it is
    # ink between the rows, and the band check must not ignore it (LT-098).
    second = _line("(11.61)", "CMR10", line="b4-l0", y=148.0, x=66.0)
    glyphs, items = _tagged_display(extra=second)
    assert len(_displays(_regions(_Page(), glyphs, items))) == 2


def test_its_own_label_line_still_rejoins_the_rows() -> None:
    glyphs, items = _tagged_display()
    displays = _displays(_regions(_Page(), glyphs, items))
    assert len(displays) == 1 and "label-line-joined" in displays[0]["provenance"]


# --- LT-099: an over-cluster with several captions is partitioned by caption ownership ----

def _figures(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in regions if r["kind"] == "figure"]


def _figure_page(panels: list[list[float]], captions: list[list[float]], texts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [{"label": "image", "bbox": _box(p)} for p in panels]
    items += [{"label": "figure_title", "bbox": _box(c)} for c in captions]
    return _regions(_Page(), texts, items)


def test_side_by_side_captioned_stacks_join_one_figure_per_caption() -> None:
    # Two stacks of panels, each captioned of its own, sit closer than the panel gap, so
    # proximity clusters every panel together; caption ownership splits the cluster back
    # into one figure per caption (LT-099).
    a1, a2 = [60.0, 88.0, 200.0, 192.0], [60.0, 206.0, 200.0, 310.0]
    b1, b2 = [212.0, 88.0, 352.0, 192.0], [212.0, 206.0, 352.0, 310.0]
    captions = [[60.0, 314.0, 200.0, 324.0], [212.0, 314.0, 352.0, 324.0]]
    texts = [*_line("Figure 11.2. First", "CMR10", line="b9-l0", y=315.0, x=62.0),
             *_line("Figure 11.3. Second", "CMR10", line="b10-l0", y=315.0, x=214.0)]
    figures = _figures(_figure_page([a1, a2, b1, b2], captions, texts))
    assert len(figures) == 2
    assert [f["bbox"] for f in figures[0]["fragments"]] == [a1, a2]
    assert [f["bbox"] for f in figures[1]["fragments"]] == [b1, b2]
    assert all("figure-panels-joined" in f["provenance"] for f in figures)


def test_panels_that_each_carry_a_caption_still_stay_apart() -> None:
    left, right = [60.0, 88.0, 200.0, 192.0], [212.0, 88.0, 352.0, 192.0]
    captions = [[60.0, 196.0, 200.0, 206.0], [212.0, 196.0, 352.0, 206.0]]
    texts = [*_line("Figure 11.4.", "CMR10", line="b9-l0", y=197.0, x=62.0),
             *_line("Figure 11.5.", "CMR10", line="b10-l0", y=197.0, x=214.0)]
    assert len(_figures(_figure_page([left, right], captions, texts))) == 2
