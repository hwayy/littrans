# Literature Translation

Translate technical books and papers into Simplified Chinese while retaining original figures,
tables and formulas. LitTrans preserves the source, organizes translation and independent
review, and exports bilingual HTML and Markdown reading editions. Work is saved in a project
so you can stop and resume later.

## Set up

[Install the plugin for your host](references/installation.md), then start a new assistant
session. You need Python 3.12 or newer. Check the runtime and install the layout detector once:

```text
python <plugin-root>/scripts/littrans.py doctor
python <plugin-root>/scripts/littrans.py layout install
```

Replace `<plugin-root>` with the installed plugin directory. The launcher prepares its Python
dependencies when needed. See [runtime troubleshooting](references/runtime.md) if setup fails.
Keep your PDF and translation workspace outside the plugin directory.

## Start a project

Invoke the `literature-translation` skill in your assistant and give it the PDF path, a private
project directory and a small page range. For example:

> Prepare PDF pages 1–10 of /books/example.pdf in /work/example-translation.
> Show me the preserved source before starting translation.

Once the source is ready:

> Translate the prepared pages into Simplified Chinese, review the translation, and produce
> a bilingual reading edition.

You can request source preparation, terminology work or translation separately. Optional
formula and table transcription can follow later when project policy permits it; original
images remain available. Required transcription must pass before final delivery.

If you prefer direct CLI setup:

```text
python <plugin-root>/scripts/littrans.py project init /books/example.pdf /work/example-translation
python <plugin-root>/scripts/littrans.py status /work/example-translation
```

The [CLI reference](references/cli-reference.md) covers all commands and input formats.
The assistant performs model work; creating a CLI task does not itself run a model.

## Resume and upgrade

In a new session, give the assistant the existing project path and the scope to continue:

> Resume /work/example-translation. Check its saved status and continue pages 1–10.

Saved work and review evidence determine what remains. After a plugin upgrade, follow
[project migration](MIGRATING.md) before continuing an older workspace.

## Further reading

- [Installation and upgrades](references/installation.md).
- [CLI reference](references/cli-reference.md), including data contracts.
- [Source preparation](references/source-processing.md) and [translation workflow](references/translation-workflow.md).
- [Context management](references/context-management.md) and [reading editions](references/rendering.md).
- [Host adapters](references/host-runtimes.md) for advanced integration.

Source files and translations remain private project material. The plugin license does not
grant rights to publish them.
