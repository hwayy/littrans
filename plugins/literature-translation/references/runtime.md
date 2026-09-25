# Local runtime

Run the CLI through the bundled source launcher:

```text
python <plugin-root>/scripts/littrans.py doctor
```

The first invocation creates a versioned virtual environment in the current user's cache (`%USERPROFILE%\.littrans` on Windows, `$XDG_CACHE_HOME/littrans` — by default `~/.cache/littrans` — on Linux and macOS; `LITTRANS_CACHE_DIR` overrides both) and installs the plugin's declared Python dependencies. This may need package-index network access. Subsequent invocations reuse that environment. The launcher never installs into the system Python and never stores the environment inside the plugin or translation project.

When `littrans` is already installed as a console command, it is equivalent and may be used directly.
The wheel includes its bilingual rendering template, so rendering through the installed command
does not depend on the plugin source tree.

## Layout runtime

Source preparation depends on an isolated CPU layout detector (MinerU 3.4.5 with the
PP-DocLayoutV2 weights). It is a required component: `source prepare` refuses to run without it
unless `--allow-missing-layout` is passed at the user's explicit request, and pages prepared that
way require full visual region review.

`doctor` reports the detector under `layout_runtime`, and `layout status` gives the same detail.
Both run the detector interpreter itself: it must import PyTorch and the MinerU layout model,
and it must be the Python its `pyvenv.cfg` records. `identity` shows the configured and resolved
interpreter paths, the recorded `home` and version and the version and base executable that
actually run; a mismatch or a failed import makes `ok` false with the reason, since a ready
receipt only proves the state at install time. Install it with:

```text
python <plugin-root>/scripts/littrans.py layout install
```

The command creates a separate virtual environment in the same cache
(`layout/venv`, so `%USERPROFILE%\.littrans\layout` or `~/.cache/littrans/layout`), installs CPU-only PyTorch and MinerU 3.4.5, and downloads the
PP-DocLayoutV2 weights (about 200 MB) from Hugging Face (`--model-source modelscope` is the
alternative mirror). MinerU requires a Python 3.10-3.13 base interpreter; pass `--python PATH`
or set `LITTRANS_LAYOUT_BASE_PYTHON` when the launcher's own interpreter is outside that range.
Expect roughly 1.5 GB of disk and package-index plus model-hub network access on first install.
`LITTRANS_LAYOUT_PYTHON` and `LITTRANS_LAYOUT_MODEL` still override the interpreter and weight
locations for an externally managed runtime. The detector never decodes formulas; it only
proposes regions that the source review must confirm against the original page.

Repair a broken environment with `layout install --repair`: it deletes and recreates only
`layout/venv` and keeps the weights its ready receipt verifies, so nothing is downloaded again
(`--force` recreates the environment and downloads the weights). Never run `pip` against the
layout environment by hand, and never move or delete files inside it: a package built for
another Python leaves an environment that still reports its old metadata.

Builds before 0.7.1 kept both environments in `%LOCALAPPDATA%\littrans`. `doctor` names that
directory as `legacy_cache`; `layout install` copies its weights when its own ready receipt
verifies them instead of downloading them, and builds a fresh environment beside them. The old
directory is never removed for you: delete it once `doctor` reports `ok` on the new location.

### Packaged Windows clients

An MSIX-packaged client (the Codex desktop app) has its view of `AppData` redirected: files it
creates there land in a private copy under `%LOCALAPPDATA%\Packages\<package>\LocalCache`,
which shadows the real directory for that client only. Two hosts then run two different
runtimes, and a "repair" from the packaged view writes into the real one. That is why the cache
lives outside `AppData`. `doctor` reports `packaged_app`, reports a runtime that resolves into
such a private copy as `AppData redirection`, `source prepare` treats it as unavailable, and
`layout install` refuses to install into `AppData` from a packaged client. Set
`LITTRANS_CACHE_DIR` only to a directory outside `AppData`.

## Environment variables

| Variable | Effect |
| --- | --- |
| `LITTRANS_LAYOUT_PYTHON`, `LITTRANS_LAYOUT_MODEL` | Use an externally managed detector interpreter and weight directory instead of the cache. |
| `LITTRANS_LAYOUT_BASE_PYTHON` | The 3.10–3.13 interpreter `layout install` builds the detector environment from. |
| `LITTRANS_PLUGIN_ROOT` | The plugin directory a project's `tools/lt.py` launcher runs (one containing `scripts/littrans.py`); otherwise the launcher tries the root recorded when it was generated (or the same path under this user's home) while it exists, the install of the client running the session (detected from the same environment signals as workflow coordination: Claude Code's `~/.claude/plugins/installed_plugins.json` record, otherwise the highest version in that client's cache), the newest sibling of a recorded root outside every cache, and then every client's install by version (`~/.claude/plugins/cache/littrans/literature-translation/<version>`, `~/.codex/plugins/cache/…`, `~/.cursor/plugins/local/literature-translation`, `~/.qoder-cn/plugins/literature-translation`; build metadata such as `+codex.<stamp>` sorts as a later build of the same version). |
| `LITTRANS_LAUNCHER_VERBOSE` | `tools/lt.py` prints the plugin root it resolved. |
| `LITTRANS_CACHE_DIR` | Where the CLI and layout environments live, on every platform. Keep it outside `AppData` on Windows. |
| `XDG_CACHE_HOME` | The cache base on Linux and macOS when `LITTRANS_CACHE_DIR` is unset (Windows uses `%USERPROFILE%\.littrans`). |

## Hosts

The plugin runs on Windows, Linux and macOS with the same record. A Windows console should
run the CLI with `PYTHONIOENCODING=utf-8` when piping its output; the CLI writes its own
files as UTF-8 with LF line endings on every platform. A project's `tools/lt.sh` needs the
executable bit on POSIX (`chmod +x tools/lt.sh`, or `git update-index --chmod=+x tools/lt.sh`
from a Windows checkout); `sh tools/lt.sh …` works without it.
