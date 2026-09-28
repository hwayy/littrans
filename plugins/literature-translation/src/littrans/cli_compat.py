"""Canonical routes and legacy aliases share callbacks and parameter signatures."""
from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import Any

import typer


def legacy(callback: Callable[..., Any], old: str, new: str) -> Callable[..., Any]:
    @wraps(callback)
    def invoke(*args: Any, **kwargs: Any) -> Any:
        typer.echo(f"Deprecated: '{old}'; use '{new}' (supported throughout 0.8.x).", err=True)
        return callback(*args, **kwargs)
    return invoke


def move_command(source: typer.Typer, target: typer.Typer, name: str,
                 new_name: str, old_path: str, new_path: str) -> None:
    """Register the unchanged callback canonically, wrap only its legacy registration."""
    command = next(item for item in source.registered_commands
                   if item.name == name or (item.name is None and item.callback is not None
                                           and item.callback.__name__.replace('_', '-') == name))
    assert command.callback is not None
    target.command(new_name, help=command.help)(command.callback)
    command.callback = legacy(command.callback, old_path, new_path)
    command.hidden = True


def move_group(source: typer.Typer, target: typer.Typer, name: str,
               old_path: str, new_path: str) -> None:
    """Copy registrations, retaining one shared implementation per operation."""
    canonical = typer.Typer(no_args_is_help=True)
    for command in source.registered_commands:
        assert command.callback is not None
        command_name = command.name or command.callback.__name__.replace('_', '-')
        canonical.command(command_name, help=command.help)(command.callback)
        command.callback = legacy(command.callback, f"{old_path} {command_name}",
                                  f"{new_path} {command_name}")
    target.add_typer(canonical, name=name)
