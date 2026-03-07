# Requirements Traceability: Queue Publish PR Title and Description System

| DOC-REQ ID | Source Reference | Mapped FR(s) | Planned Implementation Surfaces | Validation Strategy |
|---|---|---|---|---|
| DOC-REQ-001 | docs/TaskQueueSystem.md §4.3 | FR-001, FR-002, FR-003 | `moonmind/agents/codex_worker/worker.py` | Unit tests verify commit/title/body overrides are used verbatim when non-empty. |
| DOC-REQ-002 | docs/TaskQueueSystem.md §6.3 | FR-005 | `moonmind/agents/codex_worker/worker.py` | Unit tests verify publish-stage PR arguments derive from publish context and branch semantics. |
| DOC-REQ-003 | docs/TaskQueueSystem.md §6.4 (item 1) | FR-001 | `moonmind/agents/codex_worker/worker.py` | Unit tests verify commit message fallback generation when override is missing. |
| DOC-REQ-004 | docs/TaskQueueSystem.md §6.4 (item 2) | FR-002 | `moonmind/agents/codex_worker/worker.py` | Unit tests verify title fallback order: step title -> instruction sentence/line -> fallback. |
| DOC-REQ-005 | docs/TaskQueueSystem.md §6.4 (item 2) | FR-002 | `moonmind/agents/codex_worker/worker.py` | Unit tests verify derived title avoids full UUID and remains concise. |
| DOC-REQ-006 | docs/TaskQueueSystem.md §6.4 (item 3) | FR-003 | `moonmind/agents/codex_worker/worker.py` | Unit tests verify default body generation when `publish.prBody` is omitted. |
| DOC-REQ-007 | docs/TaskQueueSystem.md §6.4 metadata footer requirements | FR-003, FR-004 | `moonmind/agents/codex_worker/worker.py` | Unit tests verify metadata markers/keys and full UUID inclusion in body without token-like fields. |
| DOC-REQ-008 | docs/TaskQueueSystem.md §6.3 | FR-005 | `moonmind/agents/codex_worker/worker.py` | Unit tests verify `prBaseBranch` override precedence and correct head branch value in generated metadata/PR args. |
