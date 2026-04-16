# Feature Specification: Claude Checkpoints Rewind

**Feature Branch**: `187-claude-checkpoints-rewind`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-346 from MM board
Summary: MoonSpec STORY-005: Expose Claude checkpoints and rewind lineage
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-346 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-346: MoonSpec STORY-005: Expose Claude checkpoints and rewind lineage

User Story
As a user recovering from an unwanted change, I need Claude checkpoints, restore modes, summarize-from-here, and rewind lineage exposed through the session plane without replacing git history.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 14. Checkpointing and rewind
- 17.5 Checkpoint APIs
- 18.5 Work events
- 19.2 Core stores
- 24. Open questions

Coverage IDs
- DESIGN-REQ-016
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-029
- DESIGN-REQ-030
- DESIGN-REQ-028

Story Metadata
- Story ID: STORY-005
- Short name: claude-checkpoints-rewind
- Dependency mode: none
- Story dependencies from breakdown: STORY-001, STORY-004

Acceptance Criteria
- User prompts and tracked file edits create checkpoint records according to capture rules.
- Bash side effects do not create code-state checkpoints by default.
- Manual external edits are represented as best-effort only.
- All four rewind operations are exposed by the checkpoint API contract.
- Rewind preserves pre-rewind event log, keeps old checkpoints addressable until retention expiry or GC, and records rewound_from_checkpoint_id.
- Checkpoint payloads remain runtime-local by default with only metadata and storage references in the plane.

Requirements
- Treat checkpointing as a session-plane operation.
- Expose checkpoint list, restore, and summarize APIs.
- Preserve provenance through rewinds.
- Keep checkpoint payload locality explicit.

Independent Test
Drive user-prompt, file-edit, bash-side-effect, and manual-edit cases through the checkpoint service and assert captures, restorable modes, active cursor updates, event log preservation, and payload pointer behavior.

Out of Scope
- Using checkpoints as a replacement for git.
- Central storage of checkpoint payloads by default.
- Provider-specific checkpoint internals.

Source Design Coverage
- DESIGN-REQ-016: Owns exposing Claude checkpointing and rewind as session-plane operations rather than UI-only runtime behavior.
- DESIGN-REQ-020: Owns checkpoint and rewind work events so captures, restores, summaries, and lineage changes remain visible in the shared event stream.
- DESIGN-REQ-021: Owns checkpoint metadata and restore references in the central plane while keeping large checkpoint payloads outside the plane by default.
- DESIGN-REQ-029: Owns source-control safety expectations for rewinds, including preserving pre-rewind event history and keeping prior checkpoints addressable until retention expiry or garbage collection.
- DESIGN-REQ-030: Owns the unresolved checkpoint payload locality question, with runtime-local storage as the default unless explicit policy later permits export.
- DESIGN-REQ-028: Owns rollout alignment for making checkpoint index, rewind APIs, and summary artifact pointers part of the Claude session-plane phase.

Needs Clarification
- Should checkpoint payloads ever leave the runtime host?

Notes
This story depends on the Claude managed-session core schema and context snapshot stories so checkpoint records, active cursors, restore references, summary artifacts, and rewind lineage can attach to shared session-plane records without replacing git history or centralizing checkpoint payloads by default.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Claude Checkpoints And Rewind

**Summary**: As a user recovering from an unwanted change, I want Claude checkpoints, restore modes, summarize-from-here, and rewind lineage exposed through the session plane so that recovery is visible and provenance-preserving without replacing git history.

**Goal**: Users and operators can inspect checkpoint metadata, understand what triggered each checkpoint, invoke one of the documented rewind modes, and confirm that rewind lineage preserves pre-rewind history while checkpoint payloads stay runtime-local by default.

**Independent Test**: Drive user-prompt, tracked file-edit, bash-side-effect, and manual-edit checkpoint cases through the checkpoint boundary; list checkpoints; restore code, conversation, both, and summarize-from-here; then assert capture rules, active cursor updates, rewind lineage, event log preservation, and payload pointer behavior.

**Acceptance Scenarios**:

1. **Given** a user prompt enters a Claude session, **when** checkpoint capture is evaluated, **then** a checkpoint metadata record can be created with trigger `user_prompt`.
2. **Given** a tracked file edit occurs during a Claude turn, **when** checkpoint capture is evaluated, **then** a code-state checkpoint metadata record can be created with a runtime-local payload reference.
3. **Given** a bash side effect occurs, **when** checkpoint capture is evaluated, **then** no code-state checkpoint is created by default and the skipped capture is explainable.
4. **Given** a manual external edit is detected, **when** checkpoint capture is evaluated, **then** the checkpoint is marked best-effort rather than authoritative.
5. **Given** an operator lists checkpoints for a session, **when** the checkpoint index is returned, **then** old checkpoints remain addressable until retention expiry or garbage collection.
6. **Given** a rewind request uses any documented mode, **when** the request is validated, **then** restore code and conversation, restore conversation only, restore code only, and summarize-from-here are accepted and unknown modes are rejected.
7. **Given** a rewind completes, **when** session history is inspected, **then** the pre-rewind event log remains intact, the active checkpoint cursor changes, and `rewound_from_checkpoint_id` records lineage.
8. **Given** checkpoint metadata is centrally visible, **when** records are serialized, **then** checkpoint payloads are represented by storage references and compact metadata only by default.

### Edge Cases

- Bash side effects may be recorded as skipped capture evidence, but must not silently become code-state checkpoints.
- Manual external edits are best-effort because the runtime may not have authoritative before/after state.
- Summarize-from-here changes conversation context without claiming disk state was restored.
- Rewind requests for expired or garbage-collected checkpoints must fail validation or report non-addressability rather than inventing recovery state.
- Checkpoint metadata must remain bounded and must not embed transcripts, file diffs, or raw checkpoint payloads.

## Assumptions

- MM-346 builds on the canonical Claude session, work item, decision, and context snapshot contracts from MM-342 through MM-345.
- The default answer to the Jira clarification is that checkpoint payloads remain runtime-local unless a future explicit export policy permits otherwise.
- This story defines runtime-validatable contracts and deterministic boundary helpers; provider-specific restore mechanics remain adapter-owned.

## Source Design Requirements

- **DESIGN-REQ-016**: Source `docs/ManagedAgents/ClaudeCodeManagedSessions.md` section 14 requires checkpointing and rewind to be session-plane operations with capture rules for user prompts, file edits, bash side effects, and manual edits. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, and FR-006.
- **DESIGN-REQ-020**: Source section 18.5 requires normalized `work.checkpoint.created`, `work.rewind.started`, and `work.rewind.completed` events for checkpoint and rewind visibility. Scope: in scope. Maps to FR-011 and FR-012.
- **DESIGN-REQ-021**: Source section 19.2 requires CheckpointIndex metadata and restore references while large checkpoint payloads stay outside the central plane by default. Scope: in scope. Maps to FR-007, FR-008, FR-009, and FR-014.
- **DESIGN-REQ-029**: Source section 14.4 requires rewind provenance preservation, including active cursor changes, `rewound_from_checkpoint_id`, and old checkpoint addressability until expiry or garbage collection. Scope: in scope. Maps to FR-009, FR-010, FR-013, and FR-015.
- **DESIGN-REQ-030**: Source section 24 leaves checkpoint payload export unresolved; this story uses runtime-local checkpoint payload references as the default. Scope: in scope. Maps to FR-014 and FR-016.
- **DESIGN-REQ-028**: Source sections 17.5 and 23.3 require checkpoint list, restore, summarize-from-here, checkpoint index, rewind APIs, and summary artifact pointers as part of Claude session-plane rollout. Scope: in scope. Maps to FR-006, FR-010, FR-012, FR-013, and FR-017.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a Claude Checkpoint record associated with a canonical Claude managed session and optional turn.
- **FR-002**: Checkpoint records MUST include stable checkpoint identifier, session identifier, optional turn identifier, trigger, capture mode, checkpoint status, active-cursor marker, retention state, storage reference, and bounded metadata.
- **FR-003**: Checkpoint trigger MUST accept only documented capture sources: user prompt, tracked file edit, bash side effect, and external manual edit.
- **FR-004**: User prompt and tracked file edit triggers MUST support checkpoint capture.
- **FR-005**: Bash side-effect triggers MUST NOT create code-state checkpoints by default.
- **FR-006**: External manual edit triggers MUST be represented as best-effort only.
- **FR-007**: CheckpointIndex output MUST expose metadata and storage references only by default.
- **FR-008**: Checkpoint metadata MUST reject large payload-like fields that would embed transcripts, file diffs, or raw checkpoint payloads.
- **FR-009**: Checkpoints MUST remain addressable through metadata until their retention state is expired or garbage-collected.
- **FR-010**: System MUST expose a rewind request contract that accepts only restore code and conversation, restore conversation only, restore code only, and summarize-from-here modes.
- **FR-011**: Checkpoint capture MUST be able to emit a normalized checkpoint-created work item.
- **FR-012**: Rewind execution MUST be able to emit normalized rewind-started and rewind-completed work items or events.
- **FR-013**: A completed rewind MUST record the source checkpoint, the previous active checkpoint, the new active checkpoint cursor, and `rewound_from_checkpoint_id` lineage.
- **FR-014**: Checkpoint payloads MUST default to runtime-local storage references and MUST NOT be represented as central payload bodies.
- **FR-015**: Rewind output MUST preserve pre-rewind event log references rather than replacing or deleting history.
- **FR-016**: Summary-from-here output MUST be represented as a summary artifact reference without claiming code state was restored.
- **FR-017**: Unsupported checkpoint triggers, capture modes, rewind modes, event names, or retention states MUST fail validation.

### Key Entities

- **Claude Checkpoint**: Bounded metadata record for a recoverable point in a Claude session.
- **CheckpointIndex**: Operator-visible list of checkpoint metadata and runtime-local restore references.
- **Claude Rewind Request**: Validated request to restore code and conversation, restore conversation only, restore code only, or summarize from a checkpoint.
- **Claude Rewind Result**: Provenance-preserving output that records active cursor changes, source checkpoint, lineage, summary references, and event log preservation.
- **Checkpoint Work Item**: Normalized work evidence for checkpoint capture and rewind lifecycle events.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests cover every documented checkpoint trigger and prove bash side effects do not create code-state checkpoints by default.
- **SC-002**: Unit tests cover every documented rewind mode and prove unknown modes fail validation.
- **SC-003**: Unit tests prove checkpoint metadata and rewind output reject large payload bodies while accepting compact runtime-local references.
- **SC-004**: Boundary tests prove a representative checkpoint and rewind flow preserves pre-rewind event log references, updates the active cursor, records `rewound_from_checkpoint_id`, and emits normalized work evidence.
- **SC-005**: Validation proves summarize-from-here produces a summary artifact reference without claiming code state restoration.
- **SC-006**: Source design coverage for DESIGN-REQ-016, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-028, DESIGN-REQ-029, and DESIGN-REQ-030 maps to passing verification evidence.
