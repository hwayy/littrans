# Context management
The document brief holds stable document purpose and audience. The style guide holds
project-wide translation rules. Approved terminology is gated; reference entries express
proper-name and sense conventions; candidates hold proposals. Preserve the existing glossary
matching and scope semantics. Use context glossary lookup to inspect what a page/batch sees.

Source structure is managed here as versioned context, but extraction-rule decisions belong
to Source Processor. A terminology task cannot change source rules. Read document-structure.md
only when proposing or interpreting those rules.

Run context check for missing files and glossary diagnostics. Before applying an accepted
change, run context snapshot PROJECT PATH (a new path within PROJECT). Run context impact
PROJECT SNAPSHOT afterward. This is a conservative explanation, not a replacement for
source verify or workflow status. Global brief/style edits invalidate translation audit
coverage; glossary edits use existing per-unit dependencies; page rules use their source scope.

Workers submit evidence-backed proposals in task results; one coordinator merges them into
complete candidate resources through `context apply`, preserving user-owned content. Record accepted/rejected decisions
and unresolved conflicts. Never promote a candidate solely because it was suggested.

Use [configuration](configuration.md#context-resources) for transactional imports, conflict
detection and terminology promotion. Overlapping approved scopes cannot prescribe conflicting
translations. Imports validate content; source review and approval still require their own gates.
