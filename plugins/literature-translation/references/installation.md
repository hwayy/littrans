# Installation and upgrades

Choose your assistant below. Codex, Claude Code and OpenCode have dedicated integration;
Cursor and Qoder use the shared skills and role instructions with general compatibility.
Start a new host session after installation or upgrade. Keep PDFs and translation projects
outside the plugin directory.

- [Codex](#codex)
- [Claude Code](#claude-code)
- [OpenCode](#opencode)
- [Cursor and Qoder](#cursor-and-qoder)
- [Runtime setup](#runtime-setup)

## Codex

For a released installation, track the release-only `stable` branch:

```text
codex plugin marketplace add https://github.com/hwayy/littrans.git --ref stable
codex plugin add literature-translation@littrans
```

Contributors with GitHub SSH access may use `git@github.com:hwayy/littrans.git` instead.
For local development, run these commands from the repository root:

```text
codex plugin marketplace add .
codex plugin add literature-translation@littrans
```

To upgrade a released installation:

```text
codex plugin marketplace upgrade littrans
codex plugin add literature-translation@littrans
codex plugin list --json
```

For a local marketplace, reinstall with `codex plugin add` without upgrading the marketplace.
Do not remove a build still used by a running chat. Installed development builds may use a
cache-busting version suffix; do not commit or tag that temporary version.

## Claude Code

Register and install the shared plugin marketplace:

```text
claude plugin marketplace add https://github.com/hwayy/littrans.git
claude plugin install literature-translation@littrans
```

Inside an interactive session, use `/plugin marketplace add` and `/plugin install` with the
same arguments. Skills appear as `/literature-translation:<skill-name>`.

For local development, register `.` from the repository root, or load the checkout for one session:

```text
claude --plugin-dir plugins/literature-translation
```

To upgrade a marketplace installation:

```text
claude plugin marketplace update littrans
claude plugin update literature-translation@littrans
```

A `--plugin-dir` session picks up checkout changes on the next start. Installed plugins live
under `~/.claude/plugins/cache/littrans/literature-translation/<version>` on each platform.
See the [Claude adapter](host-claude.md) for advanced dispatch behavior.

## OpenCode

Use a local copy of the plugin and complete runtime setup below. OpenCode discovers generated
project-local agents and skills; follow the [OpenCode adapter](host-opencode.md) to generate them
for a LitTrans project. Regenerate those resources after plugin or project-policy changes,
preserving the same containing workspace, then restart OpenCode. Select `--host opencode`
explicitly when using the CLI.

## Cursor and Qoder

Use a repository copy for manual installation. Released users clone `stable`:

```text
git clone --branch stable https://github.com/hwayy/littrans.git
cd littrans
```

Developers use their existing checkout instead. Copy `plugins/literature-translation` into the
appropriate directory below; use a real directory, not a junction or symlink.

| Host | Destination under the user home directory | Enable / reload |
| --- | --- | --- |
| Cursor | `.cursor/plugins/local/literature-translation` | Enable third-party plugins, skills and configs; reload the window, then check Customize |
| Qoder | `.qoder-cn/plugins/literature-translation` | Enable `literature-translation` in `enabledPlugins` in `.qoder-cn/settings.json`, then start a new session |

From the repository root on Windows, the Cursor copy is:

```powershell
$pluginSource = Join-Path (Get-Location) "plugins/literature-translation"
$pluginDestination = Join-Path $env:USERPROFILE ".cursor/plugins/local/literature-translation"
New-Item -ItemType Directory -Force $pluginDestination | Out-Null
robocopy $pluginSource $pluginDestination /E /XD __pycache__ .pytest_cache .venv .mypy_cache
```

For Qoder, substitute `.qoder-cn/plugins/literature-translation` as the destination. On Linux
or macOS, the equivalent Cursor copy is:

```bash
plugin_destination="$HOME/.cursor/plugins/local/literature-translation"
mkdir -p "$plugin_destination"
rsync -a --exclude __pycache__ --exclude .pytest_cache --exclude .venv --exclude .mypy_cache \
  plugins/literature-translation/ "$plugin_destination/"
```

Before upgrading an existing manual installation, finish or checkpoint active sessions, update
the checkout, and move the old installed directory to a backup location. Copy into a new empty
destination so removed files do not survive the upgrade, then reload the host. Keep the backup
until active work no longer depends on it.

For Qoder, confirm the plugin appears as `literature-translation@littrans` in
`~/.qoder-cn/plugins/installed_plugins_v2.json`. Clients with Git marketplace support may instead
register the repository's `.qoder-plugin/marketplace.json`; manual copying does not require it.
Cursor Teams/Enterprise installations may use `.cursor-plugin/marketplace.json` as a team marketplace.
Use a local Cursor session for private PDFs and project work, rather than Cursor Cloud Agents.

## Runtime setup

The CLI needs Python 3.12 or newer. Replace `<plugin-root>` with the installed plugin path:

```text
python <plugin-root>/scripts/littrans.py doctor
python <plugin-root>/scripts/littrans.py layout install
```

The source launcher prepares its Python dependencies in a user cache if needed. The layout
detector uses a separate compatible interpreter and downloads model weights. See
[runtime setup and troubleshooting](runtime.md) for caches, interpreter selection and repair;
see the [CLI reference](cli-reference.md) for options and environment overrides.

After upgrading the plugin, follow the
[project migration guide](https://github.com/hwayy/littrans/blob/main/plugins/literature-translation/MIGRATING.md)
for existing workspaces. An installed source plugin also includes `MIGRATING.md` at its root.
Command spelling changes alone do not require re-extracting the source.
