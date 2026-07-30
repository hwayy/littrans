# Releasing LitTrans

Releases are prepared on the primary development client and distributed through the private
GitHub marketplace. GitHub Actions is not required; the same checks are run locally before every
release.

## Release checklist

1. Create a topic branch and complete the intended changes.
2. Choose the next semantic version.
3. Set the same version in:
   - `plugins/literature-translation/.codex-plugin/plugin.json`
   - `plugins/literature-translation/pyproject.toml`
   - `plugins/literature-translation/src/littrans/__init__.py`
4. Update `CHANGELOG.md` with the release date and user-visible changes.
5. Run `./scripts/check.ps1` from the repository root.
6. Review `git diff` and confirm that no PDFs, workspaces, generated artifacts, credentials, or
   local environments are tracked.
7. Merge the release commit into `master` and create an annotated `v<version>` tag.
8. Fast-forward the `stable` branch to that tagged release commit. Never advance `stable` to an
   untagged development commit.
9. Push `master`, `stable`, and the tag to `origin`.
10. On consumer clients without active work, refresh the Git-backed `littrans` marketplace,
   reinstall the plugin, and verify the installed version with `codex plugin list --json`.
11. On the primary development client, reinstall from its configured local `littrans` marketplace
   without refreshing a Git marketplace.
12. Start a new Codex task for the updated plugin.

## Compatibility policy

- Patch releases contain compatible fixes and workflow refinements.
- Minor releases add compatible capabilities or project-schema migrations.
- Major releases may require an explicit project migration.
- Long-running translation projects should record the LitTrans version used for each formal
  processing stage.
- Never delete an installed cache version while a running task may still call its scripts,
  templates, schemas, or skill references.

## Development cachebusters

Local cachebuster versions such as `0.2.2+codex.<timestamp>` may be used temporarily while testing
an installed local development build. They are not release versions and must not be committed to
`master` or tagged. Published releases use the plain semantic version.
