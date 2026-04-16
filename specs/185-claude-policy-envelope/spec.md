# Feature Specification: Claude Policy Envelope

**Feature Branch**: `185-claude-policy-envelope`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
Jira issue: MM-343 from MM board
Summary: MoonSpec STORY-002: Resolve Claude policy envelopes and handshakes
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-343 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-343: MoonSpec STORY-002: Resolve Claude policy envelopes and handshakes

User Story
As an enterprise administrator, I need Claude managed settings, permission modes, rules, hooks, sandbox, MCP, memory, provider, and handshake state compiled into a versioned PolicyEnvelope so session behavior is enforceable and auditable.

Source Document
docs/ManagedAgents/ClaudeCodeManagedSessions.md

Source Sections
- 2.3 Treat policy as compiled runtime state
- 11. Policy model
- 21.2 Managed settings risk model
- 22.5 Known semantic mismatch

Coverage IDs
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-024
- DESIGN-REQ-028
- DESIGN-REQ-030

Story Metadata
- Story ID: STORY-002
- Short name: claude-policy-envelope
- Dependency mode: none
- Story dependencies from breakdown: STORY-001

Acceptance Criteria
- Server-managed settings win when non-empty; endpoint-managed settings apply only when server-managed settings are empty or unsupported.
- Managed settings cannot be overridden by local settings, CLI arguments, or user settings.
- Policy fetch states include cache_hit, fetched, fetch_failed, and fail_closed as applicable.
- Risky managed hooks and managed environment variables can require an explicit security-dialog state in interactive sessions.
- BootstrapPreferences for Claude are labeled and handled as bootstrap templates, never represented as native user-overridable managed defaults.
- Every compiled envelope records provider_mode and policy trust level for governance reporting.

Requirements
- Compile all relevant policy sources into one effective PolicyEnvelope.
- Version policy envelopes over time and emit policy events.
- Preserve Claude settings precedence exactly.
- Expose limited support for Codex-style managed defaults honestly.
- Support fail-closed startup behavior when configured policy refresh fails.

Independent Test
Feed server-managed, endpoint-managed, empty, fetch-failed, fail-closed, interactive-dialog, and non-interactive policy scenarios through the policy boundary and assert the compiled PolicyEnvelope, events, and handshake states match precedence rules.

Out of Scope
- Actual tool execution decisions.
- Context compaction.
- Hook runtime invocation.

Needs Clarification
- [NEEDS CLARIFICATION] How much policy fetch state should be exposed to end users versus administrators?
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - Claude Policy Envelope

**Summary**: As an enterprise administrator, I want Claude managed settings, permission controls, provider constraints, and startup handshake state compiled into a versioned policy envelope so that Claude session behavior is enforceable and auditable.

**Goal**: Administrators can inspect the effective Claude Code policy for a managed session, including policy source precedence, fetch state, trust level, provider mode, bootstrap-template handling, and any required startup security-dialog state.

**Independent Test**: Feed server-managed, endpoint-managed, empty, cache-hit, fetch-failed, fail-closed, interactive-dialog, and non-interactive policy scenarios through the policy boundary, then assert the compiled envelope, policy events, and handshake states match precedence and governance rules without invoking live Claude execution.

**Acceptance Scenarios**:

1. **Given** non-empty server-managed settings and endpoint-managed settings are both available, **when** the policy boundary compiles the Claude policy, **then** server-managed settings determine the managed source and endpoint-managed settings cannot override them.
2. **Given** server-managed settings are empty or unsupported and endpoint-managed settings are available, **when** the policy boundary compiles the Claude policy, **then** endpoint-managed settings become the effective managed source.
3. **Given** local project settings, CLI arguments, or user settings conflict with managed settings, **when** the effective policy is compiled, **then** the managed settings remain authoritative and lower scopes are retained only for observability.
4. **Given** a configured policy refresh fails in fail-closed mode, **when** startup policy is resolved, **then** the handshake enters a fail-closed state and no permissive envelope is produced.
5. **Given** managed hooks or managed environment variables are classified as risky in an interactive session, **when** policy is resolved, **then** the handshake records that a security dialog is required before the session can proceed.
6. **Given** BootstrapPreferences are supplied for Claude, **when** the envelope is compiled, **then** they are represented as bootstrap templates and are not labeled as native Claude managed defaults.
7. **Given** any policy envelope is produced, **when** it is inspected for governance, **then** it records provider mode, policy trust level, managed source kind, fetch state, version, and the effective enforcement controls.

### Edge Cases

- Empty server-managed settings must not mask usable endpoint-managed settings.
- Failed refreshes in fail-open or cache-allowed scenarios must preserve the exact fetch state rather than collapsing all failures into one status.
- Non-interactive sessions that require an interactive security dialog must be blocked or marked waiting according to policy instead of silently accepting risky controls.
- Lower-scope settings may be useful for diagnostics, but they must never be presented as equivalent to managed enforcement.
- Provider modes with limited policy support must still produce honest governance metadata rather than implying unavailable enforcement.
- Bootstrap templates must remain distinguishable from runtime-enforced managed settings in every persisted and reported envelope.

## Assumptions

- Policy fetch details are fully visible to administrator and operator surfaces, while user-facing surfaces expose only a coarse, non-sensitive policy status unless explicitly authorized.
- This story builds on the canonical Claude session records from MM-342 / STORY-001 and does not introduce a parallel session model.
- Policy fixtures are sufficient for required validation; live Claude or provider calls are outside this story.

## Source Design Requirements

- **DESIGN-REQ-007**: Source `docs/ManagedAgents/ClaudeCodeManagedSessions.md` sections 2.3 and 11 require managed settings, permission modes, rules, hooks, sandbox, MCP, memory, provider constraints, and surface constraints to compile into one effective versioned `PolicyEnvelope`. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, and FR-010.
- **DESIGN-REQ-008**: Source section 11 requires managed source resolution to preserve server-managed and endpoint-managed precedence, cache-hit, fetched, fetch-failed, and fail-closed states. Scope: in scope. Maps to FR-002, FR-005, and FR-006.
- **DESIGN-REQ-009**: Source section 22.5 requires Codex-style managed defaults to be represented honestly for Claude as limited bootstrap templates, not native user-overridable managed defaults. Scope: in scope. Maps to FR-008.
- **DESIGN-REQ-010**: Source section 11.5 requires startup policy handshake states, including security-dialog requirements for risky managed controls. Scope: in scope. Maps to FR-007 and FR-011.
- **DESIGN-REQ-024**: Source section 21.2 requires policy trust level, provider caveats, and governance distinctions such as endpoint-enforced versus server-managed best-effort policy. Scope: in scope. Maps to FR-004 and FR-009.
- **DESIGN-REQ-028**: Source section 23.2 requires this policy foundation to fit the phased Claude session-plane rollout after the core session schema. Scope: in scope. Maps to FR-001 and FR-012.
- **DESIGN-REQ-030**: Source section 24 includes an open question about user-visible policy fetch state. Scope: in scope with the assumption that administrators receive detailed fetch evidence and non-admin surfaces receive coarse status. Maps to FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST resolve Claude policy for an existing canonical Claude managed session without creating a separate session model.
- **FR-002**: System MUST compile server-managed settings, endpoint-managed settings, permission modes, allow/ask/deny rules, protected paths, sandbox controls, hooks, MCP controls, memory controls, provider mode, and surface constraints into one effective policy envelope.
- **FR-003**: Every compiled policy envelope MUST be versioned and associated with the target Claude managed session.
- **FR-004**: Every compiled policy envelope MUST record provider mode, policy trust level, managed source kind, policy fetch state, and whether a security dialog is required.
- **FR-005**: Server-managed settings MUST take precedence over endpoint-managed settings when server-managed settings are non-empty and supported.
- **FR-006**: Endpoint-managed settings MUST apply only when server-managed settings are empty or unsupported.
- **FR-007**: Managed settings MUST NOT be overridden by CLI arguments, local project settings, shared project settings, or user settings; lower scopes may be retained for observability only.
- **FR-008**: BootstrapPreferences for Claude MUST be represented only as bootstrap templates and MUST NOT be labeled as native Claude managed defaults.
- **FR-009**: Provider modes with limited policy support MUST record their support limits in governance metadata instead of implying full enforcement.
- **FR-010**: Policy resolution MUST emit policy events for fetch start, fetch success, fetch failure, compiled envelope, version changes, dialog required, dialog accepted, and dialog rejected when those outcomes occur.
- **FR-011**: Interactive sessions with risky managed hooks or managed environment variables MUST surface a security-dialog handshake state before proceeding.
- **FR-012**: Fail-closed policy refresh failures MUST block startup or produce an explicit fail-closed handshake state rather than falling back to a permissive policy.
- **FR-013**: Administrator-facing policy output MUST include detailed fetch state and trust evidence, while non-admin output MUST expose only coarse policy status unless an authorization boundary permits more detail.

### Key Entities

- **Policy Envelope**: Versioned effective policy attached to a Claude managed session, including provider mode, trust level, managed source kind, fetch state, enforcement controls, and security-dialog requirement.
- **Managed Policy Source**: Server-managed, endpoint-managed, or absent managed source with version and fetch metadata used during policy resolution.
- **Policy Handshake**: Startup state that records whether policy resolution succeeded, failed closed, requires a security dialog, or is ready for session execution.
- **Bootstrap Template**: A Claude bootstrap preference value that may seed launch behavior but is not an enforced native managed-default layer.
- **Policy Event**: Append-only governance event describing fetch, compile, version-change, and dialog lifecycle transitions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Validation covers at least seven policy scenarios: server-managed, endpoint-managed, empty, cache-hit, fetch-failed, fail-closed, and security-dialog-required.
- **SC-002**: Precedence validation proves server-managed settings win over endpoint-managed settings in all conflicting managed-source cases.
- **SC-003**: Override validation proves CLI, local project, shared project, and user settings cannot override managed settings in every conflicting fixture.
- **SC-004**: Governance validation proves every successful envelope records provider mode, policy trust level, managed source kind, fetch state, security-dialog state, and version.
- **SC-005**: Failure validation proves fail-closed refresh failures do not produce permissive startup behavior.
- **SC-006**: Bootstrap validation proves every Claude BootstrapPreferences fixture is labeled as a bootstrap template and never as native managed defaults.
- **SC-007**: Source design coverage for DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009, DESIGN-REQ-010, DESIGN-REQ-024, DESIGN-REQ-028, and DESIGN-REQ-030 maps to passing validation evidence.
