"""Synthetic contract oracles; these decisions are not real-page visual approval."""
from __future__ import annotations

import copy
from pathlib import Path

import pytest
from test_fidelity_source import approve
from test_fidelity_source import project as source_project
from test_source_structure_v8 import _asset, _line

from littrans import fidelity, layout_review
from littrans.layout_geometry import absorb_formula_row_labels, fraction_components, possessive_ids
from littrans.models import SourceUnit
from littrans.storage import read_json, sha256_file, write_json

project = source_project


def test_inline_math_face_abbreviation_across_native_spans() -> None:
    glyphs = _line("i.i.d. N(0,1)", "CMMI10")
    for i, glyph in enumerate(glyphs):
        glyph["line"] = f"b{i}-l0"
    conditions = fidelity._auto_formula_conditions({"glyph_ids": [g["id"] for g in glyphs]}, {g["id"]: g for g in glyphs})
    assert [c["source_text"] for c in conditions] == ["i.i.d."]


def test_fraction_components_recover_roman_denominator_without_table_rule() -> None:
    above = _line("1", "CMR10", x=20, y=10)
    below = _line("2", "CMR10", line="b1-l0", x=20, y=24)
    glyphs = above + below
    rule = [19., 20., 27., 20.]
    assert fraction_components(glyphs, [rule], {above[0]["id"]}) == [{g["id"] for g in glyphs}]
    assert not fraction_components(glyphs, [[0, 20, 200, 20]], {above[0]["id"]})


def test_possessive_stays_prose_but_derivative_does_not() -> None:
    glyphs = _line("x’s ", ["CMMI10", "CMR10", "CMR10", "CMR10"])
    assert possessive_ids([glyphs]) == {glyphs[1]["id"], glyphs[2]["id"]}
    glyphs[2]["baseline"] += 4
    assert not possessive_ids([glyphs])


def test_multiple_equation_labels_preserve_legacy_serialization() -> None:
    unit = fidelity._make_unit(1, "u", "x=y", [0, 0, 100, 20], {}, kind="equation", equation_number="1")
    assert "equation_numbers" not in unit.model_dump(mode="json")
    multiple = SourceUnit.model_validate({**unit.model_dump(), "equation_numbers": ["1", "2"]})
    from littrans.rendering import _unit_html
    rendered = _unit_html(multiple, None, source_view=True)
    assert rendered.count("(1)") == rendered.count("(2)") == 1
    with pytest.raises(ValueError, match="first"):
        SourceUnit.model_validate({**unit.model_dump(), "equation_numbers": ["2", "3"]})


def issue_page() -> dict:
    return {"layout_concerns": [{"id": "one"}, {"id": "two"}]}


def decision(*ids: str, uncertain: bool = False) -> dict:
    return {"layout_adjudications": [{"concern_ids": list(ids), "choice": "retain",
                                      "reason": "Original geometry supports the recorded boundary.",
                                      "uncertain": uncertain}]}


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "empty-reason", "missing-uncertain", "correction"])
def test_incomplete_or_malformed_adjudication_is_rejected(mutation: str) -> None:
    value = decision("one", "two")
    entry = value["layout_adjudications"][0]
    if mutation == "missing":
        entry["concern_ids"].pop()
    elif mutation == "duplicate":
        entry["concern_ids"].append("one")
    elif mutation == "unknown":
        entry["concern_ids"].append("unknown")
    elif mutation == "empty-reason":
        entry["reason"] = " "
    elif mutation == "missing-uncertain":
        entry.pop("uncertain")
    else:
        entry["choice"] = "correct"
    with pytest.raises(ValueError):
        layout_review.adjudications(issue_page(), value)


def test_uncertain_is_a_decision_and_partial_is_explicit() -> None:
    assert len(layout_review.adjudications(issue_page(), decision("one", "two", uncertain=True))) == 2
    assert len(layout_review.adjudications(issue_page(), decision("one"), partial=True)) == 1


def synthetic_concerns(page: dict) -> list[dict]:
    return [{"id": f"p{page['page']}-{name}", "code": "synthetic-boundary",
             "page": page["page"], "evidence": "Controlled ambiguous boundary"} for name in ("one", "two")]


def test_supplement_is_partial_replayable_and_does_not_approve(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fidelity.prepare_source(project, allow_missing_layout=True)
    assert approve(project)["approved_pages"] == [1]
    monkeypatch.setattr(layout_review, "concerns", synthetic_concerns)
    paths = [project / "derived/units.jsonl", project / "derived/fidelity-assets.jsonl",
             project / "evidence/pages/fidelity-p0001.review.json"]
    before = {p: sha256_file(p) for p in paths}
    packet = fidelity.scan_layout(project, "1-2")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "synthetic-oracle"
    review["pages"] = [review["pages"][0]]
    review["pages"][0].update(decision("p1-one", uncertain=True), viewed_original=True)
    path = project / "supplement.json"
    write_json(path, review)
    result = fidelity.import_source_review(project, path, True)
    assert result["approved_pages"] == []
    assert result["layout_summary"][0]["pending"] == 1
    assert result["layout_summary"][0]["uncertain"] == 1
    assert before == {p: sha256_file(p) for p in paths}
    assert fidelity.import_source_review(project, path, True) == result
    rescanned = fidelity.scan_layout(project, "1")
    remaining = read_json(Path(rescanned["review_template"]))["pages"][0]["context"]
    assert remaining["pending_layout_concern_ids"] == ["p1-two"]
    assert remaining["layout_status"]["adjudicated"] == 1
    assert fidelity.verify_fidelity(project, "1")["passed"]
    assert not fidelity.verify_fidelity(project, "2")["passed"]
    evidence = next((project / "evidence/layout").glob("*.json"))
    payload = read_json(evidence)
    assert len(payload["records"]) == 1
    payload["records"][0]["decision"]["layout_adjudications"][0]["reason"] = "tampered"
    write_json(evidence, payload)
    with pytest.raises(ValueError, match="changed"):
        fidelity.scan_layout(project, "1")


def test_full_review_requires_whole_page_and_each_concern(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fidelity.prepare_source(project, allow_missing_layout=True)
    monkeypatch.setattr(layout_review, "concerns", synthetic_concerns)
    packet = fidelity.build_source_review_packet(project, "1")
    page = read_json(Path(packet["packet_path"]))["pages"][0]
    row = read_json(Path(packet["review_template"]))["pages"][0]
    for key in row:
        if isinstance(row[key], bool):
            row[key] = True
    row.update(decision("p1-one", "p1-two", uncertain=True))
    from fidelity_fixtures import confirm_structure_checks
    saved = copy.deepcopy(row["layout_adjudications"])
    confirm_structure_checks(row)
    row["layout_adjudications"] = saved
    assert not fidelity._source_decision_failures(page, row)
    row["full_page_review_completed"] = False
    assert "full_page_review_completed is not attested" in fidelity._source_decision_failures(page, row)
    html = Path(packet["visual_report"]).read_text(encoding="utf-8")
    assert html.index("<svg") < html.index("<details>")


def test_proposed_rule_cannot_approve_same_page(project: Path) -> None:
    fidelity.prepare_source(project, allow_missing_layout=True)
    packet = fidelity.build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review.update(reviewer="synthetic-oracle", proposed_page_rules=[{"pages": "1", "evidence": "new form"}])
    path = project / "conflict.json"
    write_json(path, review)
    with pytest.raises(ValueError, match="overlap"):
        fidelity.import_source_review(project, path, True)


def test_label_mixed_with_list_item_binds_by_glyph_geometry() -> None:
    asset = _asset("formula", (100, 20, 200, 35))
    assets = {asset.id: asset}
    label = fidelity._make_unit(1, "labels", "(8.32) (i)", [0, 20, 220, 60], assets)
    equation = fidelity._make_unit(1, "equation", "{{asset:formula}}", [100, 20, 200, 35], assets)
    glyphs = _line("(8.32)", "CMR10", y=20)
    result = fidelity._bind_geometric_labels([label, equation], assets, glyphs)
    assert [(u.unit_id, u.equation_number) for u in result] == [("equation", "8.32"), ("labels", None)]
    assert result[1].source_text == "(i)"


def test_equation_label_in_native_line_preserves_neighbouring_prose() -> None:
    label = _line("(8.32)", "CMR10", x=0, y=20)
    prose = _line("Next paragraph", "CMR10", x=100, y=40, line="b1-l0")
    for g in prose:
        g["line"] = label[0]["line"]
    assert fidelity._tag_glyph_ids(label + prose) == {g["id"] for g in label}
    assert not fidelity._tag_glyph_ids(_line("See (8.32) above", "CMR10"))
    arguments = _line("(0)x(0)", "CMR10")
    arguments[3]["bbox"][0] += 15
    assert not fidelity._tag_glyph_ids(arguments)


def test_two_labels_on_one_formula_are_each_retained() -> None:
    assets = {"formula": _asset("formula", (100, 20, 200, 35))}
    label = fidelity._make_unit(1, "labels", "(1.1) (1.2)", [0, 20, 80, 35], assets)
    formula = fidelity._make_unit(1, "equation", "{{asset:formula}}", [100, 20, 200, 35], assets)
    result = fidelity._separate_display_units([label, formula], assets)
    assert len(result) == 1 and result[0].equation_numbers == ["1.1", "1.2"]


def test_consecutive_container_keeps_sentence_flags_and_stops_at_qed() -> None:
    parent = fidelity._make_unit(1, "theorem", "Theorem 1.", [0, 0, 100, 10], {}, parent_id="theorem")
    tail = fidelity._make_unit(1, "item2", "(ii) Second", [0, 30, 100, 40], {}, parent_id="theorem")
    current = fidelity._make_unit(2, "item3", "(iii) Third", [0, 0, 100, 10], {}, parent_id="item3")
    result = fidelity._continue_numbered_container([parent, tail], [current], {})
    assert result[0].parent_id == "theorem"
    assert not result[0].continues_from_previous
    tail.source_text += " □"
    assert fidelity._continue_numbered_container([parent, tail], [current], {})[0].parent_id == "item3"


def test_adjudications_do_not_cover_unlisted_defects(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fidelity.prepare_source(project, allow_missing_layout=True)
    monkeypatch.setattr(layout_review, "concerns", synthetic_concerns)
    packet = fidelity.build_source_review_packet(project, "1")
    page = read_json(Path(packet["packet_path"]))["pages"][0]
    row = read_json(Path(packet["review_template"]))["pages"][0]
    from fidelity_fixtures import confirm_structure_checks
    confirm_structure_checks(row)
    for key, value in row.items():
        if isinstance(value, bool):
            row[key] = True
    row["issues"] = ["Synthetic original contains another unlisted missing caption."]
    assert "issues are not empty" in fidelity._source_decision_failures(page, row)


def test_newly_changed_context_rejects_supplement_before_writing(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fidelity.prepare_source(project, allow_missing_layout=True)
    monkeypatch.setattr(fidelity, "_cross_page_container_candidates", lambda units, page: [
        {"unit_id": "u", "candidate_parent_id": "v", "context_page": 2}] if page == 1 else [])
    packet = fidelity.scan_layout(project, "1")
    review = read_json(Path(packet["review_template"]))
    row = review["pages"][0]
    ids = [item["id"] for item in row["context"]["layout_concerns"]]
    row.update(decision(*ids), viewed_original=True)
    review["reviewer"] = "synthetic-oracle"
    path = project / "review.json"
    write_json(path, review)
    ledger_path = project / "derived/fidelity-pages/p0002.json"
    ledger = read_json(ledger_path)
    ledger["layout_reason"] = "Changed context evidence"
    write_json(ledger_path, ledger)
    with pytest.raises(ValueError, match="context changed"):
        fidelity.import_source_review(project, path, True)
    assert not list((project / "evidence/layout").glob("*.json"))


def test_closed_proof_groups_paragraphs_without_joining_sentences() -> None:
    units = [fidelity._make_unit(1, str(i), text, [0, i*20, 100, i*20+10], {}, parent_id=str(i))
             for i, text in enumerate(["Proof. First claim.", "Next independent sentence.", "Done. □", "Outside."])]
    result = fidelity._complete_closed_proofs(copy.deepcopy(units), {})
    assert [u.parent_id for u in result] == ["0", "0", "0", "3"]
    assert not any(u.continues_from_previous for u in result)
    units[2].source_text = "Theorem 2. A new statement. □"
    assert fidelity._complete_closed_proofs(units, {})[1].parent_id == "1"


def test_formula_row_labels_need_aligned_consecutive_mathematical_rows() -> None:
    one = _line("(i)x=y", "CMR10", x=30, y=20)
    two = _line("(ii)y=z", "CMR10", x=30, y=40, line="b1-l0")
    regions = [{"kind": "math", "display": True, "bbox": [48, 20, 70, 30],
                "glyph_ids": [g["id"] for g in one[3:]], "provenance": []},
               {"kind": "math", "display": True, "bbox": [30, 40, 80, 50],
                "glyph_ids": [g["id"] for g in two], "provenance": []}]
    isolated = copy.deepcopy(regions[:1])
    absorb_formula_row_labels(isolated, one)
    assert one[0]["id"] not in isolated[0]["glyph_ids"]
    absorb_formula_row_labels(regions, one + two)
    assert set(regions[0]["glyph_ids"]) == {g["id"] for g in one}


def test_open_upright_proof_break_is_one_explicit_concern() -> None:
    units = [fidelity._make_unit(1, str(i), text, [0, i*20, 100, i*20+10], {}, parent_id=str(i))
             for i, text in enumerate(["Proof. We show the claim.", "First consider the operator.", "Next paragraph."])]
    page = {"page": 1, "units": [u.model_dump(mode="json") for u in units],
            "assets": [], "ledger": {"glyphs": []}}
    issues = layout_review.concerns(page)
    assert len(issues) == 1 and issues[0]["code"] == "same-page-container-candidate"
