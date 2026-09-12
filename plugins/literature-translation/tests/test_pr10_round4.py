from pathlib import Path

import pymupdf as fitz
import pytest
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.fidelity_models import load_assets
from littrans.models import SourceUnit, TableData, UnitKind
from littrans.rendering import _unit_html
from littrans.source_structure import plan_structure
from littrans.storage import load_project, read_json, save_project, write_json
from littrans.workflow import create_workflow_packet

project = workflow_project


@pytest.mark.parametrize("after_approval", [False, True])
def test_source_report_damage_invalidates_review(project: Path, after_approval: bool) -> None:
    packet = fidelity.build_source_review_packet(project, "1")
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    for p in review["pages"]:
        for key in (
            "viewed_original",
            "coverage_complete",
            "boundaries_complete",
            "reading_order_correct",
            "grouping_checked",
            "layout_fallback_checked",
        ):
            p[key] = True
    path = project / "new-review.json"
    write_json(path, review)
    if after_approval:
        fidelity.import_source_review(project, path, True)
    Path(packet["visual_report"]).write_text("changed overlay", encoding="utf-8")
    with pytest.raises(ValueError, match="report|artifact"):
        fidelity.import_source_review(project, path, True)
    assert fidelity.verify_fidelity(project, "1")["passed"] is (not after_approval)


@pytest.mark.parametrize("host", ["claude", "cursor"])
def test_packet_explicit_host(project: Path, monkeypatch: pytest.MonkeyPatch, host: str) -> None:
    monkeypatch.setenv("CODEX_THREAD_ID", "test")
    cfg = load_project(project)
    cfg.agent_models["codex"] = {}
    cfg.agent_models[host] = {"translate": "test-model", "reasoning_effort": "high"}
    save_project(project, cfg)
    assert create_workflow_packet(project, "translate", ["sample-one-b001"], host=host)


@pytest.mark.parametrize("source_view", [True, False])
def test_cross_page_footnote_stable_anchor(source_view: bool) -> None:
    caller = SourceUnit(
        unit_id="caller",
        kind=UnitKind.TABLE,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="A[^1]",
        source_hash="a",
        confidence=1,
        footnote_refs=["note"],
        table=TableData(rows=[["A[^1]"]], column_count=1),
    )
    note = caller.model_copy(
        update={
            "unit_id": "note",
            "kind": UnitKind.FOOTNOTE,
            "page": 2,
            "source_text": "Note",
            "footnote_number": "1",
            "footnote_refs": [],
        }
    )
    scope = {"caller": caller, "note": note}
    call_html = _unit_html(caller, None, source_view=source_view, unit_map=scope)
    note_html = _unit_html(note, None, source_view=source_view, unit_map=scope)
    anchor = f"fn-{'source' if source_view else 'target'}-note"
    assert f'href="#{anchor}"' in call_html and f'id="{anchor}"' in note_html


@pytest.mark.parametrize("number", ["1", "12"])
def test_cmr_footnote_call(number: str) -> None:
    with fitz.open() as doc:
        p = doc.new_page()
        p.insert_text((50, 200), "Prose with a note", fontsize=12)
        p.insert_text((160, 196), number, fontsize=7)
        p.insert_text((50, 700), number + "Note text.", fontsize=9)
        glyphs, blocks = fidelity._native(p)
        for g in glyphs:
            g["font"] = "CMR7" if g["size"] == 7 else "CMR10"
        plan = plan_structure(
            glyphs, blocks, [{"label": "footnote", "bbox": [90, 1370, 400, 1420]}], p.rect.height
        )
        assert len([m for m in plan["markers"].values() if not m["definition"]]) == len(number)


@pytest.mark.parametrize("same_import", [False, True])
def test_override_asset_ids_do_not_replace_other_pages(project: Path, same_import: bool) -> None:
    packet = fidelity.build_source_review_packet(project)
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    assets = load_assets(project)
    old_id = next(a.id for a in assets.values() if a.fragments[0].page == 1)
    selected = review["pages"] if same_import else [review["pages"][1]]
    for p in selected:
        p["override"] = {
            "regions": [
                {
                    "id": "duplicate" if same_import else old_id,
                    "kind": "math",
                    "bbox": [50, 85, 100, 105],
                }
            ]
        }
    review["pages"] = selected
    path = project / "collision.json"
    write_json(path, review)
    before = (project / "derived/fidelity-assets.jsonl").read_bytes()
    with pytest.raises(ValueError, match="asset.*ID|asset.*id"):
        fidelity.import_source_review(project, path, True)
    assert (project / "derived/fidelity-assets.jsonl").read_bytes() == before


def test_source_report_rebuild_does_not_restore_approval(project: Path) -> None:
    original = fidelity.build_source_review_packet(project)
    report = Path(original["visual_report"])
    assert "file:///" not in report.read_text(encoding="utf-8")
    report.write_text("broken", encoding="utf-8")
    fresh = fidelity.build_source_review_packet(project)
    assert fresh["packet_id"] != original["packet_id"]
    assert not fidelity.verify_fidelity(project)["passed"]
    assert report.read_text(encoding="utf-8") == "broken"


def test_cli_packet_honors_host(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from littrans.cli import app

    monkeypatch.setenv("CODEX_THREAD_ID", "test")
    cfg = load_project(project)
    cfg.agent_models["codex"] = {}
    cfg.agent_models["claude"] = {"translate": "test-model", "reasoning_effort": "high"}
    save_project(project, cfg)
    result = CliRunner().invoke(
        app,
        [
            "workflow",
            "packet",
            str(project),
            "--stage",
            "translate",
            "--batch-ids",
            "sample-one-b001",
            "--host",
            "claude",
        ],
    )
    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("base_font,base_text", [("CMMI10", "x"), ("CMR10", "2")])
def test_cmr_exponent_is_not_footnote(base_font: str, base_text: str) -> None:
    with fitz.open() as doc:
        p = doc.new_page()
        p.insert_text((50, 180), "Normal surrounding prose to establish text size.", fontsize=12)
        p.insert_text((50, 200), base_text, fontsize=12)
        p.insert_text((58, 196), "1", fontsize=7)
        p.insert_text((50, 700), "1Note text.", fontsize=9)
        glyphs, blocks = fidelity._native(p)
        for g in glyphs:
            g["font"] = (
                "CMR7" if g["size"] == 7 else base_font if g["origin"][1] == 200 else "CMR10"
            )
        plan = plan_structure(
            glyphs, blocks, [{"label": "footnote", "bbox": [90, 1370, 400, 1420]}], p.rect.height
        )
        assert not [m for m in plan["markers"].values() if not m["definition"]]


def test_page_scoped_render_includes_cross_page_definition(project: Path) -> None:
    from littrans.rendering import render_project
    from littrans.source_render import render_source_review
    from littrans.storage import read_jsonl, write_jsonl

    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    caller = next(u for u in units if u.page == 1 and u.kind is UnitKind.PARAGRAPH)
    caller.source_text = "A[^1]"
    caller.source_markdown = "A[^1]"
    caller.footnote_refs = ["test-note"]
    note = caller.model_copy(
        update={
            "unit_id": "test-note",
            "kind": UnitKind.FOOTNOTE,
            "page": 2,
            "source_text": "Note",
            "source_markdown": "Note",
            "footnote_refs": [],
            "footnote_number": "1",
        }
    )
    units.append(note)
    write_jsonl(project / "derived/units.jsonl", units)
    source = render_source_review(project, "1", name="cross-page-source")
    bilingual = render_project(project, "1", name="cross-page-bilingual", allow_draft=True)
    for result in (source, bilingual):
        paths = [Path(v) for k, v in result.items() if isinstance(v, str) and v.endswith(".html")]
        assert paths
        document = paths[0].read_text(encoding="utf-8")
        assert 'href="#fn-source-test-note"' in document
        assert 'id="fn-source-test-note"' in document


def test_asset_companion_footnotes_use_referenced_unit() -> None:
    from types import SimpleNamespace

    from littrans.rendering import _asset_companions

    caller = SourceUnit(
        unit_id="caller",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="A[^1]",
        source_hash="a",
        confidence=1,
        footnote_refs=["note"],
    )
    note = caller.model_copy(
        update={
            "unit_id": "note",
            "kind": UnitKind.FOOTNOTE,
            "page": 2,
            "footnote_number": "1",
            "footnote_refs": [],
        }
    )
    record = SimpleNamespace(
        asset_translations=[
            SimpleNamespace(
                language_present=True,
                asset_id="a",
                target_text="Text[^1]",
                target_table=TableData(rows=[["Cell[^1]"]], column_count=1),
                figure_labels=[],
            )
        ]
    )
    _, markup = _asset_companions(record, caller, {"note": note})
    assert markup.count('href="#fn-target-note"') == 2
