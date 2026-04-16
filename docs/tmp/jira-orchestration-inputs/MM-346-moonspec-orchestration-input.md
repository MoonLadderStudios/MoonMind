# MM-346 MoonSpec Orchestration Input

## Source

- Jira issue: MM-346
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MoonSpec STORY-005: Expose Claude checkpoints and rewind lineage
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

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
