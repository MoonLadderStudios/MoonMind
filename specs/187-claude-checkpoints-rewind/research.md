# Research: Claude Checkpoints Rewind

## Runtime Boundary

Decision: Implement MM-346 as Pydantic schema contracts and deterministic helper functions in `moonmind/schemas/managed_session_models.py`.

Rationale: Existing Claude stories for session core, policy envelopes, decisions, hooks, and context snapshots use this module as the compact managed-session schema boundary. Checkpoints and rewinds need the same importable, Temporal-safe validation behavior before workflow or adapter persistence can consume them.

Alternatives considered: A separate `claude_checkpoint_models.py` module was considered, but it would split closely related Claude session-plane records and require extra exports before there is enough complexity to justify another module.

## Payload Boundaries

Decision: Store checkpoint metadata, event-log references, summary references, and runtime-local payload references only, and validate extra metadata through the existing compact Temporal mapping helper.

Rationale: The source design and Jira brief require checkpoint payloads to remain runtime-local by default. Reusing compact mapping validation keeps behavior consistent with existing managed-session metadata, context, policy, decision, and hook records.

Alternatives considered: Embedding checkpoint diffs, transcripts, or restore payload snippets was rejected because MM-346 explicitly excludes central checkpoint payload storage and forbids replacing git history with session checkpoints.

## Capture Rules

Decision: Model checkpoint triggers and capture modes as strict literals, with a helper that evaluates documented defaults for user prompts, tracked file edits, bash side effects, and external manual edits.

Rationale: The story requires user prompts and tracked file edits to create checkpoints, bash side effects to avoid code-state checkpoints by default, and manual edits to be best-effort only. A helper makes those defaults deterministic and testable without live Claude execution.

Alternatives considered: Free-form trigger strings plus adapter-specific interpretation were rejected because unsupported values should fail validation under the compatibility policy.

## Rewind Semantics

Decision: Model rewind requests and results with strict modes and explicit lineage fields, including source checkpoint, previous active checkpoint, new active checkpoint, rewound-from checkpoint, preserved event-log reference, and optional summary artifact reference.

Rationale: Rewind changes session truth and must not destroy provenance. Explicit fields make active cursor updates and summarize-from-here behavior auditable at the schema boundary.

Alternatives considered: A generic operation model was rejected because the four documented modes have different code/conversation effects and summarize-from-here needs a summary artifact without code restore.

## Work Evidence

Decision: Extend Claude work-item event validation to accept checkpoint and rewind event names for checkpoint/rewind work items and provide helper-created work evidence.

Rationale: The design requires `work.checkpoint.created`, `work.rewind.started`, and `work.rewind.completed` to be visible in the shared event stream. Reusing `ClaudeManagedWorkItem` keeps event evidence aligned with the existing session-plane contracts.

Alternatives considered: Creating a separate event-only model was rejected because checkpoints and rewinds are already documented as work items.

## Testing Strategy

Decision: Add focused unit tests for trigger/mode validation, capture-rule defaults, payload locality, rewind lineage, summary artifact behavior, and compact metadata limits; add an integration-style schema boundary test for a representative checkpoint and rewind flow.

Rationale: This matches the existing Claude context and decision story pattern and gives workflows/adapters a stable contract before persistence or runtime wiring is added.

Alternatives considered: Full Temporal workflow tests were rejected for this story because no workflow or activity signature changes are required; integration-style schema tests are sufficient for the current boundary.
