"""Regressions recorded while translating chapter 1 of a TeX-set mathematics book.

Terminology gates must survive TeX accent/quote extraction artifacts, glossary status
must be honoured, and line-end hyphens must not delete real compounds.
"""
from __future__ import annotations

from pathlib import Path

import pymupdf as fitz
import pytest
from test_efficiency_v4 import _make_project, _submit

from littrans.evidence import fold_term_text, relevant_terms, term_matches, term_source_text
from littrans.fidelity import _rejoin_line_breaks, prepare_source
from littrans.models import ProjectConfig, SourceUnit
from littrans.project import load_terms
from littrans.quality import run_qa
from littrans.storage import (
    initialize_project_dirs,
    read_jsonl,
    save_project,
    sha256_file,
    write_yaml,
)


@pytest.mark.parametrize("glossary, extracted", [
    ("Hölder inequality", "H¨older inequality"),
    ("Lévy's continuity theorem", "L´evy’s continuity theorem"),
    ("Chebyshev's inequality", "Chebyshev’s inequality"),
    ("Itô", "Itˆo"),
    ("fine", "ﬁne"),
    ("Lévy", "Lévy"),
    ("random  variable", "random\nvariable"),
    ("non-linear", "non–linear"),
])
def test_fold_term_text_equates_tex_extraction_variants(glossary: str, extracted: str) -> None:
    assert fold_term_text(glossary) == fold_term_text(extracted)
    assert term_matches({"source": glossary}, fold_term_text("... " + extracted + " ..."))


def test_term_match_modes() -> None:
    folded = fold_term_text("A measured function; the partition function Z means much.")
    assert term_matches({"source": "measure"}, folded)
    assert not term_matches({"source": "measure", "match": "word"}, folded)
    assert term_matches({"source": "partition function", "match": "word"}, folded)
    # Word boundaries do not separate senses; a regex can exclude the other compound.
    assert term_matches({"source": "partition", "match": "word"}, fold_term_text("partition function"))
    assert not term_matches({"source": r"\bpartition\b(?! function)", "match": "regex"}, fold_term_text("partition function"))
    assert term_matches({"source": r"\bpartition\b(?! function)", "match": "regex"}, fold_term_text("a partition of Ω"))
    assert term_matches({"source": r"\bmean(s)?\b", "match": "regex"}, folded)
    assert not term_matches({"source": r"^measure\b", "match": "regex"}, folded)
    assert not term_matches({"source": ""}, folded)


def test_load_terms_filters_status_and_validates_match(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "glossary").mkdir(parents=True)
    write_yaml(root / "glossary" / "approved.yaml", {"terms": [
        {"source": "measure", "target": "测度"},
        {"source": "mean", "target": "均值", "status": "approved"},
        {"source": "moment", "target": "矩", "status": "proposed"},
        {"source": "partition", "target": "划分", "status": "reference-only"},
        "not a mapping",
    ]})
    assert [t["source"] for t in load_terms(root)] == ["measure", "mean"]
    assert [t["source"] for t in load_terms(root, enforced_only=False)] == ["measure", "mean", "moment", "partition"]
    write_yaml(root / "glossary" / "approved.yaml", {"terms": [{"source": "x", "target": "y", "match": "fuzzy"}]})
    with pytest.raises(ValueError, match="unknown match mode"):
        load_terms(root)
    write_yaml(root / "glossary" / "approved.yaml", {"terms": [{"source": "(unclosed", "target": "y", "match": "regex"}]})
    with pytest.raises(ValueError, match="regular expression"):
        load_terms(root)


def test_qa_and_packets_share_term_source_and_report_unmatched_terms(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    batch_id = manifests[0].batch_id
    units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
    unit = units[0]
    assert "architecture" in unit.source_text
    quoted = "framework"
    assert f'"{quoted}' not in unit.source_text
    write_yaml(root / "glossary" / "approved.yaml", {"terms": [
        {"source": "Architecture", "target": "架构"},
        {"source": "H¨older", "target": "赫尔德"},
        {"source": "layout", "target": "布局", "status": "proposed"},
    ]})
    assert [t["source"] for t in relevant_terms(root, units)] == ["Architecture"]
    _submit(root, batch_id, target_text="架构 已经翻译")
    report = run_qa(root, batch_id)
    assert not any(item.code == "approved-term-missing" for item in report.errors)
    unmatched = [item for item in report.warnings if item.code == "approved-term-never-matched"]
    assert [item.message.rsplit(": ", 1)[1] for item in unmatched] == ["H¨older"]
    # A term that only occurs inside a quoted title is neither injected nor enforced.
    titled = unit.model_copy(update={"source_text": 'See the book “Binding Theory” for details.', "source_markdown": None})
    write_yaml(root / "glossary" / "approved.yaml", {"terms": [{"source": "binding theory", "target": "绑定理论"}]})
    assert relevant_terms(root, [titled]) == []
    assert not term_matches({"source": "binding theory"}, term_source_text(titled))


def test_rejoin_line_breaks_uses_document_evidence() -> None:
    from collections import Counter

    evidence = (Counter({"well-known": 3, "non-linear": 1}), Counter({"probability": 5, "nonlinear": 2}))
    assert _rejoin_line_breaks("The most well-\nknown proba-\nbility", evidence) == "The most well-known probability"
    # Ambiguous compounds fall back to the plain join, as before.
    assert _rejoin_line_breaks("a non-\nlinear map", evidence) == "a nonlinear map"
    # Only a line end is a soft break; a suspended hyphen mid-line stays.
    assert _rejoin_line_breaks("pre- and post-processing", evidence) == "pre- and post-processing"
    # A capitalised second half is a name compound: the hyphen stays and the seam closes.
    assert _rejoin_line_breaks("Borel-\nCantelli", evidence) == "Borel-Cantelli"
    assert _rejoin_line_breaks("*well-\nknown*", evidence) == "*well-known*"


def test_prepared_prose_keeps_compounds_printed_elsewhere(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.fidelity as fidelity

    monkeypatch.setattr(fidelity, "detect_layout", lambda images, output: {"status": "unavailable", "reason": "isolated test", "pages": {}})
    initialize_project_dirs(tmp_path)
    pdf = tmp_path / "source/book.pdf"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "The most well-\nknown example of proba-\nbility uses pre- and post-processing.")
        page = doc.new_page()
        page.insert_text((60, 80), "A well-known fact about probability.")
        doc.save(pdf)
    save_project(tmp_path, ProjectConfig(project_id="t", title="t", source_path="source/book.pdf", source_sha256=sha256_file(pdf), source_pages=2, profile="technical-book"))
    prepare_source(tmp_path, allow_missing_layout=True)
    texts = [u.source_text for u in read_jsonl(tmp_path / "derived/units.jsonl", SourceUnit) if u.page == 1]
    joined = " ".join(texts)
    assert "well-known example" in joined
    assert "probability uses" in joined
    assert "pre- and post-processing" in joined


def test_unmeasured_glyph_ink_is_recorded_in_region_provenance(monkeypatch: pytest.MonkeyPatch) -> None:
    import littrans.glyph_export as glyph_export
    from littrans.fidelity import _native, _regions

    monkeypatch.setattr(glyph_export, "glyph_ink_boxes", lambda page, glyphs: {})
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((60, 80), "Let x = 1 hold.")
        glyphs, _ = _native(page)
        regions = _regions(page, glyphs, [])
    math = [r for r in regions if r.get("glyph_ids")]
    assert math
    assert all("ink-bounds-unmeasured" in r["provenance"] for r in math)
