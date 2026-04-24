# Feature Specification: Remediation Action Registry

**Feature Branch**: `229-remediation-action-registry`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-454 as the canonical Moon Spec orchestration input.

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
# MM-454 MoonSpec Orchestration Input

## Source

- Jira issue: MM-454
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Provide a typed remediation action registry with audited request and result contracts
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-454 from MM project
Summary: Provide a typed remediation action registry with audited request and result contracts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-454 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-454: Provide a typed remediation action registry with audited request and result contracts

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 11. Remediation action registry
  - 10.6 High-risk actions
  - 17. Recommended v1
- Coverage IDs:
  - DESIGN-REQ-012
  - DESIGN-REQ-013
  - DESIGN-REQ-023
  - DESIGN-REQ-024

User Story
As a remediation task, I can request only typed, allowlisted administrative actions and receive durable request/result artifacts with risk, precondition, idempotency, verification, and audit data.

Acceptance Criteria
- `list_allowed_actions` returns only policy-compatible typed action kinds with risk and input metadata.
- `execute_action` validates action kind, target class, inputs, risk policy, preconditions, and idempotency key before invoking owning control-plane code.
- Action request artifacts include `schemaVersion`, `actionId`, `actionKind`, `requester`, target workflow/run/resource, `riskTier`, `dryRun`, `idempotencyKey`, and bounded params.
- Action result artifacts include status, `appliedAt` when applicable, before/after refs, `verificationRequired`, `verificationHint`, and bounded `sideEffects`.
- Unsupported raw host, SQL, Docker, volume, network, secret-reading, or redaction-bypass operations fail fast and are audited as rejected.
- V1 registry scope matches the recommended small action set unless a supported action is unavailable, in which case it is omitted rather than exposed as raw access.

Requirements
- Administrative actions are MoonMind-owned typed capabilities.
- Managed-session and Docker workload actions go through their owning control planes.
- Every side-effecting action declares risk and verification requirements.

Relevant Implementation Notes
- Preserve MM-454 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation action registry behavior, high-risk action handling, and recommended v1 scope.
- Expose remediation actions through MoonMind-owned typed capabilities such as `remediation.list_allowed_actions()` and `remediation.execute_action(actionKind, params, dryRun?)`; do not require the remediation runtime to use UI scraping or raw administrative access.
- Model registry entries as allowlisted action kinds with explicit target type, allowed inputs, risk tier, preconditions, idempotency rules, verification requirements, and audit payload shape.
- Include the recommended v1 action set where supported: `execution.pause`, `execution.resume`, `execution.request_rerun_same_workflow`, `session.interrupt_turn`, `session.clear`, `provider_profile.evict_stale_lease`, and `session.restart_container` only when implemented through the owning plane.
- Keep `execution.start_fresh_rerun`, `execution.cancel`, `execution.force_terminate`, `session.cancel`, `session.terminate`, `workload.restart_helper_container`, and `workload.reap_orphan_container` aligned with the canonical registry semantics if they are included or deferred.
- Treat high-risk actions with explicit `low`, `medium`, or `high` risk tiers, and let approval policy decide whether each action is auto-allowed, requires operator approval, or is disabled.
- Request artifacts must use a durable v1 contract that records schema version, action identity, action kind, requester, target workflow/run/resource, risk, dry-run mode, idempotency key, and bounded parameters.
- Result artifacts must use a durable v1 contract that records status, application timestamp when applicable, before/after refs, verification requirement, verification hint, and bounded side effects.
- Supported result status values should include `applied`, `no_op`, `rejected`, `precondition_failed`, `approval_required`, `timed_out`, and `failed`.
- Reject unsupported raw host shell, arbitrary SQL, arbitrary Docker image/run, arbitrary volume mount, arbitrary network egress change, decrypted secret read, and redaction-bypass requests as typed audited rejections.
- Ensure side-effecting action execution validates action kind, target class, input shape, risk policy, preconditions, idempotency key, and ownership/control-plane routing before invoking the owning service.
- Verify action effects through target-specific checks such as lease state after stale lease eviction, session identity and continuity boundary after container restart, and target run state/runId rollover after same-workflow rerun requests.

Non-Goals
- Arbitrary raw host shell access.
- Arbitrary SQL or direct database editing by the remediation runtime.
- Arbitrary Docker daemon, image, volume, or network access.
- Reading decrypted secret contents.
- Bypassing artifact redaction rules.
- Exposing unsupported actions as raw access fallbacks.

Validation
- Verify `list_allowed_actions` returns only policy-compatible typed actions with risk and input metadata for the active policy/profile.
- Verify `execute_action` rejects unsupported action kinds, wrong target classes, malformed inputs, failed preconditions, missing/duplicate unsafe idempotency keys, and policy-disallowed risk tiers before invoking owning control-plane code.
- Verify action request artifacts include the required v1 fields and keep params bounded.
- Verify action result artifacts include the required v1 fields, bounded side effects, and verification guidance.
- Verify high-risk actions require approval or are disabled according to policy.
- Verify unsupported raw host, SQL, Docker, volume, network, secret-reading, and redaction-bypass requests fail fast and are audited as rejected.
- Verify managed-session and Docker workload actions route through their owning control planes rather than direct host/runtime access.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-454 blocks MM-453, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-454 is blocked by MM-455, whose embedded status is Backlog.

Needs Clarification
- None
```

## User Story - Request Typed Remediation Actions

**Summary**: As a remediation task, I want to request only typed, allowlisted administrative actions and receive durable request/result artifacts so that remediation can act through audited MoonMind-owned capabilities instead of raw host, Docker, SQL, storage, network, or secret access.

**Goal**: A remediation action request is evaluated through a typed registry that exposes only policy-compatible action kinds, validates the request before execution, records bounded request/result/audit data, and rejects unsupported raw-access operations.

**Independent Test**: Create a linked remediation execution for a target run, evaluate allowed, approval-gated, high-risk, unsupported, raw-access, duplicate, and redaction-sensitive action requests, then verify the decisions, executable flags, risk handling, idempotency behavior, and audit payloads match the active policy/profile without exposing raw access material.

**Acceptance Scenarios**:

1. **Given** a remediation run has a policy-compatible action profile, **When** it lists or evaluates allowed actions, **Then** only typed action kinds compatible with the policy/profile are available with risk and input metadata.
2. **Given** a side-effecting action request includes an action kind, target, params, dry-run mode, requester, and idempotency key, **When** the registry evaluates it, **Then** action kind, target class, input shape, risk policy, preconditions, profile authorization, and idempotency are validated before any owning control-plane action can run.
3. **Given** an action request is accepted, **When** durable evidence is produced, **Then** request and result/audit payloads identify schema version, action identity, action kind, requester, target workflow/run/resource, risk tier, dry-run state, idempotency key, bounded params, status, verification guidance, and bounded side effects where applicable.
4. **Given** a high-risk action is requested without required approval, **When** the registry evaluates it, **Then** execution is not allowed and the result requires approval or reports policy denial.
5. **Given** a raw host, SQL, Docker, volume, network, secret-reading, storage-key, or redaction-bypass operation is requested, **When** the registry evaluates it, **Then** the operation fails fast as a typed audited rejection.
6. **Given** the recommended v1 action set is only partly implemented by owning control planes, **When** allowed actions are derived, **Then** unavailable actions are omitted instead of exposed through raw access fallbacks.

### Edge Cases

- Blank, unknown, disabled, or unsupported action kinds are denied with explicit reasons.
- Missing remediation links, missing idempotency keys, and missing target-view permission fail closed without leaking target existence.
- Duplicate requests with the same remediation workflow, idempotency key, action kind, and dry-run state return the original decision rather than reapplying side effects.
- Security profiles that are absent, disabled, or not authorized for the action prevent execution.
- Dry-run requests produce non-side-effecting decisions even when the action kind is otherwise valid.
- Redaction-sensitive params, principals, URLs, and local paths are scrubbed from durable outputs.

## Assumptions

- The MM-454 story is a single runtime feature slice focused on action registry request/result decision contracts, not on implementing every owning control-plane action in the full future registry.
- Existing remediation authority and evidence stories cover evidence-bundle creation, server-mediated evidence access, and authority-mode submission semantics; this story covers action request evaluation and durable decision outputs.
- Actions are evaluated as MoonMind-owned typed capabilities first; individual execution adapters can be connected only after the typed request is accepted.

## Source Design Requirements

- **DESIGN-REQ-012** (`docs/Tasks/TaskRemediation.md` section 11.2): The initial remediation action registry must expose typed canonical action families and every action kind must declare target type, allowed inputs, risk tier, preconditions, idempotency rules, verification requirements, and audit payload shape. Scope: in scope, mapped to FR-001 through FR-007.
- **DESIGN-REQ-013** (`docs/Tasks/TaskRemediation.md` sections 11.4 through 11.6): Action request and result evidence must use durable bounded contracts that include action identity, requester, target, risk, dry-run, idempotency, status, before/after refs, verification requirements, hints, and side effects. Scope: in scope, mapped to FR-008 through FR-013.
- **DESIGN-REQ-023** (`docs/Tasks/TaskRemediation.md` sections 6, 9.5, and 17): Missing or unavailable supported actions must degrade by omission or bounded denial rather than unbounded waits or raw access fallback. Scope: in scope, mapped to FR-014 and FR-015.
- **DESIGN-REQ-024** (`docs/Tasks/TaskRemediation.md` sections 4, 10.7, and 11.7): The registry must explicitly reject arbitrary host shell, SQL, Docker, volume, network, decrypted secret, raw storage, and redaction-bypass operations. Scope: in scope, mapped to FR-016 through FR-018.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST expose remediation action evaluation through typed MoonMind-owned action kinds rather than raw host, Docker, SQL, storage, network, or secret access.
- **FR-002**: The registry MUST return only policy-compatible enabled action kinds for the active remediation policy/profile and MUST include risk and input metadata for each returned kind.
- **FR-003**: The registry MUST deny blank, unknown, disabled, or unsupported action kinds before any side-effecting execution can occur.
- **FR-004**: The registry MUST validate remediation link identity, target class, target-view permission, action kind, dry-run state, params, risk policy, security profile authorization, approval requirement, and idempotency key before returning an executable decision.
- **FR-005**: Side-effecting actions MUST be executable only when the selected authority mode, policy, profile, approval state, and risk tier allow execution.
- **FR-006**: High-risk actions MUST require approval or be disabled according to policy and MUST NOT become executable merely because the remediation mode allows automatic administration.
- **FR-007**: Duplicate or retried action requests with the same remediation workflow, idempotency key, action kind, and dry-run state MUST return a deterministic original decision rather than applying a second side effect.
- **FR-008**: Action request evidence MUST include schema version, action identity, action kind, requester, target workflow/run/resource, risk tier, dry-run state, idempotency key, and bounded parameters.
- **FR-009**: Action result evidence MUST include status, application timestamp when applicable, before/after refs when applicable, verification requirement, verification hint, and bounded side effects.
- **FR-010**: Supported action result statuses MUST include `applied`, `no_op`, `rejected`, `precondition_failed`, `approval_required`, `timed_out`, and `failed` or an equivalent closed set that maps these outcomes without ambiguity.
- **FR-011**: The audit payload for each evaluated action MUST identify the requesting principal or workflow, execution principal or security profile when present, decision, denial/approval reason, and redacted summary.
- **FR-012**: Request, result, and audit payloads MUST redact raw secrets, secret-bearing headers, presigned URLs, storage keys, and absolute local filesystem paths before durable storage or display.
- **FR-013**: Accepted action requests MUST carry verification requirements or hints appropriate to the action kind and target class.
- **FR-014**: The v1 action set MUST include only action kinds that have a supported owning control-plane path; unavailable recommended actions MUST be omitted or denied rather than exposed as raw access.
- **FR-015**: Managed-session and Docker workload actions MUST route through their owning control planes when supported and MUST NOT grant direct Docker daemon or host shell capability to the remediation runtime.
- **FR-016**: Raw host shell, arbitrary SQL, arbitrary Docker image/run, arbitrary volume mount, arbitrary network egress change, decrypted secret read, raw storage key read, and redaction-bypass requests MUST fail fast as audited typed rejections.
- **FR-017**: Missing remediation links, missing idempotency keys, missing target-view permission, disabled profiles, unauthorized profiles, and missing approvals MUST fail closed with explicit validation results.
- **FR-018**: Durable registry decisions MUST preserve MM-454 traceability through spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities

- **Action Registry Entry**: A typed allowlisted remediation capability with action kind, target type, allowed inputs, risk tier, preconditions, idempotency rule, verification requirement, and audit payload shape.
- **Action Request**: A bounded command envelope for one remediation action evaluation, including requester, target, action kind, params, dry-run state, risk, approval/profile evidence, and idempotency key.
- **Action Result**: A bounded outcome envelope for one evaluated action, including status, application metadata, before/after refs, verification guidance, and side effects.
- **Action Audit Record**: Redaction-safe durable evidence of who requested the action, which execution principal/profile would execute it, and why the registry allowed, gated, or denied it.
- **Idempotency Key**: A stable request key that prevents retries from duplicating side effects.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove allowed-action evaluation exposes only enabled typed action kinds compatible with policy/profile and includes risk metadata.
- **SC-002**: Tests prove action evaluation denies unsupported action kinds, missing links, missing idempotency keys, missing permissions, disabled profiles, and unauthorized profile/action combinations before execution.
- **SC-003**: Tests prove high-risk actions require approval or are denied according to policy.
- **SC-004**: Tests prove duplicate action requests with the same idempotency key and request shape return the original decision.
- **SC-005**: Tests prove request/result/audit payloads include requester, execution profile/principal, target identity, action kind, risk, decision/status, and verification guidance where applicable.
- **SC-006**: Tests prove raw host, SQL, Docker, volume, network, secret-reading, storage-key, and redaction-bypass action attempts fail fast as audited rejections.
- **SC-007**: Tests prove durable action outputs redact raw secrets, secret-bearing headers, presigned URLs, storage keys, and absolute local filesystem paths.
- **SC-008**: Traceability verification confirms MM-454 and DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-023, and DESIGN-REQ-024 are preserved in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
