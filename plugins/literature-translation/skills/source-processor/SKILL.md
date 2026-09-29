---
name: source-processor
description: Coordinate LitTrans source extraction and review, or optional structured asset transcription and independent verification. Use for source preparation or asset enhancement.
---

# Source Processor

Run `config validate PROJECT --host HOST` before creating source tasks. Source
policy changes require fresh affected source evidence. Apply accepted structure
guidance with `context apply`; this never certifies extraction or replaces visual
review. Use the [configuration](../../references/configuration.md) impact report.

You coordinate source work; assigned workers inspect pages and produce candidates.

Read [source processing](../../references/source-processing.md), [runtime](../../references/runtime.md), and [task protocol](../../references/task-protocol.md).

## Extract

1. Initialize or resume the project. Probe representative pages and dispatch document-scout tasks for structure/layout questions. Validate proposed source rules against originals before applying them. The source processor owns extraction-rule decisions; context management records their versions.
2. Run source extract for the requested pages. Preserve cached layout and recorded overrides on --replace; --redetect and --discard-overrides remain explicit user choices.
3. Create source-review tasks for bounded contiguous ranges. Give original pages and packet evidence, no expected verdict. Import decisions, then dispatch fresh tasks for corrected, blocked or invalidated pages. Apply proposed page_rules centrally; never approve pages under unapplied rules.
4. Require source verify, then source render and inspect the reading checkpoint. Report any unviewed evidence. Extraction ends before batch creation.

## Parse

For verified source, create transcribe tasks scoped by asset IDs or existing batches. Translation can proceed independently; configured transcription requirements must pass before final approval and delivery. Receive candidates, then create distinct asset-audit tasks with originals and actual candidate renders. Retain original-image fallback for pending, uncertain or rejected candidates. Revision requires a fresh packet and independent re-audit.

Follow [asset representation](../../references/asset-representation.md) for evidence and fallback details. Context proposals go to Context Manager; meaningful language in table/figure assets remains translation work.
