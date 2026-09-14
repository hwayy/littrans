# Local runtime

Run the CLI through the bundled source launcher:

```text
python <plugin-root>/scripts/littrans.py doctor
```

The first invocation creates a versioned virtual environment in the current user's cache and installs the plugin's declared Python dependencies. This may need package-index network access. Subsequent invocations reuse that environment. The launcher never installs into the system Python and never stores the environment inside the plugin or translation project.

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

The command creates a separate virtual environment in the user's cache
(`littrans/layout/venv`), installs CPU-only PyTorch and MinerU 3.4.5, and downloads the
PP-DocLayoutV2 weights (about 200 MB) from Hugging Face (`--model-source modelscope` is the
alternative mirror). MinerU requires a Python 3.10-3.13 base interpreter; pass `--python PATH`
or set `LITTRANS_LAYOUT_BASE_PYTHON` when the launcher's own interpreter is outside that range.
Expect roughly 1.5 GB of disk and package-index plus model-hub network access on first install.
`LITTRANS_LAYOUT_PYTHON` and `LITTRANS_LAYOUT_MODEL` still override the interpreter and weight
locations for an externally managed runtime. The detector never decodes formulas; it only
proposes regions that the source review must confirm against the original page.
