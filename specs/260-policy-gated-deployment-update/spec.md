# Feature Specification: Policy-Gated Deployment Update API

**Feature Branch**: `260-policy-gated-deployment-update`
**Created**: 2026-04-25
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-518 as the canonical MoonSpec orchestration input.

Preserve the Jira issue key MM-518 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

Jira Issue: MM-518
Issue Type: Story
Summary: Policy-gated deployment update API
Status: In Progress

Source Reference:
- Source Document: docs/Tools/DockerComposeUpdateSystem.md
- Source Title: Docker Compose Deployment Update System
- Source Sections:
  - 7. API contract
  - 10.1 Validate request
  - 13.1 Authorization
  - 13.2 Allowlisted stacks
  - 13.3 No arbitrary shell
- Coverage IDs:
  - DESIGN-REQ-003
  - DESIGN-REQ-004
  - DESIGN-REQ-005
  - DESIGN-REQ-008
  - DESIGN-REQ-018

Story:
As a MoonMind administrator, I need typed deployment update APIs that enforce deployment policy before any Compose operation can start, so updates can be submitted and inspected without exposing arbitrary host controls.

Acceptance Criteria:
- Admin callers can submit a valid update request and receive deploymentUpdateRunId, taskId or workflowId, and QUEUED status.
- Non-admin callers and ordinary task submitters cannot submit deployment updates.
- Unknown stacks, caller-provided paths, unapproved repositories, unrecognized flags, invalid references, and missing reasons are rejected before workflow/tool execution.
- Current deployment state and allowed image-target endpoints return the documented typed shapes.
- Mutable tag responses identify digest pinning as recommended and preserve requested-reference versus resolved-digest semantics where known.
- The API does not accept arbitrary shell command text, arbitrary Compose file paths, arbitrary host paths, or updater runner image choices.

Requirements:
- Expose the documented typed backend endpoints.
- Bind request validation to deployment policy and admin authorization.
- Represent unsupported values as explicit errors rather than hidden fallback behavior.
- Keep deployment update permissions distinct from ordinary task submission."

## User Story - Policy-Gated Deployment Update Requests

**Summary**: As a MoonMind administrator, I want typed deployment update APIs that enforce deployment policy before Compose work can start, so that updates can be submitted and inspected without exposing arbitrary host controls.

**Goal**: Administrators can request and inspect deployment updates through bounded typed operations while unauthorized callers and unsafe inputs are rejected before any deployment workflow or tool execution begins.

**Independent Test**: Can be fully tested by exercising the deployment update endpoints as admin and non-admin callers with valid and invalid request payloads, confirming that accepted requests return queued run metadata and rejected requests never start deployment work.

**Acceptance Scenarios**:

1. **Given** an administrator and an allowlisted stack, repository, reference, mode, permitted options, and non-empty reason, **When** the administrator submits a deployment update request, **Then** the system returns a deployment update run identifier, a task or workflow identifier, and `QUEUED` status.
2. **Given** a non-admin caller or an ordinary task submitter, **When** the caller submits a deployment update request, **Then** the system rejects the request before deployment workflow or tool execution.
3. **Given** a request with an unknown stack, caller-provided path, unapproved repository, unrecognized flag, invalid image reference, or missing reason, **When** the request is submitted, **Then** the system rejects it before deployment workflow or tool execution and reports the unsupported value explicitly.
4. **Given** a caller with permission to inspect deployment operations, **When** the caller requests current deployment state for an allowlisted stack, **Then** the system returns the documented typed deployment state shape.
5. **Given** a caller with permission to inspect deployment operations, **When** the caller requests allowed image targets for an allowlisted stack, **Then** the system returns documented repository target data including mutable tag guidance and requested-reference versus resolved-digest semantics where known.
6. **Given** any deployment update API request, **When** the payload includes arbitrary shell command text, arbitrary Compose file paths, arbitrary host paths, or updater runner image choices, **Then** the system rejects the request before deployment workflow or tool execution.

### Edge Cases

- Empty, whitespace-only, or absent update reasons are rejected.
- Image references with invalid syntax are rejected before any registry, workflow, or Compose work starts.
- Requests for stacks that exist in operator infrastructure but are not allowlisted are rejected the same as unknown stacks.
- Mutable tag target responses still identify digest pinning as recommended when mutable tags are allowed.
- Rejected requests produce explicit errors without silently falling back to default stacks, repositories, modes, flags, or runner choices.

## Assumptions

- Deployment update submission is restricted to administrator-level authorization; read-only inspection can use the same policy boundary unless existing permissions define a narrower operation-specific read permission.
- A queued deployment update may expose either a task identifier or workflow identifier when one of those identifiers is the canonical run handle available at submission time.
- Registry tag discovery may be best effort, but the typed image-target response must preserve configured repository policy and digest-pinning guidance.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST expose a typed deployment update submission operation for allowlisted deployment stacks.
- **FR-002**: System MUST return a deployment update run identifier, task or workflow identifier, and `QUEUED` status for accepted update requests.
- **FR-003**: System MUST restrict deployment update submission to administrator callers.
- **FR-004**: System MUST keep deployment update permission distinct from ordinary task submission permission.
- **FR-005**: System MUST validate stack name, image repository, image reference syntax, update mode, required reason, and requested options before workflow or tool execution.
- **FR-006**: System MUST reject unknown stacks, caller-provided paths, unapproved repositories, unrecognized flags, invalid references, and missing reasons before workflow or tool execution.
- **FR-007**: System MUST represent unsupported request values as explicit errors instead of silently falling back to defaults.
- **FR-008**: System MUST expose a typed current deployment state read operation for allowlisted stacks.
- **FR-009**: System MUST expose a typed allowed image-target read operation for allowlisted stacks.
- **FR-010**: System MUST identify digest pinning as recommended for mutable tag responses and preserve requested-reference versus resolved-digest semantics where known.
- **FR-011**: System MUST NOT accept arbitrary shell command text, arbitrary Compose file paths, arbitrary host paths, or updater runner image choices through the deployment update APIs.
- **FR-012**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-518` and the canonical Jira preset brief.

### Key Entities

- **Deployment Update Request**: A typed request to update an allowlisted stack using a policy-approved image repository, reference, mode, options, and reason.
- **Deployment Update Run**: The queued operational record returned after a valid update request is accepted, including the deployment update run identifier and task or workflow identifier.
- **Deployment Stack State**: The typed visible state of an allowlisted stack, including configured image, running service images, service health, and last update reference when known.
- **Image Target Policy**: The typed allowed image repository and reference metadata for a stack, including mutable tag allowance and digest-pinning guidance.

## Source Design Requirements

- **DESIGN-REQ-003**: Source `docs/Tools/DockerComposeUpdateSystem.md` section 7.1 requires a typed deployment update submission endpoint with `deploymentUpdateRunId`, task or workflow identifier, and `QUEUED` status in the accepted response. Scope: in scope. Maps to FR-001, FR-002.
- **DESIGN-REQ-004**: Source `docs/Tools/DockerComposeUpdateSystem.md` sections 7.2 and 7.3 require typed read endpoints for current deployment state and allowed image targets. Scope: in scope. Maps to FR-008, FR-009, FR-010.
- **DESIGN-REQ-005**: Source `docs/Tools/DockerComposeUpdateSystem.md` section 10.1 requires validation of administrator authorization, allowlisted stack, allowlisted repository, valid image reference, permitted mode, required reason, and permitted options before execution. Scope: in scope. Maps to FR-003, FR-005, FR-006.
- **DESIGN-REQ-008**: Source `docs/Tools/DockerComposeUpdateSystem.md` sections 13.1 and 13.2 require deployment update authorization to be administrator-only, distinct from ordinary task submission, and constrained to allowlisted stack targets instead of caller-provided paths. Scope: in scope. Maps to FR-003, FR-004, FR-006.
- **DESIGN-REQ-018**: Source `docs/Tools/DockerComposeUpdateSystem.md` section 13.3 requires typed inputs only and rejection of arbitrary shell commands, unapproved Compose files, host paths, image repositories, updater runner images, and unrecognized flags. Scope: in scope. Maps to FR-006, FR-007, FR-011.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A valid administrator deployment update request for an allowlisted stack receives queued run metadata in one request.
- **SC-002**: Non-admin and ordinary task submitter attempts to submit deployment updates are rejected in all tested cases.
- **SC-003**: Every invalid stack, repository, reference, mode, option, path, runner choice, shell command, and missing reason case is rejected before workflow or tool execution.
- **SC-004**: Current deployment state responses include stack identity, configured image, running service image details, service state, and last update reference when known.
- **SC-005**: Allowed image-target responses include repository policy, allowed references or recent tags when known, and digest pinning recommendation for mutable tags.
- **SC-006**: Verification evidence confirms `MM-518`, the canonical Jira preset brief, and DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-008, and DESIGN-REQ-018 remain traceable in MoonSpec artifacts.
