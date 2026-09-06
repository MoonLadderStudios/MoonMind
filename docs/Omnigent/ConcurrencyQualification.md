# Omnigent Concurrency Qualification

Status: Target design
Document Class: System / Feature Design View
Owners: MoonMind Platform
Last updated: 2026-09-06

**Issue:** [MoonLadderStudios/MoonMind#3885](https://github.com/MoonLadderStudios/MoonMind/issues/3885)
([Omnigent concurrency] Complete layered N-way qualification with
production-boundary evidence and truthful readiness).

## Related docs

- [`docs/Omnigent/ControlPlaneConcurrencyAndFencing.md`](./ControlPlaneConcurrencyAndFencing.md) — the fencing guarantees the concurrent journeys exercise.
- [`docs/Omnigent/ConformanceAndLiveSmoke.md`](./ConformanceAndLiveSmoke.md) — the protected conformance program this record extends rather than replaces.
- [`docs/Omnigent/OmnigentHarnessPlatformDesign.md`](./OmnigentHarnessPlatformDesign.md) — the exact support combination the concurrency dimension binds to.

## Why

Concurrency support is a claim about a deployment, not about a scheduler. A
green suite proves the coordinator is correct; it does not prove that the built
MoonMind, Omnigent server, and host images ran two, four, or eight executions
at once on a real daemon without sharing a container, a state volume, a
workspace, a session, or a cleanup claim.

Three failure modes motivate this document:

- **A configured ceiling read as a result.** `max_parallel_runs=8` is an
  intention. Without an observation it says nothing about whether eight
  executions were ever simultaneously live.
- **A skipped row read as a pass.** A runner without Docker, without the exact
  images, or without protected-live admission produces no evidence. Omitting
  the row makes the matrix look complete.
- **A validated level read as a general property.** A pass at `N=2` on one
  harness, architecture, host mode, image, and materializer says nothing about
  `N=16`, or about any other combination.

## Concurrency support identity

`ConcurrencySupportIdentity` (`moonmind/omnigent/concurrency_qualification.py`)
binds a validated level to the substrate that produced it:

| Field | Meaning |
| --- | --- |
| `supportCombinationKey` | The exact support combination (harness, images, architecture, materializer, host class, launch policy, realizer, model policy). |
| `moonmindCommit`, `workerBuildRef` | The MoonMind source and worker build under test. |
| `providerCapacityPolicyVersion` | The Provider Profile capacity/backpressure policy in force. |
| `hostCapacityPolicyVersion` | The aggregate host and machine-resource admission policy in force. |
| `transportPoolPolicyVersion` | The pooled transport policy in force. |
| `workerTopologyRef` | The worker replica/task-queue topology and concurrency settings. |
| `resourceClass` | The declared machine (`cpuCores`, `memoryGib`) the exact-image rows ran on. |
| `scenarioCatalogVersion` | The scenario catalog the evidence was produced against. |

Evidence produced against a different catalog version is refused. The identity
is carried on `ProtectedExecutionSupportEvidence.concurrency`, and the record
must name the same `supportCombinationKey` as the row it is filed under.

## Layers

| Layer | Levels | Environment | Owner |
| --- | --- | --- | --- |
| `hermetic` | 1, 2, 4, 8, 16 | Real schemas, planning, realizer, runtime bindings, host leases, session/bridge stores, cleanup authority, and real database constraints, over controlled provider and Docker boundaries. | Required pull-request CI. |
| `exact_docker` | 2, 4, 8 | The built MoonMind, Omnigent server, and host artifacts under a real Docker daemon on a declared resource class. | `Provider / Omnigent Concurrency Qualification` (scheduled). |
| `protected_live` | 2 up to the provider-safe ceiling | The exact eligible credentialless OpenCode Zen route under a bounded pricing/privacy/load policy (`tests/provider/omnigent/test_omnigent_concurrency.py`). | Same workflow, opt-in dispatch only. |

`hermetic` and `exact_docker` are the **required layers**: a level is validated
only when *both* carry a passing row at that exact level. A hermetic pass alone
validates nothing, because the deployed artifacts were never exercised.

The hermetic layer spans three production boundaries, each with its own owner:

- **Authoring** — `N` simultaneous submissions compiled through
  `compile_execution_plan` behind the canonical plan store, producing `N`
  immutable plans that each select the generic Omnigent combination.
- **Dispatch** — `N+2` submissions driven through
  `MoonMindAgentRun._admit_omnigent_capacity_before_execution` against a real
  `ProfileSlotState` ledger and the production `GenericHostCapacityAdmission`,
  so admission, durable waiting, grant-on-release, capacity changes, and queued
  cancellation are decided by the ledger rather than by a stub.
- **Execution** — `N` real `GenericOmnigentHostRealizer.execute` calls against
  one shared machine ledger.

Because the layer includes real database constraints, one hermetic owner
(`tests/integration/omnigent/test_machine_capacity_reservations_postgres.py`)
races two **independent PostgreSQL transactions** for the final slot. Running
`--layer hermetic` therefore requires local PostgreSQL binaries or
`MOONMIND_TEST_POSTGRES_URL`; that is the same environment
`./tools/test_integration.sh` provides.

The exact-Docker layer launches `N` run-dedicated hosts from the digest-pinned
image through the production side-effect owners — `DockerOmnigentHostLauncher`
and `DockerOmnigentHostCleanupService` over `DockerCommandBackend` — using the
production identity derivations (correlation name, state-volume digest,
expected Omnigent host id). No host is torn down until every host has been
observed `running`, so the recorded windows overlap rather than merely abut, and
the post-run scan is a real `docker inspect` of every container and volume the
wave owned.

## Authority handoffs under concurrency

Every handoff the concurrent journey crosses carries a confined-failure case at
`N>=4`: turn claim, provider lease confirmation, runtime binding creation (the
grant landed but the run is not scheduled), credential preparation, workspace
preparation, host lease acquisition (scheduled, not started), container and
state-volume creation, host registration, session creation, first message,
event streaming, and harvest — plus drain, host teardown, credential cleanup,
and the final provider-capacity release.

Two ambiguity cases are covered explicitly, because they are the ones a retry
can turn into a second mutation:

- **Lost acknowledgment** — the registration happened and the answer was lost.
- **Late completion** — the caller gave up and the registration lands during
  teardown.

Both must converge on the same host identity, and neither may authorize a
second launch.

## Publishing an observation

A layer's owning tests publish what they observed through one reader/writer
pair in `moonmind/omnigent/concurrency_qualification.py`:

- `requested_concurrency_level(default=...)` reads the level the runner
  selected from `MOONMIND_OMNIGENT_CONCURRENCY_LEVEL`;
- `publish_observed_overlap(layer, overlap)` writes it to
  `$MOONMIND_OMNIGENT_CONCURRENCY_EVIDENCE_DIR/<layer>-<level>.json`, keyed by
  the observation's own level;
- `load_observed_overlap(dir, layer, level)` is what the runner reads, and it
  refuses a file that measured a different level.

With no evidence directory exported — an ordinary developer run — publishing is
a no-op. A layer that publishes nothing is recorded `partial`, never `passed`.

## Observed overlap

`ObservedOverlapEvidence` is the only accepted source of a peak. It requires:

- per-execution `started_at`/`ended_at` samples, from which the peak is swept;
- `barrier_synchronized=True`, meaning every admitted execution held a barrier
  or controlled hold inside its own live session until the effective limit was
  reached;
- a peak equal to `min(requested_level, effective_limit)`;
- when the requested level exceeds the effective limit, exactly
  `requested_level - effective_limit` observed durable waiters.

N executions submitted together but executed one after another sweep to a peak
of 1 and are rejected. A self-asserted number with no samples is rejected.

## Row outcomes

`ConcurrencyRowStatus` records six distinct outcomes, and only `passed` raises
the validated level:

| Status | Meaning |
| --- | --- |
| `passed` | Executed, with observed overlap and independently resolvable evidence. |
| `failed` | Executed and violated an invariant. |
| `skipped` | Deselected by the risk-based matrix for this run. |
| `blocked` | Refused by a policy, authorization, or release gate. |
| `unavailable` | The environment (daemon, exact images, provider route) was absent. |
| `partial` | Executed, but only part of the family completed. |

A passing row must carry its overlap evidence, an `evidenceRef`, an
`evidenceDigest`, and — for `exact_docker` — its machine resource class. A
non-passing row may not carry overlap evidence, and may not claim
`policyQualified`. A resource-class exception that lowers a required
exact-image level must be named explicitly in the row; a missing level with no
exception is simply not qualified.

## Scenario catalog

`CONCURRENCY_SCENARIO_CATALOG` is the risk-based scenario/layer matrix. Each of
the seven required families names the test that executes it at each required
layer, plus the sibling escaped regressions that owner replays:

1. `isolation_and_completion`
2. `queue_and_dynamic_limits`
3. `validation_maintenance_backpressure`
4. `host_and_transport_pressure`
5. `failure_at_authority_handoffs`
6. `recovery_and_release`
7. `product_and_readability`

`unowned_scenarios()` returns any required family/layer pair without an owner;
a non-empty result is a program gap. Only hermetic owners may be marked
`required_in_ci` — exact-image and protected-live rows are scheduled or
opt-in, never required on a pull request, and never run from an untrusted fork.

## Readiness: qualification vs availability

`moonmind/omnigent/control_plane/readiness.py` classifies every capability as
`STRUCTURAL` or `TRANSIENT`.

- **Structural** capabilities (schema, builds, transport, janitor, exact image,
  protected evidence, …) fail closed on unknown, as before. Their absence means
  the deployment is not qualified.
- **Transient** capabilities (`provider_capacity`, `host_capacity`,
  `worker_capacity`) are *observed pressure*. Unknown never blocks, because an
  unmeasured layer has not been shown to be saturated. Observed saturation sets
  `wait_for_capacity` while `structurally_supported` stays `True`.

A busy or throttled installation therefore waits rather than reporting itself
unsupported. That distinction is load-bearing: "unsupported" is the state that
invites a substitution to another credential, profile, or runtime, and a
deployment that is merely full must never produce it.

`advertised_concurrency_ceiling()` reports the validated level or a lower
operator ceiling, whichever is smaller. A combination with no concurrency
evidence advertises `0`, not `1`: unqualified is not implicitly qualified at
one. The configured operator ceiling is never rewritten by this call.

## Cleanup and repeated waves

- `CleanupScanReport.zero_leak` is derived from the entries, never asserted. A
  scan carrying unresolved MoonMind-owned resources reports `False`. Foreign
  and profile-owned resources are reported as preserved and are never deleted,
  and one cannot be recorded as "resolved" by this authority.
- `RepeatedWaveReport` evaluates bounded growth across at least two waves
  against a `RepeatedWaveThresholds` budget for the declared resource class:
  observed overlap, control latency, per-lease database mutations, registration
  request counts, transport pool peak, and residual resources. The checks are
  both absolute (a slow allocator) and comparative (a leak that stays under
  budget per wave but grows wave over wave). Provider latency is excluded by
  construction — `WaveObservation` carries only control-plane timings.
- `max_control_seconds` binds on **every** control latency a wave reports —
  wait, launch, registration, the wave total, and cleanup — not only the total.
  A saturated deployment does not slow every phase evenly; a cancellation or
  teardown that stalls behind the event stream breaches the budget on its own.
  The hermetic owner measures those latencies from substrate milestones, so the
  budget binds on observations rather than on placeholder zeros, and drives one
  wave under event-stream saturation while cancellation and cleanup must still
  complete.

## Producing the record

```bash
python tools/run_omnigent_concurrency_qualification.py \
    --layer exact_docker --levels 2,4,8 \
    --support-combination-key "omnigent-support:sha256:..." \
    --moonmind-commit "$GITHUB_SHA" \
    --resource-class ci-standard-4x8@1 \
    --output artifacts/omnigent-concurrency/record.json
```

The runner converts each (layer, level) outcome into one row. A missing daemon,
missing exact image, missing host server endpoint, or missing PostgreSQL
cluster for the hermetic layer produces `unavailable` rows; a protected-live
run that was not admitted produces `blocked` rows; owning tests that pass
without publishing an observation produce `partial` rows. In every case the
rows are written to the record and the command exits non-zero.

Every layer's environment precondition is checked *before* its owning tests
run, so a missing dependency is recorded as `unavailable` naming what was
absent rather than as a `failed` row that reads like a concurrency defect.

**The exit gate is per invocation.** The command exits zero only when every
requested `(layer, level)` row passed, and a requested row that was never
produced counts as a failure. That is deliberately separate from
`validated_concurrency_level`, which is the *cross-layer advertisement* and can
only be reached by a record carrying both required layers at the same level.
Each CI job runs one layer, so gating a job on the cross-layer level would make
it impossible to pass.

**The runner never executes a test that calls the runner.** `_run_owning_tests`
spawns pytest over a layer's owning tests with that layer's environment still
set, so an owning test that asked the runner to build the same layer's rows
would recurse without bound. The runner's own record contract is asserted in
`tests/unit/omnigent/test_concurrency_qualification.py`, which the catalog does
not own, and `test_no_owning_test_re_enters_the_qualification_runner` keeps it
that way.

The protected-live owner fails rather than skips when credentials or admission
are absent, and caps its level at the provider-safe ceiling, so a
misconfiguration cannot open an unbounded number of live provider sessions.

## Non-goals

No new event store, runtime lifecycle, database scheduler, always-on test
service, or arbitrary live load. The record does not rewrite configured
operator ceilings, and it does not replace the protected support registry — it
adds the concurrency dimension to it.
