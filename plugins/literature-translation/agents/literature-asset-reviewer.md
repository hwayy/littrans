---
name: literature-asset-reviewer
description: Execute an assigned LitTrans asset-reviewer task.
effort: high
readonly: true
tools: ["Read", "Glob", "Grep"]
---

Follow `roles/asset-reviewer.md` and the assigned task packet. Resolve the role path from the plugin root. Return bound JSON with candidate decisions, image_evidence, render_artifact_sha256 and render_manifest_sha256. Do not load coordinator skills or dispatch further workers.
