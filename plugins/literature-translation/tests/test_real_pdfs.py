from __future__ import annotations

from pathlib import Path

import pytest

from littrans.extractor import extract_source
from littrans.project import initialize_project

REPO_ROOT = Path(__file__).resolve().parents[3]
WPF_PDF = next(REPO_ROOT.glob("*WPF*.pdf"), None)
PAPER_PDF = next(REPO_ROOT.glob("*Bodenschatz*.pdf"), None)


@pytest.mark.skipif(WPF_PDF is None, reason="Local WPF PDF fixture is unavailable")
def test_wpf_complex_pages(tmp_path: Path) -> None:
    assert WPF_PDF is not None
    root = tmp_path / "wpf"
    initialize_project(WPF_PDF, root, "technical-book")
    units = extract_source(root, "30-31,61,70,86-87")
    page_61 = [unit for unit in units if unit.page == 61]
    page_70 = [unit for unit in units if unit.page == 70]
    page_86 = [unit for unit in units if unit.page == 86]
    page_87 = [unit for unit in units if unit.page == 87]
    assert any(
        unit.kind == "code"
        and unit.code_language == "xaml"
        and '    xmlns="' in unit.source_text
        for unit in page_61
    )
    assert any(unit.kind == "note" for unit in page_61)
    assert any(unit.kind == "figure" for unit in page_61)
    assert any(unit.kind == "caption" for unit in page_61)
    assert any(unit.kind == "figure" and unit.asset_refs for unit in page_86)
    assert any(unit.kind == "caption" and "Figure 3-1" in unit.source_text for unit in page_86)
    assert any(
        unit.kind == "table"
        and unit.table
        and unit.table.column_count == 2
        and len(unit.table.rows) == 4
        for unit in page_86
    )
    assert not any(
        "DispatcherObject Legend Abstract Class" in unit.source_text
        for unit in page_86
        if unit.translatable
    )
    page_31_first = next(unit for unit in units if unit.page == 31 and unit.kind == "paragraph")
    assert page_31_first.continues_from_previous
    assert not any("\nw\nWPF" in unit.source_text for unit in page_61)
    assert any(
        unit.kind == "paragraph" and "corrected markup" in unit.source_text
        for unit in page_70
    )
    assert not any(
        unit.kind == "table" and "corrected markup" in unit.source_text
        for unit in page_70
    )
    assert any(
        unit.kind == "paragraph" and "Along with these core containers" in unit.source_text
        for unit in page_87
    )
    assert not any(
        unit.kind == "table" and "Along with these core containers" in unit.source_text
        for unit in page_87
    )


@pytest.mark.skipif(PAPER_PDF is None, reason="Local paper PDF fixture is unavailable")
def test_paper_formula_and_plot_pages(tmp_path: Path) -> None:
    assert PAPER_PDF is not None
    root = tmp_path / "paper"
    initialize_project(PAPER_PDF, root, "research-paper")
    units = extract_source(root, "3,5,10-11")
    page_3 = [unit for unit in units if unit.page == 3]
    page_5 = [unit for unit in units if unit.page == 5]
    page_10 = [unit for unit in units if unit.page == 10]
    page_11 = [unit for unit in units if unit.page == 11]
    assert any(unit.kind == "equation" and unit.asset_refs and unit.latex for unit in page_3)
    assert any("incompressibility condition" in unit.source_text for unit in page_3 if unit.kind == "paragraph")
    assert any(unit.kind == "footnote" for unit in page_3)
    assert any("√15Re" in unit.source_text for unit in page_3 if unit.kind == "footnote")
    assert any(unit.kind == "caption" and "Figure 4" in unit.source_text for unit in page_10)
    assert any(unit.kind == "figure" and unit.asset_refs for unit in page_10)
    assert any(unit.kind == "figure" and unit.asset_refs for unit in page_5)
    assert any(unit.kind == "figure" and unit.asset_refs for unit in page_11)
    assert not any("Downloaded from www.annualreviews.org" in unit.source_text for unit in units)
    assert not any(
        unit.translatable and unit.source_text.strip() in {"10−8", "10−6", "10−4"}
        for unit in page_10
    )
