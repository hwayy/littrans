# Changelog

All notable distributed changes to LitTrans are recorded here. Versions follow semantic
versioning and correspond to Git tags named `v<version>`.

## [0.2.2] - 2026-07-30

### Fixed

- Merged list items that continue across PDF pages into one logical item in Markdown and
  bilingual HTML renders.
- Recorded external-review timeouts as failed review runs instead of losing the attempt history.
- Preserved sidebar context and joined sidebar body fragments across page boundaries.
- Normalized translated Chinese figure captions consistently during QA, external review, and
  rendering.

## [0.2.1] - 2026-07-29

### Added

- Established the private Git-backed `littrans` marketplace as the stable distribution source.
- Added controlled batch rendering and external review gates.
- Added structured support for tables, code, callouts, sidebars, reader notes, and cross-page
  continuations.
- Added repository-local validation and a documented manual release workflow.

### Changed

- Aligned the plugin manifest, Python package, and runtime package version at `0.2.1`.
- Separated the stable release identity from local Codex cachebuster versions.

### Fixed

- Preserved translation and rendering semantics across structured regions and page boundaries.
- Rejected placeholder external-review evidence and recovered source units nested in extracted
  tables.
