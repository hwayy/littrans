from __future__ import annotations

import json
import re
import shutil
import stat
import tempfile
from collections import Counter
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pymupdf as fitz
import yaml
from pydantic import BaseModel

from littrans.build_info import build_identity
from littrans.hosts import (
    SUBAGENT_DISPATCH,
    dispatch_advisories,
    resolve_coordination_host,
)
from littrans.models import (
    PROJECT_SCHEMA_VERSION,
    AuditRun,
    BatchManifest,
    ExternalReviewAttempt,
    ExternalReviewRun,
    PageVerificationReceipt,
    ProjectConfig,
    ProjectStatus,
    ReviewIssue,
    SourceUnit,
    TranslationRecord,
    WorkflowPacketManifest,
)
from littrans.storage import (
    PROJECT_DIRS,
    atomic_write_text,
    initialize_project_dirs,
    load_project,
    plugin_root,
    read_jsonl,
    restore_files,
    save_project,
    sha256_file,
    snapshot_files,
    write_json,
)


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:64] or "translation-project"


def load_profile(profile: str) -> dict[str, Any]:
    candidate = Path(profile)
    if not candidate.is_file():
        candidate = plugin_root() / "profiles" / f"{profile}.yaml"
    if not candidate.is_file():
        candidate = Path(__file__).resolve().parent / "profiles" / f"{profile}.yaml"
    if not candidate.is_file():
        raise ValueError(f"Unknown profile: {profile}")
    payload = yaml.safe_load(candidate.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Profile must contain a YAML object: {candidate}")
    return payload


def initialize_project(
    source: Path,
    root: Path,
    profile: str,
    title: str | None = None,
    source_language: str = "en",
    target_language: str = "zh-CN",
    repo_root: Path | None = None,
    scaffold: bool = True,
    scaffold_report: dict[str, Any] | None = None,
) -> ProjectConfig:
    """Create a schema-6 project and grow its record structure.

    Besides the directories and ``project.yaml``, the project receives the context and
    glossary skeletons, a ``.gitignore`` that keeps the source out and the record in, and
    the handbook, records, ledger, launcher and plugin-facts files under ``repo_root``
    (the project root unless the project is nested in a larger repository).
    """
    from littrans.scaffold import SCAFFOLD_FILES, scaffold_project

    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    root = root.resolve()
    if root.joinpath("project.yaml").exists():
        raise ValueError(f"Project already exists: {root}")
    load_profile(profile)
    # Refuse a record root that cannot hold the project before anything is written, so a
    # wrong --repo-root never leaves a half-initialized project behind.
    record_root = root if repo_root is None else Path(repo_root).resolve()
    if not root.is_relative_to(record_root):
        raise ValueError(f"The project root {root} must lie inside the record root {record_root}")

    # Initialization also writes into an ancestor record root. Snapshot only paths this
    # call may touch, then remove only its new files and empty directories on failure.
    targets = {root / "project.yaml", root / "settings.yaml", root / "settings.local.yaml", root / "derived/project-state.json",
               root / "derived" / "provenance.json"}
    targets.update(
        (root if spec.at_project_root else record_root) / spec.relative
        for spec in SCAFFOLD_FILES
    )
    snapshots = snapshot_files(targets)
    modes = {path: stat.S_IMODE(path.stat().st_mode) for path, data in snapshots.items() if data is not None}
    created_dirs: set[Path] = set()
    for directory in {root, *(root / name for name in PROJECT_DIRS), *(path.parent for path in targets)}:
        while not directory.exists():
            created_dirs.add(directory)
            directory = directory.parent

    # A PDF inside the project is recorded relative to it, so a clone on another host
    # finds it under the same name; one kept elsewhere can only be named absolutely.
    recorded_source = source.relative_to(root).as_posix() if source.is_relative_to(root) else str(source)
    record_parts = root.relative_to(record_root).parts
    report: dict[str, Any] | None = None
    try:
        initialize_project_dirs(root)
        with fitz.open(source) as document:
            config = ProjectConfig(
                project_id=slugify(title or source.stem),
                title=title or source.stem,
                source_path=recorded_source,
                source_sha256=sha256_file(source),
                source_pages=document.page_count,
                profile=profile,
                record_root_relative="/".join(".." for _ in record_parts) or ".",
                source_language=source_language,
                target_language=target_language,
            )
        save_project(root, config)
        if scaffold:
            report = scaffold_project(root, repo_root=record_root, refresh=True)
        write_json(
            root / "derived" / "provenance.json",
            {
                "source_path": recorded_source,
                "source_sha256": config.source_sha256,
                "rights_status": config.rights_status,
                "source_is_copied": False,
                "generator": build_identity(),
            },
        )
    except BaseException:
        restore_files(snapshots)
        for path, mode in modes.items():
            path.chmod(mode)
        for directory in sorted(created_dirs, key=lambda path: len(path.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass  # Leave a directory that gained unrelated contents alone.
        raise
    if scaffold_report is not None and report is not None:
        scaffold_report.update(report)
    return config


REBUILD_SETTINGS_MODES = ("preserve", "preset")
# Historical manifests carried policy that v7 keeps in settings.yaml; it is reported, not converted.
LEGACY_POLICY_FIELDS = ("external_review", "agent_models")


def rebuild_project(
    old: Path,
    new: Path,
    *,
    settings: str | None = None,
    preset: str | None = None,
    report: dict[str, Any] | None = None,
) -> ProjectConfig:
    """Create a v7 workspace from validated source/context, without inheriting approvals.

    A v7 project keeps its validated ``settings.yaml`` and machine command bindings by
    default; ``settings="preset"`` explicitly starts from a preset instead. Historical
    formats have no v7 policy and always start from a preset. ``report`` receives what
    was preserved, reset and not migrated, which is also recorded in the new project.
    """
    from littrans.configuration import (
        apply,
        differences,
        read_local,
        read_settings,
        resolve_command_binding,
    )
    from littrans.scaffold import scaffold_project
    from littrans.settings import preset as preset_settings

    old, new = old.resolve(), new.resolve()
    if new == old or new.exists():
        raise ValueError("Rebuild requires a new, non-existing directory distinct from OLD")
    if settings not in (None, *REBUILD_SETTINGS_MODES):
        raise ValueError("Rebuild --settings must be preserve or preset")
    payload = yaml.safe_load((old / "project.yaml").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Invalid historical project configuration")
    source_schema = payload.get("schema_version")
    current = source_schema == PROJECT_SCHEMA_VERSION
    legacy = [key for key in LEGACY_POLICY_FIELDS if payload.get(key)]
    old_settings = old_local = None
    if current:
        # Validates the manifest, settings and machine bindings before anything is copied.
        payload = load_project(old).model_dump(mode="json")
        old_settings, old_local = read_settings(old), read_local(old)
    mode = settings or ("preserve" if current else "preset")
    if mode == "preserve" and not current:
        raise ValueError(
            f"Project schema v{source_schema} has no v7 settings to preserve; "
            "rebuild it with --settings preset"
        )
    if preset is not None and mode != "preset":
        raise ValueError("--preset requires --settings preset")
    preset_name = preset or payload.get("profile", "technical-book")
    if old_local is not None:
        source_binding = old_local.source_path
    else:
        local_path = old / "settings.local.yaml"
        local = yaml.safe_load(local_path.read_text(encoding="utf-8")) if local_path.exists() else {}
        source_binding = (local or {}).get("source_path")
    source = Path(source_binding or payload["source_path"])
    if not source.is_absolute():
        source = old / source
    if sha256_file(source) != payload["source_sha256"]:
        raise ValueError("Historical source PDF hash changed")
    new.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".littrans-rebuild-", dir=new.parent) as temporary:
        staging = Path(temporary) / "project"
        copied_source = staging / "source" / source.name
        copied_source.parent.mkdir(parents=True)
        shutil.copyfile(source, copied_source)
        if sha256_file(copied_source) != payload["source_sha256"]:
            raise ValueError("Copied source PDF hash changed")
        config = initialize_project(
            copied_source, staging, preset_name, payload.get("title"),
            payload.get("source_language", "en"), payload.get("target_language", "zh-CN"),
        )
        config.source_path = copied_source.relative_to(staging).as_posix()
        config.rights_status = payload.get("rights_status", config.rights_status)
        if mode == "preserve":
            assert old_settings is not None
            # A byte copy keeps reviewed comments; it must validate to the same policy.
            shutil.copyfile(old / "settings.yaml", staging / "settings.yaml")
            if read_settings(staging).payload() != old_settings.payload():
                raise ValueError("Copied settings.yaml does not reproduce the validated policy")
        else:
            document = (old_settings.document.model_dump() if old_settings else
                        {"title": config.title, "source_language": config.source_language,
                         "target_language": config.target_language, "rights_status": config.rights_status})
            candidate = preset_settings(preset_name, document["title"], document["source_language"],
                                        document["target_language"]).payload()
            candidate["document"] = document
            apply(staging, candidate)
        rebuilt = read_settings(staging)
        local_migrated: list[dict[str, str]] = []
        not_migrated: list[dict[str, str]] = []
        if old_local is not None:
            commands = {}
            for key, command in sorted(old_local.commands.items()):
                if key not in rebuilt.external_review.reviewers:
                    not_migrated.append({"path": f"settings.local.yaml:commands.{key}",
                                         "reason": "The rebuilt settings do not define this reviewer."})
                    continue
                # Relative bindings are anchored to OLD; the rebuilt binding names the same file.
                commands[key] = resolve_command_binding(old, command)
                local_migrated.append({"path": f"commands.{key}",
                                       "binding": "command-name" if commands[key] == command
                                       and not Path(command).is_absolute() else "path"})
            if commands:
                apply(staging, {"schema_version": 1, "source_path": None, "commands": commands}, local=True)
        if source_binding:
            not_migrated.append({"path": "settings.local.yaml:source_path",
                                 "reason": f"Replaced by the copied {config.source_path}."})
        next_actions = []
        for host in ("codex", "opencode"):
            if (old / f".littrans/host-agents/{host}.json").is_file():
                not_migrated.append({"path": f".littrans/host-agents/{host}.json",
                                     "reason": "Generated native agent files are not copied."})
                next_actions.append(f"project agents NEW --host {host} --check")
        for key in legacy:
            not_migrated.append({"path": f"project.yaml:{key}",
                                 "reason": "Legacy policy is not converted; configure it with config apply."})
        after = rebuilt.payload()
        sections = [key for key in after if key != "schema_version"]
        if old_settings is not None:
            reset = differences(old_settings.payload(), after)
            changed = {item["path"].split(".", 1)[0] for item in reset}
            preserved = [key for key in sections if key not in changed]
        else:
            preserved = ["document"]
            reset = [{"path": key, "before": None, "after": after[key], "operation": "preset-default"}
                     for key in sections if key != "document"]
        if mode == "preset" or legacy:
            next_actions.append("config show NEW; config apply NEW CANDIDATE --dry-run")
        configuration = {
            "mode": mode, "source_schema_version": source_schema,
            "preset": {"name": rebuilt.preset.name, "version": rebuilt.preset.version},
            "preserved": preserved, "reset": reset, "local_migrated": local_migrated,
            "not_migrated": not_migrated, "next_actions": next_actions,
        }
        copied = ["source"]
        # The decision trace travels with the context it explains; approvals do not.
        for directory in ("context", "glossary", "docs"):
            if (old / directory).is_dir():
                shutil.copytree(old / directory, staging / directory, dirs_exist_ok=True)
                copied.append(directory)
        scaffold_project(staging, refresh=True)
        from littrans.context_config import validate
        validate(staging)
        save_project(staging, config)
        write_json(staging / "derived" / "provenance.json", {
            "source_path": config.source_path, "source_sha256": config.source_sha256,
            "rights_status": rebuilt.document.rights_status, "source_is_copied": True,
            "generator": build_identity(),
        })
        write_json(staging / "derived" / "rebuild-provenance.json", {
            "historical_project": str(old), "source_sha256": config.source_sha256,
            "copied": copied, "inherited_approvals": False,
            "context_policy": "Historical style text is context; the v6 asset-reference contract takes precedence.",
            "configuration": configuration,
        })
        if new.exists():
            raise ValueError("Rebuild destination appeared during initialization")
        staging.rename(new)
    if report is not None:
        report.update({"copied": copied, "inherited_approvals": False, "configuration": configuration})
    return load_project(new)


def translation_map(root: Path) -> dict[str, TranslationRecord]:
    return {
        record.unit_id: record
        for record in read_jsonl(root / "translations" / "current.jsonl", TranslationRecord)
    }


TERM_MATCH_MODES = ("substring", "word", "regex")
APPROVED_STATUS = "approved"
REFERENCE_STATUS = "reference-only"
PROPOSED_STATUS = "proposed"
REFERENCE_FILE = "reference.yaml"
DEFAULT_REFERENCE_KIND = "reference"


def _validate_term(path: Path, term: dict[str, Any]) -> None:
    """Refuse an entry whose source forms could never be matched as written."""
    source = str(term.get("source", ""))
    scope = term.get("scope", "document")
    if not isinstance(scope, str) or not scope.strip() or (
        scope.startswith("page:") and not re.fullmatch(r"page:[1-9]\d*", scope)
    ):
        raise ValueError(f"{path}: term {source!r} has invalid scope")
    status = term.get("status", APPROVED_STATUS)
    if path.name == "approved.yaml" and (
        not isinstance(status, str)
        or status not in {APPROVED_STATUS, REFERENCE_STATUS, PROPOSED_STATUS}
    ):
        raise ValueError(
            f"{path}: term {source!r} has unknown status {status!r}; "
            "use approved, reference-only, or proposed"
        )
    mode = str(term.get("match", "substring"))
    if mode not in TERM_MATCH_MODES:
        raise ValueError(f"{path}: term {source!r} has unknown match mode {mode!r}; use one of {TERM_MATCH_MODES}")
    aliases = term.get("aliases", [])
    if aliases is None:
        aliases = []
    if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
        raise ValueError(f"{path}: term {source!r} must list its aliases as strings")
    if mode == "regex":
        from littrans.evidence import fold_regex_pattern

        for pattern in (source, *aliases):
            try:
                re.compile(fold_regex_pattern(pattern), re.I)
            except re.error as exc:
                raise ValueError(f"{path}: term {pattern!r} is not a valid regular expression: {exc}") from exc


def _read_term_file(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    from littrans.configuration import yaml_read
    # Round-trip YAML nodes belong to the editor, not packet/domain serializers.
    data = json.loads(json.dumps(yaml_read(path)))
    if not isinstance(data, dict) or not isinstance(data.get("terms", []), list):
        raise ValueError(f"{path} must contain a terms list")
    terms = []
    for index, term in enumerate(data.get("terms", []), start=1):
        if not isinstance(term, dict):
            raise ValueError(f"{path}: terms[{index}] must be a mapping, got {type(term).__name__}")
        _validate_term(path, term)
        terms.append(term)
    return terms


def load_terms(root: Path, filename: str = "approved.yaml", *, enforced_only: bool = True) -> list[dict[str, Any]]:
    """Load glossary entries; by default only those whose ``status`` is enforced.

    An entry without ``status`` counts as ``approved``. ``status: reference-only`` entries
    are never enforced: they travel to packets through ``load_reference_terms``. Any other
    status (``proposed``, ...) is inert unless the caller asks for every entry, e.g. to
    list candidates.
    """
    path = root / "glossary" / filename
    terms = []
    for term in _read_term_file(path):
        if enforced_only and str(term.get("status", APPROVED_STATUS)) != APPROVED_STATUS:
            continue
        terms.append(term)
    if filename == "approved.yaml" and enforced_only:
        from littrans.evidence import fold_term_text
        units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
        for index, left in enumerate(terms):
            left_forms = {fold_term_text(str(value)) for value in
                          (left.get("source", ""), *(left.get("aliases") or []))}
            for right in terms[index + 1:]:
                right_forms = {fold_term_text(str(value)) for value in
                               (right.get("source", ""), *(right.get("aliases") or []))}
                if not left_forms & right_forms or left.get("target") == right.get("target"):
                    continue
                a, b = left.get("scope", "document"), right.get("scope", "document")
                overlap = a == b or "document" in (a, b) or any(
                    a in {unit.parent_id, f"page:{unit.page}"}
                    and b in {unit.parent_id, f"page:{unit.page}"} for unit in units)
                if overlap:
                    raise ValueError(f"Approved terminology scope conflict: {left.get('source')!r} ({a}, {b})")
    return terms


def load_reference_terms(root: Path) -> list[dict[str, Any]]:
    """Load the binding-but-not-gated entries shown to translators and auditors.

    They come from ``approved.yaml`` entries with ``status: reference-only`` and from every
    entry of ``glossary/reference.yaml``, whose ``status`` defaults to ``reference-only``;
    ``proposed`` entries stay inert in both files and ``approved`` is refused in
    ``reference.yaml`` because that file never gates. Each entry carries a ``kind``
    (``reference`` when absent) so proper names, one-word-two-senses registers and other
    project-defined categories stay distinguishable in packets. Every other key is passed
    through verbatim.
    """
    reference: list[dict[str, Any]] = []
    for term in _read_term_file(root / "glossary" / "approved.yaml"):
        if str(term.get("status", APPROVED_STATUS)) == REFERENCE_STATUS:
            reference.append(term)
    path = root / "glossary" / REFERENCE_FILE
    for term in _read_term_file(path):
        status = str(term.get("status", REFERENCE_STATUS))
        if status == PROPOSED_STATUS:
            continue
        if status != REFERENCE_STATUS:
            raise ValueError(
                f"{path}: term {term.get('source', '')!r} has status {status!r}; reference.yaml never gates, "
                f"move a gated entry to approved.yaml"
            )
        reference.append(term)
    return [
        {"kind": str(term.get("kind") or DEFAULT_REFERENCE_KIND), **{k: v for k, v in term.items() if k != "kind"}}
        for term in reference
    ]


def project_status(root: Path) -> dict[str, Any]:
    config = load_project(root)
    units_path = root / "derived" / "units.jsonl"
    translations = translation_map(root)
    batches = sorted(path.name for path in (root / "batches").glob("*") if path.is_dir())
    issues: list[str] = []
    for path in (root / "reviews").glob("*.issues.jsonl"):
        issues.extend(
            line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
        )
    unit_count = (
        sum(1 for line in units_path.read_text(encoding="utf-8").splitlines() if line.strip())
        if units_path.exists()
        else 0
    )
    units = read_jsonl(units_path, SourceUnit)
    translatable_ids = {unit.unit_id for unit in units if unit.translatable}
    status_counts = Counter(
        record.status.value
        for unit_id, record in translations.items()
        if unit_id in translatable_ids
    )
    reviewed_count = sum(
        status_counts[status]
        for status in (
            ProjectStatus.MACHINE_REVIEWED.value,
            ProjectStatus.EXTERNAL_REVIEWED.value,
            ProjectStatus.HUMAN_APPROVED.value,
        )
    )
    return {
        "project_id": config.project_id,
        "title": config.title,
        "status": config.status,
        "source_pages": config.source_pages,
        "unit_count": unit_count,
        "translation_count": len(translations),
        "translatable_unit_count": len(translatable_ids),
        "translation_status_counts": dict(sorted(status_counts.items())),
        "machine_reviewed_coverage": (
            reviewed_count / len(translatable_ids) if translatable_ids else 1.0
        ),
        "batches": batches,
        "review_issue_count": len(issues),
        "output_files": sorted(path.name for path in (root / "output").glob("*")),
    }


def schema_models() -> dict[str, type[BaseModel]]:
    from littrans.fidelity_models import FidelityAsset
    from littrans.representation_models import AssetReviewSubmission, AssetSubmission
    from littrans.settings import LocalSettings, ProjectManifest, ProjectSettings
    from littrans.task_models import TaskEnvelope
    return {
        "task-envelope.schema.json": TaskEnvelope,
        "project.schema.json": ProjectManifest,
        "settings.schema.json": ProjectSettings,
        "settings-local.schema.json": LocalSettings,
        "source-unit.schema.json": SourceUnit,
        "translation-record.schema.json": TranslationRecord,
        "review-issue.schema.json": ReviewIssue,
        "batch-manifest.schema.json": BatchManifest,
        "external-review-run.schema.json": ExternalReviewRun,
        "external-review-attempt.schema.json": ExternalReviewAttempt,
        "page-verification-receipt.schema.json": PageVerificationReceipt,
        "audit-run.schema.json": AuditRun,
        "workflow-packet-manifest.schema.json": WorkflowPacketManifest,
        "fidelity-asset.schema.json": FidelityAsset,
        "asset-submission.schema.json": AssetSubmission,
        "asset-review-submission.schema.json": AssetReviewSubmission,
    }


def dispatch_report(
    root: Path,
    host: str | None = None,
    reported: Iterable[str] | None = None,
    *,
    project_config: ProjectConfig | None = None,
) -> dict[str, Any]:
    """Expose the shared effective policy plus legacy host advisories."""
    project_config = project_config or load_project(root)
    from littrans.configuration import effective
    view = effective(root, host or "auto", project_config=project_config)
    resolved = resolve_coordination_host(host)
    capability = SUBAGENT_DISPATCH[resolved]
    selected = tuple(view["roles"]) if reported is None else tuple(reported)
    advisories: list[str] = []
    for role, dispatch in view["roles"].items():
        if role in selected:
            advisories.extend(dispatch_advisories(
                resolved, role, dispatch["model"], dispatch["reasoning_effort"]))
    # Audit lens tasks are reported as "audit:<lens>"; a lens without its own override
    # resolves to the audit role and shares its advisories.
    for lens, dispatch in view["audit_lenses"].items():
        if f"audit:{lens}" in selected:
            overridden = dispatch != view["roles"]["audit"]
            advisories.extend(dispatch_advisories(
                resolved, "audit", dispatch["model"], dispatch["reasoning_effort"],
                lens=lens if overridden else None))
    advisories = list(dict.fromkeys(advisories))
    return {**view, "supports": {**view["supports"], "agent_effort": capability.agent_effort},
            "advisories": advisories}



def role_dispatch(root: Path, host: str | None, role: str) -> dict[str, str | None]:
    """The dispatch a coordinator hands to one role's subagent on the resolved host.

    Source review uses it beside its packet rather than inside it: a source packet's
    identity binds content only, so identical pages keep one packet on every host.
    """
    resolved = resolve_coordination_host(host)
    dispatch = load_project(root).dispatch(resolved, role)
    return {"host": resolved, "role": role, "model": dispatch.model,
            "reasoning_effort": dispatch.reasoning_effort}


def schema_mismatches(output: Path) -> list[str]:
    expected = schema_models()
    actual_names = {path.name for path in output.glob("*.json")}
    mismatches = [
        f"missing:{filename}" for filename in sorted(set(expected) - actual_names)
    ]
    mismatches.extend(
        f"unexpected:{filename}" for filename in sorted(actual_names - set(expected))
    )
    for filename, model in expected.items():
        path = output / filename
        if path.is_file() and json.loads(path.read_text(encoding="utf-8")) != model.model_json_schema():
            mismatches.append(f"stale:{filename}")
    return mismatches


def write_schemas(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    for filename, model in schema_models().items():
        atomic_write_text(
            output / filename,
            json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2) + "\n",
        )


def promote_status(root: Path, status: ProjectStatus) -> None:
    config = load_project(root)
    config.status = status
    save_project(root, config)
