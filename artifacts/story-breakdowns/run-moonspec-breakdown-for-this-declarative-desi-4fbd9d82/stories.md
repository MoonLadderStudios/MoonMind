# MoonSpec Breakdown — Step Executions and Checkpointing

- **Source design title:** Step Executions and Checkpointing
- **Source document reference path:** `docs/Steps/StepExecutionsAndCheckpointing.md`
- **Story extraction date:** 2026-06-10T04:59:22Z
- **Story output mode:** `jira`
- **Coverage gate:** `PASS - every major design point is owned by at least one story.`

> Note: the task prompt referenced `docs/Steps/StepAttemptsAndCheckpointing.md`. The canonical file in the repository is `docs/Steps/StepExecutionsAndCheckpointing.md` (the design renames "Attempt" to "Step Execution"). That existing repo file was used as the original declarative document and its path is preserved in `source.referencePath` and in every story's `sourceReference.path`.

---

## Design Summary

The document defines the desired-state MoonMind primitive **Step Executions with Checkpointing**: re-executing a logical step as a new, explicitly identified attempt while controlling which prior side effects are preserved, restored, ignored, invalidated, promoted, or superseded. It establishes a layered source-of-truth model (resolved plan artifact, workflow state, step ledger projection, immutable step execution manifest artifacts, checkpoints, git state, and a repairable DB projection), run-scoped attempt identity with optional cross-run lineage, deterministic idempotency keys, immutable digest-addressed context bundles with retrieval and memory inputs, a checkpoint contract (boundaries/kinds/validation), workspace and git policies with a hard accepted-output rule, side-effect classification and external guardrails, an activity/worker ownership boundary, workflow-owned gated iteration with structured verdicts and budgets, dependency invalidation, failed-step recovery (Resume), managed-runtime context policy, operator/API surfaces, failure/stop semantics, and security guardrails.

It is bounded by explicit non-goals and an in-flight-compatibility / phased-rollout constraint, and it explicitly does **not** redefine product-facing Step Types, executable Tool contracts, generic artifact storage internals, Temporal Activity retry policy, provider runtime launch internals, or full run-history product UI.

---

## Coverage Points (`DESIGN-REQ-*`)

| ID | Title | Type | Source section(s) |
| --- | --- | --- | --- |
| DESIGN-REQ-001 | Step Execution primitive and layered source of truth | state-model | §1, §2, §3 |
| DESIGN-REQ-002 | Run-scoped Step Execution identity and ordinals | state-model | §4, §6.1 |
| DESIGN-REQ-003 | Cross-run lineage provenance | state-model | §6.2 |
| DESIGN-REQ-004 | Deterministic idempotency keys for side-effecting activities | constraint | §6.3 |
| DESIGN-REQ-005 | Step Execution manifest contract | artifact | §7, §7.1–7.3 |
| DESIGN-REQ-006 | Context bundle, retrieval manifest, and memory promotion inputs | artifact | §8, §8.1, §8.2 |
| DESIGN-REQ-007 | Checkpoint contract: boundaries, kinds, policy requirements, validation | artifact | §9, §9.1–9.4 |
| DESIGN-REQ-008 | Workspace and git policy with accepted-output rule | state-model | §10, §10.1, §10.2 |
| DESIGN-REQ-009 | Side-effect classes and external guardrails | security | §11 |
| DESIGN-REQ-010 | Activity surface and worker boundaries | integration | §12 |
| DESIGN-REQ-011 | Gated iteration loops: verdicts, budgets, stop rules | requirement | §13, §13.1, §13.2 |
| DESIGN-REQ-012 | Dependency invalidation and preserved output reuse | state-model | §14 |
| DESIGN-REQ-013 | Failed-step recovery (Resume) relationship | requirement | §15 |
| DESIGN-REQ-014 | Managed runtime relationship and runtime context policy | integration | §16, §16.1 |
| DESIGN-REQ-015 | Operator and API surfaces | observability | §17 |
| DESIGN-REQ-016 | Failure and stop semantics / terminal dispositions | state-model | §18 |
| DESIGN-REQ-017 | Security and side-effect guardrails | security | §19 |
| DESIGN-REQ-018 | Non-goals and explicit exclusions | non-goal | §21 |
| DESIGN-REQ-019 | In-flight Temporal compatibility and versioned cutover | migration | §5 (inv. 17), §22 |
| DESIGN-REQ-020 | Core invariants enforced as workflow guarantees | constraint | §5 |

---

## Ordered Story Candidates

Stories are ordered by dependency, risk, and value. High-risk contract/state stories (identity, checkpoints, workspace/git, gating) come early to unlock reliable TDD for later stories.

### STORY-001 — Step Execution identity and immutable manifest evidence model
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§1, §2, §3, §4, §5, §6.1, §6.2, §7)
- **Why:** Foundational primitive and source-of-truth layering everything else depends on.
- **Independent test:** Two semantic executions of one logical step yield distinct monotonic ordinals, deterministic identifiers, an immutable append-only manifest each with valid reason/status/disposition, optional lineage pinning source workflowId/runId, and a compact refs-only workflow projection.
- **Owns:** DESIGN-REQ-001, -002, -003, -005, -020
- **Dependencies:** none

### STORY-002 — Step ledger projection and bounded attempt-history operator/API surfaces
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§2 ledger/projection rows, §17)
- **Why:** Attempt evidence is only valuable if observable.
- **Independent test:** Default view returns latest attempt + count + gate summary; expanded API returns the bounded full attempt list with required fields; responses carry artifact refs, never inlined transcripts/diffs/reports.
- **Owns:** DESIGN-REQ-015
- **Dependencies:** STORY-001

### STORY-003 — Immutable context bundle with retrieval manifest and memory promotion inputs
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§8, §8.1, §8.2)
- **Why:** Reliable reattempts and gate diagnosis require explicit, immutable, reproducible per-attempt context.
- **Independent test:** Context bundle is digest-addressed, immutable after start, refs-only; differing retrieval/memory context across attempts is recorded via manifest refs; a failed attempt's memory proposal stays non-durable.
- **Owns:** DESIGN-REQ-006
- **Dependencies:** STORY-001

### STORY-004 — Checkpoint capture, kinds, policy requirements, and validation
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§9, §9.1–9.4)
- **Why:** Checkpoints are the durable recovery evidence that makes re-execution and resume truthful.
- **Independent test:** Checkpoints created at each boundary with idempotent writes and kind-specific restore strength; validation rejects missing/corrupted/unauthorized/plan-mismatched/policy-incompatible checkpoints before any agent launch or workspace mutation.
- **Owns:** DESIGN-REQ-007
- **Dependencies:** STORY-001

### STORY-005 — Workspace and git policy with the accepted-output rule
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§10, §10.1, §10.2)
- **Why:** Repeated coding mutates the workspace; safe reattempts need explicit policy and a hard accepted-output rule.
- **Independent test:** Each policy with its required checkpoint records the policy in metadata, classifies git effect correctly, marks a committing pass as accepted, and never marks a dirty failed tree as accepted.
- **Owns:** DESIGN-REQ-008
- **Dependencies:** STORY-001, STORY-004

### STORY-006 — Side-effect classification, idempotency keys, and external handoff guardrails
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§6.3, §11)
- **Why:** Reattempts are unsafe unless external/local side effects are explicitly classified and guarded.
- **Independent test:** Each side-effect class is classified before advancement; idempotency keys derive from attempt identity and are stable across retries; non-idempotent external work is blocked in autonomous reattempts absent explicit policy; publication-class effects require gate-approved state.
- **Owns:** DESIGN-REQ-004, DESIGN-REQ-009
- **Dependencies:** STORY-001

### STORY-007 — Workflow-owned gated iteration with structured verdicts, budgets, and stop semantics
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§13, §13.1, §13.2, §18, §20.1)
- **Why:** Gated iteration makes repeated work safe and bounded; the gate is the only normal path to publication.
- **Independent test:** A verify/remediate loop returning ADDITIONAL_WORK_NEEDED until budget exhaustion branches only on structured verdicts, stops before publication with a deterministic terminal disposition and published evidence, requires FULLY_IMPLEMENTED before publication, and skips/blocks/invalidates/revalidates dependent downstream steps on gate failure.
- **Owns:** DESIGN-REQ-011, DESIGN-REQ-016
- **Dependencies:** STORY-001, STORY-005, STORY-006

### STORY-008 — Dependency invalidation and preserved output reuse
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§14, §20.4)
- **Why:** Changed accepted outputs can silently invalidate downstream work in both resume and autonomous loops.
- **Independent test:** Changing an accepted upstream output marks dependents for invalidation/revalidation; preserved outputs are reused only when a checkpoint proves contract satisfaction; revalidation results are structured evidence.
- **Owns:** DESIGN-REQ-012
- **Dependencies:** STORY-001, STORY-004

### STORY-009 — Failed-step recovery (Resume) from validated checkpoints
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§15, §20.2)
- **Why:** Failed-step recovery is a primary, high-value consumer of the primitive composing checkpoints, lineage, and invalidation.
- **Independent test:** Resuming a run with steps 1–2 succeeded and step 3 failed preserves steps 1–2 from refs (no rerun), starts step 3 as a new local execution from a validated checkpoint with lineage pinned to the source run, and fails explicitly on bad checkpoint evidence rather than degrading to full rerun.
- **Owns:** DESIGN-REQ-013
- **Dependencies:** STORY-004, STORY-008

### STORY-010 — Managed runtime relationship and runtime context policy
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§16, §16.1)
- **Why:** Runtimes execute attempts but must not own attempt semantics; clean-context reattempts must be explicit.
- **Independent test:** A managed-runtime attempt shows MoonMind owning identity/checkpoint/advancement, a runtime context policy recorded separately from the workspace policy, agent recommendations that do not create attempts or mutate the ledger, and external-agent attempts that still record attempt identity and available evidence.
- **Owns:** DESIGN-REQ-014
- **Dependencies:** STORY-001, STORY-005

### STORY-011 — Activity surface and worker boundary contract
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§12)
- **Why:** The orchestration/side-effect ownership boundary is a non-negotiable platform contract keeping payloads compact and replay-safe.
- **Independent test:** Workflow code performs no direct IO/git/provider/memory operations; boundary results are compact typed payloads with large outputs passed as refs; side-effecting activities are idempotent or key-guarded.
- **Owns:** DESIGN-REQ-010
- **Dependencies:** STORY-001

### STORY-012 — Security and side-effect guardrails for Step Executions
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§19)
- **Why:** Reattempt/recovery surfaces touch credentials, artifacts, external systems, and the filesystem.
- **Independent test:** Generated bundles/manifests/checkpoints/retrieval/memory contain no secrets; artifact reads enforce authorization; restores attempting path traversal or out-of-workspace writes are rejected; failed-attempt diagnostics are sanitized before display/publication.
- **Owns:** DESIGN-REQ-017
- **Dependencies:** STORY-001, STORY-003, STORY-004

### STORY-013 — Step Execution non-goals and scope guardrails
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§21)
- **Why:** Non-goals must be explicitly owned so future work does not silently violate stated exclusions.
- **Independent test:** Implemented behaviors are reviewed against each non-goal (bounded loops, no single-transcript hiding, refs-only state, retry vs reattempt distinction, no automatic external replay, no durable repo memory from failed attempts, one shared recovery foundation, latest-only default view, no forced autonomous Step Types) and none are violated.
- **Owns:** DESIGN-REQ-018
- **Dependencies:** STORY-001

### STORY-014 — In-flight Temporal compatibility and versioned cutover for Step Execution contracts
- **Source reference:** `docs/Steps/StepExecutionsAndCheckpointing.md` (§5 invariant 17, §22)
- **Why:** Core invariant 17 and the phased rollout require payload/contract changes never break in-flight workflows.
- **Independent test:** A Step Execution/checkpoint payload change keeps in-flight runs on the prior shape working (compatibility test) or exercises an explicit versioned cutover, covering unknown/blank/new field values per the repo's workflow-boundary testing rules.
- **Owns:** DESIGN-REQ-019
- **Dependencies:** STORY-001

---

## Coverage Matrix

| Coverage point | Owning story (mode: `jira`) |
| --- | --- |
| DESIGN-REQ-001 | STORY-001 |
| DESIGN-REQ-002 | STORY-001 |
| DESIGN-REQ-003 | STORY-001 |
| DESIGN-REQ-004 | STORY-006 |
| DESIGN-REQ-005 | STORY-001 |
| DESIGN-REQ-006 | STORY-003 |
| DESIGN-REQ-007 | STORY-004 |
| DESIGN-REQ-008 | STORY-005 |
| DESIGN-REQ-009 | STORY-006 |
| DESIGN-REQ-010 | STORY-011 |
| DESIGN-REQ-011 | STORY-007 |
| DESIGN-REQ-012 | STORY-008 |
| DESIGN-REQ-013 | STORY-009 |
| DESIGN-REQ-014 | STORY-010 |
| DESIGN-REQ-015 | STORY-002 |
| DESIGN-REQ-016 | STORY-007 |
| DESIGN-REQ-017 | STORY-012 |
| DESIGN-REQ-018 | STORY-013 |
| DESIGN-REQ-019 | STORY-014 |
| DESIGN-REQ-020 | STORY-001 (enforcement distributed across STORY-001…STORY-014) |

---

## Dependencies Between Stories

- STORY-001 → (none)
- STORY-002 → STORY-001
- STORY-003 → STORY-001
- STORY-004 → STORY-001
- STORY-005 → STORY-001, STORY-004
- STORY-006 → STORY-001
- STORY-007 → STORY-001, STORY-005, STORY-006
- STORY-008 → STORY-001, STORY-004
- STORY-009 → STORY-004, STORY-008
- STORY-010 → STORY-001, STORY-005
- STORY-011 → STORY-001
- STORY-012 → STORY-001, STORY-003, STORY-004
- STORY-013 → STORY-001
- STORY-014 → STORY-001

---

## Out-of-Scope Items and Rationale

These are explicit document non-goals and external boundaries; they are owned as guardrails (chiefly STORY-013, with security/compat guardrails in STORY-012/STORY-014) and are not implemented as positive features:

- Product-facing Step Types, executable Tool contracts, generic artifact storage internals, Temporal Activity retry policy, provider runtime launch internals, and full run-history product UI (§1 — redefinition explicitly excluded; see `docs/Steps/StepTypes.md` and related docs).
- Infinite autonomous loops; hiding attempts inside a single agent transcript; storing large diffs/logs in workflow state; treating Temporal Activity retries as semantic reattempts; automatically replaying every external side effect; committing failed-attempt memory proposals directly to the repo; separate recovery systems for resume vs autonomous loops; exposing full immutable attempt history in the default task detail view; forcing every Step Type into autonomous reattempt semantics (§21).

---

## Coverage Gate Result

```text
PASS - every major design point is owned by at least one story.
```

---

## Downstream Notes

- **Recommended first story for `/speckit.specify`:** STORY-001 — Step Execution identity and immutable manifest evidence model.
- **Stories with unresolved `[NEEDS CLARIFICATION]` markers:** none.
- No `spec.md` files were created and no directories under `specs/` were created during this breakdown.
- TDD remains the default strategy for downstream `/speckit.plan`, `/speckit.tasks`, and `/speckit.implement`.
- Run `/speckit.verify` after implementation to compare final behavior against the original design preserved through specify.
