"""Does git track exactly the project record, and nothing that must stay out?

Four ways to get a ``.gitignore`` wrong, and only the first is obvious: a file meant for the
record is ignored (silently never committed); a file meant to stay out is tracked
(copyrighted bytes in git); a file meant for the record is neither ignored nor added (it
looks fine in ``git status`` once and is forgotten); a file in neither set at all. The
record itself is derived from the data — assets, receipts, batches, ledgers — never from a
hard-coded list, and git is asked to classify paths rather than re-implementing its
pattern rules: a re-include chain is easy to get subtly wrong, and the point of this check
is to not trust reading.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from littrans.fidelity import source_packet_liveness
from littrans.fidelity_models import load_assets
from littrans.scaffold import SCAFFOLD_FILES
from littrans.storage import load_project

BATCH_FILES = ("manifest.yaml", "translation.jsonl", "source.md", "context.md", "output-schema.json")
RECORD_GLOBS = (
    "context/*.md",
    "context/*.json",
    "glossary/*.yaml",
    "derived/fidelity-pages/*.json",
    "packets/*/review-template.json",
    "reviews/*.json",
    "reviews/*.jsonl",
    "reviews/external-dry-run/**/*.json",
    "evidence/pages/*",
    "evidence/audits/*.json",
    "evidence/audits/*.jsonl",
    "evidence/representations/**/*",
    "translations/*.jsonl",
    "qa/*.json",
    "qa/*.md",
    ".littrans/work/**/*",
)
# Initialization and rebuild always write these, so a missing one is a lost record file,
# not a stage the project has not reached yet.
INITIAL_RECORD_FILES = ("project.yaml", "derived/provenance.json")
RECORD_FILES = ("derived/units.jsonl", "derived/fidelity-assets.jsonl")
GAP_REPORT_LIMIT = 20


def _git(toplevel: Path, *args: str, stdin: str | None = None) -> str:
    """Ask git in bytes: a str stdin gains ``\\r`` on Windows and no pattern matches it then."""
    result = subprocess.run(
        ["git", "-C", str(toplevel), "-c", "core.quotePath=false", *args],
        input=None if stdin is None else stdin.encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if result.returncode not in (0, 1):
        raise ValueError(f"git {args[0]} failed: {result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout.decode("utf-8", "replace")


def git_toplevel(root: Path) -> Path:
    if shutil.which("git") is None:
        raise ValueError("git is not on PATH; the record check asks git which paths it tracks")
    result = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, check=False
    )
    if result.returncode != 0:
        raise ValueError(f"{root} is not inside a git repository; initialize one before checking what it tracks")
    return Path(result.stdout.decode("utf-8", "replace").strip()).resolve()


def record_sets(root: Path) -> tuple[set[str], set[str], list[str]]:
    """The record and the excluded set, relative to the project root, plus the live source packets."""
    root = Path(root).resolve()
    must_track: set[str] = set(INITIAL_RECORD_FILES)
    for relative in RECORD_FILES:
        if (root / relative).is_file():
            must_track.add(relative)
    for pattern in RECORD_GLOBS:
        must_track.update(path.relative_to(root).as_posix() for path in root.glob(pattern) if path.is_file())
    for directory in sorted(path for path in (root / "batches").glob("*") if path.is_dir()):
        must_track.update(f"batches/{directory.name}/{name}" for name in BATCH_FILES if (directory / name).is_file())
    must_ignore: set[str] = set()
    if (root / "derived" / "fidelity-assets.jsonl").is_file():
        for asset in load_assets(root).values():
            for fragment in asset.fragments:
                for relative in (fragment.png_path, fragment.svg_path):
                    if relative and (root / relative).is_file():
                        must_track.add(Path(relative).as_posix())
                directory = Path(fragment.png_path).parent
                if (root / directory / "evidence.json").is_file():
                    must_track.add((directory / "evidence.json").as_posix())
                if (root / directory / "original.pdf").is_file():
                    must_ignore.add((directory / "original.pdf").as_posix())
    # A packet named by a page receipt is a live dependency of that review (the verifier
    # refuses a receipt whose packet is missing). Unreferenced packets may still be kept
    # as historical evidence, so their core files are optional rather than excluded.
    liveness = source_packet_liveness(root)
    for name in liveness.get("live_source_packets", []):
        for filename in ("packet.json", "coverage.html"):
            must_track.add(f"packets/{name}/{filename}")
    if (root / ".littrans" / "state.json").is_file():
        must_ignore.add(".littrans/state.json")
    # The scaffold excludes the whole source directory, including nested PDFs and
    # companion files. A force-added file anywhere below it is still a privacy leak.
    must_ignore.update(
        path.relative_to(root).as_posix()
        for path in (root / "source").rglob("*") if path.is_file()
    )
    source = load_project(root).source(root)
    if source.is_relative_to(root):
        must_ignore.add(source.relative_to(root).as_posix())
    must_ignore.update(
        path.relative_to(root).as_posix()
        for path in (root / "output").rglob("*")
        if path.is_file() and path.relative_to(root).as_posix() != "output/.gitkeep"
    )
    # A detector result is evidence a page ledger names and no other runtime reproduces,
    # so every rerun (on any host) cuts the page on it; the request and log of the run
    # carry the paths and interpreter of the host that ran it and stay out.
    for path in (root / "derived" / "fidelity-layout").glob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if path.name.endswith(".json") and not path.name.endswith(".request.json"):
            must_track.add(relative)
        else:
            must_ignore.add(relative)
    return must_track, must_ignore, list(liveness.get("live_source_packets", []))


def record_tracking(root: Path) -> dict[str, Any]:
    """Classify every file under the project with git and report the gaps; read-only."""
    root = Path(root).resolve()
    config = load_project(root)
    toplevel = git_toplevel(root)
    if config.record_root_relative is not None:
        record_root = (root / config.record_root_relative).resolve()
    else:
        # Older project files did not record --repo-root. Prefer the nearest ancestor
        # carrying a distinctive scaffold file, then the git root for a nested layout.
        ancestors = (root, *root.parents[:len(root.parts) - len(toplevel.parts)])
        markers = ("docs/LITTRANS.md", "PLUGIN-ISSUES.md", "tools/lt.py")
        record_root = next(
            (ancestor for ancestor in ancestors if any((ancestor / name).is_file() for name in markers)),
            toplevel,
        )
    if not root.is_relative_to(record_root) or not record_root.is_relative_to(toplevel):
        raise ValueError(f"The record root {record_root} must be inside {toplevel} and contain {root}")
    prefix = root.relative_to(toplevel).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    project_track, project_ignore, live_packets = record_sets(root)
    scaffold_paths = {
        ((root if spec.at_project_root else record_root) / spec.relative).relative_to(toplevel).as_posix()
        for spec in SCAFFOLD_FILES
    }
    must_track = {prefix + path for path in project_track} | scaffold_paths
    must_ignore = {prefix + path for path in project_ignore}
    universe = {
        prefix + path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }
    if record_root != root:
        # The record root owns its top-level files, handbook, and launcher. Do not
        # sweep sibling projects into this project's check.
        shared = [path for path in record_root.iterdir() if path.is_file()]
        shared.extend(
            path for directory in ("docs", "tools")
            for path in (record_root / directory).rglob("*") if path.is_file()
        )
        # A PDF there is not presumed private: the configured source is queried by name
        # below, and one the repository's .gitignore excludes yet is tracked is reported
        # like any other force-added file.
        universe.update(path.relative_to(toplevel).as_posix() for path in shared)
    universe.update(path for path in scaffold_paths if (toplevel / path).is_file())
    # check-ignore only answers about what it is asked, so the whole universe goes in,
    # not just the declared sets, or every ignored scratch file reads as a gap.
    payload = "\n".join(sorted(universe | must_track | must_ignore))
    # --no-index also classifies force-added files; otherwise git suppresses ignored
    # paths already in its index and a private file can evade this check.
    ignored = set(_git(toplevel, "check-ignore", "--no-index", "--stdin", stdin=payload).splitlines())
    record_prefix = record_root.relative_to(toplevel).as_posix()
    pathspecs = [prefix or ".", record_prefix or "."] if record_root != root else [prefix or "."]
    tracked = set(_git(toplevel, "ls-files", "--", *pathspecs).splitlines())
    # Optional record files may be ignored by default or deliberately retained in git.
    # Keep the exception narrow: only source packet cores without a live receipt and
    # the output directory's own placeholder qualify.
    optional = {
        prefix + f"packets/{path.name}/{filename}"
        for path in (root / "packets").glob("source-*")
        if path.is_dir() and path.name not in live_packets
        for filename in ("packet.json", "coverage.html")
    }
    optional.add(prefix + "output/.gitkeep")
    problems: list[str] = []
    required_paths = scaffold_paths | {prefix + path for path in INITIAL_RECORD_FILES} | {
        prefix + f"packets/{name}/{filename}"
        for name in live_packets for filename in ("packet.json", "coverage.html")
    }
    for relative in sorted(must_track & must_ignore):
        problems.append(f"in both the record and the excluded set (a defect in this check, not the project): {relative}")
    for relative in sorted(must_track):
        if relative in required_paths and not (toplevel / relative).is_file():
            problems.append(f"required record file is missing: {relative}")
        elif relative not in tracked:
            if relative in ignored:
                problems.append(f"meant for the record but .gitignore excludes it: {relative}")
            else:
                problems.append(f"meant for the record but not committed: {relative}")
    for relative in sorted(must_ignore):
        if relative in tracked:
            problems.append(f"must never be tracked but is: {relative}")
    for relative in sorted((tracked & ignored & universe) - must_track - must_ignore - optional):
        problems.append(f"tracked despite .gitignore exclusion: {relative}")
    # A nested project may keep its source beside it in the same repository.
    # Query that one file explicitly; the rest of the report remains project-scoped.
    source = config.source(root)
    if source.is_relative_to(toplevel) and not source.is_relative_to(root):
        relative = source.relative_to(toplevel).as_posix()
        if _git(toplevel, "ls-files", "-z", "--", ":(literal)" + relative):
            problems.append(f"must never be tracked but is: {relative} (relative to repository root)")
    gap = sorted(universe - tracked - ignored)
    for relative in gap[:GAP_REPORT_LIMIT]:
        problems.append(f"neither tracked nor ignored (silently outside the record): {relative}")
    if len(gap) > GAP_REPORT_LIMIT:
        problems.append(f"... and {len(gap) - GAP_REPORT_LIMIT} more in the same state")
    return {
        "project_root": str(root),
        "git_toplevel": str(toplevel),
        "must_track": len(must_track),
        "must_ignore": len(must_ignore),
        "tracked": len(tracked & universe),
        "ignored": len(ignored & universe),
        "gap": len(gap),
        "live_source_packets": live_packets,
        "problems": problems,
    }
