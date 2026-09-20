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

Tested revision: `ea36e3d57b134ae50b6753796af0c3e3592445b2` plus the
uncommitted Plan A qualification change in this working tree
(`moonmind/workflows/temporal/container_job_backend.py` own-slot admission
fix + `tests/integration/reliability/test_container_job_plan_a_qualification_journey.py`).

Executed locally in this environment:

- `python3 -m py_compile` on the touched backend, the new journey file, and
  `moonmind/container_job_cli.py`: pass.
- `printf '<changed files>' | python3 tools/select_test_suites.py`: selects
  `integration_ci=true` and `reliability_journey=true` (plus
  `temporal_boundary`, `api_component`, `exact_artifact`,
  `omnigent_conformance`) for this change, so existing required CI runs the
  new journeys. Full output recorded in the attempt log.
- Standalone flock-serialization check (two lock instances sharing one root,
  concurrent enumerate-then-start with a race-widening sleep): exactly one
  winner, one parked (`FLOCK_SERIALIZES_OK`). This mirrors the R1 journey's
  cross-worker shape, not the production code path itself.
- Not run here (no Docker CLI, no Temporal/pytest runtime in this sandbox):
  the new pytest journeys and the hermetic authority journey. Required CI
  must run: `tests/integration/reliability` with `-m reliability_journey`
  (includes the new Plan A qualification journeys and the preserved
  `test_container_job_authority_journey.py`), the
  `tests/unit/workflows/temporal/test_container_job_backend.py` unit suite,
  and the Docker-backed `test_real_docker_inspect_shows_stock_fixed_limits`
  case (skipped without a Docker CLI).

New focused coverage (all in the new journey file, preserved hermetic suites
untouched):

- R1: free-slot race at limit 1 (exactly one start, peak overlap <= 1) and
  created-waiter forward progress (one claims the slot, the other parks, then
  proceeds after release).
- R2: lost start acknowledgment reconciles with one real side effect; worker
  death releases the shared lock; container finishing between observation and
  retry frees its slot; own paused-holder retry keeps its slot (covers the
  `_admit_job_slot` fix admitting any slot-holding own state, not just
  running; `created` exclusion preserved).
- R3: wait/release/restart, non-waitable refusal class, and agent-host vs
  job-ledger separation.
- R4: stock CLI submission asserts 2000 cpuMillis / 4096 memoryMiB / 512 pids
  with no pool content, start path issues one `ps` and no `info` probe, and
  the Docker-gated case inspects `NanoCpus`/`Memory`/`PidsLimit` on a
  `moonmind-test-*` container.

## Verification that could not run in this environment (prior status)

No pytest, Docker, PostgreSQL, or Temporal here (offline sandbox), so suites
were verified by compilation, targeted logic checks (slot parsing/admission,
settings resolution, host-capacity decisions, historical decoding), doc-link
and doc-architecture checks, and careful replay-boundary review. CI must run:
targeted unit suites, `integration_ci`, the container-job authority and
recurring-cleanup reliability journeys, and the hermetic container-job path
(2 CPU / 4 GiB default; agent-alive-while-test-runs; final-slot race; worker
loss during launch; wait/cancel; historical replay; fixed-limit Docker
inspection; truthful limit/launch failures; Batch PR Resolver preset route).
