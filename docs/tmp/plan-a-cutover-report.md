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

Tested content: `340a8d132970e9b1c36af01bde5d463c654a99e9` (own-slot
admission fix + 463-line qualification journey, committed) plus the
remediation working-tree extension to
`tests/integration/reliability/test_container_job_plan_a_qualification_journey.py`
(multiprocess lock race, pre-start death, stock `create` limits, daemon-absent
skip, production-workflow wait/cancel). No production-code change in the
remediation pass: the new boundary tests confirm the existing
daemon/lock mechanism, so it is retained. Preserved hermetic suites untouched.

Executed via the managed container path (`moonmind container python-tests`,
per AGENTS.md), all green:

- `moonmind container python-tests
  tests/integration/reliability/test_container_job_plan_a_qualification_journey.py
  --timeout-seconds 900` → **13 passed, 1 skipped**
  (`container-job:cc6bd6e45e3940f08f9ec832b12133d6`, logsRef
  `art_01M306FRW2TV1RCRW3C2EHPYMJ`). The skip is
  `test_real_docker_inspect_shows_stock_fixed_limits`: no reachable Docker
  daemon in the managed container, so it now skips instead of failing.
- `moonmind container python-tests
  tests/unit/workflows/temporal/test_container_job_backend.py
  tests/unit/test_container_job_cli.py
  tests/unit/omnigent/test_resolver_verification_capability.py
  --timeout-seconds 600` → **112 passed**
  (`container-job:fe4fe54872524a57a32d625cae4981ba`, logsRef
  `art_01M306H9E15JWH61F6QZYNW8E9`).
- `moonmind container python-tests
  tests/integration/reliability/test_container_job_authority_journey.py
  --timeout-seconds 900` → **1 passed**
  (`container-job:12ddd0523aa74679977d8de2bc593e70`, logsRef
  `art_01M306HXX19T0NGKFG09XPCPQ9`).
- `printf '<changed files>' | python3 tools/select_test_suites.py` on the
  candidate change set (backend + journey + this handoff) selects
  `integration_ci=true` and `reliability_journey=true` (plus
  `temporal_boundary`, `api_component`, `exact_artifact`,
  `omnigent_conformance`), so existing required CI runs the new journeys.
  The journey file alone selects `reliability_journey=true`.

New focused coverage (all in the journey file, preserved hermetic suites
untouched):

- R1: free-slot race at limit 1 (exactly one start, peak overlap <= 1),
  created-waiter forward progress (one claims the slot, the other parks, then
  proceeds after release), and a two-**process** flock-serialization test:
  two OS processes with separate lock instances sharing one lock root never
  hold the lock together. Same-process asyncio overlap remains simulated
  (fake daemon); real-Docker overlapping execution still needs Docker-backed
  CI (see below).
- R2: lost start acknowledgment reconciles with one real side effect; worker
  death before the start leaves no side effect and the next worker proceeds;
  worker death releases the shared lock; container finishing between
  observation and retry frees its slot; own paused-holder retry keeps its
  slot (covers the `_admit_job_slot` fix admitting any slot-holding own
  state, not just running; `created` exclusion preserved). All fault
  injection is still backend-boundary simulation, not real worker-process
  death on real Docker.
- R3: wait/release/restart, non-waitable refusal class, and agent-host vs
  job-ledger separation at the backend boundary, **plus** a production
  `MoonMindContainerJobWorkflow` journey with registered Activities:
  the workflow parks in `WAITING_FOR_CAPACITY` behind a running holder,
  honors the `cancel` signal (no `docker start` for the parked job, the
  created container is stopped), and terminates `canceled`.
- R4: stock CLI submission asserts 2000 cpuMillis / 4096 memoryMiB / 512 pids
  with no pool content; the start path issues one `ps` and no `info` probe;
  a new hermetic `create` test asserts `--cpus 2.0` / `--memory 4096m` /
  `--pids-limit 512` reach `docker create` verbatim with no cgroup parent;
  the Docker-gated case inspects `NanoCpus`/`Memory`/`PidsLimit` on a
  `moonmind-test-*` container when a daemon is reachable and skips otherwise.
  No Batch PR Resolver preset-route-to-host test exists yet.

## Still requiring Docker-backed required CI (not runnable in this sandbox)

No reachable Docker daemon and no GitHub Actions run from this sandbox, so
the following still need existing required CI at the published revision:
real-Docker overlapping-start proof (R1), real worker-process death with
real-Docker reconcile (R2), the Docker-backed
`test_real_docker_inspect_shows_stock_fixed_limits` pass (R4), a Batch PR
Resolver preset-route-to-host test (R4, missing), and the required-CI run of
the new journeys with recorded run URLs (R5). The candidate branch is
unmerged; on publication, re-resolve `refs/heads/main` and verify the actual
target content.
