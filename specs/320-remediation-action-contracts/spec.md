# Feature Specification: Remediation Action Contracts

**Feature Branch**: `320-remediation-action-contracts`
**Created**: 2026-05-08
**Status**: Draft
**Input**: User description: """
Use the Jira preset brief for MM-620 as the canonical Moon Spec orchestration input.

Additional constraints:

Jira Orchestrate always runs as a runtime implementation workflow.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts.

Canonical Jira preset brief:

# MM-620 MoonSpec Orchestration Input

## Source

- Jira issue: MM-620
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Provide typed remediation action registry and v1 action evidence contracts
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-620 from MM project
Summary: Provide typed remediation action registry and v1 action evidence contracts
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Source Reference
Source Document: docs/Tasks/TaskRemediation.md
Source Title: Task Remediation
Source Sections:
- 9.5 Evidence access surface for remediation tasks
- 11. Remediation action registry
- 17. Recommended v1
- Appendix A. Example action policy
Coverage IDs:
- DESIGN-REQ-015
- DESIGN-REQ-016
- DESIGN-REQ-017
- DESIGN-REQ-026
As a remediation task, I can list and request only typed, allowlisted administrative actions with durable v1 request/result evidence so that interventions are validated, risk-scored, idempotency-aware, and auditable.
Acceptance Criteria
- Allowed action listing returns only policy-compatible typed actions with target type, inputs, risk, preconditions, idempotency, verification, and audit metadata.
- Action request/result evidence uses durable bounded v1 contracts and contains no raw secrets or raw administrative handles.
- High-risk actions are marked high and return approval_required, rejected, or executable according to policy.
- Unsupported raw operations are rejected by kind before any side effect.
- The initial registry matches the practical v1 subset unless an action is unavailable, in which case it is omitted or denied with bounded reason.
Requirements
- All side effects are requested through typed action kinds.
- Every action has durable evidence and verification requirements.
- The registry is extensible without exposing raw control channels.

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-620 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.
"""

Preserved source Jira preset brief: `MM-620` from the trusted `jira.get_issue` response, reproduced in the `**Input**` field above for downstream verification.

Original brief reference: trusted `jira.get_issue` MCP response for `MM-620` and local artifact `/work/agent_jobs/mm:f14332d1-2a04-407d-acdd-23b4fa3c3448/artifacts/moonspec/MM-620-orchestration-input.md`.
Classification: single-story runtime feature request.
Resume decision: no existing Moon Spec feature directory preserved `MM-620`, so `Specify` was the first incomplete stage.

## User Story - Typed Remediation Actions

**Summary**: As a remediation task, I can list and request only typed, allowlisted administrative actions with durable v1 request and result evidence so that interventions are validated, risk-scored, idempotency-aware, and auditable.

**Goal**: Remediation work exposes a bounded, policy-compatible action catalog and records every action request and outcome as durable, redacted evidence before any side effect is considered successful.

**Independent Test**: Create remediation contexts with different action policies and target states, then verify that allowed action listing, action request validation, risk outcomes, idempotency behavior, rejection of unsupported raw operations, and request/result evidence are all observable without requiring unrestricted administrative access.

**Acceptance Scenarios**:

1. **Given** a remediation task has an action policy and target evidence, **When** it asks what actions are available, **Then** it receives only policy-compatible typed actions with target type, input metadata, risk tier, preconditions, idempotency expectations, verification expectations, and audit metadata.
2. **Given** a remediation task requests an allowlisted action with valid target, inputs, preconditions, and idempotency context, **When** the request is evaluated, **Then** a bounded v1 action request record is produced and the decision is one of executable, approval-required, rejected, or precondition-failed according to policy.
3. **Given** a high-risk remediation action is requested, **When** policy requires approval or disables the action, **Then** the result is approval-required or rejected and no side effect is executed before that decision is recorded.
4. **Given** an action completes, is skipped as a no-op, times out, fails, or is rejected, **When** the result is recorded, **Then** a bounded v1 action result record includes status, verification requirement, verification hint, before/after evidence references when available, and a redacted side-effect summary.
5. **Given** a remediation task attempts an unsupported raw host, database, Docker, volume, network, secret-reading, or redaction-bypass operation, **When** the action is evaluated, **Then** it is rejected by kind before any side effect and the rejection is audited.
6. **Given** the practical v1 policy does not support a documented action in the current environment, **When** the available action registry is listed, **Then** the action is omitted or denied with a bounded reason rather than exposed as a raw control channel.

### Edge Cases

- Missing or stale target evidence prevents side-effecting actions until fresh preconditions can be evaluated.
- Reusing an idempotency key with a different action, target, dry-run shape, or parameters does not inherit a previous executable decision.
- A dry-run request produces evidence and verification guidance without claiming that a side effect was applied.
- Evidence references that are missing, unauthorized, or no longer readable result in a bounded denial or precondition failure that does not leak hidden target details.
- Action records redact raw secrets, storage-local handles, privileged administrative handles, and unauthorized target identifiers.
- Registry listing remains deterministic for the same policy and target evidence even when unavailable actions are omitted.

## Assumptions

- Runtime intent is required because the Jira Orchestrate preset always runs as a runtime implementation workflow.
- The referenced `docs/Tasks/TaskRemediation.md` sections are source requirements for this story, while unreferenced remediation lifecycle, user interface, and automatic self-healing behavior remain outside this specification unless needed to validate typed action evidence.
- The practical v1 registry is allowed to expose only actions that are currently supportable through MoonMind-owned control surfaces; unsupported actions must be omitted or denied rather than simulated through raw access.

## Source Design Requirements

- **DESIGN-REQ-015** (`docs/Tasks/TaskRemediation.md` section 9.5, lines 545-570): Remediation tasks must use a MoonMind-owned typed evidence and action surface for context, artifacts, logs, allowed action listing, action execution, and target verification instead of scraping pages or using raw administrative access. **Scope**: In scope. **Maps to**: FR-001, FR-002, FR-008.
- **DESIGN-REQ-016** (`docs/Tasks/TaskRemediation.md` section 11, lines 727-900): Remediation actions must be exposed as typed action kinds with declared target type, allowed inputs, risk tier, preconditions, idempotency rules, verification requirements, audit payload shape, request contract, result contract, and explicit rejection of unsupported raw operations. **Scope**: In scope. **Maps to**: FR-001 through FR-008.
- **DESIGN-REQ-017** (`docs/Tasks/TaskRemediation.md` section 17, lines 1309-1353): The v1 remediation registry must remain practical and bounded, with manual creation, pinned target run evidence, artifact-first context, a small supportable action registry, exclusive mutation lock expectations, and full audit artifacts. **Scope**: In scope for registry/evidence behavior; manual creation and broader lock lifecycle are covered only where they constrain action availability and evidence. **Maps to**: FR-001, FR-003, FR-004, FR-005, FR-007, FR-009.
- **DESIGN-REQ-026** (`docs/Tasks/TaskRemediation.md` Appendix A, lines 1395-1433): Action policy must express allowed actions, approval rules, verification requirements, retry budgets, cooldowns, locking expectations, and nested-remediation restrictions in a way that bounds action decisions. **Scope**: In scope for policy-visible action decisions and evidence; detailed policy administration is out of scope. **Maps to**: FR-001, FR-003, FR-005, FR-006, FR-009.
- **DESIGN-REQ-OUT-001** (`docs/Tasks/TaskRemediation.md` unreferenced sections): Full remediation creation flow, Mission Control presentation, automatic self-healing policy, broad audit UI, and long-term prevention pull request workflows are outside this single story except where their existing evidence or policy state affects typed action decisions. **Scope**: Out of scope to preserve a single independently testable story. **Maps to**: None.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST provide a remediation action registry that lists only policy-compatible typed actions for the current remediation task and target evidence.
- **FR-002**: Each listed action MUST include target type, allowed inputs, risk tier, preconditions, idempotency expectations, verification requirements, and audit metadata sufficient for a remediation task to decide whether the action can be requested.
- **FR-003**: System MUST evaluate every action request against action kind, target type, caller/remediation authority, policy, inputs, preconditions, risk tier, dry-run flag, idempotency key, and current target evidence before any side effect is authorized.
- **FR-004**: System MUST record every action request using a bounded v1 evidence contract that includes schema version, action identity, action kind, requester identity, target identity, risk tier, dry-run state, idempotency key, and redacted bounded parameters.
- **FR-005**: System MUST record every action outcome using a bounded v1 evidence contract that includes schema version, action identity, status, user-safe message, applied timestamp when applicable, before/after evidence references when available, verification requirement, verification hint, and redacted side-effect summary.
- **FR-006**: System MUST represent action decisions and results with explicit statuses for applied, no-op, rejected, precondition-failed, approval-required, timed-out, and failed outcomes.
- **FR-007**: System MUST mark high-risk actions as high risk and return approval-required, rejected, or executable according to the active action policy before any high-risk side effect occurs.
- **FR-008**: System MUST reject unsupported raw host, database, Docker, volume, network, secret-reading, and redaction-bypass operations by action kind before any side effect.
- **FR-009**: System MUST omit unsupported v1 actions or deny them with a bounded reason rather than exposing raw administrative channels or fabricated availability.
- **FR-010**: System MUST preserve Jira issue key `MM-620` and the original preset brief in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Key Entities *(include if feature involves data)*

- **Remediation Action Registry Entry**: A typed, policy-compatible action available to a remediation task, including action kind, target type, input metadata, risk tier, preconditions, idempotency expectations, verification requirements, and audit metadata.
- **Remediation Action Request Evidence**: Durable bounded record of a requested remediation action, including requester, target, risk, dry-run state, idempotency key, and redacted parameters.
- **Remediation Action Result Evidence**: Durable bounded record of an action decision or outcome, including status, message, application timestamp when applicable, evidence references, verification requirements, and redacted side effects.
- **Action Policy**: The policy context that determines allowed actions, risk handling, approval requirements, verification requirements, retry/cooldown limits, locking expectations, and nested remediation constraints.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For every remediation action policy tested, the listed actions contain 100% of required metadata fields and contain no unsupported raw operation kinds.
- **SC-002**: 100% of side-effecting action requests produce durable bounded request evidence before an executable or approval-required decision is returned.
- **SC-003**: 100% of completed, denied, timed-out, failed, no-op, and approval-required action outcomes produce durable bounded result evidence with a verification requirement or explicit reason no verification applies.
- **SC-004**: High-risk action scenarios return approval-required, rejected, or executable according to policy in 100% of validation cases, with no side effect before the decision is recorded.
- **SC-005**: Unsupported raw operation attempts are rejected before side effects in 100% of validation cases.
- **SC-006**: Verification evidence can trace `MM-620`, the preserved preset brief, and all in-scope source design requirements to functional requirements without unmapped in-scope coverage IDs.
