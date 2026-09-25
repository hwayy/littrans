# LitTrans

[![Release checks](https://github.com/hwayy/littrans/actions/workflows/release-checks.yml/badge.svg?branch=main)](https://github.com/hwayy/littrans/actions/workflows/release-checks.yml)
[![GitHub release](https://img.shields.io/github/v/release/hwayy/littrans)](https://github.com/hwayy/littrans/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

LitTrans is a public Git-backed marketplace for the `literature-translation` plugin. The plugin
provides a controlled, resumable workflow for translating English technical books and research
papers into Simplified Chinese. It installs on Codex, Cursor, Claude Code and Qoder from the same plugin tree.

The implementation lives in [`plugins/literature-translation`](plugins/literature-translation/).
Source PDFs, extracted assets, translation workspaces, credentials, and generated reading
editions are intentionally kept outside version control.

## Unified 0.7 workflow

Preserve native prose and original complex-element images first, then run independent transcription
and translation tasks against that shared source context. Source coverage, translated meaning and
structured-asset correctness have separate review states. Reviewed reading editions retain original
images whenever LaTeX or another structured representation is unfinished or unverified.

Every model stage — source review and correction, translation, transcription, the three audit
lenses and asset review — runs in a fresh subagent dispatched by the coordinating session. Role
models are configured per host in each project's `agent_models`, seeded from the plugin's
`profiles/host-models.yaml`. Recommended on Codex: `gpt-6-luna` at `max` effort for `translate`
and `transcribe`, `gpt-6-sol` at `high` for `audit`, `asset-audit` and `source-review`. On Claude
Code every role uses `sonnet`, and the effort is the `effort: high` each LitTrans agent declares,
because Claude Code takes no per-dispatch effort. Leaving a role unset dispatches on the host's
own default, which is the only possibility on Cursor and Qoder; `project models PROJECT --host
HOST` reports the resolved policy and the plugin advises rather than blocks when configuration
and host capability disagree.
Source preparation requires the isolated layout detector installed by `littrans layout install`.
Projects from 0.5 or earlier rebuild into a new schema-6 directory with source/context/glossary/docs
only; projects from any 0.6 or 0.7 build upgrade in place. `project init` also grows the project's record
structure (handbook, records, defect ledger, launcher, `.gitignore`, and a plugin-owned
`docs/LITTRANS.md` stating what the installed build guarantees); `project scaffold --refresh`
regenerates it after an upgrade and `project tracked` asks git whether exactly the record is
tracked. See the [plugin workflow](plugins/literature-translation/README.md) and [migration
guide](plugins/literature-translation/MIGRATING.md).

## Repository layout

```text
.agents/plugins/marketplace.json          Codex marketplace catalog
.cursor-plugin/marketplace.json           Cursor marketplace catalog
.claude-plugin/marketplace.json           Claude Code marketplace catalog
.qoder-plugin/marketplace.json            Qoder marketplace catalog
plugins/literature-translation/           Installable plugin
plugins/literature-translation/.codex-plugin/   Codex plugin manifest
plugins/literature-translation/.cursor-plugin/  Cursor plugin manifest
plugins/literature-translation/.claude-plugin/  Claude Code plugin manifest
plugins/literature-translation/.qoder-plugin/   Qoder plugin manifest
plugins/literature-translation/skills/    Translation workflows
plugins/literature-translation/agents/    Cursor, Claude Code and Qoder local subagents
plugins/literature-translation/profiles/  Document profiles and per-host model defaults
plugins/literature-translation/src/       Deterministic Python tooling
scripts/                                  Repository validation commands
```

## Install on Codex

The public marketplace can be installed anonymously over HTTPS. Do not put tokens in this
repository or in marketplace configuration.

### Consumer clients

Track the release-only `stable` branch:

```powershell
codex plugin marketplace add https://github.com/hwayy/littrans.git --ref stable
codex plugin add literature-translation@littrans
```

Authenticated contributors may use SSH instead:

```powershell
codex plugin marketplace add git@github.com:hwayy/littrans.git --ref stable
codex plugin add literature-translation@littrans
```

Start a new Codex task after installation.

### Primary development client

The primary development client uses its local checkout as the marketplace source. From the
repository root:

```powershell
codex plugin marketplace add .
codex plugin add literature-translation@littrans
```

Released builds use a plain semantic version. Temporary installed development builds may use one
`+codex.<cachebuster>` suffix generated by the plugin update tooling, but those versions must not
be committed or tagged.

### Update a Codex client

Published plugin changes always receive a new semantic version. On a consumer client, refresh the
Git marketplace, reinstall the selected plugin version, verify it, and then start a new task:

```powershell
codex plugin marketplace upgrade littrans
codex plugin add literature-translation@littrans
codex plugin list --json
```

Do not remove an older installed version while a running task still depends on it. Existing
tasks should finish or reach a durable checkpoint before the client installation is migrated.
After any upgrade, bring existing projects up to date with
[MIGRATING.md](plugins/literature-translation/MIGRATING.md).
On the primary development client, reinstall directly from its configured local `littrans`
marketplace without running `marketplace upgrade`.

## Install on Cursor

Enable **Include third-party Plugins, Skills, and other configs** in Cursor settings. Load the
plugin from `~/.cursor/plugins/local/literature-translation`, then reload the window
(**Developer: Reload Window**). Confirm the skills and the local production and review agents in **Customize**.

Keep source PDFs and translation workspaces on the local machine. Do not run this workflow through
Cursor Cloud Agents or `/in-cloud`.

Cursor rejects local plugins whose path is a junction or symlink into another tree. Copy the plugin
directory into `~/.cursor/plugins/local` instead of linking it.

### Consumer clients

Clone or update the `stable` branch, then copy the plugin directory:

```powershell
git clone --branch stable https://github.com/hwayy/littrans.git
$repo = Join-Path (Get-Location) "littrans"
$src = Join-Path $repo "plugins\literature-translation"
$dest = Join-Path $env:USERPROFILE ".cursor\plugins\local\literature-translation"
New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
robocopy $src $dest /E /XD __pycache__ .pytest_cache .venv .mypy_cache
```

On Linux or macOS:

```bash
git clone --branch stable https://github.com/hwayy/littrans.git
dest="$HOME/.cursor/plugins/local/literature-translation"
rm -rf "$dest" && mkdir -p "$(dirname "$dest")"
rsync -a --exclude __pycache__ --exclude .pytest_cache --exclude .venv --exclude .mypy_cache \
  littrans/plugins/literature-translation/ "$dest/"
```

Contributors with GitHub authentication may use the SSH repository URL instead.
Start a new Cursor agent session after installation.

### Primary development client

Copy the local plugin checkout into Cursor's local plugin directory. From the repository root:

```powershell
$src = Join-Path (Get-Location) "plugins\literature-translation"
$dest = Join-Path $env:USERPROFILE ".cursor\plugins\local\literature-translation"
New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
robocopy $src $dest /E /XD __pycache__ .pytest_cache .venv .mypy_cache
```

```bash
dest="$HOME/.cursor/plugins/local/literature-translation"
rm -rf "$dest" && mkdir -p "$(dirname "$dest")"
rsync -a --exclude __pycache__ --exclude .pytest_cache --exclude .venv --exclude .mypy_cache \
  plugins/literature-translation/ "$dest/"
```

A Teams or Enterprise plan may also import this repository as a Cursor team marketplace from
`.cursor-plugin/marketplace.json`. Local install does not require that.

### Update a Cursor client

Published plugin changes always receive a new semantic version. Finish or checkpoint any running
session first. Recopy the plugin directory into `~/.cursor/plugins/local/literature-translation`,
reload the window, and start a new agent session. A leftover junction from an older install will
be ignored. After any upgrade, bring existing projects up to date with
[MIGRATING.md](plugins/literature-translation/MIGRATING.md).

## Install on Claude Code

Claude Code (CLI and the desktop app's Code tab) reads the same plugin tree through
`.claude-plugin/marketplace.json`. Add the marketplace and install the plugin, then start a new
session:

### Consumer clients

```powershell
claude plugin marketplace add https://github.com/hwayy/littrans.git
claude plugin install literature-translation@littrans
```

Inside an interactive session the equivalent commands are `/plugin marketplace add
https://github.com/hwayy/littrans.git` and `/plugin install literature-translation@littrans`.

### Primary development client

From the repository root, either register the checkout as a local marketplace:

```powershell
claude plugin marketplace add .
claude plugin install literature-translation@littrans
```

or load the plugin for a single CLI session without installing it:

```powershell
claude --plugin-dir plugins/literature-translation
```

Skills are invoked as `/literature-translation:<skill-name>`; the plugin subagents appear as
`literature-translation:<agent-name>`. Run `python plugins/literature-translation/scripts/littrans.py doctor`
and `... layout install` once so the CLI runtime and the required layout detector are available.
The commands are the same on Windows, Linux and macOS; the installed plugin lives under
`~/.claude/plugins/cache/littrans/literature-translation/<version>` on every platform. Keep
source PDFs and translation workspaces on the local machine.

### Update a Claude Code client

Finish or checkpoint any running session first. Run `claude plugin marketplace update littrans`
followed by `claude plugin update literature-translation@littrans` (a `--plugin-dir` session simply
picks up the checkout on its next start), then start a new session. After any upgrade, bring existing projects up to date with
[MIGRATING.md](plugins/literature-translation/MIGRATING.md).

## Install on Qoder

Qoder reads the same plugin tree through `.qoder-plugin/plugin.json` and reuses the shared
`skills/*/SKILL.md` and `agents/*.md` files. The verified install path is to copy the plugin
directory into Qoder's user plugin folder, enable it, and start a new session.

### Consumer clients

Clone or update the `stable` branch, then copy the plugin directory:

```powershell
git clone --branch stable https://github.com/hwayy/littrans.git
$repo = Join-Path (Get-Location) "littrans"
$src = Join-Path $repo "plugins\literature-translation"
$dest = Join-Path $env:USERPROFILE ".qoder-cn\plugins\literature-translation"
New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
robocopy $src $dest /E /XD __pycache__ .pytest_cache .venv .mypy_cache
```

On Linux or macOS:

```bash
git clone --branch stable https://github.com/hwayy/littrans.git
dest="$HOME/.qoder-cn/plugins/literature-translation"
rm -rf "$dest" && mkdir -p "$(dirname "$dest")"
rsync -a --exclude __pycache__ --exclude .pytest_cache --exclude .venv --exclude .mypy_cache \
  littrans/plugins/literature-translation/ "$dest/"
```

Contributors with GitHub authentication may use the SSH repository URL instead.

### Primary development client

Copy the local plugin checkout into Qoder's plugin directory. From the repository root:

```powershell
$src = Join-Path (Get-Location) "plugins\literature-translation"
$dest = Join-Path $env:USERPROFILE ".qoder-cn\plugins\literature-translation"
New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
if (Test-Path $dest) { Remove-Item $dest -Recurse -Force }
robocopy $src $dest /E /XD __pycache__ .pytest_cache .venv .mypy_cache
```

```bash
dest="$HOME/.qoder-cn/plugins/literature-translation"
rm -rf "$dest" && mkdir -p "$(dirname "$dest")"
rsync -a --exclude __pycache__ --exclude .pytest_cache --exclude .venv --exclude .mypy_cache \
  plugins/literature-translation/ "$dest/"
```

Enable `literature-translation` under `enabledPlugins` in `~/.qoder-cn/settings.json` and confirm it
appears as `literature-translation@littrans` in `~/.qoder-cn/plugins/installed_plugins_v2.json`. A
Qoder client that supports a Git marketplace may instead register `.qoder-plugin/marketplace.json`
and install `literature-translation@littrans`; the manual copy above does not depend on that.

Skills are invoked as `/literature-translation:<skill-name>`; the plugin subagents appear as
`literature-translation:<agent-name>`. Run `python <dest>\scripts\littrans.py doctor` and
`... layout install` once so the CLI runtime and the required layout detector are available, then
start a new Qoder session. Keep source PDFs and translation workspaces on the local machine.

### Update a Qoder client

Finish or checkpoint any running session first. Recopy the plugin directory into
`~/.qoder-cn/plugins/literature-translation`, then start a new session. As on every host, a plugin
update is only picked up when the version string changes. After any upgrade, bring existing projects up to date with
[MIGRATING.md](plugins/literature-translation/MIGRATING.md).

## Development and release

Development happens on `main` or topic branches. The `stable` branch advances only to checked,
tagged release commits. Every distributed release increments the version in all host plugin manifests,
Python package metadata, and `littrans.__version__` together.

Run the local release checks from the repository root — the two scripts perform the same steps
(repository `.venv`, release metadata, ruff, mypy, the test suite, `doctor`) and the
`release-checks` workflow runs both on Windows and Linux:

```powershell
./scripts/check.ps1
```

```bash
bash scripts/check.sh
```

See [`RELEASING.md`](RELEASING.md) for the manual release procedure and
[`CHANGELOG.md`](CHANGELOG.md) for version history.

## Scope and privacy

The plugin is intended for private research reading. It does not call a model API directly and
does not determine publication rights. Keep copyrighted source material and translated project
state in separate private workspaces.

The [MIT License](LICENSE) covers only the code and documentation committed to this repository.
It does not license source PDFs, books, papers, translations, credentials, extracted assets, or
project workspaces. Those materials are not part of LitTrans and must not be committed or
published without the relevant rights and permissions.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development guidance and [SECURITY.md](SECURITY.md) for
private vulnerability reporting.
