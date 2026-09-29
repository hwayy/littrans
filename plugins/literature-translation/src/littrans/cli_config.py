"""Structured configuration CLI; mutations go through the transactional service."""
import json
from pathlib import Path

import typer

from littrans import configuration as service
from littrans.settings import settings_schema


def register(app: typer.Typer) -> None:
    from littrans.cli import emit
    group = typer.Typer(no_args_is_help=True, help="Inspect and manage persistent project policy.")
    app.add_typer(group, name="config")

    @group.command("show")
    def show(project: Path, local: bool = False, effective: bool = False, host: str = "auto") -> None:
        if local and effective:
            raise ValueError("--local and --effective are exclusive")
        emit(service.effective(project, host) if effective else service.show(project, local))

    @group.command("get")
    def get(project: Path, key: str, local: bool = False, effective: bool = False, host: str = "auto") -> None:
        if local and effective:
            raise ValueError("--local and --effective are exclusive")
        if effective:
            view = service.effective(project, host)
            value = view["settings"]
            value["agents"][view["host"]]["roles"] = view["roles"]
            value["agents"][view["host"]]["audit_lenses"] = view["audit_lenses"]
        else:
            value = service.show(project, local)["value"]
        for part in key.split("."):
            if not isinstance(value, dict) or part not in value:
                raise ValueError(f"Unknown configuration key: {key}")
            value = value[part]
        emit({"key": key, "value": value})

    @group.command("schema")
    def schema(local: bool = False) -> None:
        emit(settings_schema(local))

    @group.command("validate")
    def validate(project: Path, host: str = "auto") -> None:
        result = service.effective(project, host)
        emit({"valid": not result["errors"], "errors": result["errors"], "host": result["host"]})
        if result["errors"]:
            raise typer.Exit(1)

    @group.command("set")
    def set_value(project: Path, key: str, value: str, json_value: bool = typer.Option(False, "--json"),
                  local: bool = False, dry_run: bool = False) -> None:
        emit(service.edit(project, key, json.loads(value) if json_value else value,
                          local=local, dry_run=dry_run))

    @group.command("unset")
    def unset(project: Path, key: str, local: bool = False, dry_run: bool = False) -> None:
        emit(service.edit(project, key, unset=True, local=local, dry_run=dry_run))

    @group.command("apply")
    def apply(project: Path, file: Path, local: bool = False, dry_run: bool = False,
              expect: str | None = None) -> None:
        emit(service.apply(project, service.yaml_read(file), local=local, dry_run=dry_run, expect=expect))

    @group.command("reset")
    def reset(project: Path, preset: str = typer.Option(...), dry_run: bool = False) -> None:
        emit(service.reset(project, preset, dry_run))

    @group.command("presets")
    def presets() -> None:
        emit({"presets": ["technical-book", "research-paper"], "version": "1"})
