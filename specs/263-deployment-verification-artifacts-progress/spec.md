# Feature Specification: Deployment Verification, Artifacts, and Progress

**Feature Branch**: `263-deployment-verification-artifacts-progress`
**Created**: 2026-04-26
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-521 as the canonical Moon Spec orchestration input.

Additional constraints:

Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-521 MoonSpec Orchestration Input

## Source

- Jira issue: MM-521
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Deployment verification, artifacts, and progress
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-521 from MM project
Summary: Deployment verification, artifacts, and progress
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Tools/DockerComposeUpdateSystem.md
Source Title: Docker Compose Deployment Update System
Source Sections:
- 12. Verification model
- 14. Audit and artifacts
- 19. Observability
Coverage IDs:
- DESIGN-REQ-012
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-017

As an administrator reviewing an update, I need MoonMind to verify the deployed state and preserve audit artifacts, so a run is only successful when the desired state is proven and the before/after evidence is durable.

Acceptance Criteria
- A run is marked SUCCEEDED only when expected services are running, health checks pass when present, image IDs match the requested target or resolved digest where applicable, requested smoke checks pass, and orphan expectations hold.
- If verification cannot prove the requested desired state, the final status is FAILED or PARTIALLY_VERIFIED, never SUCCEEDED.
- Every run writes beforeStateArtifactRef, commandLogArtifactRef, verificationArtifactRef, and afterStateArtifactRef.
- Audit output includes run/workflow/task IDs where applicable, stack, operator identity and role, reason, image request, resolved digest, mode, options, timestamps, final status, and failure reason when applicable.
- Secrets, auth tokens, registry credentials, and sensitive environment variables are redacted from artifacts and logs.
- Progress states include the documented lifecycle values with short messages; detailed command output remains in artifacts.

Requirements
- Make verification a success gate.
- Store durable audit and artifact evidence for every run.
- Redact sensitive values before artifact publication or UI display.
- Represent partial verification explicitly.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-521 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

## Classification

Input classification: single-story feature request. The Jira brief selects one independently testable runtime behavior story from `docs/Tools/DockerComposeUpdateSystem.md`; it does not require `moonspec-breakdown`.

## User Story - Deployment Verification Evidence

**Summary**: As an administrator reviewing an update, I need MoonMind to verify the deployed state and preserve audit artifacts, so a run is only successful when the desired state is proven and the before/after evidence is durable.

**Goal**: Deployment update execution reports success only after verification proves desired state, always preserves durable redacted evidence, emits audit metadata, represents partial verification explicitly, and exposes lifecycle progress states without leaking detailed command output into workflow state.

**Independent Test**: Invoke the typed deployment update executor with fake runner, artifact writer, and context, then assert success gating, failed and partially verified outcomes, required artifact refs, redacted artifact payloads, audit metadata, and progress lifecycle messages without touching a real Docker host.

**Acceptance Scenarios**:

1. **Given** verification proves services are running and expected image evidence matches, **When** the update completes, **Then** the result status is `SUCCEEDED` and required artifact refs are present.
2. **Given** verification cannot prove desired state, **When** the update completes, **Then** the result status is `FAILED` or `PARTIALLY_VERIFIED`, never `SUCCEEDED`, and verification evidence explains the failed checks.
3. **Given** a deployment update succeeds, fails verification, or partially verifies, **When** the run result is produced, **Then** before-state, command-log, verification, and after-state artifact refs are present whenever the lifecycle reached those phases.
4. **Given** audit context is available, **When** evidence is written, **Then** audit output includes run/workflow/task IDs where applicable, stack, operator identity and role, reason, image request, resolved digest, mode, options, timestamps, final status, and failure reason when applicable.
5. **Given** command output or captured state contains secret-like values, **When** artifacts are written, **Then** secrets, auth tokens, registry credentials, and sensitive environment values are redacted before publication.
6. **Given** a deployment update progresses through lifecycle phases, **When** progress is reported, **Then** documented progress states and short messages are exposed while detailed command output remains only in artifacts.

### Edge Cases

- Verification that reports a partial state uses `PARTIALLY_VERIFIED` instead of `FAILED` when enough structured evidence identifies partial completion.
- Missing after-state, command-log, or verification artifacts fail closed rather than claiming success.
- Secret-like values are redacted recursively from nested mappings and lists before artifact refs are generated.
- Progress reporting remains bounded to lifecycle state names and short messages even when runner command output is verbose.

## Assumptions

- MM-518 owns admin authorization and queuing, MM-519 owns the typed executable tool contract, and MM-520 owns locking, desired-state persistence, and command construction. This story owns final verification semantics, audit/evidence completeness, redaction, and progress reporting inside the deployment update executor.
- Verification remains hermetic in tests through the existing injectable runner boundary.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST mark a deployment update `SUCCEEDED` only when verification proves the requested desired state.
- **FR-002**: System MUST return `FAILED` or `PARTIALLY_VERIFIED`, never `SUCCEEDED`, when verification cannot prove the requested desired state.
- **FR-003**: System MUST support explicit `PARTIALLY_VERIFIED` verification results when verification evidence identifies partial completion.
- **FR-004**: System MUST write and return `beforeStateArtifactRef`, `commandLogArtifactRef`, `verificationArtifactRef`, and `afterStateArtifactRef` for every lifecycle that reaches the corresponding phases.
- **FR-005**: System MUST fail closed instead of returning success when required command-log, verification, or after-state evidence is missing.
- **FR-006**: Audit evidence MUST include run/workflow/task IDs when available, stack, operator identity and role, reason, image request, resolved digest, mode, options, timestamps, final status, and failure reason when applicable.
- **FR-007**: Artifact payloads and command logs MUST redact secrets, auth tokens, registry credentials, password-like values, and sensitive environment variables before publication.
- **FR-008**: Progress output MUST include documented lifecycle states with short messages and MUST NOT embed detailed command output.
- **FR-009**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-521` and the canonical Jira preset brief.

### Key Entities

- **Deployment Verification Result**: Structured verification outcome containing final status, updated services, running services, and check details.
- **Deployment Evidence Artifact**: Redacted durable artifact reference for before-state, command-log, verification, or after-state data.
- **Deployment Audit Metadata**: Operator and workflow context attached to deployment evidence and final result output.
- **Deployment Progress Event**: Bounded lifecycle state and short message suitable for workflow/UI display.

## Source Design Requirements

- **DESIGN-REQ-001**: Source `docs/Tools/DockerComposeUpdateSystem.md` section 12 requires Compose-level and optional smoke-check verification before success. Scope: in scope. Maps to FR-001, FR-002, FR-003.
- **DESIGN-REQ-002**: Source section 12.3 requires failed proof to produce `FAILED` or `PARTIALLY_VERIFIED`, never `SUCCEEDED`, and link to artifacts. Scope: in scope. Maps to FR-002, FR-003, FR-004, FR-005.
- **DESIGN-REQ-003**: Source section 14 requires every run to record audit metadata and required before, command, verification, and after artifacts. Scope: in scope. Maps to FR-004, FR-006.
- **DESIGN-REQ-004**: Source section 14.3 requires command logs and state captures to redact secrets, auth tokens, registry credentials, and sensitive environment variables. Scope: in scope. Maps to FR-007.
- **DESIGN-REQ-005**: Source section 19 requires progress states `QUEUED`, `VALIDATING`, `LOCK_WAITING`, `CAPTURING_BEFORE_STATE`, `PERSISTING_DESIRED_STATE`, `PULLING_IMAGES`, `RECREATING_SERVICES`, `VERIFYING`, `CAPTURING_AFTER_STATE`, `SUCCEEDED`, `FAILED`, and `PARTIALLY_VERIFIED` with short messages and detailed command output only in artifacts. Scope: in scope. Maps to FR-008.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests prove successful verification returns `SUCCEEDED` only with all required artifact refs.
- **SC-002**: Unit tests prove failed and partial verification results never return `SUCCEEDED` and preserve verification evidence.
- **SC-003**: Unit tests prove recursive redaction removes secret-like values from command and state artifact payloads.
- **SC-004**: Unit tests prove audit metadata contains run/workflow/task identifiers and operator context when provided.
- **SC-005**: Unit or integration tests prove progress contains lifecycle states and short messages without raw command output.
- **SC-006**: Integration tests prove the `deployment.update_compose_stack` dispatch boundary returns the structured evidence and final status shape.
- **SC-007**: Traceability evidence preserves `MM-521`, the canonical Jira preset brief, and DESIGN-REQ-001 through DESIGN-REQ-005 in MoonSpec artifacts.
