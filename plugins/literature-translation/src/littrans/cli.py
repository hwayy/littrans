from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from pydantic import BaseModel

from littrans.batching import create_batches, refresh_batch, show_batch
from littrans.extractor import apply_layout_overrides, extract_source, inspect_source
from littrans.migration import migrate_translations
from littrans.models import IssueStatus
from littrans.project import initialize_project, project_status
from littrans.quality import approve_batch, import_review, resolve_issue, review_status, run_qa
from littrans.rendering import render_project
from littrans.translation import submit_translation
from littrans.verification import verify_extraction

app = typer.Typer(no_args_is_help=True, help="Controlled literature translation tooling.")
project_app = typer.Typer(no_args_is_help=True)
source_app = typer.Typer(no_args_is_help=True)
batch_app = typer.Typer(no_args_is_help=True)
translation_app = typer.Typer(no_args_is_help=True)
qa_app = typer.Typer(no_args_is_help=True)
review_app = typer.Typer(no_args_is_help=True)
app.add_typer(project_app, name="project")
app.add_typer(source_app, name="source")
app.add_typer(batch_app, name="batch")
app.add_typer(translation_app, name="translation")
app.add_typer(qa_app, name="qa")
app.add_typer(review_app, name="review")

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PathArg = Annotated[Path, typer.Argument(resolve_path=True)]


def emit(payload: object) -> None:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


@app.command()
def doctor() -> None:
    """Check the local runtime without changing it."""
    modules = [
        "fitz",
        "jinja2",
        "latex2mathml",
        "pydantic",
        "pygments",
        "yaml",
        "rapidfuzz",
        "typer",
    ]
    emit(
        {
            "python": sys.version,
            "python_ok": sys.version_info >= (3, 12),
            "modules": {name: importlib.util.find_spec(name) is not None for name in modules},
            "pdftoppm": shutil.which("pdftoppm"),
            "pdfinfo": shutil.which("pdfinfo"),
        }
    )


@project_app.command("init")
def project_init(
    source: PathArg,
    project: PathArg,
    profile: str = typer.Option("technical-book"),
    title: str | None = typer.Option(None),
    source_language: str = typer.Option("en"),
    target_language: str = typer.Option("zh-CN"),
) -> None:
    emit(initialize_project(source, project, profile, title, source_language, target_language))


@source_app.command("inspect")
def source_inspect(project: PathArg, pages: str = typer.Option("all")) -> None:
    emit(inspect_source(project, pages))


@source_app.command("extract")
def source_extract(
    project: PathArg,
    pages: str = typer.Option("all"),
    replace: bool = typer.Option(False),
) -> None:
    units = extract_source(project, pages, replace)
    emit({"units": len(units), "pages": sorted({unit.page for unit in units})})


@source_app.command("apply-overrides")
def source_apply_overrides(project: PathArg) -> None:
    units = apply_layout_overrides(project)
    emit({"units": len(units), "overrides_applied": True})


@source_app.command("verify")
def source_verify(project: PathArg, pages: str = typer.Option("all")) -> None:
    """Run structural gates and create a visual extraction report."""
    emit(verify_extraction(project, pages))


@batch_app.command("create")
def batch_create(
    project: PathArg,
    pages: str = typer.Option(...),
    max_words: int | None = typer.Option(None),
    prefix: str | None = typer.Option(None),
    untranslated_only: bool = typer.Option(False),
) -> None:
    emit(
        [
            manifest.model_dump(mode="json")
            for manifest in create_batches(project, pages, max_words, prefix, untranslated_only)
        ]
    )


@batch_app.command("show")
def batch_show(project: PathArg, batch_id: str) -> None:
    emit(show_batch(project, batch_id))


@batch_app.command("refresh")
def batch_refresh(project: PathArg, batch_id: str) -> None:
    emit(refresh_batch(project, batch_id).model_dump(mode="json"))


@translation_app.command("submit")
def translation_submit(project: PathArg, batch_id: str, input_file: PathArg) -> None:
    emit(
        [
            record.model_dump(mode="json")
            for record in submit_translation(project, batch_id, input_file)
        ]
    )


@translation_app.command("migrate")
def translation_migrate(
    source_project: PathArg,
    target_project: PathArg,
    pages: str = typer.Option("all"),
    minimum_score: float = typer.Option(88.0),
) -> None:
    """Migrate drafts after an explicit re-extraction of the same PDF."""
    emit(migrate_translations(source_project, target_project, pages, minimum_score))


@qa_app.command("run")
def qa_run(project: PathArg, batch_id: str) -> None:
    emit(run_qa(project, batch_id))


@review_app.command("import")
def review_import(
    project: PathArg,
    batch_id: str,
    input_file: PathArg,
    lenses: str = typer.Option("fidelity,technical,chinese-style"),
) -> None:
    emit(
        [
            issue.model_dump(mode="json")
            for issue in import_review(
                project,
                batch_id,
                input_file,
                [value.strip() for value in lenses.split(",") if value.strip()],
            )
        ]
    )


@review_app.command("resolve")
def review_resolve(
    project: PathArg,
    batch_id: str,
    issue_id: str,
    status: IssueStatus = typer.Option(IssueStatus.RESOLVED),
    resolution: str = typer.Option(...),
) -> None:
    emit(resolve_issue(project, batch_id, issue_id, status, resolution))


@review_app.command("status")
def review_get_status(project: PathArg, batch_id: str) -> None:
    emit(review_status(project, batch_id))


@app.command()
def approve(
    project: PathArg,
    batch_id: str,
    level: str = typer.Option("machine"),
    confirm_user_approved: bool = typer.Option(False),
) -> None:
    emit({"status": approve_batch(project, batch_id, level, confirm_user_approved)})


@app.command("render")
def render_command(
    project: PathArg,
    pages: str | None = typer.Option(None),
    batch_id: str | None = typer.Option(None),
    name: str = typer.Option(...),
    allow_draft: bool = typer.Option(False),
) -> None:
    emit(render_project(project, pages, name, allow_draft, batch_id))


@app.command()
def status(project: PathArg) -> None:
    emit(project_status(project))


if __name__ == "__main__":
    app()
