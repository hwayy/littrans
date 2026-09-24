from pathlib import Path

import pytest
from test_workflow_v6 import project as workflow_project

from littrans import fidelity
from littrans.models import RoleDispatch, SourceUnit, UnitKind
from littrans.rendering import render_project
from littrans.storage import (
    load_project,
    read_json,
    read_jsonl,
    save_project,
    write_json,
    write_jsonl,
)
from littrans.workflow import create_workflow_packet

project = workflow_project


@pytest.mark.parametrize("stage", ["transcribe", "asset-audit"])
def test_asset_packet_honors_workflow_host(
    project: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from littrans import representations

    monkeypatch.setenv("CODEX_THREAD_ID", "test")
    cfg = load_project(project)
    cfg.agent_models["codex"] = {}
    cfg.agent_models["claude"] = {
        "transcribe": RoleDispatch(model="selected-model", reasoning_effort="high")
    }
    save_project(project, cfg)
    if stage == "asset-audit":
        monkeypatch.setattr(
            representations,
            "representation_status",
            lambda root, ids: {"assets": {aid: {"state": "asset-audit"} for aid in ids}},
        )
        observed = []
        monkeypatch.setattr(
            representations,
            "build_asset_packet",
            lambda *args, **kwargs: (
                observed.append(kwargs.get("host")) or {"host": kwargs.get("host")}
            ),
        )
    result = create_workflow_packet(project, stage, ["sample-one-b001"], host="claude")
    assert result["host"] == "claude"


@pytest.mark.parametrize("same_import", [False, True])
def test_override_unit_ids_cannot_collide(project: Path, same_import: bool) -> None:
    packet = fidelity.build_source_review_packet(project)
    payload = read_json(Path(packet["packet_path"]))
    review = read_json(Path(packet["review_template"]))
    review["reviewer"] = "test"
    foreign = payload["pages"][0]["units"][0]["unit_id"]
    selected = review["pages"] if same_import else [review["pages"][1]]
    for decision in selected:
        page = next(p for p in payload["pages"] if p["page"] == decision["page"])
        source_units = [
            {
                "unit_id": u["unit_id"],
                "source_markdown": u["source_markdown"] or u["source_text"],
                "bbox": u["bbox"],
                "kind": u["kind"],
            }
            for u in page["units"]
        ]
        source_units[0]["unit_id"] = "duplicate" if same_import else foreign
        decision["override"] = {"units": source_units}
    review["pages"] = selected
    path = project / "bad-units.json"
    write_json(path, review)
    before = (project / "derived/units.jsonl").read_bytes()
    with pytest.raises(ValueError, match="unit.*ID|unit.*id"):
        fidelity.import_source_review(project, path, True)
    assert (project / "derived/units.jsonl").read_bytes() == before


def test_markdown_footnote_calls_have_unique_definitions(project: Path) -> None:
    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    for page in (1, 2):
        caller = next(u for u in units if u.page == page and u.kind is UnitKind.PARAGRAPH)
        caller.source_text = caller.source_markdown = "Call[^1]"
        caller.footnote_refs = [f"note-{page}"]
        units.append(
            caller.model_copy(
                update={
                    "unit_id": f"note-{page}",
                    "kind": UnitKind.FOOTNOTE,
                    "source_text": "Definition",
                    "source_markdown": "Definition",
                    "footnote_number": "1",
                    "footnote_refs": [],
                }
            )
        )
    write_jsonl(project / "derived/units.jsonl", units)
    result = render_project(project, "all", name="notes-md", allow_draft=True)
    path = next(Path(v) for v in result.values() if isinstance(v, str) and v.endswith(".zh.md"))
    import re

    text = path.read_text(encoding="utf-8")
    definitions = re.findall(r"^\[\^([^]]+)\]: ", text, re.M)
    calls = re.findall(r"\[\^([^]]+)\](?!:)", text)
    assert len(definitions) == len(set(definitions)) == 2
    assert set(calls) == set(definitions)


@pytest.mark.parametrize("command", ["assets", "workflow"])
def test_asset_cli_explicit_host(
    project: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    from typer.testing import CliRunner

    from littrans.cli import app
    from littrans.fidelity_models import load_assets

    monkeypatch.setenv("CODEX_THREAD_ID", "test")
    cfg = load_project(project)
    cfg.agent_models["codex"] = {}
    cfg.agent_models["claude"] = {
        "transcribe": RoleDispatch(model="selected-model", reasoning_effort="high")
    }
    save_project(project, cfg)
    selector = (
        ["--asset-ids", next(iter(load_assets(project)))]
        if command == "assets"
        else ["--batch-ids", "sample-one-b001"]
    )
    result = CliRunner().invoke(
        app,
        [command, "packet", str(project), "--stage", "transcribe", "--host", "claude", *selector],
    )
    assert result.exit_code == 0, result.output
    import json

    assert json.loads(result.stdout)["host"] == "claude"


def test_markdown_calls_preserve_literal_code_and_multiline_note() -> None:
    from littrans.rendering import _markdown_footnote_calls, _markdown_note_label, _target_markdown

    caller = SourceUnit(
        unit_id="call",
        kind=UnitKind.PARAGRAPH,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="Call[^1]",
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
            "source_text": "First\nSecond",
            "footnote_refs": [],
        }
    )
    text = "**Call[^1]** `literal[^1]`\n```text\ncode[^1]\n```\n$x[^1]$"
    result = _markdown_footnote_calls(text, caller, {"note": note})
    assert result.startswith("**Call[^" + _markdown_note_label(note) + "]**")
    assert "`literal[^1]`" in result and "code[^1]" in result and "$x[^1]$" in result
    definition = _target_markdown(note, None)
    assert definition == f"[^{_markdown_note_label(note)}]: First\n    Second"


def test_continued_tables_keep_distinct_footnote_scopes() -> None:
    from littrans.models import TableData
    from littrans.rendering import _coalesce_table_units

    first = SourceUnit(
        unit_id="t1",
        kind=UnitKind.TABLE,
        page=1,
        bbox=(0, 0, 1, 1),
        source_text="A[^1]",
        source_hash="a",
        confidence=1,
        table=TableData(rows=[["A[^1]"]], column_count=1),
        footnote_refs=["n1"],
        continued_to_next=True,
    )
    second = first.model_copy(
        update={
            "unit_id": "t2",
            "page": 2,
            "footnote_refs": ["n2"],
            "continues_from_previous": True,
            "continued_to_next": False,
        }
    )
    units, groups = _coalesce_table_units([first, second])
    assert [u.unit_id for u in units] == ["t1", "t2"]
    assert not groups


def test_expanded_asset_stays_inside_markdown_note(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from littrans import rendering

    units = read_jsonl(project / "derived/units.jsonl", SourceUnit)
    caller = next(u for u in units if u.kind is UnitKind.PARAGRAPH)
    note = caller.model_copy(
        update={
            "unit_id": "expanded-note",
            "kind": UnitKind.FOOTNOTE,
            "source_text": "TOKEN",
            "source_markdown": "TOKEN",
            "footnote_number": "1",
            "footnote_refs": [],
        }
    )
    units.append(note)
    write_jsonl(project / "derived/units.jsonl", units)
    original = rendering.resolve_asset_markdown

    def expand(root, text, output, **kwargs):
        return original(root, text, output, **kwargs).replace(
            "TOKEN", "First\n\n![image](image.png)\n\nLast"
        )

    monkeypatch.setattr(rendering, "resolve_asset_markdown", expand)
    result = render_project(project, "all", name="expanded-note", allow_draft=True)
    path = next(Path(v) for v in result.values() if isinstance(v, str) and v.endswith(".zh.md"))
    text = path.read_text(encoding="utf-8")
    label = rendering._markdown_note_label(note)
    assert f"[^{label}]: First\n    \n    ![image](image.png)\n    \n    Last" in text
