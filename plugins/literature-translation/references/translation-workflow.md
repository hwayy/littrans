# Translation Workflow

## Batches

Batches are cut when translation starts, at the user's request, never as the last step of source
preparation: the user may still correct verified source, and a batch freezes the units it was cut
from. `translation batch create PROJECT --pages PAGES` cuts verified pages into batches of about 900 source words
and a soft limit of 60 assets at logical boundaries; `--unit-ids` selects an explicit complete
unit set and `--untranslated-only` limits the editable scope to units without a current
translation. Both record `frozen_scope: true`. Refresh keeps their selected IDs rather than
filling interior gaps; removed IDs or newly cut logical groups require an explicit new selection.
Legacy/range manifests retain interval refresh behavior.

Untranslated-only manifests retain complete selected parent groups. Their `read_only_unit_ids`
are context, excluded from `translatable_unit_ids` and submission; refresh preserves that boundary
and reopens read-only units whose translations are missing or stale. Packet source and context
identify both scopes. Batch output schemas come from the submission model and include
`image_evidence` and `asset_translations`; refresh existing batches to update their emitted schema.

## Translation records

Translation records retain their unit's `source_hash`, references and an `image_evidence` map of
inspected original image path to SHA-256 from `original-images.json`. Supplementary translations
of image-contained language live in `asset_translations`, keyed by `asset_id`, using
`target_text`, `target_table` or `figure_labels`. Declare `language_present: false` with
explanatory `notes` only for an asset with no translatable natural language. Asset candidate
content never replaces the source reference ID. Do not put raw LaTeX in translated image
companions; preserve references and submit mathematical candidates through the independently
reviewed asset channel. Companion text, table cells and label mappings cannot contain asset
placeholders or live footnote calls; keep calls in the main translation and escape literal
notation or use code literals.

A resubmission that is semantically identical to the current record keeps its revision. When
only the `source_hash` binding changed, the record is rebound as `revised` and its audits are
invalidated. When only `image_evidence` changed, the receipt is updated in place: the translated
content is untouched, audits stay valid, and QA (which binds the receipt) simply becomes stale.

## Deterministic QA

`translation qa` checks the batch's editable units and its whole dependency closure. Its cached result is
bound to the translation fingerprint and a QA context that includes approved terminology, asset
semantic uncertainty, recorded translation uncertainties, dependency presence, dependency source
and translation hashes, each scoped translation's viewing receipt and the current required-image
hashes. Any change there makes the cached report stale; QA context versions change when a rule
changes, and existing reports must then rerun.

Rules worth knowing when reading a report:

- A source unit with prose outside its `{{asset:ID}}` placeholders needs target text; a target
  that consists only of placeholders is an `empty-translation`, while a source that is only an
  asset reference (a whole figure or table block) needs none.
- Real footnote calls are counted with multiplicity in prose and table cells; literal
  code/math/escaped syntax (including tilde and backtick fences and escaped dollar delimiters) is
  excluded. Live calls inside image-language companions are rejected.
- Dollar inline math requires non-whitespace inner boundaries and a closing dollar not followed
  by a digit and cannot span intervening unescaped dollars, so ordinary currency amounts retain
  their real footnote calls; explicit double-dollar display math remains protected. Code fences
  start on a line with at most three leading spaces and close only on an otherwise blank line
  with a matching or longer delimiter; unclosed blocks extend to end of input.
- Translatable dependency units, including read-only context, need current translations with
  current receipts (`missing-translation`, `source-hash-mismatch`, `asset-image-receipt-missing`).
- Asset uncertainty across the dependency scope, including non-translatable formulas, blocks
  approval (`asset-semantic-uncertainty`); damaged images remain routable to source repair.
- Structured target tables render once, including when `target_text` is explicitly empty.

### Terminology

Three glossary files share one entry schema — a `terms` list whose entries carry `source` and
optionally `aliases` (other attested source forms), `match`, `scope` (`document`, `page:N` or a
parent unit ID), `status`, `target`, `forbidden` and any project-defined key — and differ only
in effect:

| File | Effect | Reaches packets | In the audit hash |
| --- | --- | --- | --- |
| `approved.yaml`, `status` absent or `approved` | hard per-unit QA gate | entries matching the packet's units | those entries |
| `approved.yaml`, `status: reference-only` | binding, never gated | same filter | same |
| `reference.yaml` (`status` defaults to `reference-only`) | binding, never gated; grouped by `kind` | same filter | same |
| `status: proposed` in either file | inert | no | no |
| `candidates.yaml` | none: the record of promotion decisions | no | no |

- Reference entries are the channel for data that grows with the chapters but must not gate:
  proper names kept in source form, one-word-two-senses registers, chapter usage notes.
  `kind` (default `reference`, e.g. `proper-name`, `sense`) groups them in packets; every other
  key (`targets`, `rule`, `note`, `first_seen`, ...) is shown verbatim. `status: approved` inside
  `reference.yaml` is refused: that file never gates. Because reference entries are filtered per
  unit like approved terms, appending a chapter's names changes only the audit context of the
  batches that mention them, and correcting an entry resets only the batches it matches — the
  two context files, by contrast, are hashed whole.
- Packets show the gated entries under `# Relevant approved terminology` (`approved_terms`)
  and, only when at least one matches, the reference entries under
  `# Relevant reference terminology (not gated)` (`reference_terms`, one list per `kind`); the
  batch `context.md` and external-review packets carry the same two sections. A project without
  reference entries keeps the audit context it had before the channel existed.
- `candidates.yaml` entries without `status` (or `status: proposed`) are undecided and are
  listed in the finalize unresolved report; entries whose status records a decision
  (`reference-only`, `rejected`, ...) are only counted there.
- `context glossary lookup PROJECT --batch-id ID | --pages SPEC | --unit-ids IDS | --text FILE`
  lists the approved and reference entries a selection receives, with the packet's own scope
  and folding rules (`--kind` narrows the reference groups, `--jsonl` emits one entry per
  line); `context glossary check PROJECT` loads every file and reports entries matching no prepared
  unit. Both are read-only.
- The unit's source representations (text, Markdown, table cells, figure labels) minus quoted
  titles are folded before matching, and so is `source`. A quoted title is a double-quoted phrase
  of at least two words whose words are capitalised except `a an and as at but by for from in
  into nor of on or over the to via vs with` (`“Binding Theory”`); every quotation of a
  `bibliography` unit counts as one. A quoted term (`“strict mode”`) is matched. Folding covers
  precomposed, combining and TeX spacing accents (`Hölder` ≡ `H¨older`, `Lévy` ≡ `L´evy`), ligatures, curly quotes and apostrophes
  (`Chebyshev's` ≡ `Chebyshev’s`), dash variants, whitespace runs and case. QA and the
  `relevant_terms` packet injection share this folding, so a term shown to the translator is the
  term QA enforces.
- `match` selects how `source` is located in the folded text: `substring` (default; `measure`
  also hits `measurable`), `word` (no letter/digit on either side), or `regex` (a Python pattern
  searched case-insensitively in the folded text, e.g. `\bpartition\b(?! function)`). The
  literal characters of a regex are folded like a substring source (`Hölder`, `Chebyshev’s`
  and `H¨older`, `Chebyshev's` are the same pattern) while escape sequences such as `\b`, `\B`
  or `\s` are kept verbatim. Invalid modes or patterns fail loading.
- When `source` occurs in a unit, `target` must appear in that unit's translation
  (`approved-term-missing`). A `source` that matches no prepared unit at all is reported once per
  QA run as the warning `approved-term-never-matched`; fix the spelling or narrow the entry.
- `forbidden` wording is checked in **every** translated unit and asset companion, whether or
  not that unit contains `source`. List only wording that is wrong in every context (a wrong
  transliteration), never a rendering that is merely wrong for this term (`mean` → 意味着).
- Editing a gated entry changes the QA context of every batch and the audit context of batches
  whose relevant terms change (existing audits become `audit_stale`); finish the gate baseline
  before `source extract`, or at the latest before the audit wave. Drafts belong in
  `glossary/candidates.yaml`, which has no effect until an entry is moved into `approved.yaml`
  or `reference.yaml`.

## Audit coverage

Audit coverage is bound to the brief, the style guide, the relevant approved and reference
terms and the dependency-closure units of each run (`audit_context_text` is exactly those four
parts; `project.yaml` is not among them). `audit_coverage` (and `workflow status`,
`translation review status`) reports why a run no longer counts: `context-changed`, `dependency-changed`,
`unit-changed`, `invalidated`, `closure-incomplete` or `context-units-removed`. A
`context-changed` run recorded after this version also lists `context_changes` — which part
(`document-brief`, `style-guide`, `approved-terms`, `reference-terms`) changed and its line
count before and after — so the cost of growing a whole-file context is visible where it is
paid. Finish context edits before the audit wave.

`translation review import-set` rewrites reviewer issue ids to canonical `audit-<hash>` ids and keeps the
reviewer id as `source_issue_id`; `translation review resolve` accepts either. Review issues against
read-only context route to an editable owning batch; issues against non-translatable source units
dispatch source review.

## Workflow coordination

`workflow packet` stages are `source-review`, `translate`, `revise`, `audit`, `transcribe` and
`asset-audit`. Use the emitted schemas as the authority for exact fields. All model work reads
original image evidence; a translate packet does not consume unverified transcription candidates.
New workflow packets record host/model/reasoning_effort in their manifest and identity, resolved from
the stage's own role in `agent_models.<host>`; each role carries its own model and effort. Either may be
absent, which dispatches on the host's default and is reported as an advisory, never refused. The
roles are `translate` (also `revise`), `transcribe`, `audit`, `asset-audit` and `source-review`; each
stage runs in a fresh subagent of its agent (see host-runtimes.md). Source-review material carries
its dispatch beside the packet, never inside it: `workflow packet --stage source-review`,
`source review-packets --host HOST` and the `source-review` task of `workflow next` report the
role's model and effort, while the source packet identity stays bound to content alone. Legacy
manifests remain readable with absent policy fields; create fresh packets for an explicitly bound
dispatch policy. `workflow status --host` uses the same override as next/packet, and
`project models PROJECT --host HOST` reports the resolved policy with its advisories.

Workflow coordination rechecks source authority across each selected batch's page-evidence
closure before reusing QA. Failed authority returns `source-review`, suppresses optional asset
tasks and permits `workflow packet --stage source-review` to rebuild review materials; the returned
materials include open `workflow_issues`, which must be addressed and resolved in their original
batches. Repair missing/changed source artifacts before independent review; cached translation
approval cannot override this gate.

A `revise` packet (`workflow packet --stage revise`) carries the translate packet files plus the
batch's current translation records, its open translation review issues and `<batch>.revise.md`. Revision
packets retain their originating batch IDs for coordinator issue resolution. A dependency-only
QA failure dispatches its editable owning batch as prerequisite work, even outside resume bounds;
`requested_batch_ids` retains the original selected wave in `workflow next`. Uncertain fallback
assets dispatch transcription before QA regardless of whether their owning source unit is
translatable, and workflow dispatches the independent asset audit before QA.

A batch set for a packet or a render may span batch series when units do not overlap and source
order holds; within one series the batches must stay consecutive.

`workflow next` and `workflow status` check batch coverage over the coordinated scope: the
pages of the coordinated batches (the bounded or series range for `next`, the requested IDs
for `status`) and the reading-order span between them. A renderable unit no manifest covers
inside that scope stops the wave (`unbatched_units=…`: refresh or create batches); pages
outside it that hold such units — a chapter extracted but not yet batched — are reported as
`unbatched_pages` and do not block coordinating the batched chapters. Formal rendering keeps
its own check over the pages it renders.
