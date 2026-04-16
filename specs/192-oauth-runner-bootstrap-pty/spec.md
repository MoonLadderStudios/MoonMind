# Feature Specification: OAuth Runner Bootstrap PTY

**Feature Branch**: `192-oauth-runner-bootstrap-pty`  
**Created**: 2026-04-16  
**Status**: Draft  
**Input**:

```text
# MM-361 MoonSpec Orchestration Input

## Source

- Jira issue: MM-361
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: [OAuthTerminal] Replace placeholder auth runner with provider bootstrap PTY lifecycle
- Labels: `MM-318`, `managed-sessions`, `oauth-terminal`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-361 from MM project
Summary: [OAuthTerminal] Replace placeholder auth runner with provider bootstrap PTY lifecycle
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-361 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-361: [OAuthTerminal] Replace placeholder auth runner with provider bootstrap PTY lifecycle

Short Name
oauth-runner-bootstrap-pty

User Story
As a MoonMind operator, I can start a Codex OAuth enrollment session that launches a short-lived auth runner container running the provider bootstrap command in a PTY, so credential enrollment is first-party and does not depend on placeholder container behavior.

Acceptance Criteria
- Given an authorized Codex OAuth session starts, the auth runner container mounts the selected auth volume at the provider enrollment path.
- Given the provider registry defines a bootstrap command, the runner executes that command in a PTY owned by the OAuth enrollment session.
- Given the session succeeds, fails, expires, or is cancelled, the runner stops and cleanup is idempotent.
- Given the runner is active, it exposes no ordinary managed task terminal and no generic Docker exec capability.
- Given runner startup fails because Docker or the provider CLI is unavailable, the OAuth session fails with an actionable, redacted reason.

Current Evidence
- `specs/183-oauth-terminal-flow/verification.md` has verdict `ADDITIONAL_WORK_NEEDED`.
- `moonmind/workflows/temporal/runtime/terminal_bridge.py` currently starts a runner image with `sleep` and comments that the real specialized PTY container is not implemented.

Requirements
- Replace the placeholder auth runner lifecycle with a short-lived runner container that executes the selected provider bootstrap command in a PTY.
- Mount the selected Codex OAuth auth volume at the provider enrollment path during enrollment.
- Scope the runner and PTY ownership to the OAuth enrollment session.
- Route terminal behavior through MoonMind's authenticated PTY/WebSocket bridge only.
- Stop the runner after success, failure, expiry, or cancellation, and make cleanup idempotent.
- Fail with actionable, redacted diagnostics when Docker, runner startup, or provider CLI execution is unavailable.
- Preserve the boundary that OAuth terminal code is for enrollment only, not managed task execution or generic Docker exec.

Independent Test
Start a Codex OAuth session with a fake provider bootstrap command, assert the auth runner mounts the selected auth volume, executes the bootstrap command inside the session-owned PTY, exposes only authenticated terminal bridge access, and performs idempotent cleanup for success, failure, expiry, and cancellation paths with redacted failure reasons.

Out of Scope
- Managed Codex task execution changes.
- Claude/Gemini task-scoped session parity.
- Generic Docker exec exposure.
- Ordinary managed task terminal attachment.

Source Document
docs/ManagedAgents/OAuthTerminal.md

Source Sections
- 5. OAuth Terminal Contract
- 5.1 Auth runner container
- 10. Operator Behavior

Coverage IDs
- DESIGN-REQ-011
- DESIGN-REQ-012
- DESIGN-REQ-014
- DESIGN-REQ-020

Source Design Coverage
- DESIGN-REQ-011: Provide a first-party OAuth terminal architecture using Mission Control, OAuth Session API, MoonMind.OAuthSession, short-lived auth runner, PTY/WebSocket bridge, and xterm.js.
- DESIGN-REQ-012: Run a short-lived auth runner container that mounts the auth volume at the provider enrollment path and tears down on success, cancellation, expiry, or failure.
- DESIGN-REQ-014: Do not expose generic Docker exec access or ordinary task-run terminal attachment through the OAuth terminal bridge.
- DESIGN-REQ-020: Preserve ownership boundaries among OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration.

Relevant Implementation Notes
- The auth runner container is short-lived and scoped to one OAuth session.
- For Codex, the auth runner targets `codex_auth_volume` at `/home/app/.codex` while enrollment is happening.
- The OAuth terminal is only for credential enrollment or repair and must not become the runtime surface for managed Codex task execution.
- Later task-scoped Codex managed sessions target the registered provider profile and mount the auth volume separately when needed.

Needs Clarification
- None
```

**Implementation Intent**: Runtime implementation. Required deliverables include production behavior changes plus validation tests.

## User Story - OAuth Runner Bootstrap PTY

**Summary**: As a MoonMind operator, I want Codex OAuth enrollment to launch a short-lived, session-owned auth runner that executes the provider bootstrap command in an interactive terminal so that credential enrollment is first-party and no longer depends on placeholder container behavior.

**Goal**: Operators can start Codex OAuth enrollment and observe that the selected auth volume is mounted for enrollment, the provider bootstrap command owns the interactive terminal session, cleanup is reliable across terminal outcomes, and failures are actionable without exposing secrets.

**Independent Test**: Start a Codex OAuth session using a fake provider bootstrap command, complete or terminate the session through success, failure, expiry, and cancellation paths, and verify volume targeting, command execution ownership, bridge-only access, idempotent cleanup, and redacted failure reporting.

**Acceptance Scenarios**:

1. **Given** an authorized operator starts a Codex OAuth enrollment session, **when** the auth runner starts, **then** the selected auth volume is mounted at the provider enrollment path for that session.
2. **Given** the selected provider profile defines a bootstrap command, **when** the auth runner becomes interactive, **then** the command runs in a terminal session owned by the OAuth enrollment session.
3. **Given** the auth runner is active, **when** terminal access is requested, **then** access is available only through MoonMind's authenticated terminal bridge and not through ordinary task terminal attachment or generic Docker exec.
4. **Given** the session succeeds, fails, expires, or is cancelled, **when** cleanup runs once or multiple times, **then** the runner stops and cleanup leaves a consistent terminal state.
5. **Given** the runner cannot start or the provider bootstrap command cannot run, **when** the OAuth session reports failure, **then** the operator receives an actionable, redacted reason.

### Edge Cases

- The selected auth volume is missing, unavailable, or cannot be mounted at the enrollment path.
- The provider registry has no bootstrap command or has a command that exits before terminal readiness.
- Runner startup succeeds but terminal bridge readiness never occurs before the session expires.
- The operator cancels while bootstrap command startup, terminal readiness, or credential verification is in progress.
- Cleanup is retried after partial runner startup, failed runner startup, or an already-stopped runner.
- Failure details include credential-like output that must not appear in workflow history, logs, artifacts, or browser responses.

## Assumptions

- MM-361 is a follow-up to the MM-358 OAuth terminal story and narrows scope to replacing the placeholder auth runner lifecycle with real provider bootstrap terminal ownership.
- Existing OAuth session authorization, profile registration, credential verification, and managed Codex task execution behavior remain in place unless needed to validate this runner lifecycle.
- Runtime validation can use deterministic fake provider bootstrap commands before live provider enrollment is exercised.

## Source Design Requirements

- **DESIGN-REQ-011**: Source section 5 requires first-party OAuth terminal enrollment to connect Mission Control, OAuth session control, a short-lived auth runner, a terminal bridge, provider login, mounted auth volume verification, and profile registration. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, and FR-011.
- **DESIGN-REQ-012**: Source section 5.1 requires the auth runner to be short-lived, scoped to one OAuth session, mounted to the provider enrollment path, connected only through the authenticated bridge, stopped after terminal outcomes, and leave credentials in the durable auth volume for later verification. Scope: in scope. Maps to FR-001, FR-002, FR-003, FR-006, FR-007, FR-008, FR-009, and FR-010.
- **DESIGN-REQ-014**: Source sections 5.2 and 11 forbid generic Docker exec access and ordinary task-run terminal attachment through the OAuth terminal bridge. Scope: in scope. Maps to FR-004, FR-005, and FR-010.
- **DESIGN-REQ-020**: Source section 11 requires OAuth terminal code, Provider Profile code, managed-session controller code, Codex session runtime code, and Docker workload orchestration to keep their ownership boundaries separate. Scope: in scope. Maps to FR-010, FR-011, FR-012, and FR-013.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST start a short-lived auth runner for an authorized Codex OAuth enrollment session.
- **FR-002**: The auth runner MUST mount the selected auth volume at the provider enrollment path for the duration of enrollment.
- **FR-003**: The auth runner MUST execute the selected provider bootstrap command in a terminal session owned by the OAuth enrollment session.
- **FR-004**: Terminal input and output for the runner MUST be reachable only through MoonMind's authenticated OAuth terminal bridge.
- **FR-005**: System MUST reject or omit ordinary managed task terminal attachment for OAuth runner sessions.
- **FR-006**: System MUST stop the auth runner after success, failure, expiry, or cancellation.
- **FR-007**: Runner cleanup MUST be idempotent across repeated cleanup requests and partially-started runner states.
- **FR-008**: Runner startup failures MUST produce actionable, redacted failure reasons for operators.
- **FR-009**: Provider bootstrap command failures MUST produce actionable, redacted failure reasons for operators.
- **FR-010**: System MUST NOT expose generic Docker exec access through the OAuth terminal bridge.
- **FR-011**: Runtime records MUST keep OAuth enrollment terminal evidence separate from managed Codex task execution evidence.
- **FR-012**: Moon Spec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key MM-361 and the original preset brief as traceability evidence.
- **FR-013**: System MUST keep raw credential contents out of workflow history, logs, artifacts, and browser-visible failure responses during runner startup, execution, and cleanup.

### Key Entities

- **OAuth Enrollment Session**: Operator-started credential enrollment flow scoped to an auth volume, provider profile target, runner lifecycle, terminal state, and terminal outcome.
- **Auth Runner**: Short-lived enrollment runner scoped to one OAuth session that mounts the selected auth volume, runs the provider bootstrap command, and stops after a terminal outcome.
- **Provider Bootstrap Command**: Provider-defined enrollment command that creates or repairs credentials in the selected auth volume.
- **OAuth Terminal Bridge**: Authenticated terminal transport that mediates browser input and output for the runner without exposing generic exec or ordinary task terminal access.
- **Runner Outcome**: Secret-free terminal result describing success, failure, expiry, cancellation, cleanup status, and redacted operator-facing reason when needed.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove authorized Codex OAuth enrollment starts exactly one session-scoped auth runner with the selected auth volume mounted at the provider enrollment path.
- **SC-002**: Unit tests prove provider bootstrap command execution is represented as session-owned terminal behavior rather than placeholder sleep behavior.
- **SC-003**: Unit tests prove success, failure, expiry, and cancellation each stop the auth runner and repeated cleanup leaves the same terminal runner state.
- **SC-004**: Unit tests prove missing runner dependencies, mount failures, startup failures, and bootstrap command failures return actionable redacted reasons.
- **SC-005**: Boundary tests prove OAuth runner terminal access is only available through the authenticated OAuth terminal bridge and not through ordinary managed task terminal attachment or generic Docker exec.
- **SC-006**: Verification evidence confirms MM-361 and the original preset brief are preserved in the active Moon Spec artifacts and delivery metadata.
