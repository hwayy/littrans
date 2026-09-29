# Contributing to LitTrans

Thank you for helping improve LitTrans. Contributions should preserve deterministic project
state, resumable workflows, review evidence, and compatibility with long-running translation
projects.

## Development setup

Use Python 3.12 or newer. From the repository root, create a virtual environment and install
both the plugin and development dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e "./plugins/literature-translation[dev]" "hatchling>=1.25"
```

On Linux or macOS, use `.venv/bin/python` instead. The editable install exposes `littrans` in
the virtual environment. The source launcher is also available:

```text
python plugins/literature-translation/scripts/littrans.py --help
```

See [local installation by host](plugins/literature-translation/references/installation.md)
to load the checkout into your assistant. Restart the host after changing installed plugin
files; avoid replacing a build that an active task still uses.

## Checks and documentation

`./scripts/check.ps1` and `bash scripts/check.sh` perform the same release checks: dependency
setup, release metadata, Ruff, mypy, pytest and runtime diagnostics. CI runs both platforms.
See [Releasing](RELEASING.md) for distribution and publication.

Keep the two READMEs short and aimed at human readers. Maintain command syntax and data
contracts in the [CLI reference](plugins/literature-translation/references/cli-reference.md).
Its command headings and parameter tables are checked against the registered CLI by pytest.
When changing a command, update its arguments, defaults, return value, side effects and example;
when changing a submission format, update its contract and validated examples. Workflow guidance
belongs in the relevant topic reference, linked to the contract rather than duplicated.

## Before opening a pull request

1. Create a topic branch from `main`.
2. Keep source PDFs, extracted assets, translations, credentials, and project workspaces outside
   this repository.
3. Add or update tests for behavioral changes.
4. Run `./scripts/check.ps1` from the repository root.
5. Run `git diff --check` and inspect the staged diff for private material.
6. Update `CHANGELOG.md` for user-visible changes.

Pull requests should explain the problem, the chosen approach, compatibility impact, and test
evidence. By participating, you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Dependency updates

Dependabot groups weekly minor and patch updates by ecosystem. Major-version updates remain
separate pull requests so their migration notes and compatibility impact can be reviewed
individually. Dependency pull requests are not merged automatically: they must pass
`release-checks`, receive a current Codex Review with no unresolved threads, and be merged by a
maintainer. When several major updates touch adjacent constraints, merge them one at a time and
rerun the checks after each rebase.

## Reporting security issues

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md) instead.

## License

By contributing, you agree that your contribution is licensed under the repository's MIT License.
This does not grant rights to third-party source material or translations.
