"""Integration checks for policy snapshots and current task imports."""
from pathlib import Path

import pytest
from test_workflow_v6 import project as project_fixture

from littrans.batching import load_manifest
from littrans.configuration import edit
from littrans.models import SourceUnit, TranslationRecord
from littrans.storage import read_json, read_jsonl, write_jsonl
from littrans.tasks import claim_task, create_task, receive_task

project = project_fixture


@pytest.mark.parametrize("key,value,stale", [
    ("agents.codex.roles.translate.model", "new-model", False),
    ("agents.codex.wave_size", 2, False),
    ("batch.max_source_words", 1200, False),
    ("translation.equations.inline", "reviewed-transcription", False),
    ("translation.code.translate_comments", True, True),
    ("translation.prose_punctuation", "preserve", True),
    ("outline_source", "pdf-bookmarks", True),
])
def test_task_receipt_checks_consumed_policy_domains(project, key, value, stale):
    task = create_task(project, "translate", batch_ids=["sample-one-b001"], host="codex")
    directory = Path(task["handoff"]).parent
    claim_task(project, task["task_id"], "writer", "fresh-session")
    envelope = read_json(directory / "task.json")
    original = read_json(project / envelope["packet"]["files"]["original-images"])
    editable = load_manifest(project, "sample-one-b001").translatable_unit_ids
    records = [TranslationRecord(unit_id=unit.unit_id, source_hash=unit.source_hash,
                target_text=unit.source_text.replace("The identity holds.", "该恒等式成立。"),
                image_evidence=original["required_images"])
               for unit in read_jsonl(project / "derived/units.jsonl", SourceUnit) if unit.unit_id in editable]
    write_jsonl(directory / "result.json", records)
    edit(project, key, value)
    if stale:
        with pytest.raises(ValueError, match="policy changed"):
            receive_task(project, task["task_id"])
    else:
        assert receive_task(project, task["task_id"])["state"] == "imported"


def _audit_ready(tmp_path: Path) -> tuple[Path, str]:
    from test_efficiency_v4 import _make_project, _submit

    from littrans.quality import run_qa
    root, manifests = _make_project(tmp_path, 1)
    batch = manifests[0].batch_id
    edit(root, "agents.claude.roles.audit.model", "sonnet")
    edit(root, "agents.claude.audit_lenses.technical.model", "opus")
    _submit(root, batch)
    assert run_qa(root, batch).passed
    return root, batch


def _lens_models(tasks: list[dict]) -> dict[str, str | None]:
    assert {task["stage"] for task in tasks} == {"audit"}
    return {task["lens"]: task["model"] for task in tasks}


def test_audit_ready_tasks_resolve_each_lens_policy(tmp_path: Path) -> None:
    from littrans.workflow import create_workflow_packet, workflow_next, workflow_status
    root, batch = _audit_ready(tmp_path)
    expected = {"fidelity": "sonnet", "technical": "opus", "chinese-style": "sonnet"}
    wave = workflow_next(root, host="claude")
    assert wave["stage"] == "audit"
    assert _lens_models(wave["ready_tasks"]) == expected
    assert _lens_models(workflow_status(root, [batch], host="claude")["ready_tasks"]) == expected
    # Lens advisories name the settings path that actually holds the lens policy.
    assert not any("agent_models" in note for note in wave["dispatch_advisories"])
    packet = create_workflow_packet(root, "audit", [batch], "technical", host="claude")
    assert packet.model == "opus"
    original = read_json(root / packet.storage_root / packet.packet_id / "original-images.json")
    assert original["dispatch"]["model"] == "opus"


def test_audit_ready_tasks_list_only_missing_lenses(tmp_path: Path) -> None:
    from test_efficiency_v4 import _packet_dir

    from littrans.workflow import create_workflow_packet, import_review_set, workflow_next
    root, batch = _audit_ready(tmp_path)
    packet = create_workflow_packet(root, "audit", [batch], "fidelity", host="claude")
    issues = _packet_dir(root, packet) / "issues.jsonl"
    write_jsonl(issues, [])
    import_review_set(root, _packet_dir(root, packet) / "manifest.json", issues)
    wave = workflow_next(root, host="claude")
    assert _lens_models(wave["ready_tasks"]) == {"technical": "opus", "chinese-style": "sonnet"}


def test_opencode_ready_tasks_name_native_lens_agents(tmp_path: Path) -> None:
    from littrans.workflow import workflow_next
    root, _ = _audit_ready(tmp_path)
    tasks = workflow_next(root, host="opencode")["ready_tasks"]
    assert {task["lens"]: task["native_agent"] for task in tasks} == {
        lens: f"littrans-translation-reviewer-{lens}" for lens in ("fidelity", "technical", "chinese-style")}
    from littrans.storage import load_project
    config = load_project(root)
    for task in tasks:
        dispatch = config.dispatch("opencode", "audit", task["lens"])
        assert (task["model"], task["reasoning_effort"]) == (dispatch.model, dispatch.reasoning_effort)
