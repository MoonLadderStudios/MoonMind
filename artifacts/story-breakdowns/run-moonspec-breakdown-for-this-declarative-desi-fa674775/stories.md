# Story Breakdown: Step Executions and Checkpointing

- Source design: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Story extraction date: `2026-06-09T07:04:31Z`
- Output mode: `jira`
- Coverage gate: `PASS - every major design point is owned by at least one story.`

## Design Summary

The design defines Step Executions with Checkpointing as MoonMind's execution-plane primitive for safely repeating logical steps. It requires explicit per-attempt identity, immutable artifact-backed evidence, checkpoint validation, workspace/git side-effect policy, structured gates, dependency invalidation, resume lineage, managed-runtime boundaries, and bounded operator/API visibility. The design excludes redefining Step Types, executable tool contracts, generic artifact internals, Temporal Activity retry policy, provider launch internals, and full run-history UI, while requiring security, idempotency, compact workflow payloads, and versioned or compatible Temporal-facing contracts.

## Coverage Points

- `DESIGN-REQ-001` (requirement, 1. Purpose): Execution primitive scope - Define Step Executions with Checkpointing as the shared primitive for semantic re-execution, failed-step recovery, gated loops, and autonomous story loops without redefining Step Types, tool contracts, artifact storage internals, Temporal Activity retries, provider launch internals, or full run-history UI.
- `DESIGN-REQ-002` (state-model, 2. Layering and Source of Truth): Layered source of truth - Keep planned structure, workflow live state, ledger projections, immutable attempt manifests, checkpoints, git state, and app DB projections in distinct authority layers, with append-only attempt evidence and compact workflow refs.
- `DESIGN-REQ-003` (requirement, 3. Desired-State Summary): Explicit per-attempt model - Each Step Execution owns attempt identity, lineage, reason, input snapshot, context bundle, workspace/git baseline, child refs, artifacts, checks, side-effect dispositions, dependency effects, and terminal status/disposition.
- `DESIGN-REQ-004` (constraint, 4. Terminology): Terminology separation - Distinguish logical steps, semantic Step Executions, low-level retries, step re-execution, failed-step recovery, checkpoints, context bundles, gates, preserved steps, and invalidated steps.
- `DESIGN-REQ-005` (constraint, 5. Core Invariants): Core invariants - Enforce stable logical step identity, monotonically increasing run-scoped execution ordinals, append-only evidence, immutable digest-addressed context, classified side effects, accepted-output rules, gate-owned advancement, downstream invalidation, explicit resume failures, adapter boundaries, and safe Temporal-facing contract evolution.
- `DESIGN-REQ-006` (state-model, 6. Identity, Lineage, and Idempotency): Run-scoped identity and lineage - Use (workflowId, runId, logicalStepId, executionOrdinal) as the durable attempt key, keep cross-run lineage as optional provenance, pin source workflow/run in resume, and sanitize externally exposed identifiers.
- `DESIGN-REQ-007` (security, 6.3 Idempotency keys): Idempotency keys for side effects - All side-effecting activities participating in an attempt must accept or derive stable idempotency keys from namespace, workflow, run, logical step, attempt, and operation; Temporal activity attempt numbers are not business keys.
- `DESIGN-REQ-008` (artifact, 7. Step Execution Manifest Contract): Step execution manifest contract - Persist an immutable artifact-backed manifest using the recommended content type, bounded reasons, statuses, terminal dispositions, input/context refs, workspace metadata, outputs, checks, side effects, dependency effects, and budget metadata.
- `DESIGN-REQ-009` (artifact, 8. Context Bundle, Retrieval, and Memory Inputs): Immutable context, retrieval, and memory inputs - Build immutable digest-addressed context bundles with compact refs, no raw credentials, workspace policy/baseline, runtime selection, retrieval manifests, and memory proposals with explicit promotion states.
- `DESIGN-REQ-010` (artifact, 9. Checkpoint Contract): Checkpoint contract and boundaries - Create durable checkpoint evidence at prepare, pre-mutation, post-execution, post-gate, pre-publication, and pre-resume boundaries, using explicit checkpoint kinds and idempotent writes.
- `DESIGN-REQ-011` (constraint, 9.4 Checkpoint validation): Checkpoint validation - Before reuse, validate source workflow/run, task input, plan digest, logical step, provenance, artifact access, workspace/branch/commit consistency, checkpoint kind compatibility, and side-effect policy eligibility; fail before launching new work if validation fails.
- `DESIGN-REQ-012` (state-model, 10. Workspace and Git Policy): Workspace and git policy - Record an explicit workspace policy before repeated coding work, support canonical policies, classify git effects, and require committed/published/typed accepted output or accepted no-change before a logical implementation step succeeds.
- `DESIGN-REQ-013` (security, 11. Side-Effect Classes and External Guardrails): Side-effect guardrails - Classify workspace, artifact, idempotent external, non-idempotent external, publication, provider-account, memory, and retrieval side effects; gate publication and external transitions; handle compensation explicitly.
- `DESIGN-REQ-014` (integration, 12. Activity Surface and Worker Boundaries): Activity and worker ownership boundaries - MoonMind.Run owns orchestration; activities own side effects; runtime adapters execute attempts. Workflow code must not perform file/git/artifact/provider/memory side effects directly, and boundary results must be compact and typed.
- `DESIGN-REQ-015` (requirement, 13. Gated Iteration): Gated iteration loops - Parent workflows own bounded implement/verify/remediate iteration, structured verdict interpretation, retry/fail/pass branching, downstream skip/invalidation, and final publication eligibility.
- `DESIGN-REQ-016` (state-model, 13.1 Gate verdict contract, 13.2 Budgets and stop rules): Gate verdicts and budgets - Use structured verdicts such as FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, NO_DETERMINATION, BLOCKED, and FAILED_UNRECOVERABLE plus explicit attempt, time, spend, no-progress, command-repeat, and unsafe-attempt budgets.
- `DESIGN-REQ-017` (state-model, 14. Dependency Invalidation and Preserved Output Reuse): Dependency invalidation and preserved output reuse - Associate downstream inputs with producing attempt identities, invalidate or require revalidation when upstream accepted outputs change, and validate preserved outputs before reuse.
- `DESIGN-REQ-018` (requirement, 15. Resume Relationship): Failed-step resume relationship - Failed-step recovery creates a linked follow-up run with preserved prior steps, a new local Step Execution at the failed logical step, pinned lineage to the source attempt, validated checkpoint restoration, and explicit failure instead of silent full rerun.
- `DESIGN-REQ-019` (integration, 16. Managed Runtime Relationship): Managed runtime attempt semantics - For managed runtimes, MoonMind creates attempt records, context bundles, workspace policy, AgentRun boundaries, output refs, gates, side-effect classification, ledger projection updates, and manifest updates while agents cannot create hidden attempts.
- `DESIGN-REQ-020` (integration, 17. Operator and API Surfaces): Operator and API attempt surfaces - Default task details show current/latest attempts with counts, gate summaries, evidence refs, and provenance; expanded APIs expose bounded attempt projections and artifact refs without large inline transcripts or diffs.
- `DESIGN-REQ-021` (requirement, 18. Failure and Stop Semantics): Failure and stop semantics - When attempts fail or gates request more work, classify next state as accepted, retryable, blocked, needs_human, discarded, superseded, or failed_with_remaining_work, and prevent downstream publication or external transitions on failed gates.
- `DESIGN-REQ-022` (security, 19. Security and Side-Effect Guardrails): Security posture - Forbid raw credentials in context and manifests, enforce artifact authorization, scope workspace restore, sanitize failed logs, respect untrusted skill source policy, and prevent path traversal or unauthorized materialization.
- `DESIGN-REQ-023` (requirement, 20. Examples): Examples as validation scenarios - Support Jira orchestrate gated implementation, failed-step recovery, autonomous story loops, and dependency invalidation as end-to-end behavioral scenarios.
- `DESIGN-REQ-024` (non-goal, 21. Non-Goals): Non-goals - Do not require infinite loops, hidden transcript attempts, large workflow-state diffs/logs, semantic reliance on Temporal Activity retries, automatic replay of every side effect, direct repo memory commits from failed attempts, separate recovery systems, full default attempt history UI, or autonomous semantics for every Step Type.
- `DESIGN-REQ-025` (migration, 22. Rollout Guidance): Phased rollout guidance - Implementation should be phased through manifests/evidence, checkpoints, gated reattempt loops, retrieval and memory manifests, and autonomous story/PRD loops.
- `DESIGN-REQ-026` (constraint, 23. Constitution Alignment): Constitution alignment - Preserve orchestration ownership, operator-controlled data, explicit bounded resiliency, spec-driven traceability, and structured improvement evidence.

## Ordered Story Candidates

### STORY-001: Record immutable step execution attempts

- Short name: `attempt-manifests`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md` (1. Purpose; 2. Layering and Source of Truth; 3. Desired-State Summary; 4. Terminology; 5. Core Invariants; 6. Identity, Lineage, and Idempotency; 7. Step Execution Manifest Contract; 23. Constitution Alignment)
- Dependencies: None
- Independent test: Create initial and repeated executions for one logical step through the workflow/activity boundary, then assert monotonically increasing run-scoped identities, compact workflow refs, immutable manifest artifacts, bounded reason/status/disposition values, idempotency keys, and no large payloads in workflow state.
- Narrative: As MoonMind.Run, I need every semantic execution of a logical step to create compact workflow state and immutable artifact-backed evidence so repeated work is explicit, inspectable, and safe for downstream orchestration.
- Assumptions: Existing artifact services can store the new manifest content type without adding persistent tables.
- Needs clarification: None

Acceptance criteria:
- Given a resolved logical step, when the first semantic attempt starts, a Step Execution record is created with executionOrdinal 1 and a manifest artifact ref using the step-execution content type.
- Given a failed or repeated logical step, when another semantic attempt starts, a new Step Execution is created with a higher run-scoped executionOrdinal and a source execution/reason before agent work begins.
- The workflow state stores only compact refs and bounded summaries; full context, outputs, diffs, checks, side effects, dependency effects, and budget metadata live in artifacts.
- Step Execution reasons, statuses, and terminal dispositions are validated against bounded canonical values and fail fast for unsupported values.
- Side-effecting activity calls derive stable business idempotency keys from workflow, run, logical step, attempt, and operation, not Temporal activity attempt numbers.
- A test verifies that low-level Temporal Activity retry does not create a new semantic Step Execution.

Requirements:
- Model run-scoped Step Execution identity as workflowId, runId, logicalStepId, and executionOrdinal.
- Represent optional cross-run lineage as provenance without replacing local storage identity.
- Create and persist append-only step execution manifest evidence through activity/service boundaries.
- Keep Step Types and logical authoring steps distinct from Step Executions.
- Preserve workflow determinism by carrying refs and compact metadata only.

Owned source design coverage:
- `DESIGN-REQ-001`: Owns the core primitive and non-redefinition boundary.
- `DESIGN-REQ-002`: Owns layered authority and append-only/projection behavior.
- `DESIGN-REQ-003`: Owns per-attempt identity, lineage, evidence, and disposition shape.
- `DESIGN-REQ-004`: Owns terminology separation in contracts and validation.
- `DESIGN-REQ-005`: Owns core invariants that apply before later stories can safely build on attempts.
- `DESIGN-REQ-006`: Owns durable identity and lineage metadata.
- `DESIGN-REQ-007`: Owns attempt-scoped idempotency key derivation.
- `DESIGN-REQ-008`: Owns manifest artifact contract.
- `DESIGN-REQ-026`: Owns traceable, operator-controlled evidence and resiliency alignment.

### STORY-002: Prepare attempt context and checkpointed workspace state

- Short name: `checkpointed-context`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md` (8. Context Bundle, Retrieval, and Memory Inputs; 9. Checkpoint Contract; 10. Workspace and Git Policy; 11. Side-Effect Classes and External Guardrails; 19. Security and Side-Effect Guardrails)
- Dependencies: STORY-001
- Independent test: Run a mutating step attempt with a selected workspace policy, capture required checkpoints, validate the checkpoint before a second attempt, and assert that invalid checkpoint source, plan digest, artifact authorization, or policy compatibility fails before launching the runtime.
- Narrative: As MoonMind.Run, I need each attempt to launch from an immutable context bundle and a validated checkpoint/workspace policy so re-execution, resume, and recovery never rely on logs or hidden mutable state.
- Assumptions: Checkpoint artifacts can reuse existing artifact authorization and workspace capture services where available.
- Needs clarification: None

Acceptance criteria:
- Each attempt gets an immutable context bundle artifact containing compact refs, builder version, digest, workspace policy, workspace baseline, retrieval manifest ref, memory manifest ref, runtime selection, and no raw credentials.
- Checkpoint evidence is created idempotently at required boundaries for prepare, pre-mutation, post-execution, post-gate, pre-publication, and pre-resume where applicable.
- Workspace policies declare their minimum checkpoint evidence and are rejected before runtime launch when evidence is unavailable or incompatible.
- Git effects are classified as accepted, candidate, discarded, superseded, or none, and uncommitted dirty work is never treated as an accepted implementation output.
- Side effects are classified before workflow advancement, with non-idempotent external effects forbidden in autonomous reattempts unless explicit policy permits them.
- Checkpoint restoration is scoped to the approved workspace and rejects path traversal, unauthorized artifact materialization, or mismatched workflow/run/plan/logical-step provenance.

Requirements:
- Persist context bundles, retrieval manifests, memory manifests, and checkpoint refs as artifacts rather than workflow payloads.
- Validate checkpoint source identity, input snapshot, plan digest, logical step, provenance, artifact access, workspace consistency, checkpoint kind, and side-effect eligibility before reuse.
- Support canonical workspace policies: continue_from_previous_execution, restore_pre_execution, apply_previous_execution_diff_to_clean_baseline, start_from_last_passed_commit, and fresh_branch_from_source.
- Support git checkpoint kinds and workspace checkpoint kinds required by the design.
- Track memory proposal promotion states and prevent failed attempts from silently writing durable repo memory.

Owned source design coverage:
- `DESIGN-REQ-009`: Owns context bundle, retrieval, and memory input contracts.
- `DESIGN-REQ-010`: Owns checkpoint artifact contract and required boundaries.
- `DESIGN-REQ-011`: Owns checkpoint validation and fail-fast behavior.
- `DESIGN-REQ-012`: Owns workspace policy, git effect classification, and accepted output rule.
- `DESIGN-REQ-013`: Owns side-effect classification and external guardrails.
- `DESIGN-REQ-022`: Owns secret exclusion, authorization, workspace-scope, sanitization, skill-source, and path traversal guardrails.

### STORY-003: Run gated reattempt loops with dependency invalidation

- Short name: `gated-reattempts`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md` (12. Activity Surface and Worker Boundaries; 13. Gated Iteration; 14. Dependency Invalidation and Preserved Output Reuse; 16. Managed Runtime Relationship; 18. Failure and Stop Semantics; 20. Examples; 21. Non-Goals)
- Dependencies: STORY-001, STORY-002
- Independent test: Exercise a workflow-owned implement-verify-remediate loop where the first gate returns ADDITIONAL_WORK_NEEDED, a second attempt runs under budget, downstream outputs are invalidated after an upstream accepted change, and publication remains blocked until a FULLY_IMPLEMENTED verdict validates accepted output refs.
- Narrative: As MoonMind.Run, I need structured gates and budgets to decide whether implementation, verification, and remediation attempts advance, repeat, stop, or invalidate downstream work so publication and external handoffs happen only from accepted evidence.
- Assumptions: Existing workflow tests can model gate verdicts and runtime adapter output refs without live providers.
- Needs clarification: None

Acceptance criteria:
- Parent workflow branches on typed gate verdicts instead of parsing prose and supports FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, NO_DETERMINATION, BLOCKED, and FAILED_UNRECOVERABLE.
- Autonomous loops enforce configured budgets for attempts, wall-clock time, provider spend or estimate, no-progress attempts, repeated command failures, and unsafe or policy-denied attempts where configured.
- Budget exhaustion produces deterministic terminal disposition and publishes latest evidence plus recommended next action without running publication, Jira movement, merge, deployment, or provider-account actions.
- Runtime adapters execute attempts at an AgentRun/tool boundary, but cannot create hidden attempts or mutate the attempt ledger outside MoonMind-owned orchestration.
- Changed accepted outputs invalidate or require revalidation of downstream steps that consumed prior producing attempt identities.
- Preserved downstream outputs are reused only when a structured gate or checkpoint validates that they still satisfy their contracts.
- Tests cover the Jira orchestrate loop, autonomous story loop, and dependency invalidation examples from the source design.

Requirements:
- Keep workflow code free of direct file, git, artifact, provider, and memory side effects; activities own those side effects.
- Return compact typed activity and gate results across workflow boundaries.
- Associate downstream input refs with producing Step Execution identities unless the workflow explicitly refreshes them.
- Classify failed attempts as retryable, blocked, needs_human, discarded, superseded, failed_with_remaining_work, or accepted according to structured policy.
- Respect non-goals by avoiding infinite loops, hidden transcript attempts, large workflow-state evidence, and semantic reliance on Temporal Activity retries.

Owned source design coverage:
- `DESIGN-REQ-014`: Owns workflow/activity/runtime ownership boundaries.
- `DESIGN-REQ-015`: Owns gated iteration and publication eligibility.
- `DESIGN-REQ-016`: Owns verdict and budget contracts.
- `DESIGN-REQ-017`: Owns dependency invalidation and preserved output reuse.
- `DESIGN-REQ-019`: Owns managed runtime attempt semantics.
- `DESIGN-REQ-021`: Owns failure and stop classification.
- `DESIGN-REQ-023`: Owns concrete end-to-end examples as validation scenarios.
- `DESIGN-REQ-024`: Owns explicit exclusions and non-goals.

### STORY-004: Resume failed steps from validated checkpoints

- Short name: `failed-step-resume`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md` (6.2 Cross-run lineage; 9.4 Checkpoint validation; 14. Dependency Invalidation and Preserved Output Reuse; 15. Resume Relationship; 18. Failure and Stop Semantics; 20.2 Failed-step recovery; 21. Non-Goals)
- Dependencies: STORY-001, STORY-002
- Independent test: Given a source run with two completed logical steps and one failed logical step, trigger RecoverFromFailedStep and assert the follow-up run imports prior steps as preserved provenance, creates local executionOrdinal 1 for the failed step with lineageExecutionOrdinal 2, validates checkpoint evidence before work, and fails explicitly when preserved refs are missing or mismatched.
- Narrative: As an operator recovering a failed task, I need Resume to create a linked follow-up run that preserves completed prior steps and starts fresh work at the failed logical step from validated checkpoint evidence, with clear lineage instead of a silent full rerun.
- Assumptions: The existing recovery action can create a linked workflow execution and carry source execution references.
- Needs clarification: None

Acceptance criteria:
- Resume creates a linked follow-up workflow execution and does not continue the old failed step in place.
- The first newly executed failed step uses reason recover_from_failed_step, local run-scoped executionOrdinal 1, pinned source workflowId/runId/logicalStepId/sourceExecutionOrdinal, and optional lineageExecutionOrdinal for display.
- Completed prior steps are imported as preserved progress with preservedFrom workflowId, runId, logicalStepId, and executionOrdinal plus reusable output refs.
- Workspace restoration validates checkpoint evidence before any new agent/tool work or mutation starts.
- Resume rejects silent task input edits, silent full-rerun fallback, re-executing preserved prior steps, missing/corrupted/unauthorized/inconsistent checkpoint evidence, and conflating local attempt identity with lineage identity.
- Downstream steps after the resumed failed step execute normally only after the failed step passes and dependency invalidation rules are applied.

Requirements:
- Represent resume lineage separately from durable local attempt identity.
- Pin source workflow and run identifiers so provenance cannot drift.
- Materialize preserved prior steps from artifact/checkpoint refs rather than logs or UI projections.
- Fail with an explicit terminal state and actionable evidence when checkpoint validation or preserved output validation fails.
- Render or expose preserved/resumed provenance clearly enough for operator-facing step rows.

Owned source design coverage:
- `DESIGN-REQ-006`: Owns durable identity and lineage metadata.
- `DESIGN-REQ-011`: Owns checkpoint validation and fail-fast behavior.
- `DESIGN-REQ-017`: Owns dependency invalidation and preserved output reuse.
- `DESIGN-REQ-018`: Owns failed-step recovery semantics.
- `DESIGN-REQ-021`: Owns failure and stop classification.
- `DESIGN-REQ-023`: Owns concrete end-to-end examples as validation scenarios.
- `DESIGN-REQ-024`: Owns explicit exclusions and non-goals.

### STORY-005: Expose bounded attempt history to operators and APIs

- Short name: `attempt-surfaces`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md` (2. Layering and Source of Truth; 17. Operator and API Surfaces; 18. Failure and Stop Semantics; 21. Non-Goals; 22. Rollout Guidance)
- Dependencies: STORY-001
- Independent test: Query the default step ledger and expanded attempts API for a logical step with multiple attempts, then assert the default view shows only latest/current attempt with count and evidence refs while the expanded endpoint returns bounded attempt projections with lineage, reason, child refs, policy, git disposition, gate verdict, output refs, and terminal disposition.
- Narrative: As an operator or API client, I need task details and step-attempt endpoints to show latest attempt status, counts, evidence refs, gate summaries, lineage, and expanded attempt history without inlining large transcripts, diffs, or provider payloads.
- Assumptions: Existing Mission Control and execution APIs can add bounded projections without changing durable source-of-truth storage.
- Needs clarification: None

Acceptance criteria:
- The default task detail view or API projection shows each logical step latest/current attempt, attempt count, blocked or failed gate summary, latest evidence refs, and preserved/resumed provenance markers.
- Expanded attempt history endpoints expose bounded attempt projections for a workflow/logical step/attempt using artifact refs instead of large inline transcripts, diffs, provider payloads, or verification reports.
- The step ledger remains a read projection downstream of workflow state, artifact linkage, and git state; app DB projections are repairable and do not invent attempt truth.
- Attempt list responses include workflowId, runId, logicalStepId, latestStepExecution, executionOrdinal, status, reason, gitDisposition, gateVerdict, and manifestRef at minimum.
- Failure and stop dispositions are visible enough for operators to understand whether work is retryable, blocked, needs human attention, discarded, superseded, failed with remaining work, or accepted.
- Default UI does not expose full immutable attempt history unless the operator opens an expanded surface.

Requirements:
- Expose bounded GET surfaces for steps and step attempts consistent with the design examples.
- Keep large logs, diffs, transcripts, provider payloads, and reports behind artifact refs.
- Mark preserved/resumed provenance in step rows.
- Support degraded reads from projections while preserving workflow/artifact/git authority.
- Follow rollout guidance by enabling manifest/evidence visibility before broader checkpoint and autonomous loop UX.

Owned source design coverage:
- `DESIGN-REQ-002`: Owns layered authority and append-only/projection behavior.
- `DESIGN-REQ-020`: Owns operator and API attempt surfaces.
- `DESIGN-REQ-021`: Owns failure and stop classification.
- `DESIGN-REQ-024`: Owns explicit exclusions and non-goals.
- `DESIGN-REQ-025`: Owns phased rollout sequencing.

## Coverage Matrix

- `DESIGN-REQ-001` Execution primitive scope -> STORY-001
- `DESIGN-REQ-002` Layered source of truth -> STORY-001, STORY-005
- `DESIGN-REQ-003` Explicit per-attempt model -> STORY-001
- `DESIGN-REQ-004` Terminology separation -> STORY-001
- `DESIGN-REQ-005` Core invariants -> STORY-001
- `DESIGN-REQ-006` Run-scoped identity and lineage -> STORY-001, STORY-004
- `DESIGN-REQ-007` Idempotency keys for side effects -> STORY-001
- `DESIGN-REQ-008` Step execution manifest contract -> STORY-001
- `DESIGN-REQ-009` Immutable context, retrieval, and memory inputs -> STORY-002
- `DESIGN-REQ-010` Checkpoint contract and boundaries -> STORY-002
- `DESIGN-REQ-011` Checkpoint validation -> STORY-002, STORY-004
- `DESIGN-REQ-012` Workspace and git policy -> STORY-002
- `DESIGN-REQ-013` Side-effect guardrails -> STORY-002
- `DESIGN-REQ-014` Activity and worker ownership boundaries -> STORY-003
- `DESIGN-REQ-015` Gated iteration loops -> STORY-003
- `DESIGN-REQ-016` Gate verdicts and budgets -> STORY-003
- `DESIGN-REQ-017` Dependency invalidation and preserved output reuse -> STORY-003, STORY-004
- `DESIGN-REQ-018` Failed-step resume relationship -> STORY-004
- `DESIGN-REQ-019` Managed runtime attempt semantics -> STORY-003
- `DESIGN-REQ-020` Operator and API attempt surfaces -> STORY-005
- `DESIGN-REQ-021` Failure and stop semantics -> STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-022` Security posture -> STORY-002
- `DESIGN-REQ-023` Examples as validation scenarios -> STORY-003, STORY-004
- `DESIGN-REQ-024` Non-goals -> STORY-003, STORY-004, STORY-005
- `DESIGN-REQ-025` Phased rollout guidance -> STORY-005
- `DESIGN-REQ-026` Constitution alignment -> STORY-001

## Dependencies

- `STORY-001` depends on: None
- `STORY-002` depends on: STORY-001
- `STORY-003` depends on: STORY-001, STORY-002
- `STORY-004` depends on: STORY-001, STORY-002
- `STORY-005` depends on: STORY-001

## Out Of Scope

- Creating or modifying `spec.md` files; this breakdown is a pre-specification handoff only.
- Creating directories under `specs/`; downstream `/speckit.specify` owns that step.
- Implementing code, generating task lists, creating Jira issues, publishing pull requests, or performing Jira transitions.
- Redefining product Step Types, executable tool contracts, generic artifact storage internals, Temporal Activity retry policy, provider launch internals, or full run-history UI.
- Infinite autonomous loops, hidden transcript attempts, large workflow-state logs/diffs, automatic replay of all external side effects, direct repo memory commits from failed attempts, separate recovery systems, full default attempt history UI, and forcing autonomous reattempt semantics onto every Step Type.

## Coverage Gate Result

PASS - every major design point is owned by at least one story.
