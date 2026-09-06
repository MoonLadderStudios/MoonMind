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
| `hermetic` | 1, 2, 4, 8, 16 | Real schemas, planning, realizer, runtime bindings, host leases, session/bridge stores, cleanup authority, over controlled provider and Docker boundaries. | Required pull-request CI. |
| `exact_docker` | 2, 4, 8 | The built MoonMind, Omnigent server, and host artifacts under a real Docker daemon on a declared resource class. | `Provider / Omnigent Concurrency Qualification` (scheduled). |
| `protected_live` | 2 up to the provider-safe ceiling | The exact eligible credentialless OpenCode Zen route under a bounded pricing/privacy/load policy (`tests/provider/omnigent/test_omnigent_concurrency.py`). | Same workflow, opt-in dispatch only. |

`hermetic` and `exact_docker` are the **required layers**: a level is validated
only when *both* carry a passing row at that exact level. A hermetic pass alone
validates nothing, because the deployed artifacts were never exercised.

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

## Producing the record

```bash
python tools/run_omnigent_concurrency_qualification.py \
    --layer exact_docker --levels 2,4,8 \
    --support-combination-key "omnigent-support:sha256:..." \
    --moonmind-commit "$GITHUB_SHA" \
    --resource-class ci-standard-4x8@1 \
    --output artifacts/omnigent-concurrency/record.json
```

The runner converts each (layer, level) outcome into one row. A missing daemon
or missing exact image produces `unavailable` rows; a protected-live run that
was not admitted produces `blocked` rows; owning tests that pass without
publishing an observation produce `partial` rows. In every case the rows are
written to the record and the command exits non-zero, because no level was
validated.

The protected-live owner fails rather than skips when credentials or admission
are absent, and caps its level at the provider-safe ceiling, so a
misconfiguration cannot open an unbounded number of live provider sessions.

## Non-goals

No new event store, runtime lifecycle, database scheduler, always-on test
service, or arbitrary live load. The record does not rewrite configured
operator ceilings, and it does not replace the protected support registry — it
adds the concurrency dimension to it.
