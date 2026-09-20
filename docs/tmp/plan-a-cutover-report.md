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

## Verification (MoonLadderStudios/MoonMind#4457)

Qualification owners: `moonmind/workflows/temporal/container_job_backend.py`
(`start_container` reconciles the existing container before `docker start`;
`reconcile_container` fails closed on an unreadable daemon instead of
reporting absence; the `created`-exclusion fix is preserved).

- `tests/unit/workflows/temporal/test_container_job_plan_a_4457.py` — lost
  start-acknowledgment matrix (running reattach, finished no-restart,
  created starts once), reconcile fail-closed vs vanished distinction,
  two-process shared-lock race for the final slot at limit 1, created-waiter
  forward progress, host/container ledger independence, CLI stock limits
  reaching Docker verbatim with no `info` probe, preset profiles on fixed
  limits. 11 tests.
- `tests/integration/reliability/test_container_job_plan_a_qualification_4457.py`
  — production workflow/Activities journeys for slot wait/release/cancel/
  restart and host separation, preset-route effective-launch compilation,
  plus two real-Docker tests (two-process final-slot race with external
  overlap observation; real `docker inspect` of the CLI stock 2 CPU / 4 GiB /
  512 PID limits). The real-Docker tests skip without a reachable daemon and
  run in required CI. 4 hermetic tests + 2 real-Docker tests.
- Existing hermetic coverage is preserved:
  `tests/integration/reliability/test_container_job_authority_journey.py` and
  `tests/unit/workflows/temporal/test_container_job_backend.py` still pass
  unchanged.

Executed 2026-09-20 on `main` at `100a08d6ed146b0038e2c6751f5d99f08e0eff3b`
plus the uncommitted change above, via the normal route:

- `moonmind container python-tests
  tests/unit/workflows/temporal/test_container_job_plan_a_4457.py
  tests/unit/workflows/temporal/test_container_job_backend.py` → 96 passed.
- `moonmind container python-tests
  tests/integration/reliability/test_container_job_plan_a_qualification_4457.py
  tests/unit/workflows/temporal/test_container_job_plan_a_4457.py
  tests/integration/reliability/test_container_job_authority_journey.py`
  → 16 passed, 2 skipped (real-Docker tests, no daemon in this sandbox).
- Adjacent suites: `test_container_job_workflow.py` +
  `test_generic_host_capacity.py` → 74 passed; `test_container_job_evidence.py`
  + `test_container_job_backend_registry_auth.py` +
  `test_container_image_acquisition.py` → 70 passed;
  `test_container_job_cli.py` + `test_container_job_cli_4226.py` +
  `test_concurrency_qualification.py` → 133 passed.
- `python3 tools/select_test_suites.py` on the changed files selects
  `reliability_journey`, `integration_ci`, `temporal_boundary`, and the unit
  suites, so the new tests run in existing required CI with no config change.

Remaining for CI: the two real-Docker tests execute on runners with a Docker
daemon (reliability shards); the handoff claim above the line is the
locally executed evidence, not a substitute for that run.
