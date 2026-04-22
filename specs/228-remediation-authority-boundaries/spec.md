# Feature Specification: Remediation Authority Boundaries

**Feature Branch**: `228-remediation-authority-boundaries`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-453 as the canonical Moon Spec orchestration input.

Additional constraints:


Selected mode: runtime.
Default to runtime mode and only use docs mode when explicitly requested.
If the brief points at an implementation document, treat it as runtime source requirements.
Source design path (optional): .

Classify the input as a single-story feature request, broad technical or declarative design, or existing feature directory.
Inspect existing Moon Spec artifacts and resume from the first incomplete stage instead of regenerating valid later-stage artifacts."

**Canonical Jira Brief**: `docs/tmp/jira-orchestration-inputs/MM-453-moonspec-orchestration-input.md`

## Original Preset Brief

```text
# MM-453 MoonSpec Orchestration Input

## Source

- Jira issue: MM-453
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Enforce remediation authority modes, permissions, and redaction boundaries
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-453 from MM project
Summary: Enforce remediation authority modes, permissions, and redaction boundaries
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-453 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-453: Enforce remediation authority modes, permissions, and redaction boundaries

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 10. Security and authority model
  - 6. Core invariants
  - 4. Non-goals
- Coverage IDs:
  - DESIGN-REQ-010
  - DESIGN-REQ-011
  - DESIGN-REQ-024

User Story
As a platform security owner, I can configure remediation authority through explicit modes, permissions, and named security profiles so privileged troubleshooting never implies raw host access or secret disclosure.

Acceptance Criteria
- `observe_only` remediation can read evidence and produce diagnoses but cannot execute side-effecting actions.
- `approval_gated` remediation can propose or dry-run actions but requires recorded approval before side effects.
- `admin_auto` remediation can execute only allowlisted actions within policy and still respects high-risk approval rules.
- Audit records include both requesting user/workflow and the execution principal used for privileged actions.
- A user with target view permission alone cannot launch admin remediation or approve high-risk actions.
- Generated context, logs, summaries, diagnostics, and artifacts do not contain raw secrets or durable raw storage/file access material.

Requirements
- Authority is policy/profile based, not implicit root or ordinary runtime access.
- Unauthorized direct fetches do not leak execution existence.
- Secret redaction rules apply even to admin remediation.

Relevant Implementation Notes
- Preserve MM-453 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation authority modes, permission boundaries, redaction behavior, and non-goals.
- Model remediation authority with explicit modes: `observe_only`, `approval_gated`, and `admin_auto`.
- Elevated remediation must execute through a named admin remediation principal or security profile rather than the ordinary user runtime.
- Distinguish permissions to view a target execution, create a remediation task, request an admin remediation profile, approve high-risk actions, and inspect remediation audit history.
- Keep administrative actions typed and allowlisted; remediation must not imply arbitrary host shell access, Docker daemon access, SQL/database editing, or any command the model suggests.
- Keep evidence, artifact, and log access server-mediated through refs, read capability handles, redacted previews, or typed observability APIs.
- Never place raw secrets, presigned storage URLs, storage backend keys, absolute local filesystem paths, or raw secret-bearing config bundles in durable remediation context, logs, summaries, diagnostics, or artifacts.
- Ensure side-effecting remediation actions are idempotent or safely keyed, and keep high-risk actions subject to explicit approval policy even under `admin_auto`.
- Preserve redaction-safe visibility: unauthorized direct fetches must not leak execution existence, and admin remediation artifacts must still follow redaction-safe display rules.

Non-Goals
- Arbitrary raw shell access on the MoonMind host.
- Unrestricted Docker daemon access from the remediation runtime.
- Arbitrary SQL or direct database row editing by the agent.
- Silent import of another task's entire workflow history into `initialParameters`.
- Cross-task managed-session reuse.
- Treating every failed task as automatically eligible for an admin healer.
- Bypassing the Secrets System or artifact redaction rules.
- Treating Live Logs as the source of truth or as a control channel.

Validation
- Verify each supported authority mode enforces its side-effect permissions: `observe_only`, `approval_gated`, and `admin_auto`.
- Verify side-effecting actions require recorded approval when policy or risk classification requires it.
- Verify audit records include both the requesting user or workflow and the execution principal/security profile used for privileged actions.
- Verify target view permission alone is insufficient to launch admin remediation or approve high-risk actions.
- Verify generated context, logs, summaries, diagnostics, and artifacts redact raw secrets and do not expose durable raw storage or filesystem access material.
- Verify unauthorized direct fetches do not leak execution existence.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-453 blocks MM-452, whose embedded status is Code Review.
- Trusted Jira link metadata at fetch time shows MM-453 is blocked by MM-454, whose embedded status is Backlog.

Needs Clarification
- None
```

## User Story - Govern Remediation Authority

**Summary**: As a platform security owner, I want remediation authority controlled by explicit modes, permissions, and named security profiles so that privileged troubleshooting never implies raw host access or secret disclosure.

**Goal**: Remediation can diagnose and act only within the authority granted by its selected mode, caller permissions, security profile, approval policy, and redaction rules.

**Independent Test**: Configure remediation submissions and action requests for each authority mode, permission level, and risk level, then verify the system allows, gates, audits, rejects, or redacts the request according to the selected mode and security policy without exposing raw secrets or durable raw access material.

**Acceptance Scenarios**:

1. **Given** a remediation run is configured as `observe_only`, **When** it reads evidence and produces diagnosis output, **Then** those read-only operations are allowed and all side-effecting action requests are rejected before execution.
2. **Given** a remediation run is configured as `approval_gated`, **When** it proposes or dry-runs a side-effecting action, **Then** the proposal is allowed but execution requires a recorded approval before any side effect occurs.
3. **Given** a remediation run is configured as `admin_auto`, **When** it requests an allowlisted action, **Then** the action may execute only within policy and high-risk actions still follow explicit approval policy.
4. **Given** a remediation action is executed through elevated authority, **When** audit evidence is recorded, **Then** the record identifies both the requesting user or workflow and the execution principal or security profile used for the privileged action.
5. **Given** a user has permission to view the target execution only, **When** the user attempts to launch admin remediation or approve a high-risk action, **Then** the request is denied without granting additional authority.
6. **Given** remediation context, logs, summaries, diagnostics, or artifacts are generated, **When** they are stored or displayed, **Then** raw secrets and durable raw storage or filesystem access material are omitted or redacted.

### Edge Cases

- Unsupported, blank, or unknown authority modes are rejected instead of defaulting to stronger authority.
- A missing, disabled, or unauthorized security profile prevents elevated remediation from launching or executing privileged actions.
- High-risk action classification cannot be bypassed by selecting `admin_auto`.
- Unauthorized direct fetches fail without revealing whether the target execution exists.
- Existing evidence and log access remains server-mediated and never becomes a direct storage grant.
- Nested remediation and cross-task session reuse remain disabled unless policy explicitly enables them outside this story.

## Assumptions

- The MM-453 story is a single runtime feature slice focused on authority, permission, audit, and redaction enforcement for remediation, not on building remediation evidence bundles or action registry internals covered by adjacent Jira slices.
- Existing remediation create, evidence, artifact, audit, and action surfaces are the expected product surfaces to enforce this behavior; this spec defines observable behavior rather than requiring a new top-level workflow.

## Source Design Requirements

- **DESIGN-REQ-010** (`docs/Tasks/TaskRemediation.md` section 10.1): Remediation authority must be represented by explicit modes: `observe_only`, `approval_gated`, and `admin_auto`, each with distinct read, proposal, approval, and execution behavior. Scope: in scope, mapped to FR-001 through FR-005.
- **DESIGN-REQ-011** (`docs/Tasks/TaskRemediation.md` sections 6, 10.2, 10.3, and 10.6): Privileged remediation must use named execution principals or security profiles, enforce distinct permissions for viewing, launching, approving, and auditing, keep actions typed and allowlisted, and preserve high-risk approval requirements. Scope: in scope, mapped to FR-006 through FR-012.
- **DESIGN-REQ-024** (`docs/Tasks/TaskRemediation.md` sections 4, 6, 10.4, 10.5, and 10.7): Remediation must not bypass secrets, artifact redaction, server-mediated evidence access, visibility scoping, or non-goals such as raw host, Docker, SQL, or workflow-history access. Scope: in scope, mapped to FR-013 through FR-018.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept only supported remediation authority modes: `observe_only`, `approval_gated`, and `admin_auto`.
- **FR-002**: `observe_only` remediation MUST allow authorized evidence reads and diagnosis output while rejecting all side-effecting action execution.
- **FR-003**: `approval_gated` remediation MUST allow action proposals and dry runs while requiring a recorded approval before side-effecting execution.
- **FR-004**: `admin_auto` remediation MUST execute only allowlisted actions that are permitted by the selected policy and security profile.
- **FR-005**: High-risk actions MUST follow explicit approval policy even when remediation authority mode is `admin_auto`.
- **FR-006**: Elevated remediation MUST require a named execution principal or security profile before privileged action execution.
- **FR-007**: The system MUST separately enforce permission to view a target execution, create a remediation task, request an admin remediation profile, approve high-risk actions, and inspect remediation audit history.
- **FR-008**: Target view permission alone MUST NOT authorize launching admin remediation, requesting an admin security profile, approving high-risk actions, or inspecting privileged audit history.
- **FR-009**: Remediation audit records MUST identify the requesting user or workflow and the execution principal or security profile used for privileged actions.
- **FR-010**: Side-effecting remediation actions MUST be typed, allowlisted, and validated against the active authority mode, action policy, security profile, and risk classification before execution.
- **FR-011**: Duplicate or retried side-effecting action requests MUST be safely keyed or rejected so retries do not create duplicate destructive effects.
- **FR-012**: Unsupported authority modes, missing security profiles, disabled profiles, and unauthorized approvals MUST fail closed with explicit validation results.
- **FR-013**: Remediation MUST NOT provide arbitrary host shell access, unrestricted Docker daemon access, arbitrary SQL/database editing, cross-task managed-session reuse, or silent import of another task's full workflow history.
- **FR-014**: Evidence, artifact, and log access for remediation MUST remain server-mediated through authorized refs, read handles, redacted previews, or typed observability views.
- **FR-015**: Durable remediation context, workflow payloads, run summaries, logs, diagnostics, and artifacts MUST NOT contain raw secrets, raw storage keys, presigned storage URLs, absolute local filesystem paths, or raw secret-bearing config bundles.
- **FR-016**: Unauthorized direct fetches MUST fail without revealing whether the target execution exists.
- **FR-017**: Stronger remediation authority MUST NOT override secret redaction, audit, visibility, or secret-reference rules.
- **FR-018**: Live Logs MUST remain an observation surface and MUST NOT become the source of truth or control channel for remediation intervention.

### Key Entities

- **Authority Mode**: The declared remediation authority level controlling read-only diagnosis, gated action execution, or policy-limited automatic execution.
- **Security Profile**: A named privileged execution identity or profile used for elevated remediation actions.
- **Approval Record**: Evidence that a permitted approver authorized a side-effecting or high-risk remediation action.
- **Remediation Audit Record**: Durable evidence linking the requestor, workflow, authority mode, security profile, action, risk level, approval state, and outcome.
- **Redaction Boundary**: The visibility and secret-handling constraint that prevents raw secrets and durable raw access material from entering remediation outputs.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove all three authority modes enforce their allowed and rejected operation classes.
- **SC-002**: Tests prove side-effecting action execution under `approval_gated` requires a recorded approval and under `admin_auto` remains limited to allowlisted, policy-permitted actions.
- **SC-003**: Tests prove high-risk actions require approval or are disabled according to policy even under `admin_auto`.
- **SC-004**: Tests prove target view permission alone cannot launch admin remediation, request an admin profile, approve high-risk actions, or inspect privileged audit history.
- **SC-005**: Tests prove audit records include both the requesting user or workflow and the execution principal or security profile for privileged actions.
- **SC-006**: Tests prove remediation context, summaries, logs, diagnostics, artifacts, and durable payloads do not expose raw secrets, raw storage keys, presigned storage URLs, absolute local filesystem paths, or raw secret-bearing config bundles.
- **SC-007**: Tests prove unsupported modes, missing profiles, unauthorized approvals, and unauthorized direct fetches fail closed without leaking target execution existence.
- **SC-008**: Traceability verification confirms MM-453 and DESIGN-REQ-010, DESIGN-REQ-011, and DESIGN-REQ-024 are preserved in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
