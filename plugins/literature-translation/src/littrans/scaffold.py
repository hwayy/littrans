"""Grow a project's record structure at initialization, and keep the plugin's part current.

A translation project needs more than the schema-6 directories before the first page is
prepared: a handbook, dated records, a decision trace, a defect ledger, a launcher, a
``.gitignore`` that keeps the copyrighted source out and the record in, and a statement of
what the installed plugin actually does. Every project rebuilt those by hand, and every
hand-written paragraph about the plugin drifted. ``scaffold_project`` writes skeletons for
the user-owned files exactly once and regenerates the one plugin-owned file
(``docs/LITTRANS.md``) from the package's own constants whenever asked.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from jinja2 import Environment

from littrans.build_info import build_identity
from littrans.hosts import (
    DISPATCH_ROLES,
    HOST_ENV_SIGNALS,
    LENS_REVIEWER_BATCH_MAX,
    SUBAGENT_DISPATCH,
    WAVE_LIMITS,
)
from littrans.storage import atomic_write_text, plugin_root, write_text_if_missing

PLUGIN_OWNED_FILE = "docs/LITTRANS.md"
PROJECT_IGNORE_LINES = (".littrans/*", "!.littrans/work/")
# Projects created before the pair above excluded the whole runtime directory. Such a line
# hides `.littrans/work/` from the re-include, so it is removed when the pair is added.
LEGACY_STATE_IGNORE_KEYS = frozenset({(False, ".littrans/"), (False, ".littrans")})


@dataclass(frozen=True, slots=True)
class ScaffoldFile:
    """One generated file: where it goes, which template renders it, who owns it."""

    relative: str
    template: str
    at_project_root: bool = False
    plugin_owned: bool = False


SCAFFOLD_FILES: tuple[ScaffoldFile, ...] = (
    ScaffoldFile("context/document-brief.md", "document-brief.md.j2", at_project_root=True),
    ScaffoldFile("context/style-guide.md", "style-guide.md.j2", at_project_root=True),
    ScaffoldFile("glossary/approved.yaml", "approved.yaml.j2", at_project_root=True),
    ScaffoldFile("glossary/candidates.yaml", "candidates.yaml.j2", at_project_root=True),
    ScaffoldFile("glossary/reference.yaml", "reference.yaml.j2", at_project_root=True),
    ScaffoldFile(".gitignore", "gitignore.j2", at_project_root=True),
    ScaffoldFile(".gitattributes", "gitattributes.j2"),
    ScaffoldFile("README.md", "README.md.j2"),
    ScaffoldFile("AGENTS.md", "AGENTS.md.j2"),
    ScaffoldFile("CLAUDE.md", "CLAUDE.md.j2"),
    ScaffoldFile("PLUGIN-ISSUES.md", "PLUGIN-ISSUES.md.j2"),
    ScaffoldFile("docs/HISTORY.md", "HISTORY.md.j2"),
    ScaffoldFile("docs/DECISIONS.md", "DECISIONS.md.j2"),
    ScaffoldFile("docs/TERMINOLOGY.md", "TERMINOLOGY.md.j2"),
    ScaffoldFile("docs/REVIEWS.md", "REVIEWS.md.j2"),
    ScaffoldFile("tools/lt.py", "lt.py.j2"),
    ScaffoldFile("tools/lt.cmd", "lt.cmd.j2"),
    ScaffoldFile("tools/lt.sh", "lt.sh.j2"),
    ScaffoldFile(PLUGIN_OWNED_FILE, "LITTRANS.md.j2", plugin_owned=True),
)


def _environment() -> Environment:
    return Environment(autoescape=False, keep_trailing_newline=True, trim_blocks=True, lstrip_blocks=True)


def _template_text(name: str) -> str:
    return files("littrans").joinpath("templates", "scaffold", name).read_text(encoding="utf-8")


def render_scaffold_template(name: str, context: dict[str, Any]) -> str:
    text = _environment().from_string(_template_text(name)).render(**context)
    return text.replace("\r\n", "\n")


def plugin_facts() -> dict[str, Any]:
    """What the installed plugin guarantees, read from the package rather than typed."""
    from littrans.evidence import AUDIT_CONTEXT_PARTS
    from littrans.models import AUDIT_STALE_REASONS, WORKFLOW_PACKET_STAGES, ProjectStatus
    from littrans.project import (
        APPROVED_STATUS,
        DEFAULT_REFERENCE_KIND,
        PROPOSED_STATUS,
        REFERENCE_STATUS,
        TERM_MATCH_MODES,
    )
    from littrans.quality import DETERMINISTIC_QA_VERSION, REQUIRED_AUDIT_LENSES

    identity = build_identity()
    return {
        "version": identity["plugin_version"],
        "build_digest": identity["build_digest"],
        "generated_at": identity["generated_at"],
        "term_match_modes": list(TERM_MATCH_MODES),
        "approved_status": APPROVED_STATUS,
        "reference_status": REFERENCE_STATUS,
        "proposed_status": PROPOSED_STATUS,
        "default_reference_kind": DEFAULT_REFERENCE_KIND,
        "audit_context_parts": list(AUDIT_CONTEXT_PARTS),
        "stale_reasons": list(AUDIT_STALE_REASONS),
        "packet_stages": list(WORKFLOW_PACKET_STAGES),
        "wave_limits": {host: {"default": spec.default, "maximum": spec.maximum} for host, spec in WAVE_LIMITS.items()},
        "lens_reviewer_batch_max": LENS_REVIEWER_BATCH_MAX,
        # What each host's task launcher lets the coordinator choose for one dispatch.
        "subagent_dispatch": {
            host: {"model": spec.model, "reasoning_effort": spec.reasoning_effort,
                   "agent_effort": spec.agent_effort}
            for host, spec in SUBAGENT_DISPATCH.items()
        },
        "dispatch_roles": list(DISPATCH_ROLES),
        "audit_lenses": sorted(REQUIRED_AUDIT_LENSES),
        "status_order": [status.value for status in ProjectStatus],
        "qa_version": DETERMINISTIC_QA_VERSION,
        "ignore_lines": list(PROJECT_IGNORE_LINES),
        # The launcher detects the client running a session by the plugin's own signals.
        "host_signals": {host: list(names) for host, names in HOST_ENV_SIGNALS.items()},
    }


def scaffold_context(root: Path, repo_root: Path) -> dict[str, Any]:
    try:
        project_rel = root.relative_to(repo_root).as_posix()
    except ValueError as exc:
        raise ValueError(f"The project root {root} must lie inside the record root {repo_root}") from exc
    project_rel = project_rel or "."
    root_dir = plugin_root()
    home = Path.home()
    return {
        **plugin_facts(),
        "project_rel": project_rel,
        "project_prefix": "" if project_rel == "." else project_rel + "/",
        "plugin_root": str(root_dir),
        # The same root relative to the home directory, so a clone on another host or
        # account finds the plugin where this user's client installed it.
        "plugin_root_home_relative": root_dir.relative_to(home).as_posix() if root_dir.is_relative_to(home) else "",
        "plugin_owned_file": PLUGIN_OWNED_FILE,
    }


def _ignore_key(line: str) -> tuple[bool, str]:
    """A gitignore pattern's identity: its negation and its path without the leading slash.

    ``/.littrans/*`` and ``.littrans/*`` anchor the same path in a file that lives in the
    directory they name (a pattern with a slash inside is anchored either way).
    """
    line = line.strip()
    negated = line.startswith("!")
    return negated, line[1:].lstrip("/") if negated else line.lstrip("/")


def ensure_project_ignore(root: Path) -> bool:
    """Add the runtime-state ignore pair to an existing project ``.gitignore`` once.

    A pair the file already carries in an equivalent spelling (a leading slash) is present.
    The whole-directory exclusion projects wrote before the pair existed (``/.littrans/``)
    is dropped rather than appended to: git never descends into an excluded directory, so a
    later ``!.littrans/work/`` cannot re-include the packet payloads that line hides.
    """
    ignore_path = root / ".gitignore"
    existing = ignore_path.read_text(encoding="utf-8") if ignore_path.is_file() else ""
    # Line endings are kept as written: only the legacy line is removed and the pair added.
    lines = existing.splitlines(keepends=True)
    kept = [line for line in lines if _ignore_key(line) not in LEGACY_STATE_IGNORE_KEYS]
    present = {_ignore_key(line) for line in kept}
    missing = [line for line in PROJECT_IGNORE_LINES if _ignore_key(line) not in present]
    if not missing and len(kept) == len(lines):
        return False
    body = "".join(kept)
    separator = "" if not body or body.endswith(("\n", "\r")) else "\n"
    atomic_write_text(ignore_path, body + separator + "\n".join(missing) + ("\n" if missing else ""))
    return True


def scaffold_project(root: Path, *, repo_root: Path | None = None, refresh: bool = False) -> dict[str, Any]:
    """Create the missing record files; regenerate the plugin-owned one when ``refresh``.

    ``repo_root`` is where the handbook, records, ledger and launcher live; it defaults
    to the project root and may be an ancestor of it for nested layouts. User-owned files
    are never overwritten, so the call is idempotent and safe on a project in progress.
    Nothing here depends on the document being translated.
    """
    root = Path(root).resolve()
    repo_root = root if repo_root is None else Path(repo_root).resolve()
    context = scaffold_context(root, repo_root)
    created: list[str] = []
    kept: list[str] = []
    refreshed: list[str] = []
    for spec in SCAFFOLD_FILES:
        base = root if spec.at_project_root else repo_root
        target = base / spec.relative
        label = target.relative_to(repo_root).as_posix() if target.is_relative_to(repo_root) else str(target)
        if spec.plugin_owned:
            if target.exists() and not refresh:
                kept.append(label)
                continue
            existed = target.exists()
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(target, render_scaffold_template(spec.template, context))
            (refreshed if existed else created).append(label)
            continue
        if spec.relative == ".gitignore" and target.exists():
            if ensure_project_ignore(root):
                refreshed.append(label)
            else:
                kept.append(label)
            continue
        if write_text_if_missing(target, render_scaffold_template(spec.template, context)):
            created.append(label)
        else:
            kept.append(label)
        if spec.relative.startswith("tools/") and os.name != "nt":
            # A POSIX host runs the launcher directly; git carries the bit once committed.
            target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return {
        "project_root": str(root),
        "repo_root": str(repo_root),
        "created": created,
        "kept": kept,
        "refreshed": refreshed,
        "plugin_owned": [PLUGIN_OWNED_FILE],
    }
