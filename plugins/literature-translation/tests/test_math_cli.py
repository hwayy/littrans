from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fidelity_fixtures import make_asset_fixture
from typer.testing import CliRunner

from littrans import cli, fidelity, representations

runner = CliRunner()


@pytest.mark.parametrize("command", ["extract", "math-candidates", "math-review-packets", "import-math-review", "repair-math-structural-ledger"])
def test_previous_source_entrypoints_are_removed(command: str) -> None:
    result = runner.invoke(cli.app, ["source", command, "--help"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_source_prepare_preserves_page_scope_without_a_mode_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed = []
    def prepare(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"prepared_pages": [2, 3]}
    monkeypatch.setattr(fidelity, "prepare_source", prepare)
    result = runner.invoke(cli.app, ["source", "prepare", str(tmp_path), "--pages", "2-3"])
    assert result.exit_code == 0, result.output
    assert observed == [(tmp_path, "2-3", False, False)]
    assert json.loads(result.output)["prepared_pages"] == [2, 3]
    invalid = runner.invoke(cli.app, ["source", "prepare", str(tmp_path), "--mode", "visual"])
    assert invalid.exit_code != 0
    assert len(observed) == 1


def test_source_review_packet_retains_requested_pages(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed = []
    def build(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"pages": [2, 3]}
    monkeypatch.setattr(fidelity, "build_source_review_packet", build)
    result = runner.invoke(cli.app, ["source", "review-packets", str(tmp_path), "--pages", "2-3"])
    assert result.exit_code == 0, result.output
    assert observed == [(tmp_path, "2-3")]


@pytest.mark.parametrize("lane", ["source", "assets"])
@pytest.mark.parametrize("confirmed", [False, True])
def test_review_import_forwards_explicit_visual_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lane: str, confirmed: bool) -> None:
    observed = []
    def importing(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"reviewed": 1}
    module = fidelity if lane == "source" else representations
    name = "import_source_review" if lane == "source" else "import_asset_review"
    monkeypatch.setattr(module, name, importing)
    path = tmp_path / "review.json"
    args = [lane, "import-review", str(tmp_path), str(path)]
    if confirmed:
        args.append("--confirm-visual-review")
    result = runner.invoke(cli.app, args)
    assert result.exit_code == 0, result.output
    assert observed == [(tmp_path, path, confirmed)]


def test_asset_submission_is_separate_from_translation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    observed = []
    def submit(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"candidate_count": 1}
    monkeypatch.setattr(representations, "submit_candidates", submit)
    path = tmp_path / "candidate.json"
    result = runner.invoke(cli.app, ["assets", "submit", str(tmp_path), str(path)])
    assert result.exit_code == 0, result.output
    assert observed == [(tmp_path, path)]


def test_asset_packet_rejects_duplicate_ids_and_uses_source_context(tmp_path: Path) -> None:
    root, units, _ = make_asset_fixture(tmp_path, [("Original context.", "math", "x=1")])
    duplicate = runner.invoke(cli.app, ["assets", "packet", str(root), "--asset-ids", "fixture-asset-1,fixture-asset-1"])
    assert duplicate.exit_code != 0
    assert "unique" in str(duplicate.exception)
    result = runner.invoke(cli.app, ["assets", "packet", str(root), "--asset-ids", "fixture-asset-1"])
    assert result.exit_code == 0, result.output
    packet = json.loads(result.output)
    assert packet["asset_ids"] == ["fixture-asset-1"]
    assert any(item["unit_id"] == units[0].unit_id for item in packet["context_units"])
    assert packet["required_images"]


def test_asset_revision_packet_forwards_bound_review_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root, units, _ = make_asset_fixture(tmp_path, [("Original context.", "figure", "Label")])
    observed = []
    def build(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"stage": "transcribe"}
    monkeypatch.setattr(representations, "build_asset_packet", build)
    result = runner.invoke(cli.app, ["assets", "packet", str(root), "--asset-ids", "fixture-asset-1", "--revision-notes", "Restore the omitted label."])
    assert result.exit_code == 0, result.output
    assert observed[0][1:3] == (["fixture-asset-1"], "transcribe")
    assert observed[0][-1] == "Restore the omitted label."
    assert any(unit.unit_id == units[0].unit_id for unit in observed[0][3])
