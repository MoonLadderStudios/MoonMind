# Checkpoint Resume Promotion

MoonLadderStudios/MoonMind#3278 defines checkpoint-backed Resume as a promoted
`codex_cli` capability, distinct from same-session continuation and full retry.
The immutable runtime descriptor states what deployed code can do. A separate
environment policy and readiness snapshot states whether a new recovery may be
offered and admitted. The admitted descriptor, readiness, activity routes,
boundary, phase, deployment generation, version, and digest are frozen before
Temporal starts; later policy changes do not alter an admitted history.

## Supported contract

Only `codex_cli` supports `worktree_archive`, owned by
`agent_runtime.capture_workspace_checkpoint` and
`agent_runtime.restore_workspace_checkpoint`. Supported boundary/phase pairs
are declared in the versioned descriptor. Other runtimes have explicit matrix
entries and do not inherit this support from `managed_cli`.

Promotion states are `disabled`, `shadow_capture`, `shadow_restore`, `internal`,
`limited`, `broad`, `ga`, and `paused`. Resume action exposure and execution
admission are always off during disabled, shadow, and paused states. Capture,
disposable shadow restore, action exposure, and new execution admission have
independent controls. All controls default off.

Promotion requires recorded objective evidence: at least 100 captures at 99%
success with verified digests and no authority, credential, or idempotency
violations; at least 50 source-destroying restores with complete integrity and
no source dependency or duplicate destination; at least 20 successful internal
Resumes without false eligibility or duplicate effects; required cold-resume CI
and replay suites; a rollback drill; and a green live canary for the exact
runtime image, CLI/protocol, archive implementation, and managed-run-store
generation. Promotion is deliberate and never time based.

Shadow restore uses a disposable destination, launches no agent, verifies the
manifest, file modes, symlinks, Git base and status digest, records evidence,
then destroys the destination. It must not retain or consult the source live
workspace.

## Promotion procedure

Keep controls disabled while collecting evidence. Run the repository gate:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/executions/test_runtime_capabilities.py tests/unit/workflows/executions/test_checkpoint_resume_admission.py tests/unit/workflows/executions/test_checkpoint_promotion.py tests/unit/workflows/temporal/workflows/test_run_recover_from_failed_step.py
./tools/test_integration.sh tests/integration/reliability
```

Run the credentialed source-destroying canary for the exact deployed generation.
Record it with cold-resume CI and shadow counts in
`FEATURE_FLAGS__CHECKPOINT_RESUME_PROMOTION_EVIDENCE_JSON`. Evidence includes
`deploymentGeneration`, `coldResumeCiPassed`, `shadowRestoreSamples`,
`shadowRestoreSuccesses`, `integrityFailures`, `duplicateSideEffects`,
`liveCanaryPassed`, and `recordedAt`; generation mismatch fails closed.

Set capture, shadow restore, action exposure, and admission independently only
after review. Also set generation, maximum archive size, explicit allowlists,
and all route/store readiness controls. Workflow Detail shows the stable reason,
runtime, checkpoint kind, promotion state, generation, capability set, and
digest.

## Metrics and pause conditions

Dashboards use the bounded dimensions `runtime_id`, `checkpoint_kind`,
`checkpoint_boundary`, `resume_phase`, `promotion_state`, `status`,
`stable_failure_code`, and `deployment_generation`. They cover eligibility and
admission, capture and restore counts/bytes/entries/duration/retries/integrity,
and Resume success/failure/full-retry requests/false eligibility/source
dependency/duplicate effects. IDs, repositories, users, paths, artifact refs,
and free-form errors are forbidden labels.

Any integrity mismatch, credential/runtime-home inclusion, managed workspace at
the sandbox resolver, false-positive eligibility, source dependency, duplicate
non-idempotent effect, ready-route divergence, capability mismatch, or missing
required main-branch journey pauses new admissions. Sustained failure-rate
alerts do the same. Pause never interrupts an activity mid-write.

Alert evaluation uses `evaluate_automatic_pause` with stable integrity,
duplicate-side-effect, false-eligibility, and restore-rate reason codes. The
dashboard series are `checkpoint_resume.eligibility_total`,
`checkpoint_resume.admission_total`, `checkpoint_resume.capture_total`,
`checkpoint_resume.shadow_restore_total`, `checkpoint_resume.restore_total`,
`checkpoint_resume.outcome_total`, and
`checkpoint_resume.automatic_pause_total`. Metric tags are restricted to
runtime, deployment generation, and stable outcome.

## Emergency rollback and drain

1. Set promotion to `paused`, disable action exposure and new admission, and
   optionally disable new capture. Never translate Resume into full retry.
2. Count admitted histories by frozen deployment generation and retain their
   agent-runtime worker queues until they finish, reconcile, are explicitly
   cancelled, or are deliberately migrated.
3. Reconcile partial restores idempotently. Preserve checkpoint, manifest,
   diagnostics, and partial-destination evidence before cleanup.
4. Investigate stable failure codes and the bounded dashboards. Confirm artifact
   and managed-run stores, route health, digest/generation equality, integrity,
   containment, and side-effect evidence.
5. Resume only after the required CI and a source-destroying live canary pass for
   the exact generation. Otherwise keep admission disabled and communicate full
   retry as a separate explicit operator choice.

The deployment drain controller feeds generation-scoped open-history and
pending-restoration counts through `evaluate_worker_drain`. Worker routes may be
removed only when `mayRemoveWorkerRoutes` is true. Preserve partial restore
diagnostics before cancellation or migration and never mutate a frozen action.

A new runtime such as `claude_code` requires its own descriptor, artifact
contract, capture and restore routes, boundary matrix, cold CI journey, live
canary, promotion evidence, and explicit allowlist entry. Family membership is
never sufficient.
