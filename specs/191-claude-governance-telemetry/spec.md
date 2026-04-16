# Feature Specification: Claude Governance Telemetry

**Feature Branch**: `191-claude-governance-telemetry`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
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
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Claude Governance Telemetry

**Summary**: As an enterprise auditor, I want Claude managed-session events, storage references, telemetry, retention metadata, and governance evidence exported without centralizing source code by default so that sessions can be reviewed safely and consistently.

**Goal**: Auditors and operators can subscribe to bounded Claude session evidence, inspect payload-light storage pointers, verify retention policy choices, map Claude OpenTelemetry into shared observability signals, and review governance summaries that distinguish policy trust, provider mode, hooks, protected paths, and local versus cloud execution.

**Independent Test**: Run synthetic Claude managed-session flows that include policy decisions, hooks, checkpoints, compactions, subagents, team messages, surface reconnects, and usage, then assert event subscriptions, storage pointers, retention metadata, normalized metrics, trace span names, usage rollups, and compliance export records without embedding source code, transcripts, or checkpoint payloads in central-plane records.

**Acceptance Scenarios**:

1. **Given** Claude session, surface, policy, turn, work, decision, and child-work activity occurs, **when** an auditor subscribes to session, group, or org-policy events, **then** the event stream exposes append-only normalized event envelopes with bounded metadata.
2. **Given** Claude runtime artifacts include transcripts, file reads, checkpoint payloads, and local caches, **when** central-plane storage records are generated, **then** the central plane stores metadata and artifact references by default rather than full runtime payloads.
3. **Given** retention policy is configured for session metadata, event logs, usage rollups, audit metadata, and checkpoint payload references, **when** evidence is exported, **then** retention classes are reported from policy-controlled values rather than hard-coded constants.
4. **Given** Claude emits OpenTelemetry observations, **when** observability evidence is normalized, **then** shared metrics, logs or events, and optional trace spans use the managed-session schema expected by MoonMind.
5. **Given** Claude managed sessions, groups, users, workspaces, runtime kinds, and providers produce usage, **when** usage evidence is queried, **then** usage rolls up across those dimensions without duplicating child or team usage.
6. **Given** governance evidence is exported, **when** an auditor reviews it, **then** the record distinguishes policy trust level, provider mode, protected-path behavior, hook audit provenance, and local, Remote Control, or cloud execution mode.

### Edge Cases

- Event subscription rejects unknown event family names or unsupported subscription scopes instead of silently returning an unfiltered stream.
- Central-plane records reject embedded source text, full transcripts, full file reads, and checkpoint payloads when default payload-light mode is active.
- Retention export fails validation when a class is missing, blank, or hard-coded outside policy.
- Unknown metric names, trace span names, hook outcomes, policy trust levels, provider modes, or execution modes fail validation instead of being coerced into generic values.
- Usage rollups must not double-count subagent usage when both child and parent summaries are inspected.

## Assumptions

- MM-349 builds on the Claude session core, policy, context, checkpoint, child-work, and surface stories from STORY-001 through STORY-007 and does not replace those identity models.
- Runtime validation can use deterministic synthetic session fixtures before live Claude provider telemetry export is available.
- Checkpoint payloads remain runtime-local by default for this story; optional external export sinks may receive references and audit metadata, not full payloads.

## Source Design Requirements

- **DESIGN-REQ-019**: Source sections 17.7, 18, and 19 require event subscriptions and storage records to preserve session, group, event, lineage, artifact, and usage identities across Claude session evidence. Scope: in scope. Maps to FR-001, FR-002, FR-006, FR-007, FR-008, and FR-021.
- **DESIGN-REQ-020**: Source sections 17.7 and 18 require subscription APIs for session, group, and org-policy events and normalized event names for session, surface, policy, turn, work, decision, and child-work events. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, and FR-005.
- **DESIGN-REQ-021**: Source section 19 requires the central plane to stay payload-light by default, storing metadata, event envelopes, policy versions, usage counters, and artifact pointers while runtime-local stores retain transcripts, file reads, checkpoint payloads, and local caches. Scope: in scope. Maps to FR-006, FR-007, FR-008, FR-009, FR-010, and FR-011.
- **DESIGN-REQ-022**: Source section 19.3 requires retention classes for session metadata, event logs, usage rollups, audit metadata, and checkpoint payloads to be policy-driven rather than hard-coded. Scope: in scope. Maps to FR-012, FR-013, and FR-014.
- **DESIGN-REQ-023**: Source section 20 requires runtime observations to normalize into shared metrics, logs or events, optional traces, and the recommended managed-session metric and span names. Scope: in scope. Maps to FR-015, FR-016, FR-017, FR-018, and FR-019.
- **DESIGN-REQ-024**: Source section 21 requires governance evidence for layered controls, policy trust level, provider mode, protected-path policy, hook audit provenance, and cloud versus local execution distinctions. Scope: in scope. Maps to FR-020, FR-022, FR-023, FR-024, FR-025, FR-026, and FR-027.
- **DESIGN-REQ-025**: Source section 21.5 requires hook audit records to include hook name, source scope, event type, matcher, and outcome for governance review. Scope: in scope. Maps to FR-024 and FR-025.
- **DESIGN-REQ-028**: Source section 23.6 requires the enterprise telemetry and audits phase to deliver OTel normalization, hook audit streams, policy trust level, compliance export views, and provider-mode-aware dashboards. Scope: in scope. Maps to FR-015, FR-016, FR-020, FR-024, FR-026, FR-027, and FR-028.
- **DESIGN-REQ-029**: Source sections 19 and 20 require usage rollups and observability evidence to be keyed by session, group, user, workspace, runtime kind, provider, and child or team dimensions where applicable. Scope: in scope. Maps to FR-019, FR-021, and FR-029.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose event subscription requests for Claude session events, group events, and org-policy events.
- **FR-002**: Event subscription requests MUST include a bounded scope identifier appropriate to the subscription type.
- **FR-003**: Event envelopes MUST be append-only and MUST include bounded identity fields, event name, event time, source family, and compact metadata.
- **FR-004**: Event names MUST be normalized for session, surface, policy, turn, work, decision, and child-work families.
- **FR-005**: Unsupported event families or event names MUST fail validation instead of being stored or streamed as generic events.
- **FR-006**: Central-plane storage evidence MUST distinguish session registry, event log, policy store, context index, checkpoint index, artifact index, and usage store records.
- **FR-007**: Central-plane storage evidence MUST store metadata, event envelopes, policy versions, usage counters, and artifact pointers by default.
- **FR-008**: Runtime-local payload evidence MUST distinguish transcripts, full file reads, checkpoint payloads, and local caches from central-plane metadata.
- **FR-009**: Default central-plane evidence MUST NOT embed source code, full transcripts, full file reads, checkpoint payloads, or local caches.
- **FR-010**: Artifact references used by central-plane evidence MUST be bounded nonblank pointers rather than embedded payload bodies.
- **FR-011**: Optional export sinks MAY be represented for audit logs, OpenTelemetry backends, and compliance archives without changing the default payload-light central-plane behavior.
- **FR-012**: Retention evidence MUST include policy-controlled retention classes for hot session metadata, hot event logs, usage rollups, audit event metadata, and checkpoint payload references.
- **FR-013**: Retention evidence MUST identify whether each class is policy-controlled and MUST reject blank retention class names.
- **FR-014**: Retention evidence MUST NOT silently substitute hard-coded retention values when policy values are missing.
- **FR-015**: Claude OpenTelemetry observations MUST normalize into shared managed-session metric records.
- **FR-016**: Claude OpenTelemetry observations MUST normalize logs or events into the same event envelope conventions used by session evidence.
- **FR-017**: Claude OpenTelemetry trace evidence MUST use supported managed-session span names when trace data is present.
- **FR-018**: Unsupported metric names or trace span names MUST fail validation instead of being coerced into generic telemetry.
- **FR-019**: Usage rollup evidence MUST support session, group, user, workspace, runtime kind, provider, input/output token direction, child, and team dimensions without double-counting child usage.
- **FR-020**: Governance evidence MUST include policy trust level and provider mode for every exported Claude session governance record.
- **FR-021**: Governance evidence MUST link back to session, group, artifact, and usage identifiers using bounded references.
- **FR-022**: Governance evidence MUST distinguish managed settings source resolution, permission rules, permission mode, protected paths, sandboxing, hooks, classifier-based auto mode, interactive dialogs, and runtime isolation layers when those controls are present.
- **FR-023**: Protected-path behavior MUST be represented as a dedicated governance field rather than inferred only from permission mode.
- **FR-024**: Hook audit evidence MUST include hook name, source scope, event type, matcher, and outcome.
- **FR-025**: Unsupported hook source scopes or outcomes MUST fail validation rather than being stored as generic hook metadata.
- **FR-026**: Governance evidence MUST distinguish local execution, Remote Control projection, and cloud execution.
- **FR-027**: Compliance export views MUST expose policy trust level, provider mode, protected-path behavior, hook audit records, execution mode, storage reference summaries, retention summaries, and telemetry summaries.
- **FR-028**: Provider-mode-aware dashboard summaries MUST be derivable from governance evidence without requiring embedded runtime payloads.
- **FR-029**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-349 and the original preset brief as traceability evidence.

### Key Entities

- **Claude Event Subscription**: Bounded request for session, group, or org-policy evidence streams.
- **Claude Event Envelope**: Append-only normalized event record with identity, event name, time, source family, and compact metadata.
- **Claude Storage Evidence**: Payload-light record describing central-plane stores, runtime-local payload classes, artifact references, and optional export sinks.
- **Claude Retention Evidence**: Policy-controlled retention class mapping for session metadata, event logs, usage rollups, audit metadata, and checkpoint payload references.
- **Claude Telemetry Evidence**: Normalized metrics, logs/events, and optional trace spans derived from Claude observations.
- **Claude Governance Evidence**: Compliance-oriented record tying policy trust, provider mode, protected paths, hooks, execution mode, storage references, retention, telemetry, and usage evidence together.
- **Claude Usage Rollup**: Usage summary keyed by session, group, user, workspace, runtime kind, provider, token direction, and child/team dimensions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove all supported session, surface, policy, turn, work, decision, and child-work event names validate and unsupported names are rejected.
- **SC-002**: Unit tests prove central-plane storage evidence accepts bounded references and rejects embedded source code, full transcripts, full file reads, checkpoint payloads, or local caches in default mode.
- **SC-003**: Unit tests prove retention evidence is policy-controlled and rejects missing or blank retention classes.
- **SC-004**: Unit tests prove Claude OpenTelemetry metric and trace observations normalize only to supported managed-session names.
- **SC-005**: Unit tests prove governance evidence records policy trust level, provider mode, protected-path behavior, hook audit fields, and local, Remote Control, or cloud execution mode.
- **SC-006**: Integration-style boundary tests construct one synthetic session, group, policy decision, hook, checkpoint, compaction, subagent, team message, surface reconnect, and usage flow, then assert event subscriptions, storage references, retention metadata, telemetry summaries, usage rollups, and compliance export records remain payload-light.
- **SC-007**: Verification evidence maps DESIGN-REQ-019, DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-025, DESIGN-REQ-028, and DESIGN-REQ-029 to passing behavior.
