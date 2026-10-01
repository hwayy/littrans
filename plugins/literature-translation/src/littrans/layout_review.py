"""Explainable layout concerns and fingerprint-bound supplemental decisions.

No detector score is a probability, and an empty list is never a coverage certificate.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from littrans.storage import read_json, write_json

RULE_VERSION = "1"
MODES = {"full", "layout-adjudication"}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def concerns(page: dict[str, Any]) -> list[dict[str, Any]]:
    """Coalesce existing boundary/grouping evidence into addressable review items."""
    rows = list(page.get("boundary_diagnostics", []))
    from littrans.fidelity import _tag_glyph_ids, _visual_lines
    from littrans.layout_geometry import fraction_components, possessive_ids
    from littrans.source_structure import EQUATION_LABEL
    glyphs = page["ledger"]["glyphs"]
    active = None
    from littrans.source_structure import _starts_statement
    for unit in page.get("units", []):
        if unit.get("render_policy") == "omit":
            continue
        text = unit["source_text"]
        if unit["kind"] == "heading" or _starts_statement(text):
            active = None
        if re.match(r"[*\s]*(?:Proof|Example|Step|Case|Solution)\b", text):
            active = unit
        elif (active and unit["kind"] in {"paragraph", "equation", "list_item"}
              and unit.get("parent_id") != active["unit_id"]):
            rows.append({"code": "same-page-container-candidate", "unit_id": unit["unit_id"],
                         "candidate_parent_id": active["unit_id"],
                         "action": "Check whether the upright paragraph continues the open container or starts independent prose."})
            active = None  # One boundary question covers the interrupted container.
        if any(mark in text for mark in "□■∎◻◼◇♦"):
            active = None
    owners = {gid: a["id"] for a in page["assets"] for f in a["fragments"] for gid in f["glyph_ids"]}
    math_ids = {gid for a in page["assets"] if a["kind"] == "math" for f in a["fragments"] for gid in f["glyph_ids"]}
    labels = {number for unit in page.get("units", [])
              for number in (unit.get("equation_numbers") or [unit.get("equation_number")]) if number}
    tag_ids = _tag_glyph_ids(glyphs)
    for line in _visual_lines([g for g in glyphs if g["id"] in tag_ids]):
        text = "".join(g["text"] for g in line)
        missing = [number for number in re.findall(EQUATION_LABEL, text) if number not in labels]
        if missing:
            rows.append({"code": "unbound-equation-label", "glyph_ids": [g["id"] for g in line],
                         "labels": missing, "action": "Bind the printed label to its formula, or explain why it is ordinary text."})
    for ids in fraction_components(glyphs, page["ledger"].get("vector_regions", []), math_ids):
        if len({owners.get(gid, "native-text") for gid in ids}) > 1:
            rows.append({"code": "split-fraction-candidate", "glyph_ids": sorted(ids),
                         "action": "Check numerator, bar and denominator together against the original."})
    suffixes = possessive_ids(_visual_lines(glyphs)) & math_ids
    for aid in sorted({owners[gid] for gid in suffixes}):
        rows.append({"code": "possessive-in-math", "asset_id": aid,
                     "glyph_ids": sorted(gid for gid in suffixes if owners[gid] == aid),
                     "action": "Check whether the baseline apostrophe-s belongs to the sentence."})
    for asset in page["assets"]:
        if asset["kind"] == "math" and not asset.get("display"):
            ids = {gid for f in asset["fragments"] for gid in f["glyph_ids"]}
            owned = [g for g in glyphs if g["id"] in ids and not g["text"].isspace()]
            bangs = [g["id"] for g in owned[-1:] if g["text"] == "!"]
            if bangs:
                rows.append({"code": "factorial-or-sentence", "asset_id": asset["id"], "glyph_ids": bangs,
                             "action": "Check factorial versus sentence punctuation using the complete sentence."})
    for asset in page["assets"]:
        if asset.get("grouping_pending"):
            rows.append({"code": "grouping-pending", "asset_id": asset["id"],
                         "action": "Check the complete original region and decide its grouping."})
        elif "ink-bounds-unmeasured" in asset.get("provenance", []):
            rows.append({"code": "ink-bounds-unmeasured", "asset_id": asset["id"],
                         "action": "Compare original and crop extents; some ink could not be measured."})
    for row in page["ledger"].get("crop_diagnostics", []):
        # Successful measurements are evidence, not new warnings.
        if row.get("warnings"):
            rows.append({"code": "crop-measurement-warning", **row})
    unique: dict[str, dict[str, Any]] = {}
    for row in rows:
        location = {k: row[k] for k in ("asset_id", "unit_id", "candidate_parent_id",
                    "context_page", "glyph_ids") if k in row}
        if "glyph_ids" in location:
            location["glyph_ids"] = sorted(set(location["glyph_ids"]))
        identity = {"rule_version": RULE_VERSION, "page": page["page"],
                    "code": row["code"], **location}
        key = "layout-" + digest(identity)[:24]
        unit = next((u for u in page.get("units", []) if u["unit_id"] == row.get("unit_id")), None)
        ownership = {gid: owners.get(gid, "native-text") for gid in location.get("glyph_ids", [])}
        current: dict[str, Any] = ({"parent_id": unit.get("parent_id"), "kind": unit["kind"]} if unit else
                   {"glyph_ownership": ownership} if ownership else
                   {"asset_id": row.get("asset_id"), "grouping": "recorded crop and fragments"})
        alternatives = {
            "same-page-container-candidate": ["Continue the named container", "Start independent prose"],
            "cross-page-container-candidate": ["Continue the previous-page container", "Start a new container"],
            "factorial-or-sentence": ["Mathematical factorial", "Sentence-ending exclamation mark"],
            "possessive-in-math": ["English possessive outside the formula", "Mathematical prime and variable"],
            "split-fraction-candidate": ["One complete fraction", "Separate neighbouring expressions"],
            "unbound-equation-label": ["A formula label", "An independent list or prose label"],
        }.get(row["code"], ["Retain the recorded glyph ownership and crop", "Correct the boundary or grouping"])
        unique[key] = {"id": key, **identity, "evidence": row,
                       "current_choice": current, "alternatives": alternatives}
    return sorted(unique.values(), key=lambda row: (row["code"], row["id"]))


def adjudications(page: dict[str, Any], decision: dict[str, Any], *,
                  partial: bool = False) -> dict[str, dict[str, Any]]:
    known = {row["id"] for row in page.get("layout_concerns", [])}
    result: dict[str, dict[str, Any]] = {}
    entries = decision.get("layout_adjudications", [])
    if not isinstance(entries, list):
        raise ValueError("layout_adjudications must be a list")
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("layout adjudication must be an object")
        ids = entry.get("concern_ids")
        reason = entry.get("reason")
        if (not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids)
                or not isinstance(reason, str) or not reason.strip()
                or entry.get("choice") not in {"retain", "correct"}
                or type(entry.get("uncertain")) is not bool):
            raise ValueError("adjudication requires concern_ids, choice, reason and uncertain")
        if entry["choice"] == "correct" and not decision.get("override"):
            raise ValueError("a correction adjudication requires a source override")
        for key in ids:
            if key not in known or key in result:
                raise ValueError("unknown, stale or duplicate layout concern: " + key)
            result[key] = entry
    # The established grouping acceptance is an alternate spelling of the same
    # decision, not a second questionnaire. Preserve it for existing clients.
    for accepted in decision.get("accepted_grouping_pending", []):
        if not isinstance(accepted, dict) or not isinstance(accepted.get("reason"), str) or not accepted["reason"].strip():
            continue
        for row in page.get("layout_concerns", []):
            if row.get("code") == "grouping-pending" and row.get("asset_id") == accepted.get("asset_id"):
                result.setdefault(row["id"], {"concern_ids": [row["id"]], "choice": "retain",
                                               "reason": accepted["reason"], "uncertain": False})
    if not partial and known - result.keys():
        raise ValueError("layout concerns need adjudication: " + ", ".join(sorted(known - result.keys())))
    return result


def binding(page: dict[str, Any], guidance: Any) -> str:
    return digest({"fingerprint": page["fingerprint"], "rule_version": RULE_VERSION,
                   "guidance": guidance, "context": page.get("layout_context", {})})


def supplemental_path(root: Path, page: int, key: str) -> Path:
    return root / "evidence/layout" / f"p{page:04d}-{key}.json"


def saved_decisions(root: Path, page: dict[str, Any]) -> dict[str, dict[str, Any]]:
    key = page.get("layout_binding")
    if not key:
        return {}
    path = supplemental_path(root, page["page"], key)
    if not path.is_file():
        return {}
    data = read_json(path)
    if data.get("sha256") != digest({k: v for k, v in data.items() if k != "sha256"}):
        raise ValueError("supplemental layout evidence changed: " + str(path))
    if data.get("binding") != key:
        raise ValueError("supplemental layout binding mismatch")
    from littrans.fidelity import _load_source_packet
    result: dict[str, dict[str, Any]] = {}
    for record in data["records"]:
        packet = _load_source_packet(root, record["packet_id"], record["packet_sha256"])
        original = next(p for p in packet["pages"] if p["page"] == page["page"])
        if original.get("layout_binding") != key:
            raise ValueError("supplemental packet binding mismatch")
        result.update(adjudications(original, record["decision"], partial=True))
    return result


def save_supplement(root: Path, page: dict[str, Any], review: dict[str, Any],
                    decision: dict[str, Any]) -> None:
    key = page["layout_binding"]
    path = supplemental_path(root, page["page"], key)
    saved_decisions(root, page)  # Never overwrite evidence that failed integrity checks.
    records = read_json(path)["records"] if path.is_file() else []
    record = {k: review[k] for k in ("packet_id", "packet_sha256", "reviewer")}
    record["decision"] = decision
    if record not in records:
        records.append(record)
    payload = {"schema_version": 1, "binding": key, "page": page["page"], "records": records}
    write_json(path, {**payload, "sha256": digest(payload)})


def applicable_decisions(root: Path, page: dict[str, Any], source_hash: str) -> dict[str, dict[str, Any]]:
    """Combine full-review and supplemental decisions without replacing either record."""
    from littrans.fidelity import _receipt_path, _verify_source_receipt
    result = {}
    receipt_path = _receipt_path(root, page["page"])
    if receipt_path.is_file():
        receipt = read_json(receipt_path)
        if receipt.get("fingerprint") == page["fingerprint"] and receipt.get("passed"):
            _verify_source_receipt(root, page, receipt, source_hash)
            for entry in receipt["decision"].get("layout_adjudications", []):
                result.update({key: entry for key in entry["concern_ids"]})
    result.update(saved_decisions(root, page))
    return result


def summary(page: dict[str, Any], decisions: dict[str, dict[str, Any]]) -> dict[str, Any]:
    ids = {row["id"] for row in page.get("layout_concerns", [])}
    selected = {key: value for key, value in decisions.items() if key in ids}
    return {"page": page["page"], "pending": len(ids - selected.keys()),
            "adjudicated": len(selected),
            "uncertain": sum(bool(row["uncertain"]) for row in selected.values()),
            "uncertain_decisions": [{"concern_id": key, **value} for key, value in selected.items()
                                    if value["uncertain"]]}
