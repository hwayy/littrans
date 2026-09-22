from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import BaseModel
from typer.core import TyperGroup

import littrans
from littrans.batching import create_batches, refresh_batch, show_batch
from littrans.build_info import build_identity
from littrans.external_review import external_review_status, run_external_review
from littrans.extractor import inspect_source
from littrans.models import IssueStatus
from littrans.project import initialize_project, project_status, rebuild_project
from littrans.quality import (
    approve_batch,
    import_review,
    list_issues,
    resolve_issues,
    review_status,
    run_qa,
)
from littrans.rendering import render_project
from littrans.translation import submit_translation
from littrans.verification import verify_extraction
from littrans.workflow import (
    create_workflow_packet,
    import_review_set,
    prune_workflow_packets,
    workflow_metrics,
    workflow_next,
    workflow_status,
)

# Typer renders click's own exception class as a clean error panel; resolve it
# through BadParameter so the lookup survives typer bundling click privately.
_ClickException: type[Exception] = next(
    base for base in typer.BadParameter.__mro__ if base.__name__ == "ClickException"
)


class _GuardedGroup(TyperGroup):
    """Report a refused precondition as an error, not as a crash.

    Workflow guards (stale packets, missing batches, invalid submissions) signal
    "not available" with ValueError, pydantic's ValidationError included, and a
    wrong path surfaces as FileNotFoundError. At the CLI boundary those are an
    error message with exit code 1, never a traceback that reads like a defect
    and invites bypassing the guard.
    """

    def invoke(self, ctx: Any) -> Any:
        try:
            return super().invoke(ctx)
        except ValueError as exc:
            raise _ClickException(str(exc)) from exc
        except FileNotFoundError as exc:
            raise _ClickException(f"{exc.strerror or 'File not found'}: {exc.filename}") from exc


app = typer.Typer(cls=_GuardedGroup, no_args_is_help=True, help="Controlled literature translation tooling.")
project_app = typer.Typer(no_args_is_help=True)
source_app = typer.Typer(no_args_is_help=True)
batch_app = typer.Typer(no_args_is_help=True)
translation_app = typer.Typer(no_args_is_help=True)
qa_app = typer.Typer(no_args_is_help=True)
review_app = typer.Typer(no_args_is_help=True)
workflow_app = typer.Typer(no_args_is_help=True)
assets_app = typer.Typer(no_args_is_help=True)
layout_app = typer.Typer(no_args_is_help=True)
glossary_app = typer.Typer(no_args_is_help=True)
app.add_typer(project_app, name="project")
app.add_typer(source_app, name="source")
app.add_typer(batch_app, name="batch")
app.add_typer(translation_app, name="translation")
app.add_typer(qa_app, name="qa")
app.add_typer(review_app, name="review")
app.add_typer(workflow_app, name="workflow")
app.add_typer(assets_app, name="assets")
app.add_typer(layout_app, name="layout")
app.add_typer(glossary_app, name="glossary")


def _configure_console_stream(stream: Any) -> None:
    """Emit UTF-8 with LF line endings regardless of the console code page.

    Windows consoles default to a legacy code page (GBK on zh-CN hosts) and
    translate newlines when piped; both break coordinators that pipe JSON
    containing Chinese text or bullet characters into files.
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace", newline="\n")
    except (ValueError, OSError):
        pass


for _stream in (sys.stdout, sys.stderr):
    _configure_console_stream(_stream)

PathArg = Annotated[Path, typer.Argument(resolve_path=True)]


def emit(payload: object) -> None:
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def advise(notes: Iterable[str]) -> None:
    """Report dispatch-policy gaps on stderr, leaving the stdout JSON contract untouched."""
    for note in notes:
        typer.echo(f"LitTrans advisory: {note}", err=True)


def advise_roles(project: Path, host: str | None, *roles: str) -> None:
    """Report the gaps of the roles a command is about to dispatch."""
    from littrans.project import dispatch_report

    try:
        advise(dispatch_report(project, host, roles)["advisories"])
    except (OSError, ValueError):  # a project or host problem the command itself reports
        return


@app.command()
def doctor() -> None:
    """Check the local runtime, including the required layout detector, without changing it."""
    from littrans.layout_runtime import layout_runtime_status
    modules = [
        "pymupdf",
        "httpx",
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
            # The installed build, comparable with the `generator` block of artifacts.
            "build": {**{k: v for k, v in build_identity().items() if k != "generated_at"}, "package_path": str(Path(littrans.__file__).resolve().parent)},
            "modules": {name: importlib.util.find_spec(name) is not None for name in modules},
            "pdftoppm": shutil.which("pdftoppm"),
            "pdfinfo": shutil.which("pdfinfo"),
            "layout_runtime": layout_runtime_status(),
        }
    )


@layout_app.command("status")
def layout_status() -> None:
    """Report the isolated layout detector runtime required by source preparation."""
    from littrans.layout_runtime import layout_runtime_status
    emit(layout_runtime_status())


@layout_app.command("install")
def layout_install(
    python: Path | None = typer.Option(None, help="Python 3.10-3.13 base interpreter for the isolated MinerU environment."),
    force: bool = typer.Option(False, help="Recreate the environment and re-download the weights."),
    model_source: str = typer.Option("huggingface", help="huggingface or modelscope."),
) -> None:
    """Create the isolated MinerU 3.4.5 environment and fetch PP-DocLayoutV2 weights."""
    from littrans.layout_runtime import install_layout_runtime
    emit(install_layout_runtime(python, force, model_source))


@project_app.command("init")
def project_init(
    source: PathArg,
    project: PathArg,
    profile: str = typer.Option("technical-book"),
    title: str | None = typer.Option(None),
    source_language: str = typer.Option("en"),
    target_language: str = typer.Option("zh-CN"),
    repo_root: Path | None = typer.Option(
        None, resolve_path=True,
        help="Where the handbook, records, ledger and launcher go when the project is nested in a larger repository (default: PROJECT).",
    ),
) -> None:
    """Create a private schema-6 project with its record structure scaffolded."""
    from littrans.scaffold import scaffold_project

    config = initialize_project(
        source, project, profile, title, source_language, target_language, repo_root=repo_root, scaffold=False
    )
    emit({**config.model_dump(mode="json"), "scaffold": scaffold_project(project, repo_root=repo_root, refresh=True)})


@project_app.command("models")
def project_models(
    project: PathArg,
    host: str = typer.Option("auto", help="Coordination host: auto, codex, cursor, claude, or qoder."),
) -> None:
    """Report the resolved per-role dispatch policy for a host, with its advisories."""
    from littrans.project import dispatch_report

    emit(dispatch_report(project, host))


@project_app.command("scaffold")
def project_scaffold(
    project: PathArg,
    repo_root: Path | None = typer.Option(None, resolve_path=True, help="Record root for a nested layout (default: PROJECT)."),
    refresh: bool = typer.Option(False, help="Regenerate the plugin-owned docs/LITTRANS.md from the installed build."),
) -> None:
    """Create the missing record files of an existing project; never overwrites user-owned files."""
    from littrans.scaffold import scaffold_project

    emit(scaffold_project(project, repo_root=repo_root, refresh=refresh))


@project_app.command("tracked")
def project_tracked(project: PathArg) -> None:
    """Ask git whether exactly the project record is tracked; exit 1 on any gap. Read-only."""
    from littrans.record import record_tracking

    result = record_tracking(project)
    emit(result)
    if result["problems"]:
        raise typer.Exit(code=1)


@source_app.command("inspect")
def source_inspect(project: PathArg, pages: str = typer.Option("all")) -> None:
    emit(inspect_source(project, pages))


@source_app.command("verify")
def source_verify(
    project: PathArg,
    pages: str = typer.Option("all"),
    force: bool = typer.Option(False),
) -> None:
    """Check current source coverage, original assets and visual-review evidence."""
    emit(verify_extraction(project, pages, force))


@project_app.command("rebuild")
def project_rebuild(old: PathArg, new: PathArg) -> None:
    """Build a new v6 workspace; preserve the historical project and its evidence."""
    emit(rebuild_project(old, new))


@source_app.command("probe")
def source_probe(project: PathArg, pages: str = typer.Option("all")) -> None:
    """Create a source-bound document structure profile before preparation."""
    from littrans.structure_profile import probe_structure
    emit(probe_structure(project, pages))


@source_app.command("rescope")
def source_rescope(
    project: PathArg,
    packet: str = typer.Option(..., "--packet", help="A source packet whose embedded base rules the earlier receipts are bound to."),
    pages: str = typer.Option(..., "--pages", help="The pages the appended rule text holds for (a spec such as 52-67)."),
    label: str = typer.Option("", "--label", help="Descriptive name of the block, e.g. 'Chapter 2'."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would move without writing the profile."),
) -> None:
    """Move rule text appended since a packet into a page_rules block so its receipts verify again."""
    from littrans.structure_profile import rescope_rules
    emit(rescope_rules(project, packet, pages, label, apply=not dry_run))


@source_app.command("prepare")
def source_prepare(
    project: PathArg,
    pages: str = typer.Option("all"),
    replace: bool = typer.Option(False),
    discard_overrides: bool = typer.Option(
        False, "--discard-overrides",
        help="With --replace, re-derive pages that carry a reviewer's override instead of replaying it.",
    ),
    allow_missing_layout: bool = typer.Option(
        False, "--allow-missing-layout",
        help="Only at the user's explicit request: prepare without the layout detector; every region then needs full visual review.",
    ),
    redetect: bool = typer.Option(
        False, "--redetect",
        help="With --replace, run the layout detector again instead of cutting each page on the result its ledger records.",
    ),
) -> None:
    """Preserve original prose and complex visual assets without formula transcription."""
    from littrans.fidelity import prepare_source
    if redetect and not replace:
        raise typer.BadParameter("--redetect only applies with --replace")
    emit(prepare_source(project, pages, replace, allow_missing_layout, discard_overrides, redetect))


@source_app.command("gc")
def source_gc(
    project: PathArg,
    apply: bool = typer.Option(False),
    dry_run: bool = typer.Option(False),
) -> None:
    """List or remove original-asset directories no current fragment refers to."""
    if apply == dry_run:
        raise typer.BadParameter("choose exactly one of --apply or --dry-run")
    from littrans.fidelity import gc_asset_directories
    emit(gc_asset_directories(project, apply=apply))


@source_app.command("render")
def source_render(
    project: PathArg,
    pages: str = typer.Option("all"),
    name: str | None = typer.Option(None, help="Output file name (default source-pNNNN-pNNNN)."),
    standalone: bool = typer.Option(False, "--standalone", help="Embed the asset images so the single HTML file can be shared."),
) -> None:
    """Write a readable HTML checkpoint of the preserved source with original assets inline."""
    from littrans.source_render import render_source_review
    emit(render_source_review(project, pages, name, standalone=standalone))


@source_app.command("review-packets")
def source_review_packets(project: PathArg, pages: str = typer.Option("all")) -> None:
    from littrans.fidelity import build_source_review_packet
    emit(build_source_review_packet(project, pages))


@source_app.command("import-review")
def source_import_review(project: PathArg, input_file: PathArg, confirm_visual_review: bool = typer.Option(False, "--confirm-visual-review")) -> None:
    from littrans.fidelity import import_source_review
    emit(import_source_review(project, input_file, confirm_visual_review))


@assets_app.command("submit")
def assets_submit(project: PathArg, input_file: PathArg) -> None:
    from littrans.representations import submit_candidates
    emit(submit_candidates(project, input_file))


@assets_app.command("packet")
def assets_packet(
    project: PathArg,
    asset_ids: str = typer.Option(..., help="Comma-separated stable asset IDs."),
    stage: str = typer.Option("transcribe"),
    revision_notes: str | None = typer.Option(None, help="Explicit correction request bound to existing candidate/review evidence."),
    host: str = typer.Option("auto", help="Coordination host: auto, codex, cursor, claude, or qoder."),
) -> None:
    from littrans.context_packets import adjacent_source_units
    from littrans.fidelity_models import asset_reference_ids
    from littrans.models import SourceUnit
    from littrans.representations import build_asset_packet
    from littrans.storage import read_jsonl
    ids = [aid.strip() for aid in asset_ids.split(",") if aid.strip()]
    units = [u for u in read_jsonl(project / "derived/units.jsonl", SourceUnit)
             if set(ids) & set(asset_reference_ids(u.source_markdown or u.source_text))]
    emit(build_asset_packet(project, ids, stage, units + adjacent_source_units(project, units), revision_notes, host=host))
    advise_roles(project, host, stage)


@assets_app.command("import-review")
def assets_import_review(project: PathArg, input_file: PathArg, confirm_visual_review: bool = typer.Option(False, "--confirm-visual-review")) -> None:
    from littrans.representations import import_asset_review
    emit(import_asset_review(project, input_file, confirm_visual_review))


@assets_app.command("status")
def assets_status(project: PathArg) -> None:
    from littrans.representations import representation_status
    emit(representation_status(project))


@batch_app.command("create")
def batch_create(
    project: PathArg,
    pages: str = typer.Option(...),
    max_words: int | None = typer.Option(None),
    prefix: str | None = typer.Option(None),
    untranslated_only: bool = typer.Option(False),
    unit_ids: str | None = typer.Option(None, help="Comma-separated source unit IDs; include complete continuations."),
) -> None:
    emit(
        [
            manifest.model_dump(mode="json")
            for manifest in create_batches(project, pages, max_words, prefix, untranslated_only,
                                           [value.strip() for value in unit_ids.split(",") if value.strip()]
                                           if unit_ids is not None else None)
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


@review_app.command("import-set")
def review_import_set(project: PathArg, packet_manifest: PathArg, issues_jsonl: PathArg) -> None:
    emit(import_review_set(project, packet_manifest, issues_jsonl))


@review_app.command("resolve")
def review_resolve(
    project: PathArg,
    batch_id: str,
    issue_id: str = typer.Argument(
        ..., help="Canonical or reviewer-supplied issue id; comma-separate several."
    ),
    status: IssueStatus = typer.Option(IssueStatus.RESOLVED),
    resolution: str = typer.Option(...),
) -> None:
    issue_ids = [value.strip() for value in issue_id.split(",") if value.strip()]
    resolved = resolve_issues(project, batch_id, issue_ids, status, resolution)
    emit(resolved[0] if len(resolved) == 1 else [issue.model_dump(mode="json") for issue in resolved])


@review_app.command("issues")
def review_list_issues(
    project: PathArg,
    batch_id: str,
    all_issues: bool = typer.Option(False, "--all", help="Include resolved issues."),
    jsonl: bool = typer.Option(False, help="One issue per line, ready to save as a JSONL file."),
) -> None:
    """List review issues for one batch (open issues by default)."""
    issues = list_issues(project, batch_id, open_only=not all_issues)
    if jsonl:
        for issue in issues:
            typer.echo(json.dumps(issue.model_dump(mode="json", exclude_none=True), ensure_ascii=False))
        return
    emit([issue.model_dump(mode="json") for issue in issues])


@review_app.command("status")
def review_get_status(project: PathArg, batch_id: str) -> None:
    emit(review_status(project, batch_id))


@glossary_app.command("lookup")
def glossary_lookup_command(
    project: PathArg,
    batch_id: str | None = typer.Option(None, help="The batch whose units to match."),
    pages: str | None = typer.Option(None, help="PDF page spec, e.g. 26-51 or 3,5-7."),
    unit_ids: str | None = typer.Option(None, help="Comma-separated source unit IDs."),
    text: Path | None = typer.Option(None, help="Any UTF-8 file to match without page scope."),
    kind: str | None = typer.Option(None, help="Only reference entries of this kind."),
    jsonl: bool = typer.Option(False, help="One entry per line with its channel."),
) -> None:
    """List the approved (gated) and reference (not gated) entries a selection needs; read-only."""
    from littrans.glossary import glossary_lookup

    result = glossary_lookup(
        project,
        batch_id=batch_id,
        pages=pages,
        unit_ids=[value.strip() for value in unit_ids.split(",") if value.strip()] if unit_ids else None,
        text=text,
        kind=kind,
    )
    if jsonl:
        for term in result["approved"]:
            typer.echo(json.dumps({"channel": "approved", **term}, ensure_ascii=False))
        for group, terms in result["reference"].items():
            for term in terms:
                typer.echo(json.dumps({"channel": "reference", "kind": group, **term}, ensure_ascii=False))
        return
    emit(result)


@glossary_app.command("check")
def glossary_check_command(project: PathArg) -> None:
    """Load every glossary file and report entries matching no prepared unit; read-only."""
    from littrans.glossary import glossary_check

    emit(glossary_check(project))


@review_app.command("external")
def review_external(
    project: PathArg,
    batch_id: str,
    reviewer: str | None = typer.Option(None),
    second_opinion: bool = typer.Option(False),
    dry_run: bool = typer.Option(False),
    from_result: Path | None = typer.Option(None, "--from-result"),
    from_dry_run: Path | None = typer.Option(None, "--from-dry-run"),
    actual_model: str | None = typer.Option(
        None,
        "--actual-model",
        help="Actual model label attested by the trusted Cursor host coordinator.",
    ),
) -> None:
    """Run one isolated, read-only external translation review."""
    emit(
        run_external_review(
            project,
            batch_id,
            reviewer,
            second_opinion,
            dry_run,
            from_result=from_result,
            from_dry_run=from_dry_run,
            host_actual_model=actual_model,
        )
    )


@review_app.command("external-status")
def review_get_external_status(project: PathArg, batch_id: str) -> None:
    """Report the current external-review evidence and gate state."""
    emit(external_review_status(project, batch_id))


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
    batch_ids: str | None = typer.Option(None),
    name: str | None = typer.Option(None),
    allow_draft: bool = typer.Option(False),
    originals_only: bool = typer.Option(False, help="Render original images only, even when verified candidates exist."),
) -> None:
    parsed_batch_ids = (
        [value.strip() for value in batch_ids.split(",") if value.strip()] if batch_ids else None
    )
    emit(render_project(project, pages, name, allow_draft, batch_id, parsed_batch_ids, originals_only=originals_only))


@workflow_app.command("next")
def workflow_get_next(
    project: PathArg,
    limit: int | None = typer.Option(
        None,
        help="Wave size. Defaults to 3 on Codex, Claude Code and Qoder, 6 on Cursor.",
    ),
    start_at: str | None = typer.Option(None),
    through: str | None = typer.Option(None),
    host: str = typer.Option(
        "auto",
        help="Coordination host: auto, codex, cursor, claude, or qoder.",
    ),
) -> None:
    wave = workflow_next(project, limit, start_at, through, host)
    emit(wave)
    advise(wave["dispatch_advisories"])


@workflow_app.command("status")
def workflow_get_status(project: PathArg, batch_ids: str = typer.Option(...),
                        host: str = typer.Option("auto", help="Coordination host: auto, codex, cursor, claude, or qoder.")) -> None:
    status = workflow_status(
        project,
        [value.strip() for value in batch_ids.split(",") if value.strip()],
        host=host,
    )
    emit(status)
    advise(status["dispatch_advisories"])


@workflow_app.command("packet")
def workflow_create_packet(
    project: PathArg,
    stage: str = typer.Option(
        ...,
        help="source-review, translate, revise (current translation plus open issues), audit, transcribe or asset-audit.",
    ),
    batch_ids: str = typer.Option(...),
    lens: str | None = typer.Option(None),
    host: str = typer.Option("auto", help="Coordination host: auto, codex, cursor, claude, or qoder."),
) -> None:
    result = create_workflow_packet(
        project,
        stage,
        [value.strip() for value in batch_ids.split(",") if value.strip()],
        lens,
        host,
    )
    emit(
        [packet.model_dump(mode="json") for packet in result]
        if isinstance(result, list)
        else result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    )
    advise_roles(project, host, "translate" if stage in {"translate", "revise"} else stage)


@workflow_app.command("prune-packets")
def workflow_prune_packets(
    project: PathArg,
    batch_ids: str | None = typer.Option(None),
    apply: bool = typer.Option(False),
    dry_run: bool = typer.Option(False),
) -> None:
    if apply == dry_run:
        raise typer.BadParameter("choose exactly one of --apply or --dry-run")
    emit(
        prune_workflow_packets(
            project,
            [value.strip() for value in batch_ids.split(",") if value.strip()]
            if batch_ids
            else None,
            apply=apply,
        )
    )


@workflow_app.command("metrics")
def workflow_get_metrics(project: PathArg, batch_ids: str | None = typer.Option(None)) -> None:
    emit(
        workflow_metrics(
            project,
            [value.strip() for value in batch_ids.split(",") if value.strip()]
            if batch_ids
            else None,
        )
    )


@app.command()
def status(project: PathArg) -> None:
    emit(project_status(project))


if __name__ == "__main__":
    app()
