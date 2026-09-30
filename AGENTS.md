# Repository Guidelines

## Project Structure & Module Organization

LitTrans combines agent workflows with a deterministic Python CLI for literature translation.
Within `plugins/literature-translation/`:

- `src/littrans/`: CLI, extraction, configuration, storage, review, and rendering modules.
- `tests/`: pytest suites and synthetic fixtures.
- `skills/`, `roles/`, and `agents/`: workflow and host instructions.
- `profiles/`, `schemas/`, and `references/`: presets, data contracts, and documentation.
- `src/littrans/templates/` and `src/littrans/vendor/`: Jinja templates and offline rendering assets.

Root `scripts/` contains checks and distribution tooling. `docs/` and `validation/` retain
planning and validation evidence; they do not define current behavior.

## Build, Test, and Development Commands

Use Python 3.12+. Create and activate `.venv` before running Python commands from the repository root.

- `python -m venv .venv`: create the development environment.
- `python -m pip install -e "./plugins/literature-translation[dev]" "hatchling>=1.25"`: install editable source and development tools.
- `./scripts/check.ps1` or `bash scripts/check.sh`: set up dependencies, validate release metadata, and run Ruff, mypy, pytest, and runtime diagnostics.
- `python plugins/literature-translation/scripts/littrans.py --help`: inspect the local CLI.
- `python -m pytest plugins/literature-translation/tests -q`: run the test suite; add `-k configuration` to focus it.
- `python scripts/build_distribution.py tmp/distribution`: validate and build the wheel, plugin ZIP, and hash manifest.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions/modules, `PascalCase` classes, and
`UPPER_CASE` constants. Add type annotations compatible with strict mypy. Aim for 100-character
lines and follow Ruff's lint and import-order rules in `pyproject.toml`. Preserve deterministic
state, resumable workflows, and review evidence.

## Testing Guidelines

Name pytest files `test_*.py` and functions `test_<behavior>`. Use synthetic fixtures and
`tmp_path` for isolated projects. Add regression tests for behavioral changes; no numeric
coverage threshold is configured. Real-PDF tests skip when local fixtures are absent. Mark
tests requiring the actual layout detector with `@pytest.mark.layout_runtime`.

## Commit & Pull Request Guidelines

Create topic branches from `main`. Prefer recent commit conventions such as
`fix: harden review locking`, `feat: add configuration support`, and `test:` or `docs:` prefixes.
Follow `.github/pull_request_template.md`: explain the problem, approach, compatibility impact,
and validation evidence. Update `CHANGELOG.md` for user-visible changes. Run the checks above
and `git diff --check` before opening a PR.

## Documentation & Security

Update `references/cli-reference.md` when CLI contracts change. Regenerate configuration
references with `scripts/update_configuration_reference.py` using the plugin's `src` on
`PYTHONPATH`. Keep PDFs, translations, workspaces, and credentials outside this repository.
Report vulnerabilities through `SECURITY.md`.
