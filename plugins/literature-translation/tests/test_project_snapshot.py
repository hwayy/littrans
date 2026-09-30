"""Count real source reads, independently of file size and filesystem timing."""
from __future__ import annotations

import inspect
import json
import os
import shutil
import sys
from pathlib import Path

import pymupdf as fitz
import pytest
from synthetic_fixtures import copy_workflow_template

from littrans import batching, configuration, external_recheck, external_review, storage
from littrans.context_packets import original_context
from littrans.evidence import batch_unit_fingerprints, translation_memory
from littrans.fidelity import verify_fidelity
from littrans.models import BatchManifest, ExternalReviewRun, SourceUnit, TranslationRecord
from littrans.project import initialize_project
from littrans.quality import batch_translation_fingerprint, current_qa_context_fingerprint
from littrans.structure_profile import PROFILE_PATH, structure_context
from littrans.tasks import claim_task, create_task, receive_task
from littrans.workflow import workflow_next, workflow_status


def make_project(directory: Path, batches: int = 1, external: bool = False) -> Path:
    source = directory / "source.pdf"
    with fitz.open() as pdf:
        for _ in range(batches):
            pdf.new_page().insert_text((50, 60), "Synthetic source.")
        pdf.save(source)
    root = directory / "project"
    config = initialize_project(source, root, "technical-book", scaffold=False)
    for name in ("document-brief.md", "style-guide.md"):
        (root / "context" / name).write_text("Synthetic context.\n", encoding="utf-8")
    if external:
        settings = configuration.read_settings(root).payload()
        settings["external_review"].update(enabled=True, primary="test", reviewers={
            "test": {"driver": "codex-cli", "model": "gpt-6", "model_identity": None, "effort": None},
        })
        (root / "settings.yaml").write_text(configuration.yaml_text(settings), encoding="utf-8")
    units = [SourceUnit(
        unit_id=f"u{i}", page=i + 1, kind="paragraph", bbox=(0, 0, 100, 100),
        source_text=f"Synthetic unit {i}.", source_hash=storage.sha256_text(str(i)), confidence=1,
    ) for i in range(batches)]
    storage.write_jsonl(root / "derived/units.jsonl", units)
    for i, unit in enumerate(units):
        manifest = BatchManifest(
            batch_id=f"sample-b{i + 1:03d}", project_id=config.project_id, pages=[unit.page],
            unit_ids=[unit.unit_id], translatable_unit_ids=[unit.unit_id], source_words=3,
        )
        storage.write_yaml(root / "batches" / manifest.batch_id / "manifest.yaml",
                           manifest.model_dump(mode="json"))
    return root


def source_reads(root: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    source = storage.load_project(root).source(root).resolve()
    original = storage.sha256_file
    calls: list[str] = []

    def measured(path: Path) -> str:
        if path.resolve() == source:
            calls.append(inspect.stack()[1].function)
        return original(path)

    # Patch imported aliases too; otherwise a new redundant call in another module
    # could evade a test that patches only configuration.sha256_file.
    for name, module in list(sys.modules.items()):
        if name.startswith("littrans") and getattr(module, "sha256_file", None) is original:
            monkeypatch.setattr(module, "sha256_file", measured)
    return calls


def add_run(root: Path, bid: str, run_id: str = "run", base: str | None = None,
            recheck: bool = False) -> ExternalReviewRun:
    units = batching.load_manifest(root, bid).unit_ids
    run = ExternalReviewRun(
        run_id=run_id, batch_id=bid, reviewer_id="test", driver="codex-cli",
        role="primary", requested_model="gpt-6", actual_model="gpt-6", model_verified=True,
        execution="cli", translation_fingerprint=batch_translation_fingerprint(root, bid),
        packet_sha256="0" * 64, prompt_version=external_review.PROMPT_VERSION,
        covered_unit_ids=units, unit_fingerprints=batch_unit_fingerprints(root, bid),
        verdict="accepted", summary="Synthetic review.", base_run_id=base,
        scope="incremental" if base else "full", issue_ids=["missing-issue"] if recheck else [],
    )
    run.context_fingerprint = external_review._external_review_context_fingerprint(
        root, bid, units, run.scope,
    )
    storage.append_jsonl(external_review._runs_path(root, bid), [run])
    if recheck:
        binding = external_recheck._binding(root, run)
        storage.write_json(root / "reviews/external-rechecks" / binding / "receipt.json", {
            "binding": binding, "task_id": "synthetic-task", "findings": [],
        })
    return run


@pytest.mark.parametrize("batches", [1, 30])
@pytest.mark.parametrize("external", [False, True])
def test_workflow_hashes_once_across_batches_and_page_groups(tmp_path, monkeypatch, batches, external):
    root = make_project(tmp_path, batches, external)
    calls = source_reads(root, monkeypatch)
    for operation in (
        lambda: workflow_next(root, host="codex"),
        lambda: workflow_status(root, [f"sample-b{i + 1:03d}" for i in range(min(6, batches))],
                                host="codex"),
    ):
        calls.clear()
        assert operation()["stage"] == "source-review"
        assert calls == ["bind"]


@pytest.mark.parametrize("mode", ["missing", "accepted", "receipt", "chain"])
def test_external_status_reuses_config_across_reviews(tmp_path, monkeypatch, mode):
    root = make_project(tmp_path, external=True)
    bid = "sample-b001"
    if mode != "missing":
        add_run(root, bid, recheck=mode == "receipt")
    if mode == "chain":
        for i in range(6):
            add_run(root, bid, f"child-{i}", "run" if i == 0 else f"child-{i - 1}")
    calls = source_reads(root, monkeypatch)
    status = external_review.external_review_status(root, bid)
    assert calls == ["bind"]
    assert status["verdict"] == {
        "missing": "missing", "accepted": "accepted", "receipt": "inconclusive", "chain": "accepted",
    }[mode]
    calls.clear()
    workflow_next(root, host="codex")
    assert calls == ["bind"]


def test_standalone_helpers_match_verified_config(tmp_path, monkeypatch):
    root = make_project(tmp_path, external=True)
    run = add_run(root, "sample-b001", recheck=True)
    config = storage.load_project(root)
    units = storage.read_jsonl(root / "derived/units.jsonl", SourceUnit)
    # A real memory candidate exercises the nested workflow snapshot path.
    storage.write_jsonl(root / "translations/current.jsonl", [TranslationRecord(
        unit_id=units[0].unit_id, source_hash=units[0].source_hash,
        target_text="Synthetic translation.", status="human-approved",
    )])
    storage.write_json(root / PROFILE_PATH, {
        "source_sha256": config.source_sha256, "pages": [1],
    })
    calls = source_reads(root, monkeypatch)
    operations = (
        lambda **kw: current_qa_context_fingerprint(root, "sample-b001", **kw),
        lambda **kw: original_context(root, units, **kw),
        lambda **kw: translation_memory(root, ["another-unit"], **kw),
        lambda **kw: batching._context_text(root, units, None, None, **kw),
        lambda **kw: external_recheck.recheck_status(root, run, **kw),
        lambda **kw: verify_fidelity(root, **kw),
        lambda **kw: structure_context(root, **kw),
    )
    for operation in operations:
        calls.clear()
        expected = operation()
        assert calls == ["bind"]
        calls.clear()
        assert operation(project_config=config) == expected
        assert calls == []


def test_memory_completion_cache_belongs_to_one_operation(tmp_path, monkeypatch):
    from littrans import evidence

    root = make_project(tmp_path, batches=2)
    units = storage.read_jsonl(root / "derived/units.jsonl", SourceUnit)
    storage.write_jsonl(root / "translations/current.jsonl", [TranslationRecord(
        unit_id=units[0].unit_id, source_hash=units[0].source_hash,
        target_text="Synthetic translation.", status="human-approved",
    )])
    config = storage.load_project(root)
    calls = source_reads(root, monkeypatch)
    evaluations = []
    original = evidence._memory_complete_batch_ids

    def count_evaluation(*args, **kwargs):
        evaluations.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(evidence, "_memory_complete_batch_ids", count_evaluation)
    cache = {}
    for _ in range(2):
        batching._context_text(root, [units[1]], units[0], None,
                               project_config=config, completion_cache=cache)
    assert len(evaluations) == 1
    assert calls == []
    batching._context_text(root, [units[1]], units[0], None)
    assert len(evaluations) == 2
    assert calls == ["bind"]


def test_verified_pages_still_check_images_and_structure(tmp_path, monkeypatch, workflow_v6_template):
    root = copy_workflow_template(workflow_v6_template, tmp_path)
    config = storage.load_project(root)
    calls = source_reads(root, monkeypatch)
    result = verify_fidelity(root)
    assert result["passed"], result
    assert calls == ["bind"]
    calls.clear()
    assert workflow_next(root, host="codex")["stage"] == "translate"
    assert calls == ["bind"]
    storage.write_json(root / PROFILE_PATH, {
        "source_sha256": config.source_sha256, "pages": [1, 2],
        "handling_rules": {"paragraphs": "A new rule requires another source review."},
    })
    calls.clear()
    result = verify_fidelity(root)
    assert not result["passed"]
    assert calls == ["bind"]
    (root / PROFILE_PATH).unlink()
    image = root / "evidence/pages/fidelity-p0001.png"
    image.write_bytes(image.read_bytes() + b"changed")
    calls.clear()
    result = verify_fidelity(root)
    assert not result["passed"]
    assert any("page image changed" in error["message"] for error in result["errors"])
    assert calls == ["bind"]


@pytest.mark.parametrize("change", ["replace", "same-stat", "delete"])
def test_next_operation_and_state_save_revalidate_source(tmp_path, change):
    root = make_project(tmp_path)
    config = storage.load_project(root)
    workflow_next(root, host="codex")
    source = config.source(root)
    state = (root / "derived/project-state.json").read_bytes()
    if change == "delete":
        source.unlink()
    else:
        stat = source.stat()
        content = source.read_bytes()
        source.write_bytes(content.replace(b"PDF", b"PDX", 1) if change == "same-stat" else b"other")
        if change == "same-stat":
            os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
            assert source.stat().st_size == stat.st_size
            assert source.stat().st_mtime_ns == stat.st_mtime_ns
    for operation in (lambda: workflow_next(root, host="codex"), lambda: storage.save_project(root, config)):
        with pytest.raises(ValueError, match="Source binding"):
            operation()
        assert (root / "derived/project-state.json").read_bytes() == state


def test_direct_policy_edits_and_source_rebinding_are_visible(tmp_path):
    root = make_project(tmp_path)
    config = storage.load_project(root)
    fingerprint = current_qa_context_fingerprint(root, "sample-b001")
    settings = configuration.read_settings(root).payload()
    settings["translation"]["memory_minimum_status"] = "human-approved"
    (root / "settings.yaml").write_text(configuration.yaml_text(settings), encoding="utf-8")
    assert current_qa_context_fingerprint(root, "sample-b001") != fingerprint
    relocated = tmp_path / "relocated.pdf"
    shutil.copyfile(config.source(root), relocated)
    configuration.edit(root, "source_path", str(relocated), local=True)
    assert storage.load_project(root).source(root) == relocated
    local = (root / "settings.local.yaml").read_bytes()
    relocated.write_bytes(b"invalid PDF")
    with pytest.raises(ValueError):
        configuration.edit(root, "source_path", str(relocated), local=True)
    assert (root / "settings.local.yaml").read_bytes() == local
    shutil.copyfile(config.source(root), relocated)
    state = (root / "derived/project-state.json").read_bytes()
    (root / "settings.yaml").write_text("unknown_policy: true\n", encoding="utf-8")
    with pytest.raises(ValueError):
        storage.save_project(root, config)
    assert (root / "derived/project-state.json").read_bytes() == state


@pytest.mark.parametrize("task_mismatch", [False, True])
def test_task_source_check_precedes_result_and_state_writes(
    tmp_path, monkeypatch, workflow_v6_template, task_mismatch,
):
    root = copy_workflow_template(workflow_v6_template, tmp_path)
    task = create_task(root, "translate", batch_ids=["sample-one-b001"], host="generic")
    directory = Path(task["handoff"]).parent
    if task_mismatch:
        # Seed a correctly sealed envelope with a different task-level source
        # identity. Context equality must not replace the explicit task check.
        envelope = storage.read_json(directory / "task.json")
        envelope["source_sha256"] = "0" * 64
        payload = {key: value for key, value in envelope.items() if key != "task_id"}
        task["task_id"] = "task-" + storage.sha256_text(json.dumps(payload, sort_keys=True))[:24]
        envelope["task_id"] = task["task_id"]
        renamed = directory.with_name(task["task_id"])
        directory.rename(renamed)
        directory = renamed
        storage.write_json(directory / "task.json", envelope)
    claim_task(root, task["task_id"], "synthetic-writer", "fresh-session")
    incoming = tmp_path / "incoming.jsonl"
    incoming.write_text("", encoding="utf-8")
    config = storage.load_project(root)
    calls = source_reads(root, monkeypatch)
    source = config.source(root)
    if not task_mismatch:
        source.write_bytes(source.read_bytes() + b"\n% changed source\n")
    calls.clear()
    task_state = (directory / "state.json").read_bytes()
    project_state = (root / "derived/project-state.json").read_bytes()
    with pytest.raises(ValueError, match="Task source changed" if task_mismatch else "Source binding"):
        receive_task(root, task["task_id"], incoming)
    assert calls == ["bind"]
    assert not (directory / "result.json").exists()
    assert (directory / "state.json").read_bytes() == task_state
    assert (root / "derived/project-state.json").read_bytes() == project_state
    assert not list((root / "translations").glob("*.jsonl"))


@pytest.mark.parametrize("batches", [1, 30])
def test_batch_creation_hashes_at_boundaries_not_per_context(tmp_path, monkeypatch, batches):
    from test_efficiency_v4 import _make_project

    root, _ = _make_project(tmp_path, pages=batches)
    calls = source_reads(root, monkeypatch)
    created = batching.create_batches(root, "all", max_words=100, prefix="new")
    assert len(created) == batches
    # Entry, source evidence gate, status promotion and state save stay independent.
    assert calls == ["bind"] * 4
    calls.clear()
    batching.refresh_batch(root, created[0].batch_id)
    assert calls == ["bind"]
