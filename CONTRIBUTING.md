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

## Reporting security issues

Do not open a public issue for a vulnerability. Follow [SECURITY.md](SECURITY.md) instead.

## License

By contributing, you agree that your contribution is licensed under the repository's MIT License.
This does not grant rights to third-party source material or translations.
