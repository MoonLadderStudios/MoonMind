# Feature Specification: Explicit Failure and Rollback Controls

**Feature Branch**: `265-explicit-failure-and-rollback-controls`
**Created**: 2026-04-26
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-523 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-523 MoonSpec Orchestration Input

## Source

- Jira issue: MM-523
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Explicit failure and rollback controls
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-523 from MM project
Summary: Explicit failure and rollback controls
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Tools/DockerComposeUpdateSystem.md
Source Title: Docker Compose Deployment Update System
Source Sections:
- 15. Failure and rollback semantics
- 4.2 Non-goals
- 20. Locked decisions

Coverage IDs:
- DESIGN-REQ-015
- DESIGN-REQ-018
- DESIGN-REQ-003
- DESIGN-REQ-013

As an operations administrator, I need failed updates and rollbacks to remain explicit audited actions, so partial deployment changes do not trigger hidden retries or silent rollbacks.

Acceptance Criteria
- Each documented failure class produces a clear failed or partially verified result and actionable failure reason.
- Deployment updates do not perform automatic multi-attempt retries by default.
- A rollback request is submitted as a normal deployment update to a previous image reference, with admin authorization, reason, confirmation, lock acquisition, before/after artifacts, and verification.
- The UI or API offers rollback only when before-state artifacts contain enough information to construct a safe target image reference.
- The system never silently rolls back after failure unless an explicit separate policy enables automatic rollback.
- Rollback and failure records remain visible in recent actions and audit output.

Requirements
- Keep retries and rollback operator-driven by default.
- Preserve the same security, audit, artifact, and verification requirements for rollback as for forward updates.
- Do not expand the feature into general GitOps, Kubernetes, or non-allowlisted stack management.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-523 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

Input classification: single-story feature request. The Jira brief selects one independently testable runtime behavior story from `docs/Tools/DockerComposeUpdateSystem.md`; it does not require `moonspec-breakdown`.

## User Story - Explicit Failure and Rollback Controls

**Summary**: As an operations administrator, I need failed updates and rollbacks to remain explicit audited actions, so partial deployment changes do not trigger hidden retries or silent rollbacks.

**Goal**: Deployment update execution reports failures clearly, keeps retries and rollbacks operator-driven by default, exposes rollback only when safe before-state evidence exists, and records rollback and failure outcomes as visible audited actions.

**Independent Test**: Exercise deployment update failure and rollback decision flows with controlled update outcomes and artifact evidence, then verify final statuses, retry behavior, rollback eligibility, required authorization inputs, audit records, and absence of silent rollback.

**Acceptance Scenarios**:

1. **Given** a deployment update hits a documented failure class, **When** the run completes, **Then** the result is failed or partially verified with a clear actionable failure reason.
2. **Given** a deployment update fails after partially changing services, **When** the failure is reported, **Then** no automatic multi-attempt retry is started by default.
3. **Given** an operator requests rollback to a previous image reference, **When** rollback is submitted, **Then** it follows the normal deployment update path with admin authorization, reason, confirmation, lock acquisition, before/after artifacts, and verification.
4. **Given** before-state artifacts contain enough information to construct a safe previous image target, **When** rollback options are shown, **Then** the rollback action is available with the safe target reference.
5. **Given** before-state artifacts do not contain enough safe target information, **When** rollback options are shown, **Then** the rollback action is unavailable.
6. **Given** a deployment update fails, **When** no explicit separate automatic rollback policy is enabled, **Then** the system does not silently roll back.
7. **Given** a rollback or failure occurs, **When** recent actions and audit output are inspected, **Then** the rollback or failure record remains visible with relevant outcome details.

### Edge Cases

- A rollback request without admin authorization, reason, confirmation, lock acquisition, artifacts, or verification fails closed before making deployment changes.
- A previous image reference that cannot be safely reconstructed from before-state evidence is not offered as a rollback target.
- Failure classes caused by invalid input, authorization, policy, unavailable lock, Compose validation, image pull, service recreation, or verification each produce a clear terminal result.
- Re-running a failed update is treated as a new explicit operator action through the audited deployment path.
- Non-allowlisted stacks, host paths, repositories, runner images, and arbitrary shell-style update targets remain outside rollback scope.

## Assumptions

- Earlier deployment-update stories own the base admin-only typed update flow, allowlist policy, desired-state persistence, and artifact capture. This story owns explicit failure behavior, retry defaults, rollback eligibility, rollback submission semantics, and audit visibility.
- Any future automatic rollback behavior requires a separate explicit policy and is out of scope for the default behavior in this story.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST produce a clear failed or partially verified result with an actionable failure reason for each documented deployment update failure class.
- **FR-002**: System MUST NOT perform automatic multi-attempt deployment update retries by default after a failure.
- **FR-003**: System MUST treat re-running a failed deployment update as an explicit operator action that follows the audited deployment update path.
- **FR-004**: System MUST treat rollback as an explicit deployment update to a previous image reference.
- **FR-005**: Rollback submission MUST require admin authorization, a reason, confirmation, deployment lock acquisition, before and after artifacts, and verification.
- **FR-006**: System MUST offer a rollback action only when before-state artifacts contain enough information to construct a safe previous image target.
- **FR-007**: System MUST withhold rollback actions when before-state evidence is missing, ambiguous, unsafe, or insufficient to construct the target image reference.
- **FR-008**: System MUST NOT silently roll back after failure unless a separately documented explicit automatic rollback policy enables that behavior.
- **FR-009**: System MUST keep rollback and failure records visible in recent actions and audit output.
- **FR-010**: System MUST keep rollback scope limited to the same allowlisted deployment update boundaries as forward updates and MUST NOT expand into general GitOps, Kubernetes, non-allowlisted stack management, or arbitrary shell execution.
- **FR-011**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-523` and the canonical Jira preset brief.

### Key Entities

- **Deployment Failure Result**: Terminal or partial outcome containing failure class, final status, actionable reason, and audit visibility metadata.
- **Rollback Eligibility Decision**: Determination of whether before-state evidence can safely produce a previous image target.
- **Rollback Request**: Explicit operator action containing authorization, reason, confirmation, target image reference, lock, artifacts, and verification outcome.
- **Deployment Audit Record**: Recent-action and audit-visible record for failure, retry-by-new-action, or rollback outcomes.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tools/DockerComposeUpdateSystem.md` section 15.1 requires fast failure on invalid input, authorization failure, policy violation, unavailable deployment lock, Compose config validation failure, image pull failure, service recreation failure, and verification failure. Scope: in scope. Maps to FR-001.
- **DESIGN-REQ-002**: Source section 15.2 requires deployment updates not to use automatic multi-attempt retries by default and requires re-running an update to be an explicit operator action through the same audited path. Scope: in scope. Maps to FR-002, FR-003.
- **DESIGN-REQ-003**: Source section 15.3 requires rollback to be an explicit deployment update to a previous image reference with admin authorization, reason, confirmation, deployment lock, before/after artifacts, and verification. Scope: in scope. Maps to FR-004, FR-005.
- **DESIGN-REQ-004**: Source section 15.3 requires rollback UI availability only when before-state artifacts can construct a safe target image reference and forbids silent rollback unless an explicit separate policy enables it. Scope: in scope. Maps to FR-006, FR-007, FR-008.
- **DESIGN-REQ-005**: Source section 4.2 excludes general Docker UI, general shell runner, non-admin Docker controls, arbitrary updater runner images, general GitOps or Kubernetes replacement, non-allowlisted stacks or host paths, deployment updates as agent instruction skills, and silent rollback without explicit auditable policy. Scope: in scope. Maps to FR-008, FR-010.
- **DESIGN-REQ-006**: Source section 20 locks decisions that stack names, Compose paths, image repositories, and runner images are allowlisted; arbitrary shell input is not accepted; desired image state is persisted before Compose is brought up; and before/after state plus command logs are written as artifacts. Scope: in scope. Maps to FR-005, FR-009, FR-010.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests cover all documented failure classes and verify each produces a failed or partially verified result with a non-empty actionable reason.
- **SC-002**: Tests prove a failed deployment update does not start any automatic second update attempt by default.
- **SC-003**: Tests prove an explicit retry follows the normal audited deployment update path as a distinct operator action.
- **SC-004**: Tests prove rollback submission is rejected unless authorization, reason, confirmation, lock, before/after artifacts, and verification are present.
- **SC-005**: Tests prove rollback is offered only when before-state evidence safely identifies a previous image target and is withheld otherwise.
- **SC-006**: Tests prove failure does not trigger silent rollback when no explicit automatic rollback policy is enabled.
- **SC-007**: Tests or verification evidence prove rollback and failure outcomes remain visible in recent actions and audit output.
- **SC-008**: Traceability evidence preserves `MM-523`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-006 in MoonSpec artifacts.
