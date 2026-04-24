# Feature Specification: Remediation Lifecycle Audit

**Feature Branch**: `232-remediation-lifecycle-audit`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-456 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `spec.md` (Input)

## Original Preset Brief

```text
# MM-456 MoonSpec Orchestration Input

## Source

- Jira issue: MM-456
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Publish remediation lifecycle phases, artifacts, summaries, and audit events
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-456 from MM project
Summary: Publish remediation lifecycle phases, artifacts, summaries, and audit events
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-456 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-456: Publish remediation lifecycle phases, artifacts, summaries, and audit events

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 13. Runtime lifecycle
  - 14. Artifacts, summaries, and audit
  - 16. Failure modes and edge cases
- Coverage IDs:
  - DESIGN-REQ-017
  - DESIGN-REQ-018
  - DESIGN-REQ-019
  - DESIGN-REQ-022
  - DESIGN-REQ-023

User Story
As an operator or reviewer, I can inspect a remediation run from evidence collection through verification because each phase, decision, action, result, and final outcome leaves durable artifacts and queryable audit evidence.

Acceptance Criteria
- remediationPhase values reflect collecting_evidence, diagnosing, awaiting_approval, acting, verifying, resolved, escalated, or failed as the run progresses.
- Required remediation artifacts are published with expected artifact_type values and obey artifact preview/redaction metadata rules.
- run_summary.json includes the remediation block with target identity, mode, authorityMode, actionsAttempted, resolution, lockConflicts, approvalCount, evidenceDegraded, and escalated fields.
- Target-managed session or workload mutations continue to produce native continuity/control artifacts in addition to remediation audit artifacts.
- Control-plane audit events record actor, execution principal, remediation workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
- Cancellation or remediation failure does not mutate the target except for already-requested actions and attempts final summary publication and lock release.
- Continue-As-New preserves target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.

Requirements
- Existing MoonMind.Run state remains the top-level state source.
- Artifacts remain operator-facing deep evidence; audit rows remain compact queryable trail.
- Target-side linkage summary metadata is available for downstream detail views.

Relevant Implementation Notes
- Preserve MM-456 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation runtime lifecycle phases, artifact publication, final summaries, audit events, cancellation behavior, failure handling, and Continue-As-New preservation.
- Keep MoonMind.Run as the top-level execution state source; remediation-specific phase values should complement that state rather than replace it.
- Publish remediation artifacts with bounded metadata and existing artifact preview/redaction rules.
- Ensure final run summaries include the remediation block with target identity, mode, authority mode, action attempts, resolution, lock conflicts, approval count, degraded evidence, and escalation state.
- Preserve native managed-session or workload continuity/control artifacts when remediation mutates those targets.
- Record compact queryable audit events for remediation actions, approvals, risk tiers, actors, execution principals, target workflow/run identity, timestamps, and bounded metadata.
- On cancellation or remediation failure, avoid new target mutation except for already-requested actions; still attempt final summary publication and lock release.
- Preserve target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor across Continue-As-New.

Non-Goals
- Replacing MoonMind.Run as the top-level state source.
- Treating audit rows as a replacement for operator-facing artifact evidence.
- Dropping native target continuity or control artifacts when remediation touches managed sessions or workloads.
- Mutating targets after cancellation or failure except for already-requested actions.
- Losing remediation context across Continue-As-New.

Validation
- Verify remediationPhase values reflect collecting_evidence, diagnosing, awaiting_approval, acting, verifying, resolved, escalated, or failed as the run progresses.
- Verify required remediation artifacts are published with expected artifact_type values and obey artifact preview/redaction metadata rules.
- Verify run_summary.json includes the remediation block with target identity, mode, authorityMode, actionsAttempted, resolution, lockConflicts, approvalCount, evidenceDegraded, and escalated fields.
- Verify target-managed session or workload mutations continue to produce native continuity/control artifacts in addition to remediation audit artifacts.
- Verify control-plane audit events record actor, execution principal, remediation workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
- Verify cancellation or remediation failure does not mutate the target except for already-requested actions and attempts final summary publication and lock release.
- Verify Continue-As-New preserves target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-456 blocks MM-455, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-456 is blocked by MM-457, whose embedded status is Backlog.

Needs Clarification
- None
```

## User Story - Inspect Remediation Lifecycle Evidence

**Summary**: As an operator or reviewer, I want each remediation run to expose its current lifecycle phase, durable remediation artifacts, final summary, target-side continuity evidence, and compact audit trail so that I can understand what happened from evidence collection through verification.

**Goal**: A remediation run produces a coherent operator-visible record that shows phase progression, decisions, actions, results, final outcome, degraded evidence, cancellation/failure handling, and preserved context after continuation.

**Independent Test**: Run a remediation execution through evidence collection, diagnosis, approval or action, verification, and terminal outcome; then inspect its run summary, remediation artifacts, target-side artifacts, and audit evidence to confirm every required phase, artifact class, summary field, and bounded audit field is present.

**Acceptance Scenarios**:

1. **Given** a remediation run progresses through its lifecycle, **When** operators inspect the run, **Then** the remediation-specific phase reflects collecting_evidence, diagnosing, awaiting_approval, acting, verifying, resolved, escalated, or failed without replacing the top-level run state.
2. **Given** a remediation run produces evidence, plan, decision, action, verification, and summary outputs, **When** artifacts are listed or previewed, **Then** each required remediation artifact uses the expected artifact type and obeys preview and redaction rules.
3. **Given** a remediation run reaches a terminal outcome, **When** its run summary is inspected, **Then** the remediation block includes target identity, mode, authority mode, actions attempted, resolution, lock conflicts, approval count, degraded evidence, and escalation state.
4. **Given** a remediation action mutates a managed session or workload target, **When** the target-side evidence is inspected, **Then** subsystem-native continuity or control artifacts are still present alongside remediation audit evidence.
5. **Given** a remediation action, approval, or risk decision occurs, **When** the audit trail is queried, **Then** compact events identify the actor, execution principal, remediation workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
6. **Given** a remediation task is canceled or fails, **When** the run terminates, **Then** it avoids new target mutation except already-requested actions and still attempts final summary publication and lock release.
7. **Given** a remediation task continues as new, **When** the continued run is inspected, **Then** target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor are preserved.

### Edge Cases

- Historical targets may have only merged logs; the remediation record must show degraded evidence instead of hiding the limitation.
- Missing or partial artifact refs may still allow safe diagnosis; unavailable evidence classes must be recorded explicitly.
- Live follow may be unavailable; durable logs, diagnostics, summaries, and artifacts remain the fallback evidence.
- Target reruns during remediation preserve the pinned snapshot and record any intentionally resulting run.
- A failed remediator still publishes a bounded summary and releases locks where possible; automatic remediation of the remediator remains off by default.

## Assumptions

- The MM-456 brief is a single runtime feature slice focused on lifecycle observability and auditability for remediation runs, not on creating remediation tasks, defining authority modes, or adding new action kinds.
- Existing remediation create/link, evidence/context, authority, action registry, and mutation guard stories provide surrounding behavior; this story makes the lifecycle and evidence record complete and inspectable.
- The source implementation document `docs/Tasks/TaskRemediation.md` is treated as runtime source requirements because the selected mode is runtime.

## Source Design Requirements

- **DESIGN-REQ-017** (`docs/Tasks/TaskRemediation.md` section 13): Remediation must expose bounded phase progression values while preserving existing top-level run state, recommended lifecycle flow, cancellation semantics, rerun semantics, and Continue-As-New safety. Scope: in scope, mapped to FR-001 through FR-007 and FR-024.
- **DESIGN-REQ-018** (`docs/Tasks/TaskRemediation.md` sections 14.1 through 14.3): Remediation must publish required context, plan, decision log, action request/result, verification, and summary artifacts with bounded metadata and a stable remediation summary block. Scope: in scope, mapped to FR-008 through FR-016.
- **DESIGN-REQ-019** (`docs/Tasks/TaskRemediation.md` sections 14.2, 14.4, and 14.5): Target-side continuity artifacts, target-side linkage metadata, and compact control-plane audit events must remain available in addition to deep remediation artifacts. Scope: in scope, mapped to FR-017 through FR-023.
- **DESIGN-REQ-022** (`docs/Tasks/TaskRemediation.md` sections 13.5, 13.6, 16.2, 16.7, and 16.11): Reruns, precondition changes, continuation, and remediator failure must preserve target identity and produce explicit recorded outcomes rather than silent retargeting or silent success. Scope: in scope, mapped to FR-005 through FR-007, FR-014, FR-024, FR-026, and FR-027.
- **DESIGN-REQ-023** (`docs/Tasks/TaskRemediation.md` sections 16.1 through 16.11): Missing targets, partial evidence, live-follow unavailability, lock conflicts, stale leases, gone containers, unsafe forced termination, and remediator failure must degrade, no-op, escalate, fail, or deny with bounded reasons. Scope: in scope, mapped to FR-014, FR-025, and FR-027 through FR-030.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose a remediation-specific phase for remediation runs without replacing the existing top-level run state.
- **FR-002**: The remediation-specific phase MUST use bounded values covering collecting evidence, diagnosing, awaiting approval, acting, verifying, resolved, escalated, and failed.
- **FR-003**: Operators MUST be able to inspect phase progression from evidence collection through terminal outcome.
- **FR-004**: Remediation lifecycle evidence MUST distinguish resolved, escalated, unsafe-to-act, evidence-unavailable, lock-conflict, and failed outcomes.
- **FR-005**: Canceling a remediation task MUST NOT mutate the target except for actions already requested before cancellation.
- **FR-006**: A canceled or failed remediation task MUST attempt final remediation summary publication and lock release.
- **FR-007**: A remediation task that continues as new MUST preserve target workflow identity, pinned target run, remediation context ref, acquired lock identity, action ledger, approval state, retry budget, and last live-follow cursor.
- **FR-008**: The system MUST publish a remediation context artifact for each remediation run that reaches evidence collection.
- **FR-009**: The system MUST publish a remediation plan artifact when a remediation plan is selected or proposed.
- **FR-010**: The system MUST publish a remediation decision log that records bounded decision entries.
- **FR-011**: The system MUST publish remediation action request artifacts for requested actions.
- **FR-012**: The system MUST publish remediation action result artifacts for attempted actions.
- **FR-013**: The system MUST publish remediation verification artifacts for verification attempts.
- **FR-014**: The system MUST publish a final remediation summary artifact for resolved, escalated, failed, canceled, evidence-degraded, or no-op outcomes when publication is possible.
- **FR-015**: Remediation artifacts MUST obey normal artifact presentation safety, including bounded metadata, artifact refs rather than access URLs, correct preview/redaction behavior, and no secrets in metadata or bodies.
- **FR-016**: The remediation run summary MUST include target workflow ID, target run ID, mode, authority mode, attempted actions, resolution, lock conflict count, approval count, evidence degraded flag, and escalation flag.
- **FR-017**: When remediation mutates a managed session or workload target, the target side MUST continue to produce subsystem-native continuity or control artifacts.
- **FR-018**: Remediation evidence MUST add a parallel remediation audit trail rather than replacing subsystem-native target artifacts.
- **FR-019**: Target execution detail views or read models MUST expose inbound remediation metadata including active remediation count, latest remediation title, latest status, latest action kind, and last updated time.
- **FR-020**: The control-plane audit trail MUST record remediation action and approval events with actor user, execution principal, remediation workflow/run, target workflow/run, action kind, risk tier, approval decision, timestamps, and bounded metadata.
- **FR-021**: Audit evidence MUST remain compact and queryable while deep operator-facing evidence remains artifact-backed.
- **FR-022**: Audit event metadata MUST remain bounded and MUST NOT contain secrets, raw access grants, storage keys, or unbounded log bodies.
- **FR-023**: Target-side linkage summary metadata MUST be available for downstream detail views without requiring consumers to parse deep artifact bodies.
- **FR-024**: If a target changes runs while remediation is active, the system MUST preserve the pinned diagnosis snapshot and record any intentionally resulting run when known.
- **FR-025**: Missing target visibility, partial artifact refs, unavailable live follow, lock conflicts, failed preconditions, stale leases, missing containers, unsafe forced termination, and remediator failure MUST produce explicit bounded outcomes.
- **FR-026**: If a precondition no longer holds, the system MUST record a no-op or precondition-failed outcome rather than silent success.
- **FR-027**: If the remediation task itself fails, the system MUST publish a final remediation summary and release locks where possible, while automatic remediation of the remediator remains disabled by default.
- **FR-028**: Historical targets with only merged logs MUST be diagnosable when safe and MUST set degraded evidence in the remediation record.
- **FR-029**: Missing or partial artifact refs MUST identify which evidence classes are unavailable.
- **FR-030**: Live-follow unavailability MUST fall back to durable logs, diagnostics, summaries, and artifacts.
- **FR-031**: Remediation lifecycle, artifact, summary, and audit evidence MUST preserve MM-456 traceability through spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **Remediation Phase**: A bounded remediation-specific lifecycle marker exposed inside summaries or read models while top-level run state remains authoritative.
- **Remediation Artifact Set**: The durable operator-facing evidence for context, plan, decisions, action requests, action results, verification, and final summary.
- **Remediation Summary Block**: The compact run-summary section containing target identity, mode, authority, attempted actions, resolution, lock conflicts, approvals, degraded evidence, and escalation state.
- **Target-Side Linkage Summary**: The compact metadata that lets target detail views show inbound remediation status without parsing deep artifacts.
- **Control-Plane Audit Event**: A compact queryable record of remediation actions, approvals, risk decisions, principals, targets, timestamps, and bounded metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove remediation runs expose only the allowed remediation phase values and do not replace top-level run state.
- **SC-002**: Tests prove required remediation artifacts are published with expected artifact type values and artifact presentation safety metadata.
- **SC-003**: Tests prove the remediation summary block includes all required target, mode, action, resolution, lock, approval, evidence, and escalation fields.
- **SC-004**: Tests prove target-side continuity or control artifacts remain present when remediation mutates managed sessions or workloads.
- **SC-005**: Tests prove control-plane audit events include required actor, principal, workflow/run, action, risk, approval, timestamp, and bounded metadata fields.
- **SC-006**: Tests prove cancellation and remediator failure avoid new target mutation except already-requested actions and still attempt final summary publication and lock release.
- **SC-007**: Tests prove Continue-As-New preserves target identity, pinned run, context ref, lock identity, action ledger, approval state, retry budget, and live-follow cursor.
- **SC-008**: Tests prove degraded evidence, partial artifacts, live-follow unavailability, precondition failure, and target rerun cases produce explicit bounded outcomes.
