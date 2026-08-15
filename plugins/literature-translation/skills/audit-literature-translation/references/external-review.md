# External review

External review is an optional project-specific gate after deterministic QA, all three internal
audit lenses, issue resolution, and machine approval. Reviewer configuration belongs in
`project.yaml`; the plugin supplies no default provider or model.

Set the optional `external_review.domain_expertise` string when a project needs an explicit
subject-matter specialization. The value is included in the isolated review packet and therefore
covered by its recorded SHA-256. When omitted, reviewers infer the required expertise from the
document brief. Provider prompts remain domain-neutral.

Run `review external <project> <batch-id>` for least-used assignment, or add `--reviewer <id>`
to keep a revision with its original reviewer. Add `--dry-run` to inspect the isolated packet,
prompt, and command without invoking a provider. Use `review external-status` to inspect the
current translation fingerprint, actual model evidence, verdict, and open issues.

Set `external_review.assignment_since` to a timezone-aware ISO 8601 timestamp when adding a new
reviewer to an established project and a fresh balancing epoch is required. Historical telemetry
remains intact, while least-used selection counts completed and active calls only from that time.
Concurrent selections reserve their reviewer briefly so separate services can be used in parallel
without choosing the same least-used reviewer in a race.

For `cursor-cli`, use exact Cursor model IDs and omit separate `effort` and `fast` fields. Cursor
first-party quota failures (including Grok, Composer, and Auto) and third-party quota failures
(including Claude) are recorded separately per attempt. The CLI exhausts the reviewer's configured
model chain before selecting a replacement reviewer.

Each call receives only the current source, translation, checklist, style guide, approved terms,
and relevant PDF page images. Prior review issues and translator rationale are excluded. The
CLI runs read-only in a temporary directory. Preserve the normalized result, raw response,
actual-model evidence, CLI version, prompt version, and translation fingerprint; remove temporary
provider logs after extracting model evidence.

An inconclusive verdict, an unverified actual model, a blocker/major issue, or confidence below
the configured threshold requires a second opinion from a different reviewer. Merge agreement.
Leave conflicts inconclusive for high-level adjudication with a recorded evidence-based reason.
Never convert external acceptance into human approval.
