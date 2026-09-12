"""Independent, evidence-bound structured representations of immutable source assets.

Candidate import never grants mathematical correctness. A separate visual reviewer
must examine the original and the actual rendered candidate in the audit packet.
The original remains the reader's fallback, including when the browser renderer fails.
"""
from __future__ import annotations

import hashlib
import html
import inspect
import json
import os
import re
import shutil
import tempfile
from collections import Counter
from importlib.resources import files
from pathlib import Path
from typing import Any

from littrans.models import SourceUnit
from littrans.representation_models import AssetReviewSubmission, AssetSubmission
from littrans.storage import (
    atomic_write_text,
    load_project,
    project_write_lock,
    read_json,
    read_jsonl,
    write_json,
)

ASSET_RE = re.compile(r"\{\{asset:([A-Za-z0-9][A-Za-z0-9._-]*)\}\}")
FORMAT_TYPES = {"latex", "table", "code", "text"}
ASSET_FORMATS = {"math": "latex", "table": "table", "code": "code"}
PROMPT_VERSION = "fidelity-contextual-representations-0.6.0-2"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _data(value: Any) -> dict[str, Any]:
    return value.model_dump(mode="json") if hasattr(value, "model_dump") else dict(value)


def _directory(root: Path) -> Path:
    return root / "evidence" / "representations"


def _assets(root: Path) -> dict[str, dict[str, Any]]:
    from littrans.fidelity_models import load_assets

    return {key: _data(value) for key, value in load_assets(root).items()}


def _local(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Asset evidence must stay inside its project")
    return path


def _asset_fingerprint(root: Path, asset: dict[str, Any]) -> str:
    evidence: dict[str, str] = {}
    for fragment in asset["fragments"]:
        for field in ("png_path", "svg_path", "pdf_path"):
            name = fragment.get(field)
            if name:
                evidence[name] = hashlib.sha256(_local(root, name).read_bytes()).hexdigest()
                expected = fragment.get("file_sha256", {}).get(name)
                if expected and expected != evidence[name]:
                    raise ValueError("Original asset file hash mismatch: " + name)
    if not evidence or not any(fragment.get("png_path") for fragment in asset["fragments"]):
        raise ValueError(f"Original image evidence is missing for asset {asset['id']}")
    return _hash({"asset": asset, "files": evidence})


def _require_format(asset: dict[str, Any], candidate: dict[str, Any] | None = None) -> None:
    expected = ASSET_FORMATS.get(asset["kind"])
    if expected is None:
        raise ValueError("Asset is not representable; classify through source review first: " + asset["id"])
    if candidate is not None and candidate.get("format") != expected:
        raise ValueError(f"Candidate format must be {expected} for {asset['kind']} asset {asset['id']}")


def _verify_render_artifact(root: Path, packet: dict[str, Any]) -> None:
    manifest = packet.get("render_manifest")
    if not manifest or _hash(manifest) != packet.get("render_manifest_sha256"):
        raise ValueError("Render manifest missing or modified; build a fresh asset-audit packet")
    artifact = packet["render_artifact"]
    expected_path = _directory(root) / "renders" / packet["packet_id"] / "comparison.html"
    if _local(root, artifact["path"]) != expected_path.resolve():
        raise ValueError("Render artifact path mismatch")
    if manifest.get("comparison.html") != artifact["sha256"]:
        raise ValueError("Render artifact manifest mismatch")
    folder = expected_path.parent
    for relative, digest in manifest.items():
        path = _local(folder, relative)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("Render dependency missing or modified: " + relative)
    if packet["renderer_sha256"] != _runtime_fingerprint():
        raise ValueError("Candidate render artifact is stale")


def _index(root: Path) -> dict[str, Any]:
    path = _directory(root) / "index.json"
    return read_json(path) if path.exists() else {"candidates": {}, "reviews": {}}


def _immutable(path: Path, payload: dict[str, Any]) -> None:
    if path.exists():
        if read_json(path) != payload:
            raise ValueError("Conflicting immutable evidence: " + str(path))
    else:
        write_json(path, payload)


def _context(root: Path, units: list[Any] | None, asset_ids: set[str]) -> list[dict[str, Any]]:
    if units is None:
        units = read_jsonl(root / "derived" / "units.jsonl", SourceUnit)
        units = [unit for unit in units if asset_ids.intersection(
            ASSET_RE.findall(unit.source_markdown or unit.source_text))]
    return [_data(unit) for unit in units]


def _context_documents(root: Path) -> dict[str, str]:
    names = ("context/document-brief.md", "context/style-guide.md", "context/terminology.yaml",
             "glossary/approved.yaml", "document-brief.md", "style-guide.md")
    return {name: (root / name).read_text(encoding="utf-8") for name in names
            if (root / name).is_file()}


def _image_receipt(packet: dict[str, Any], payload: dict[str, Any]) -> None:
    receipt = payload.get("image_evidence", {})
    if not isinstance(receipt, dict) or any(receipt.get(path) != digest
                                          for path, digest in packet["required_images"].items()):
        raise ValueError("Missing or stale original-image viewing receipt")


def _packet_current(root: Path, packet: dict[str, Any]) -> None:
    assets = _assets(root)
    for key, expected in packet["asset_fingerprints"].items():
        if key not in assets or _asset_fingerprint(root, assets[key]) != expected:
            raise ValueError("Stale original asset evidence: " + key)
    current = {unit.unit_id: _data(unit) for unit in read_jsonl(
        root / "derived" / "units.jsonl", SourceUnit)}
    for unit in packet["context_units"]:
        key = unit["unit_id"]
        if key not in current or _hash(current[key]) != _hash(unit):
            raise ValueError("Stale source context: " + key)
    if packet["context_documents"] != _context_documents(root):
        raise ValueError("Stale project context documents")


def _revision_context(root: Path, asset_ids: list[str], fingerprints: dict[str, str]) -> dict[str, Any]:
    idx = _index(root)
    result: dict[str, Any] = {}
    for key in asset_ids:
        sha = idx["candidates"].get(key)
        if not sha:
            raise ValueError("A revision requires an existing candidate: " + key)
        candidate = read_json(_directory(root) / "candidates" / f"{sha}.json")
        if candidate["asset_fingerprint"] != fingerprints[key]:
            raise ValueError("A revision cannot reuse stale original evidence: " + key)
        review_packet_id = idx["reviews"].get(sha)
        review = _load_review(root, review_packet_id) if review_packet_id else None
        decision = next(item for item in review["decisions"] if item["asset_id"] == key) if review else None
        result[key] = {"candidate_sha256": sha, "candidate": candidate,
                       "review_packet_id": review_packet_id,
                       "review_sha256": review["review_sha256"] if review else None,
                       "review_decision": decision}
    return result


def build_asset_packet(root: Path, asset_ids: list[str], stage: str = "transcribe",
                       context_units: list[Any] | None = None,
                       revision_notes: str | None = None) -> dict[str, Any]:
    """Persist a reproducible source-only or independent asset-review work packet."""
    if stage not in {"transcribe", "asset-audit"}:
        raise ValueError("Asset packet stage must be transcribe or asset-audit")
    if not asset_ids or len(asset_ids) != len(set(asset_ids)):
        raise ValueError("Asset IDs must be nonempty and unique")
    if revision_notes is not None and (stage != "transcribe" or not revision_notes.strip()):
        raise ValueError("Nonempty revision_notes are supported only for transcribe packets")
    with project_write_lock(root):
        assets = _assets(root)
        missing = set(asset_ids) - assets.keys()
        if missing:
            raise ValueError(f"Unknown asset IDs: {sorted(missing)}")
        for key in asset_ids:
            _require_format(assets[key])
        from littrans.hosts import resolve_coordination_host

        host = resolve_coordination_host(None)
        profile = load_project(root).agent_models.get(host, {})
        if stage == "transcribe" and (not profile.get("transcribe", "").strip()
                                      or not profile.get("reasoning_effort", "").strip()):
            raise ValueError(f"Configure agent_models.{host}.transcribe and reasoning_effort "
                             "before dispatching transcription; model substitution is not automatic")
        payload: dict[str, Any] = {
            "stage": stage, "host": host,
            "model": profile.get("transcribe" if stage == "transcribe" else "asset-audit"),
            "reasoning_effort": profile.get("reasoning_effort"),
            "fresh_context": True, "prompt_version": PROMPT_VERSION,
            "allowed_formats": {key: ASSET_FORMATS[assets[key]["kind"]] for key in asset_ids},
            "asset_ids": asset_ids, "assets": [assets[key] for key in asset_ids],
            "asset_fingerprints": {key: _asset_fingerprint(root, assets[key]) for key in asset_ids},
            "context_units": _context(root, context_units, set(asset_ids)),
            "context_documents": _context_documents(root),
            "instructions": (
                "View every original fragment and its source-page/context evidence. Transcribe only "
                "what is present, preserving equation structure, signs, scope, indices and accents. "
                "Do not repair presumed mathematical mistakes. Supply one record per asset. "
                "If unclear, return status=unresolved and explain. Do not translate or use any "
                "other task's answer. Use the asset-specific allowed_formats mapping. Figure and mixed-region internals remain images."
                if stage == "transcribe" else
                "Independently compare every original with the rendered candidate. Inspect all "
                "symbols, subscripts, accents, signs, scope, multiline and matrix structure. "
                "Compilation and similarity are not correctness. Accept only after visually "
                "examining a successful actual render; otherwise reject or leave unresolved. "
                "Record mathematical interpretation uncertainty separately."
            ),
        }
        image_paths = {fragment["png_path"] for asset in payload["assets"] for fragment in asset["fragments"]}
        for page in {unit["page"] for unit in payload["context_units"]}:
            path = f"evidence/pages/fidelity-p{page:04d}.png"
            if (root / path).is_file():
                image_paths.add(path)
        payload["required_images"] = {path: hashlib.sha256(_local(root, path).read_bytes()).hexdigest()
                                      for path in sorted(image_paths)}
        if revision_notes is not None:
            payload["revision_notes"] = revision_notes.strip()
            payload["revision_context"] = _revision_context(root, asset_ids, payload["asset_fingerprints"])
            payload["instructions"] = (
                "Revise the supplied earlier candidate against the ORIGINAL images and source context. "
                "Address revision_notes and the independent review feedback. The prior candidate and "
                "feedback are unverified claims, never authoritative source. Preserve mathematical "
                "content and structure exactly; do not correct presumed source mistakes. Return one "
                "candidate or unresolved result per requested asset. Do not translate. Every revised "
                "candidate needs a fresh independent visual review; prior acceptance does not carry over."
            )
        if stage == "asset-audit":
            idx = _index(root)
            payload["candidates"] = {}
            for key in asset_ids:
                sha = idx["candidates"].get(key)
                if not sha:
                    raise ValueError("No candidate to review: " + key)
                candidate = read_json(_directory(root) / "candidates" / f"{sha}.json")
                if candidate["asset_fingerprint"] != payload["asset_fingerprints"][key]:
                    raise ValueError("Stale candidate: " + key)
                _require_format(assets[key], candidate)
                payload["candidates"][key] = candidate
            payload["renderer_sha256"] = _runtime_fingerprint()
        if stage == "asset-audit":
            renders = _directory(root) / "renders"
            renders.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="building-", dir=renders) as temporary:
                folder = Path(temporary)
                install_mathjax(folder)
                atomic_write_text(folder / "comparison.html", _review_html(root, folder, payload))
                manifest = {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in sorted(folder.rglob("*")) if path.is_file()}
                payload["render_manifest"] = manifest
                payload["render_manifest_sha256"] = _hash(manifest)
                while True:
                    packet_id = _hash({k: v for k, v in payload.items()
                                       if k not in {"packet_id", "render_artifact"}})
                    payload["packet_id"] = packet_id
                    final = renders / packet_id
                    payload["render_artifact"] = {
                        "path": (final / "comparison.html").relative_to(root).as_posix(),
                        "sha256": manifest["comparison.html"],
                    }
                    if not final.exists():
                        shutil.copytree(folder, final)
                        break
                    try:
                        _verify_render_artifact(root, payload)
                        break
                    except (ValueError, OSError):
                        # New identity requires a new review; never repair bytes behind old receipts.
                        payload["previous_render_packet_id"] = packet_id

        else:
            packet_id = _hash(payload)
            payload["packet_id"] = packet_id
        _immutable(_directory(root) / "packets" / f"{packet_id}.json", payload)
        return payload


def _load_review(root: Path, packet_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", packet_id):
        raise ValueError("Invalid review packet identity")
    record = read_json(_directory(root) / "reviews" / f"{packet_id}.json")
    if not isinstance(record, dict):
        raise ValueError("Invalid stored review")
    payload = {key: value for key, value in record.items() if key != "review_sha256"}
    if record.get("review_sha256") != _hash(payload):
        raise ValueError("Stored review digest mismatch; review evidence is corrupt")
    AssetReviewSubmission.model_validate(payload)
    if payload["packet_id"] != packet_id:
        raise ValueError("Stored review packet mismatch")
    return record


def _load_packet(root: Path, packet_id: str, stage: str) -> dict[str, Any]:
    if not re.fullmatch(r"[a-f0-9]{64}", packet_id):
        raise ValueError("Invalid packet ID")
    packet = read_json(_directory(root) / "packets" / f"{packet_id}.json")
    if _hash({key: value for key, value in packet.items()
              if key not in {"packet_id", "render_artifact"}}) != packet_id:
        raise ValueError("Packet evidence was modified")
    if packet["stage"] != stage:
        raise ValueError("Wrong packet stage")
    _packet_current(root, packet)
    return packet


def candidate_validation(format_name: str, content: Any) -> list[str]:
    """Conservative syntactic safety; success does not certify mathematical fidelity."""
    errors: list[str] = []
    if format_name not in FORMAT_TYPES:
        return ["unsupported-format"]
    if format_name == "table":
        if not (isinstance(content, dict) and isinstance(content.get("rows"), list)
                and content["rows"] and all(isinstance(row, list) and row for row in content["rows"])
                and len({len(row) for row in content["rows"]}) == 1
                and all(isinstance(cell, str) for row in content["rows"] for cell in row)):
            errors.append("invalid-table-structure")
        return errors
    if not isinstance(content, str) or not content.strip():
        return ["empty-or-nontext-content"]
    if format_name != "latex":
        return errors
    if re.search(r"\\(?:href|url|html\w*|include\w*|input|write\w*|read|open\w*|close\w*|"
                 r"usepackage|documentclass|require|autoload|def|gdef|edef|xdef|let|futurelet|"
                 r"csname|catcode|newcommand|renewcommand|setoptions|special|style|class|cssId)\b",
                 content, re.I):
        errors.append("unsafe-or-unsupported-latex-command")
    if "$" in content or re.search(r"\\(?:begin|end)\{document\}", content):
        errors.append("latex-must-be-math-body")
    depth = 0
    for token in re.findall(r"\\.|[{}]", content):
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        if depth < 0:
            break
    if depth:
        errors.append("unbalanced-latex-braces")
    # Literal newlines disappear in TeX. Flag multiple fresh equations without an
    # explicit row delimiter; do not silently insert it and call the model correct.
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) > 1 and "\\\\" not in content and sum(
        "=" in line and not line.startswith(("=", "&", r"\approx", r"\sim")) for line in lines
    ) > 1:
        errors.append("ambiguous-multiline-equations-without-row-break")
    return errors


def submit_candidates(root: Path, input_file: Path) -> dict[str, Any]:
    payload = AssetSubmission.model_validate(read_json(input_file)).model_dump(mode="json")
    with project_write_lock(root):
        packet = _load_packet(root, str(payload.get("packet_id", "")), "transcribe")
        _image_receipt(packet, payload)
        author = payload.get("author_task_id")
        if not isinstance(author, str) or not author.strip():
            raise ValueError("Candidate author_task_id is required")
        if not payload.get("model"):
            raise ValueError("Record the actual candidate model")
        if ((packet["model"] and payload.get("model") != packet["model"])
                or (packet["reasoning_effort"] and payload.get("reasoning_effort") != packet["reasoning_effort"])):
            raise ValueError("Candidate model/effort must match the dispatched packet")
        records = payload.get("candidates", [])
        if not isinstance(records, list) or Counter(item["asset_id"] for item in records) != Counter(packet["asset_ids"]):
            raise ValueError("Candidate coverage must match packet assets exactly, once each")
        assets = _assets(root)
        for item in records:
            _require_format(assets[item["asset_id"]], item)
        response_sha = _hash(payload)
        response_path = _directory(root) / "responses" / f"{packet['packet_id']}.json"
        if response_path.exists():
            previous = read_json(response_path)
            if previous["response_sha256"] != response_sha:
                raise ValueError("Successful response already cached for this input; do not repeat paid generation")
            idx = _index(root)
            # Recover an interrupted index publication, but never roll a newer
            # revision back when somebody replays a historical cached response.
            for key, sha in previous["result"]["candidate_sha256"].items():
                prior = packet.get("revision_context", {}).get(key, {}).get("candidate_sha256")
                if key not in idx["candidates"] or (prior is not None and idx["candidates"][key] == prior):
                    idx["candidates"][key] = sha
            write_json(_directory(root) / "index.json", idx)
            return {**previous["result"], "replayed": True}
        if "revision_context" in packet and packet["revision_context"] != _revision_context(
                root, packet["asset_ids"], packet["asset_fingerprints"]):
            raise ValueError("Revision packet is stale for the current candidate or review feedback")
        prepared: list[dict[str, Any]] = []
        for item in records:
            key = item["asset_id"]
            if item.get("status", "candidate") not in {"candidate", "unresolved"}:
                raise ValueError("Candidate status must be candidate or unresolved")
            record = {
                "asset_id": key, "asset_fingerprint": packet["asset_fingerprints"][key],
                "packet_id": packet["packet_id"], "author_task_id": author,
                "model": payload["model"], "reasoning_effort": payload.get("reasoning_effort"),
                "format": item.get("format", "latex"), "content": item.get("content", ""),
                "status": item.get("status", "candidate"),
                "notes": item.get("notes", ""),
                "semantic_uncertainty": item.get("semantic_uncertainty", ""),
                "usage": payload.get("usage"),
            }
            if "revision_context" in packet:
                prior = packet["revision_context"][key]
                record["revision_of"] = {"candidate_sha256": prior["candidate_sha256"],
                                         "review_sha256": prior["review_sha256"],
                                         "revision_notes": packet["revision_notes"]}
            record["validation_errors"] = candidate_validation(record["format"], record["content"])
            record["candidate_sha256"] = _hash(record)
            prepared.append(record)
        idx = _index(root)
        for record in prepared:
            sha = record["candidate_sha256"]
            _immutable(_directory(root) / "candidates" / f"{sha}.json", record)
            idx["candidates"][record["asset_id"]] = sha
        result = {"packet_id": packet["packet_id"], "candidate_count": len(prepared),
                  "candidate_sha256": {item["asset_id"]: item["candidate_sha256"] for item in prepared},
                  "usage": payload.get("usage"), "replayed": False}
        # Content-addressed leaves first, one atomic index last. A crash before
        # indexing leaves reusable evidence, never a partially accepted candidate.
        _immutable(response_path, {"response_sha256": response_sha, "response": payload, "result": result})
        write_json(_directory(root) / "index.json", idx)
        return result


def import_asset_review(root: Path, input_file: Path, confirm_visual_review: bool = False) -> dict[str, Any]:
    if not confirm_visual_review:
        raise ValueError("Explicit confirmation of actual visual review is required")
    payload = AssetReviewSubmission.model_validate(read_json(input_file)).model_dump(mode="json")
    with project_write_lock(root):
        packet = _load_packet(root, str(payload.get("packet_id", "")), "asset-audit")
        _image_receipt(packet, payload)
        reviewer = payload.get("reviewer_task_id")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise ValueError("Independent reviewer_task_id is required")
        artifact = packet["render_artifact"]
        _verify_render_artifact(root, packet)
        if (payload.get("render_artifact_sha256") != artifact["sha256"]
                or payload.get("render_manifest_sha256") != packet["render_manifest_sha256"]):
            raise ValueError("Review must identify the rendered artifact and manifest it examined")
        decisions = payload.get("decisions", [])
        if not isinstance(decisions, list) or Counter(item["asset_id"] for item in decisions) != Counter(packet["asset_ids"]):
            raise ValueError("Review coverage must match the packet exactly")
        idx = _index(root)
        for decision in decisions:
            key = decision["asset_id"]
            candidate = packet["candidates"][key]
            _require_format(_assets(root)[key], candidate)
            sha = candidate["candidate_sha256"]
            if reviewer == candidate["author_task_id"]:
                raise ValueError("Self-review cannot verify an asset candidate")
            if decision.get("candidate_sha256") != sha or idx["candidates"].get(key) != sha:
                raise ValueError("Review targets a stale candidate: " + key)
            if decision.get("verdict") not in {"accept", "reject", "unresolved"}:
                raise ValueError("Invalid asset review verdict")
            if decision.get("visual_checked") is not True:
                raise ValueError("Original-image visual inspection is required")
            if decision["verdict"] == "accept" and (
                decision.get("render_checked") is not True
                or candidate["validation_errors"] or candidate["status"] == "unresolved"
                or decision.get("semantic_uncertainty")
            ):
                raise ValueError("Acceptance requires a valid, actually rendered and visually verified candidate")
        review_sha = _hash(payload)
        path = _directory(root) / "reviews" / f"{packet['packet_id']}.json"
        if path.exists():
            previous = _load_review(root, packet["packet_id"])
            if previous["review_sha256"] != review_sha:
                raise ValueError("Conflicting replay of an immutable asset review")
            for decision in decisions:
                idx["reviews"][decision["candidate_sha256"]] = packet["packet_id"]
            write_json(_directory(root) / "index.json", idx)
            return {"review_sha256": review_sha, "reviewed": len(decisions), "replayed": True}
        record = {"review_sha256": review_sha, **payload}
        _immutable(path, record)
        for decision in decisions:
            idx["reviews"][decision["candidate_sha256"]] = packet["packet_id"]
        write_json(_directory(root) / "index.json", idx)
        return {"review_sha256": review_sha, "reviewed": len(decisions), "replayed": False}


def representation_status(root: Path, asset_ids: list[str] | None = None) -> dict[str, Any]:
    assets = _assets(root)
    selected = list(assets) if asset_ids is None else asset_ids
    idx = _index(root)
    result: dict[str, Any] = {}
    for key in selected:
        if key not in assets:
            raise ValueError("Unknown asset: " + key)
        state: dict[str, Any] = {"state": "transcribe", "candidate_sha256": None,
                                 "semantic_uncertainty": ""}
        if assets[key].get("kind") not in ASSET_FORMATS:
            state["state"] = "fallback"
            result[key] = state
            continue
        sha = idx["candidates"].get(key)
        if sha:
            candidate = read_json(_directory(root) / "candidates" / f"{sha}.json")
            try:
                _require_format(assets[key], candidate)
                current = _asset_fingerprint(root, assets[key]) == candidate["asset_fingerprint"]
            except (OSError, ValueError):
                current = False
            if current:
                state.update(candidate_sha256=sha, state="asset-audit",
                             semantic_uncertainty=candidate["semantic_uncertainty"])
                if candidate["status"] == "unresolved" or candidate["validation_errors"]:
                    state["state"] = "fallback"
                review_packet = idx["reviews"].get(sha)
                if review_packet:
                    try:
                        review = _load_review(root, review_packet)
                        decisions = [item for item in review["decisions"]
                                     if item["asset_id"] == key and item["candidate_sha256"] == sha]
                        if len(decisions) != 1:
                            raise ValueError("Stored review candidate coverage mismatch")
                        decision = decisions[0]
                        state["state"] = "verified" if decision["verdict"] == "accept" else "fallback"
                        state["semantic_uncertainty"] = decision.get("semantic_uncertainty", "")
                        packet = _load_packet(root, review_packet, "asset-audit")
                        _verify_render_artifact(root, packet)
                        if (review.get("render_manifest_sha256") != packet["render_manifest_sha256"]
                                or review.get("render_artifact_sha256") != packet["render_artifact"]["sha256"]
                                or _hash({k: v for k, v in candidate.items() if k != "candidate_sha256"}) != sha):
                            state["state"] = "asset-audit"
                    except (OSError, KeyError, ValueError):
                        state["state"] = "asset-audit"
        result[key] = state
    return {"assets": result, "counts": dict(Counter(item["state"] for item in result.values()))}


def validate_asset_references(root: Path, source: str, target: str) -> list[dict[str, str]]:
    """Compare references per source block, preserving multiplicity but not order."""
    errors: list[dict[str, str]] = []
    if "{{asset:" not in source + target:
        return errors
    original, translated = Counter(ASSET_RE.findall(source)), Counter(ASSET_RE.findall(target))
    assets = _assets(root)
    unknown = (original.keys() | translated.keys()) - assets.keys()
    if unknown:
        errors.append({"code": "unknown-asset-reference", "message": f"Unknown assets: {sorted(unknown)}"})
    if original != translated:
        errors.append({"code": "asset-reference-mismatch", "message":
                       f"Source/target asset multiplicities differ: {dict(original)} / {dict(translated)}"})
    if "{{asset:" in ASSET_RE.sub("", source + target):
        errors.append({"code": "malformed-asset-reference", "message": "Malformed asset reference"})
    if original:
        # New structured math must go to the independent candidate registry.
        if re.search(r"(?<!\\)\$|\\\(|\\\[|\\(?:begin|frac|sqrt)\b", ASSET_RE.sub("", target)):
            errors.append({"code": "candidate-in-translation", "message": "Keep asset IDs in the translation; submit LaTeX separately"})
        status = representation_status(root, sorted(original.keys() - unknown))["assets"]
        for key in original.keys() - unknown:
            try:
                _asset_fingerprint(root, assets[key])
            except (OSError, ValueError):
                errors.append({"code": "asset-image-evidence-missing", "message": key})
            if status[key]["semantic_uncertainty"]:
                errors.append({"code": "asset-semantic-uncertainty", "message":
                               f"{key}: {status[key]['semantic_uncertainty']}"})
    return errors


def validate_asset_translations(root: Path, source: str, supplements: list[Any],
                                target_text: str = "", target_table: Any = None) -> list[dict[str, str]]:
    """Image-native prose/labels require their own translated companion, not a marker."""
    ids = set(ASSET_RE.findall(source))
    if not ids and not supplements:
        return []
    assets = _assets(root)
    errors: list[dict[str, str]] = []
    mapped: dict[str, dict[str, Any]] = {}
    for supplement in supplements:
        item = _data(supplement)
        key = item["asset_id"]
        if key not in ids or key in mapped:
            errors.append({"code": "asset-translation-mapping", "message":
                           f"Supplement must name one distinct asset in this source block: {key}"})
            continue
        mapped[key] = item
        if not item.get("language_present", True):
            if assets.get(key, {}).get("formula_conditions"):
                errors.append({"code": "formula-language-required", "message": "Declared formula conditions require a translated companion: " + key})
            if not item.get("notes", "").strip():
                errors.append({"code": "asset-language-attestation", "message":
                               "No-language claims require an explanation and independent technical review: " + key})
            continue
        texts = [item.get("target_text", "")]
        if item.get("target_table"):
            texts += [cell for row in item["target_table"]["rows"] for cell in row]
        labels = item.get("figure_labels", [])
        if len({label["source"] for label in labels}) != len(labels) or any(not label.get("target") for label in labels):
            errors.append({"code": "asset-label-translation", "message": "Missing or duplicate label mappings: " + key})
        texts += [label.get("target", "") for label in labels]
        if re.search(r"(?<!\\)\$|\\\(|\\\[|\\(?:begin|frac|sqrt)\b", "\n".join(texts)):
            errors.append({"code": "candidate-in-asset-translation", "message":
                           "Translated image companions must not bypass independent structured-expression review: " + key})
        body = ASSET_RE.sub("", "\n".join(texts)).strip()
        if not body or (load_project(root).target_language == "zh-CN" and not re.search(r"[\u3400-\u9fff]", body)):
            errors.append({"code": "asset-language-untranslated", "message": "Source image language needs a translated companion, or language_present=false with notes when the image holds only notation: " + key})
    for key in ids & assets.keys():
        if (assets[key]["kind"] in {"mixed-region", "table", "figure"} or assets[key].get("formula_conditions")) and key not in mapped:
            # Old structured table records remain expressible; image-only table
            # records must use the explicit per-asset language contract.
            if assets[key]["kind"] == "table" and target_table is not None:
                continue
            errors.append({"code": "asset-language-unaccounted", "message":
                           "Provide translated prose/cells/labels or an explained no-language attestation: " + key})
    return errors


def _runtime_fingerprint() -> str:
    return hashlib.sha256(files("littrans").joinpath("vendor", "mathjax", "manifest.json").read_bytes()
                          + mathjax_bootstrap().encode()
                          + "\n".join(inspect.getsource(fn) for fn in (
                              _asset_html, _original_html, _candidate_html, _review_html,
                          )).encode()).hexdigest()


def install_mathjax(output: Path) -> Path:
    """Copy only vendored, pinned runtime files. Never fetch from the reader."""
    source = Path(str(files("littrans").joinpath("vendor", "mathjax")))
    target = output / "mathjax"
    manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["files"].items():
        incoming = source / relative
        if hashlib.sha256(incoming.read_bytes()).hexdigest() != expected:
            raise ValueError("Vendored MathJax runtime fingerprint mismatch: " + relative)
        dest = target / relative
        if not dest.exists() or hashlib.sha256(dest.read_bytes()).hexdigest() != expected:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(incoming, dest)
    return target


def mathjax_bootstrap(base: str = "mathjax") -> str:
    # Every original stays visible until its own render succeeds. Unknown TeX,
    # failed dynamic loads, missing JS and browser exceptions all preserve it.
    base_json = json.dumps(base)
    return r'''<script>
(function(){
const base = new URL(BASE+'/',document.baseURI).href.replace(/\/$/,'');
window.MathJax = {
 loader:{paths:{mathjax:base, 'mathjax-newcm':base+'/mathjax-newcm'}},
 tex:{packages:{'[-]':['autoload','require','noundefined','configmacros']}, maxBuffer:20000,
  formatError:(_jax,error)=>{throw error;}},
 svg:{fontCache:'local',dynamicPrefix:base+'/mathjax-newcm/svg/dynamic'},
 output:{fontPath:base+'/mathjax-newcm'},
 options:{enableMenu:false,enableEnrichment:false,enableSpeech:false,enableBraille:false,enableExplorer:false,
  menuOptions:{settings:{enrich:false,collapsible:false,speech:false,braille:false,assistiveMml:false}}}, startup:{typeset:false,ready(){
  MathJax.startup.defaultReady();
  MathJax.startup.promise.then(async()=>{
   for(const box of document.querySelectorAll('[data-candidate-latex]')){
    try{
     const node=await MathJax.tex2svgPromise(box.dataset.candidateLatex,{display:box.dataset.display==='true'});
     if(node.querySelector('[data-mml-node="merror"],mjx-merror,[data-mjx-error]'))throw Error('TeX error');
     const out=box.querySelector('.asset-candidate');out.replaceChildren(node);out.hidden=false;
     box.dataset.renderStatus='passed';
     if(box.dataset.candidateVerified==='true')box.querySelector('.asset-original').hidden=true;
    }catch(error){box.dataset.renderStatus='failed';box.dataset.renderError=String(error);}
   }
  });
 }}
};
})();</script><script defer src="BASE_PATH/tex-svg.js"></script>'''.replace("BASE_PATH", html.escape(base, quote=True)).replace("BASE", base_json)


def _href(root: Path, name: str, output: Path) -> str:
    original = _local(root, name)
    digest = hashlib.sha256(original.read_bytes()).hexdigest()
    destination = output / "original-assets" / (digest + original.suffix.lower())
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
        shutil.copyfile(original, destination)
    return html.escape(Path(os.path.relpath(destination, output)).as_posix(), quote=True)


def _original_html(root: Path, asset: dict[str, Any], output: Path, *, linked: bool = False) -> str:
    rendered = []
    for fragment in asset["fragments"]:
        png, svg = fragment.get("png_path"), fragment.get("svg_path")
        if not png:
            raise ValueError("Missing original image: " + asset["id"])
        bbox = fragment.get("bbox", [0, 0, 30, 12])
        width = max(1, float(bbox[2]) - float(bbox[0]))
        height = max(1, float(bbox[3]) - float(bbox[1]))
        baseline = fragment.get("baseline")
        descent = max(0.0, height - float(baseline)) / 10 if baseline is not None else 0.2
        # Source dimensions are PDF points; em sizing retains inline proportions.
        image = f'<img src="{_href(root, svg or png, output)}" alt="原式 {html.escape(asset["id"])}" style="width:{width / 10:.3f}em;max-width:100%;height:auto;vertical-align:-{descent:.3f}em"'
        if svg:
            fallback = "this.onerror=null;this.src=" + json.dumps(html.unescape(_href(root, png, output))) + ";"
            image += f' onerror="{html.escape(fallback, quote=True)}"'
        image += ">"
        if linked:
            href = _href(root, fragment.get("pdf_path") or png, output)
            image = f'<a class="original-image-link" href="{href}" title="查看高清原式" aria-label="查看高清原式 {html.escape(asset["id"], quote=True)}">{image}</a>'
        rendered.append(image)
    return '<span class="asset-original">' + "".join(rendered) + "</span>"


def _candidate_html(candidate: dict[str, Any]) -> str:
    content, kind = candidate["content"], candidate["format"]
    if kind == "table":
        return "<table>" + "".join("<tr>" + "".join("<td>" + html.escape(cell) + "</td>" for cell in row) + "</tr>" for row in content["rows"]) + "</table>"
    if kind == "code":
        return "<pre><code>" + html.escape(str(content)) + "</code></pre>"
    return html.escape(str(content)).replace("\n", "<br>")


def _asset_html(root: Path, asset: dict[str, Any], output: Path,
                candidate: dict[str, Any] | None = None, verified: bool = False, *, originals_only: bool = False) -> str:
    key = html.escape(asset["id"], quote=True)
    original = _original_html(root, asset, output, linked=originals_only)
    # An inline formula whose precise glyph export fell back to a raw mixed
    # region is still inline reading content; only whole tables, code and
    # figures force block display regardless of the recorded flag.
    display = asset.get("display", False) or asset.get("kind") in {"table", "code", "figure"}
    attributes = f' class="fidelity-asset" data-asset-id="{key}" data-display="{str(display).lower()}"'
    label = "结构化表达已核验" if verified else "转写未完成／待核验"
    if originals_only:
        return "<span" + attributes + ' data-original-only="true">' + original + "</span>"
    content = original
    if candidate and not candidate["validation_errors"] and candidate["status"] != "unresolved":
        if candidate["format"] == "latex":
            attributes += f' data-candidate-latex="{html.escape(candidate["content"], quote=True)}" data-candidate-verified="{str(verified).lower()}"'
            content += '<span class="asset-candidate" hidden></span>'
        else:
            content += '<span class="asset-candidate">' + _candidate_html(candidate) + "</span>"
    originals = "".join(f'<a href="{_href(root, fragment.get("pdf_path") or fragment["png_path"], output)}">查看原式 {index + 1}</a> ' for index, fragment in enumerate(asset["fragments"]))
    return "<span" + attributes + ">" + content + f'<small class="asset-state">{label}</small><span class="asset-original-links">{originals}</span></span>'


def resolve_asset_html(root: Path, text: str, output: Path, *, originals_only: bool = False) -> str:
    if not ASSET_RE.search(text):
        return text
    assets = _assets(root)
    ids = ASSET_RE.findall(text)
    status = representation_status(root, ids)["assets"] if ids and not originals_only else {}

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in assets:
            raise ValueError("Unknown asset reference: " + key)
        candidate = None
        if not originals_only and status[key]["state"] == "verified":
            candidate = read_json(_directory(root) / "candidates" / f"{status[key]['candidate_sha256']}.json")
        return _asset_html(root, assets[key], output, candidate, candidate is not None, originals_only=originals_only)

    return ASSET_RE.sub(replace, text)


def _candidate_markdown(candidate: dict[str, Any]) -> str:
    content, kind = candidate["content"], candidate["format"]
    if kind == "table":
        def cell(value: str) -> str:
            return html.escape(value).replace("|", "&#124;").replace("\r\n", "\n").replace("\n", "<br>")
        rows = ["| " + " | ".join(cell(value) for value in row) + " |" for row in content["rows"]]
        # Empty headers avoid inventing source header semantics.
        width = len(content["rows"][0])
        return "\n".join(["|" + " |" * width, "|" + " --- |" * width, *rows])
    if kind == "code":
        content = str(content)
        fence = "`" * max(3, max((len(run) + 1 for run in re.findall(r"`+", content)), default=3))
        return fence + "\n" + content + ("" if content.endswith("\n") else "\n") + fence
    if kind == "text":
        return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", html.escape(str(content)))
    raise ValueError("Unsupported Markdown candidate format: " + kind)


def resolve_asset_markdown(root: Path, text: str, output: Path, *, originals_only: bool = False) -> str:
    if not ASSET_RE.search(text):
        return text
    assets = _assets(root)
    ids = ASSET_RE.findall(text)
    status = representation_status(root, ids)["assets"] if ids and not originals_only else {}

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in assets:
            raise ValueError("Unknown asset reference: " + key)
        asset = assets[key]
        pictures = " ".join(f'![原式 {key}](<{_href(root, fragment["png_path"], output)}>)' for fragment in asset["fragments"])
        if not originals_only and status[key]["state"] == "verified":
            sha = status[key]["candidate_sha256"]
            candidate = read_json(_directory(root) / "candidates" / f"{sha}.json")
            if candidate["format"] == "latex":
                display = asset.get("display", asset.get("kind") not in {"inline_math", "inline", "variable"})
                delimiter = "$$" if display else "$"
                return f'{delimiter}{candidate["content"]}{delimiter} {pictures}（已核验；原式备查）'
            return _candidate_markdown(candidate) + "\n\n" + pictures + "（已核验；原式备查）"
        if originals_only:
            return pictures
        return pictures + "（转写未完成／待核验）"

    return ASSET_RE.sub(replace, text)


def _review_html(root: Path, output: Path, packet: dict[str, Any]) -> str:
    rows = []
    for asset in packet["assets"]:
        candidate = packet["candidates"][asset["id"]]
        rows.append("<section><h2>" + html.escape(asset["id"]) + "</h2>" +
                    _asset_html(root, asset, output, candidate, False) +
                    "<pre>" + html.escape(json.dumps(candidate, ensure_ascii=False, indent=2)) + "</pre></section>")
    return '<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>独立原式核验</title><style>body{max-width:1000px;margin:auto;padding:1em}section{padding:1em;border:1px solid #aaa;margin:1em 0}.asset-original,.asset-candidate{display:block;overflow:auto}img{background:white}.asset-state,.asset-original-links{display:block}pre{white-space:pre-wrap;overflow-wrap:anywhere}[hidden]{display:none!important}</style><body><h1>候选待核验</h1><p>必须实际查看原图与成功渲染的候选；源图始终保留。浏览器 data-render-status 应为 passed；该状态不代表数学正确。</p>' + "".join(rows) + mathjax_bootstrap() + "</body></html>"
