# Project configuration

Schema-7 projects separate the immutable source manifest (`project.yaml`), shared
policy (`settings.yaml`), private machine bindings (`settings.local.yaml`) and
workflow state (`derived/project-state.json`). Source locations resolve relative
to the project, regardless of the CLI working directory. A relocated PDF must
match the manifest hash. Credentials remain in the external provider's CLI.

Initialize with `project init SOURCE PROJECT --preset technical-book` (or
`research-paper`). Presets are expanded once. A plugin upgrade does not change
saved settings. Older project formats are rejected; `project rebuild OLD NEW`
copies reusable source/context into a new project without inheriting approvals. A v7 rebuild
keeps `settings.yaml` and reviewer executable bindings unless `--settings preset` is given, and
reports what was preserved, reset and not migrated.

## Read and validate

```text
littrans config show PROJECT
littrans config show PROJECT --effective --host codex
littrans config get PROJECT agents.codex
littrans config schema
littrans config validate PROJECT --host codex
```

The effective view distinguishes configured overrides, resolved values, their
origins and host capabilities. A dispatch that requests an unsupported model or
effort fails. Claude agents use their fixed effort; Cursor and Qoder inherit
their host selection. OpenCode policies require regenerated native agents.

## Make changes

```text
littrans config set PROJECT agents.codex.roles.translate.model MODEL
littrans config set PROJECT agents.codex.roles.revise.model null --json
littrans config set PROJECT batch.max_source_words 1200 --json --dry-run
littrans config apply PROJECT candidate.yaml --expect CURRENT_SHA256
littrans config unset PROJECT agents.codex.roles.revise.model
littrans config reset PROJECT --preset research-paper --dry-run
```

Values are strings unless `--json` is supplied. `apply` takes a complete candidate
configuration and validates all references before writing. Unknown keys,
duplicate YAML keys, invalid types and missing required settings are errors.
Writes preserve unaffected comments, report semantic differences and affected
artifacts, and leave historical evidence intact. No-op changes do not write.
`--expect` rejects concurrent changes. Required settings cannot be unset.

An absent role field inherits its host default. An explicit null selects the
host's own default. `revise` inherits `translate` before its own overrides;
`external-recheck` inherits `audit`. The three `audit_lenses` override audit
separately. `scout` and `terminology` are also configurable roles.

`config reset` replaces shared policy with an expanded preset, preserving the
document metadata. Preview first. Local command bindings to removed reviewers
must be unset before removing those reviewer definitions.

## External review

`external_review.reviewers` maps IDs to driver, model, model_identity and effort.
`primary` and ordered `fallbacks` reference those IDs. Disabled review may have
no primary. Enabling it requires a valid primary. Driver-specific restrictions
are validated. `timeout_seconds` applies to each invocation; format repair is
limited to the existing single retry. A recheck is required when confidence is
below the configured threshold **or** severity occurs in the configured set.

```text
littrans config set PROJECT commands.main C:/Tools/claude.exe --local
littrans config set PROJECT source_path D:/Books/source.pdf --local
```

Local files cannot override policy. PATH executable defaults apply without a
local command binding. Neither config validation nor preview contacts providers.

## Policy and evidence

Source coverage, complete logical units and assets, visual evidence, protected
symbols and separate reader notes remain mandatory. Project policy can choose
batch sizes, headings, punctuation, minimum translation-memory status, figure
text handling, code annotations, table translation and asset presentation.
Machine checks enforce structural requirements; independent reviewers assess
semantic requirements, including whether reader-note citations are primary
sources. A URL alone is not evidence of source quality.

Code annotations use `code_annotations` with `kind` (`comment` or `string`),
`source` and `target`; image-native code also names its original `asset_id` and requires
original-image viewing evidence and independent review. They are rendered separately, never substituted into the
original executable code. Disabled annotation categories are rejected.

Formula presentation independently selects `original` or
`reviewed-transcription` for inline/display math. The latter requires verified
transcription for final delivery. Tables can use original images or reviewed
transcription with an explicit image fallback policy. Original evidence remains
available even when a transcription is selected.

Tasks save policy snapshots. Dispatch and machine-path changes affect new tasks;
they do not invalidate existing content evidence. Source and translation policy
changes invalidate their dependent evidence. Presentation changes affect output
and may introduce delivery requirements without invalidating translation audits.
Use the returned next actions; never erase evidence to clear a stale gate.

## Context resources

Keep prose in Markdown, terms in glossary YAML, and page rules in source-structure
JSON. Supported resource names: `brief`, `style`, `approved`, `reference`,
`candidates`, `source-structure`.

```text
littrans context show PROJECT brief
littrans context validate PROJECT
littrans context apply PROJECT changes.yaml --dry-run
littrans context apply PROJECT changes.yaml --expect CURRENT_SHA256
```

The change manifest contains `resources`, a mapping from resource names to full
candidate files relative to the manifest, and an optional `reason`. Changes to
approved terms require a reason. Submit candidate removal and approved additions
together when promoting a term. Candidates are validated in isolation before
the multi-file transaction; failed writes restore the previous content.
Content import grants no source, translation or human approval.

The generated [field ownership reference](settings-fields.md) lists consumers and change domains.
Approved entries with conflicting translations in overlapping scopes are rejected, including
direct edits. Disjoint page scopes remain distinct; no import grants review or approval.
