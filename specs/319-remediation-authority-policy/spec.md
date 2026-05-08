# Feature Specification: Remediation Authority Policy

**Feature Branch**: `319-remediation-authority-policy`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-619 as the canonical Moon Spec orchestration input.

Additional constraints:


Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-619 MoonSpec Orchestration Input

## Source

- Jira issue: MM-619
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Enforce remediation authority, policy profiles, and secret-safe access
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.
- Trusted response artifact: `/work/agent_jobs/mm:05cfaa29-6f29-4fa8-9606-83a64b58a844/artifacts/moonspec-inputs/MM-619-trusted-jira-get-issue.json`

## Canonical MoonSpec Feature Request

Jira issue: MM-619 from MM project
Summary: Enforce remediation authority, policy profiles, and secret-safe access
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-619 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-619: Enforce remediation authority, policy profiles, and secret-safe access

Source Reference
Source Document: docs/Tasks/TaskRemediation.md
Source Title: Task Remediation
Source Sections:
- 10. Security and authority model
- 11.7 Explicitly unsupported actions
- Appendix C. Design rule summary
Coverage IDs:
- DESIGN-REQ-013
- DESIGN-REQ-014
- DESIGN-REQ-017

As a platform administrator, I can govern remediation authority through explicit modes, named principals, permissions, approval policy, and redaction rules so that privileged remediation never becomes implicit host, secret, or visibility bypass access.

Acceptance Criteria
- Observe-only remediators can read allowed evidence and suggest actions but cannot execute side effects.
- Admin remediation uses a named principal/profile and records both requester and effective privileged principal.
- Users cannot create admin remediation, approve high-risk actions, or inspect audit history without explicit permission.
- No remediation context, payload, artifact preview, log, diagnostic, or summary exposes raw secrets or unauthorized target existence.
- Unsupported raw operations fail closed through policy, not through hidden fallback execution.

Requirements
- Authority is explicit and policy-bound.
- Secret and visibility guardrails apply even to privileged remediation.
- Raw admin operations remain unsupported by default.

## Related Jira Context

- blocks MM-618: Build bounded remediation evidence context and live follow tools [Done]
- is blocked by MM-620: Provide typed remediation action registry and v1 action evidence contracts [Backlog]

## MoonSpec Orchestration Notes

- Selected mode: runtime.
- Treat the referenced source document sections as runtime source requirements.
- Classify this input as a single-story feature request unless later MoonSpec analysis proves it is too broad.
- Inspect existing MoonSpec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.
- Preserve `MM-619` and the listed coverage IDs throughout downstream artifacts and final evidence.
"""

## Classification

Input classification: single-story runtime feature request. The Jira brief selects one independently testable remediation governance behavior from `docs/Tasks/TaskRemediation.md`: authority modes, named privileged principals, explicit permissions, approval policy, secret-safe evidence handling, and closed rejection of raw admin actions. It does not require story splitting.

Resume decision: no existing Moon Spec feature directory or later-stage artifacts matched `MM-619` under `specs/`; only `specs/318-bounded-remediation-evidence/` references MM-619 as future linked authority/policy scope. Specify is the first incomplete stage.

## User Story - Govern Remediation Authority

**Summary**: As a platform administrator, I can govern remediation authority through explicit modes, named principals, permissions, approval policy, and redaction rules so that privileged remediation never becomes implicit host, secret, or visibility bypass access.

**Goal**: Remediation action requests are evaluated against explicit authority mode, caller permissions, named privileged profile, approval requirements, risk policy, and redaction rules before any side effect is executable.

**Independent Test**: Create remediation links in observe-only, approval-gated, and admin-auto modes, then evaluate read-only, side-effecting, high-risk, raw-access, unauthorized, and secret-bearing action requests. The story passes when only policy-authorized requests are executable, approval is required where appropriate, audit identity is recorded, and serialized outputs leak no raw secrets or unauthorized target existence.

**Acceptance Scenarios**:

1. **Given** a remediation link uses observe-only authority, **When** a remediator evaluates a side-effecting action, **Then** the action is denied or limited to dry-run evidence and cannot execute a side effect.
2. **Given** a remediation link uses approval-gated authority, **When** a side-effecting action is evaluated without an approval reference, **Then** the decision is approval-required and no execution is authorized.
3. **Given** admin remediation is evaluated with an enabled named security profile and sufficient caller permissions, **When** an allowed medium-risk action is requested, **Then** the decision records both the requester and the effective privileged principal and marks the request executable.
4. **Given** a high-risk remediation action is requested, **When** approval is missing or the caller lacks high-risk approval permission, **Then** execution is blocked with an explicit approval or permission reason.
5. **Given** a caller lacks target visibility, admin-profile permission, approval permission, or audit-inspection permission for the requested operation, **When** remediation authority is evaluated or displayed, **Then** unauthorized actions, approval decisions, and audit details are not exposed as executable capabilities.
6. **Given** remediation parameters, context, artifact previews, logs, diagnostics, summaries, or audit outputs contain secret-like or storage-local values, **When** they are serialized for workflow state, API response, artifact, log, or UI display, **Then** raw secrets, presigned storage URLs, storage keys, local filesystem paths, and unauthorized target identifiers are redacted or omitted.
7. **Given** a raw host, SQL, Docker, volume, network, or secret-reading operation is requested, **When** remediation authority is evaluated, **Then** the request fails closed through policy and is not converted into a hidden fallback execution.

### Edge Cases

- Unsupported or stale authority modes fail closed rather than falling back to privileged behavior.
- Disabled or missing security profiles prevent privileged execution.
- Reused idempotency keys do not let a different action or dry-run shape inherit a prior executable decision.
- Missing remediation links or unauthorized targets produce sanitized denial output that does not reveal hidden execution existence.
- Approval-gated remediation exposes a bounded pending approval state but does not expose raw action payloads to unauthorized users.

## Assumptions

- The selected story governs authority decisions and secret-safe surfaces; it does not implement new typed remediation action kinds beyond the existing registry boundary.
- Existing remediation evidence and live-follow context from MM-618 remains a dependency, not part of this story's implementation scope.

## Source Design Requirements

- **DESIGN-REQ-013**: Source `docs/Tasks/TaskRemediation.md` section 10 and Appendix C. Remediation authority must be explicit and policy-bound through supported authority modes, named privileged profile/principal identity, separate permissions, approval handling for high-risk actions, and audit that records requester plus effective principal. Scope: in scope. Maps to FR-001 through FR-009.
- **DESIGN-REQ-014**: Source `docs/Tasks/TaskRemediation.md` section 10.4, section 10.5, and Appendix C. Privileged remediation must not bypass secret handling, artifact/log mediation, visibility limits, or redaction-safe display rules. Scope: in scope. Maps to FR-010 through FR-013.
- **DESIGN-REQ-017**: Source `docs/Tasks/TaskRemediation.md` section 11.7 and Appendix C. Raw host shell, SQL, arbitrary Docker, arbitrary volume/network, decrypted secret reading, and redaction bypass operations must remain unsupported by default and fail closed as typed policy denials. Scope: in scope. Maps to FR-014 and FR-015.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST persist and evaluate remediation authority mode as one of `observe_only`, `approval_gated`, or `admin_auto`.
- **FR-002**: System MUST reject unsupported remediation authority modes without substituting a privileged fallback.
- **FR-003**: System MUST prevent observe-only remediation from executing side-effecting actions while still allowing bounded diagnosis or dry-run output.
- **FR-004**: System MUST require approval before approval-gated remediation can execute a side-effecting action.
- **FR-005**: System MUST require approval or equivalent high-risk permission before high-risk remediation actions can execute.
- **FR-006**: System MUST require a named enabled security profile before privileged remediation actions can execute.
- **FR-007**: System MUST record both requesting principal and effective privileged execution principal for allowed privileged decisions.
- **FR-008**: System MUST distinguish target visibility, remediation creation, admin-profile request, high-risk approval, and audit-inspection permissions.
- **FR-009**: System MUST expose only policy-compatible action capabilities for the caller and selected profile.
- **FR-010**: System MUST redact raw secrets, credentials, authorization headers, storage-local paths, and presigned storage URLs from remediation request, result, audit, context, and summary output.
- **FR-011**: System MUST keep artifact and log access mediated through refs, redacted previews, or typed access surfaces rather than raw storage URLs, backend keys, local paths, or secret-bearing bundles.
- **FR-012**: System MUST avoid leaking unauthorized target existence in denial responses, summaries, logs, or audit output.
- **FR-013**: System MUST preserve redaction and visibility guardrails for privileged remediation, not only ordinary remediation.
- **FR-014**: System MUST deny raw host shell, raw SQL, arbitrary Docker, arbitrary volume/network, decrypted-secret, and redaction-bypass operations by default.
- **FR-015**: System MUST represent unsupported raw operations as explicit policy denials, not hidden fallback execution through generic tooling.
- **FR-016**: MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata MUST preserve Jira issue key `MM-619` and this canonical Jira preset brief for traceability.

### Key Entities

- **Remediation Authority Mode**: The selected authority level for a remediation link; determines whether the run may diagnose only, require approval, or auto-execute allowed actions.
- **Remediation Permission Set**: The caller capabilities used to authorize visibility, remediation creation, admin profile use, high-risk approval, and audit inspection.
- **Remediation Security Profile**: A named privileged principal/profile that constrains allowed action kinds and records effective execution identity.
- **Remediation Action Decision**: The policy result for one requested action, including decision, reason, risk, executability, idempotency key, redacted parameters, and audit identity.
- **Remediation Audit Output**: A bounded, redacted record of requester, effective principal, decision, reason, target reference, and action metadata.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Unit tests cover observe-only denial/dry-run behavior, approval-gated approval requirements, admin-auto profile authorization, high-risk approval handling, unsupported authority modes, and raw-operation denial.
- **SC-002**: API or service-boundary tests cover remediation creation with supported and unsupported authority/policy values plus bounded approval state serialization.
- **SC-003**: Redaction tests confirm serialized remediation decisions and audit output do not contain raw tokens, authorization headers, local workspace paths, presigned URLs, or unauthorized target identifiers.
- **SC-004**: Capability-list tests confirm callers only see actions allowed by their permissions and security profile.
- **SC-005**: Traceability checks confirm `MM-619`, `DESIGN-REQ-013`, `DESIGN-REQ-014`, and `DESIGN-REQ-017` are preserved across MoonSpec artifacts and final evidence.
