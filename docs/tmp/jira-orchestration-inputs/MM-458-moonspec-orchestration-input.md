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
