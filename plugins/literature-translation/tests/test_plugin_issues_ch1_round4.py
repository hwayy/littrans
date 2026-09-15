"""Regressions for PLUGIN-ISSUES.md LT-019..031 (chapter-1 ledger, round 4)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pymupdf as fitz
import pytest
from test_fidelity_source import approve
from test_fidelity_source import project as fidelity_project  # noqa: F401
from test_source_structure_v8 import _line, _owned_text, _Page

from littrans import fidelity
from littrans.fidelity import (
    _auto_formula_conditions,
    _display_box_owned,
    _formula_condition_glyphs,
    _make_unit,
    _regions,
    _separate_display_units,
    _strip_display_prose,
    build_source_review_packet,
    import_source_review,
    page_review_findings,
    prepare_source,
    verify_fidelity,
)
from littrans.fidelity_models import FidelityAsset, FidelityFragment, load_assets
from littrans.project import ProjectConfig, initialize_project_dirs, save_project
from littrans.source_render import render_source_review
from littrans.source_structure import EQUATION_LABEL, language_words
from littrans.storage import read_json, sha256_file, write_json

project = fidelity_project


def _texts(regions: list[dict[str, Any]], glyphs: list[dict[str, Any]]) -> list[str]:
    return [t.strip() for t in _owned_text(regions, glyphs)]


# --- LT-024: operator names before an argument join the inline run -----------------------

def test_operator_name_flush_against_its_argument_joins_the_formula() -> None:
    line = _line("so Cov(X, Y ) = 0 holds", ["CMR10"] * 7 + ["CMMI10"] + ["CMR10"] * 2 + ["CMMI10"] + ["CMR10"] * 12)
    assert _texts(_regions(_Page(), line, []), line) == ["Cov(X, Y ) = 0"]
    line = _line("the mean(X) is", ["CMR10"] * 9 + ["CMMI10"] + ["CMR10"] * 4)
    assert _texts(_regions(_Page(), line, []), line) == ["mean(X)"]
    # A prose word before a space is not an operator prefix.
    line = _line("the pair (X, Y )", ["CMR10"] * 10 + ["CMMI10"] + ["CMR10"] * 2 + ["CMMI10"] + ["CMR10"] * 2)
    assert _texts(_regions(_Page(), line, []), line) == ["(X, Y )"]


# --- LT-025: prose brackets are trimmed by balance ----------------------------------------

def test_unbalanced_prose_bracket_after_a_formula_is_not_cropped() -> None:
    line = _line("(the space L(Ω)) and", ["CMR10"] * 11 + ["CMMI10", "CMR10", "CMR10", "CMR10", "CMR10"] + ["CMR10"] * 4)
    assert _texts(_regions(_Page(), line, []), line) == ["L(Ω)"]
    # Intervals are balanced across bracket kinds and keep both delimiters.
    line = _line("x ∈ [0, 1) and", ["CMMI10", "CMR10", "CMSY10"] + ["CMR10"] * 11)
    assert _texts(_regions(_Page(), line, []), line) == ["x ∈ [0, 1)"]


# --- LT-026: inline runs continue across a line break after a relation -------------------

def test_inline_formula_broken_after_a_relation_is_one_two_fragment_asset() -> None:
    first = _line("so f(λ) >", ["CMR10"] * 3 + ["CMMI10", "CMR10", "CMMI10", "CMR10", "CMR10", "CMMI10"], line="b0-l0", y=20)
    second = _line("0 holds.", "CMR10", line="b0-l1", y=34)
    regions = [r for r in _regions(_Page(), first + second, []) if r.get("glyph_ids")]
    assert len(regions) == 1
    joined = regions[0]
    assert "line-break-continued" in joined["provenance"] and not joined["display"]
    assert [len(f["glyph_ids"]) for f in joined["fragments"]] == [6, 1]
    assert _texts(regions, first + second) == ["f(λ) >0"]
    # Without a relation at the line end the digit opening the next line stays prose.
    first = _line("so f(λ)", ["CMR10"] * 3 + ["CMMI10", "CMR10", "CMMI10", "CMR10"], line="b0-l0", y=20)
    assert _texts(_regions(_Page(), first + second, []), first + second) == ["f(λ)"]


# --- LT-022: display boxes keep denominators and case rows -------------------------------

def test_phrase_inside_the_formula_extent_is_not_stripped_as_set_off_prose() -> None:
    head = _line("ρ(x) =", ["CMMI10", "CMR10", "CMMI10", "CMR10", "CMR10", "CMR10"], line="b0-l0", y=30, x=100)
    denominator = _line("area(B)", ["CMR8"] * 5 + ["CMMI8", "CMR8"], line="b1-l2", y=30, x=180)
    prose_ids = {g["id"] for g in denominator[:4]}
    line = head + denominator
    assert _strip_display_prose(line, prose_ids) == head
    other_row = _line("1, if x ∈ B,", ["CMR8", "CMMI10", "CMR10", "CMR10", "CMR10", "CMR10", "CMMI10", "CMR10", "CMSY10", "CMR10", "CMMI10", "CMMI10"], line="b1-l1", y=18, x=175)
    assert _strip_display_prose(line, prose_ids, [g for g in other_row if g["font"] != "CMR10"]) == line


def test_rows_bracketed_by_an_owned_delimiter_stay_in_the_formula() -> None:
    brace = [{"id": "brace", "text": "\x05", "font": "CMEX10", "bbox": [150.0, 10.0, 158.0, 52.0], "line": "b1-l0", "size": 10.0, "baseline": 40.0, "origin": [150.0, 40.0]}]
    head = _line("ρ(x) =", ["CMMI10", "CMR10", "CMMI10", "CMR10", "CMR10", "CMR10"], line="b0-l0", y=25, x=100)
    row1 = _line("1, if x ∈ B,", ["CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMMI10", "CMR10", "CMSY10", "CMR10", "CMMI10", "CMR10"], line="b1-l1", y=14, x=165)
    row2 = _line("0, otherwise.", "CMR10", line="b2-l0", y=38, x=165)
    prose = {g["id"] for g in row1[3:5]} | {g["id"] for g in row2 if g["text"].isalpha()}
    kept, outside, displayed = _display_box_owned(brace + head + row1 + row2, prose, margin=0.0)
    assert not outside and not displayed
    assert len(kept) == len(brace + head + row1 + row2)


# --- LT-023: language tokens include letter-dot abbreviations; inline conditions ----------

def test_letter_dot_abbreviations_are_language_and_may_be_declared_inline() -> None:
    assert language_words("P(A i.o.) a.s., i.e. limsup") == ["i.o.", "a.s.", "i.e."]
    line = _line("P(An i.o.)", ["MSBM10", "CMR10", "CMMI10", "CMMI7", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10", "CMR10"])
    region = {"glyph_ids": [g["id"] for g in line]}
    conditions = _auto_formula_conditions(region, {g["id"]: g for g in line})
    assert [c["source_text"] for c in conditions] == ["i.o."]
    asset = {"kind": "math", "display": False, "fragments": [{"glyph_ids": [g["id"] for g in line]}], "formula_conditions": conditions}
    assert _formula_condition_glyphs(asset, line) == set(conditions[0]["glyph_ids"])
    digest = "a" * 64
    fragment = FidelityFragment(page=1, bbox=(0, 0, 60, 10), png_path="x.png", svg_path="x.svg", width=60, height=10,
                                glyph_ids=[g["id"] for g in line], file_sha256={"x.png": digest, "x.svg": digest})
    inline = FidelityAsset(id="io", kind="math", source_sha256=digest, content_sha256=digest, fragments=[fragment],
                           display=False, formula_conditions=conditions)
    assert inline.formula_conditions[0].source_text == "i.o."
    # An operator name applied to its argument is notation, not a condition.
    line = _line("limsup An", ["CMR10"] * 6 + ["CMR10", "CMMI10", "CMMI7"])
    assert _auto_formula_conditions({"glyph_ids": [g["id"] for g in line]}, {g["id"]: g for g in line}) == []


# --- LT-021: equation labels sharing a block with a tombstone bind -----------------------

def _display_assets() -> dict[str, FidelityAsset]:
    digest = "b" * 64
    fragment = FidelityFragment(page=1, bbox=(120, 100, 300, 140), png_path="d.png", svg_path="d.svg", width=180, height=40,
                                file_sha256={"d.png": digest, "d.svg": digest})
    return {"D": FidelityAsset(id="D", kind="math", source_sha256=digest, content_sha256=digest, fragments=[fragment], display=True)}


def test_label_beside_a_tombstone_binds_and_the_tombstone_survives() -> None:
    assets = _display_assets()
    units = [_make_unit(1, "p0001-b24", "Then", [58, 80, 400, 96], assets),
             _make_unit(1, "p0001-b25", "□ (1.50)", [58, 112, 417, 126], assets),
             _make_unit(1, "p0001-b26", "{{asset:D}}", [120, 100, 300, 140], assets, kind="equation")]
    split = _separate_display_units(units, assets)
    equation = next(u for u in split if u.kind.value == "equation")
    assert equation.equation_number == "1.50"
    assert [u.source_text for u in split if u.unit_id == "p0001-b25"] == ["□"]
    import re
    assert re.match(r"[*\s]*" + EQUATION_LABEL, "(1.50)") and re.match(r"[*\s]*" + EQUATION_LABEL, "(A.4)")


# --- LT-019: the build identity never enters a fingerprint; reruns keep receipts ---------

def test_rerun_with_identical_content_keeps_page_fingerprint_packet_and_receipt(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from littrans import build_info

    monkeypatch.setattr(build_info, "utc_now", lambda: "2026-09-15T00:00:00+00:00")
    first = prepare_source(project, "1", allow_missing_layout=True)
    ledger = read_json(project / "derived/fidelity-pages/p0001.json")
    approve(project)
    assert verify_fidelity(project, "1")["passed"]
    monkeypatch.setattr(build_info, "utc_now", lambda: "2026-09-16T00:00:00+00:00")
    second = prepare_source(project, "1", replace=True, allow_missing_layout=True)
    rerun = read_json(project / "derived/fidelity-pages/p0001.json")
    assert rerun["generator"]["generated_at"] != ledger["generator"]["generated_at"]
    assert rerun["fingerprint"] == ledger["fingerprint"]
    assert second["review_packet"] == first["review_packet"]
    assert second["retained_receipt_pages"] == [1] and second["invalidated_pages"] == []
    assert verify_fidelity(project, "1")["passed"]
    packet = read_json(Path(second["review_packet"]))
    assert "generator" not in packet["pages"][0]["ledger"] and packet["generator"]["plugin_version"]


# --- LT-027: recorded overrides are replayed, not silently dropped -----------------------

def _override_page_one(project: Path, region: dict[str, Any]) -> dict[str, Any]:
    packet = build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "corrector"
    # A review may cover a subset of the packet's pages; dependency pages stay outside it.
    review["pages"] = [page for page in review["pages"] if page["page"] == 1]
    review["pages"][0]["override"] = {"regions": [region]}
    write_json(project / "override.json", review)
    return import_source_review(project, project / "override.json", True)


def test_replace_replays_a_recorded_override_unless_discarded(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    result = _override_page_one(project, {"id": "kept-rule", "kind": "mixed-region", "bbox": [95, 95, 155, 105], "grouping_pending": False})
    assert result["changed_pages"] == [1]
    ledger = read_json(project / "derived/fidelity-pages/p0001.json")
    assert ledger["source_overrides_origin"]["reviewer"] == "corrector" and ledger["source_overrides_origin"]["packet_id"].startswith("source-")
    replayed = prepare_source(project, "1", replace=True, allow_missing_layout=True)
    assert replayed["replayed_override_pages"] == [1] and replayed["discarded_override_pages"] == []
    assert "kept-rule" in load_assets(project)
    rerun = read_json(project / "derived/fidelity-pages/p0001.json")
    assert rerun["source_overrides"] == ledger["source_overrides"] and rerun["source_overrides_origin"] == ledger["source_overrides_origin"]
    discarded = prepare_source(project, "1", replace=True, allow_missing_layout=True, discard_overrides=True)
    assert discarded["discarded_override_pages"] == [1] and discarded["replayed_override_pages"] == []
    assert "kept-rule" not in load_assets(project)
    assert read_json(project / "derived/fidelity-pages/p0001.json")["source_overrides"] is None


# --- LT-028: dependency pages whose fingerprint moved are reported ------------------------

@pytest.fixture
def continued_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.draw_line((100, 30), (150, 50))
        page.insert_text((60, 80), "Let x = 1 and note that the")
        page = doc.new_page()
        page.insert_text((60, 80), "result holds on the next page.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="t", title="t", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=2, profile="technical-book"))
    return tmp_path


def test_override_import_names_the_dependency_pages_it_invalidates(continued_project: Path) -> None:
    prepare_source(continued_project, "1-2", allow_missing_layout=True)
    from littrans.evidence import page_evidence_units
    from littrans.models import SourceUnit
    from littrans.storage import read_jsonl
    units = read_jsonl(continued_project / "derived/units.jsonl", SourceUnit)
    assert {u.page for u in page_evidence_units(2, units)} == {1, 2}
    pending = [a.id for a in load_assets(continued_project).values() if a.grouping_pending]
    packet = build_source_review_packet(continued_project, "1-2")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "r"
    for page in review["pages"]:
        for key in ("viewed_original", "coverage_complete", "boundaries_complete", "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
            page[key] = True
        page["accepted_grouping_pending"] = [{"asset_id": aid, "reason": "diagonal rule kept as original evidence"} for aid in pending]
    write_json(continued_project / "review.json", review)
    assert import_source_review(continued_project, continued_project / "review.json", True)["approved_pages"] == [1, 2]
    assert verify_fidelity(continued_project, "1-2")["passed"]
    result = _override_page_one(continued_project, {"id": "rule", "kind": "mixed-region", "bbox": [95, 25, 155, 55], "grouping_pending": False})
    assert result["changed_pages"] == [1] and result["invalidated_pages"] == [2] and result["requires_new_packet"]
    assert not (continued_project / "evidence/pages/fidelity-p0002.review.json").is_file()


# --- LT-029/LT-031: the gate asks the ledger ---------------------------------------------

def test_pending_grouping_blocks_approval_until_accepted_with_a_reason(continued_project: Path) -> None:
    prepare_source(continued_project, "1", allow_missing_layout=True)
    pending = [a.id for a in load_assets(continued_project).values() if a.grouping_pending]
    assert pending
    packet = build_source_review_packet(continued_project, "1")
    template = read_json(Path(packet["review_template"]))["pages"][0]
    assert template["context"]["grouping_pending"] == pending
    assert [f["code"] for f in template["context"]["findings"]] == ["grouping-pending"]
    assert template["accepted_grouping_pending"] == []
    result = approve(continued_project)
    assert result["approved_pages"] == [] and result["rejected_pages"][1] == ["grouping-pending: " + ", ".join(pending)]
    receipt = read_json(continued_project / "evidence/pages/fidelity-p0001.review.json")
    assert receipt["passed"] is False and receipt["failures"] == result["rejected_pages"][1]
    html = Path(render_source_review(continued_project, "1")["html"]).read_text(encoding="utf-8")
    assert "pending grouping decision" in html
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "r"
    page = review["pages"][0]
    for key in ("viewed_original", "coverage_complete", "boundaries_complete", "reading_order_correct", "grouping_checked", "layout_fallback_checked"):
        page[key] = True
    page["accepted_grouping_pending"] = [{"asset_id": pending[0], "reason": ""}]
    write_json(continued_project / "review.json", review)
    assert import_source_review(continued_project, continued_project / "review.json", True)["approved_pages"] == []
    page["accepted_grouping_pending"] = [{"asset_id": aid, "reason": "diagonal rule is original evidence"} for aid in pending]
    write_json(continued_project / "review.json", review)
    assert import_source_review(continued_project, continued_project / "review.json", True)["approved_pages"] == [1]
    assert verify_fidelity(continued_project, "1")["passed"]
    html = Path(render_source_review(continued_project, "1")["html"]).read_text(encoding="utf-8")
    assert "accepted by review" in html and "asset(s) with a pending grouping decision" not in html


def test_template_lists_declared_conditions_and_undeclared_language_blocks(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    _override_page_one(project, {"id": "words", "kind": "math", "bbox": [58, 68, 90, 86]})
    asset = load_assets(project)["words"]
    assert [c.source_text for c in asset.formula_conditions] == ["Let x"]
    packet = build_source_review_packet(project, "1")
    template = read_json(Path(packet["review_template"]))["pages"][0]
    assert template["context"]["formula_conditions"] == [{"asset_id": "words", "source_text": "Let x", "bbox": list(asset.fragments[0].bbox), "display": False}]
    assert approve(project, "1", "formula_conditions_checked")["approved_pages"] == [1]
    # An explicit empty declaration on a crop that holds words is refused by the ledger.
    _override_page_one(project, {"id": "silent", "kind": "math", "bbox": [58, 68, 90, 86], "formula_conditions": []})
    assert not load_assets(project)["silent"].formula_conditions
    page = read_json(Path(build_source_review_packet(project, "1")["packet_path"]))["pages"][0]
    assert page_review_findings(page)[0]["code"] == "undeclared-formula-language"
    assert approve(project, "1", "formula_conditions_checked")["rejected_pages"] == {1: ["undeclared-formula-language: silent"]}


# --- LT-030: a missing packet is named as a live dependency -------------------------------

def test_missing_packet_is_reported_as_a_live_review_dependency(project: Path) -> None:
    prepare_source(project, "1", allow_missing_layout=True)
    approve(project)
    verified = verify_fidelity(project, "1")
    assert verified["passed"] and list(verified["receipt_packets"].values()) == [[1]]
    packet_id = next(iter(verified["receipt_packets"]))
    from littrans.fidelity import gc_asset_directories
    assert gc_asset_directories(project)["live_source_packets"] == [packet_id]
    (project / "packets" / packet_id).rename(project / "packets" / "moved")
    error = verify_fidelity(project, "1")["errors"][0]["message"]
    assert packet_id in error and "live dependency" in error


# --- LT-020: doctor names the installed build ------------------------------------------

def test_doctor_reports_the_build_identity() -> None:
    import json

    from typer.testing import CliRunner

    from littrans import __version__
    from littrans.cli import app

    result = CliRunner().invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    build = json.loads(result.output)["build"]
    assert build["plugin_version"] == __version__ and len(build["build_digest"]) == 16 and "generated_at" not in build
