# Story Breakdown: Step Executions and Checkpointing

- Source design: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Requested path: `docs\Steps\StepAttemptsAndCheckpointing.md`
- Resolution note: requested path/name was not present as a readable repo file; used the readable canonical document.
- Extracted at: `2026-06-09T21:41:29Z`
- Output mode: `jira`

## Design Summary

The design defines Step Executions with Checkpointing as MoonMind's execution-plane primitive for safely repeating work. It separates logical steps from semantic attempts and Temporal Activity retries, stores compact workflow state with artifact-backed evidence, and requires explicit identity, lineage, context, checkpoint, workspace, side-effect, gate, budget, and stop-rule contracts before work can repeat. The target behavior covers managed runtime boundaries, failed-step recovery, autonomous gated loops, dependency invalidation, operator/API inspection surfaces, rollout order, non-goals, and security constraints that prevent hidden side effects or raw secret exposure.

## Coverage Points

- `DESIGN-REQ-001` (requirement, 1. Purpose): Step re-execution primitive - Repeat work is a new Step Execution with explicit preservation, restoration, invalidation, promotion, or supersession policy.
- `DESIGN-REQ-002` (constraint, 1. Purpose; 4. Terminology): Distinct from Step Types and Activity retries - Step Executions are execution-plane attempts, not authoring Step Types, tool contracts, or low-level Temporal retries.
- `DESIGN-REQ-003` (state-model, 2. Layering and Source of Truth): Layered source of truth - Plans, workflow state, ledger projections, manifests, checkpoints, git, and DB projections each have defined authority.
- `DESIGN-REQ-004` (artifact, 2; 7. Step Execution Manifest Contract): Append-only manifest evidence - Full attempt evidence lives in immutable artifact-backed manifests while workflow state carries compact refs.
- `DESIGN-REQ-005` (integration, 2; 17. Operator and API Surfaces): Latest attempt default with expanded history - The ledger shows current/latest attempts by default and expanded surfaces expose bounded attempt history.
- `DESIGN-REQ-006` (requirement, 3. Desired-State Summary): Explicit launch inputs for repeats - Before a reattempt, MoonMind knows source attempt, reason, context, workspace, reuse, side effects, idempotency, budget, and stop rule.
- `DESIGN-REQ-007` (state-model, 5; 6. Identity, Lineage, and Idempotency): Run-scoped identity plus optional lineage - Durable identity uses workflowId, runId, logicalStepId, and executionOrdinal; lineage is display/provenance only.
- `DESIGN-REQ-008` (security, 6.3 Idempotency keys): Stable idempotency keys - Side-effecting activities derive stable keys from namespace, workflow, run, logical step, attempt, and operation.
- `DESIGN-REQ-009` (state-model, 7.1-7.3): Bounded reasons, statuses, and dispositions - Attempt lifecycle metadata is bounded and rich explanation remains artifact-backed.
- `DESIGN-REQ-010` (artifact, 8. Context Bundle, Retrieval, and Memory Inputs): Immutable context bundles - Attempt-visible context is immutable, digest-addressed, compact, and records workspace, retrieval, memory, runtime, and gate policy.
- `DESIGN-REQ-011` (artifact, 8.1 Retrieval context): Retrieval as explicit attempt input - RAG inputs are captured in retrieval manifests with query/selector, index, refs, filters, exclusions, and safe summaries.
- `DESIGN-REQ-012` (state-model, 8.2 Memory effects): Policy-governed memory proposals - Memory effects carry promotion states and failed attempts cannot silently write durable repo memory.
- `DESIGN-REQ-013` (artifact, 9; 9.1 Checkpoint boundaries): Durable checkpoints at boundaries - Checkpoints record enough evidence to restore or validate state without reconstructing from logs or UI projections.
- `DESIGN-REQ-014` (state-model, 9.2 Workspace checkpoint kinds): Explicit checkpoint kinds - Checkpoint kinds include git commits, patches, worktree archives, ephemeral workspace refs, and external state refs.
- `DESIGN-REQ-015` (security, 9.3; 9.4): Checkpoint validation before use - MoonMind validates source, input, plan, artifact authorization, workspace consistency, policy compatibility, and side-effect eligibility before launch.
- `DESIGN-REQ-016` (requirement, 10. Workspace and Git Policy): Workspace and git policies - Repeated attempts record a workspace policy such as continue, restore, apply diff to clean baseline, last passed commit, or fresh branch.
- `DESIGN-REQ-017` (state-model, 10.1; 10.2): Git effects and accepted outputs - Mutating attempts classify git effects and implementation steps succeed only with committed, pushed, typed accepted artifact, or explicit no-change output.
- `DESIGN-REQ-018` (security, 11. Side-Effect Classes and External Guardrails): Side-effect classification - Workspace, artifact, external, publication, provider-account, memory, and retrieval effects are classified before advancement.
- `DESIGN-REQ-019` (security, 11; 19. Security and Side-Effect Guardrails): External handoffs require gates - Publication, Jira movement, merge, deployment, and provider-account actions require gate-approved workflow state.
- `DESIGN-REQ-020` (integration, 12. Activity Surface and Worker Boundaries): Side effects stay in activities - MoonMind.Run owns orchestration; activities own file, git, artifact, provider, and memory side effects.
- `DESIGN-REQ-021` (integration, 12. Activity Surface and Worker Boundaries): Compact idempotent activity contracts - Activity results crossing workflow boundaries are compact and typed; large outputs are refs and side effects are idempotent or keyed.
- `DESIGN-REQ-022` (requirement, 13. Gated Iteration): Workflow-owned gated loops - The parent workflow owns retry allowance, repeated step selection, verdict branching, invalidation, stop state, and publication eligibility.
- `DESIGN-REQ-023` (integration, 13.1 Gate verdict contract): Structured gate verdicts - Gates return structured verdicts such as FULLY_IMPLEMENTED, ADDITIONAL_WORK_NEEDED, NO_DETERMINATION, BLOCKED, and FAILED_UNRECOVERABLE.
- `DESIGN-REQ-024` (constraint, 13.2; 18. Failure and Stop Semantics): Budgets and deterministic stops - Autonomous loops enforce explicit budgets and stop with deterministic disposition, evidence, and next action.
- `DESIGN-REQ-025` (state-model, 14. Dependency Invalidation and Preserved Output Reuse): Outputs tied to producing attempts - Downstream inputs resolve to specific producing Step Execution outputs unless explicitly refreshed.
- `DESIGN-REQ-026` (requirement, 14. Dependency Invalidation and Preserved Output Reuse): Downstream invalidation/revalidation - Changed accepted outputs invalidate or require revalidation of dependent downstream steps before reuse.
- `DESIGN-REQ-027` (requirement, 15. Resume Relationship): Failed-step recovery as linked execution - Resume creates a linked follow-up workflow and a new local Step Execution at the failed logical step.
- `DESIGN-REQ-028` (constraint, 15. Resume Relationship): No silent resume degradation - Resume cannot silently edit input, full-rerun, rerun preserved steps, or ignore invalid checkpoint evidence.
- `DESIGN-REQ-029` (integration, 16; 16.1 Runtime context policy): Runtime context policy separated - Managed runtimes execute attempts while records distinguish runtime/session policy from workspace policy.
- `DESIGN-REQ-030` (integration, 17. Operator and API Surfaces): Bounded operator/API inspection - APIs expose attempt counts, lineage, reasons, refs, git disposition, gate verdicts, diagnostics, and bounded history without large payloads.
- `DESIGN-REQ-031` (security, 19. Security and Side-Effect Guardrails): Security for artifacts and restoration - Context, manifests, checkpoints, retrieval, memory proposals, and diagnostics avoid raw secrets; restoration prevents unauthorized writes.
- `DESIGN-REQ-032` (non-goal, 21. Non-Goals): Explicit non-goals - The design excludes infinite loops, hidden transcript attempts, large workflow payloads, Activity retries as semantic attempts, automatic external replay, and full default history.
- `DESIGN-REQ-033` (migration, 22. Rollout Guidance): Phased rollout guidance - Rollout proceeds through manifests, checkpoints, gated loops, retrieval/memory manifests, and autonomous story loops.
- `DESIGN-REQ-034` (constraint, 23. Constitution Alignment): Constitution alignment - The design supports orchestration, operator-owned data, resiliency, docs-first traceability, and continuous improvement evidence.

## Ordered Story Candidates

### STORY-001: Create Step Execution manifest and identity contracts

- Short name: `execution-manifests`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 1. Purpose, 2. Layering and Source of Truth, 3. Desired-State Summary, 4. Terminology, 5. Core Invariants, 6. Identity, Lineage, and Idempotency, 7. Step Execution Manifest Contract, 22. Rollout Guidance, 23. Constitution Alignment
- Dependencies: None
- Independent test: Run a workflow-boundary/unit test that executes an initial step and one semantic re-execution, then asserts distinct run-scoped IDs, bounded lifecycle values, append-only manifest refs, compact workflow state, and no conflation with Temporal Activity retry attempts.
- Coverage IDs: DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-009, DESIGN-REQ-033, DESIGN-REQ-034

Acceptance criteria:
- A new semantic execution creates a new Step Execution keyed by workflowId, runId, logicalStepId, and executionOrdinal.
- Reasons, statuses, and terminal dispositions are restricted to bounded contract values.
- Full evidence is stored by manifest artifact refs while workflow state contains only compact refs and summaries.
- Failed attempt evidence remains available after later attempts.
- Documentation or code clearly distinguishes Step Executions from Step Types and Temporal Activity retries.

Needs clarification: None

### STORY-002: Build immutable attempt context, retrieval, and memory envelopes

- Short name: `attempt-context`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 8. Context Bundle, Retrieval, and Memory Inputs, 8.1 Retrieval context, 8.2 Memory effects, 19. Security and Side-Effect Guardrails, 22. Rollout Guidance
- Dependencies: STORY-001
- Independent test: Create two attempts with different retrieval or memory inputs and assert distinct digest-addressed context bundles, retrieval manifests, memory proposal state, and no raw secret-like values.
- Coverage IDs: DESIGN-REQ-010, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-031, DESIGN-REQ-033

Acceptance criteria:
- Context bundles are immutable once an attempt starts and include digest and builder version metadata.
- Bundles include compact refs for task input, plan, prepared inputs, workspace policy, baseline, prior evidence, retrieval, memory, runtime selection, and gate profile.
- Retrieval inputs are recorded as attempt-specific manifests.
- Memory effects use explicit promotion states and failed attempts do not silently write durable repo memory.
- Context-family artifacts do not include raw credentials.

Needs clarification: None

### STORY-003: Capture and validate checkpoints for workspace policies

- Short name: `checkpoint-policies`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 9. Checkpoint Contract, 9.1 Checkpoint boundaries, 9.2 Workspace checkpoint kinds, 9.3 Policy-to-checkpoint requirements, 9.4 Checkpoint validation, 10. Workspace and Git Policy, 10.1 Git effect states, 10.2 Accepted output rule, 19. Security and Side-Effect Guardrails, 22. Rollout Guidance
- Dependencies: STORY-001
- Independent test: Exercise checkpoint capture and reattempt launch for restore_pre_execution and apply_previous_execution_diff_to_clean_baseline, then assert missing or mismatched evidence is rejected before runtime launch.
- Coverage IDs: DESIGN-REQ-013, DESIGN-REQ-014, DESIGN-REQ-015, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-031, DESIGN-REQ-033

Acceptance criteria:
- Checkpoint artifacts record source, plan, input, workspace, and output evidence sufficient to restore or validate a boundary.
- Checkpoints are created idempotently at required boundaries.
- Workspace checkpoint kinds and restore strengths are explicit.
- Workspace policies declare minimum evidence and reject incompatible evidence before launch.
- Git effects are classified and implementation steps only succeed with accepted outputs.
- Restoration rejects unauthorized/path-traversal materialization.

Needs clarification: None

### STORY-004: Classify side effects and enforce external handoff guardrails

- Short name: `side-effect-guardrails`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 6.3 Idempotency keys, 11. Side-Effect Classes and External Guardrails, 12. Activity Surface and Worker Boundaries, 19. Security and Side-Effect Guardrails, 21. Non-Goals
- Dependencies: STORY-001, STORY-003
- Independent test: Simulate an attempt that writes artifacts, mutates git, and requests Jira transition; assert idempotency keys exist and publication/Jira movement is blocked until passing structured gate state exists.
- Coverage IDs: DESIGN-REQ-008, DESIGN-REQ-018, DESIGN-REQ-019, DESIGN-REQ-021, DESIGN-REQ-031, DESIGN-REQ-032

Acceptance criteria:
- Side-effecting activities accept or derive stable attempt-based idempotency keys.
- Attempt manifests classify side effects by class, operation, safe target, idempotency key, and disposition.
- Non-idempotent external work is forbidden in autonomous reattempts unless policy permits it.
- Publication, Jira transitions, merge, deployment, and provider-account actions require gate-approved workflow state.
- Cleanup or compensation is explicit, idempotent, and observable.

Needs clarification: None

### STORY-005: Implement workflow-owned gated reattempt loops

- Short name: `gated-reattempts`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 13. Gated Iteration, 13.1 Gate verdict contract, 13.2 Budgets and stop rules, 18. Failure and Stop Semantics, 20.1 Jira Orchestrate gated implementation, 20.3 Autonomous story loop, 21. Non-Goals, 22. Rollout Guidance
- Dependencies: STORY-001, STORY-003, STORY-004
- Independent test: Run a workflow-boundary test where verification returns ADDITIONAL_WORK_NEEDED twice and then FULLY_IMPLEMENTED, plus a budget-exhausted case; assert new Step Executions, workspace policy selection, blocked publication before pass, and deterministic terminal disposition.
- Coverage IDs: DESIGN-REQ-022, DESIGN-REQ-023, DESIGN-REQ-024, DESIGN-REQ-019, DESIGN-REQ-032, DESIGN-REQ-033, DESIGN-REQ-034

Acceptance criteria:
- Gates return structured verdicts and refs instead of requiring prose parsing.
- The parent workflow owns retry allowance, repeated step selection, verdict branching, invalidation, stop state, and publication eligibility.
- Autonomous loops enforce configured budgets.
- Budget exhaustion stops with deterministic terminal disposition plus evidence and next action.
- Agent recommendations cannot create hidden attempts or bypass workflow gates.

Needs clarification: None

### STORY-006: Resume failed steps with preserved progress and lineage

- Short name: `failed-step-resume`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 6.2 Cross-run lineage, 14. Dependency Invalidation and Preserved Output Reuse, 15. Resume Relationship, 18. Failure and Stop Semantics, 20.2 Failed-step recovery, 21. Non-Goals
- Dependencies: STORY-001, STORY-003
- Independent test: Create a source run with two succeeded steps and one failed step, then recover; assert preserved provenance refs, local executionOrdinal 1 for the failed step with lineage to source, checkpoint validation, and fail-before-launch on invalid refs.
- Coverage IDs: DESIGN-REQ-007, DESIGN-REQ-015, DESIGN-REQ-025, DESIGN-REQ-027, DESIGN-REQ-028, DESIGN-REQ-032

Acceptance criteria:
- RecoverFromFailedStep creates a linked follow-up workflow.
- The first newly executed failed step has local identity and lineage to source workflowId/runId/logicalStepId/executionOrdinal.
- Completed prior steps are preserved from source refs and not silently re-executed.
- Resume rejects task input edits, full-rerun fallback, missing/corrupt/unauthorized checkpoints, and plan-mismatched refs before launch.
- Operator-facing rows distinguish preserved steps, lineage, and local attempt numbers.

Needs clarification: None

### STORY-007: Invalidate and revalidate downstream attempt outputs

- Short name: `dependency-invalidation`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 14. Dependency Invalidation and Preserved Output Reuse, 18. Failure and Stop Semantics, 20.4 Dependency invalidation
- Dependencies: STORY-001, STORY-005, STORY-006
- Independent test: Accept upstream API contract v1, run downstream UI, then accept upstream v2; assert downstream consumers of v1 are requires_revalidation or pending and cannot be reused without structured validation evidence.
- Coverage IDs: DESIGN-REQ-025, DESIGN-REQ-026, DESIGN-REQ-027, DESIGN-REQ-028, DESIGN-REQ-023

Acceptance criteria:
- Downstream input refs resolve to specific producing Step Execution outputs unless explicitly refreshed.
- Accepted output changes record dependency effects and revalidation requirements.
- Preserved outputs include source workflowId, runId, logicalStepId, and executionOrdinal.
- Invalid preserved output refs cause Resume or reuse to fail before work.
- A gate can revalidate downstream outputs through structured evidence.

Needs clarification: None

### STORY-008: Route Step Execution work through activity and runtime boundaries

- Short name: `runtime-boundaries`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 12. Activity Surface and Worker Boundaries, 16. Managed Runtime Relationship, 16.1 Runtime context policy, 19. Security and Side-Effect Guardrails, 23. Constitution Alignment
- Dependencies: STORY-001, STORY-002, STORY-003
- Independent test: Run a workflow/activity boundary test that launches a managed agent attempt and asserts MoonMind creates attempt records, context, workspace policy validation, runtime child refs, output refs, gates, side-effect classification, and ledger updates while workflow code performs no direct file/git/provider mutation.
- Coverage IDs: DESIGN-REQ-020, DESIGN-REQ-021, DESIGN-REQ-029, DESIGN-REQ-031, DESIGN-REQ-034

Acceptance criteria:
- Workflow code does not read files, run git, inspect artifacts, call providers, or mutate memory directly.
- Activities own side effects and return compact typed results.
- Managed runtime attempts record runtime context policy separately from workspace policy.
- Runtime policy values include fresh agent run, new session epoch, same epoch as rare explicit mode, and external continuation.
- External runtimes record known identity, refs, side effects, and checkpoint evidence where available.

Needs clarification: None

### STORY-009: Expose bounded attempt history in operator and API surfaces

- Short name: `attempt-surfaces`
- Source reference: `docs/Steps/StepExecutionsAndCheckpointing.md`
- Source sections: 2. Layering and Source of Truth, 17. Operator and API Surfaces, 18. Failure and Stop Semantics, 21. Non-Goals, 22. Rollout Guidance
- Dependencies: STORY-001, STORY-006
- Independent test: Seed an execution with three Step Executions and query step list plus attempt-history endpoint; assert default response shows latest/current attempt and count, while expanded attempts include bounded projections and refs without large payloads.
- Coverage IDs: DESIGN-REQ-003, DESIGN-REQ-005, DESIGN-REQ-030, DESIGN-REQ-032, DESIGN-REQ-033

Acceptance criteria:
- Default task detail shows latest/current attempt, attempt count, gate summary, latest evidence refs, and preserved/resumed provenance.
- Expanded surfaces expose attempt number, lineage, reason, source, runtime child refs, context bundle ref, workspace policy, git disposition, gate verdict, diagnostics refs, and terminal disposition.
- API responses are bounded projections and do not inline large transcripts, diffs, provider payloads, or reports.
- Read-model projections do not invent attempt truth.
- Full immutable history is not forced into the default task detail view.

Needs clarification: None

## Coverage Matrix

- `DESIGN-REQ-001` Step re-execution primitive: STORY-001
- `DESIGN-REQ-002` Distinct from Step Types and Activity retries: STORY-001
- `DESIGN-REQ-003` Layered source of truth: STORY-001, STORY-009
- `DESIGN-REQ-004` Append-only manifest evidence: STORY-001
- `DESIGN-REQ-005` Latest attempt default with expanded history: STORY-009
- `DESIGN-REQ-006` Explicit launch inputs for repeats: STORY-001
- `DESIGN-REQ-007` Run-scoped identity plus optional lineage: STORY-001, STORY-006
- `DESIGN-REQ-008` Stable idempotency keys: STORY-004
- `DESIGN-REQ-009` Bounded reasons, statuses, and dispositions: STORY-001
- `DESIGN-REQ-010` Immutable context bundles: STORY-002
- `DESIGN-REQ-011` Retrieval as explicit attempt input: STORY-002
- `DESIGN-REQ-012` Policy-governed memory proposals: STORY-002
- `DESIGN-REQ-013` Durable checkpoints at boundaries: STORY-003
- `DESIGN-REQ-014` Explicit checkpoint kinds: STORY-003
- `DESIGN-REQ-015` Checkpoint validation before use: STORY-003, STORY-006
- `DESIGN-REQ-016` Workspace and git policies: STORY-003
- `DESIGN-REQ-017` Git effects and accepted outputs: STORY-003
- `DESIGN-REQ-018` Side-effect classification: STORY-004
- `DESIGN-REQ-019` External handoffs require gates: STORY-004, STORY-005
- `DESIGN-REQ-020` Side effects stay in activities: STORY-008
- `DESIGN-REQ-021` Compact idempotent activity contracts: STORY-004, STORY-008
- `DESIGN-REQ-022` Workflow-owned gated loops: STORY-005
- `DESIGN-REQ-023` Structured gate verdicts: STORY-005, STORY-007
- `DESIGN-REQ-024` Budgets and deterministic stops: STORY-005
- `DESIGN-REQ-025` Outputs tied to producing attempts: STORY-006, STORY-007
- `DESIGN-REQ-026` Downstream invalidation/revalidation: STORY-007
- `DESIGN-REQ-027` Failed-step recovery as linked execution: STORY-006, STORY-007
- `DESIGN-REQ-028` No silent resume degradation: STORY-006, STORY-007
- `DESIGN-REQ-029` Runtime context policy separated: STORY-008
- `DESIGN-REQ-030` Bounded operator/API inspection: STORY-009
- `DESIGN-REQ-031` Security for artifacts and restoration: STORY-002, STORY-003, STORY-004, STORY-008
- `DESIGN-REQ-032` Explicit non-goals: STORY-004, STORY-005, STORY-006, STORY-009
- `DESIGN-REQ-033` Phased rollout guidance: STORY-001, STORY-002, STORY-003, STORY-005, STORY-009
- `DESIGN-REQ-034` Constitution alignment: STORY-001, STORY-005, STORY-008

## Dependencies

- `STORY-001` depends on no prior stories.
- `STORY-002` depends on STORY-001.
- `STORY-003` depends on STORY-001.
- `STORY-004` depends on STORY-001, STORY-003.
- `STORY-005` depends on STORY-001, STORY-003, STORY-004.
- `STORY-006` depends on STORY-001, STORY-003.
- `STORY-007` depends on STORY-001, STORY-005, STORY-006.
- `STORY-008` depends on STORY-001, STORY-002, STORY-003.
- `STORY-009` depends on STORY-001, STORY-006.

## Out Of Scope

- Create or modify spec.md files: Breakdown only produces story candidates; specification happens in /speckit.specify.
- Create directories under specs/: Feature spec directories are reserved for the specify step.
- Implement the Step Execution system: This run only decomposes the declarative design into independently testable story candidates.
- Push commits, create PRs, Jira transitions, or publish reports: The managed step boundary authorizes breakdown artifact creation and a local commit only.

## Coverage Gate

PASS - every major design point is owned by at least one story.

## Recommended First Story

`STORY-001` - Create Step Execution manifest and identity contracts.

## Downstream Notes

- No `spec.md` files or `specs/` directories are created by this breakdown.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- Run `/speckit.verify` after implementation to compare final behavior against the original design preserved through specify.
