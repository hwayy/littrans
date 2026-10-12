# Migrating to LitTrans 0.9.2-dev.5

The target format is project manifest v8, SourceUnit v4, TranslationRecord v3 and settings
schema v1. Choose the upgrade or rebuild route below before resuming an older workspace.
See [configuration](references/configuration.md) for policy storage and the
[CLI reference](references/cli-reference.md) for command syntax.

## Updated model recommendations and Codex wave size

New projects recommend `gpt-6.1-sol` for Codex audit, asset-audit and source-review roles,
and `openai/gpt-6.1-sol` for the same OpenCode roles. Their reasoning effort remains `high`.
Existing project settings retain their saved models; use `config set` to change each role
explicitly, and regenerate OpenCode native agents after updating their policies.

Codex keeps a default `agents.codex.wave_size` of 3 and now accepts values up to 6.
Use `littrans config set PROJECT agents.codex.wave_size 6 --json` to opt in.

## Complete composite figure crops

This release exports the panels of one captioned figure as one raw rectangular crop.
PNG and SVG remain two formats of that single fragment. Caption and prose boundaries still
prevent joining different printed figures or a region containing intervening text. Figures
without a detected shared caption, or with sub-captions between panels, require visual review
and, when justified by the page, a single-region override.

Existing multi-fragment records and HTML remain unchanged until source evidence is replaced
and the output rendered again. Merely upgrading or running `source render` does not recrop them.
Back up the project and inspect its recorded `source_overrides` before choosing a route:

- On an unpinned page, run `littrans source extract PROJECT --pages RANGE --replace`.
  This uses its recorded layout evidence when available and applies the new grouping rule.
  Without `--replace`, cached pages retain the old fragments.
- A pinned `regions`, `preserve_asset_id` or `asset_crops` decision replays the old crop even
  with `--replace`. Use fresh `source review-packets`, replace that figure's region with
  `{"id":"EXISTING_ID","kind":"figure","bbox":[x0,y0,x1,y1],"display":true}` and omit
  its `fragments` and `glyph_ids`. Keep the caption outside the box; carry all other regions
  forward with `preserve_asset_id`, and retain the page's units and other override blocks.
  A `regions` list replaces all page regions, not just the figure. Remove that figure's
  obsolete `asset_crops` entries explicitly (use `null` if dropping the whole block).
  Preview with `source preview-review`, then import through normal source review. Correction
  does not approve the page; obtain a new independent review of the resulting packet.
- To deliberately re-derive every decision on a page, use `source extract PROJECT --pages
  RANGE --replace --discard-overrides`, then review all resulting boundaries and units again.
  For a separate project, use `project rebuild OLD NEW` and extract/review NEW; rebuild does
  not inherit old receipts or translations. Keep OLD for recovery.

`asset_crops` changes one existing fragment and cannot collapse several fragments into one.
Do not widen the first fragment while leaving the others, edit the asset registry by hand,
or copy old approvals onto changed content. A complete crop changes the content identity
even when the asset ID is retained. Source receipts and dependent page evidence are
invalidated as applicable; check source verification, batch/source hashes, translation QA
and audit evidence before regenerating the final reading edition. Review narrow and wide
HTML after rendering.

## Other changes

Source review requires contract-v8 packets, including whole-page inspection, paragraph
confirmations and explicit layout adjudications. Old approvals do not certify upgraded
structure. Supplemental `source scan-layout` decisions remain separate from full-page approval;
see [source review](references/source-review.md) for the procedure.

Refresh `project scaffold` to protect task results and inspection evidence from Git newline
conversion. Restore already-converted evidence from an intact copy; never adjust its hashes.
Refresh corrections against new packets when extraction changes asset IDs or grouping.

Applied composed operators such as `log log x`, and the operator names `erf` and `erfc`, no
longer count as translatable formula conditions. Existing original-image crops remain usable.
Remove any obsolete operator-only declarations through normal source-review overrides and
obtain fresh affected review receipts; do not change a receipt or declare an operator merely
to satisfy the language gate. Actual condition words and abbreviations remain subject to the
same declaration checks.

Tightly spaced upright capital operands such as `min MSE` and `argmax ELBO` also remain
notation when a smaller mathematical limit is visibly attached above or below the operator.
This requires complete native words and does not exempt italic text, partial words or capitals
without that layout evidence. Adjacent language conditions still require every native glyph,
including spaces. Existing crops need no format conversion; correct obsolete declarations
through normal source-review overrides and obtain fresh affected review receipts.

New tasks snapshot and hash their instructions as LF text, so Git's line-ending conversion no
longer invalidates them. Tasks created by earlier development builds still verify while their
snapshot bytes are unchanged; keep any `-text` attribute that protects them.

The layout worker's identity in layout fingerprints and the build digest are now computed from
LF text. Installations copied from a checkout that had written the plugin with CRLF therefore
get a new layout fingerprint once: the next extraction runs the detector again instead of
reusing the stored layout result. Clean LF installations are unaffected.

## Preserve the old project

Checkpoint workers, retain their successful responses and back up the entire project. Keep
its recorded plugin installation available. Do not point old and new clients at the same
project directory. Install the new plugin and start a fresh session; `littrans doctor` must
report the target build. Installation details are in [installation](references/installation.md).

## Upgrade or rebuild into a new directory

For a v7 workspace, run `littrans project upgrade OLD NEW --dry-run`, then
`littrans project upgrade OLD NEW`. NEW must be a separate, nonexistent directory. Upgrade
stages and validates the conversion before publishing NEW; failure leaves OLD unchanged.
It preserves full source, original assets, settings and context. The conversion report is
`derived/paragraph-upgrade.json`; historical units, translations, approvals, overrides and
task evidence are retained under `history/paragraph-upgrade-v7`. Active translations remain
drafts with their original bindings. Confirm paragraph structure, separate body and label
translations, then resubmit through fresh batches. Old heading/list kinds do not establish
new outline levels or label types; only explicit outline evidence restores a heading level.

Use `littrans project rebuild OLD NEW` for older unsupported formats or to deliberately
restart extraction without retaining translations. The destination must not exist, and the
source must match its recorded SHA-256. Rebuild reuses source and context material; historical task
results, source receipts, translations and approvals do not become evidence in the new project.
Review copied brief, style, glossary and structure guidance before using them. Historical model
answers must not enter blind review tasks. Keep `OLD` unchanged for recovery.

A v8 `OLD` keeps its validated `settings.yaml` (agents, translation and verification policy,
external reviewers) and the executable bindings of reviewers it defines. Add `--settings preset`
(optionally `--preset NAME`) to start from a preset instead. Older formats always start from a
preset. Read the returned `rebuild.configuration` report: `preserved`, `reset`, `local_migrated`,
`not_migrated` and `next_actions`. It is also saved in `derived/rebuild-provenance.json`.
`rebuild.unvalidated` lists copied `context/` and `glossary/` files that LitTrans neither
validates nor maintains, such as project-defined manifests or notes. They are copied unchanged;
whatever they record about the old project's extraction, packets or approvals may no longer
hold, so review, move or delete them before agents navigate by them.

## Configure the new project

Run `littrans config validate NEW --host HOST` and `littrans config show NEW --effective --host HOST`.
Policies are saved in `settings.yaml`; machine paths are in ignored `settings.local.yaml`.
The manifest identifies the source; `derived/project-state.json` stores workflow state.
Runtime operations do not inherit updated plugin presets.

Use `config set` for individual changes or `config apply` for a complete candidate with related
reviewer edits. Preview with `--dry-run`; use the current digest with `--expect` for concurrent
imports. Use `context apply` for shared content. Importing content does not approve it.
To move the source, update `source_path` with `config set NEW source_path PATH --local`;
the replacement must have the same hash. A different source document requires a new project.

Review resolved models, audit lenses, wave limits and external reviewer definitions. Local
executable bindings do not override project policy. Explicit unsupported host settings must be
cleared before dispatch. For OpenCode native agents, run `project agents NEW --host opencode
--check`, then `--write`; reconcile user-edited files and reload the host.

## Establish current evidence

Probe and extract source, create independent source-review tasks, receive their results and
require `source verify`. Inspect `source render` before batching and translation. Complete QA,
three independent audit lenses and configured external review/rechecks. Original assets remain
preserved; configured transcription and visual-evidence requirements can block final delivery.
Policy snapshots bind running tasks, so stale results must not be relabeled as current.

After configuration changes, follow the CLI's impact report and gate diagnostics. Re-run the
listed verification or reviews; no configuration command automatically rebatches, deletes
historical results or starts paid external calls. Confirm `config validate`, workflow status,
record tracking and rendered output before delivery.

## Recover

If upgrading or rebuilding fails, keep the old project and retry in a fresh destination after correcting
the reported source or context problem. To resume old work, use `OLD` with its recorded plugin
build in a fresh session. Do not copy new approvals back into it. Restore a complete backup
when rolling back a modified project; preserve both records until recovery is verified.

Version history is maintained in [CHANGELOG](https://github.com/hwayy/littrans/blob/dev/0.9/CHANGELOG.md).
