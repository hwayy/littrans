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
