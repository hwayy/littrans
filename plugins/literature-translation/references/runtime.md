# Local runtime

Run the CLI through the bundled source launcher:

```text
python <plugin-root>/scripts/littrans.py doctor
```

The first invocation creates a versioned virtual environment in the current user's cache (`%LOCALAPPDATA%\littrans` on Windows, `$XDG_CACHE_HOME/littrans` — by default `~/.cache/littrans` — on Linux and macOS) and installs the plugin's declared Python dependencies. This may need package-index network access. Subsequent invocations reuse that environment. The launcher never installs into the system Python and never stores the environment inside the plugin or translation project.

When `littrans` is already installed as a console command, it is equivalent and may be used directly.
The wheel includes its bilingual rendering template, so rendering through the installed command
does not depend on the plugin source tree.

## Layout runtime

Source preparation depends on an isolated CPU layout detector (MinerU 3.4.5 with the
PP-DocLayoutV2 weights). It is a required component: `source prepare` refuses to run without it
unless `--allow-missing-layout` is passed at the user's explicit request, and pages prepared that
way require full visual region review.

`doctor` reports the detector under `layout_runtime`, and `layout status` gives the same detail.
Install or repair it with:

```text
python <plugin-root>/scripts/littrans.py layout install
```

The command creates a separate virtual environment in the same cache
(`littrans/layout/venv`, so `%LOCALAPPDATA%\littrans\layout` or `~/.cache/littrans/layout`), installs CPU-only PyTorch and MinerU 3.4.5, and downloads the
PP-DocLayoutV2 weights (about 200 MB) from Hugging Face (`--model-source modelscope` is the
alternative mirror). MinerU requires a Python 3.10-3.13 base interpreter; pass `--python PATH`
or set `LITTRANS_LAYOUT_BASE_PYTHON` when the launcher's own interpreter is outside that range.
Expect roughly 1.5 GB of disk and package-index plus model-hub network access on first install.
`LITTRANS_LAYOUT_PYTHON` and `LITTRANS_LAYOUT_MODEL` still override the interpreter and weight
locations for an externally managed runtime. The detector never decodes formulas; it only
proposes regions that the source review must confirm against the original page.

## Environment variables

| Variable | Effect |
| --- | --- |
| `LITTRANS_LAYOUT_PYTHON`, `LITTRANS_LAYOUT_MODEL` | Use an externally managed detector interpreter and weight directory instead of the cache. |
| `LITTRANS_LAYOUT_BASE_PYTHON` | The 3.10–3.13 interpreter `layout install` builds the detector environment from. |
| `LITTRANS_PLUGIN_ROOT` | The plugin directory a project's `tools/lt.py` launcher runs (one containing `scripts/littrans.py`); otherwise the launcher tries the root recorded when it was generated (or the same path under this user's home) while it exists, the install of the client running the session (detected from the same environment signals as workflow coordination: Claude Code's `~/.claude/plugins/installed_plugins.json` record, otherwise the highest version in that client's cache), the newest sibling of a recorded root outside every cache, and then every client's install by version (`~/.claude/plugins/cache/littrans/literature-translation/<version>`, `~/.codex/plugins/cache/…`, `~/.cursor/plugins/local/literature-translation`, `~/.qoder-cn/plugins/literature-translation`; build metadata such as `+codex.<stamp>` sorts as a later build of the same version). |
| `LITTRANS_LAUNCHER_VERBOSE` | `tools/lt.py` prints the plugin root it resolved. |
| `XDG_CACHE_HOME` / `LOCALAPPDATA` | Where the CLI and layout environments live (POSIX / Windows). |

## Hosts

The plugin runs on Windows, Linux and macOS with the same record. A Windows console should
run the CLI with `PYTHONIOENCODING=utf-8` when piping its output; the CLI writes its own
files as UTF-8 with LF line endings on every platform. A project's `tools/lt.sh` needs the
executable bit on POSIX (`chmod +x tools/lt.sh`, or `git update-index --chmod=+x tools/lt.sh`
from a Windows checkout); `sh tools/lt.sh …` works without it.
