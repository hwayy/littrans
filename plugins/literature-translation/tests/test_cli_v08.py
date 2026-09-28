"""The canonical routes preserve domain behavior and machine-readable legacy output."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from littrans import cli

ROUTES = [
    (["source", "prepare"], ["source", "extract"]),
    (["batch", "show"], ["translation", "batch", "show"]),
    (["qa", "run"], ["translation", "qa"]),
    (["review", "status"], ["translation", "review", "status"]),
    (["glossary", "check"], ["context", "glossary", "check"]),
    (["approve"], ["translation", "approve"]),
    (["render"], ["translation", "render"]),
]


@pytest.mark.parametrize("old,new", ROUTES)
def test_legacy_help_remains_available(old: list[str], new: list[str]) -> None:
    runner = CliRunner()
    for route in (old, new):
        result = runner.invoke(cli.app, [*route, "--help"])
        assert result.exit_code == 0, result.output


def test_canonical_and_legacy_qa_share_result_and_defaults(monkeypatch: pytest.MonkeyPatch,
                                                         tmp_path: Path) -> None:
    calls: list[tuple[Path, str]] = []

    def qa(project: Path, batch_id: str) -> dict[str, object]:
        calls.append((project, batch_id))
        return {"passed": True, "batch_id": batch_id}

    monkeypatch.setattr(cli, "run_qa", qa)
    runner = CliRunner()
    old = runner.invoke(cli.app, ["qa", "run", str(tmp_path), "b001"])
    new = runner.invoke(cli.app, ["translation", "qa", str(tmp_path), "b001"])
    assert old.exit_code == new.exit_code == 0
    assert json.loads(old.stdout) == json.loads(new.stdout) == {"passed": True, "batch_id": "b001"}
    assert "Deprecated:" in old.stderr and "translation qa" in old.stderr
    assert "Deprecated:" not in new.stderr
    assert calls == [(tmp_path, "b001"), (tmp_path, "b001")]


def test_legacy_warning_does_not_change_guarded_failure(monkeypatch: pytest.MonkeyPatch,
                                                      tmp_path: Path) -> None:
    def fail(project: Path, batch_id: str) -> None:
        raise ValueError("stale source evidence")

    monkeypatch.setattr(cli, "run_qa", fail)
    runner = CliRunner()
    results = [runner.invoke(cli.app, [*route, str(tmp_path), "b001"])
               for route in (["qa", "run"], ["translation", "qa"])]
    assert [result.exit_code for result in results] == [1, 1]
    assert all("stale source evidence" in result.output for result in results)
    assert all(result.stdout == "" for result in results)


def test_root_help_prefers_canonical_groups() -> None:
    result = CliRunner().invoke(cli.app, ["--help"])
    assert result.exit_code == 0
    for group in ("translation", "context", "source", "assets"):
        assert group in result.stdout
    # Legacy commands still resolve above; their registrations are hidden only in help.
    assert all(group.hidden for group in cli.app.registered_groups
               if group.name in {"qa", "review", "batch", "glossary"})
