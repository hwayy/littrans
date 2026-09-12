from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pymupdf as fitz
import pytest
from test_workflow_v6 import project as workflow_project

from littrans import fidelity, structure_profile
from littrans.context_packets import original_context
from littrans.models import SourceUnit, TranslationRecord
from littrans.project import rebuild_project
from littrans.quality import run_qa
from littrans.source_structure import plan_structure
from littrans.storage import (
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    save_project,
    write_json,
    write_jsonl,
    write_yaml,
)
from littrans.translation import submit_translation
from littrans.workflow import create_workflow_packet, workflow_next

project = workflow_project


@pytest.mark.parametrize("bad_id", ["../escape", "bad id", "x}}", ""])
def test_multifragment_rejects_unsafe_id(project: Path, bad_id: str) -> None:
    config = load_project(project)
    with fitz.open(config.source(project)) as doc:
        glyphs, _ = fidelity._native(doc[0])
        with pytest.raises(ValueError, match="asset ID"):
            fidelity._asset(
                project,
                doc,
                1,
                config.source_sha256,
                {
                    "id": bad_id,
                    "kind": "math",
                    "fragments": [
                        {"bbox": [50, 85, 75, 105]},
                        {"bbox": [75, 85, 100, 105]},
                    ],
                },
                glyphs,
            )


@pytest.mark.parametrize(
    "label", ["1 Note", "12\u00a0Note", "12. Note", "1) Note", "12中注", "12Note"]
)
def test_separated_footnote_labels(label: str) -> None:
    number = "12" if label.startswith("12") else "1"
    with fitz.open() as doc:
        page = doc.new_page()
        page.insert_text((50, 200), "Prose with a note", fontsize=12)
        page.insert_text((160, 196), number, fontsize=7)
        page.insert_text((50, 700), label.replace("中注", "ZZ"), fontsize=9)
        glyphs, blocks = fidelity._native(page)
        if "中" in label:
            for glyph in glyphs:
                if glyph["text"] == "Z":
                    glyph["text"] = "中"
        plan = plan_structure(
            glyphs,
            blocks,
            [
                {"label": "footnote", "bbox": [90, 1370, 400, 1420]},
            ],
            page.rect.height,
        )
        calls = [m for m in plan["markers"].values() if not m["definition"]]
        assert len(calls) == len(number)
        assert {m["number"] for m in calls} == {number}


def test_rebuild_preserves_rights(project: Path) -> None:
    config = load_project(project)
    config.rights_status = "permission-granted-for-internal-use"
    save_project(project, config)
    rebuilt = project.parent / "rebuilt"
    result = rebuild_project(project, rebuilt)
    assert result.rights_status == config.rights_status
    assert load_project(rebuilt).rights_status == config.rights_status
    assert read_json(rebuilt / "derived/provenance.json")["rights_status"] == config.rights_status


def test_probe_waits_for_lock_and_merges_latest_profile(project: Path, monkeypatch) -> None:
    entered = Event()
    original = structure_profile._observe

    def observe(page, number):
        entered.set()
        return original(page, number)

    monkeypatch.setattr(structure_profile, "_observe", observe)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with project_write_lock(project):
            future = pool.submit(structure_profile.probe_structure, project, "2")
            ran_while_locked = entered.wait(0.3)
            config = load_project(project)
            write_json(
                project / structure_profile.PROFILE_PATH,
                {
                    "source_sha256": config.source_sha256,
                    "pages": [1],
                    "observations": [{"page": 1}],
                    "handling_rules": {"footnotes": "preserve"},
                    "review_notes": "keep notes",
                    "status": "reviewed",
                    "inspected_pages": [1],
                },
            )
        future.result(timeout=10)
    assert not ran_while_locked
    profile = read_json(project / structure_profile.PROFILE_PATH)
    assert profile["pages"] == [1, 2]
    assert {o["page"] for o in profile["observations"]} == {1, 2}
    assert profile["handling_rules"] == {"footnotes": "preserve"}
    assert profile["review_notes"] == "keep notes"
    assert profile["status"] == "draft"


@pytest.mark.parametrize("stale", [False, True])
def test_failed_qa_routes_to_revision_only_when_current(project: Path, stale: bool) -> None:
    bid = "sample-one-b001"
    units = [
        u
        for u in read_jsonl(project / "derived/units.jsonl", SourceUnit)
        if u.page == 1 and u.translatable
    ]
    records = [
        TranslationRecord(
            unit_id=u.unit_id,
            source_hash=u.source_hash,
            target_text=u.source_text.replace("The identity holds.", "该恒等式成立."),
            image_evidence=original_context(project, [u])["required_images"],
        )
        for u in units
    ]
    path = project / "input.jsonl"
    write_jsonl(path, records)
    submit_translation(project, bid, path)
    write_yaml(
        project / "glossary/approved.yaml",
        {
            "terms": [
                {"source": "identity", "target": "恒等关系", "status": "approved"},
            ]
        },
    )
    report = run_qa(project, bid)
    assert not report.passed
    if stale:
        records[0].target_text += " 新增内容。"
        write_jsonl(path, records)
        submit_translation(project, bid, path)
    result = workflow_next(project, start_at=bid, through=bid, host="codex")
    assert result["stage"] == ("qa" if stale else "revise")
    if not stale:
        packet = create_workflow_packet(project, "revise", [bid], host="codex")
        directory = project / packet.storage_root / packet.packet_id
        assert "approved-term-missing" in (directory / f"{bid}.revise.md").read_text(
            encoding="utf-8"
        )
        for record in records:
            record.target_text = record.target_text.replace("恒等式", "恒等关系")
        write_jsonl(path, records)
        submit_translation(project, bid, path)
        assert workflow_next(project, start_at=bid, through=bid)["stage"] == "qa"
        assert run_qa(project, bid).passed
        assert workflow_next(project, start_at=bid, through=bid)["stage"] == "audit"
