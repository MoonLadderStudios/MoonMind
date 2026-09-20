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

## Verification that could not run in this environment

No pytest, Docker, PostgreSQL, or Temporal here (offline sandbox), so suites
were verified by compilation, targeted logic checks (slot parsing/admission,
settings resolution, host-capacity decisions, historical decoding), doc-link
and doc-architecture checks, and careful replay-boundary review. CI must run:
targeted unit suites, `integration_ci`, the container-job authority and
recurring-cleanup reliability journeys, and the hermetic container-job path
(2 CPU / 4 GiB default; agent-alive-while-test-runs; final-slot race; worker
loss during launch; wait/cancel; historical replay; fixed-limit Docker
inspection; truthful limit/launch failures; Batch PR Resolver preset route).
