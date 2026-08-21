# Releasing LitTrans

Releases are prepared on a topic branch, validated locally and in GitHub Actions, and distributed
through the public GitHub marketplace. The release-only `stable` branch is the consumer entry
point; `main` remains the development branch.

## Release checklist

1. Create a topic branch and complete the intended changes.
2. Choose the next semantic version.
3. Set the same version in:
   - `plugins/literature-translation/.codex-plugin/plugin.json`
   - `plugins/literature-translation/.cursor-plugin/plugin.json`
   - `plugins/literature-translation/pyproject.toml`
   - `plugins/literature-translation/src/littrans/__init__.py`
4. Update `CHANGELOG.md` with the release date and user-visible changes.
5. Run `./scripts/check.ps1` from the repository root.
6. Review `git diff` and confirm that no PDFs, workspaces, generated artifacts, credentials, or
   local environments are tracked.
7. Open a pull request to `main`, pass the `release-checks` workflow and review, then merge it with
   a merge commit.
8. Create an annotated `v<version>` tag on the final `main` merge commit.
9. Fast-forward the `stable` branch to that tagged release commit. Never advance `stable` to an
   untagged development commit.
10. Push `main`, `stable`, and the tag to `origin`.
11. Create a GitHub Release targeting the tag. Publish it as the latest non-prerelease release;
    GitHub's generated source archives are the only release artifacts.
12. On Codex consumer clients without active work, refresh the Git-backed `littrans` marketplace,
    reinstall the plugin, and verify the installed version with `codex plugin list --json`.
13. On the Codex primary development client, reinstall from its configured local `littrans`
    marketplace without refreshing a Git marketplace.
14. On Cursor clients without active work, update the local plugin path under
    `~/.cursor/plugins/local/literature-translation`, reload the window, and confirm the skills
    and agents in Customize.
15. Start a new agent session for the updated plugin on each host.

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
an installed local Codex development build. They are not release versions and must not be committed
to `main` or tagged. Published releases use the plain semantic version.
