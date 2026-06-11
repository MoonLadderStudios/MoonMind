# Step Executions and Checkpointing — Implementation Gap Plan

Status: Execution plan (disposable; not canonical)
Created: 2026-06-10
Canonical design: `docs/Steps/StepExecutionsAndCheckpointing.md`

This document tracks the remaining wiring between the canonical desired-state
design and production `MoonMind.UserWorkflow` orchestration. It exists under
`docs/tmp/` per Constitution principle XII. Delete it when the gaps close.

---

## 1. Verified current state (2026-06-10)

The earlier gap assessment ("write-side helpers are unwired") was checked
against the codebase and is **partially out of date**. Verified wiring:

| Capability | Status | Evidence |
| --- | --- | --- |
| Step Execution identity / ordinal increment | Wired | `_mark_step_running(increment_attempt=True)` in `moonmind/workflows/temporal/workflows/run.py` |
| Manifest start writes (typed) | Wired | `_record_step_execution_manifest_start` (run.py:810) → `StepExecutionManifestModel` → `_write_step_execution_manifest` (run.py:970), called at run.py:6036 |
| Manifest terminal writes (typed) | Wired | `_record_step_execution_manifest_terminal` (run.py:891), called at run.py:4828, 4862, 5008, 5063, 5125 with status + terminal disposition + git effect + dependency effects |
| Manifest start writes (untyped, duplicate) | Wired — **must be consolidated** | `_record_step_execution_manifest_started` (run.py:2083) using `build_step_execution_manifest_payload` (step_executions.py:471), called at run.py:4540 |
| Ledger manifest evidence (latest + history refs) | Wired | `mark_step_execution_manifest_evidence` (step_ledger.py:582), called at run.py:2187 |
| Workspace-policy launch validation | Wired | `_workspace_policy_launch_blocked` → blocked manifest + `_record_workspace_policy_launch_block` |
| Gated reattempt loop with verdicts | Wired | run.py:4480–5200+: `ADDITIONAL_WORK_NEEDED` / `FULLY_IMPLEMENTED` drive new Step Executions (reason `quality_gate_failed`); bounded by MoonSpec remediation plan steps |
| Publication gating on gate verdicts | Wired | `_apply_blocking_moonspec_gate_to_publish`; `_step_has_accepted_output_evidence` gates downstream handoff |
| Downstream invalidation | Wired | `invalidate_downstream_steps_for_changed_output` (step_ledger.py:302), called via `_record_downstream_dependency_effects` (run.py:1822, 5121) |
| Read APIs | Wired | `api_service/api/routers/executions.py`: `GET /{workflow_id}/steps`, `.../steps/{logical_step_id}/step-executions`, `.../step-executions/{execution_ordinal}`, `POST /{workflow_id}/recover-from-failed-step`, `POST /{workflow_id}/recover-from-selected-step` |
| Content types | Code canonical | `STEP_EXECUTION_MANIFEST_CONTENT_TYPE`, `STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE` (schemas/temporal_models.py:62–67); docs aligned 2026-06-10 |

Confirmed **not wired** (helpers/tests only, or absent):

| Gap | Evidence |
| --- | --- |
| Checkpoint capture at boundaries | `step_checkpoints.py` helpers (`build_step_checkpoint_payload`, `validate_step_checkpoint*`) have no workflow/activity call sites; no checkpoint artifacts are written |
| Checkpoint-backed restoration in Resume | No workspace restore path consumes validated checkpoints |
| Dedicated activities | No `step_execution.*`, `step_checkpoint.*`, `workspace.capture_checkpoint`, `workspace.apply_policy`, `workspace.classify_git_effect` in `activity_catalog.py` (manifests go through generic `artifact.create` / `artifact.write_complete` — acceptable per design §12, listed for completeness) |
| Context bundles | Both manifest writers emit `context={}`; no immutable digest-addressed context bundle artifact exists |
| Side-effect classification in terminal manifests | Terminal writer emits `sideEffects={}`; compensation plans attach only in the duplicate untyped start path |
| Typed gate verdicts | Verdict carried as ad-hoc strings (`quality_gate_verdict: str \| None`, `_moonspec_gate_verdict`), not the §13.1 structured gate result contract |
| Disposition-gated handoff | `terminal_disposition` recorded but not consulted when gating downstream external handoff |
| Retrieval/memory manifests | No retrieval manifest refs in context; no memory promotion state enum in code |
| Autonomous story/PRD loop | Not implemented beyond MoonSpec remediation presets |

---

## 2. Work packages

Each WP must add or update tests at the workflow/activity boundary (not only
unit tests), including one in-flight payload compatibility case and one
degraded-input case, per repo testing instructions.

### WP1 — Consolidate manifest writers (prerequisite, small)

The design now requires exactly one manifest write path per identity
(design §7). Today there are two:

- typed: `_record_step_execution_manifest_start` / `_terminal` (run.py:810/891)
- untyped: `_record_step_execution_manifest_started` (run.py:2083) + `build_step_execution_manifest_payload`

Work: fold the untyped path's unique behavior (reattempt compensation plan,
side-effect records, budget payload) into the typed path; delete the untyped
writer and `build_step_execution_manifest_payload` entirely (Constitution
XIII — no partial migration). Update call site run.py:4540 and tests.

Acceptance: one writer; compensation/side-effect/budget data appears in typed
manifests; grep finds no references to the deleted helper.

### WP2 — Checkpoint capture and validation wiring

Wire `step_checkpoints.py` into orchestration:

1. Capture checkpoint evidence at the canonical boundaries (`after_prepare`,
   `before_execution`, `after_execution`, `after_gate`, `before_publication`,
   `before_recovery_restoration`) via a sandbox-queue activity
   (`workspace.capture_checkpoint` or equivalent), idempotent under
   `{stepExecutionId}:checkpoint:{boundary}` keys.
2. Persist with `STEP_EXECUTION_CHECKPOINT_CONTENT_TYPE`; attach
   `checkpointBeforeRef` / `checkpointAfterRef` to manifests and ledger rows.
3. Call `validate_step_checkpoint` before applying any workspace policy that
   requires checkpoint evidence (§9.3); surface typed
   `StepCheckpointValidationFailureCode` values in diagnostics.

Acceptance: reattempts under `restore_pre_execution` /
`apply_previous_execution_diff_to_clean_baseline` consume real checkpoint
artifacts; launch validation rejects policies whose evidence is missing
(already partially wired) with typed failure codes.

### WP3 — Checkpoint-backed Resume restoration

1. `before_recovery_restoration` validation in the recovery execution before
   any new work (pin source workflowId/runId, task input snapshot, plan
   digest, preserved-step refs).
2. Workspace restoration from validated checkpoint kind; explicit failure
   (no silent full rerun) on validation failure.
3. Preserved-step output reuse blocked unless checkpoint proves contract
   satisfaction; `requires_revalidation` rows must block downstream reuse.

### WP4 — Side-effect classification and disposition-gated handoff

1. Populate `sideEffects` in terminal manifests from the side-effect record
   helpers (accepted git effects, external records, compensation plans).
2. Gate publication/Jira/merge activities on `terminalDisposition ==
   "accepted"` for the producing step, in addition to the existing MoonSpec
   gate-verdict blocking.
3. Enforce non-idempotent external work policy (§11) at the activity boundary.

### WP5 — Typed gate verdict contract

Replace ad-hoc verdict strings with the §13.1 structured gate result model
(verdict, confidence, validatedRefs, remainingWorkRef,
workspacePolicyRecommendation, recommendedNextAction). The parent workflow
branches on the typed model only. Preserve in-flight compatibility for runs
carrying string verdicts (read-side normalization → typed invalid/degraded
decision) or document the cutover.

### WP6 — Context bundles, retrieval and memory manifests

1. Build immutable digest-addressed context bundles per attempt; stop writing
   `context={}`.
2. Add retrieval manifest capture (`retrieval.build_step_execution_manifest`).
3. Add memory promotion state enum (code currently has none) and proposal
   manifests; gate repo-level memory writes behind publication gates.

### WP7 — Autonomous story/PRD loop

Compile PRD/story items into logical steps using WP1–WP6 primitives:
independent quality gates, commit-only-accepted-attempts, budget + at least
one non-attempt stop dimension recorded in manifests (§13.2).

---

## 3. Ordering and dependencies

```text
WP1 ──► WP2 ──► WP3
  │       └───► WP4
  └───► WP5 (parallel to WP2)
WP2 + WP5 ──► WP6 ──► WP7
```

WP1 is small and unblocks everything; do it first. WP3 and WP4 can proceed in
parallel after WP2.

## 4. Resolved decisions

- Checkpoint content type: `application/vnd.moonmind.step-execution-checkpoint+json;version=1`
  is canonical (code already matched; `docs/Steps/StepExecutionsAndCheckpointing.md`
  and `docs/Temporal/StepLedgerAndProgressModel.md` §11.3 aligned 2026-06-10;
  `step-checkpoint` and `step-resume-checkpoint` spellings deleted).
- Idempotency keys: `{stepExecutionId}:{operation}` (no namespace prefix),
  matching `step_execution_operation_idempotency_key`.
- API vocabulary: `step-executions` keyed by `execution_ordinal`; "attempts"
  removed from API surface language.
