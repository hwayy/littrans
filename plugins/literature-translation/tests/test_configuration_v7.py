import copy
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from littrans.cli import app
from littrans.configuration import apply, edit, read_settings, show, yaml_read, yaml_text
from littrans.project import initialize_project
from littrans.settings import ProjectSettings
from littrans.storage import load_project, save_project


@pytest.fixture
def configured(tmp_path: Path) -> Path:
    source = tmp_path / "source.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "A configuration fixture.")
        pdf.save(source)
    root = tmp_path / "project"
    initialize_project(source, root, "technical-book")
    return root


def test_manifest_settings_state_are_separate(configured):
    manifest = yaml_read(configured / "project.yaml")
    assert manifest["schema_version"] == 7
    assert "title" not in manifest and "status" not in manifest
    before = (configured / "settings.yaml").read_bytes()
    config = load_project(configured)
    save_project(configured, config)
    assert (configured / "settings.yaml").read_bytes() == before
    assert (configured / "derived/project-state.json").exists()
    assert "settings.local.yaml" in (configured / ".gitignore").read_text()


def test_state_save_rejects_missing_or_invalid_settings(configured):
    config = load_project(configured)
    state = (configured / "derived/project-state.json").read_bytes()
    path = configured / "settings.yaml"
    original = path.read_bytes()
    path.unlink()
    with pytest.raises(ValueError, match="settings.yaml"):
        save_project(configured, config)
    assert not path.exists()
    assert (configured / "derived/project-state.json").read_bytes() == state
    path.write_bytes(original + b"unexpected: true\n")
    with pytest.raises(ValueError):
        save_project(configured, config)
    assert (configured / "derived/project-state.json").read_bytes() == state


def test_semantically_equal_numbers_have_stable_digest(configured):
    path = configured / "settings.yaml"
    text = path.read_text(encoding="utf-8").replace("confidence_below: 0.9", "confidence_below: 1")
    path.write_text(text, encoding="utf-8")
    before = show(configured)
    result = apply(configured, before["value"], expect=before["sha256"])
    assert not result["written"]
    assert result["sha256"] == show(configured)["sha256"]
    assert path.read_text(encoding="utf-8") == text


def test_context_rejects_overlapping_approved_terms_and_duplicate_keys(configured):
    from littrans.context_config import validate
    path = configured / "glossary/approved.yaml"
    path.write_text(yaml_text({"terms": [
        {"source": "signal", "target": "信号", "scope": "document"},
        {"source": "signal", "target": "讯号", "scope": "page:1"},
    ]}), encoding="utf-8")
    with pytest.raises(ValueError, match="conflict"):
        validate(configured)
    path.write_text("terms: []\nterms: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid YAML"):
        validate(configured)


def test_rebuild_rejects_invalid_copied_context(configured):
    from littrans.project import rebuild_project
    (configured / "glossary/approved.yaml").write_text("terms: broken\n", encoding="utf-8")
    new = configured.parent / "rebuilt"
    with pytest.raises(ValueError, match="terms"):
        rebuild_project(configured, new)
    assert not new.exists()


def test_legacy_external_migration_cannot_write_old_project(configured):
    from littrans.external_recheck import migrate_external_config
    path = configured / "project.yaml"
    payload = yaml_read(path)
    payload["schema_version"] = 6
    path.write_text(yaml_text(payload), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="rebuild"):
        migrate_external_config(configured, apply=True)
    assert path.read_bytes() == before


def test_yaml_reads_are_isolated_and_observe_direct_edits(configured):
    path = configured / "settings.yaml"
    read = yaml_read(path)
    read["document"]["title"] = "Must not leak"
    assert yaml_read(path)["document"]["title"] != "Must not leak"
    path.write_text(yaml_text(read), encoding="utf-8")
    assert read_settings(configured).document.title == "Must not leak"


def test_removing_only_valid_source_binding_is_rejected(configured):
    before = show(configured, local=True)
    with pytest.raises(ValueError, match="identity"):
        edit(configured, "source_path", unset=True, local=True)
    assert show(configured, local=True) == before


def test_config_atomic_replace_failure_preserves_original(configured, monkeypatch):
    from littrans import storage
    path = configured / "settings.yaml"
    before = path.read_bytes()
    original = storage.os.replace
    def fail(source, target):
        if Path(target) == path:
            raise OSError("injected replace failure")
        return original(source, target)
    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="injected"):
        edit(configured, "batch.max_source_words", 1200)
    assert path.read_bytes() == before
    assert not list(configured.glob(".settings.yaml.*"))


def test_required_unknown_duplicate_and_atomic_validation(configured):
    before = show(configured)
    invalid = copy.deepcopy(before["value"])
    del invalid["batch"]["max_source_words"]
    with pytest.raises(ValueError):
        apply(configured, invalid)
    with pytest.raises(ValueError):
        edit(configured, "external_review.primary", "missing")
    assert show(configured) == before
    duplicate = configured / "duplicate.yaml"
    duplicate.write_text("key: 1\nkey: 2\n")
    with pytest.raises(ValueError, match="Invalid YAML"):
        yaml_read(duplicate)


def test_comment_noop_dryrun_conflict(configured):
    path = configured / "settings.yaml"
    path.write_text("# Keep this project note\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
    before = path.read_bytes()
    report = edit(configured, "batch.max_source_words", 1100, dry_run=True)
    assert not report["written"] and path.read_bytes() == before
    edit(configured, "batch.max_source_words", 1100)
    assert path.read_text().startswith("# Keep this project note")
    with pytest.raises(ValueError, match="changed"):
        apply(configured, show(configured)["value"], expect=report["previous_sha256"])
    before = path.read_bytes()
    assert not apply(configured, show(configured)["value"])["written"]
    assert before == path.read_bytes()


def test_dispatch_inheritance_explicit_null_and_lenses(configured):
    edit(configured, "agents.codex.roles.translate.model", "writer")
    assert load_project(configured).dispatch("codex", "revise").model == "writer"
    edit(configured, "agents.codex.roles.revise.model", None)
    assert load_project(configured).dispatch("codex", "revise").model is None
    edit(configured, "agents.codex.audit_lenses.technical.model", "technical")
    assert load_project(configured).dispatch("codex", "audit", "technical").model == "technical"
    assert load_project(configured).dispatch("codex", "audit", "fidelity").model != "technical"
    edit(configured, "agents.cursor.roles.translate.model", "unsupported")
    with pytest.raises(ValueError, match="cannot apply"):
        load_project(configured).dispatch("cursor", "translate")


def test_model_view_and_dispatch_share_roles_and_lenses(configured):
    from littrans.configuration import effective
    from littrans.project import dispatch_report
    from littrans.settings import LENSES, ROLES
    edit(configured, "agents.codex.audit_lenses.technical.model", "technical-reviewer")
    view = effective(configured, "codex")
    report = dispatch_report(configured, "codex")
    assert report["roles"] == view["roles"]
    assert report["audit_lenses"] == view["audit_lenses"]
    config = load_project(configured)
    for role in ROLES:
        assert config.dispatch("codex", role).model_dump() == view["roles"][role]
    for lens in LENSES:
        assert config.dispatch("codex", "audit", lens).model_dump() == view["audit_lenses"][lens]
    edit(configured, "agents.cursor.audit_lenses.technical.model", "unsupported")
    with pytest.raises(ValueError, match=r"audit_lenses.technical.model null"):
        load_project(configured).dispatch("cursor", "audit", "technical")


def test_scoped_terminology_remains_distinct(configured):
    from littrans.context_config import validate
    (configured / "glossary/approved.yaml").write_text(yaml_text({"terms": [
        {"source": "signal", "target": "A", "scope": "page:1"},
        {"source": "signal", "target": "B", "scope": "page:2"},
    ]}), encoding="utf-8")
    assert validate(configured)["valid"]


def test_local_cannot_override_policy_or_change_source(configured):
    before = (configured / "settings.local.yaml").read_bytes()
    with pytest.raises(ValueError):
        edit(configured, "agents", {}, local=True)
    with pytest.raises(ValueError, match="identity"):
        edit(configured, "source_path", "missing.pdf", local=True)
    assert (configured / "settings.local.yaml").read_bytes() == before


def test_cli_typed_values_and_schema(configured):
    runner = CliRunner()
    assert runner.invoke(app, ["config", "set", str(configured), "batch.max_source_words", "1200", "--json"]).exit_code == 0
    assert read_settings(configured).batch.max_source_words == 1200
    assert runner.invoke(app, ["config", "validate", str(configured), "--host", "codex"]).exit_code == 0
    assert runner.invoke(app, ["config", "schema"]).exit_code == 0
    payload = read_settings(configured).payload()
    assert ProjectSettings.model_validate(payload).payload() == payload


def test_policy_domains_do_not_invalidate_on_dispatch_or_presentation(configured):
    from littrans.configuration import policy_domains
    before = policy_domains(read_settings(configured).payload())
    edit(configured, "agents.codex.roles.translate.model", "different-model")
    assert policy_domains(read_settings(configured).payload()) == before
    edit(configured, "translation.equations.inline", "reviewed-transcription")
    after = policy_domains(read_settings(configured).payload())
    assert after["translation"] == before["translation"]
    assert after["presentation"] != before["presentation"]
    edit(configured, "translation.prose_punctuation", "preserve")
    assert policy_domains(read_settings(configured).payload())["translation"] != before["translation"]


def test_context_transaction_preview_conflict_and_rollback(configured, monkeypatch):
    from littrans import context_config
    brief = configured / "new-brief.md"
    style = configured / "new-style.md"
    brief.write_text("New audience.\n", encoding="utf-8")
    style.write_text("New style.\n", encoding="utf-8")
    manifest = configured / "context-change.yaml"
    manifest.write_text(yaml_text({"resources": {"brief": brief.name, "style": style.name}}), encoding="utf-8")
    previous = {name: (configured / name).read_bytes() for name in ("context/document-brief.md", "context/style-guide.md")}
    preview = context_config.apply(configured, manifest, dry_run=True)
    assert not preview["written"] and set(preview["changed"]) == {"brief", "style"}
    with pytest.raises(ValueError, match="changed"):
        context_config.apply(configured, manifest, expect="stale")
    writer = context_config.atomic_write_text
    def fail_second(path, value):
        if path == configured / "context/style-guide.md":
            raise OSError("seeded failure")
        writer(path, value)
    monkeypatch.setattr(context_config, "atomic_write_text", fail_second)
    with pytest.raises(OSError, match="seeded"):
        context_config.apply(configured, manifest, expect=preview["previous_sha256"])
    assert all((configured / path).read_bytes() == value for path, value in previous.items())


@pytest.mark.parametrize("kind,setting", [("comment", "translate_comments"), ("string", "translate_string_literals")])
def test_code_annotations_require_policy_permission(configured, kind, setting):
    from littrans.models import CodeAnnotation, SourceUnit, TranslationRecord
    from littrans.policy import record_errors
    unit = SourceUnit(unit_id="u", page=1, kind="code", bbox=(0, 0, 100, 100), source_text="print('hello')", source_hash="x", confidence=1)
    record = TranslationRecord(unit_id="u", source_hash="x", target_text=unit.source_text,
                               code_annotations=[CodeAnnotation(kind=kind, source="hello", target="你好")])
    assert record_errors(configured, unit, record)
    edit(configured, "translation.code." + setting, True)
    assert record_errors(configured, unit, record) == []


@pytest.mark.parametrize("relative", [False, True])
def test_external_references_and_machine_binding(configured, relative):
    candidate = show(configured)["value"]
    candidate["external_review"].update(enabled=True, primary="primary", reviewers={
        "primary": {"driver": "codex-cli", "model": "review-model", "model_identity": None, "effort": "high"}}, timeout_seconds=42)
    apply(configured, candidate)
    executable = configured.parent / "Tools" / "provider.exe"
    binding = "../Tools/provider.exe" if relative else str(executable)
    edit(configured, "commands.primary", binding, local=True)
    runtime = load_project(configured).external_review.reviewer
    assert Path(runtime.command) == executable.resolve()
    assert runtime._timeout_seconds == 42
    assert "provider.exe" not in (configured / "settings.yaml").read_text(encoding="utf-8")


def test_settings_can_repair_an_invalid_value(configured):
    path = configured / "settings.yaml"
    value = yaml_read(path)
    value["batch"]["max_source_words"] = 5
    path.write_text(yaml_text(value), encoding="utf-8")
    with pytest.raises(ValueError):
        load_project(configured)
    edit(configured, "batch.max_source_words", 900)
    assert load_project(configured)._settings.batch.max_source_words == 900


@pytest.mark.parametrize("field,value", [
    ("translation.memory_minimum_status", "extracted"),
    ("verification.block_unfinished_transcription", "false"),
    ("external_review.recheck.confidence_below", "0.5"),
    ("external_review.recheck.severities", ["major", "major"]),
    ("external_review.timeout_seconds", 0),
    ("agents.codex.roles.unknown", {}),
])
def test_invalid_policy_values_are_rejected_without_write(configured, field, value):
    before = (configured / "settings.yaml").read_bytes()
    with pytest.raises(ValueError):
        edit(configured, field, value)
    assert (configured / "settings.yaml").read_bytes() == before


def test_context_promotion_requires_candidate_removal_and_reason(configured):
    from littrans import context_config
    candidate = {"terms": [{"source": "signal", "target": "信号", "status": "proposed"}]}
    (configured / "glossary/candidates.yaml").write_text(yaml_text(candidate), encoding="utf-8")
    (configured / "approve.yaml").write_text(yaml_text({"terms": [{"source": "signal", "target": "信号"}]}), encoding="utf-8")
    (configured / "empty.yaml").write_text("terms: []\n", encoding="utf-8")
    manifest = configured / "promote.yaml"
    spec = {"resources": {"approved": "approve.yaml"}, "reason": "Checked source usage"}
    manifest.write_text(yaml_text(spec), encoding="utf-8")
    with pytest.raises(ValueError, match="same transaction"):
        context_config.apply(configured, manifest)
    spec["resources"]["candidates"] = "empty.yaml"
    manifest.write_text(yaml_text(spec), encoding="utf-8")
    assert context_config.apply(configured, manifest)["written"]
    assert (configured / "context/decisions.jsonl").is_file()


@pytest.mark.parametrize("mode,expected", [("auto", "heading"), ("pdf-bookmarks", "heading"), ("font-and-numbering", "paragraph")])
def test_outline_policy_consumes_pdf_bookmarks(tmp_path, mode, expected):
    from littrans.fidelity import prepare_source
    from littrans.models import SourceUnit
    from littrans.storage import read_jsonl
    source = tmp_path / "outline.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((72, 72), "A bookmark title")
        pdf.set_toc([[1, "A bookmark title", 1]])
        pdf.save(source)
    root = tmp_path / "project"
    initialize_project(source, root, "technical-book")
    edit(root, "outline_source", mode)
    prepare_source(root, allow_missing_layout=True)
    units = read_jsonl(root / "derived/units.jsonl", SourceUnit)
    assert units[0].kind == expected


@pytest.mark.parametrize("mode,blocked", [("original", False), ("reviewed-transcription", True)])
def test_formula_policy_requires_reviewed_representation(configured, monkeypatch, mode, blocked):
    from types import SimpleNamespace

    from littrans import fidelity_models, representations
    from littrans.policy import delivery_errors
    asset = SimpleNamespace(kind="math", model_dump=lambda **kwargs: {"kind": "math", "display": True})
    monkeypatch.setattr(fidelity_models, "load_assets", lambda root: {"formula": asset})
    monkeypatch.setattr(representations, "representation_status", lambda root, ids: {"assets": {"formula": {"state": "missing"}}})
    edit(configured, "translation.equations.display", mode)
    assert bool(delivery_errors(configured, ["formula"])) is blocked


@pytest.mark.parametrize("fallback,blocked", [(True, False), (False, True)])
def test_table_fallback_gate(configured, monkeypatch, fallback, blocked):
    from types import SimpleNamespace

    from littrans import fidelity_models, representations
    from littrans.policy import delivery_errors
    monkeypatch.setattr(fidelity_models, "load_assets", lambda root: {"table": SimpleNamespace(kind="table")})
    monkeypatch.setattr(representations, "representation_status", lambda root, ids: {"assets": {"table": {"state": "missing"}}})
    edit(configured, "translation.tables.presentation", "reviewed-transcription")
    edit(configured, "translation.tables.image_fallback_in_final", fallback)
    assert bool(delivery_errors(configured, ["table"])) is blocked


def test_reader_note_and_caption_policy_have_real_consumers(configured):
    from littrans.models import ReaderNote, SourceUnit, TranslationRecord
    from littrans.policy import record_errors
    unit = SourceUnit(unit_id="u", page=1, kind="caption", bbox=(0, 0, 100, 100), source_text="Figure caption", source_hash="x", confidence=1)
    record = TranslationRecord(unit_id="u", source_hash="x", target_text="图注", reader_note=ReaderNote(text="Background"))
    assert record_errors(configured, unit, record)
    edit(configured, "translation.reader_notes.require_primary_https_sources", False)
    assert record_errors(configured, unit, record) == []
    edit(configured, "translation.reader_notes.allow_modernization", False)
    assert record_errors(configured, unit, record)
    record.reader_note = None
    edit(configured, "translation.figures.translate_caption", False)
    assert record_errors(configured, unit, record)
    record.target_text = unit.source_text
    assert record_errors(configured, unit, record) == []


@pytest.mark.parametrize("preserve", [True, False])
def test_figure_original_text_policy_controls_companion_requirement(configured, monkeypatch, preserve):
    from littrans import representations
    edit(configured, "translation.figures.internal_labels", "preserve" if preserve else "explain-below")
    monkeypatch.setattr(representations, "_assets", lambda root: {"figure": {"kind": "figure"}})
    errors = representations.validate_asset_translations(configured, "{{asset:figure}}", [])
    assert bool(errors) is not preserve


def test_schema_describes_each_settings_leaf():
    from littrans.settings import settings_schema
    schema = settings_schema()
    fields = schema["x-fields"]
    assert "agents.*.roles.*.model" in fields
    assert "translation.equations.inline" in fields
    assert "external_review.recheck.confidence_below" in fields
    assert all(field["x-consumer"] and field["x-change-effect"] for field in fields.values())


def test_get_effective_uses_the_same_field_paths(configured):
    import json
    edit(configured, "agents.codex.roles.translate.model", "inherited-model")
    result = CliRunner().invoke(app, ["config", "get", str(configured), "agents.codex.roles.revise.model", "--effective", "--host", "codex"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["value"] == "inherited-model"
