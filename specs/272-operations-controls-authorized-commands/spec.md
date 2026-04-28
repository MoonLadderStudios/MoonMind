# Feature Specification: Operations Controls Exposed as Authorized Commands

**Feature Branch**: `272-operations-controls-authorized-commands`
**Created**: 2026-04-28
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-542 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-542 MoonSpec Orchestration Input

## Source

- Jira issue: MM-542
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Operations controls exposed as authorized commands
- Trusted fetch tool: `jira.get_issue`
- Canonical source: synthesized from trusted Jira issue fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-542 from MM project
Summary: Operations controls exposed as authorized commands
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-542 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-542: Operations controls exposed as authorized commands

Source Reference
Source Document: docs/Security/SettingsSystem.md
Source Title: Settings System
Source Sections:
- 2.4 What Operations own
- 6.3 Operations
- 17. Operations Settings
- 20. Authorization Model
- 27.4 Pause Workers
Coverage IDs:
- DESIGN-REQ-002
- DESIGN-REQ-013
- DESIGN-REQ-014

As an operator, I can invoke operational controls from Settings with current state, impact, confirmation, authorization, audit trail, and rollback or resume feedback instead of editing ordinary preferences.

Acceptance Criteria
- Operational controls show current state, command impact, confirmation requirements, actor authorization, last action and actor, pending transitions, failure reason, and safe rollback/resume action where available.
- Actions that stop active work, prevent launches, affect all workers/runtimes, delete data, revoke credentials, or change global routing require confirmation.
- Operations commands include actor, target, requested state, reason, confirmation state, timestamp, idempotency key, audit event, result status, and rollback/resume path where possible.
- Unauthorized users cannot invoke operations even if frontend controls are hidden or manipulated.
- Operational subsystems remain authoritative for worker, queue, scheduler, health, and destructive/disruptive semantics.

Requirements
- Settings exposes Operations for discoverability only; Operations remains the semantic owner of command effects.
- Operations controls are auditable actions, not mutable preferences.
- The UI must communicate expected effect before the command is submitted.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-542 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

- Input type: Single-story runtime feature request.
- Breakdown decision: `moonspec-breakdown` was not run because the Jira preset brief defines one independently testable Operations story.
- Selected mode: Runtime.
- Source design: `docs/Security/SettingsSystem.md` is treated as runtime source requirements.
- Resume decision: No existing Moon Spec artifacts for `MM-542` were found under `specs/`; specification is the first incomplete stage.
- Multi-spec ordering: Not applicable for `MM-542`.

## User Story - Invoke Authorized Operations Commands

**Summary**: As an operator, I can invoke operational controls from Settings with current state, impact, confirmation, authorization, audit trail, and rollback or resume feedback instead of editing ordinary preferences.

**Goal**: MoonMind lets authorized operators discover and invoke operational controls from Settings while preserving operational subsystems as the authority for command effects, status, auditability, and rollback or resume semantics.

**Independent Test**: Open Settings -> Operations as an authorized operator, inspect an operational control such as Pause Workers, verify current state and expected impact are shown, submit the command with required confirmation and reason, confirm the backend authorizes and records the command as an operation, and verify the UI shows result status, audit metadata, and a safe resume or rollback action when available. Repeat as an unauthorized user and verify the command is rejected even if frontend controls are manipulated.

### Acceptance Scenarios

1. Given an authorized operator opens Settings -> Operations, when operational controls are listed, then each control shows current state, command impact, confirmation requirement, actor authorization state, last action and actor, pending transition state, failure reason when present, and rollback or resume action where available.
2. Given an operational action can stop active work, prevent new launches, affect all workers or runtimes, delete data, revoke credentials, or change global routing, when the operator attempts to invoke it, then the UI requires explicit confirmation before submitting the command.
3. Given an authorized operator submits an operation command, when the request reaches the backend, then the command records actor, target, requested state, reason, confirmation state, timestamp, idempotency key, audit event, result status, and rollback or resume path where possible.
4. Given a user without operation permission manipulates the frontend or calls the operation surface directly, when they attempt to invoke an operation, then the backend rejects the command and no operation side effect is applied.
5. Given worker, queue, scheduler, runtime health, destructive, or disruptive operations are exposed in Settings, when an operator invokes them, then the corresponding operational subsystem remains the semantic authority for command behavior and status.
6. Given an operation command succeeds, fails, or remains pending, when Settings refreshes the control, then the operator can see the current result, last actor, failure reason when present, and the next safe rollback or resume action where available.

### Edge Cases

- An operation status refresh fails or returns stale state while a command is pending.
- A duplicate command is submitted with the same idempotency key.
- An operation is authorized for viewing but not invocation.
- An operation succeeds but no rollback or resume path is available.
- A command's target subsystem returns a validation failure, conflict, or unavailable state.

## Requirements

### Functional Requirements

- **FR-001**: Settings MUST expose an Operations surface for discoverability while treating operation controls as explicit commands or status-backed controls rather than ordinary mutable preferences.
- **FR-002**: Each exposed operation control MUST show current state, expected command impact, confirmation requirements, actor authorization state, last action and actor, pending transition state, failure reason when present, and rollback or resume action where available.
- **FR-003**: Operations that stop active work, prevent launches, affect all workers or runtimes, delete data, revoke credentials, or change global routing MUST require explicit confirmation before invocation.
- **FR-004**: Operation command submissions MUST include actor, target, requested state, reason, confirmation state, timestamp, idempotency key, audit event, result status, and rollback or resume path where possible.
- **FR-005**: Backend authorization MUST be enforced for every operation invocation; hiding or disabling frontend controls MUST NOT be the only protection.
- **FR-006**: Unauthorized operation attempts MUST be rejected without invoking the underlying operational subsystem or recording a successful operation result.
- **FR-007**: Worker, queue, scheduler, runtime health, destructive, and disruptive operation effects MUST remain owned by their operational subsystems; Settings MUST only present and invoke the authorized command surface.
- **FR-008**: Operation result status MUST distinguish pending, succeeded, failed, unauthorized, conflicted, and unavailable outcomes with sanitized operator-visible diagnostics.
- **FR-009**: Operation history or audit metadata exposed in Settings MUST identify actor, target, reason, timestamp, command outcome, and affected system without leaking credentials or secret values.
- **FR-010**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-542` and this canonical Jira preset brief.

### Key Entities

- **OperationControl**: A Settings-visible operational action or statusful control that exposes current state, expected impact, confirmation requirements, authorization state, last action, pending transition, failure reason, and rollback or resume affordance.
- **OperationCommand**: An auditable request to an operational subsystem containing actor, target, requested state, reason, confirmation state, timestamp, idempotency key, and command metadata.
- **OperationResult**: The outcome of an operation command, including pending, succeeded, failed, unauthorized, conflicted, or unavailable status plus sanitized diagnostics and safe next actions where available.
- **OperationAuditEvent**: A durable audit record that captures who invoked the operation, what target was affected, why it was requested, when it occurred, and what result was produced.

## Assumptions

- Existing operational subsystems already own at least one command or status boundary that Settings can invoke or adapt for this story.
- This story introduces or extends the Settings -> Operations presentation and command invocation path, not unrelated operational semantics.
- Role and permission names may reuse the desired-state `operations.read` and `operations.invoke` concepts from the source design where the existing authorization layer supports them.

## Source Design Requirements

| ID | Source | Requirement Summary | Scope | Mapped Requirements |
| --- | --- | --- | --- | --- |
| DESIGN-REQ-002 | `docs/Security/SettingsSystem.md` section 2.4 | Operational subsystems own worker pause, drain, quiesce, resume, runtime health, queue state, scheduler state, and destructive or disruptive operational semantics; Settings may expose controls but must treat them as explicit commands with status feedback. | In scope | FR-001, FR-007 |
| DESIGN-REQ-013 | `docs/Security/SettingsSystem.md` sections 6.3 and 17 | Operations controls must show current state, authorization, expected effect, audit trail, command metadata, result status, and rollback or resume path where possible, and disruptive actions require confirmation. | In scope | FR-002, FR-003, FR-004, FR-008, FR-009 |
| DESIGN-REQ-014 | `docs/Security/SettingsSystem.md` sections 20 and 27.4 | Operation invocation requires backend authorization and the Pause Workers flow validates permission, confirmation, subsystem invocation, status recording, audit event, and resume action. | In scope | FR-005, FR-006, FR-007, FR-009 |

## Success Criteria

- **SC-001**: Authorized operators can view Settings -> Operations controls with current state, expected impact, confirmation requirements, authorization state, last action, pending transition, failure reason, and rollback or resume action where available.
- **SC-002**: Disruptive operation controls cannot be submitted without explicit confirmation and reason capture where the command requires a reason.
- **SC-003**: Backend tests prove unauthorized direct operation submissions are rejected without invoking the underlying operational subsystem.
- **SC-004**: Command submissions produce auditable operation records containing actor, target, requested state, reason, confirmation state, timestamp, idempotency key, result status, and rollback or resume metadata where available.
- **SC-005**: Settings invokes operational subsystem command surfaces instead of implementing worker, queue, scheduler, runtime health, destructive, or disruptive semantics itself.
- **SC-006**: Verification evidence preserves `MM-542`, DESIGN-REQ-002, DESIGN-REQ-013, and DESIGN-REQ-014 across MoonSpec artifacts.
