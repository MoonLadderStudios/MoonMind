# Plan A cutover report (temporary rollout handoff)

Removal of the custom CPU pool and automatic machine-resource admission.
Repository implementation: the obsolete `machine_capacity_reservations`
table drop is authored as forward migration `386_drop_machine_capacity_4459`
(MoonLadderStudios/MoonMind#4459; revision `372_machine_reservations` stays
in history). Applying that migration to a deployment still waits for the
existing release process after the release owner's rollback/consumer
disposition — this report does not authorize production data deletion.

## Retired aggregate settings (remove from deployment configuration)

These are no longer read. When still exported they are ignored, never
reinterpreted (an aggregate memory value never becomes a per-container limit):

- `MOONMIND_MACHINE_UTILIZATION_PERCENT`
- `MOONMIND_MACHINE_CPU_MILLIS`
- `MOONMIND_MACHINE_MEMORY_MIB`
- `MOONMIND_MACHINE_PROCESSES`
- `MOONMIND_MACHINE_TEMPORARY_STORAGE_MIB`
- `MOONMIND_MACHINE_MAX_CONCURRENT_INITIALIZING`
- `MOONMIND_MACHINE_PRELAUNCH_TTL_SECONDS`
- `MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_MEMORY_MIB`

## Surviving fixed limits and concurrency (unchanged operator values kept)

- Per-container ceilings (unchanged): `MOONMIND_CONTAINER_BACKEND_MAX_CPU_MILLIS`
  (8000), `MOONMIND_CONTAINER_BACKEND_MAX_MEMORY_MIB` (16384),
  `MOONMIND_CONTAINER_BACKEND_MAX_PIDS` (2048),
  `MOONMIND_CONTAINER_BACKEND_SHM_SIZE_MIB` /
  `MOONMIND_CONTAINER_BACKEND_MAX_SHM_SIZE_MIB`, GPU/timeout/output ceilings.
- Generic hosts (unchanged): `MOONMIND_OMNIGENT_GENERIC_HOST_CAPACITY=8`,
  burst 2, window 30s. Existing explicit values are preserved as-is.
- Container jobs (new): `MOONMIND_CONTAINER_BACKEND_MAX_ACTIVE_JOBS`, default 1.
- Stock policy/image defaults: 2 CPUs / 4 GiB, no probe.

## Legacy container-job successor behavior (MoonLadderStudios/MoonMind#4456)

An already-admitted, unstarted legacy job (shared-pool `cpuMillis: 0` or a
retired `minimumMemoryMiB` range) is not re-planned by hand. An exact retry of
the persisted request keeps the original job identity and serialized request;
when reconcile finds no existing container, the launch boundary executes the
deterministic fixed-resource successor (stock 2 CPUs for a zero value, the
persisted `memoryMiB` as the fixed limit, other explicit fields preserved) and
records it as the resolved resources next to the untouched original. Repeated
retries converge on the same successor, existing or uncertain containers are
reconciled rather than recreated, cancellation follows the same continuation,
and the waiting parent receives the successor's real terminal result. A
successor the deployment ceiling cannot admit still fails closed with an
explicit replan disposition.

## Sizing risk carried forward

Fixed limits and concurrency reduce exposure but do not guarantee fit: eight
generic hosts at 2 CPU / 4 GiB each oversubscribes a small host. Size host
count, job count, and per-container limits so the configured concurrency fits
the deployment's host. Agent hosts and subordinate test jobs use separate
counts, so a full host count never blocks an agent's own test job.

## Upgrade order

1. Merge this PR (runtime stops reading/writing `machine_capacity_reservations`;
   table and rows stay for rollback).
2. Rehearse on a populated test deployment (legacy policies, queued jobs,
   completed histories, active-container fixtures).
3. Production cutover: pause dispatch, drain or preserve affected executions,
   deploy API + worker builds, let bootstrap advance eligible inactive bindings
   to fixed successors, resume dispatch.
4. Post-cutover: apply forward migration `386_drop_machine_capacity_4459`
   to drop the obsolete table, once the release owner confirms the
   rollback/consumer disposition. The downgrade recreates only the empty
   schema — dropped rows are not restored, so rollback needing that data
   must restore a pre-upgrade backup.

## Verification evidence (MoonLadderStudios/MoonMind#4457)

Qualification lives in
`tests/integration/reliability/test_container_job_plan_a_qualification_journey.py`
(marker `reliability_journey`). `tools/select_test_suites.py` selects
`reliability_journey` for changes under `tests/integration/reliability`, and
the required `CI / Test Suite` workflow
(`.github/workflows/pytest-unit-tests.yml`) runs it on Docker-equipped
`ubuntu-latest` reliability shards, where the Docker-gated cases execute
rather than skip. The preserved hermetic suites
(`test_container_job_authority_journey.py`,
`tests/unit/workflows/temporal/test_container_job_backend.py`) still run.

### Executed: first qualification pass (PR #4469)

Required CI run
[35555808858](https://github.com/MoonLadderStudios/MoonMind/actions/runs/35555808858)
at head `b56c6b09dab8a32a82734dda4146690dea2d8108` (merged as `2c67aeb95`):
reliability shards 1–4 succeeded. The job logs show all 21 journey tests
**PASSED**, none skipped, including the real-Docker two-worker final-slot
race, the lost-start-ack reconcile, and the HostConfig inspect. These
confirmed the daemon-ledger count and the shared flock. The `created`
exclusion is preserved.

### Second pass: defects found by the remaining boundary cases

Red on `96e615fe4`, then green after the smallest owner fixes:

- **Same-job retry after its own container finished.** After a lost start
  acknowledgment, the container ran to completion. `start_container` then
  either parked the finished job behind other slot holders or `docker start`ed
  the exited container again, a duplicate execution. Fix (in
  `container_job_backend.py`): under the capacity lock, a start that finds its
  own container `exited` reports that container for observation. It is never
  restarted and needs no slot.
- **Lost start ack through the production workflow.** `start_container` ran
  with one attempt. A lost ack or a worker lost during start therefore failed
  the job and force-removed the live workload instead of reconciling it. Fix
  (in `activity_catalog.py`): start is retried, because each attempt
  reconciles the daemon's record of its own container first. A start that the
  backend already failed closed (restricted-egress evidence unpublishable, so
  the container was removed) raises the non-retryable `launch` class. A retry
  therefore cannot replace that cause with a missing-container error.
- **Real-Docker HostConfig through the production create route.**
  `test_real_docker_inspect_shows_stock_fixed_limits` now sends the stock
  `python_test_submission` spec through production `create_container` against
  the daemon, instead of hand-typed `docker create` flags. Only the network is
  `none`, because restricted-egress attestation needs the deployment gateway
  and is covered by the egress suites.

New cases:
- `test_same_job_retry_after_own_container_exited_does_not_rerun`
- `test_real_docker_same_job_retry_after_exit_does_not_rerun` (Docker-gated,
  checks that `StartedAt` is unchanged)
- `test_lost_start_ack_reconciles_through_production_workflow`
- `test_container_finishing_before_start_retry_through_production_workflow`

`test_slot_wait_release_cancel_and_restart` now expects a finished job to be
reconciled, not re-executed.

Local targeted commands (outside a managed workflow, with `DOCKER_HOST`
pointed at a nonexistent socket so the Docker-gated cases skip instead of
touching a deployment daemon):

- `pytest tests/integration/reliability/test_container_job_plan_a_qualification_journey.py`
  plus the authority journey, the backend, workflow, and GPU unit suites, the
  container-job workflow integration suite, and the restricted-egress CI
  suite. Red: 4 failed, 2 skipped on the new and changed cases before the
  fix. Green: 238 passed, 4 skipped (the four Docker-gated cases).

The required-CI run for this candidate, with its tested SHA, is recorded
on the pull request that carries this revision. Until that run passes, the
four Docker-gated cases are verified only by the earlier run above (three
of them) and remain unverified for the new real-Docker retry case.
