# Plan A cutover report (temporary rollout handoff)

Removal of the custom CPU pool and automatic machine-resource admission.
Primary PR only: the obsolete `machine_capacity_reservations` table drop is
deferred until after the deployment cutover (forward migration, post rollback
window; revision `372_machine_reservations` stays in history).

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
4. Post-cutover: drop the obsolete table in a forward migration.

## Verification evidence (MoonLadderStudios/MoonMind#4457)

Candidate: `main` at `928bd43b1` plus the Plan A qualification change
(`moonmind/workflows/temporal/container_job_backend.py`: admit an already
slot-holding own container in any started Docker state, treat a retried
`docker start` refused as already-started idempotent success) with new
coverage in `tests/unit/workflows/temporal/test_container_job_plan_a_admission.py`
and `tests/integration/reliability/test_container_job_plan_a_qualification.py`.

Executed via the supported managed path (each command below ran the target
through `moonmind container python-tests` and succeeded):

- `moonmind container python-tests
  "tests/unit/workflows/temporal/test_container_job_plan_a_admission.py"` —
  10 passed. Covers R1 (cross-process filesystem-lock exclusion, created
  waiters progress), R2 (own restarting/paused/removing admitted, lost start
  ack idempotent), R3 (agent-host/job counts separate, full-slot refusal),
  R4 (stock 2 CPU / 4 GiB / 512 pids reach `create` verbatim, no `info`
  probe; preset route has no pool/ledger/probe owner).
- `moonmind container python-tests
  "tests/integration/reliability/test_container_job_plan_a_qualification.py"` —
  3 passed, 3 skipped (no Docker daemon in that sandbox). The 3 passing legs
  run the production workflow/Activities: slot wait -> release -> restart,
  cancel while parked (no start leaks), and reconcile-before-retry with a
  container finishing between observation and retry (no duplicate create or
  start, cleanup still removes the consumer).
- `moonmind container python-tests
  "tests/unit/workflows/temporal/test_container_job_backend.py"
  "tests/integration/reliability/test_container_job_authority_journey.py"` —
  82 passed, preserving the existing hermetic coverage.
- `moonmind container python-tests
  "tests/unit/workflows/temporal/test_container_job_workflow.py"
  "tests/unit/test_container_job_cli.py"
  "tests/unit/test_container_job_cli_4226.py"` — 53 passed.

Still owned by existing required CI (not run from this sandbox): the three
real-Docker legs in `test_container_job_plan_a_qualification.py`
(SIGKILLed lock holder reacquired, overlapping real starts never exceed
limit 1 with release readmission, stock limits inspected on the daemon).
They are selected by the existing `reliability_journey` shards — any change
to `moonmind/workflows/temporal/container_job_backend.py`,
`moonmind/container_job_cli.py`, or `tests/integration/reliability/` selects
those shards via `tools/select_test_suites.py` — and they skip with a
recorded reason where no daemon exists, so a missing environment can never
be mistaken for a pass.

## Earlier verification that could not run in the original sandbox

No pytest, Docker, PostgreSQL, or Temporal here (offline sandbox), so suites
were verified by compilation, targeted logic checks (slot parsing/admission,
settings resolution, host-capacity decisions, historical decoding), doc-link
and doc-architecture checks, and careful replay-boundary review. CI must run:
targeted unit suites, `integration_ci`, the container-job authority and
recurring-cleanup reliability journeys, and the hermetic container-job path
(2 CPU / 4 GiB default; agent-alive-while-test-runs; final-slot race; worker
loss during launch; wait/cancel; historical replay; fixed-limit Docker
inspection; truthful limit/launch failures; Batch PR Resolver preset route).
