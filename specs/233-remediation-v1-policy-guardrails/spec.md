# Feature Specification: Remediation V1 Policy Guardrails

**Feature Branch**: `233-remediation-v1-policy-guardrails`
**Created**: 2026-04-22
**Status**: Draft
**Input**: User description: "Use the Jira preset brief for MM-458 as the canonical Moon Spec orchestration input.

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
# MM-458 MoonSpec Orchestration Input

## Source

- Jira issue: MM-458
- Jira project key: MM
- Issue type: Story
- Current status at fetch time: In Progress
- Summary: Document and enforce bounded v1 rollout and future self-healing policy constraints
- Labels: `moonmind-workflow-mm-4fcd9c9b-785c-42de-a6ca-ed60359eadf6`
- Trusted fetch tool: `jira.get_issue`
- Canonical source: normalized Jira preset brief synthesized from trusted Jira tool response fields because the MCP issue response did not expose `recommendedImports.presetInstructions`, `normalizedPresetBrief`, `presetBrief`, or `presetInstructions`.

## Canonical MoonSpec Feature Request

Jira issue: MM-458 from MM project
Summary: Document and enforce bounded v1 rollout and future self-healing policy constraints
Issue type: Story
Current Jira status: In Progress
Jira project key: MM

Use this Jira preset brief as the canonical MoonSpec orchestration input. Preserve the Jira issue key MM-458 in spec artifacts, implementation notes, verification output, commit text, and pull request metadata.

MM-458: Document and enforce bounded v1 rollout and future self-healing policy constraints

Source Reference
- Source document: `docs/Tasks/TaskRemediation.md`
- Source title: Task Remediation
- Source sections:
  - 4. Non-goals
  - 7.6 Future automatic self-healing policy
  - 16. Failure modes and edge cases
  - 17. Recommended v1
  - 18. Future extensions
  - 19. Acceptance criteria
  - Appendix C. Design rule summary
- Coverage IDs:
  - DESIGN-REQ-016
  - DESIGN-REQ-022
  - DESIGN-REQ-023
  - DESIGN-REQ-024

User Story
As a product/platform owner, I can ship a constrained manual v1 and leave future automatic self-healing behind explicit bounded policy so remediation remains safe as capabilities expand.

Acceptance Criteria
- Default v1 behavior supports manual remediation only and does not automatically spawn admin healers.
- Unsupported raw admin capabilities are absent from APIs, tools, and UI and fail closed if requested.
- Future self-healing policy fields, if accepted by schemas, remain inert unless explicitly enabled and bounded by policy.
- All documented failure/edge cases have structured output expectations such as validation failure, evidenceDegraded, no_op, precondition_failed, lock_conflict, escalated, unsafe_to_act, or failed.
- The implementation acceptance checklist can be traced back to each design rule without relying on future-work language for current v1 guarantees.

Requirements
- V1 must stay useful but constrained.
- Future extensions preserve artifact-first evidence, typed actions, explicit locks, strict audit, redaction, and no raw root shell.
- Non-goals are enforced as guardrails, not merely documentation.

Relevant Implementation Notes
- Preserve MM-458 in downstream MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
- Use `docs/Tasks/TaskRemediation.md` as the source design reference for remediation non-goals, future automatic self-healing policy, failure and edge-case outputs, recommended v1 scope, future extension boundaries, acceptance criteria, and design-rule summary.
- Keep v1 remediation manual by default: operators create remediation tasks explicitly from Mission Control or equivalent operator surfaces.
- Do not introduce an automatic rule where every failed, stalled, timed-out, or attention-required task spawns an admin healer.
- If future automatic remediation policy fields are accepted by schemas or payload models, keep them policy-driven, bounded, and inert unless explicitly enabled.
- Preserve bounded self-healing controls such as triggers, create mode, template reference, authority mode, and max active remediations when future policy support is modeled.
- Enforce non-goals as runtime, API, tool, and UI guardrails: no arbitrary raw host shell, unrestricted Docker daemon access, arbitrary SQL, direct database row editing, redaction bypass, secret reads, or silent workflow-history bulk imports.
- Keep remediation evidence artifact-first and server-mediated; do not embed unbounded workflow history, raw local filesystem paths, presigned storage URLs, or raw storage keys into workflow payloads.
- Keep typed admin actions behind explicit policy/profile bindings and owning control planes; do not expose raw root or generic admin console semantics.
- Require failure and edge-case handling to produce structured, auditable outcomes rather than silent success or ambiguous fallback behavior.
- Surface degraded evidence explicitly with `evidenceDegraded = true` when diagnosis proceeds with merged logs or partial artifacts.
- Treat unavailable targets, stale target assumptions, lock conflicts, lost locks, already-released leases, missing containers, and unsafe forced termination as explicit validation, no_op, precondition_failed, lock_conflict, verification_failed, escalated, unsafe_to_act, or failed outcomes according to policy.
- Ensure remediation task failure attempts final summary publication and lock release; automatic remediation of the failed remediator remains off by default.
- Align the implementation acceptance checklist with stable design rules: remediation is not dependency waiting, `workflowId` identifies the logical target, `runId` pins evidence, live follow is non-authoritative, mutations are locked/idempotent/audited, secrets stay redacted, and loop prevention is required.
- Preserve future extension boundaries: richer action coverage, templates, analytics, stuck-detection integration, proposal-based review, and automatic remediation policies must not weaken artifact-first evidence, typed actions, explicit locks, strict audit, or redaction.

Non-Goals
- Automatically spawning admin healers for every failed task in v1.
- Exposing raw host shell, raw root, unrestricted Docker, arbitrary SQL, direct database row edits, decrypted secret reads, or redaction bypass actions.
- Treating future automatic self-healing fields as active behavior without explicit bounded policy enablement.
- Using future-work language as a substitute for current v1 safety guarantees.
- Silently importing another task's entire workflow history into `initialParameters`.
- Treating Live Logs as authoritative durable evidence.
- Allowing remediation loops, automatic self-remediation, or unbounded self-healing depth by default.

Validation
- Verify default v1 remediation creation remains manual and no failed-task path automatically spawns an admin healer.
- Verify unsupported raw admin capabilities are absent from API contracts, MCP/tool surfaces, workflow inputs, and Mission Control UI language or controls.
- Verify raw host shell, unrestricted Docker, arbitrary SQL, direct database row editing, decrypted secret reads, and redaction bypass requests fail closed with structured errors.
- Verify future automatic self-healing policy fields, if accepted by schemas, remain inert unless explicitly enabled and bounded by policy.
- Verify accepted self-healing policy shape preserves bounded controls such as triggers, create mode, template reference, authority mode, and max active remediations.
- Verify documented failure and edge cases produce structured outcomes such as validation failure, evidenceDegraded, no_op, precondition_failed, lock_conflict, escalated, unsafe_to_act, verification_failed, or failed as appropriate.
- Verify partial evidence paths surface `evidenceDegraded = true` and record unavailable evidence classes.
- Verify final acceptance checks trace to the stable design rules in `docs/Tasks/TaskRemediation.md` without relying on future extension behavior.
- Verify future extension placeholders do not weaken artifact-first evidence, typed action registry boundaries, explicit locking, audit, redaction, or loop-prevention defaults.

Dependencies
- Trusted Jira link metadata at fetch time shows MM-458 blocks MM-457, whose embedded status is Code Review.
- No Jira blocker links were detected where another issue blocks MM-458.

Needs Clarification
- None
```

## User Story - Enforce Remediation V1 Policy Guardrails

**Summary**: As a product/platform owner, I want remediation v1 to remain manual, bounded, and policy-constrained so that current remediation behavior is useful without enabling unsafe automatic self-healing or raw administrative capabilities.

**Goal**: The runtime accepts and exposes only constrained remediation behavior: manual creation by default, no automatic admin healer spawning, no unsupported raw admin capability, inert future self-healing policy unless explicitly enabled and bounded, and structured outcomes for documented edge cases.

**Independent Test**: Exercise remediation submission, policy parsing, action/tool exposure, UI-visible capability metadata, and failure/edge-case classification; verify v1 remains manual by default, unsupported raw admin requests fail closed, future self-healing policy is inert unless explicitly enabled, and every documented edge case produces a structured bounded outcome.

**Acceptance Scenarios**:

1. **Given** a task fails, stalls, times out, or requests attention, **When** no explicit operator or policy-driven remediation creation request exists, **Then** the system does not automatically spawn an admin healer.
2. **Given** a user or runtime attempts to request raw host shell, unrestricted Docker, arbitrary SQL, direct database editing, decrypted secret reads, redaction bypass, or full workflow-history import, **When** remediation contracts, tools, APIs, or UI capability metadata are evaluated, **Then** those capabilities are absent or rejected with structured fail-closed errors.
3. **Given** future automatic self-healing policy fields are present in accepted payloads or configuration, **When** explicit bounded enablement is absent, **Then** those fields remain inert and do not create or authorize remediation.
4. **Given** automatic self-healing policy metadata is explicitly enabled before a supported bounded runtime implementation exists, **When** policy is evaluated, **Then** it remains non-executable; future runtime support may proceed only after validating trigger, create mode, template reference, authority mode, maximum active remediation, depth, audit, and redaction limits.
5. **Given** target visibility, evidence, live follow, locks, leases, containers, preconditions, or remediator execution fail in documented ways, **When** remediation evaluates the condition, **Then** it emits validation failure, evidenceDegraded, no_op, precondition_failed, lock_conflict, escalated, unsafe_to_act, verification_failed, or failed instead of silent success or raw-access fallback.
6. **Given** downstream verification inspects the implementation, **When** traceability is checked, **Then** MM-458 and the relevant Task Remediation design rules are preserved in artifacts, implementation notes, verification output, commit text, and pull request metadata.

### Edge Cases

- Existing historical runs may only have merged logs or partial artifacts; diagnosis can continue only when safe and must set degraded evidence.
- Policy payloads may include future self-healing fields before automatic self-healing ships; the fields must be preserved only as inert or explicitly bounded data.
- Multiple remediation requests may be active across a target graph; v1 must still avoid automatic loops and unbounded self-healing depth.
- UI, API, and tool metadata may be generated from different sources; unsupported raw capabilities must be absent or rejected consistently across every boundary.
- A failed remediator must publish bounded summary evidence and release locks where possible, but it must not automatically remediate itself by default.
- Unknown or newly introduced failure reasons must fail closed with a bounded unsupported or unsafe outcome rather than falling back to raw access.

## Assumptions

- The MM-458 brief is a single runtime feature slice focused on v1 rollout and future self-healing guardrails, not a request to implement automatic self-healing.
- Existing remediation create/link, evidence/context, authority, action registry, mutation guard, UI, and lifecycle stories provide surrounding remediation behavior; this story verifies and completes the bounded policy constraints.
- The source implementation document `docs/Tasks/TaskRemediation.md` is treated as runtime source requirements because the selected mode is runtime.

## Source Design Requirements

- **DESIGN-REQ-016** (`docs/Tasks/TaskRemediation.md` sections 7.6, 17, 18, and Appendix C): Automatic remediation must remain policy-driven and bounded, v1 starts with explicit manual creation, self-healing depth remains bounded, future extensions preserve typed actions, explicit locks, strict audit, redaction, and no raw root shell. Scope: in scope, mapped to FR-001 through FR-010 and FR-018 through FR-021.
- **DESIGN-REQ-022** (`docs/Tasks/TaskRemediation.md` sections 16.2 and 16.7): Reruns, stale target assumptions, and failed preconditions must produce recorded no-op, re-diagnosis, precondition_failed, or escalation outcomes rather than silent retargeting or silent success. Scope: in scope, mapped to FR-013 through FR-016.
- **DESIGN-REQ-023** (`docs/Tasks/TaskRemediation.md` sections 16.1 through 16.11): Missing targets, partial evidence, unavailable live follow, lock conflicts, stale leases, gone containers, unsafe forced termination, and remediator failure must degrade, no-op, escalate, fail, or deny with bounded reasons. Scope: in scope, mapped to FR-011 through FR-017.
- **DESIGN-REQ-024** (`docs/Tasks/TaskRemediation.md` section 4 and Appendix C): Non-goals are enforceable guardrails: no arbitrary host shell, unrestricted Docker, arbitrary SQL, direct database row editing, redaction bypass, decrypted secret reads, full workflow-history imports, Live Logs as source of truth, or unbounded loops. Scope: in scope, mapped to FR-004 through FR-008 and FR-017 through FR-021.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Default v1 remediation behavior MUST require explicit operator or trusted workflow submission and MUST NOT automatically spawn admin healers for failed, stalled, timed-out, or attention-required tasks.
- **FR-002**: Future automatic self-healing policy fields MAY be accepted only as inert configuration unless explicit bounded enablement is present.
- **FR-003**: Explicitly enabled automatic remediation policy metadata MUST remain non-executable unless the runtime can validate trigger set, create mode, template reference, authority mode, maximum active remediation count, depth, audit, and redaction constraints.
- **FR-004**: The system MUST NOT expose arbitrary raw host shell access through remediation contracts, tools, APIs, workflow payloads, or UI capability metadata.
- **FR-005**: The system MUST NOT expose unrestricted Docker daemon access, arbitrary SQL, direct database row editing, decrypted secret reads, redaction bypass, or arbitrary network/volume mutation as remediation capabilities.
- **FR-006**: Unsupported raw admin capability requests MUST fail closed with structured policy or validation errors instead of being ignored, silently downgraded, or translated into raw access.
- **FR-007**: Remediation evidence access MUST remain artifact-first and server-mediated, and MUST NOT silently import another task's entire workflow history into initial parameters.
- **FR-008**: Live follow data MUST remain non-authoritative and MUST NOT replace durable artifact, log, diagnostic, summary, or audit evidence.
- **FR-009**: Automatic nested remediation, self-remediation, and unbounded self-healing depth MUST be disabled by default.
- **FR-010**: Any enabled future self-healing behavior MUST preserve typed actions, explicit locks, audit records, redaction, bounded evidence, and no raw root shell.
- **FR-011**: Missing target visibility MUST produce a structured validation failure or early bounded remediation error.
- **FR-012**: Historical targets with only merged logs or partial artifacts MUST surface degraded evidence when diagnosis continues.
- **FR-013**: Target reruns after remediation starts MUST preserve the pinned snapshot and MUST NOT silently retarget without a recorded outcome.
- **FR-014**: Failed preconditions MUST produce no_op, precondition_failed, re-diagnosis, or escalation outcomes rather than silent success.
- **FR-015**: Lock conflicts, stale leases, and lost locks MUST produce explicit bounded outcomes before further mutation.
- **FR-016**: Missing containers or unsafe forced termination attempts MUST produce no_op, verification_failed, approval-required, unsafe_to_act, or policy rejection outcomes as appropriate.
- **FR-017**: A failed remediator MUST attempt final summary publication and lock release, and automatic remediation of the failed remediator MUST remain off by default.
- **FR-018**: APIs, tools, workflow contracts, and UI surfaces MUST consistently advertise only supported typed remediation capabilities.
- **FR-019**: Unsupported or future-only capability metadata MUST be distinguishable from currently executable v1 behavior.
- **FR-020**: Validation and verification evidence MUST trace MM-458 and DESIGN-REQ-016, DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-024 through MoonSpec artifacts, implementation notes, commit text, and pull request metadata.
- **FR-021**: The implementation acceptance checklist MUST be traceable to current v1 guarantees and MUST NOT rely on future-work language to satisfy present safety requirements.

### Key Entities

- **Remediation Creation Policy**: The bounded decision data that determines whether remediation may be created manually or automatically.
- **Self-Healing Policy Fields**: Future-facing policy data such as triggers, create mode, template reference, authority mode, maximum active remediation count, and depth.
- **Remediation Capability Surface**: The API, tool, workflow, and UI-visible list of currently supported typed remediation actions and explicitly unsupported raw capabilities.
- **Bounded Failure Outcome**: A structured remediation decision such as validation failure, evidenceDegraded, no_op, precondition_failed, lock_conflict, escalated, unsafe_to_act, verification_failed, or failed.
- **V1 Guardrail Checklist**: The traceable acceptance evidence that current behavior enforces manual-by-default remediation, no raw admin fallback, bounded policy, and structured edge-case outcomes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Tests prove failed, stalled, timed-out, and attention-required tasks do not automatically spawn admin healers in default v1 behavior.
- **SC-002**: Tests prove unsupported raw host, Docker, SQL, database-editing, secret-reading, redaction-bypass, and workflow-history-import capabilities are absent or fail closed across API, tool, workflow, and UI-visible capability surfaces.
- **SC-003**: Tests prove future self-healing policy fields remain inert unless explicit bounded enablement is present.
- **SC-004**: Tests prove explicitly enabled self-healing policy metadata does not proceed in v1 without supported bounded runtime validation for trigger, create mode, template, authority, max-active, depth, audit, and redaction constraints.
- **SC-005**: Tests prove documented target visibility, partial evidence, live-follow, stale target, lock, lease, container, unsafe termination, and remediator failure cases produce structured bounded outcomes.
- **SC-006**: Tests prove Live Logs remains non-authoritative and durable artifacts, logs, diagnostics, summaries, or audit evidence remain the fallback evidence path.
- **SC-007**: Traceability verification confirms MM-458 and DESIGN-REQ-016, DESIGN-REQ-022, DESIGN-REQ-023, and DESIGN-REQ-024 are preserved in MoonSpec artifacts, implementation notes, verification output, commit text, and pull request metadata.
