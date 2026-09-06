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
| `hostImageRef` | The digest-pinned exact host image the run actually launched its hosts from. |
| `providerCapacityPolicyVersion` | The Provider Profile capacity/backpressure policy in force. |
| `hostCapacityPolicyVersion` | The aggregate host and machine-resource admission policy in force. |
| `transportPoolPolicyVersion` | The pooled transport policy in force. |
| `workerTopologyRef` | The worker replica/task-queue topology and concurrency settings. |
| `resourceClass` | The measured machine (`cpuCores`, `memoryMib`) the exact-image rows ran on. |
| `scenarioCatalogVersion` | The scenario catalog the evidence was produced against. |

Evidence produced against a different catalog version is refused. The identity
is carried on `ProtectedExecutionSupportEvidence.concurrency`, and the record
must name the same `supportCombinationKey` as the row it is filed under.

The record reaches that field through the protected publisher
(`moonmind/omnigent/execution_support_publication.py`): the live-conformance
publish job stages the newest successful qualification run's records and
`build_protected_support_index()` attaches each one to the passing entry naming
its own combination. There is no second registry — the combined exact-support
matrix is the existing index plus this one field. A record naming a combination
the run did not publish at all is refused; a record naming a combination the run
published as *not* passing is recorded on no row, because a combination that
failed advertises no validated peak.

Naming the right combination is necessary but not sufficient.
`supportCombinationKey` is an opaque digest of the substrate, so it cannot be
inverted back into the artifacts or the revision that produced a given record,
and the publisher compares the two things the key does not carry before it
attaches anything:

* **The revision.** A code-only change leaves the key unchanged, so a record's
  `moonmindCommit` has to name the manifest's `sourceCommit` (abbreviations on
  either side are matched by prefix). Otherwise a level observed on an older
  MoonMind would be advertised for this one.
* **The exact artifacts.** A record's `hostImageRef` has to be the entry's
  `hostImageRef`. A manual dispatch can point the qualification job at any
  digest while a repository variable still names the previous combination, and
  the rows would otherwise be published under an identity whose host artifacts
  were never exercised.
* **Resolvability.** A row whose `evidenceRef` is a workspace-local `file:` URI
  is refused: that path identifies no workflow artifact and does not survive the
  qualification runner, so it cannot back a published claim.

The qualification runner closes the same gap from the other side. It refuses to
run when `--host-image-ref` disagrees with the image the layer will actually
launch (`MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE`), and the scheduled workflow
requires a dispatch that overrides `host_image` to supply the
`support_combination_key` naming it. Both refusals happen *before* the matrix is
spent.

The same publisher records non-pass combinations as rows with their real status
and `policyQualified: false` instead of omitting them, so an operator can tell a
combination that failed from one that was never attempted.
`validate_protected_execution_support_evidence()` still refuses every non-pass
row, so widening what is recorded never widens what is admitted.

`resourceClass` is **measured, and there is no way to declare it**. The runner
reads what this process may actually use — the CPU affinity mask, the CFS quota
in `cpu.max`, the memory limit in `memory.max`, and `MemTotal` — and the smaller
of host and cgroup wins, so a container-confined runner publishes the machine it
was given rather than the machine around it. A machine that cannot be measured
fails before any layer runs; it does not fall back to a declared size.

A `resourceClassRef` may name the machine it stands for as
`<cpuCores>x<memoryGib>` — `ci-standard-4x8@1`. When it does, the ref is a claim
the measurement has to satisfy: a `ci-standard-4x8@1` row produced on a
sixteen-core runner is refused before the layer runs, rather than published as a
row that contradicts itself. A ref without such a token
(`local-deterministic@1`) carries no dimension claim and is not checked.

Memory is carried and compared in **MiB**, against a band rather than an exact
size. No machine measures the size its class names: the kernel reserves
firmware, memmap and crashkernel pages before `MemTotal` is computed, so a
nominal 8-GiB VM measures about 7947 MiB and a 16-GiB CI runner about 15989 MiB.
A measurement may land up to `MACHINE_MEMORY_TOLERANCE` (10%) below the size its
class names and may never exceed it. The band is wide enough for every
reservation a Linux host takes and far narrower than the gap to the next smaller
whole-GiB class, so a genuine 7-GiB machine still cannot be filed under a 4x8
class, and a machine with more memory than the class names cannot be either —
thresholds calibrated for the smaller class would otherwise pass on headroom the
class does not describe.

## Layers

| Layer | Levels | Environment | Owner |
| --- | --- | --- | --- |
| `hermetic` | 1, 2, 4, 8, 16 | Real schemas, planning, realizer, runtime bindings, host leases, session/bridge stores, cleanup authority, and real database constraints, over controlled provider and Docker boundaries. | Gated by required pull-request CI, except the PostgreSQL-backed machine-capacity owner, which is impact-selected `integration_ci`. Its **record** is produced by the scheduled workflow below. |
| `exact_docker` | 2, 4, 8 | The built MoonMind, Omnigent server, and host artifacts under a real Docker daemon on a declared resource class. | `Provider / Omnigent Concurrency Qualification` (scheduled). |
| `protected_live` | 2 up to the provider-safe ceiling (`PROTECTED_LIVE_MAX_LEVEL`, 4) | The exact eligible credentialless OpenCode Zen route under a bounded pricing/privacy/load policy (`tests/provider/omnigent/test_omnigent_concurrency.py`). Every session it opens is reclaimed in a bounded `finally`, including a wave that failed part-way through: sessions left on a shared free route consume the next run's capacity and contaminate the level it measures. | Same workflow, opt-in dispatch only. |

`hermetic` and `exact_docker` are the **required layers**: a level is validated
only when *both* carry a passing row at that exact level. A hermetic pass alone
validates nothing, because the deployed artifacts were never exercised.

Because the level is cross-layer, **both required layers are recorded by one
job on one machine**. The substrate identity a record is filed under carries the
*measured* resource class, so records from two different runners name two
different substrates and the publisher refuses to merge them; a hermetic record
earned on a pull-request runner could therefore never combine with the scheduled
exact-image record, and the published index would carry a record that validates
nothing. The scheduled job runs `--layer hermetic` alongside `--layer
exact_docker` for that reason alone. Where the hermetic layer is *gated* does
not move: its owning tests stay in required pull-request CI, and the hermetic
recording step runs even when the exact-image rows failed, so evidence that was
earned is never withheld.

The hermetic layer spans three production boundaries, each with its own owner:

- **Authoring** — `N` simultaneous submissions compiled through the production
  `compile_execution_plan` behind `InMemoryExecutionPlanStore`, producing `N`
  immutable plans that each select the generic Omnigent combination.
  `load_or_compile` idempotency is exercised there, scoped to that store. The
  DB-backed `DbExecutionPlanStore.persist` `IntegrityError` race belongs to the
  plan-store boundary and is outside this layer's scope; the plan-store contract
  tests cover `persist` idempotency and the typed conflict, but no test yet
  drives the concurrent-insert rollback-and-reload branch.
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
image through the production side-effect owners — `DockerOmnigentHostLauncher`,
`OmnigentHostRegistrationService` and `DockerOmnigentHostCleanupService` over
`DockerCommandBackend` — using the production identity derivations (correlation
name, state-volume digest, expected Omnigent host id).

**A live container is not an execution.** A host's observed window opens only
once the production registration authority returns *that host's* expected
addressable Omnigent host id from the configured control-plane endpoint, owned
by this deployment, with the declared harness ready. Without that boundary, `N`
containers that started and then failed to register — or retried forever — would
publish a passing row for an N-way path that cannot run anything. No host is
torn down until every host in its wave has registered, so the recorded windows
overlap rather than merely abut. A host that cannot register aborts the wave's
barrier so its peers are released rather than parked until the job's own
timeout, and every host the wave created is still offered to its cleanup
authority.

**The level is run twice.** A single wave cannot see image-level growth that
only appears after teardown and relaunch — accumulating registrations,
transports, volumes, control latency — so the layer runs `EXACT_DOCKER_WAVES`
waves at the requested level and gates the row on the `RepeatedWaveReport` for
`ci-standard-4x8@1`. The published observation carries both waves' samples on
one timeline.

The post-run scan is a real `docker inspect` of every container and volume the
wave owned, and **only Docker's own "no such" answer proves absence**. An
unreachable daemon, an authorization change, or any other failure of the scan
establishes nothing about the resource, so the entry stays unresolved, the
reason travels with it, and `zero_leak` cannot report a clean sweep the scan
never observed.

Four of its owners — the capacity-admission fence, the control plane, the
machine-capacity reservations and the provider-lease incremental contract — also
decide their invariant against real PostgreSQL, so `--layer exact_docker` needs
the same cluster the hermetic layer does; the scheduled job supplies one as a
service container. Because the layer proves registration, it also needs the
control-plane endpoint, its token and the expected host owner
(`EXACT_DOCKER_REQUIRED_ENV`); without them the row is `unavailable` naming what
was absent, never `failed`.

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
  `requested_level - effective_limit` observed durable waiters, each a
  `DurableWaitObservation` naming the execution, the durable queue that
  preserved it, and the waiting state it published.

N executions submitted together but executed one after another sweep to a peak
of 1 and are rejected. A self-asserted number with no samples is rejected.

A **refusal is not a wait**. Work that raised at the realizer holds no slot, but
it is also gone: nothing preserved it and nothing will resume it. A waiter has
to name the queue it is preserved in, so a capacity refusal cannot be published
as evidence that the surplus was queued, and an execution recorded in the peak
cannot also be recorded as waiting. The hermetic realizer substrate refuses
above its limit, so a hermetic wave that requested more than the effective limit
publishes evidence for the level it actually ran; genuine durable waiting is
observed at the dispatch boundary, where the queued run keeps its intent, starts
no execution Activity, and publishes `awaiting_slot`.

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

The operator-visible consumer is the runtime-provider migration projection
(`build_runtime_provider_migration_status()`), which reports an
`advertisedConcurrency` view per exact combination: the `validatedLevel` its
protected row observed, the `operatorCeiling` configured through
`MOONMIND_OMNIGENT_GENERIC_HOST_CAPACITY`, the `advertisedLevel` that is the
smaller of the two, and `limitedBy` naming which bound is binding
(`unqualified`, `qualified_level`, or `operator_ceiling`). A row admission would
refuse — expired, or older than `MAX_EXECUTION_SUPPORT_EVIDENCE_AGE` — advertises
`0`: `advertised_concurrency_ceiling()` asks
`validate_protected_execution_support_evidence()` rather than deciding freshness
itself, so the advertisement cannot outlive the admission window.

## Cleanup and repeated waves

- `CleanupScanReport.zero_leak` is derived from the entries, never asserted. A
  scan carrying unresolved MoonMind-owned resources reports `False`. Foreign
  and profile-owned resources are reported as preserved and are never deleted,
  and one cannot be recorded as "resolved" by this authority.
- `RepeatedWaveReport` evaluates bounded growth across at least two waves
  against a `RepeatedWaveThresholds` budget for the declared resource class.
  `repeated_wave_thresholds(resource_class_ref)` resolves that budget from
  `REPEATED_WAVE_THRESHOLDS`, which declares `local-deterministic@1` for the
  hermetic layer and `ci-standard-4x8@1` for the exact-image layer. The
  exact-image control budget is wider because real containers on a real daemon
  are slower; the per-execution mutation, registration and transport-pool
  budgets are identical, because those count control-plane work per execution
  and must not grow just because the machine did. An undeclared class is
  refused rather than silently given the deterministic budget.

  The budget binds observed overlap, control latency, per-lease database
  mutations, registration request counts, transport pool peak, and residual
  resources. The checks are both absolute (a slow allocator) and comparative (a
  leak that stays under budget per wave but grows wave over wave). Provider
  latency is excluded by construction — `WaveObservation` carries only
  control-plane timings.
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
    --host-image-ref "ghcr.io/example/opencode-host@sha256:..." \
    --worker-build-ref moonmind-worker@2026.09 \
    --worker-topology-ref single-replica@1 \
    --resource-class ci-standard-4x8@1 \
    --evidence-artifact omnigent-concurrency-required-layers-1-1 \
    --output artifacts/omnigent-concurrency/record.json
```

`--levels` is bounded by the layer's own declared matrix
(`ALLOWED_CONCURRENCY_LEVELS`): `1,2,4,8,16` hermetic, `2,4,8` exact-image, and
`2`-`4` protected-live. This program spends real containers and a shared free
provider route, so a level with no declared matrix entry — and therefore no
budget and no bounded-load contract — is refused while it is still an argument,
not after `--levels 10000` has created ten thousand host specifications on a
trusted qualification runner.

`--evidence-artifact` and `--evidence-base-ref` name where a published
observation resolves from once the record leaves the runner. In GitHub Actions
the base defaults to this run and attempt, so a passing row carries an immutable
run/artifact reference. Outside a durable publication context the ref is an
explicit `file:` URI, which the protected publisher refuses — a workspace path
never reaches the published index.

### Required repository variables

The scheduled `Provider / Omnigent Concurrency Qualification` workflow supplies
the identity arguments from repository variables. **Every one of them must be
set on the repository or the environment**; an unset GitHub variable expands to
an empty string, which `argparse` accepts *over* the flag's default.

| Repository variable | Flag | Meaning |
| --- | --- | --- |
| `OMNIGENT_CONCURRENCY_SUPPORT_KEY` | `--support-combination-key` | The exact support combination the level is claimed for. A dispatch that overrides `host_image` must supply the `support_combination_key` input naming it, because the key is a digest and cannot be recomputed from a substituted image. |
| `OMNIGENT_CONFORMANCE_OPENCODE_HOST_IMAGE` | `--host-image-ref` | The digest-pinned exact host image the rows are earned on. Held to the image the layer actually launches. |
| `OMNIGENT_WORKER_BUILD_REF` | `--worker-build-ref` | The worker build under test. |
| `OMNIGENT_WORKER_TOPOLOGY_REF` | `--worker-topology-ref` | The worker replica/task-queue topology and concurrency settings. |
| `OMNIGENT_CONCURRENCY_RESOURCE_CLASS` | `--resource-class` | The machine class the exact-image rows are filed under. A ref carrying `<cores>x<gib>` dimensions must be one the runner's measured machine satisfies. |

### The environment each layer's owners require

The identity arguments above build the record; these values let a layer's
owning tests run at all. A layer whose environment is incomplete records
`unavailable` rows naming what was absent, so a misconfigured repository is
never reported as a concurrency defect — but it also never produces a row, so
the job cannot pass until they are set.

| Layer | Value | Source | Why the layer needs it |
| --- | --- | --- | --- |
| exact-image | `MOONMIND_TEST_POSTGRES_URL` | The job's PostgreSQL service container | Four of this layer's owners decide their invariant with real PostgreSQL constraints, and their fixture fails closed without a cluster. |
| exact-image | `MOONMIND_OMNIGENT_CONCURRENCY_HOST_IMAGE` | The job's resolved digest-pinned image | The exact artifact the hosts are launched from. |
| exact-image | `MOONMIND_OMNIGENT_HOST_SERVER_URL` | `vars.OMNIGENT_SERVER_URL` | The endpoint the launched hosts are given to register against. |
| exact-image | `OMNIGENT_SERVER_URL`, `OMNIGENT_API_TOKEN` | `vars` / `secrets` | The control-plane endpoint and token the production registration authority polls for each launched host. |
| exact-image | `MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER` | `vars.MOONMIND_OMNIGENT_EXPECTED_HOST_OWNER` | The owner a registered host must report; a host owned by something else is not this deployment's execution. |
| protected-live | `OMNIGENT_ENABLED` | `vars.OMNIGENT_ENABLED` | The owning test refuses to open a session unless the Omnigent gate is on. |
| protected-live | `OMNIGENT_SERVER_URL` | `vars.OMNIGENT_SERVER_URL` | The server the live sessions are created against. |
| protected-live | `OMNIGENT_API_TOKEN` | `secrets.OMNIGENT_API_TOKEN` | The bearer token for that server. |
| protected-live | `OMNIGENT_DEFAULT_AGENT_NAME` | `vars.OMNIGENT_DEFAULT_AGENT_NAME` | The agent each concurrent session binds. |
| protected-live | `MOONMIND_OMNIGENT_PROVIDER_PROFILE_ID` | `vars.MOONMIND_OMNIGENT_PROVIDER_PROFILE_ID` | The credentialless Zen route the bounded load is placed on. |
| protected-live | `MOONMIND_OMNIGENT_PROTECTED_LIVE_CONCURRENCY` | The job, on opt-in dispatch only | Release admission. Its absence is `blocked`, not `unavailable`: refusing to admit is a different operator action from a missing credential. |

The four protected-live variables are `PROTECTED_LIVE_REQUIRED_ENV` and the
five exact-image ones are `EXACT_DOCKER_REQUIRED_ENV`. The runner's
precondition, the owning test and the workflow job all read those tuples — a
GitHub `environment:` cannot inject them into the test process, so each job
passes every name explicitly.

The runner resolves the **whole support identity before it runs any layer**. A
blank or malformed value fails immediately, naming the flag and the repository
variable behind it, so a completed exact-image wave — hours of real hosts on a
real daemon — is never spent and then discarded because the record it would be
filed under could not be built. `--moonmind-commit` comes from `github.sha`,
and the policy-version flags default to the in-force policy refs.

A valid invocation always writes its record, including when rows failed,
`unavailable`, `blocked` or `partial`. The gate is the exit code; the record is
evidence and survives the gate.

The runner converts each (layer, level) outcome into one row. A missing daemon,
missing exact image, missing host server endpoint, missing PostgreSQL cluster
for a layer whose owners need one, or an unconfigured protected-live route
produces `unavailable` rows; a protected-live run that was not admitted
produces `blocked` rows; owning tests that pass without publishing an
observation produce `partial` rows. In every case the rows are written to the
record and the command exits non-zero.

Every layer's environment precondition is checked *before* its owning tests
run, so a missing dependency is recorded as `unavailable` naming what was
absent rather than as a `failed` row that reads like a concurrency defect.
`layer_preconditions(layer)` composes that check from the layer's own catalog
owners rather than from a list kept beside a layer name: `owning_test_files`
resolves exactly the files the runner will spawn, and any layer owning a test
that requests the `control_plane_postgres_url` fixture gets the database check
automatically. A PostgreSQL-dependent owner added to a layer therefore cannot
outrun that layer's precondition. The protected-live precondition checks the
same `PROTECTED_LIVE_REQUIRED_ENV` its owning test reads, so the layer is never
entered on an environment its own owner will reject.

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
