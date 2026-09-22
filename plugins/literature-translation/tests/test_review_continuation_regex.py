"""Regressions for continuation annotations and syntax-aware glossary folding."""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf as fitz
import pytest
from test_efficiency_v4 import _make_project, _submit

from littrans.evidence import fold_regex_pattern, fold_term_text, relevant_terms, term_matches
from littrans.fidelity_models import FidelityAsset, FidelityFragment
from littrans.models import AssetTranslation, ReaderNote, SourceUnit, TranslationRecord
from littrans.project import initialize_project, load_terms
from littrans.quality import run_qa
from littrans.rendering import render_project
from littrans.storage import read_jsonl, sha256_file, write_jsonl, write_yaml
from littrans.workflow import create_workflow_packet


@pytest.mark.parametrize("pattern, text", [
    (r"(?P<Term>Hölder)\s+(?P=Term)", "H¨older Hölder"),
    (r"(?P<A>Hölder) (?P<a>Lévy) (?P=A) (?P=a)", "Holder Levy Holder Levy"),
    (r"(?P<Term>Hölder)?(?(Term) inequality|Lévy)", "Holder inequality"),
    (r"(?P<Term>Hölder)?(?(Term) inequality|Lévy)", "Levy"),
    (r"(?i:HÖLDER)(?-i: Lévy)", "holder levy"),
    (r"(?# Keep (?P<Term> intact)Hölder", "Holder"),
    ("(?x)Hölder # Keep THIS comment\n \\s+ Lévy", "Holder Levy"),
    ("(?x:Hölder # comment\n) Lévy", "Holder Levy"),
    (r"[Hh]ölder\b", "Holder"),
    (r"[A-Z]+", "Holder"),
    (r"[–]", "-"),
    (r"[［］＾]", "["),
    (r"[¨^a]", "^"),
    (r"[]a]", "]"),
    (r"a{２}", "a{2}"),
    (r"［Hölder］＋", "[Holder]+"),
    (r"\N{LATIN CAPITAL LETTER H}ölder", "Holder"),
    (r"\x48ölder\s+\u004cevy", "Holder Levy"),
    (r"\U00000048ölder", "Holder"),
    (r"(Hölder)\s+\1", "Holder Holder"),
])
def test_regex_syntax_survives_literal_folding(pattern: str, text: str) -> None:
    assert re.fullmatch(fold_regex_pattern(pattern), fold_term_text(text), re.I)


def test_regex_identifiers_and_comments_are_preserved_verbatim() -> None:
    pattern = r"(?P<Term>Hölder)(?# Keep THIS)(?P=Term)"
    assert fold_regex_pattern(pattern) == r"(?P<Term>holder)(?# Keep THIS)(?P=Term)"
    assert fold_regex_pattern(r"\N{LATIN CAPITAL LETTER H}") == r"\N{LATIN CAPITAL LETTER H}"


@pytest.mark.parametrize("pattern", [r"(?p<Term>Holder)", r"(?P<Term>Holder)(?P=term)", "[z-a]", "("])
def test_invalid_regex_is_not_repaired_by_folding(tmp_path: Path, pattern: str) -> None:
    write_yaml(tmp_path / "glossary/approved.yaml", {"terms": [{"source": pattern, "match": "regex"}]})
    with pytest.raises(ValueError, match="regular expression"):
        load_terms(tmp_path)


def test_folded_character_class_does_not_create_a_range_or_negation() -> None:
    assert not term_matches({"source": "[a–z]", "match": "regex"}, "m")
    assert not term_matches({"source": "[＾a]", "match": "regex"}, "z")
    assert not term_matches({"source": "[¨^a]", "match": "regex"}, "z")
    assert term_matches({"source": "[a–z]", "match": "regex"}, "-")


def test_named_regex_terms_load_reach_packets_and_gate_qa(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, 1)
    term = {"source": "unused", "aliases": [r"(?P<Term>Architecture)"], "match": "regex", "target": "架构"}
    write_yaml(root / "glossary/approved.yaml", {"terms": [term]})
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    assert load_terms(root) == relevant_terms(root, units) == [term]
    batch_id = manifests[0].batch_id
    packet = create_workflow_packet(root, "translate", [batch_id])
    assert not isinstance(packet, list)
    assert term["aliases"][0] in (root / packet.files["shared"]).read_text(encoding="utf-8")
    _submit(root, batch_id, target_text="已经翻译")
    assert any(item.code == "approved-term-missing" for item in run_qa(root, batch_id).errors)
    _submit(root, batch_id, target_text="架构 已经翻译")
    assert not any(item.code == "approved-term-missing" for item in run_qa(root, batch_id).errors)


@pytest.fixture
def annotation_project(tmp_path: Path) -> Path:
    pdf = tmp_path / "book.pdf"
    with fitz.open() as document:
        for _ in range(4):
            document.new_page(width=100, height=60).insert_text((10, 30), "x if positive")
        document.save(pdf)
    config = initialize_project(pdf, tmp_path / "project", "technical-book")
    root = tmp_path / "project"
    assets = []
    with fitz.open(pdf) as document:
        for page in range(1, 5):
            name = f"derived/a{page}.png"
            svg = f"derived/a{page}.svg"
            document[page - 1].get_pixmap().save(root / name)
            (root / svg).write_text(document[page - 1].get_svg_image(), encoding="utf-8")
            assets.append(FidelityAsset(
                id=f"a{page}", kind="math", source_sha256=config.source_sha256,
                content_sha256=sha256_file(root / name),
                fragments=[FidelityFragment(page=page, bbox=(0, 0, 100, 60), width=100, height=60,
                                            png_path=name, svg_path=svg,
                                            file_sha256={n: sha256_file(root / n) for n in (name, svg)})],
            ))
    write_jsonl(root / "derived/fidelity-assets.jsonl", assets)
    return root


def _render_chain(root: Path, pages: list[int], flags: list[tuple[bool, bool]], *, closed: bool = False) -> str:
    units, records = [], []
    for index, (page, (receiver, sender)) in enumerate(zip(pages, flags, strict=True)):
        aid, uid = f"a{page}", f"p{page:04d}-b0"
        text = "Consider {{asset:" + aid + "}}" + ("." if closed or index == len(pages) - 1 else " and")
        unit = SourceUnit(unit_id=uid, page=page, kind="paragraph", bbox=(0, 0, 100, 60),
                          source_text=text, source_hash=f"source-{page}", confidence=1,
                          continues_from_previous=receiver, continued_to_next=sender)
        units.append(unit)
        records.append(TranslationRecord(
            unit_id=uid, source_hash=unit.source_hash, target_text=f"BODY{page} " + "{{asset:" + aid + "}}",
            asset_translations=[AssetTranslation(asset_id=aid, target_text=f"COMPANION{page}")],
            reader_note=ReaderNote(text=f"NOTE{page}"),
        ))
    write_jsonl(root / "derived/units.jsonl", units)
    write_jsonl(root / "translations/current.jsonl", records)
    outputs = render_project(root, ",".join(map(str, pages)), "annotations", allow_draft=True)
    return Path(outputs["markdown"]).read_text(encoding="utf-8")


@pytest.mark.parametrize("flags", [
    [(False, True), (False, False)],
    [(False, False), (True, False)],
    [(False, True), (True, False)],
    [(False, True), (False, True), (False, False)],
])
def test_annotations_follow_the_complete_paragraph(annotation_project: Path, flags: list[tuple[bool, bool]]) -> None:
    pages = list(range(1, len(flags) + 1))
    text = _render_chain(annotation_project, pages, flags)
    assert text.index(f"BODY{pages[-1]}") < text.index("COMPANION1")
    assert text.index(f"COMPANION{pages[-1]}") < text.index("NOTE1")
    for page in pages:
        assert text.count(f"BODY{page}") == text.count(f"COMPANION{page}") == text.count(f"NOTE{page}") == 1
    assert len({next(i for i, line in enumerate(text.splitlines()) if f"BODY{page}" in line) for page in pages}) == 1


@pytest.mark.parametrize("pages, flags, closed", [
    ([1, 2], [(False, False), (True, False)], True),
    ([1, 3], [(False, True), (False, False)], False),
    ([1, 2], [(False, False), (False, False)], False),
])
def test_annotations_flush_at_an_actual_paragraph_boundary(
    annotation_project: Path, pages: list[int], flags: list[tuple[bool, bool]], closed: bool,
) -> None:
    text = _render_chain(annotation_project, pages, flags, closed=closed)
    assert text.index("BODY1") < text.index("COMPANION1") < text.index("NOTE1") < text.index(f"BODY{pages[1]}")


def test_annotations_flush_at_end_of_selection(annotation_project: Path) -> None:
    text = _render_chain(annotation_project, [1], [(False, True)])
    assert text.index("BODY1") < text.index("COMPANION1") < text.index("NOTE1")
