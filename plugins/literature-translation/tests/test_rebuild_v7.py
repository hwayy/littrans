"""v7 rebuilds keep validated policy, never evidence, and report every configuration outcome."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from config_fixtures import save_project as seed_project
from test_efficiency_v4 import _audit_and_approve, _make_project, _submit
from typer.testing import CliRunner

from littrans.cli import app
from littrans.configuration import apply, differences, edit, read_local, read_settings, show
from littrans.models import (
    ExternalReviewConfig,
    ExternalReviewerConfig,
    ProjectStatus,
    TranslationRecord,
)
from littrans.project import rebuild_project, translation_map
from littrans.settings import preset
from littrans.storage import load_project, read_json, read_jsonl

SECTIONS = ["preset", "document", "batch", "outline_source", "agents", "translation",
            "verification", "external_review"]


def snapshot(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def configured(tmp_path: Path) -> tuple[Path, str]:
    """An approved v7 batch plus non-default policy and machine command bindings."""
    root, manifests = _make_project(tmp_path, 1)
    batch = manifests[0].batch_id
    _submit(root, batch)
    _audit_and_approve(root, batch)
    candidate = copy.deepcopy(show(root)["value"])
    candidate["agents"]["claude"]["roles"]["translate"] = {"model": "opus"}
    candidate["agents"]["claude"]["audit_lenses"]["technical"] = {"model": "sonnet"}
    candidate["agents"]["claude"]["wave_size"] = 2
    candidate["batch"]["max_source_words"] = 700
    candidate["translation"]["reader_notes"]["allow_modernization"] = False
    candidate["verification"]["block_unfinished_transcription"] = True
    candidate["external_review"].update(
        enabled=True, primary="primary", fallbacks=["backup"], timeout_seconds=42,
        domain_expertise="Numerical linear algebra",
        reviewers={
            "primary": {"driver": "codex-cli", "model": "review-model", "model_identity": None, "effort": "high"},
            "backup": {"driver": "opencode-cli", "model": "openai/gpt-6-luna#low", "model_identity": None,
                       "effort": None},
        },
    )
    apply(root, candidate)
    edit(root, "commands.primary", "../Tools/provider.exe", local=True)
    edit(root, "commands.backup", "opencode-nightly", local=True)
    from littrans.agent_config import configure_agents
    configure_agents(root, "opencode", write=True)
    path = root / "settings.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# Reviewed policy: keep this comment.\n",
                    encoding="utf-8")
    return root, batch


def assert_no_inherited_evidence(new: Path) -> None:
    assert not read_jsonl(new / "translations/current.jsonl", TranslationRecord)
    assert not list((new / "batches").iterdir())
    assert not list((new / "qa").glob("*"))
    assert not list((new / "reviews").glob("*.json*"))
    assert not list((new / "evidence").rglob("*.json*"))
    assert load_project(new).status is ProjectStatus.INITIALIZED


def test_v7_rebuild_preserves_validated_settings_and_bindings(configured, tmp_path: Path) -> None:
    old, batch = configured
    before = snapshot(old)
    old_config = load_project(old)
    old_local = read_local(old)
    new = tmp_path / "rebuilt"
    report: dict = {}
    result = rebuild_project(old, new, report=report)

    assert snapshot(old) == before
    assert {record.status for record in translation_map(old).values()} == {ProjectStatus.MACHINE_REVIEWED}
    assert (new / "settings.yaml").read_bytes() == (old / "settings.yaml").read_bytes()
    assert read_settings(new).payload() == read_settings(old).payload()
    assert_no_inherited_evidence(new)
    rebuilt_local = read_local(new)
    assert rebuilt_local.source_path is None
    assert rebuilt_local.commands == {"backup": "opencode-nightly",
                                      "primary": str((old / "../Tools/provider.exe").resolve())}
    runtime = result.external_review
    assert runtime is not None and runtime.enabled
    assert runtime.reviewer.command == old_config.external_review.reviewer.command
    assert [item.command for item in runtime.fallbacks] == ["opencode-nightly"]
    assert runtime.reviewer._timeout_seconds == 42
    assert result.dispatch("claude", "audit", "technical").model == "sonnet"

    configuration = report["configuration"]
    expected_not_migrated = (
        [{"path": "settings.local.yaml:source_path", "reason": f"Replaced by the copied {result.source_path}."}]
        if old_local.source_path else []
    )
    assert configuration == {
        "mode": "preserve", "source_schema_version": 7,
        "preset": {"name": "technical-book", "version": "1"},
        "preserved": SECTIONS, "reset": [],
        "local_migrated": [{"path": "commands.backup", "binding": "command-name"},
                           {"path": "commands.primary", "binding": "path"}],
        "not_migrated": [*expected_not_migrated, {
            "path": ".littrans/host-agents/opencode.json",
            "reason": "Generated native agent files are not copied."}],
        "next_actions": ["project agents NEW --host opencode --check"],
    }
    assert not (new / ".littrans/host-agents").exists() and not (new / ".opencode").exists()
    assert report["inherited_approvals"] is False
    recorded = read_json(new / "derived/rebuild-provenance.json")
    assert recorded["configuration"] == configuration and not recorded["inherited_approvals"]
    assert "provider.exe" not in json.dumps(recorded)
    assert load_project(old).status is not ProjectStatus.INITIALIZED and batch


def test_explicit_preset_reports_every_reset_difference(configured, tmp_path: Path) -> None:
    old, _ = configured
    before = snapshot(old)
    old_settings = read_settings(old)
    new = tmp_path / "rebuilt"
    report: dict = {}
    rebuild_project(old, new, settings="preset", preset="research-paper", report=report)

    document = old_settings.document.model_dump()
    expected = preset("research-paper", document["title"], document["source_language"],
                      document["target_language"]).payload()
    expected["document"] = document
    assert read_settings(new).payload() == expected
    assert snapshot(old) == before
    assert_no_inherited_evidence(new)
    configuration = report["configuration"]
    assert configuration["mode"] == "preset"
    assert configuration["preset"]["name"] == "research-paper"
    assert configuration["reset"] == differences(old_settings.payload(), expected)
    reset_paths = {item["path"] for item in configuration["reset"]}
    assert {"external_review.enabled", "batch.max_source_words",
            "agents.claude.roles.translate.model"} <= reset_paths
    assert "document" in configuration["preserved"]
    assert not set(configuration["preserved"]) & {path.split(".", 1)[0] for path in reset_paths}
    # Bindings for reviewers the preset no longer defines are reported, never written.
    assert read_local(new).commands == {}
    assert {"settings.local.yaml:commands.backup", "settings.local.yaml:commands.primary"} <= {
        item["path"] for item in configuration["not_migrated"]}
    assert configuration["local_migrated"] == []
    assert configuration["next_actions"] == ["project agents NEW --host opencode --check",
                                             "config show NEW; config apply NEW CANDIDATE --dry-run"]


def test_preset_name_requires_explicit_preset_mode(configured, tmp_path: Path) -> None:
    old, _ = configured
    new = tmp_path / "rebuilt"
    with pytest.raises(ValueError, match="--preset requires --settings preset"):
        rebuild_project(old, new, preset="research-paper")
    with pytest.raises(ValueError, match="preserve or preset"):
        rebuild_project(old, new, settings="defaults")
    assert not new.exists()


def test_invalid_v7_settings_block_rebuild(configured, tmp_path: Path) -> None:
    old, _ = configured
    path = old / "settings.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "unexpected: true\n", encoding="utf-8")
    new = tmp_path / "rebuilt"
    with pytest.raises(ValueError):
        rebuild_project(old, new)
    assert not new.exists()


def test_historical_project_reports_preset_defaults_and_legacy_policy(tmp_path: Path) -> None:
    old, manifests = _make_project(tmp_path, 1)
    _submit(old, manifests[0].batch_id)
    config = load_project(old)
    config.external_review = ExternalReviewConfig(reviewer=ExternalReviewerConfig(
        id="primary", driver="codex-cli", command="codex", model="review-model"))
    seed_project(old, config)
    config.schema_version = 6
    seed_project(old, config)
    before = snapshot(old)
    new = tmp_path / "rebuilt"
    with pytest.raises(ValueError, match="no v7 settings to preserve"):
        rebuild_project(old, new, settings="preserve")
    assert not new.exists()

    report: dict = {}
    rebuild_project(old, new, report=report)
    assert snapshot(old) == before
    assert_no_inherited_evidence(new)
    configuration = report["configuration"]
    assert configuration["mode"] == "preset" and configuration["source_schema_version"] == 6
    assert configuration["preserved"] == ["document"]
    assert [item["path"] for item in configuration["reset"]] == [key for key in SECTIONS if key != "document"]
    assert all(item["operation"] == "preset-default" for item in configuration["reset"])
    assert {"path": "project.yaml:external_review",
            "reason": "Legacy policy is not converted; configure it with config apply."} in configuration["not_migrated"]
    assert not read_settings(new).external_review.enabled


def test_rebuild_cli_reports_configuration(configured, tmp_path: Path) -> None:
    old, _ = configured
    new = tmp_path / "rebuilt"
    result = CliRunner().invoke(app, ["project", "rebuild", str(old), str(new),
                                      "--settings", "preset", "--preset", "technical-book"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 7
    assert payload["rebuild"]["configuration"]["mode"] == "preset"
    assert payload["rebuild"]["inherited_approvals"] is False
