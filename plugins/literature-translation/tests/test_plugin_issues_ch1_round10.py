"""Round 10: the reference-term channel (LT-039) and the project record scaffold (LT-040).

Reference entries are binding but not gated, reach packets filtered per unit like approved
terms, and therefore reset only the audits of batches that mention them. A new project is
born with its record structure, a launcher, a `.gitignore` that keeps the record in, and a
plugin-owned statement of facts that is regenerated rather than hand-written.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_efficiency_v4 import _audit_and_approve, _make_project, _submit
from typer.testing import CliRunner

from littrans import cli
from littrans.batching import load_manifest
from littrans.evidence import (
    audit_context_fingerprint,
    audit_context_parts,
    audit_context_text,
    fold_term_text,
    project_units,
    relevant_reference_terms,
    relevant_terms,
    term_matches,
)
from littrans.glossary import glossary_check, glossary_lookup
from littrans.project import initialize_project, load_reference_terms, load_terms, rebuild_project
from littrans.quality import audit_coverage
from littrans.record import record_sets, record_tracking
from littrans.rendering import _write_unresolved
from littrans.scaffold import (
    SCAFFOLD_FILES,
    plugin_facts,
    render_scaffold_template,
    scaffold_project,
)
from littrans.storage import PROJECT_DIRS, atomic_write_text, read_yaml, write_yaml
from littrans.workflow import create_workflow_packet

REFERENCE = {
    "terms": [
        {"kind": "proper-name", "source": "Banach", "aliases": ["Banach space"], "note": "keep source form"},
        {"kind": "sense", "source": "framework", "targets": ["框架", "体系"], "rule": "structure vs doctrine"},
        {"source": "template", "target": "模板"},
        {"source": "binding", "target": "绑定", "status": "proposed"},
    ]
}


def _write_reference(root: Path, payload: dict | None = None) -> None:
    write_yaml(root / "glossary" / "reference.yaml", payload or REFERENCE)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@example.org", "-c", "user.name=t", *args],
        capture_output=True, text=True, check=True,
    ).stdout


# --- LT-039: reference channel -------------------------------------------------------


def test_reference_terms_come_from_both_files_with_kind_and_status_rules(tmp_path: Path) -> None:
    root = tmp_path / "p"
    (root / "glossary").mkdir(parents=True)
    write_yaml(root / "glossary" / "approved.yaml", {"terms": [
        {"source": "measure", "target": "测度"},
        {"source": "Poisson", "status": "reference-only", "kind": "proper-name"},
        {"source": "layout", "target": "布局", "status": "proposed"},
    ]})
    _write_reference(root)
    assert [t["source"] for t in load_terms(root)] == ["measure"]
    reference = load_reference_terms(root)
    assert [(t["kind"], t["source"]) for t in reference] == [
        ("proper-name", "Poisson"), ("proper-name", "Banach"), ("sense", "framework"), ("reference", "template"),
    ]
    assert reference[2]["targets"] == ["框架", "体系"] and reference[2]["rule"] == "structure vs doctrine"
    assert list(reference[0]) [0] == "kind"
    # A gated entry does not belong in reference.yaml; aliases must be strings.
    _write_reference(root, {"terms": [{"source": "x", "target": "y", "status": "approved"}]})
    with pytest.raises(ValueError, match="never gates"):
        load_reference_terms(root)
    _write_reference(root, {"terms": [{"source": "x", "aliases": "Banach space"}]})
    with pytest.raises(ValueError, match="aliases as strings"):
        load_reference_terms(root)
    _write_reference(root, {"terms": [{"source": "x", "match": "regex", "aliases": ["("]}]})
    with pytest.raises(ValueError, match="not a valid regular expression"):
        load_reference_terms(root)


def test_aliases_select_an_entry_under_every_match_mode() -> None:
    folded = fold_term_text("Every Banach space is complete; the H¨older norm too.")
    assert term_matches({"source": "Hölder", "aliases": ["Banach space"]}, folded)
    assert term_matches({"source": "nothing", "aliases": ["banach"], "match": "word"}, folded)
    assert not term_matches({"source": "nothing", "aliases": ["banach"], "match": "word"}, fold_term_text("Banachs"))
    assert term_matches({"source": "nothing", "aliases": [r"\bbanach\b"], "match": "regex"}, folded)
    assert not term_matches({"source": "nothing", "aliases": ["hilbert"]}, folded)


def test_packets_carry_reference_terms_grouped_by_kind_only_when_relevant(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)
    units = project_units(root)
    before = audit_context_fingerprint(root, units)
    before_text = audit_context_text(root, units)
    assert "reference terminology" not in before_text
    _write_reference(root, {"terms": []})
    # An empty reference file changes nothing: existing audits keep their context.
    assert audit_context_fingerprint(root, units) == before
    _write_reference(root)
    write_yaml(root / "glossary" / "approved.yaml", {"terms": [{"source": "architecture", "target": "架构"}]})
    assert [t["source"] for t in relevant_terms(root, units)] == ["architecture"]
    reference = relevant_reference_terms(root, units)
    assert [t["source"] for t in reference] == ["framework", "template"]  # Banach absent, proposed inert
    text = audit_context_text(root, units)
    assert "# Relevant reference terminology (not gated)" in text
    assert "reference_terms:\n  sense:\n  - source: framework" in text
    assert "  reference:\n  - source: template" in text
    assert audit_context_fingerprint(root, units) != before
    packet = create_workflow_packet(root, "translate", [manifests[0].batch_id])
    assert not isinstance(packet, list)
    shared = (root / packet.files["shared"]).read_text(encoding="utf-8")
    assert "# Relevant reference terminology (not gated)" in shared
    context = (root / "batches" / manifests[0].batch_id / "context.md")
    from littrans.batching import refresh_batch
    refresh_batch(root, manifests[0].batch_id)
    assert "# Reference terminology (not gated)" in context.read_text(encoding="utf-8")
    parts = audit_context_parts(root, units)
    assert set(parts) == {"document-brief", "style-guide", "approved-terms", "reference-terms"}
    assert parts["reference-terms"]["lines"] > 0


def test_reference_entry_resets_only_the_batches_it_matches(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=3, max_words=100)
    for batch in manifests:
        _submit(root, batch.batch_id)
        _audit_and_approve(root, batch.batch_id)
    second_page = {unit.page for unit in project_units(root) if unit.unit_id in manifests[1].unit_ids}
    assert len(second_page) == 1
    assert all(audit_coverage(root, b.batch_id)["missing"] == {"chinese-style": [], "fidelity": [], "technical": []} for b in manifests)
    # The decisive counter-example: an entry scoped to one page leaves the other audits valid.
    _write_reference(root, {"terms": [{"kind": "proper-name", "source": "framework", "scope": f"page:{second_page.pop()}"}]})
    coverage = [audit_coverage(root, b.batch_id) for b in manifests]
    assert coverage[0]["missing"]["fidelity"] == [] and coverage[2]["missing"]["fidelity"] == []
    assert coverage[1]["missing"]["fidelity"] == list(manifests[1].unit_ids)
    stale = coverage[1]["stale"]["fidelity"][0]
    assert stale["reasons"] == ["context-changed"]
    assert stale["context_changes"] == [{"part": "reference-terms", "lines_before": 0, "lines_after": 4}]
    # A whole-file edit names the file and its growth.
    style = root / "context" / "style-guide.md"
    atomic_write_text(style, style.read_text(encoding="utf-8") + "- One more rule.\n- And another.\n")
    changes = audit_coverage(root, manifests[0].batch_id)["stale"]["fidelity"][0]["context_changes"]
    assert [c["part"] for c in changes] == ["style-guide"]
    assert changes[0]["lines_after"] == changes[0]["lines_before"] + 2


def test_unresolved_report_lists_only_undecided_candidates(tmp_path: Path) -> None:
    root = tmp_path / "p"
    for name in ("glossary", "reviews", "translations"):
        (root / name).mkdir(parents=True)
    write_yaml(root / "glossary" / "candidates.yaml", {"terms": [
        {"source": "generator", "target": "生成元", "status": "reference-only"},
        {"source": "lattice", "target": "格点", "status": "rejected"},
        {"source": "extremes", "status": "proposed"},
        {"source": "order statistics", "target": "次序统计量"},
    ]})
    _write_unresolved(root / "unresolved.md", root, set())
    report = (root / "unresolved.md").read_text(encoding="utf-8")
    assert "- `extremes` → [undecided]" in report and "- `order statistics` → 次序统计量" in report
    assert "generator" not in report and "lattice" not in report
    assert "2 decided candidate(s) omitted (reference-only: 1, rejected: 1)." in report


def test_glossary_lookup_and_check(tmp_path: Path) -> None:
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)
    _write_reference(root)
    write_yaml(root / "glossary" / "approved.yaml", {"terms": [
        {"source": "architecture", "target": "架构"},
        {"source": "Hilbert", "target": "希尔伯特"},
    ]})
    result = glossary_lookup(root, batch_id=manifests[0].batch_id)
    assert result["selection"]["unit_count"] == len(load_manifest(root, manifests[0].batch_id).unit_ids)
    assert [t["source"] for t in result["approved"]] == ["architecture"]
    assert {kind: [t["source"] for t in terms] for kind, terms in result["reference"].items()} == {
        "sense": ["framework"], "reference": ["template"],
    }
    assert glossary_lookup(root, pages="1", kind="sense")["reference"] == {
        "sense": [{"source": "framework", "targets": ["框架", "体系"], "rule": "structure vs doctrine"}]
    }
    assert glossary_lookup(root, unit_ids=list(manifests[1].unit_ids))["reference_total"] == 2
    sample = tmp_path / "draft.txt"
    sample.write_text("A Banach space with a template.", encoding="utf-8")
    text_result = glossary_lookup(root, text=sample)
    assert text_result["selection"]["scope_applied"] is False
    assert list(text_result["reference"]) == ["proper-name", "reference"]
    with pytest.raises(ValueError, match="exactly one selector"):
        glossary_lookup(root)
    with pytest.raises(ValueError, match="exactly one selector"):
        glossary_lookup(root, pages="1", batch_id=manifests[0].batch_id)
    check = glossary_check(root)
    assert [t["source"] for t in check["approved"]["never_matched"]] == ["Hilbert"]
    assert check["reference"]["by_kind"] == {"proper-name": 1, "sense": 1, "reference": 1}
    assert [t["source"] for t in check["reference"]["never_matched"]["proper-name"]] == ["Banach"]

    runner = CliRunner()
    listed = runner.invoke(cli.app, ["glossary", "lookup", str(root), "--batch-id", manifests[0].batch_id, "--jsonl"])
    assert listed.exit_code == 0, listed.output
    lines = [json.loads(line) for line in listed.output.splitlines() if line.strip()]
    assert [(line["channel"], line.get("kind"), line["source"]) for line in lines] == [
        ("approved", None, "architecture"), ("reference", "sense", "framework"), ("reference", "reference", "template"),
    ]
    pretty = runner.invoke(cli.app, ["glossary", "lookup", str(root), "--pages", "1-2"])
    assert pretty.exit_code == 0 and json.loads(pretty.output)["approved_total"] == 1
    refused = runner.invoke(cli.app, ["glossary", "lookup", str(root)])
    assert refused.exit_code == 1 and "Traceback" not in refused.output and "exactly one selector" in refused.output
    checked = runner.invoke(cli.app, ["glossary", "check", str(root)])
    assert checked.exit_code == 0 and json.loads(checked.output)["approved"]["total"] == 2


# --- LT-040: record scaffold -----------------------------------------------------------


def _init(tmp_path: Path, *, repo_root: Path | None = None) -> Path:
    root = tmp_path / "repo" / "workspace" if repo_root else tmp_path / "project"
    import pymupdf

    pdf = tmp_path / "book.pdf"
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), "A page.")
    document.save(pdf)
    initialize_project(pdf, root, "technical-book", "Scaffold Fixture", repo_root=repo_root)
    return root


def test_init_scaffolds_the_record_structure_once(tmp_path: Path) -> None:
    root = _init(tmp_path)
    expected = {spec.relative for spec in SCAFFOLD_FILES}
    present = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    assert expected <= present
    assert "context/chapters" not in PROJECT_DIRS and not (root / "context" / "chapters").exists()
    for relative in expected:
        assert b"\r\n" not in (root / relative).read_bytes(), relative
    brief = (root / "context" / "document-brief.md").read_text(encoding="utf-8")
    assert "Rules only" in brief and "glossary/reference.yaml" in brief
    assert "{{asset:ID}}" in (root / "context" / "style-guide.md").read_text(encoding="utf-8")
    assert read_yaml(root / "glossary" / "reference.yaml") == {"terms": []}
    assert load_reference_terms(root) == []
    # Idempotent: a second call keeps every user edit byte for byte and creates nothing.
    readme = root / "README.md"
    atomic_write_text(readme, "# My book\n")
    facts_before = (root / "docs" / "LITTRANS.md").read_bytes()
    report = scaffold_project(root)
    assert report["created"] == [] and report["refreshed"] == []
    assert readme.read_text(encoding="utf-8") == "# My book\n"
    assert (root / "docs" / "LITTRANS.md").read_bytes() == facts_before
    refreshed = scaffold_project(root, refresh=True)
    assert refreshed["refreshed"] == ["docs/LITTRANS.md"] and refreshed["created"] == []
    with pytest.raises(ValueError, match="already exists"):
        initialize_project(tmp_path / "book.pdf", root, "technical-book")


def test_init_places_records_at_the_repo_root_of_a_nested_layout(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    root = _init(tmp_path, repo_root=repo)
    assert (repo / "README.md").is_file() and (repo / "tools" / "lt.py").is_file()
    assert (repo / "docs" / "LITTRANS.md").is_file() and not (root / "README.md").exists()
    assert (root / ".gitignore").is_file() and (root / "glossary" / "reference.yaml").is_file()
    assert "`workspace/`" in (repo / "docs" / "LITTRANS.md").read_text(encoding="utf-8")
    assert "workspace/glossary/approved.yaml" in (repo / "AGENTS.md").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="must lie inside"):
        scaffold_project(root, repo_root=tmp_path / "elsewhere")


def test_plugin_facts_are_read_from_the_package(monkeypatch: pytest.MonkeyPatch) -> None:
    facts = plugin_facts()
    assert facts["lens_reviewer_batch_max"] == 3 and facts["term_match_modes"] == ["substring", "word", "regex"]
    rendered = render_scaffold_template("LITTRANS.md.j2", {**facts, "project_rel": ".", "project_prefix": "", "plugin_root": "x", "plugin_owned_file": "docs/LITTRANS.md"})
    assert facts["version"] in rendered and facts["build_digest"] in rendered
    assert "`context-changed`" in rendered and "`asset-audit`" in rendered and "claude 3 (max 6)" in rendered
    import littrans.hosts

    monkeypatch.setattr(littrans.hosts, "LENS_REVIEWER_BATCH_MAX", 5)
    import littrans.scaffold

    monkeypatch.setattr(littrans.scaffold, "LENS_REVIEWER_BATCH_MAX", 5)
    assert "at most 5 batches" in render_scaffold_template("LITTRANS.md.j2", {**plugin_facts(), "project_rel": ".", "project_prefix": "", "plugin_root": "x", "plugin_owned_file": "docs/LITTRANS.md"})
    for spec in SCAFFOLD_FILES:
        text = render_scaffold_template(spec.template, {**facts, "project_rel": ".", "project_prefix": "", "plugin_root": "x", "plugin_owned_file": "docs/LITTRANS.md"})
        assert "Weinan" not in text and "Stochastic" not in text, spec.template


def test_generated_gitignore_keeps_the_record_in_and_the_source_out(tmp_path: Path) -> None:
    root = _init(tmp_path)
    _git(root, "init", "-q")
    # Bytes, not text: a str stdin gains a carriage return on Windows and no rule matches.
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--stdin"],
        input=b"source/book.pdf\noutput/a.html\n.littrans/state.json\nderived/fidelity-layout/x.json\n"
              b"derived/fidelity-layout/x.request.json\nderived/fidelity-layout/x.log\n"
              b"packets/source-abc/packet.json\n.littrans/work/p/shared.md\nderived/units.jsonl\n"
              b"derived/assets/fidelity/x/original.pdf\nderived/assets/fidelity/x/original.png\n",
        capture_output=True, check=False,
    ).stdout.decode("utf-8").split()
    # A detector result is part of the record; the run's request and log are not.
    assert set(ignored) == {
        "source/book.pdf", "output/a.html", ".littrans/state.json", "derived/fidelity-layout/x.request.json",
        "derived/fidelity-layout/x.log", "packets/source-abc/packet.json", "derived/assets/fidelity/x/original.pdf",
    }
    # An existing .gitignore only gains the runtime-state pair, once.
    atomic_write_text(root / ".gitignore", "*.bak\n")
    assert scaffold_project(root)["refreshed"] == [".gitignore"]
    assert (root / ".gitignore").read_text(encoding="utf-8") == "*.bak\n.littrans/*\n!.littrans/work/\n"
    assert scaffold_project(root)["refreshed"] == []


def test_project_tracked_asks_git_and_reports_every_kind_of_gap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The temporary tree may itself sit inside a repository; git must not find that one.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    root, manifests = _make_project(tmp_path, pages=2, max_words=100)
    scaffold_project(root)
    _submit(root, manifests[0].batch_id)
    (root / "source" / "book.pdf").write_bytes(b"%PDF-1.4 not really")
    must_track, must_ignore, live = record_sets(root)
    assert "derived/units.jsonl" in must_track and f"batches/{manifests[0].batch_id}/translation.jsonl" in must_track
    assert "source/book.pdf" in must_ignore
    # The fixture's page receipts name one source packet: a live dependency of the review.
    assert len(live) == 1 and f"packets/{live[0]}/packet.json" in must_track
    assert any(path.startswith("packets/") and path not in must_track for path in must_ignore)
    with pytest.raises(ValueError, match="not inside a git repository"):
        record_tracking(root)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "record")
    # The generated .gitignore excludes every source packet; the one the receipts name is a
    # live dependency and is reported until it is re-included by name (LT-030).
    first = record_tracking(root)
    assert first["live_source_packets"] == live
    assert sorted(first["problems"]) == [
        f"meant for the record but .gitignore excludes it: packets/{live[0]}/coverage.html",
        f"meant for the record but .gitignore excludes it: packets/{live[0]}/packet.json",
    ]
    ignore = root / ".gitignore"
    atomic_write_text(ignore, ignore.read_text(encoding="utf-8") + f"!packets/{live[0]}/packet.json\n!packets/{live[0]}/coverage.html\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "live packet")
    clean = record_tracking(root)
    assert clean["problems"] == [] and clean["gap"] == 0 and clean["must_ignore"] >= 1
    # A record file excluded, a record file uncommitted, and a file in neither set.
    atomic_write_text(root / ".gitignore", (root / ".gitignore").read_text(encoding="utf-8") + "derived/units.jsonl\n")
    _git(root, "rm", "-q", "--cached", "derived/units.jsonl")
    atomic_write_text(root / "qa" / "new.md", "report\n")
    atomic_write_text(root / "notes.txt", "scratch\n")
    problems = record_tracking(root)["problems"]
    assert any(p.startswith("meant for the record but .gitignore excludes it: derived/units.jsonl") for p in problems)
    assert "meant for the record but not committed: qa/new.md" in problems
    assert any("neither tracked nor ignored" in p and "notes.txt" in p for p in problems)
    runner = CliRunner()
    result = runner.invoke(cli.app, ["project", "tracked", str(root)])
    assert result.exit_code == 1 and json.loads(result.output)["gap"] >= 1


def test_launcher_resolves_the_newest_sibling_when_the_recorded_root_moved(tmp_path: Path) -> None:
    root = _init(tmp_path)
    launcher = root / "tools" / "lt.py"
    cache = tmp_path / "cache"
    for name in ("0.6.0-dev.7", "0.6.0", "0.10.0-dev.1", "0.9.9", "notes"):
        (cache / name / "scripts").mkdir(parents=True)
        (cache / name / "scripts" / "littrans.py").write_text("import sys; print(sys.argv[0])\n", encoding="utf-8")
    text = launcher.read_text(encoding="utf-8")
    text = text.replace(text[text.index("RECORDED_PLUGIN_ROOT = "):text.index("\n", text.index("RECORDED_PLUGIN_ROOT = "))],
                        f"RECORDED_PLUGIN_ROOT = r\"{cache / '0.6.0-dev.6'}\"")
    launcher.write_text(text, encoding="utf-8")
    import importlib.util

    spec = importlib.util.spec_from_file_location("lt_launcher", launcher)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.resolve_plugin_root() == cache / "0.10.0-dev.1"
    (cache / "0.10.0-dev.1" / "scripts" / "littrans.py").unlink()
    assert module.resolve_plugin_root() == cache / "0.9.9"
    assert module.version_key("0.6.0") > module.version_key("0.6.0-dev.7") > module.version_key("0.5.9")
    import os

    os.environ["LITTRANS_PLUGIN_ROOT"] = str(cache / "0.6.0")
    try:
        assert module.resolve_plugin_root() == cache / "0.6.0"
    finally:
        del os.environ["LITTRANS_PLUGIN_ROOT"]


def test_rebuild_copies_docs_and_refreshes_the_plugin_facts(tmp_path: Path) -> None:
    root = _init(tmp_path)
    atomic_write_text(root / "docs" / "DECISIONS.md", "# Decisions\n\nkept\n")
    atomic_write_text(root / "docs" / "LITTRANS.md", "stale hand-edit\n")
    rebuilt = tmp_path / "rebuilt"
    rebuild_project(root, rebuilt)
    assert (rebuilt / "docs" / "DECISIONS.md").read_text(encoding="utf-8") == "# Decisions\n\nkept\n"
    assert "stale hand-edit" not in (rebuilt / "docs" / "LITTRANS.md").read_text(encoding="utf-8")
    provenance = json.loads((rebuilt / "derived" / "rebuild-provenance.json").read_text(encoding="utf-8"))
    assert provenance["copied"] == ["source", "context", "glossary", "docs"]
    assert (rebuilt / "tools" / "lt.py").is_file()
