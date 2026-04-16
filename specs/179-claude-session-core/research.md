# Research: Claude Session Core

## Schema Home

Decision: Define Claude core managed-session contracts in `moonmind.schemas.managed_session_models` and export them through `moonmind.schemas.__init__`.

Rationale: The existing Codex managed-session contracts already live in this schema module and use shared validation helpers, camelCase aliases, and strict Pydantic v2 models. Keeping Claude core records adjacent preserves the shared Managed Session Plane vocabulary without creating a parallel schema surface.

Alternatives considered: A new `claude_managed_session_models.py` module was rejected for STORY-001 because it would split the shared core model too early. Workflow-local models were rejected because schema ownership belongs at the runtime boundary, not inside workflow implementation.

## Runtime Axes

Decision: Model `execution_owner`, `surface_kind`, and `projection_mode` as closed values on session and surface records, with validation helpers for Remote Control and cloud handoff shapes.

Rationale: The source design states that UI surface does not imply execution semantics. Closed values allow tests to prove web/mobile Remote Control projections do not mutate local execution owner while cloud handoff creates a separate cloud-owned session.

Alternatives considered: Inferring execution owner from `surface_kind` was rejected because web/mobile can mean either projection or cloud execution. Free-form strings were rejected because lifecycle and surface distinctions need reviewable contract validation.

## Lineage Representation

Decision: Represent handoff, fork, parent, and grouping lineage as optional nonblank identifier fields on the canonical session record and validate cloud handoff through a constructor-style class method.

Rationale: MM-342 requires cloud handoff to become a new session with lineage to the source session rather than mutating the source. Keeping the lineage fields on the session record supports later stories without implementing full persistence or workflow behavior now.

Alternatives considered: A separate lineage graph module was rejected as too broad for STORY-001. Mutating the source session owner was rejected by the source design.

## Alias Policy

Decision: Use camelCase aliases for Claude contract wire fields but do not define `threadId` or `childThread` aliases anywhere in Claude models.

Rationale: The Jira brief explicitly requires `session_id` naming and forbids Codex `thread_id` and `child_thread` aliases. Pydantic `extra="forbid"` makes payloads with those keys fail fast.

Alternatives considered: Accepting legacy Codex aliases and translating them to `session_id` was rejected by the compatibility policy and the MM-342 source requirements.

## Test Strategy

Decision: Add focused unit tests for contract validation and an integration-style schema boundary test for the documented session shapes.

Rationale: Unit tests provide fast red/green evidence for strict fields, lifecycle values, aliases, Remote Control, and handoff behavior. A separate integration-marked boundary test exercises the story's end-to-end data-contract scenario without requiring external credentials.

Alternatives considered: Full Temporal workflow tests were rejected for this story because MM-342 is scoped to the core schema and data-contract boundary, not workflow execution or persistence.
