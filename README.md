# LitTrans

[![Release checks](https://github.com/hwayy/littrans/actions/workflows/release-checks.yml/badge.svg?branch=main)](https://github.com/hwayy/littrans/actions/workflows/release-checks.yml)
[![GitHub release](https://img.shields.io/github/v/release/hwayy/littrans)](https://github.com/hwayy/littrans/releases/latest)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

LitTrans helps you translate English technical books and research papers into Simplified
Chinese. It preserves source material, organizes translation and independent review, and
produces bilingual reading editions. Saved project state lets you resume work across sessions.

This repository contains the `literature-translation` plugin and its deterministic Python CLI.
Models run through your coding assistant; the CLI manages files, evidence and validation.
Codex, Claude Code and OpenCode have dedicated integration. Cursor and Qoder use the shared
plugin workflows with general compatibility.

## Get started

Follow the [installation guide](plugins/literature-translation/references/installation.md)
for your host, then open a new session and follow the
[plugin quick start](plugins/literature-translation/README.md).
You will need Python 3.12 or newer and a local PDF. Source extraction also needs the layout
detector described in the installation guide.

Ask the assistant to prepare a small page range first. Review the source checkpoint before
requesting translation. Keep PDFs and translation projects in a separate private workspace.

## Develop

The implementation lives in `plugins/literature-translation/`; repository checks and release
tooling live in `scripts/`. Start with [Contributing](CONTRIBUTING.md) for environment setup,
local plugin installation and checks. From the repository root, run:

```powershell
./scripts/check.ps1
```

On Linux or macOS, use `bash scripts/check.sh`.

## Documentation

- [CLI reference](plugins/literature-translation/references/cli-reference.md): commands, parameters and data contracts.
- [Project migration](plugins/literature-translation/MIGRATING.md): upgrade existing workspaces.
- [Changelog](CHANGELOG.md) and [release procedure](RELEASING.md).
- [Security](SECURITY.md) and [Code of Conduct](CODE_OF_CONDUCT.md).

## License and source material

The [MIT License](LICENSE) covers repository code and documentation. It does not license
third-party PDFs, extracted material or translations. LitTrans is intended for private research
reading; keep source material, project outputs and credentials out of this public repository.
