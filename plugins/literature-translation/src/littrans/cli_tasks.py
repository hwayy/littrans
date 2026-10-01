"""Task and context commands; domain mutation remains in existing validators."""
from pathlib import Path

import typer

from littrans.context_management import (
    check_context,
    context_impact,
    save_context_snapshot,
)
from littrans.settings import digest
from littrans.tasks import claim_task, create_task, receive_task, release_task, task_status


def register(app: typer.Typer, context_app: typer.Typer) -> None:
    from littrans import context_config
    from littrans.cli import emit

    @context_app.command("show")
    def show_context(project: Path, resource: str) -> None:
        emit({"resource": resource, "content": context_config.content(project, resource),
              "sha256": digest(context_config.semantic(project))})

    @context_app.command("validate")
    def validate_context(project: Path, resource: str | None = None) -> None:
        emit(context_config.validate(project, resource))

    @context_app.command("apply")
    def apply_context(project: Path, manifest: Path, dry_run: bool = False,
                      expect: str | None = None) -> None:
        emit(context_config.apply(project, manifest, dry_run, expect))

    task_app = typer.Typer(no_args_is_help=True, help="Bound tasks and fresh-session handoffs.")
    app.add_typer(task_app, name="task")

    @task_app.command("create")
    def create(project: Path, stage: str = typer.Option(...),
               batch_ids: str | None = None, pages: str | None = None,
               asset_ids: str | None = None, lens: str | None = None,
               host: str = "auto", objective: str | None = None,
               revision_notes: str | None = None, review_mode: str = "full") -> None:
        emit(create_task(project, stage, batch_ids=batch_ids.split(",") if batch_ids else None,
                         pages=pages, asset_ids=asset_ids.split(",") if asset_ids else None,
                         lens=lens, host=host, objective=objective, revision_notes=revision_notes,
                         review_mode=review_mode))

    @task_app.command("status")
    def status(project: Path, task_id: str | None = None) -> None:
        emit(task_status(project, task_id))

    @task_app.command("claim")
    def claim(project: Path, task_id: str, executor: str = typer.Option(...),
              mode: str = "fresh-session") -> None:
        emit(claim_task(project, task_id, executor, mode))

    @task_app.command("release")
    def release(project: Path, task_id: str, executor: str = typer.Option(...)) -> None:
        """Release an unfinished task only after its executor has stopped."""
        emit(release_task(project, task_id, executor))

    @task_app.command("receive")
    def receive(project: Path, task_id: str, result: Path | None = None,
                confirm_visual_review: bool = False) -> None:
        emit(receive_task(project, task_id, result, confirm_visual_review))

    @context_app.command("check")
    def check(project: Path) -> None:
        emit(check_context(project))

    @context_app.command("snapshot")
    def snapshot(project: Path, destination: Path) -> None:
        emit(save_context_snapshot(project, destination))

    @context_app.command("impact")
    def impact(project: Path, previous: Path) -> None:
        emit(context_impact(project, previous))
