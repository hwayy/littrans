"""Bound, blind host rechecks and coordinator decisions; never provider calls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from littrans.models import (
    ExternalReviewRun,
    IssueStatus,
    ProjectConfig,
    ReviewIssue,
    Severity,
    utc_now,
)
from littrans.storage import (
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    sha256_text,
    write_json,
    write_jsonl,
)


def migrate_external_config(root: Path, apply: bool = False) -> dict[str, Any]:
    """Retained command entry: v7 has no in-place legacy configuration migration."""
    load_project(root)  # Reject unsupported manifests without changing their bytes.
    return {"changed": False, "applied": False,
            "next_actions": ["config show PROJECT", "config apply PROJECT CANDIDATE --dry-run"]}


def _binding(
    root: Path, run: ExternalReviewRun, *, project_config: ProjectConfig | None = None
) -> str:
    project_config = project_config or load_project(root)
    config = project_config.external_review
    assert config is not None
    return sha256_text(
        json.dumps(
            {"run": run.model_dump(mode="json"), "recheck": config.recheck.model_dump(mode="json")},
            sort_keys=True,
        )
    )


def _directory(root: Path, run: ExternalReviewRun) -> Path:
    return root / "reviews/external-rechecks" / _binding(root, run)


def _current_run(root: Path, batch_id: str) -> ExternalReviewRun:
    from littrans.external_review import external_review_status

    status = external_review_status(root, batch_id)
    if status["primary"] is None:
        raise ValueError("A current external CLI review is required")
    run = ExternalReviewRun.model_validate(status["primary"])
    if not run.success or not run.model_verified:
        raise ValueError("Recheck cannot replace a failed or unverified external CLI call")
    return run


def recheck_status(
    root: Path, run: ExternalReviewRun, *, project_config: ProjectConfig | None = None
) -> dict[str, Any]:
    project_config = project_config or load_project(root)
    from littrans.external_review import _is_cli_run, _needs_recheck, _recheck_unit_ids

    required = bool(
        _is_cli_run(run) and run.success and run.model_verified and _needs_recheck(root, run, project_config=project_config)
    )
    state: dict[str, Any] = {
        "required": required,
        "complete": not required,
        "unit_ids": _recheck_unit_ids(root, run, project_config=project_config) if required else [],
        "result": None,
        "adjudication": None,
        "verdict": run.verdict.value,
    }
    if not required:
        return state
    binding = _binding(root, run, project_config=project_config)
    directory = root / "reviews/external-rechecks" / binding
    receipt = (
        read_json(directory / "receipt.json") if (directory / "receipt.json").exists() else None
    )
    decision = (
        read_json(directory / "decision.json") if (directory / "decision.json").exists() else None
    )
    if receipt and receipt.get("binding") == binding:
        state["result"] = receipt
        state["decision_template"] = {
            "run_id": run.run_id,
            "task_id": receipt["task_id"],
            "verdict": "inconclusive",
            "reason": "",
            "issues": {
                issue_id: {"action": "inconclusive", "reason": ""}
                for issue_id in [
                    *run.issue_ids,
                    *[issue["issue_id"] for issue in receipt.get("findings", [])],
                ]
            },
        }
        if decision and decision.get("receipt_sha256") == sha256_text(
            json.dumps(receipt, sort_keys=True)
        ):
            state["adjudication"] = decision
            state["verdict"] = decision["verdict"]
            ledger_ids = {
                issue.issue_id
                for issue in read_jsonl(
                    root / "reviews" / f"{run.batch_id}.issues.jsonl", ReviewIssue
                )
            }
            required_ids = set(run.issue_ids) | {
                issue["issue_id"] for issue in receipt.get("findings", [])
            }
            state["complete"] = decision["verdict"] != "inconclusive" and required_ids <= ledger_ids
    return state


def create_recheck_packet(root: Path, batch_id: str) -> dict[str, Any]:
    from littrans.external_review import _outer_seam_context_ids, _packet_text, _render_packet

    with project_write_lock(root):
        run = _current_run(root, batch_id)
        state = recheck_status(root, run)
        if not state["required"] or state["complete"]:
            raise ValueError("No pending external recheck")
        units = state["unit_ids"]
        text, pages = _packet_text(
            root,
            batch_id,
            units,
            read_only_context_ids=_outer_seam_context_ids(root, batch_id, units),
        )
        directory = _directory(root, run)
        # Only the packet is given to the worker; this binding remains coordinator-side.
        write_json(
            directory / "binding.json",
            {
                "binding": _binding(root, run),
                "run_id": run.run_id,
                "issue_ids": run.issue_ids,
                "translation_fingerprint": run.translation_fingerprint,
            },
        )
        packet = _render_packet(root, directory / "packet", text, pages)
        from littrans.storage import sha256_file

        files = {
            str(i): page.relative_to(root).as_posix()
            for i, page in enumerate(sorted((packet.parent / "pages").glob("*.png")))
        }
        return {
            "packet_path": str(packet.resolve()),
            "unit_ids": units,
            "files": files,
            "packet_id": directory.name,
            "packet_sha256": sha256_file(packet),
            "response_format": "JSON object with verdict, summary, issues; use the external-review result schema",
        }


def receive_recheck(root: Path, task: dict[str, Any], result: Path) -> dict[str, Any]:
    from littrans.external_review import (
        _convert_issues,
        _evidence_map,
        _validate_issue_evidence,
        _validate_result,
    )

    with project_write_lock(root):
        run = _current_run(root, task["batch_ids"][0])
        binding = _binding(root, run)
        if task["packet"]["packet_id"] != binding:
            raise ValueError("External recheck became stale; create a fresh task")
        payload = _validate_result(read_json(result))
        _validate_issue_evidence(
            payload, _evidence_map(root, run.batch_id, covered_unit_ids=task["packet"]["unit_ids"])
        )
        config = load_project(root).external_review
        assert config is not None
        reviewer = config.reviewer.model_copy(update={"id": "host-recheck"})
        findings = _convert_issues(
            run.batch_id, reviewer, None, run.translation_fingerprint, run.run_id, payload
        )
        receipt = {
            "findings": [
                issue.model_copy(update={"reviewer": "host-recheck"}).model_dump(mode="json")
                for issue in findings
            ],
            "binding": binding,
            "run_id": run.run_id,
            "task_id": task["task_id"],
            "translation_fingerprint": run.translation_fingerprint,
            "result": payload,
        }
        path = _directory(root, run) / "receipt.json"
        if path.exists() and read_json(path) != receipt:
            raise ValueError("A different recheck result already exists")
        write_json(path, receipt)
        return {"received": True, "adjudication_required": True, "binding": binding}


def adjudicate_recheck(root: Path, batch_id: str, input_path: Path) -> dict[str, Any]:
    """Persist an explicit coordinator decision, never infer agreement from empty findings."""
    from littrans.external_review import external_review_status

    with project_write_lock(root):
        run = _current_run(root, batch_id)
        state = recheck_status(root, run)
        receipt = state["result"]
        if receipt is None:
            raise ValueError("A current independent recheck result is required")
        decision = read_json(input_path)
        if decision.get("run_id") != run.run_id or decision.get("task_id") != receipt["task_id"]:
            raise ValueError("Decision must reference both external run_id and recheck task_id")
        verdict = decision.get("verdict")
        if verdict not in {"accepted", "changes-requested", "inconclusive"}:
            raise ValueError("Invalid adjudication verdict")
        if not isinstance(decision.get("reason"), str) or not decision["reason"].strip():
            raise ValueError("An evidence-based adjudication reason is required")
        findings = [ReviewIssue.model_validate(issue) for issue in receipt["findings"]]
        path = root / "reviews" / f"{batch_id}.issues.jsonl"
        existing = read_jsonl(path, ReviewIssue)
        by_id = {issue.issue_id: issue for issue in existing}
        for issue in findings:
            by_id.setdefault(issue.issue_id, issue)
        required_ids = set(run.issue_ids) | {issue.issue_id for issue in findings}
        if not required_ids <= by_id.keys():
            raise ValueError("Missing original external findings")
        decisions = decision.get("issues", {})
        if not isinstance(decisions, dict) or set(decisions) != required_ids:
            raise ValueError(f"Decision issues must cover exactly {sorted(required_ids)}")
        for issue_id, choice in decisions.items():
            if (
                not isinstance(choice, dict)
                or choice.get("action") not in {"accept", "reject", "inconclusive"}
                or not isinstance(choice.get("reason"), str)
                or not choice["reason"].strip()
            ):
                raise ValueError(
                    "Each issue requires accept/reject/inconclusive and an evidence-based reason"
                )
            issue = by_id[issue_id]
            action = choice["action"]
            if action == "reject":
                by_id[issue_id] = issue.model_copy(
                    update={
                        "status": IssueStatus.REJECTED,
                        "resolution": choice["reason"],
                        "resolved_at": utc_now(),
                    }
                )
            elif action == "inconclusive" or issue.status is not IssueStatus.RESOLVED:
                by_id[issue_id] = issue.model_copy(
                    update={"status": IssueStatus.OPEN, "resolution": None, "resolved_at": None}
                )
            if verdict != "inconclusive" and action == "inconclusive":
                raise ValueError("Unresolved conflicts require an inconclusive verdict")
        if verdict == "accepted" and any(
            by_id[i].status is IssueStatus.OPEN and by_id[i].severity is not Severity.SUGGESTION
            for i in required_ids
        ):
            raise ValueError("Accepted adjudication cannot leave substantive findings open")
        decision = {
            **decision,
            "receipt_sha256": sha256_text(json.dumps(receipt, sort_keys=True)),
            "binding": _binding(root, run),
            "decided_at": utc_now(),
        }
        from littrans.storage import restore_files, snapshot_files

        decision_path = _directory(root, run) / "decision.json"
        snapshots = snapshot_files([path, decision_path])
        try:
            write_jsonl(path, list(by_id.values()))
            write_json(decision_path, decision)
        except BaseException:
            restore_files(snapshots)
            raise
        return external_review_status(root, batch_id)
