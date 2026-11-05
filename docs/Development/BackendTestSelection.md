# Backend Test Selection Strategy

## Purpose

Keep pull request feedback useful and fast without silently losing coverage. Run the inexpensive safety net for backend changes, select specialized suites at the boundaries affected, and select broader coverage when classification is uncertain. Here, fail-open means **run more tests**, never accept failed or missing required execution.

The executable owners are `tools/select_test_suites.py`, `tests/conftest.py`, `tools/verify_test_shard_ownership.py`, and `.github/workflows/pytest-unit-tests.yml`. This document describes their responsibilities and the intended verification policy. Proposed optimizations are not proof of implemented behavior.

Follow [AGENTS.md](../../AGENTS.md): targeted local tests support development and diagnosis. GitHub Actions owns broader regression, integration, and browser verification. A broad local run is not a prerequisite to opening or updating an authorized PR.

## Test Categories

Classification follows resources started by a test, not simply the module it imports.

| Category | Marker | Boundary |
| --- | --- | --- |
| Fast unit | `unit_fast` | Pure Python logic with no Docker, network, external process, or Temporal test server. |
| Slow unit | `slow` | Expensive unit tests, selected on full runs and relevant direct changes rather than the default fast path. |
| Component | `component` | In-process API/router/service wiring, TestClient, and dependency overrides. |
| Temporal boundary | `temporal_boundary` | Real test-server/worker/replay behavior, signals, updates, queries, serialized payloads, and Activity boundaries. |
| Reliability journey | `reliability_journey` | Hermetic production orchestration, scripted external counterparts, and real recovery/artifact boundaries. |
| Hermetic integration CI | `integration` and `integration_ci` | Disposable Compose-backed local dependencies, without external credentials. |
| Provider verification | `provider_verification` and provider markers | Live external-provider checks in explicitly credentialed environments, outside required credential-free PR CI. |

`pyproject.toml` owns marker registration. `tests/conftest.py` owns classification for existing tests. Unit ownership precedence is `slow > temporal_boundary > component > unit_fast`. The ownership verifier checks every provider-free unit, API component and integration node, including integration tests with missing ownership markers. Live provider, GPU and operator-owned deployment fixtures declare their external resource requirements. Host-side controller and credential-free exact-artifact tests retain their existing executable runners outside the Compose pytest service.

Reliability tests are excluded from the general Compose integration corpus so the same journeys are not executed twice under different names.

## Selector Contract

`tools/select_test_suites.py` consumes changed paths and emits:

```text
unit_fast=true|false
unit_slow=true|false
api_component=true|false
temporal_boundary=true|false
integration_ci=true|false
integration_hermetic=true|false
integration_fresh_journey=true|false
integration_upgrade_journey=true|false
integration_controller_journey=true|false
ownership_full=true|false
reliability_journey=true|false
exact_artifact=true|false
omnigent_conformance=true|false
full_backend=true|false
frontend_static=true|false
frontend_browser_chromium=true|false
frontend_browser_firefox=true|false
full_frontend=true|false
```

`backend_matrix` and `integration_matrix` additionally contain compact JSON for the selected Actions rows. The integration aggregate is the OR of its four lane decisions. The installed matrix contains the three journey lanes; hermetic tests start independently of application-image preparation. Empty matrices are intentional only when their job is unselected. `ci-required` independently validates every boolean and the exact row inventory, including all four reliability partitions, before accepting native aggregate results. Malformed/missing outputs, omitted or duplicate rows, selector failures, and selected failures/cancellations/timeouts are rejected.

### Shared Changed-File Helper

`tools/ci/compute_changed_files.sh` owns event-aware base/head resolution for selection, deployment validation, and generated-contract detection. `select-test-suites` runs it once and derives both suite selection and the `generated_contracts` output from the same list. It fetches exact commits as needed from a shallow checkout and emits a two-dot tree diff. Known PR, push, and merge-group changes use their actual base/head. Unavailable history, first pushes, schedules, and manual runs take the conservative unknown-change path. Do not add a second event classifier.

### Backend Detection

Backend source, tests, tooling, migrations, and sensitive generated contracts select the fast-unit safety net. The selector exempts six explicitly audited prose paths from the general Markdown fast-unit rule. Unlisted Markdown retains existing coverage, including executable examples and Skill contracts. Sensitive, mixed, unknown, and full-run changes keep their existing suite decisions; prose exemptions never override them. Cheap preflight policy checks run in every source/full verification. Artifact-only qualification requires the complete current source receipt instead of repeating those checks. Full ownership collection runs for changed collection inputs as described below.

Frontend selection is independent. Generated API-client changes require static validation. UI source selects static checks and Chromium. Browser tests, styles, configuration, and dependency changes can also require Firefox. Full/unknown paths select full frontend coverage.

### Component Selection

API routers, authentication, database/service wiring, component/API tests, and generated API contracts select component coverage. Exact path predicates and their regression tests live in the selector and `tests/unit/tools/test_select_test_suites.py`, rather than a second path registry in this document.

### Temporal Boundary Selection

Workflow/runtime/worker code, Temporal-facing schemas, relevant tests, and changes to replay-visible invocation or payload behavior require Temporal-boundary coverage. Adapter changes select both Temporal and reliability coverage because adapter results cross durable workflow boundaries.

### Reliability Journey Selection

Reliability coverage is selected for orchestration and runtime seams, checkpoints, skill materialization, replay fixtures, and managed runtime packaging. Saved-preset definitions, catalog/readiness code and conversion also select the journeys that consume them. Shared CI, dependency, selector, or global test-configuration changes take the full-backend path.

The credential-refresh journey uses generated gh/Git readers against controlled HTTPS, including lost acknowledgment and revoked issuance. Docker and GitHub App edges are substituted; live accounts and host-container lifecycle remain unverified.

### Backend Matrix Ownership And Failure Propagation

`backend-matrix` instantiates only selected unit-fast, api-component, temporal-boundary, and reliability rows. Reliability selection always emits all four partitions with their existing bounds. A unit-only selection creates one runner instead of seven. The matrix job is skipped when no primary backend suite is selected; it does not create placeholder success rows. Existing suite-specific setup and test guards remain as defense in depth.

Native `strategy.fail-fast` is enabled outside scheduled diagnostics. It can cancel siblings inside this matrix after a failure. It does not cancel unrelated frontend, integration, image, or migration jobs. Scheduled diagnostics keep collecting sibling outcomes within their execution bounds. Superseded-run cancellation remains a separate existing Actions concurrency behavior.

Fast rows retain their suite-specific xdist execution. Unit-fast and Temporal use per-file distribution, while API/component uses per-test distribution. Reliability runs serial pytest within each isolated runner, using its own Compose project, network, database, Temporal, and object-store state. Do not combine matrix fan-out with a new unbounded inner worker pool or shared mutable fixture stack.

The workflow initializes only submodules a selected job needs. In the reviewed implementation, unit-fast initializes MoonSpec and Omnigent fixtures and API/component initializes Omnigent. Do not assume that only the projection job needs a submodule, or initialize every submodule for every row.

### Selective Ownership Preflight

Policy scans and MoonSpec projection remain unconditional in the existing parallel preflight job. `ownership_full` selects the unchanged real verifier for tests (including fixtures, conftests, markers, plugins and duration data), collection/reporting tools, dependencies, workflow/selector changes, unknown or malformed inputs, and full-main/scheduled/manual events. Known production-only and audited prose changes do not rerun collection. The selector's tested input map is the authority; new collection inputs must extend it.

A selected verification still collects real pytest nodes, proves exactly one logical owner, and invokes the installed pytest-split plugin for all four partitions to prove completeness and disjointness. No replicated partition algorithm or cached assertion substitutes for that proof. The verifier's failure remains a preflight failure.

### Execution Budgets

The workflow is the authority for effective limits. Each row has a simple
bound sized from observed setup, testing, and bounded diagnostics/cleanup:

| Lane | Per-test timeout | Test-step bound | Job bound |
| --- | --- | --- | --- |
| Unit-fast | 60s (`--timeout 60`) | 15-minute native test step | 21-minute job |
| API/component | 120s (`--timeout 120`) | 12-minute native test step | 18-minute job |
| Temporal boundary | 120s (`--timeout 120`) | 12-minute native test step | 20-minute job |
| Ordinary reliability shard | 150s | 600-second shell deadline inside a 12-minute Actions step | 20-minute job |
| Scheduled reliability shard | 300s | 660-second shell deadline inside a 12-minute Actions step | 20-minute job |

Each invocation also sets a faulthandler stack-dump threshold below its
terminating per-test timeout (`-o faulthandler_timeout=`: 45s unit-fast,
90s API/component and Temporal, 120s ordinary and 270s scheduled
reliability). The dump only writes every thread's stack into the teed log,
including from xdist workers; pytest-timeout still terminates the test. The
`faulthandler_timeout = 300` in `pyproject.toml` is the local default only.

The reliability dependency-start step (Compose pull, `up --wait` and address
inspection) has a 5-minute native step bound; the 180-second readiness wait
alone does not bound the pull. The reliability shell deadline sends SIGINT
so pytest tears down owned stacks and writes its JUnit report, then
escalates to SIGKILL after a 30-second grace that still ends inside the
native step bound. Every row records its test command's exit status, and the
evidence summary reports the termination as a cooperative deadline stop
(124), a hard kill (137), an ordinary pytest exit, or unavailable when the
native step bound, cancellation or runner loss stopped the step first.

Unit-fast and API/component keep six minutes outside their 15/12-minute test
steps for setup and reporting. The full unit corpus can exceed twelve
minutes while making progress; API/component can exceed seven without failures.
The normal summary and upload steps each have a 2-minute cap. Temporal keeps
eight minutes for setup and reporting around its 12-minute test step: the full
corpus can exceed seven minutes while passing on slower runners.
Reliability 20-minute jobs also cover Compose
pull/up (~1-2 minutes), their 12-minute test step, and bounded diagnostics/
cleanup. Job deadlines remain hard ceilings: slow setup or multiple exhausted
failure-diagnostic caps can prevent final uploads. The reliability bound
reflects measured load: its heaviest duration-balanced partition holds ~500s of tests plus
collection/shutdown overhead, so it must not receive a shorter aspirational
cutoff. A justified individual slow test can use the installed
pytest-timeout per-test marker as a documented exception; global defaults
are never raised to fit the table.

Use the installed pytest timeout mechanism for stuck tests and native Actions step/job bounds for the outer process. Keep an existing shell deadline only where it provides a distinct useful bound. A cooperative session timeout and a diagnostic stack dump do not replace a hard process bound. No additional timeout framework, test retry loop, watchdog, or cancellation service is needed.

Pytest fail-fast (`--maxfail=1` on ordinary runs, omitted on schedules so
diagnostic collection continues) stops one invocation. Native matrix fail-fast stops its siblings. The bounds above are the implemented
behavior, not tuning proposals.

### Reliability Sharding

The collected reliability universe is split into four groups through pytest-split:

```text
--splits 4 --group N --splitting-algorithm least_duration
--durations-path tests/.reliability-test-durations.json
```

`N` is 1 through 4. All shards use the same checkout, collection inputs, and advisory hints. `tools/ci/refresh_reliability_durations.py --validate-only` checks hint usability. Missing or unusable history warns and falls back consistently; it must not exclude new tests or make an otherwise correct test fail. The ownership verifier checks the actual plugin collections for complete, disjoint coverage.

#### Measured Duration Hints

`tests/.reliability-test-durations.json` contains observed per-node JUnit total durations (setup + call + teardown), never file averages or seeded estimates. Pytest-split estimates untimed new/renamed cases and still selects them exactly once. Missing, boolean, negative or non-finite hints trigger the consistent no-history fallback.

The existing pytest evidence plugin writes each shard's exact node IDs, semantics and source/run/attempt. Refresh only from one complete passing four-shard run:

```bash
gh run download <run-id> -p 'pytest-reliability-shard-*-attempt-<n>' -D /tmp/reliability-durations
python3 tools/ci/refresh_reliability_durations.py import /tmp/reliability-durations
```

The importer rejects incomplete/mixed identities, failed shards, duplicate nodes or invalid durations without changing the committed file. An accepted import atomically replaces it with measured observations and prints provenance and per-shard load. Keep that evidence in the PR; timing artifacts are advisory placement data, not a correctness cache.

### Reliability Docker Fixture Layers

The Compose dependency file uses pinned registry images, and the reliability corpus does not build case-specific images.

If a surviving test adds an expensive build, inspect actual cold/warm build output and separate build cost from routing waits and startup. Prefer native layer caching; add only a small immutable dependency fixture if a material remaining cost is demonstrated.

Mutable release records, images under test, containers, volumes, queues, and workspaces remain case/shard-owned. A cached base must not make an intentionally removed case image appear restored. Cold execution must remain correct, and cleanup must not remove another case's resources or use global Docker prune. The existing integration/exact-artifact build caches remain separate from this measurement question.

### Logs And Diagnostic Evidence

The existing workflow streams combined pytest output through `tee`, captures the pytest exit code, and uses `tools/ci/write_backend_matrix_summary.py` with logs, actual JUnit reports, and timing artifacts. Extend that path rather than introducing another reporter.

Keep normal success evidence small: tested revision, suite/shard and attempt identity, real output, any JUnit report, and useful slow-case timings. Existing artifacts use finite retention and distinct shard/attempt names. Richer service diagnostics belong on the failure path where useful, from known test-owned locations with redaction. Do not collect whole environments, source trees, tokens, or unrelated host files.

The disposable fresh, upgrade, and controller journeys retain redacted Compose startup output in `var/artifacts/first-run-3938/<mode>/compose-startup-<phase>-<attempt>.log`. Each attempt keeps the first 5,000 lines and final 40 without overlap, with an explicit omission notice when middle lines are dropped. Retained lines carry UTC receipt timestamps, and the summary records phase, attempt, elapsed whole seconds, and the original Compose exit status. Receipt time describes arrival at the host recorder. The console prints the final 40 lines and summary; the workflow uploads these bounded logs for successful and failed journey rows. A logging failure fails the journey without retrying a successful Compose startup. After each ready startup the journey also writes `resource-footprint-<phase>.log`: the effective Compose services split into long-lived, one-shot init (`service_completed_successfully` dependencies), optional profiles, and profile services observed running on demand, plus `docker stats --no-stream` for the project's containers and `compose top temporal-worker-workflow`. Its final `footprint phase=...` line totals containers, memory, and workflow-worker processes. The upgrade journey's `source` and `candidate` files compare the old release with the candidate on one runner. An unavailable observation is recorded and never fails the journey.

Missing or partial JUnit is unavailable/incomplete evidence, never zero tests or a pass. Preserve the original failure through logging, report generation, and cleanup. Bound secondary operations separately so a hung log command cannot consume the rest of the job. Matrix cancellation is best effort for artifact collection, and a hard job kill or runner loss can prevent final uploads entirely.

Validate interruption with a real disposable subprocess through the production reporting path. Fabricating post-kill artifacts proves a parser can read them, not that a killed test leaves them. No new universal fault-injection framework or documentation-wording tests are required.

### Reproducible Before/After Comparison

Use existing CI evidence before commissioning another run. Compare equivalent selected coverage and runner resources, distinguish cold/warm setup and queue delay, and report both longest-row latency and summed runner execution. A failed/canceled partial corpus is not equivalent to a complete passing run.

If the change removes an authorized obsolete capability, identify the retired work and the retained replacement outcomes. Do not advertise that as equivalent coverage of the deleted mechanism. Do not require a new monitoring service, exact timing baseline, or performance gate to make a small improvement. An evidence-backed no-change disposition is valid when an optimization would add more complexity than benefit.

### Hermetic Integration CI Selection

The integration selection has four lanes: the independent hermetic job and the installed fresh-journey, upgrade-journey, and controller-journey matrix (including host journal transitions). Each owns its checkout, Compose project, artifacts and teardown. Matrix fail-fast and the wrapper's `--maxfail=1` apply outside schedules. Compose cleanup gets `MOONMIND_TEST_CLEANUP_TIMEOUT` seconds (default 120) plus a 10-second kill grace. The hermetic job bounds setup, proxy checks, and real scoped-egress checks separately, reserving time for the main suite's cleanup and reports. Overall and phase limits live in the [CI workflow](../../.github/workflows/pytest-unit-tests.yml). Each row collects its own redacted outputs: the hermetic JUnit report, log and project diagnostics on every run, or failed journey logs, including journal transition evidence on controller-journey. Runner loss, cancellation or excess overhead can prevent reporting.

The executable dependency map narrows only audited lane-local inputs: ordinary integration tests/fixtures select hermetic; the journal transition test selects controller-journey; unclassified host tests/fixtures retain hermetic plus controller-journey; shared journey scripts select all three journeys. Mixed changes union their lanes. The shared changed-file helper includes rename sources and destinations and deleted paths, so removal does not erase an owner.

Unknown/full changes, dependencies, Docker/Compose, shared boot/runtime/auth/config and migration boundaries conservatively retain all four. Generated OpenAPI, its generators, shared client transport and native-chat/Omnigent contracts retain cross-layer coverage. Frontend paths are not blanket exclusions. Reliability journeys keep their separate owner.

- Single-user impact: settings/secrets/preset routers and services, frontend transport, worker binding, machine-authority helpers, the credential-conversion services (`api_service/services/profile_secret_migration.py`, `api_service/services/single_user_conversion.py`), and the single-user test packages (`moonmind/single_user/`, `tests/unit/single_user/`).
- Existing Compose journeys exercise fresh installation, account-era upgrade and controller Operations through the API and built dashboard, without a seeded person or live provider credential. The eligible upgrade requires publication; PostgreSQL separately covers protected refusal. Workspace settings, global presets, schedules and executions retain their data through instance consumers without no-op plugins. Unsupported personal data refuses unchanged. Presets retain explicit inputs across restart and upgrade. Controller checks cover submission, errors/logs, reconnect after API replacement and retry without a Docker daemon. Failed or unobserved checks fail their lane.
- Fresh and upgraded candidates also admit immediate scratch work with deployment-default selections. A test-only worker controls external provider catalog observation and execution: an exact-host probe advertises one fixed supported model, while normal host attestation and model-membership checks remain intact. The worker opens the real admitted Omnigent session and runs deterministic report computation in its actual host. Production owners persist compute, archive the workspace and finalize/clean the host. The operator API downloads and verifies the generated report, then the helper removes only that verified saved workspace and downloads again. This proves composed application integration, not native model-turn execution or external free-model availability. Lost-acknowledgment redelivery must retain one execution and consistent compute evidence.
- Each deployment uses a disposable worktree, project-specific runtime volumes/networks and scoped teardown. `tools/single_user_journey_provider.py` selects fresh/upgrade CI; the cutover helper selects upgrade only; shared runner, API checker and browser changes select all three journey rows.
- Single-user integration suites select `integration_ci=true` through the `tests/integration/` prefix (reliability-owned `tests/integration/reliability/` stays excluded). `ci-required` checks the selected hermetic and installed-job results independently.

The candidate waits for a ready default profile before admission, redelivery and cancellation. Upgrade submits through authenticated controller WSGI admission. Pull refusal preserves workers and operator access. After Compose apply, a process gate delays API startup while the observer loses acknowledgment. A new observer resumes the installed checkpoint once; redelivery preserves operation and container identities. Opening the gate runs normal startup and healthchecks. Retained work remains readable; a fresh journey follows. Production ingress remains separately unverified.

`upgrade-recovery.json` retains observations; `routing-cutover-diagnostics.json` retains original errors and redacted logs. Reads share 30 seconds; helpers receive one stack dump after 120 seconds.

The hermetic PostgreSQL journey adds 12,000 tombstones to startup, admission and retention. Inputs contain service-produced bytes. One-item sweep pages release locks, checked with `NOWAIT`. JUnit's `retained_scale` records cardinality, query count, full query plans and runtime; assertions bound indexed work without latency thresholds.

`tools/test_integration.sh` builds its test image unless `MOONMIND_PYTHON_TEST_IMAGE` supplies an already loadable image. It writes `artifacts/integration-ci/` on the host, also when it runs Compose from a temporary checkout copy, and exits with pytest's status. CI sets `MOONMIND_INTEGRATION_FAIL_FAST` and a 25-minute `MOONMIND_INTEGRATION_TIMEOUT`. The deadline covers Compose startup, pytest and storage restart verification together, interrupts with SIGINT to preserve the report, then exits 124 (137 after the 30-second kill grace). CI's existing image layer cache and per-file xdist execution are reused. Installed rows restore the shared immutable application candidate instead of rebuilding it. The default worker count follows available CPU and memory through `tools/test_worker_count.py`; `MOONMIND_INTEGRATION_WORKERS` accepts a positive integer override. File-level distribution keeps file-owned resources together. This host-side path is not a reason to expose Docker to a managed agent.

The shared conftest's crash-recovery override depends on pytest-xdist 3.8.0's
private `LoadScopeScheduling` state behind the public scheduler hook. It retains
the original failed-test report, drops completed files from requeueing, and
supplies a replacement worker's required next item before execution. The latter
matches [the upstream pytest-xdist fix](https://github.com/pytest-dev/pytest-xdist/pull/1328).
`tests/unit/workflows/temporal/test_pytest_worker_crash_recovery.py` is the slow,
real-subprocess compatibility coverage, including a replacement facing queued
single-test files. Crash readiness (real operation and ownership receipt) has
a separate 10-second setup bound before the 3-second fatal test-body timeout.
Child test timers exclude fixture setup; a 40-second outer deadline bounds
the full subprocess, including startup, setup, and recovery.
When updating the declared xdist dependency, run these cases against the native
scheduler; remove the override once a released version
preserves the failed exit, runs each survivor once, and finishes those cases.
The timeout hook separately retains pytest-timeout 2.4.0's fatal callback and
cleans only SDK time-skipping and local dev-server children directly owned by
that worker. CLI cleanup recognizes `server start-dev` for both SDK-cached and
caller-supplied executable paths, while retaining the direct-parent boundary.
On Linux, cleanup rechecks each child's ownership and invocation after opening
a stable process handle, force-stops it like the SDK's normal shutdown, and
waits at most two seconds total for exit without reaping SDK-owned children.
This avoids leaving a CLI draining after its worker exits; an unconfirmed exit
is reported without replacing the original timeout failure.
Real subprocess cases retain an independent control server through fatal
timeouts and debugger suppression. Image builds cache both SDK-selected
binaries, retrying HTTP 429/5xx downloads after 30 and 60 seconds before
preserving the final error. Cached-startup coverage disables download egress.
Neither mechanism fixes the test behind the original worker failure.

Deployment and both disposable Compose test stacks pin the same multi-platform MinIO community
image from `ghcr.io/coollabsio/minio` by release and digest. The original
`quay.io/minio/minio` image is no longer publicly pullable. The replacement is
[built from upstream MinIO source](https://github.com/coollabsio/minio); it retains
the S3 protocol and root-credential environment interface exercised by artifact
integration tests. The default first-run journey exercises that same image through
the deployment Compose file. Existing deployment volume names and mounts remain
unchanged; upgrading the image must preserve stored artifact bytes.

### Omnigent Conformance Selection

The selector owns the Omnigent path and runner-input inventory. Selected conformance consumes evidence from the owning layers instead of rerunning those suites under another name. `ci-required` enforces the selected result. Live-provider qualification stays distinct from deterministic credential-free conformance.

## Full Backend Path

Unknown or unavailable changes, empty change input, pushes to main, schedules, manual runs, and shared CI/dependency/runner/selector/global-test changes select full verification. The full path uses the same exclusive owners:

```text
unit-fast + unit-slow + api-component + temporal-boundary
+ reliability-journey through four shards + integration-ci
```

Full execution does not switch fast-unit into a broad wrapper that duplicates specialized suites. Exact-artifact, conformance, frontend, and generated-contract selection continues through their existing policy. An optimization must not silently move required recovery behavior out of PR coverage.

## Required Check Model

`ci-required` is the stable, always-running result aggregator. It performs no checkout, dependency setup, or repository execution. It checks the existing policy, projection, ownership, selected backend, frontend, generated-contract, and other workflow dependencies and reports their unsuccessful outcomes.

For primary backend execution it consumes the matrix aggregate result, not the last matrix row's output. Failed, timed-out, canceled, or unexpectedly skipped selected work cannot pass. Explicit selector nonselection is allowed but is not execution evidence. A matrix with no selected Python work must not conceal an unsuccessful selector.

`preflight-policy` remains the existing owner of repository policy checks and the other always-run static invariants (the MoonSpec projection drift gate and the shard-ownership verifier), not a reason to rescan the repository in test rows or test documentation phrasing. The ownership verifier protects complete/disjoint selection.

Branch-protection requirements remain `ci-required` for the aggregated suites, the standalone `migration-gate`, and applicable independent repository checks such as CodeQL. Do not rename public required contexts or change branch protection as a side effect of test optimization. Separate requirements for retired/renamed internal aggregators should be reconciled through their owning configuration, not worked around with fake checks.

Required evidence must correspond to the current merge candidate. Use the existing merge-group path where available, or keep the PR up to date with main before merging. A green run from a different base does not prove that concurrent migrations compose correctly.

## Main, Manual, And Scheduled Runs

Main pushes, manual dispatches, and schedules run the full path. The existing test workflow owns hermetic integration for these events. Scheduled diagnostics disable matrix fail-fast but retain bounds. No second scheduled reporter or standalone duplicate integration workflow is required. Credentialed provider checks run only where that access is explicitly available and authorized.

## Local Commands

For a focused selector change inside a managed workflow:

```bash
moonmind container python-tests tests/unit/tools/test_select_test_suites.py
```

Outside a managed workflow:

```bash
./tools/test_unit.sh --python-only tests/unit/tools/test_select_test_suites.py
```

The same entrypoints accept other targeted paths or node IDs. Broader suites normally run in GitHub Actions. For a justified host-side integration reproduction, use `./tools/test_integration.sh` and its disposable services. Managed agents do not run nested Docker or acquire a deployment socket to reproduce CI.

To reproduce a reliability partition in an already prepared, disposable host/CI test environment, use its recorded pytest command with `--splits 4 --group N --splitting-algorithm least_duration` and the same validated hints. Omitting the hints path reproduces the consistent no-history fallback. Service addresses, networks, and fixture prerequisites must match that isolated environment, not the installed deployment. The workflow contains the exact suite commands, marker expressions, and environment setup.

`python3 tools/ci/refresh_reliability_durations.py` validates the committed hints; its `import` subcommand refreshes them from observed CI timings (see [Measured Duration Hints](#measured-duration-hints)). `python tools/verify_test_shard_ownership.py` checks the eligible provider-free universe in its supported environment. These are not broad local prerequisites to every PR.

The source-destroying checkpoint-resume journey still exercises durable capture/restore and idempotent recovery. It does not by itself prove the entire Temporal-to-managed-runtime journey. Use the appropriate owning integration boundary rather than treating one helper test as complete product verification.

## Maintaining The Selector

Keep path rules and their focused behavior tests together. Use conservative selection when uncertain. Selector changes require full CI because incorrect classification can silently omit tests.

During an authorized architecture removal, update obsolete test ownership, fixture setup, and duration hints with the retired code. Preserve real coverage of supported default journeys, data integrity, and the active-work transition. Do not preserve every historical class or versioning parameter solely because an older issue listed it. Explain moved/retired coverage briefly in the PR instead of building a permanent registry or approval mechanism.

### Source, Image And Evidence Ownership

The release workflow starts reusable `source` verification alongside amd64/arm64 builds. Its `artifact` phase follows assembly and recovery preparation. Receipt verification, bound to its bytes, attempt and selection, overlaps that phase; publication also requires the image it consumed. One advisory release timing report covers both phases. Pull requests, scheduled checks and manual full runs use the same reusable workflow with both phases. Main pushes have one source-suite owner, including frontend and generated-contract checks; the image build compiles assets without rerunning those host checks. Compatible dependency updates are grouped to reduce simultaneous full-suite PRs; major updates retain independent review.

Every PR application-image consumer restores one shared OCI archive through `release_image_fixture.py`, verifies its immutable manifest and checks the image's source label. Forks need no registry publication permission. Release consumers pull the supplied assembled manifest. Recovery preparation overlaps source tests and app construction; server/host targets share one graph. A compressed stream avoids a second raw archive; consumers remove verified job-owned downloads after loading. Archive checks bind bytes, image IDs, source/gitlink and run/attempt. Same-session host replacement and interrupted-step recovery run separately from app/API/worker/browser probes, against that same application candidate.

The deterministic conformance reporter consumes exact owner receipts and the actual collection manifest. It checks complete node coverage, owner identity, outcomes and source/run/attempt before reporting; it retains strict owner-log secret scans, named credential fixtures and the pending production-shaped case. Docker recovery remains separately required on the installed image. Missing, corrupt, canceled, interrupted, stale or skipped-required evidence fails reuse. Failed-job reruns reuse successful owners from the same immutable run, retaining the producer's attempt in the artifact directory even for a single download; a latest failed receipt never falls back to an older pass. Full verification checks the complete collected source universe. The standalone runner executes the source layers for local diagnosis. Different Python environments remain explicit: host source checks use Python 3.12 and the test-runtime image uses the Dockerfile's Python version.

The slow lane consumes paths from real ownership collection. When collection inputs are unchanged, the checked-in inventory is its fallback. Unit-test changes select the slow owner too, so adding a marker cannot silently omit a new slow case. The narrow native-chat test-only exception is shadow-checked against collected ownership and static helper consumers; missing/new/shared inputs broaden verification, and a changed consumer owner rejects an outdated exception. Full main and scheduled coverage remains the backstop.

SQLite optimization shares only a closed, empty schema file; each test copies it and owns fresh engines, sessions and artifacts. PostgreSQL shares only the worker's server: migration and control-plane tests receive fresh databases, including subprocess DDL. Destructive cluster/process tests retain dedicated ownership. Policy clocks can advance decision intervals, while real pipe draining, process termination, Activity timeouts and replay witnesses remain.

### Timing And Cache Evidence

Before pytest, the owning job records its interpreter, resolved packages, runtime inputs, declaration hashes and arguments. Reuse requires exact agreement with that manifest and current dependency declarations. It trusts the same-run producer; installed-image qualification still executes against its candidate. PID ownership protects nested probes. Receipts retain exact nodes, subtest outcomes, phases, collection time and workers; Vitest retains assertions. Hosted timing artifacts distinguish queue, runner usage and step costs, with unfinished or unavailable observations explicit.

The dependency-input archive keys platform, resolved bases and dependency/tool inputs, verifies the restored image label, and owns runtime caching. Cache misses read common release layers. Source/README edits do not reinstall dependencies, and cache hits still run tests. PR app/recovery builds read release caches; first automatic main attempts own recovery-cache export. Release builds own platform layer caches and omit package-manager mount export. Actions artifact digests bind owner logs.

Compare complete passing runs on clean hosted runners, distinguishing cold/warm caches, build/export/acquisition, collection, tests and cleanup. Keep measured samples separate from estimates and overlapping gains. Do not count shifted pull time as savings or use global Docker prune: fresh/upgrade/recovery samples own their resources and preserve their historical saved-data checks. Required `ci-required` and migration result linkages remain enforced.
