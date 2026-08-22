from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from littrans import cli

runner = CliRunner()


def test_math_candidates_parses_repeatable_and_comma_separated_unit_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[Any, ...]] = []

    def fake_generate(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"selected_units": len(args[-1])}

    monkeypatch.setattr(cli, "generate_math_candidates", fake_generate)
    result = runner.invoke(
        cli.app,
        [
            "source",
            "math-candidates",
            str(tmp_path),
            "--unit-ids",
            "p0001-u001-math,p0001-u002-math",
            "--unit-ids",
            "p0002-u001-math",
        ],
    )
    assert result.exit_code == 0, result.output
    assert observed[0][-1] == [
        "p0001-u001-math",
        "p0001-u002-math",
        "p0002-u001-math",
    ]
    assert json.loads(result.output)["selected_units"] == 3


def test_math_candidates_unit_id_help_and_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fake_generate(*args: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(cli, "generate_math_candidates", fake_generate)
    help_result = runner.invoke(cli.app, ["source", "math-candidates", "--help"])
    assert help_result.exit_code == 0, help_result.output
    assert "exact pilot" in help_result.output
    assert "comma-separated" in help_result.output

    duplicate = runner.invoke(
        cli.app,
        [
            "source",
            "math-candidates",
            str(tmp_path),
            "--unit-ids",
            "same,same",
        ],
    )
    assert duplicate.exit_code != 0
    assert "duplicate unit IDs" in duplicate.output
    assert called is False


def test_math_review_packets_forwards_page_complete_packet_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[Any, ...]] = []

    def fake_build(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"packet_count": 2}

    monkeypatch.setattr(cli, "build_math_review_packets", fake_build)
    result = runner.invoke(
        cli.app,
        [
            "source",
            "math-review-packets",
            str(tmp_path),
            "--pages",
            "2-7",
            "--target-units",
            "12",
            "--max-units",
            "20",
            "--output-root",
            "packets/pilot",
            "--manual-only",
            "--require-candidates",
            "--include-unit-ids",
            "p0002-u004-paragraph,p0003-u001-equation",
        ],
    )
    assert result.exit_code == 0, result.output
    (
        project,
        pages,
        target_units,
        max_units,
        output_root,
        manual_only,
        require_candidates,
        include_unit_ids,
    ) = observed[0]
    assert project == tmp_path
    assert pages == "2-7"
    assert target_units == 12
    assert max_units == 20
    assert output_root == Path("packets/pilot")
    assert manual_only is True
    assert require_candidates is True
    assert include_unit_ids == [
        "p0002-u004-paragraph",
        "p0003-u001-equation",
    ]
    assert json.loads(result.output)["packet_count"] == 2


def test_math_review_packets_help_explains_limits() -> None:
    result = runner.invoke(cli.app, ["source", "math-review-packets", "--help"])
    assert result.exit_code == 0, result.output
    assert "page-complete" in result.output
    assert "single denser" in result.output
    assert "DeepSeek pilot fails" in result.output
    assert "twice" in result.output
    assert "fully local" in result.output
    assert "--include-unit-ids" in result.output
    assert "verified" in result.output


def test_math_review_packets_rejects_invalid_include_unit_ids_before_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fake_build(*args: Any) -> dict[str, Any]:
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(cli, "build_math_review_packets", fake_build)
    result = runner.invoke(
        cli.app,
        [
            "source",
            "math-review-packets",
            str(tmp_path),
            "--manual-only",
            "--include-unit-ids",
            "same,same",
        ],
    )
    assert result.exit_code != 0
    assert "duplicate unit IDs" in result.output
    assert called is False


def test_import_math_review_forwards_strict_structural_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[tuple[Any, ...]] = []

    def fake_import(*args: Any) -> dict[str, Any]:
        observed.append(args)
        return {"structural_layout_override_count": 2}

    monkeypatch.setattr(cli, "import_math_review", fake_import)
    decisions = tmp_path / "result" / "decisions.jsonl"
    sidecar = tmp_path / "result" / "structural-overrides.yaml"
    result = runner.invoke(
        cli.app,
        [
            "source",
            "import-math-review",
            str(tmp_path / "project"),
            str(decisions),
            "--confirm-visual-review",
            "--structural-overrides",
            str(sidecar),
        ],
    )
    assert result.exit_code == 0, result.output
    project, input_file, confirmed, structural_file = observed[0]
    assert project == tmp_path / "project"
    assert input_file == decisions
    assert confirmed is True
    assert structural_file == sidecar
    assert json.loads(result.output)["structural_layout_override_count"] == 2


def test_import_math_review_help_marks_structural_yaml_as_strictly_bound() -> None:
    result = runner.invoke(cli.app, ["source", "import-math-review", "--help"])
    assert result.exit_code == 0, result.output
    assert "packet/hash/decision-bound" in result.output
    assert "unbound layout YAML is rejected" in result.output


def test_repair_math_structural_ledger_forwards_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: list[Path] = []

    def fake_repair(project: Path) -> dict[str, Any]:
        observed.append(project)
        return {"repaired_count": 1}

    monkeypatch.setattr(cli, "repair_math_structural_review_ledger", fake_repair)
    project = tmp_path / "project"
    result = runner.invoke(
        cli.app,
        ["source", "repair-math-structural-ledger", str(project)],
    )
    assert result.exit_code == 0, result.output
    assert observed == [project]
    assert json.loads(result.output)["repaired_count"] == 1
