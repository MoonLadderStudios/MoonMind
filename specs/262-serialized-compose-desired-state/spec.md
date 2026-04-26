# Feature Specification: Serialized Compose Desired-State Execution

**Feature Branch**: `262-serialized-compose-desired-state`
**Created**: 2026-04-26
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-520 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-520 MoonSpec Orchestration Input

## Source

- Jira issue: MM-520
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Serialized Compose desired-state execution
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-520 from MM project
Summary: Serialized Compose desired-state execution
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-520 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-520: Serialized Compose desired-state execution

Source Reference
Source Document: docs/Tools/DockerComposeUpdateSystem.md
Source Title: Docker Compose Deployment Update System
Source Sections:
- 9. Desired state storage
- 10. Execution lifecycle
- 11. Updater runner execution model
Coverage IDs:
- DESIGN-REQ-007
- DESIGN-REQ-008
- DESIGN-REQ-009
- DESIGN-REQ-010
- DESIGN-REQ-011

As a deployment administrator, I need MoonMind to persist the requested image and run the policy-controlled Compose update lifecycle under a per-stack lock, so a requested update survives restarts and cannot race with another update.

Acceptance Criteria
- A second update for the same stack is rejected with DEPLOYMENT_LOCKED or queued only according to explicit policy.
- Before-state capture occurs before desired-state persistence.
- The desired image is persisted before Compose up is invoked.
- changed_services mode runs the documented pull/up behavior without force-recreate.
- force_recreate mode adds force-recreate behavior only when policy permits it.
- removeOrphans and wait options adjust command construction only through recognized policy-controlled flags.
- The executor never edits arbitrary caller-selected files and never accepts caller-selected updater runner images.
- The privileged Docker access path is restricted to deployment-control infrastructure.

Requirements
- Serialize update runs per stack.
- Persist desired target state durably before service recreation.
- Perform Compose command construction from typed inputs and policy only.
- Support both privileged worker and ephemeral updater container as implementation modes.
"""

<!-- Moon Spec specs contain exactly one independently testable user story. Use /speckit.breakdown for technical designs that contain multiple stories. -->

## User Story - Serialized Compose Desired-State Execution

**Summary**: As a deployment administrator, I need MoonMind to persist the requested image and run the policy-controlled Compose update lifecycle under a per-stack lock, so a requested update survives restarts and cannot race with another update.

**Goal**: Deployment update execution serializes updates per stack, records the desired image before service recreation, and executes only policy-controlled Compose command shapes through deployment-control infrastructure.

**Independent Test**: Can be fully tested by invoking the typed deployment update handler with a fake desired-state store, artifact writer, and Compose runner, then asserting lock behavior, operation order, persisted desired state, command construction, and verification result semantics without mutating a real Docker host.

**Acceptance Scenarios**:

1. **Given** one update is already running for a stack, **When** another update for the same stack starts and policy does not queue concurrent work, **Then** the second update fails before command execution with `DEPLOYMENT_LOCKED`.
2. **Given** a valid deployment update request, **When** the executor starts the lifecycle, **Then** it captures before state before persisting desired state and persists the requested image before any Compose `up` command is invoked.
3. **Given** `mode = changed_services`, **When** the executor constructs Compose commands, **Then** it runs pull followed by `up -d` without force-recreate behavior.
4. **Given** `mode = force_recreate` and policy permits force recreation, **When** the executor constructs Compose commands, **Then** it adds force-recreate behavior only for that mode.
5. **Given** policy-controlled `removeOrphans` or `wait` options are disabled or false, **When** commands are constructed, **Then** only recognized flags are omitted and no caller-selected arbitrary flags are accepted.
6. **Given** verification cannot prove the requested desired state, **When** the lifecycle completes command execution, **Then** the run is not marked `SUCCEEDED` and includes verification evidence.
7. **Given** the executor is invoked, **When** runner infrastructure is selected, **Then** the selection is limited to deployment-controlled modes and never accepts caller-selected runner images or arbitrary files.

### Edge Cases

- Mutable image references preserve the requested reference and optional resolved digest as distinct desired-state values.
- Lock acquisition failure happens before state capture, persistence, pull, or up commands.
- Verification failure after services start still produces after-state and verification artifacts.
- Unsupported mode, unrecognized option values, or unsupported runner mode fails before any side effect.
- Desired-state persistence failure prevents Compose pull or up from running.

## Assumptions

- MM-518 owns API authorization and policy validation before the tool is queued; MM-519 owns the typed tool contract and plan validation. This story owns execution ordering, locking, desired-state persistence, and command construction after the typed tool is invoked.
- The first runtime implementation uses injectable store, artifact, and Compose runner boundaries so tests remain hermetic and privileged Docker execution can be provided only by deployment-control infrastructure.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST serialize deployment update execution per stack so only one update for a stack can run at a time unless an explicit queueing policy is added.
- **FR-002**: System MUST return non-retryable `DEPLOYMENT_LOCKED` before any lifecycle side effect when a same-stack update is already running and queueing is not enabled.
- **FR-003**: System MUST capture before-state evidence before persisting the desired image.
- **FR-004**: System MUST persist the requested desired image state before invoking any Compose `up` operation.
- **FR-005**: Persisted desired state MUST include stack, image repository, requested reference, optional resolved digest, reason, created timestamp, and source run identifier when available.
- **FR-006**: `changed_services` mode MUST construct pull and up behavior without force-recreate.
- **FR-007**: `force_recreate` mode MUST add force-recreate behavior only when the selected mode is permitted.
- **FR-008**: `removeOrphans` and `wait` MUST influence command construction only through recognized policy-controlled flags.
- **FR-009**: System MUST capture command-log, after-state, and verification evidence as structured artifact references.
- **FR-010**: System MUST NOT mark an update `SUCCEEDED` when verification cannot prove the requested desired state.
- **FR-011**: System MUST restrict execution runner selection to deployment-controlled privileged worker or ephemeral updater container modes and MUST NOT accept caller-selected runner images or arbitrary files.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-520` and the canonical Jira preset brief.

### Key Entities

- **Deployment Desired State**: Durable record of the requested stack image target and audit metadata that survives process restarts.
- **Deployment Update Lifecycle**: Ordered execution flow containing lock acquisition, before-state capture, desired-state persistence, pull, up, verification, after-state capture, lock release, and structured result reporting.
- **Deployment Runner Mode**: Deployment-controlled execution boundary for Compose commands, either privileged worker or ephemeral updater container.
- **Deployment Evidence Artifact**: Structured reference to before-state, after-state, command-log, or verification evidence.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tools/DockerComposeUpdateSystem.md` section 9.2 requires the requested target image to be persisted before Compose is brought up and to include stack, image repository, requested reference, resolved digest when available, operator, reason, timestamp, and source run ID. Scope: in scope. Maps to FR-004, FR-005.
- **DESIGN-REQ-002**: Source section 10.2 requires a per-stack deployment lock and `DEPLOYMENT_LOCKED` or explicit queueing policy when another update is running. Scope: in scope. Maps to FR-001, FR-002.
- **DESIGN-REQ-003**: Source sections 10.3 and 10.4 require before-state capture before desired image persistence and restrict persistence to an allowlisted env file or equivalent deployment-state store. Scope: in scope. Maps to FR-003, FR-004, FR-011.
- **DESIGN-REQ-004**: Source sections 10.5 and 10.6 require pull before recreate; `changed_services` uses pull/up without force-recreate, while `force_recreate` adds force-recreate only for that mode, and `removeOrphans` / `wait` adjust recognized flags. Scope: in scope. Maps to FR-006, FR-007, FR-008.
- **DESIGN-REQ-005**: Source sections 10.7 through 10.9 require verification, after-state capture, structured result fields, and failure when desired-state verification cannot prove success. Scope: in scope. Maps to FR-009, FR-010.
- **DESIGN-REQ-006**: Source section 11 requires execution through deployment-controlled privileged worker or ephemeral updater container modes, with runner image policy controlled by deployment configuration rather than operator input. Scope: in scope. Maps to FR-011.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Same-stack concurrent execution is rejected with `DEPLOYMENT_LOCKED` before any fake runner side effect in tests.
- **SC-002**: Test evidence proves before-state capture precedes desired-state persistence and desired-state persistence precedes Compose `up`.
- **SC-003**: Test evidence proves `changed_services` commands omit force-recreate and `force_recreate` commands include it only for that mode.
- **SC-004**: Test evidence proves `removeOrphans` and `wait` only add or omit recognized flags.
- **SC-005**: Test evidence proves verification failure produces a non-`SUCCEEDED` result with verification artifact evidence.
- **SC-006**: Traceability evidence preserves `MM-520`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-006 in MoonSpec artifacts.
