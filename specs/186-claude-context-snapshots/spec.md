# Feature Specification: Claude Context Snapshots

**Feature Branch**: `186-claude-context-snapshots`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-345 from MM board
Summary: MoonSpec STORY-004: Index Claude context, compaction, and memory boundaries
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-345 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-345: MoonSpec STORY-004: Index Claude context, compaction, and memory boundaries

User Story
As an operator investigating session quality, I need typed ContextSnapshot metadata, reload policy, and compaction epochs so I can see which Claude context entered a session and what survives compaction.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 2.5 Make context lifecycle inspectable
- 13. Context and memory model
- 19.2 Core stores

Coverage IDs
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-015
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-028

Story Metadata
- Story ID: STORY-004
- Short name: claude-context-snapshots
- Dependency mode: none
- Story dependencies from breakdown: STORY-001, STORY-002

Acceptance Criteria
- Startup context sources are indexed with correct segment kinds and loaded_at = startup.
- On-demand context sources are indexed only when their trigger occurs.
- Every context segment carries a reinjection_policy matching the design.
- Compaction creates a new ContextSnapshot epoch rather than mutating the old one.
- Compaction emits an explicit WorkItem and normalized event.
- Memory artifacts are represented as guidance and never become enforcement sources.

Requirements
- Make Claude context composition inspectable.
- Attach reload and reinjection semantics to context metadata.
- Preserve policy-critical context through compaction without treating memory as policy.
- Store context metadata and pointers centrally while keeping large payloads out of the plane by default.

Independent Test
Bootstrap a Claude session with managed/project/local CLAUDE files, MCP manifests, skills, hooks, file reads, nested rules, and invoked skill bodies; compact it; then assert old snapshots remain immutable and a new epoch reloads only allowed context.

Out of Scope
- Full transcript central storage.
- Hard enforcement of CLAUDE.md guidance.
- Checkpoint restore behavior.

Source Design Coverage
- DESIGN-REQ-013: Owns typed ContextSnapshot metadata for Claude startup and runtime context composition.
- DESIGN-REQ-014: Owns context segment source kinds, source references, load timing, and reload or reinjection semantics.
- DESIGN-REQ-015: Owns compaction epochs that create new immutable ContextSnapshot records instead of mutating prior snapshots.
- DESIGN-REQ-020: Owns ContextIndex storage of ContextSnapshot metadata and segment pointers while keeping large payloads outside the central plane by default.
- DESIGN-REQ-021: Owns preserving policy-critical context through compaction without treating guidance or memory artifacts as enforcement policy.
- DESIGN-REQ-028: Owns shared-plane WorkItem and event alignment for compaction so context lifecycle changes remain visible across session lineage.

Needs Clarification
- None

Notes
This story depends on the Claude managed-session core schema and policy envelope stories so context snapshots can attach to the shared session records and can distinguish guidance, memory, and enforcement boundaries.
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Claude Context Snapshots

**Summary**: As an operator investigating session quality, I want typed Claude context snapshot metadata, reload policy, and compaction epochs so that I can inspect what context entered a session and what survives compaction.

**Goal**: Operators can inspect bounded metadata for startup, on-demand, and post-compaction Claude context segments, including source kind, load timing, reinjection policy, guidance-versus-enforcement classification, and immutable compaction epoch lineage.

**Independent Test**: Bootstrap a Claude session with managed, project, and local CLAUDE files, MCP manifests, skills, hooks, file reads, nested rules, and invoked skill bodies; compact the session; then assert the original snapshot remains immutable and the new epoch reloads only allowed context with the documented reinjection policies.

**Acceptance Scenarios**:

1. **Given** a Claude session starts with startup context sources, **when** the context snapshot is recorded, **then** the snapshot indexes each startup segment with the correct source kind and `loaded_at = startup`.
2. **Given** file reads, nested CLAUDE files, path rules, or invoked skill bodies are loaded after startup, **when** the trigger occurs, **then** those segments are indexed as on-demand context and are absent before the trigger.
3. **Given** any context segment is recorded, **when** an operator inspects its metadata, **then** the segment exposes a reinjection policy that matches the documented source kind.
4. **Given** a session is compacted, **when** the post-compaction state is recorded, **then** the system creates a new ContextSnapshot epoch and leaves the previous epoch unchanged.
5. **Given** compaction occurs, **when** work history and events are inspected, **then** an explicit compaction work item and normalized compaction event are visible.
6. **Given** memory artifacts or CLAUDE guidance enter context, **when** the segment is indexed, **then** the segment is classified as guidance and is never represented as an enforcement policy source.
7. **Given** context metadata is centrally available, **when** operators inspect the ContextIndex, **then** large source payloads remain outside the central plane by default and only metadata or pointers are exposed.

### Edge Cases

- A file read that was useful before compaction must not be automatically reintroduced unless the documented trigger recurs.
- Startup-critical context must remain eligible for reinjection after compaction even when the original snapshot is immutable.
- Unknown or unsupported context source kinds must fail validation instead of being silently stored as generic context.
- Memory guidance and managed policy controls may reference similar files or settings, but the context snapshot must keep guidance distinct from enforcement.
- Context segment metadata must remain bounded and must not embed full transcripts, full file contents, or large invoked skill bodies.

## Assumptions

- MM-345 builds on the canonical Claude session records from MM-342 and policy boundary concepts from MM-343 rather than introducing a parallel session model.
- Runtime-local artifact or source references are sufficient for central inspection when full payloads must remain outside the control plane.
- The story validates compaction metadata and event behavior without implementing checkpoint restore behavior.

## Source Design Requirements

- **DESIGN-REQ-013**: Source `docs/ManagedAgents/ClaudeCodeManagedSessions.md` sections 2.5, 9.6, and 13 require typed ContextSnapshot metadata for Claude startup and runtime context composition. Scope: in scope. Maps to FR-001, FR-002, FR-003, and FR-004.
- **DESIGN-REQ-014**: Source sections 9.6 and 13 require context segment source kinds, source references, load timing, and reload or reinjection semantics. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-005, and FR-006.
- **DESIGN-REQ-015**: Source section 13.4 requires compaction to create a new context epoch instead of mutating the old snapshot. Scope: in scope. Maps to FR-007 and FR-008.
- **DESIGN-REQ-020**: Source section 19.2 requires ContextIndex storage of ContextSnapshot metadata and segment pointers while keeping large payloads outside the central plane by default. Scope: in scope. Maps to FR-009 and FR-010.
- **DESIGN-REQ-021**: Source section 13.5 requires memory artifacts and CLAUDE guidance to remain guidance and never become enforcement policy sources. Scope: in scope. Maps to FR-011 and FR-012.
- **DESIGN-REQ-028**: Source sections 3.1 and 13.4 require shared-plane work-item and event alignment for compaction so context lifecycle changes remain visible across session lineage. Scope: in scope. Maps to FR-008, FR-013, and FR-014.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a Claude ContextSnapshot contract associated with a canonical Claude managed session and a compaction epoch.
- **FR-002**: Every ContextSnapshot MUST contain one or more bounded context segment metadata records with stable segment identifiers, source kind, source reference, load timing, reinjection policy, optional token budget hint, and guidance-versus-enforcement classification.
- **FR-003**: Context segment source kind MUST accept only documented kinds for startup, on-demand, and post-compaction context.
- **FR-004**: Startup context sources MUST include system prompt, output style, managed CLAUDE file, project CLAUDE file, local CLAUDE file, auto memory, MCP tool manifest, skill description, and hook-injected context.
- **FR-005**: On-demand context sources MUST include file read, nested CLAUDE file, path rule, invoked skill body, and runtime summary segments only when their trigger occurs.
- **FR-006**: Reinjection policy MUST be explicit for every segment and MUST distinguish always, on-demand, budgeted, never, startup-refresh, and configurable behavior.
- **FR-007**: Compaction MUST create a new ContextSnapshot with an incremented compaction epoch and MUST NOT mutate any previous snapshot.
- **FR-008**: Post-compaction snapshots MUST reload startup-critical context and omit on-demand context unless its trigger recurs or a budgeted reinjection policy permits it.
- **FR-009**: ContextIndex output MUST expose metadata and source pointers only by default and MUST NOT embed full transcripts, full file contents, or large invoked skill bodies.
- **FR-010**: Context segment metadata MUST reject large payload-like metadata that exceeds bounded inspection limits.
- **FR-011**: Memory artifacts and CLAUDE guidance MUST be classified as guidance.
- **FR-012**: ContextSnapshot records MUST NOT represent memory artifacts, CLAUDE guidance, or loaded context as enforcement policy sources.
- **FR-013**: Compaction MUST produce an explicit work item for the compaction operation.
- **FR-014**: Context loading and compaction MUST emit normalized, bounded events that operators can correlate to session, turn, snapshot, and work-item identifiers.

### Key Entities

- **Claude ContextSnapshot**: Immutable metadata record for the context known to a Claude session at one compaction epoch.
- **Claude Context Segment**: Bounded metadata for one context source, including kind, source reference, load timing, reinjection policy, token budget hint, and guidance classification.
- **ContextIndex**: Operator-visible index of ContextSnapshot metadata and segment pointers without full source payload storage by default.
- **Compaction Epoch**: Monotonic snapshot version created when context is compacted and reloaded.
- **Compaction Work Item**: Explicit work record showing that compaction occurred and created a new context epoch.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests cover every documented startup context source kind and every documented on-demand context source kind.
- **SC-002**: Unit tests prove every context segment requires an explicit reinjection policy and rejects unknown source kinds, load timings, and oversized payload metadata.
- **SC-003**: Boundary tests prove compaction creates a new epoch and leaves the previous snapshot unchanged in at least one representative pre/post-compaction scenario.
- **SC-004**: Integration-style tests construct a representative session context flow and verify startup loads, on-demand loads, compaction work item emission, normalized events, and post-compaction reinjection behavior.
- **SC-005**: Validation proves memory artifacts and CLAUDE guidance are never serialized as enforcement policy sources.
- **SC-006**: Source design coverage for DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-020, DESIGN-REQ-021, and DESIGN-REQ-028 maps to passing validation evidence.
