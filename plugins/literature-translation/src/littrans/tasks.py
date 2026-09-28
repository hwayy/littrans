"""Portable task envelopes around existing domain packets and acceptance gates.

No model calls. Execution provenance is reported, not proof of independence.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from littrans.context_management import context_snapshot
from littrans.hosts import resolve_coordination_host
from littrans.resources import resource_root
from littrans.storage import (
    atomic_write_bytes,
    atomic_write_text,
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    sha256_file,
    sha256_text,
    write_json,
)
from littrans.task_models import TaskEnvelope

ROLES = {"source-review": "source-reviewer", "translate": "translator", "revise": "translator",
         "transcribe": "asset-transcriber", "asset-audit": "asset-reviewer",
         "audit": "translation-reviewer", "scout": "document-scout",
         "terminology": "terminology-researcher"}


def _directory(root: Path, task_id: str) -> Path:
    if not re.fullmatch(r"task-[a-f0-9]{24}", task_id):
        raise ValueError("Invalid task ID")
    path = root / ".littrans/work/tasks" / task_id
    path.resolve().relative_to(root.resolve())
    return path


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _read_task(directory: Path) -> dict[str, Any]:
    task = read_json(directory / "task.json")
    TaskEnvelope.model_validate(task)
    payload = {key: value for key, value in task.items() if key != "task_id"}
    expected = "task-" + sha256_text(json.dumps(payload, sort_keys=True))[:24]
    if task.get("schema_version") != 1 or task.get("task_id") != directory.name or expected != directory.name:
        raise ValueError("Task envelope changed or uses an unsupported protocol")
    return task


def create_task(root: Path, stage: str, *, batch_ids: list[str] | None = None,
                pages: str | None = None, asset_ids: list[str] | None = None,
                lens: str | None = None, host: str = "auto",
                objective: str | None = None, revision_notes: str | None = None) -> dict[str, Any]:
    root = root.resolve()
    config = load_project(root)
    if stage not in ROLES:
        raise ValueError("Unsupported task stage: " + stage)
    selected_host = resolve_coordination_host(host)
    batches = batch_ids or []
    if sum(bool(value) for value in (batches, pages, asset_ids)) != 1:
        raise ValueError("Specify exactly one of batch IDs, pages, or asset IDs")
    if stage != "audit" and lens is not None:
        raise ValueError("Only audit tasks accept a lens")
    if stage == "audit" and lens not in {"fidelity", "technical", "chinese-style"}:
        raise ValueError("Create a separate task for each audit lens")
    if stage in {"translate", "revise"} and len(batches) != 1:
        raise ValueError("One translation task owns exactly one batch")
    if stage in {"scout", "terminology"} and not (objective or "").strip():
        raise ValueError("Scout and terminology tasks require an explicit objective")
    if revision_notes and (stage != "transcribe" or not asset_ids):
        raise ValueError("Revision notes require an explicit asset-ID transcription task")
    packet: dict[str, Any]
    if batches:
        if stage in {"scout", "terminology"}:
            raise ValueError("Scout and terminology tasks require a page scope")
        from littrans.workflow import create_workflow_packet
        result = create_workflow_packet(root, stage, batches, lens, selected_host)
        if isinstance(result, list):
            raise ValueError("Task must contain one domain packet")
        packet = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
        if packet.get("pending") is False:
            raise ValueError("No pending assets in this scope")
        if stage in {"translate", "revise"} and len(batches) != 1:
            raise ValueError("One translation task owns exactly one batch")
    elif asset_ids:
        if stage not in {"transcribe", "asset-audit"}:
            raise ValueError("Asset IDs require an asset task")
        from littrans.representations import build_asset_packet
        packet = build_asset_packet(root, asset_ids, stage=stage, host=selected_host,
                                    revision_notes=revision_notes)
    elif stage == "source-review":
        from littrans.fidelity import build_source_review_packet
        packet = build_source_review_packet(root, pages or "all")
    elif stage in {"scout", "terminology"}:
        from littrans.extractor import inspect_source
        inspection = inspect_source(root, pages or "all")
        inspection.pop("source", None)
        packet = {"stage": stage, "source": inspection,
                  "source_locator": "Read source_path from the current project.yaml", "pages": pages}
    else:
        raise ValueError("This stage requires batch IDs or asset IDs")

    resources = resource_root()
    role_path = resources / "roles" / f"{ROLES[stage]}.md"
    files = [role_path, *sorted((resources / "references").glob("*.md"))]
    instructions = {path.relative_to(resources).as_posix(): sha256_file(path) for path in files}
    context = context_snapshot(root)
    # Domain validators retain their finer-grained dependency checks. This envelope also
    # refuses results made from a changed global context before any domain mutation.
    inputs: dict[str, str] = {}
    for name in packet.get("files", {}).values():
        inputs[name] = sha256_file(root / name)
    manifest_path = None
    if "storage_root" in packet:
        manifest_path = f"{packet['storage_root']}/{packet['packet_id']}/manifest.json"
        inputs[manifest_path] = sha256_file(root / manifest_path)
    if "packet_path" in packet:
        name = _relative(root, Path(packet["packet_path"]))
        inputs[name] = sha256_file(root / name)
        packet = {**packet, "packet_path": name}
        for key in ("review_template", "visual_report"):
            if key in packet:
                packet[key] = _relative(root, Path(packet[key]))
                inputs[packet[key]] = sha256_file(root / packet[key])
    payload = {"schema_version": 1, "stage": stage, "role": ROLES[stage], "lens": lens,
               "objective": objective,
               "batch_ids": batches, "pages": pages, "asset_ids": asset_ids or [],
               "packet": packet, "domain_manifest": manifest_path, "inputs": inputs,
               "context": context, "instructions": instructions,
               "dispatch": {"host": selected_host,
                            **config.dispatch(selected_host, "translate" if stage == "revise" else stage).model_dump(mode="json")}
               if stage not in {"scout", "terminology"} else {"host": selected_host},
               "source_sha256": config.source_sha256}
    if stage in {"translate", "revise"}:
        from littrans.batching import load_manifest
        from littrans.evidence import translation_unit_fingerprint
        from littrans.models import SourceUnit
        selected = set(packet["unit_ids"])
        payload["source_bindings"] = {
            unit.unit_id: translation_unit_fingerprint(unit, None)
            for unit in read_jsonl(root / "derived/units.jsonl", SourceUnit) if unit.unit_id in selected
        }
        payload["batch_binding"] = load_manifest(root, batches[0]).model_dump(mode="json")
    task_id = "task-" + sha256_text(json.dumps(payload, sort_keys=True))[:24]
    TaskEnvelope.model_validate({"task_id": task_id, **payload})
    directory = _directory(root, task_id)
    task_root = directory.parent
    task_root.mkdir(parents=True, exist_ok=True)
    with project_write_lock(task_root):
        if not (directory / "task.json").exists():
            for path in files:
                target = directory / "instructions" / path.relative_to(resources)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
            write_json(directory / "task.json", {"task_id": task_id, **payload})
            write_json(directory / "state.json", {"state": "pending", "executor": None})
            atomic_write_text(directory / "start.md", (
                f"# LitTrans {stage} task\n\n"
                "Resolve paths from this start.md file, not the shell's current directory or Git root. "
                "Its directory is the task directory; the project root is ../../../.. relative to it "
                "(the directory containing project.yaml). Resolve packet file paths from that project root. "
                f"Read task.json and "
                f"instructions/roles/{ROLES[stage]}.md. Read only the assigned evidence and scope.\n"
                "Use a fresh context. Do not inherit expected verdicts or parallel candidates.\n"
                "Save the native domain response as result.json (JSONL for translate/revise/audit). "
                "Do not change task.json or instructions. Missing visual capability must be reported, "
                "not replaced by copied image hashes. The coordinator receives the result through "
                f"`task receive PROJECT {task_id}` and the domain validator.\n"
                "For source-review return review-template decisions; report proposed rules separately. "
                "For scout/terminology return proposals with evidence and unresolved questions; "
                "these are never automatically approved.\n"
                "The coordinator claims with `task claim PROJECT TASK_ID --executor ID "
                "--mode fresh-session` (or subagent) before execution. Read-only reviewers "
                "return content for the coordinator to persist. Return the task ID, result "
                "path or content, and unresolved issues.\n"
            ))
    return task_status(root, task_id)


def task_status(root: Path, task_id: str | None = None) -> dict[str, Any]:
    if task_id is None:
        return {"tasks": [task_status(root, path.parent.name) for path in
                          sorted((root / ".littrans/work/tasks").glob("*/task.json"))]}
    directory = _directory(root, task_id)
    task = _read_task(directory)
    state = read_json(directory / "state.json")
    return {"task_id": task_id, "stage": task["stage"], **state,
            "result_available": (directory / "result.json").is_file(),
            "handoff": str(directory / "start.md")}


def claim_task(root: Path, task_id: str, executor: str, mode: str) -> dict[str, Any]:
    if not executor.strip() or mode not in {"subagent", "fresh-session"}:
        raise ValueError("Provide an executor ID and mode subagent or fresh-session")
    directory = _directory(root, task_id)
    with project_write_lock(directory.parent):
        task = _read_task(directory)
        state = read_json(directory / "state.json")
        if state["state"] == "imported":
            raise ValueError("Task already imported")
        if state.get("executor") not in {None, executor}:
            raise ValueError("Task is claimed by another executor; release it after confirming it stopped")
        # All source writers share one claim; translation writers conflict by batch,
        # transcribers by asset. Read-only reviewers can work concurrently.
        for other_path in directory.parent.glob("*/task.json"):
            if other_path.parent == directory:
                continue
            other_state = read_json(other_path.parent / "state.json")
            other = _read_task(other_path.parent)
            shared_batches = bool(set(task["batch_ids"]) & set(other["batch_ids"]))
            shared_assets = bool(set(task["packet"].get("asset_ids", []))
                                 & set(other["packet"].get("asset_ids", [])))
            if other_state.get("executor") == executor and (
                task["stage"] == "audit" and shared_batches
                and (other["stage"] in {"translate", "revise"}
                     or other["stage"] == "audit" and task["lens"] != other["lens"])
                or task["stage"] == "asset-audit" and shared_assets and other["stage"] == "transcribe"
            ):
                raise ValueError("Independent review requires a different executor context")
            if other_state.get("state") != "claimed":
                continue
            conflict = (task["stage"] == other["stage"] == "source-review"
                        or task["stage"] in {"translate", "revise"}
                        and other["stage"] in {"translate", "revise"}
                        and bool(set(task["batch_ids"]) & set(other["batch_ids"]))
                        or task["stage"] == other["stage"] == "transcribe"
                        and bool(set(task["packet"].get("asset_ids", []))
                                 & set(other["packet"].get("asset_ids", []))))
            if conflict:
                raise ValueError("Conflicting writer task: " + other["task_id"])
        write_json(directory / "state.json", {"state": "claimed", "executor": executor,
                                              "mode": mode, "independence": "executor-declared"})
    return task_status(root, task_id)


def release_task(root: Path, task_id: str, executor: str) -> dict[str, Any]:
    directory = _directory(root, task_id)
    with project_write_lock(directory.parent):
        state = read_json(directory / "state.json")
        if state.get("executor") != executor or state["state"] == "imported":
            raise ValueError("Only the current executor may release an unfinished task")
        write_json(directory / "state.json", {"state": "pending", "executor": None})
    return task_status(root, task_id)


def receive_task(root: Path, task_id: str, result: Path | None = None,
                 confirm_visual_review: bool = False) -> dict[str, Any]:
    directory = _directory(root, task_id)
    with project_write_lock(directory.parent):
        task = _read_task(directory)
        state = read_json(directory / "state.json")
        incoming = result or directory / "result.json"
        digest = sha256_file(incoming)
        if state["state"] == "imported":
            if digest != state["result_sha256"]:
                raise ValueError("Imported task result cannot be replaced; create a revision task")
            return {**task_status(root, task_id), "replayed": True}
        if state["state"] != "claimed":
            raise ValueError("Claim the task with actual execution provenance before importing")
        if task["context"] != context_snapshot(root):
            raise ValueError("Task context changed; create a fresh task")
        config = load_project(root)
        if sha256_file(config.source(root)) != task["source_sha256"]:
            raise ValueError("Task source changed")
        for name, expected in task["inputs"].items():
            path = root / name
            path.resolve().relative_to(root.resolve())
            if not path.is_file() or sha256_file(path) != expected:
                raise ValueError("Task input changed: " + name)
        for name, expected in task["instructions"].items():
            path = directory / "instructions" / name
            path.resolve().relative_to(directory.resolve())
            if not path.is_file() or sha256_file(path) != expected:
                raise ValueError("Task instructions changed: " + name)
        saved = directory / "result.json"
        if saved.exists() and sha256_file(saved) != digest:
            atomic_write_bytes(directory / f"previous-{sha256_file(saved)}.json", saved.read_bytes())
        atomic_write_bytes(saved, incoming.read_bytes())
        stage = task["stage"]
        if stage in {"source-review", "transcribe", "asset-audit"}:
            response = read_json(saved)
            if response.get("packet_id") != task["packet"]["packet_id"]:
                raise ValueError("Result belongs to another task's domain packet")
            if stage == "source-review" and task["pages"]:
                from littrans.extractor import parse_page_spec
                allowed = set(parse_page_spec(task["pages"], config.source_pages))
                if any(item.get("page") not in allowed for item in response.get("pages", [])):
                    raise ValueError("Source review decides pages outside the assigned scope")
        outcome: Any
        if stage in {"translate", "revise"}:
            from littrans.batching import load_manifest
            from littrans.evidence import translation_unit_fingerprint
            from littrans.models import SourceUnit
            from littrans.quality import run_qa
            from littrans.translation import submit_translation
            selected = set(task["source_bindings"])
            current = {unit.unit_id: translation_unit_fingerprint(unit, None)
                       for unit in read_jsonl(root / "derived/units.jsonl", SourceUnit)
                       if unit.unit_id in selected}
            if current != task["source_bindings"] or load_manifest(root, task["batch_ids"][0]).model_dump(mode="json") != task["batch_binding"]:
                raise ValueError("Task source or batch scope changed; create a fresh task")
            submit_translation(root, task["batch_ids"][0], saved)
            outcome = run_qa(root, task["batch_ids"][0]).model_dump(mode="json")
        elif stage == "audit":
            from littrans.workflow import import_review_set
            outcome = import_review_set(root, root / task["domain_manifest"], saved)
        elif stage == "source-review":
            from littrans.fidelity import import_source_review
            outcome = import_source_review(root, saved, confirm_visual_review)
        elif stage == "transcribe":
            from littrans.representations import submit_candidates
            outcome = submit_candidates(root, saved)
        elif stage == "asset-audit":
            from littrans.representations import import_asset_review
            outcome = import_asset_review(root, saved, confirm_visual_review)
        else:
            proposal = read_json(saved)
            required = {"findings", "proposed_rules", "unresolved"} if stage == "scout" else {"proposals", "unresolved"}
            if not required <= proposal.keys() or any(not isinstance(proposal[key], list) for key in required):
                raise ValueError("Proposal requires list fields: " + ", ".join(sorted(required)))
            outcome = {"proposal_received": True, "approved": False}
        write_json(directory / "state.json", {**state, "state": "imported",
                                              "result_sha256": digest, "outcome": outcome})
    return task_status(root, task_id)
