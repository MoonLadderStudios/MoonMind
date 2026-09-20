# Update mechanism: recreate-in-place

Status: in progress. Owner: update-moonmind.

## The failure that prompted this

`./tools/update-moonmind.sh` and the `deployment.update_compose_stack` workflow
(`mm:a0f4da4b-a5b1-4eba-bead-3dce7cb834e7`) are the same code path. Both died at
`ReleaseCohort.qualify_provider_managers`, holding promotion because
`provider-profile-manager:opencode` was running but unqueryable.

That singleton was genuinely wedged: 180 consecutive
`WORKFLOW_TASK_FAILED_CAUSE_NON_DETERMINISTIC_ERROR` events, all

> `[TMPRL1100] Nondeterminism error: Non-deprecated patch marker encountered for
> change provider-profile-manager-lease-tombstone-purge-v1, but there is no
> corresponding change command!`

pinned to a drained build with a stuck AutoUpgrade transition, so it could never
continue-as-new out of its own history.

## Why it wedged

The release mechanism runs several images against one Temporal task queue under
one worker-deployment name. At the time of failure, the live fleet, a
`mm-retained-*` cohort and a `mm-candidate-*` cohort all polled
`mm.workflow.user.v2` as `moonmind-workflow-fleet`, with versioning `auto` and
`AutoUpgrade` singletons, while the controller moved `CurrentVersion`. Long-lived
singletons therefore replay history across code versions mid-execution.

A leaked cohort made it worse. `mm-candidate-4f25ac89e6398798-*` survived a
failed release for 26 hours carrying `com.docker.compose.project=moonmind` and
the *same* `com.docker.compose.service` labels as the live fleet, so
`docker compose ps -q temporal-worker-workflow` returned two ids — which is the
recorded `Previous release has no coherent live worker owner` failure.

## Measured reliability

80 release jobs under `deploy/state/release-jobs/`: **10 succeeded, 28 failed
terminally, 42 never reached a terminal result**, across 14 distinct terminal
error signatures. This is not one bug; it is the cost of the mechanism.

The gate also mis-reports. `collect_provider_manager_liveness` lists
`WorkflowId STARTS_WITH 'provider-profile-manager:'`, which returns every run
ever recorded, then resolves each row with `get_workflow_handle(workflow_id)`
without a run id. For three singletons it produced 1,397 observations, a 632 KB
evidence file, and an error message repeating one sentence 170 times.

## Decision

Replace blue/green promotion with **recreate-in-place**. One fleet, one image
version, ever.

    pull -> verify digest -> persist desired state -> compose up -d
         -> reconcile stale workers -> verify -> migrate Omnigent

This is what `deployment_execution.py` already does. The cohort machinery is a
bolt-on at a single call site. Removing it deletes, at the source, the
mixed-version task queue, the leaked-cohort class, `candidate fleet did not
become ready`, `no coherent live worker owner`, and the promotion gate that
blocks an update precisely when the deployment is broken.

Accepted cost: a bounded downtime window during recreation. That is the tradeoff
`docker compose down; pull; up -d` always made, and the reason it was reliable.

## Kept

- The detached updater container. This is what lets a release recreate the
  worker that invoked it; it is the sound part of the design.
- Digest verification and the repository allowlist (invariants 3, 6, 7, 8).
- Desired-state persistence, before/after capture, command and verification
  evidence (invariants 10, 11).
- Operator-access verification and `.env`/override preservation
  (invariants 14, 15).
- Omnigent release migration.

## Removed

- `ReleaseCohort` candidate/retained orchestration.
- Temporal `SetWorkerDeploymentCurrentVersion` promotion and the canary/ramp
  routing in `release_routing.py`.
- `provider_manager_liveness.py` promotion gate.
- The retained-cohort outage recovery in `deployment_availability.py`.

A wedged singleton remains a real fault with a real runbook. It stops being a
*release* precondition, because an operator frequently needs to update in order
to fix one.

## Follow-up not in this change

One stale `cleanup_requested` row for `opencode`
(`profile-lease:credential_validation:2cf412fa...`, expired 2026-09-19 02:21,
`cleanupReason: owner_terminal`) is reported by the manager as
`cleanup_owner_unverifiable` and never enters `current_leases`. Execution
capacity is unaffected (`execution_lease_count: 0`). The durable ledger was not
hand-edited.
