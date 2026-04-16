# MM-349 MoonSpec Orchestration Input

## Source

- Jira issue: MM-349
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: MoonSpec STORY-008: Export Claude storage, telemetry, and governance evidence
- Labels: none
- Trusted fetch tool: `jira.get_issue`
- Canonical source: Synthesized from the trusted `jira.get_issue` MCP response because the response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, or `presetBrief`.

## Canonical MoonSpec Feature Request

Jira issue: MM-349 from MM project
Summary: MoonSpec STORY-008: Export Claude storage, telemetry, and governance evidence
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-349 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-349: MoonSpec STORY-008: Export Claude storage, telemetry, and governance evidence

User Story
As an enterprise auditor, I need payload-light storage, normalized events, OTel-derived telemetry, retention controls, and governance views so Claude managed sessions can be reviewed without centralizing source code by default.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 17.7 Event subscription
- 18. Event model
- 19. Storage model
- 20. Observability
- 21. Security and governance
- 23.6 Phase 6: enterprise telemetry and audits

Coverage IDs
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-025
- DESIGN-REQ-029
- DESIGN-REQ-028

Story Metadata
- Story ID: STORY-008
- Short name: claude-governance-telemetry
- Dependency mode: none
- Story dependencies from breakdown: STORY-001, STORY-002, STORY-003

Independent Test
Run synthetic sessions with policy decisions, hooks, checkpoints, compactions, subagents, teams, reconnects, and usage, then assert event subscriptions, storage pointers, retention metadata, normalized metrics, spans, and compliance export records.

Acceptance Criteria
- The EventLog is append-only and includes normalized session, surface, policy, turn, work, decision, and child-work events.
- Core stores retain metadata and references rather than full source code, transcripts, or checkpoint payloads by default.
- Retention classes are policy-configurable rather than hard-coded.
- Claude OpenTelemetry is mapped into shared metrics, logs/events, and optional trace spans.
- Usage rolls up by session, group, user, workspace, runtime kind, and provider.
- Governance export views include policy trust level, provider mode, protected-path behavior, hook audit records, and cloud/local execution distinctions.

Requirements
- Expose SubscribeSessionEvents, SubscribeGroupEvents, and SubscribeOrgPolicyEvents.
- Normalize Claude observations into shared observability contracts.
- Keep the plane metadata-first and payload-light.
- Support policy-driven retention.
- Provide audit evidence for layered security controls and hook behavior.

Implementation Notes
- Preserve an append-only EventLog for normalized session, surface, policy, turn, work, decision, and child-work events.
- Store central-plane metadata, event envelopes, policy versions, usage counters, and artifact pointers while keeping transcripts, full file reads, checkpoint payloads, and local caches runtime-local by default.
- Model retention classes as policy-controlled values rather than hard-coded constants.
- Map Claude OpenTelemetry into the shared observability schema for metrics, logs/events, and optional trace spans.
- Roll usage up by session, group, user, workspace, runtime kind, and provider.
- Expose governance evidence for policy trust level, provider mode, protected-path behavior, hook audit records, and cloud/local execution distinctions.
- Keep source-code, transcript, and checkpoint-payload centralization out of the default behavior.

Out of Scope
- Centralizing every transcript and file diff by default.
- Changing runtime policy decisions.
- Building provider-specific telemetry backends.

Source Design Coverage
- DESIGN-REQ-019
- DESIGN-REQ-020
- DESIGN-REQ-021
- DESIGN-REQ-022
- DESIGN-REQ-023
- DESIGN-REQ-024
- DESIGN-REQ-025
- DESIGN-REQ-029
- DESIGN-REQ-028
