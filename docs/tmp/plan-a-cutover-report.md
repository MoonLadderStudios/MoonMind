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

Tested content: `001d47fa4240a87916d67510f8ed226b2c57ba66` (qualification
journey + handoff, committed: production `LABEL_CONTAINER_JOB` import fix
for Docker-gated pre-creates and hermetic OS-level before-start worker-death
test) plus the working-tree remediation to
`tests/integration/reliability/test_container_job_plan_a_qualification_journey.py`:
a new hermetic OS-level during/after-start worker-death test
(`test_worker_sigkill_after_start_reconciles_without_duplicate`: a real
worker process applies the container start, dies by SIGKILL while holding
the backend capacity-lock key with no userspace cleanup, the survivor
observes the daemon ledger holding its slot and reconciles with no
duplicate start) and this handoff revision-line fix.
21 test defs. No production-code change in the remediation pass:
the new boundary tests confirm the existing daemon/lock mechanism, so it
is retained. Preserved hermetic suites untouched.

Executed via the managed container path (`moonmind container python-tests`,
per AGENTS.md), all green:

- `moonmind container python-tests
  tests/integration/reliability/test_container_job_plan_a_qualification_journey.py
  --timeout-seconds 900` → **17 passed, 3 skipped**
  (`container-job:eb6a946172a242d5937e32bdbbfd2118`, logsRef
  `art_01M3093PTWJ5PZS0DWJ3TEXPPP`). The skips are the three Docker-gated
  cases (`test_real_docker_inspect_shows_stock_fixed_limits`,
  `test_real_docker_two_workers_race_final_slot`,
  `test_real_docker_lost_start_ack_reconciles_before_retry`): no reachable
  Docker daemon in the managed container, so they skip instead of failing
  and run in Docker-backed required CI.
- `moonmind container python-tests
  tests/unit/workflows/temporal/test_container_job_backend.py
  tests/unit/test_container_job_cli.py
  tests/unit/omnigent/test_resolver_verification_capability.py
  --timeout-seconds 600` → **112 passed**
  (`container-job:8002f266945e4b8992bc2bfed4a4c197`, logsRef
  `art_01M3094K8GA25QXX4TPAVBFS6D`).
- `moonmind container python-tests
  tests/integration/reliability/test_container_job_authority_journey.py
  --timeout-seconds 900` → **1 passed**
  (`container-job:755b1e794b684f2b9d0809e5d35d2832`, logsRef
  `art_01M30953ZFG59130H7PH7PEWPQ`).
- `printf '<journey + handoff>' | python3 tools/select_test_suites.py`
  selects `reliability_journey=true`, so the existing required CI workflow
  (`.github/workflows/pytest-unit-tests.yml`, `tests/integration/reliability
  -m reliability_journey`) runs the new journeys once published.

New focused coverage (all in the journey file, preserved hermetic suites
untouched):

- R1: free-slot race at limit 1 (exactly one start, peak overlap <= 1),
  created-waiter forward progress (one claims the slot, the other parks, then
  proceeds after release), a two-**process** flock-serialization test, and a
  Docker-gated two-worker-process race on real pre-created containers that
  observes overlapping running containers through the daemon (cap <= 1,
  loser parks, proceeds after the winner stops).
- R2: lost start acknowledgment reconciles with one real side effect; worker
  death before the start leaves no side effect and the next worker proceeds;
  a real worker process killed by SIGKILL while holding the backend
  capacity-lock key frees the OS-held flock, and the survivor admits and
  starts with exactly one side effect (no fd-close simulation); a second
  real worker process killed by SIGKILL after applying the container start
  is reconciled by the survivor with no duplicate start (daemon-ledger
  slot holder observed first);
  worker death releases the shared lock; container finishing between
  observation and retry frees its slot; own paused-holder retry keeps its
  slot (covers the `_admit_job_slot` fix admitting any slot-holding own
  state, not just running; `created` exclusion preserved). A Docker-gated
  variant injects the lost ack on the real `docker start` path, reconciles
  via `docker inspect`/`_slot_holders`, asserts no duplicate container, and
  proves a stopped container frees its slot for the next waiter. The
  Docker-gated pre-creates label real containers with the production
  `LABEL_CONTAINER_JOB` (imported, not a test-local string) so the
  daemon-ledger `ps` filter observes them.
- R3: wait/release/restart, non-waitable refusal class, and agent-host vs
  job-ledger separation at the backend boundary; a production
  `MoonMindContainerJobWorkflow` wait/cancel journey (parks in
  `WAITING_FOR_CAPACITY`, honors `cancel`, stops the created container,
  terminates `canceled`); a release/proceed/restart journey (holder
  released, waiter proceeds to `succeeded`, a second workflow restarts on
  the freed slot, overlap <= 1); and a host-full subordinate journey (all 8
  generic hosts occupied, the workflow still runs its test job to
  `succeeded` through the same Activities).
- R4: stock CLI submission asserts 2000 cpuMillis / 4096 memoryMiB / 512 pids
  with no pool content; the start path issues one `ps` and no `info` probe;
  a hermetic `create` test asserts `--cpus 2.0` / `--memory 4096m` /
  `--pids-limit 512` reach `docker create` verbatim with no cgroup parent;
  a Batch PR Resolver preset-route test drives the resolver run request
  with isolated fixtures through the scoped capability environment and the
  canonical submission into the production create/start boundary (stock
  limits verbatim, no `info` probe); the Docker-gated case inspects
  `NanoCpus`/`Memory`/`PidsLimit` on a `moonmind-test-*` container when a
  daemon is reachable and skips otherwise.

## Still requiring Docker-backed required CI (not runnable in this sandbox)

No reachable Docker daemon and no GitHub Actions run from this sandbox, so
the following still need existing required CI at the published revision:
the Docker-gated real-Docker race, lost-ack reconcile, and inspect passes
(R1/R2/R4, all skip-guarded and green-skipped here), and the required-CI
run of the new journeys with recorded run URLs (R5). The candidate branch is
unmerged; on publication, re-resolve `refs/heads/main` and verify the actual
target content.
