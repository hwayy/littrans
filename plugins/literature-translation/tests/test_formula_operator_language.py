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


def _limited_roman_expression(operator: str, operand: str) -> list[dict[str, Any]]:
    glyphs = _glyphs(operator + operand, argument=False)
    # The operand is in the text face too, with TeX's thin math space after the operator.
    for glyph in glyphs[len(operator):]:
        glyph["bbox"][0] += 0.5
        glyph["bbox"][2] += 0.5
        glyph["origin"][0] += 0.5
        glyph["line"] = "l1"
    glyphs.append({"id": "limit", "text": "x", "font": "CMMI8", "size": 8,
                   "bbox": [2.0, 18.0, 6.0, 26.0], "origin": [2.0, 25.0],
                   "baseline": 25.0, "line": "l1"})
    for glyph in glyphs[:-1]:
        glyph["baseline"] = 18.0
    condition = _glyphs("subject to a given cost", argument=False)
    offset = glyphs[-2]["bbox"][2] + 20
    for glyph in condition:
        glyph["id"] = "condition-" + glyph["id"]
        glyph["bbox"][0] += offset
        glyph["bbox"][2] += offset
        glyph["origin"][0] += offset
        glyph["baseline"] = 18.0
        glyph["line"] = "l2"
    return [*glyphs, *condition]


@pytest.mark.parametrize(("operator", "operand"), [("min", "MSE"), ("argmax", "ELBO"), ("sup", "RISK")])
@pytest.mark.parametrize("measured_ink", [False, True])
@pytest.mark.parametrize("limit_above", [False, True])
@pytest.mark.parametrize("native_space", [False, True])
def test_limited_operator_with_roman_operand_stops_before_language(
    operator: str, operand: str, measured_ink: bool, limit_above: bool, native_space: bool,
) -> None:
    glyphs = _limited_roman_expression(operator, operand)
    if native_space:
        x = glyphs[len(operator) - 1]["bbox"][2]
        glyphs.insert(len(operator),
                      {"id": "operator-operand-space", "text": " ", "font": "CMR10", "size": 10,
                       "bbox": [x, 10.0, x + 1.5, 20.0], "baseline": 18.0, "line": "l0"})
    if limit_above:
        limit = next(g for g in glyphs if g["id"] == "limit")
        limit["baseline"] = 11.0
        limit["bbox"] = [2.0, 4.0, 6.0, 12.0]
        limit["origin"] = [2.0, 11.0]
    region = {"glyph_ids": [g["id"] for g in glyphs]}
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in glyphs},
                                          measured_ink=measured_ink)
    assert [c["source_text"] for c in conditions] == ["subject to a given cost"]
    assert conditions[0]["glyph_ids"] == [g["id"] for g in glyphs if g["id"].startswith("condition-")]
    page = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "formula", "kind": "math",
            "fragments": [region], "formula_conditions": conditions}]}
    assert _undeclared_formula_language(page) == {}
    page["assets"][0]["formula_conditions"] = []
    assert _undeclared_formula_language(page) == {"formula": ["subject to a given cost"]}


@pytest.mark.parametrize("change", ["no-limit", "empty-limit", "distant-limit", "normal-size", "same-baseline", "lowercase-operand", "unknown-operator", "text-limit", "vertical-distant-limit", "unowned-limit", "wide-operand"])
def test_roman_words_without_limited_operator_evidence_remain_language(change: str) -> None:
    glyphs = _limited_roman_expression("min" if change != "unknown-operator" else "the",
                                       "MSE" if change != "lowercase-operand" else "cost")
    limit = next(g for g in glyphs if g["id"] == "limit")
    if change == "no-limit":
        glyphs.remove(limit)
    elif change == "empty-limit":
        limit["text"] = " "
    elif change == "distant-limit":
        limit["bbox"] = [100.0, 18.0, 104.0, 26.0]
    elif change == "normal-size":
        limit["size"] = 10
    elif change == "same-baseline":
        limit["baseline"] = 18.0
    elif change == "text-limit":
        limit["font"] = "CMR8"
    elif change == "vertical-distant-limit":
        limit["baseline"] = 40.0
        limit["bbox"] = [2.0, 33.0, 6.0, 41.0]
    elif change == "wide-operand":
        for glyph in glyphs[3:6]:
            glyph["bbox"][0] += 5.0
            glyph["bbox"][2] += 5.0
    region = {"glyph_ids": [g["id"] for g in glyphs]}
    if change == "unowned-limit":
        region["glyph_ids"].remove("limit")
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in glyphs}, measured_ink=False)
    assert any("MSE" in c["source_text"] or "cost" in c["source_text"].split()[0]
               for c in conditions)
    declared = [{"glyph_ids": [g["id"] for g in glyphs if g["id"].startswith("condition-")],
                 "source_text": "subject to a given cost"}]
    page = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "formula", "kind": "math",
            "fragments": [region], "formula_conditions": declared}]}
    assert _undeclared_formula_language(page)


@pytest.mark.parametrize("explicit_narrow_space", [False, True])
def test_limited_operator_does_not_absorb_a_second_capital_word(explicit_narrow_space: bool) -> None:
    glyphs = _limited_roman_expression("min", "MSE")
    condition = [g for g in glyphs if g["id"].startswith("condition-")]
    gap = 1 if explicit_narrow_space else 3
    for index, (glyph, char) in enumerate(zip(condition[:2], "IF", strict=True)):
        glyph["text"] = char
        glyph["bbox"] = [29.5 + gap + 5 * index, 10.0, 33.5 + gap + 5 * index, 20.0]
    glyphs = [g for g in glyphs if not g["id"].startswith("condition-") or g in condition[:2]]
    if explicit_narrow_space:
        glyphs.insert(glyphs.index(condition[0]),
                      {"id": "narrow-space", "text": " ", "font": "CMR10", "size": 10,
                       "bbox": [29.5, 10.0, 30.5, 20.0], "baseline": 18.0, "line": "l2"})
    region = {"glyph_ids": [g["id"] for g in glyphs]}
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in glyphs}, measured_ink=False)
    assert any("IF" in c["source_text"] for c in conditions)


@pytest.mark.parametrize("missing_space", [False, True])
def test_limited_operator_condition_requires_every_native_language_glyph(missing_space: bool) -> None:
    glyphs = _limited_roman_expression("min", "MSE")
    region = {"glyph_ids": [g["id"] for g in glyphs]}
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in glyphs}, measured_ink=False)
    missing = next(g["id"] for g in glyphs if g["id"].startswith("condition-")
                   and g["text"].isspace() == missing_space)
    conditions[0]["glyph_ids"].remove(missing)
    page = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "formula", "kind": "math",
            "fragments": [region], "formula_conditions": conditions}]}
    assert _undeclared_formula_language(page) == {"formula": ["subject to a given cost"]}


@pytest.mark.parametrize("change", ["split-operator", "italic-operator", "italic-operand", "partial-word"])
def test_limited_operator_requires_complete_upright_words(change: str) -> None:
    glyphs = _limited_roman_expression("min", "MSE")
    owned = [g["id"] for g in glyphs]
    if change == "split-operator":
        space = {"id": "operator-space", "text": " ", "font": "CMR10", "size": 10,
                 "bbox": [4.0, 10.0, 5.0, 20.0], "baseline": 18.0, "line": "l0"}
        glyphs.insert(1, space)
        owned.insert(1, space["id"])
    elif change.startswith("italic-"):
        for glyph in glyphs[:3] if change == "italic-operator" else glyphs[3:6]:
            glyph["font"] = "CMTI10"
    else:
        # A crop containing only the capital prefix of a native word must not
        # turn that fragment into an operand. The rest remains in the page ledger.
        suffix = _glyphs("cost", argument=False)
        for glyph in suffix:
            glyph["id"] = "suffix-" + glyph["id"]
            glyph["bbox"][0] += 30.5
            glyph["bbox"][2] += 30.5
            glyph["baseline"] = 18.0
        glyphs[6:6] = suffix
    region = {"glyph_ids": owned}
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in glyphs})
    assert any("MSE" in c["source_text"] for c in conditions)
    page = {"ledger": {"glyphs": glyphs}, "assets": [{"id": "formula", "kind": "math",
            "fragments": [region], "formula_conditions": []}]}
    assert any("MSE" in text for text in _undeclared_formula_language(page)["formula"])
