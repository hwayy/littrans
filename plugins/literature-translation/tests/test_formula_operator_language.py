"""Known operator composition stays notation in preparation and source review."""
from __future__ import annotations

from typing import Any

import pytest

from littrans.fidelity import _auto_formula_conditions, _undeclared_formula_language


def _glyphs(text: str, *, argument: bool = True) -> list[dict[str, Any]]:
    glyphs: list[dict[str, Any]] = [
        {"id": str(i), "text": char, "font": "CMR10", "size": 10,
         "bbox": [i * 5.0, 10.0, i * 5.0 + 4, 20.0],
         "origin": [i * 5.0, 18.0], "line": "l0"}
        for i, char in enumerate(text)
    ]
    if argument:
        x = len(text) * 5.0
        glyphs.append({"id": "arg", "text": "x", "font": "CMMI10", "size": 10,
                       "bbox": [x, 10.0, x + 4, 20.0], "origin": [x, 18.0], "line": "l0"})
    return glyphs


@pytest.mark.parametrize("text", ["loglog", "log log", "logloglog", "sin cos", "erf", "+ erf", "erfc"])
@pytest.mark.parametrize("measured_ink", [False, True])
def test_applied_operator_names_are_not_conditions(text: str, measured_ink: bool) -> None:
    glyphs = _glyphs(text)
    region = {"glyph_ids": [g["id"] for g in glyphs]}
    assert _auto_formula_conditions(region, {g["id"]: g for g in glyphs},
                                    measured_ink=measured_ink) == []
    page = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "formula", "kind": "math",
            "fragments": [region], "formula_conditions": []}]}
    assert _undeclared_formula_language(page) == {}


@pytest.mark.parametrize("measured_ink", [False, True])
def test_bold_variable_does_not_join_a_real_condition(measured_ink: bool) -> None:
    glyphs = _glyphs("T for ")
    glyphs[0]["font"] = "CMBX10"
    region = {"glyph_ids": [g["id"] for g in glyphs]}
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in glyphs},
                                          measured_ink=measured_ink)
    assert [c["source_text"] for c in conditions] == ["for"]
    assert conditions[0]["glyph_ids"] == ["2", "3", "4"]
    page = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "formula", "kind": "math",
            "fragments": [region], "formula_conditions": conditions}]}
    assert _undeclared_formula_language(page) == {}
    page["assets"][0]["formula_conditions"] = []
    assert _undeclared_formula_language(page) == {"formula": ["for"]}


@pytest.mark.parametrize("text", ["Then", "Otherwise"])
def test_bold_words_remain_language_even_when_crop_owns_only_part(text: str) -> None:
    glyphs = _glyphs(text, argument=False)
    for glyph in glyphs:
        glyph["font"] = "CMBX10"
    # Classify using the full native word, not just a detector's first-letter crop.
    region = {"glyph_ids": [g["id"] for g in glyphs[:2]]}
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in glyphs})
    assert [c["source_text"] for c in conditions] == [text[:2]]


@pytest.mark.parametrize(("text", "argument"), [
    ("if", True), ("for all", True), ("otherwise.", False),
    ("a.s.", False), ("log of", True), ("logarithm", True), ("loglog", False),
])
def test_language_and_unapplied_composition_still_require_declarations(
    text: str, argument: bool,
) -> None:
    glyphs = _glyphs(text, argument=argument)
    region = {"glyph_ids": [g["id"] for g in glyphs]}
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in glyphs}, measured_ink=False)
    assert [c["source_text"] for c in conditions] == [text]
    page = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "formula", "kind": "math",
            "fragments": [region], "formula_conditions": []}]}
    assert _undeclared_formula_language(page) == {"formula": [text]}
    page["assets"][0]["formula_conditions"] = conditions
    assert _undeclared_formula_language(page) == {}
