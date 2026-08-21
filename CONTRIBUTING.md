# Contributing to LitTrans

Thank you for helping improve LitTrans. Contributions should preserve deterministic project
state, resumable workflows, review evidence, and compatibility with long-running translation
projects.

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
