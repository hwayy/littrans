# Releasing LitTrans

Releases are prepared on a topic branch, validated locally and in GitHub Actions, and distributed
through the public GitHub marketplace. The release-only `stable` branch is the consumer entry
point; `main` remains the development branch.

## Release checklist

1. Create a topic branch and complete the intended changes.
2. Choose the next semantic version.
3. Set the same version in:
   - `plugins/literature-translation/.claude-plugin/plugin.json`
   - `plugins/literature-translation/.codex-plugin/plugin.json`
   - `plugins/literature-translation/.cursor-plugin/plugin.json`
   - `plugins/literature-translation/.qoder-plugin/plugin.json`
   - `plugins/literature-translation/pyproject.toml`
   - `plugins/literature-translation/src/littrans/__init__.py`
4. Update `CHANGELOG.md` with the release date and user-visible changes, using the headings the
   changelog introduction lists, and update `plugins/literature-translation/MIGRATING.md` so it
   takes a project from every earlier version to the new one.
5. Run `./scripts/check.ps1` (Windows) or `bash scripts/check.sh` (Linux, macOS) from the repository root; the `release-checks` workflow runs both.
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
15. On Qoder clients without active work, update the plugin under
    `~/.qoder-cn/plugins/literature-translation`, confirm it is enabled in
    `~/.qoder-cn/settings.json`, and verify the installed version.
16. Start a new agent session for the updated plugin on each host.

## 0.6 acceptance evidence

Before releasing 0.6, retain the source-coverage sample report, independent production/review
results, original-image fallback checks, offline renderer checks and installable build validation.
Source PDFs and private results stay outside the tracked plugin. Report known omissions, cuts,
translation defects, reliable structured coverage, fallback proportion and unavailable usage
honestly. A sample with zero known omissions is not a whole-book guarantee.

Validate all seven skills, local role prompts, schema contracts, packaging and all host manifests.
Test rebuilding into a fresh directory without inheriting old approvals. Stable installation changes
follow the release checklist, separately from implementing or testing the development branch.

## Local installable builds and recovery

Use a validated Python 3.12+ environment with Hatchling and the plugin dependencies. From the
repository root, build outside the plugin source directory:

```powershell
python scripts/build_distribution.py ..\littrans-build
```

(`python3 scripts/build_distribution.py ../littrans-build` on Linux or macOS; the smoke commands
below take the same forward-slash paths and `unzip` in place of `Expand-Archive`.)

The command first validates the release, then writes the versioned wheel, plugin ZIP and
`build-manifest.json`. The manifest records artifact SHA-256 values and the packaged plugin file
hashes. This creates reviewable local artifacts without switching the stable installation.

Test the wheel in a fresh isolated environment or `pip --target` directory. When the selected
interpreter already has the validated dependencies, an offline target installation can use:

```powershell
python -m pip install --no-index --no-deps --target ..\littrans-wheel-smoke ..\littrans-build\littrans-0.7.6-py3-none-any.whl
Expand-Archive -LiteralPath ..\littrans-build\literature-translation-0.7.6.zip -DestinationPath ..\littrans-zip-smoke
python ..\littrans-zip-smoke\literature-translation\scripts\littrans.py doctor
```

Use the release's actual version in those filenames. For the wheel check, set `PYTHONPATH` to its
isolated target and verify the imported module path, version and all three bundled profiles. Create
a synthetic PDF project, confirm source preparation produces a review packet while unavailable
layout detection leaves approval pending, and verify every copied offline MathJax file against its
vendored manifest. Run the plugin-creator `validate_plugin.py` against the extracted plugin too.

Keep smoke results beside the build, including exact artifact hashes, Python/dependency versions,
commands and exit codes. A failed installation or extraction should be retried in a fresh target;
preserve the earlier report and successful responses. If packaged files change, rebuild and repeat
the affected distribution checks against the new hashes. Never repair an artifact by copying files
into a stable plugin cache or inherit approval from a synthetic smoke project.

## Compatibility policy

- Patch releases contain compatible fixes and workflow refinements.
- During 0.x development, minor releases may change the project contract. LitTrans 0.6 requires a new schema-6 project via `project rebuild OLD NEW` for projects from 0.5 or earlier; old approvals are not migrated. Projects from any 0.6 or 0.7 build upgrade in place.
- Major releases may require an explicit project migration.
- Long-running translation projects should record the LitTrans version used for each formal
  processing stage.
- Never delete an installed cache version while a running task may still call its scripts,
  templates, schemas, or skill references.

## Development versions

Between releases the version is a semantic-versioning pre-release of the next release,
`<next>-dev.N` (for example `0.7.7-dev.1` after 0.7.6), set in the same six files as a release
version, with a `CHANGELOG.md` section of its own.
Bump `N` in every commit that changes behaviour on the development branch, whether or not it
is installed anywhere: the version string is the only signal `claude plugin update` compares,
and it names the cache directory, so two builds under one version share a directory and
`update` reports "already at the latest version" while the installed commit falls behind.
Artifacts record the exact build in their `generator` block (`plugin_version`, `build_digest`)
and `littrans doctor` prints the installed `build`, so an installation can always be checked
against a checkout. The release commit replaces the suffix with the plain version;
`validate_release.py` accepts both forms.

## Development builds on Claude Code

`claude plugin update` is a no-op while the installed version string is unchanged, so a
development build that kept its version must be reinstalled (`claude plugin uninstall` then
`claude plugin install literature-translation@littrans`) or loaded from the checkout with
`claude --plugin-dir plugins/literature-translation`. The installed launcher runs from the cached
`src/` tree, so a hot copy of changed files into the cache is a valid short-lived test only.
A directory marketplace copies the working tree, caches included: clear `__pycache__` and the
tool caches before installing if a file-by-file comparison of the installation matters.

## Development cachebusters

Local cachebuster versions such as `0.2.2+codex.<timestamp>` may be used temporarily while testing
an installed local Codex development build. They are not release versions and must not be committed
to `main` or tagged; the committed development version is the `-dev.N` pre-release above.
Published releases use the plain semantic version.
