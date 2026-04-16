# MM-348 MoonSpec Orchestration Input

## Source

- Jira issue: MM-348
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MoonSpec STORY-007: Implement multi-surface projection and handoff contracts
- Labels: none
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-348 from MM project
Summary: MoonSpec STORY-007: Implement multi-surface projection and handoff contracts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-348 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-348: MoonSpec STORY-007: Implement multi-surface projection and handoff contracts

User Story
As a user moving between terminal, IDE, desktop, web, mobile, scheduler, and SDK contexts, I need surface attachment and handoff semantics that preserve where Claude is actually executing.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 2.2 Separate execution from presentation
- 7.3 Critical execution distinctions
- 16. Multi-surface behavior
- 17.1 Session APIs
- 18.2 Surface events
- 21.6 Cloud vs local security

Coverage IDs
- DESIGN-REQ-002
- DESIGN-REQ-004
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-024
- DESIGN-REQ-028

Story Metadata
- Story ID: STORY-007
- Short name: claude-surfaces-handoff
- Dependency mode: none
- Story dependencies from breakdown: STORY-001

Independent Test
Attach and detach terminal, web, mobile, scheduler, and SDK surfaces to local and cloud sessions, simulate disconnect/reconnect, and perform local-to-cloud handoff while asserting execution_owner, projection_mode, lineage, and events.

Acceptance Criteria
- A session may have one primary surface and multiple projections.
- Remote Control adds a SurfaceBinding without changing execution_owner or minting a new session.
- Cloud handoff creates a new session with execution_owner = anthropic_cloud_vm and handoff lineage to the source session.
- Surface disconnects do not fail sessions unless the runtime exits.
- Resuming on a different surface preserves canonical session identity when execution owner remains the same.
- Security and compliance reporting can distinguish local execution, Remote Control projection, and cloud execution.

Requirements
- Expose durable SurfaceBinding records and lifecycle events.
- Keep execution semantics independent from presentation surface.
- Represent handoff artifacts as lineage seed references.
- Avoid reimplementing Anthropic proprietary local protocol in the plane.

Implementation Notes
- Treat presentation surfaces as attachable views over an execution owner, not as independent sessions by default.
- Model durable `SurfaceBinding` records with at most one primary surface and any number of projections.
- Ensure Remote Control projection adds a binding while preserving the original session identity and execution owner.
- Represent local-to-cloud handoff as a new session whose execution owner is `anthropic_cloud_vm` and whose lineage references the source session through handoff seed artifacts.
- Preserve canonical session identity across disconnect/reconnect and cross-surface resume when the execution owner has not changed.
- Emit normalized lifecycle events for surface attachment, detachment, disconnect, reconnect, resume, and handoff so downstream audit and workflow-boundary tests can assert behavior.
- Keep security and compliance reporting able to distinguish local execution, Remote Control projection, and cloud execution.
- Do not reimplement Anthropic proprietary local transport; keep MoonMind at the orchestration, identity, lineage, event, and contract boundary.

Out of Scope
- Actual Claude proprietary transport reimplementation.
- Checkpoint restore internals.
- Enterprise telemetry dashboards.

Source Design Coverage
- DESIGN-REQ-002: Covered by preserving execution semantics separately from terminal, IDE, desktop, web, mobile, scheduler, and SDK presentation surfaces.
- DESIGN-REQ-004: Covered by modeling Remote Control as projection without changing execution owner or creating a new canonical session.
- DESIGN-REQ-019: Covered by maintaining session identity, usage, resume, archive, lineage, communication, and surface semantics across attachment and handoff.
- DESIGN-REQ-020: Covered by requiring explicit session and surface API behavior for attach, detach, resume, and handoff paths.
- DESIGN-REQ-024: Covered by security reporting that distinguishes local execution, Remote Control projection, and cloud execution.
- DESIGN-REQ-028: Covered by normalized lifecycle events for surface binding, projection, disconnect/reconnect, resume, and cloud handoff.

Needs Clarification
- [NEEDS CLARIFICATION] How should cloud handoff summaries be versioned and audited?
