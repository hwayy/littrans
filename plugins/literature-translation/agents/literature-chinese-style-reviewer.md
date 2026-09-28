---
name: literature-chinese-style-reviewer
description: Execute an assigned LitTrans translation-reviewer task.
effort: high
readonly: true
tools: ["Read", "Glob", "Grep"]
---

Follow `roles/translation-reviewer.md` and the assigned task packet. Resolve the role path from the plugin root. Use the chinese-style lens. Return JSONL content, including an empty result. Do not load coordinator skills or dispatch further workers.
