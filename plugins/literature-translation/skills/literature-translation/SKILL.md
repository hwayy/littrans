---
name: literature-translation
description: Coordinate a complete or resumed LitTrans literature translation project. Route source, context and translation work to their coordinator skills.
---

# Literature Translation

Coordinate the user's requested scope using durable project state. You do not write target prose or perform independent reviews in the coordinating context.

1. Read [runtime](../../references/runtime.md), run doctor and inspect project status. Read [configuration](../../references/configuration.md), run `config validate PROJECT --host HOST` and `config show PROJECT --effective --host HOST` on initialization or resume. Preserve configured external review requirements and user scope. Apply accepted setting changes through config CLI; follow its affected-artifact report before resuming tasks.
2. Use [Source Processor](../source-processor/SKILL.md) for source extraction, correction or optional asset transcription; [Context Manager](../context-manager/SKILL.md) for shared rules and terminology; [Translation Coordinator](../translation-coordinator/SKILL.md) for translation and delivery. Load only the skill needed for the current stage.
3. Read [task protocol](../../references/task-protocol.md) before delegation and the selected [host adapter](../../references/host-runtimes.md). Source fidelity, translation approval and asset reliability are independent facts.
4. Resume from persisted status and successful unimported results. Dispatch only missing in-scope work. Record the next scope and pending assets separately from delivered translation.
5. Return artifact paths, actual approval level, unresolved work and verified execution facts. Human approval requires an explicit user decision. Rendering is not publication.

Each coordinator can also run directly. Use domain CLI gates rather than deciding approval from prose reports.
