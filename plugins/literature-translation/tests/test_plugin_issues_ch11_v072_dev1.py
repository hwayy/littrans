"""LitTrans 0.7.2-dev.1: the defects the chapter 11 extraction ledger reported (LT-094 … LT-097).

The ledger's pages (PDF 252, 253, 255, 256 of the Appl. Stoch. Anal. project) were replayed
on a scratch copy to settle the rules; the tests here use synthetic glyph lines, detector
boxes and units only. They cover: source preparation hands over without cutting batches
(094); a display split around its own label line (095); the panels of one captioned figure
(096); the prose a page starts or ends with beyond a float (097).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from test_source_structure_v8 import _line, _Page

from littrans.evidence import continuation_neighbors
from littrans.fidelity import _make_unit, _regions, _separate_display_units
from littrans.fidelity_models import FidelityAsset, FidelityFragment
from littrans.source_structure import assemble_structure

PLUGIN = Path(__file__).resolve().parents[1]


def _box(bbox: list[float]) -> list[float]:
    """A detector item's box (the detector works at twice the PDF scale)."""
    return [v * 2 for v in bbox]


# --- LT-094: source preparation ends before batches -------------------------------------

def test_source_preparation_hands_over_without_creating_batches() -> None:
    prepare = (PLUGIN / "skills/prepare-literature-source/SKILL.md").read_text(encoding="utf-8")
    continue_ = (PLUGIN / "skills/continue-literature-translation/SKILL.md").read_text(encoding="utf-8")
    assert "Run `batch create" not in prepare
    assert "batch create" in continue_
    assert "batches" not in prepare.split("---")[1].split("description:")[1].splitlines()[0]


# --- LT-095: a display split around its own label line -----------------------------------

def _tagged_display(between: list[dict[str, Any]] | None = None, second_label: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Two display rows with the label ``(11.60)`` on a line of its own between them."""
    upper = _line("Z=abcdefgh", "CMMI10", line="b1-l0", y=130.0, x=59.0)
    label = _line("(11.60)", "CMR10", line="b2-l0", y=145.0, x=59.0)
    lower = _line("=def", "CMMI10", line="b3-l0", y=160.0, x=104.0)
    glyphs = [*upper, *label, *lower, *(between or [])]
    items = [{"label": "display_formula", "bbox": _box([58.0, 129.0, 120.0, 141.0])},
             {"label": "formula_number", "bbox": _box([58.0, 144.0, 102.0, 156.0])},
             {"label": "display_formula", "bbox": _box([103.0, 159.0, 128.0, 171.0])}]
    if second_label:
        glyphs += _line("(11.61)", "CMR10", line="b4-l0", y=160.0, x=300.0)
    return glyphs, items


def _displays(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in regions if r["kind"] == "math" and r["display"]]


def test_display_rows_split_by_their_own_label_line_are_one_region() -> None:
    glyphs, items = _tagged_display()
    displays = _displays(_regions(_Page(), glyphs, items))
    assert len(displays) == 1
    joined = displays[0]
    assert [len(f["glyph_ids"]) for f in joined["fragments"]] == [10, 4]
    assert "label-line-joined" in joined["provenance"]
    # The label is owned by no asset: it binds as the display's number.
    label_ids = {g["id"] for g in glyphs if g["line"] == "b2-l0"}
    assert not label_ids & set(joined["glyph_ids"])


def test_rows_with_prose_or_their_own_label_between_stay_apart() -> None:
    prose = _line("so that", "CMR10", line="b5-l0", y=146.0, x=105.0)
    glyphs, items = _tagged_display(between=prose)
    assert len(_displays(_regions(_Page(), glyphs, items))) == 2
    # The lower row already carries a label beside it: the label line numbers the upper one.
    glyphs, items = _tagged_display(second_label=True)
    assert len(_displays(_regions(_Page(), glyphs, items))) == 2


def _asset(aid: str, kind: str, boxes: list[list[float]], display: bool = True) -> FidelityAsset:
    fragments = [FidelityFragment(page=252, bbox=b, width=b[2] - b[0], height=b[3] - b[1], png_path="x.png", svg_path="x.svg",
                                  file_sha256={name: "c" * 64 for name in ("x.png", "x.svg")}) for b in boxes]
    return FidelityAsset(id=aid, kind=kind, source_sha256="0" * 64, content_sha256="1" * 64, fragments=fragments,
                         display=display, grouping_pending=False, provenance=["test"])


def test_a_label_line_just_above_or_between_the_rows_numbers_the_display() -> None:
    # Left label above the display it numbers (p252 (11.59)).
    assets = {"d": _asset("d", "math", [[60.0, 524.0, 416.0, 575.0]])}
    units = [_make_unit(252, "p0252-b42", "{{asset:d}}", [60.0, 524.0, 416.0, 575.0], assets),
             _make_unit(252, "p0252-b56", "(11.59)", [59.0, 511.0, 92.0, 522.0], assets)]
    result = _separate_display_units(units, assets)
    assert [(u.unit_id, u.equation_number) for u in result] == [("p0252-b42", "11.59")]
    # Between the two rows of a joined display (p253 (11.60)).
    assets = {"d": _asset("d", "math", [[59.0, 129.0, 301.0, 173.0], [104.0, 185.0, 417.0, 221.0]])}
    units = [_make_unit(253, "p0253-b2", "{{asset:d}}", [59.0, 129.0, 301.0, 173.0], assets),
             _make_unit(253, "p0253-b27", "(11.60)", [59.0, 177.0, 92.0, 187.0], assets)]
    result = _separate_display_units(units, assets)
    assert [(u.unit_id, u.equation_number) for u in result] == [("p0253-b2", "11.60")]
    assert result[0].bbox[3] >= 221.0


def test_a_label_line_away_from_the_display_or_past_prose_stays_text() -> None:
    assets = {"d": _asset("d", "math", [[60.0, 540.0, 416.0, 575.0]])}
    far = [_make_unit(252, "p0252-b42", "{{asset:d}}", [60.0, 540.0, 416.0, 575.0], assets),
           _make_unit(252, "p0252-b56", "(11.59)", [59.0, 511.0, 92.0, 522.0], assets)]
    assert [u.equation_number for u in _separate_display_units(far, assets)] == [None, None]
    assets = {"d": _asset("d", "math", [[60.0, 530.0, 416.0, 575.0]])}
    past_prose = [_make_unit(252, "p0252-b56", "(11.59)", [59.0, 511.0, 92.0, 522.0], assets),
                  _make_unit(252, "p0252-b57", "where", [59.0, 522.5, 90.0, 529.5], assets),
                  _make_unit(252, "p0252-b42", "{{asset:d}}", [60.0, 530.0, 416.0, 575.0], assets)]
    assert all(u.equation_number is None for u in _separate_display_units(past_prose, assets))


# --- LT-096: the panels of one captioned figure -------------------------------------------

PANELS = [[94.0, 88.0, 235.0, 192.0], [240.0, 86.0, 380.0, 192.0], [167.0, 202.0, 308.0, 306.0],
          [95.0, 314.0, 380.0, 420.0], [168.0, 428.0, 306.0, 532.0]]
CAPTION = [91.0, 540.0, 384.0, 586.0]


def _figures(regions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in regions if r["kind"] == "figure"]


def _figure_page(panels: list[list[float]], captions: list[list[float]], glyphs: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    items = [{"label": "image", "bbox": _box(p)} for p in panels]
    items += [{"label": "figure_title", "bbox": _box(c)} for c in captions]
    text = _line("Figure 11.1. Free energy", "CMR10", line="b9-l0", y=542.0, x=95.0)
    return _regions(_Page(), [*text, *(glyphs or [])], items)


def test_panels_sharing_one_caption_are_one_figure_read_row_by_row() -> None:
    figures = _figures(_figure_page(PANELS, [CAPTION]))
    assert len(figures) == 1
    assert [f["bbox"] for f in figures[0]["fragments"]] == PANELS
    assert "figure-panels-joined" in figures[0]["provenance"]
    stacked = _figures(_figure_page([[144.0, 86.0, 331.0, 230.0], [142.0, 230.0, 333.0, 374.0]], [[92.0, 384.0, 383.0, 408.0]]))
    assert len(stacked) == 1 and len(stacked[0]["fragments"]) == 2


def test_separately_captioned_distant_or_prose_separated_figures_stay_apart() -> None:
    # Each panel with a caption of its own.
    left, right = [60.0, 88.0, 200.0, 192.0], [215.0, 88.0, 355.0, 192.0]
    assert len(_figures(_figure_page([left, right], [[60.0, 196.0, 200.0, 206.0], [215.0, 196.0, 355.0, 206.0]]))) == 2
    # Farther apart than the panel gap.
    assert len(_figures(_figure_page([PANELS[0], [94.0, 230.0, 235.0, 330.0]], [CAPTION]))) == 2
    # A line of text between the panels (a sub-caption or prose).
    between = _line("(a) the first", "CMR10", line="b5-l0", y=193.0, x=100.0)
    assert len(_figures(_figure_page([PANELS[0], [94.0, 206.0, 235.0, 310.0]], [CAPTION], between))) == 2
    # No caption detected at all.
    assert len(_figures(_figure_page(PANELS[:2], []))) == 2


# --- LT-097: the prose a page starts or ends with beyond a float --------------------------

ASSETS: dict[str, FidelityAsset] = {}


def _unit(uid: str, text: str, bbox: list[float], page: int, kind: str = "paragraph", **extra: Any) -> Any:
    for aid in re.findall(r"\{\{asset:([^}]+)\}\}", text):
        ASSETS.setdefault(aid, _asset(aid, "figure" if kind == "figure" else "math", [bbox]))
    return _make_unit(page, uid, text, bbox, ASSETS, kind=kind, **extra)


def _plan(**overrides: Any) -> dict[str, Any]:
    plan: dict[str, Any] = {"omitted": {}, "notes": {}, "note_top": 1000.0, "first_x": {}, "margin": 58.7,
                            "font_size": 10.9, "indent_style": True, "display_blocks": [], "blocks": []}
    plan.update(overrides)
    return plan


def test_page_edge_flags_skip_a_float_at_the_page_top_or_bottom() -> None:
    top = [_unit("p0256-b1", "{{asset:fig}}", [142.0, 86.0, 333.0, 374.0], 256, kind="figure"),
           _unit("p0256-b14", "**Figure 11.2.** Plot of the energy.", [95.0, 383.0, 382.0, 406.0], 256, kind="caption"),
           _unit("p0256-b15", "model with nearest neighbours. Indeed, the", [58.7, 428.0, 417.0, 453.0], 256)]
    result = assemble_structure(top, ASSETS, _plan(first_x={"b15": 58.7}), _make_unit)
    assert [u.continues_from_previous for u in result] == [False, False, True]
    bottom = [_unit("p0255-b47", "A typical plot against the one-dimensional nearest neighbor", [58.7, 505.0, 417.0, 530.0], 255),
              _unit("p0255-b48", "{{asset:fig2}}", [142.0, 540.0, 333.0, 640.0], 255, kind="figure"),
              _unit("p0255-b49", "**Figure 11.3.** Another plot.", [95.0, 645.0, 382.0, 660.0], 255, kind="caption")]
    result = assemble_structure(bottom, ASSETS, _plan(first_x={"b47": 75.0}), _make_unit)
    assert [u.continued_to_next for u in result] == [True, False, False]
    # A capital opening keeps the LT-080 reading: the sender's flag carries the sentence.
    top[2] = _unit("p0256-b15", "Ising model. Indeed, the", [58.7, 428.0, 417.0, 453.0], 256)
    assert not assemble_structure(top, ASSETS, _plan(first_x={"b15": 58.7}), _make_unit)[2].continues_from_previous


def test_a_continuation_reaches_the_prose_beyond_the_float() -> None:
    sender = _unit("p0255-b47", "the one-dimensional nearest neighbor", [58.7, 605.0, 417.0, 659.0], 255, continued_to_next=True)
    figure = _unit("p0256-b1", "{{asset:fig}}", [142.0, 86.0, 333.0, 374.0], 256, kind="figure", parent_id="p0256-b1")
    caption = _unit("p0256-b14", "**Figure 11.2.** Plot.", [95.0, 383.0, 382.0, 406.0], 256, kind="caption", parent_id="p0256-b1")
    receiver = _unit("p0256-b15", "Ising model. Indeed, the", [58.7, 428.0, 417.0, 453.0], 256)
    neighbors = continuation_neighbors([sender, figure, caption, receiver])
    assert "p0256-b15" in neighbors["p0255-b47"] and "p0255-b47" in neighbors["p0256-b15"]
    # Without a flag on either side, the float separates two paragraphs.
    plain = sender.model_copy(update={"continued_to_next": False})
    assert "p0256-b15" not in continuation_neighbors([plain, figure, caption, receiver]).get("p0255-b47", set())
