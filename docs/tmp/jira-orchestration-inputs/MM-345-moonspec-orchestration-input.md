# MM-345 MoonSpec Orchestration Input

## Source

- Jira issue: MM-345
- Board scope: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MoonSpec STORY-004: Index Claude context, compaction, and memory boundaries
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

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
