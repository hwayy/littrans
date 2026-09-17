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
    "evidence/pages/*",
    "evidence/audits/*.json",
    "evidence/audits/*.jsonl",
    "translations/*.jsonl",
    "qa/*.json",
    "qa/*.md",
    ".littrans/work/**/*",
)
RECORD_FILES = ("project.yaml", "derived/units.jsonl", "derived/fidelity-assets.jsonl", "derived/provenance.json")
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
    must_track: set[str] = set()
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
    # refuses a receipt whose packet is missing); unreferenced packets duplicate tracked
    # tables. A path lands in exactly one set, so a conflict is reported, not silently won.
    liveness = source_packet_liveness(root) if (root / "packets").is_dir() else {}
    for name in liveness.get("live_source_packets", []):
        for filename in ("packet.json", "coverage.html"):
            if (root / "packets" / name / filename).is_file():
                must_track.add(f"packets/{name}/{filename}")
    for name in liveness.get("unreferenced_source_packets", []):
        for filename in ("packet.json", "coverage.html"):
            if (root / "packets" / name / filename).is_file():
                must_ignore.add(f"packets/{name}/{filename}")
    if (root / ".littrans" / "state.json").is_file():
        must_ignore.add(".littrans/state.json")
    must_ignore.update(path.relative_to(root).as_posix() for path in (root / "source").glob("*.pdf"))
    must_ignore.update(path.relative_to(root).as_posix() for path in (root / "output").glob("*.html"))
    must_ignore.update(
        path.relative_to(root).as_posix() for path in (root / "derived" / "fidelity-layout").glob("*") if path.is_file()
    )
    return must_track, must_ignore, list(liveness.get("live_source_packets", []))


def record_tracking(root: Path) -> dict[str, Any]:
    """Classify every file under the project with git and report the gaps; read-only."""
    root = Path(root).resolve()
    load_project(root)
    toplevel = git_toplevel(root)
    prefix = root.relative_to(toplevel).as_posix()
    prefix = "" if prefix == "." else prefix + "/"
    must_track, must_ignore, live_packets = record_sets(root)
    universe = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.relative_to(root).parts
    }
    # check-ignore only answers about what it is asked, so the whole universe goes in,
    # not just the declared sets, or every ignored scratch file reads as a gap.
    payload = "\n".join(sorted(prefix + path for path in universe | must_track | must_ignore))
    ignored = {line[len(prefix):] for line in _git(toplevel, "check-ignore", "--stdin", stdin=payload).splitlines() if line}
    tracked = {line[len(prefix):] for line in _git(toplevel, "ls-files", "--", prefix or ".").splitlines() if line}
    problems: list[str] = []
    for relative in sorted(must_track & must_ignore):
        problems.append(f"in both the record and the excluded set (a defect in this check, not the project): {relative}")
    for relative in sorted(must_track):
        if relative in ignored:
            problems.append(f"meant for the record but .gitignore excludes it: {relative}")
        elif relative not in tracked:
            problems.append(f"meant for the record but not committed: {relative}")
    for relative in sorted(must_ignore):
        if relative in tracked:
            problems.append(f"must never be tracked but is: {relative}")
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
