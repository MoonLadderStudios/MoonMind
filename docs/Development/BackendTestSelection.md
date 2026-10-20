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

`pyproject.toml` owns marker registration. `tests/conftest.py` owns classification for existing tests. Prefer an explicit resource marker for a clear boundary. Unit ownership precedence is `slow > temporal_boundary > component > unit_fast`. The ownership verifier checks that eligible provider-free tests have exactly one owner.

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

`backend_matrix` and `integration_matrix` additionally contain compact JSON for the selected Actions rows. The integration aggregate is the OR of its five lane decisions. Empty matrices are intentional only when their job is unselected. `ci-required` independently validates every boolean and the exact row inventory, including all four reliability partitions, before accepting native aggregate results. Malformed/missing outputs, omitted or duplicate rows, selector failures, and selected failures/cancellations/timeouts are rejected.

### Shared Changed-File Helper

`tools/ci/compute_changed_files.sh` owns event-aware base/head resolution for selection, deployment validation, and generated-contract detection. `select-test-suites` runs it once and derives both suite selection and the `generated_contracts` output from the same list. It fetches exact commits as needed from a shallow checkout and emits a two-dot tree diff. Known PR, push, and merge-group changes use their actual base/head. Unavailable history, first pushes, schedules, and manual runs take the conservative unknown-change path. Do not add a second event classifier.

### Backend Detection

Backend source, tests, tooling, migrations, and sensitive generated contracts select the fast-unit safety net. The selector exempts six explicitly audited prose paths from the general Markdown fast-unit rule. Unlisted Markdown retains existing coverage, including executable examples and Skill contracts. Sensitive, mixed, unknown, and full-run changes keep their existing suite decisions; prose exemptions never override them. Cheap preflight policy checks remain unconditional. Full ownership collection runs for changed collection inputs as described below.

Frontend selection is independent. Generated API-client changes require static validation. UI source selects static checks and Chromium. Browser tests, styles, configuration, and dependency changes can also require Firefox. Full/unknown paths select full frontend coverage. `test-frontend` aggregates selected frontend jobs even when intentional nonselection leaves both browser jobs skipped.

### Component Selection

API routers, authentication, database/service wiring, component/API tests, and generated API contracts select component coverage. Exact path predicates and their regression tests live in the selector and `tests/unit/tools/test_select_test_suites.py`, rather than a second path registry in this document.

### Temporal Boundary Selection

Workflow/runtime/worker code, Temporal-facing schemas, relevant tests, and changes to replay-visible invocation or payload behavior require Temporal-boundary coverage. Adapter changes select both Temporal and reliability coverage because adapter results cross durable workflow boundaries.

### Reliability Journey Selection

Reliability coverage is selected for orchestration and runtime seams, checkpoints, skill contracts and their materialization, replay fixtures, and managed runtime packaging. Shared CI, dependency, selector, or global test-configuration changes take the full-backend path.

These journeys test retained user outcomes through real production composition. Scripted external providers remove network/credential dependence, not the boundary under test.

### Backend Matrix Ownership And Failure Propagation

`backend-matrix` instantiates only selected unit-fast, api-component, temporal-boundary, and reliability rows. Reliability selection always emits all four partitions with their existing bounds. A unit-only selection creates one runner instead of seven. The matrix job is skipped when no primary backend suite is selected; it does not create placeholder success rows. Existing suite-specific setup and test guards remain as defense in depth.

Native `strategy.fail-fast` is enabled outside scheduled diagnostics. It can cancel siblings inside this matrix after a failure. It does not cancel unrelated frontend, integration, image, or migration jobs. Scheduled diagnostics keep collecting sibling outcomes within their execution bounds. Superseded-run cancellation remains a separate existing Actions concurrency behavior.

Fast rows retain their suite-specific xdist execution. Unit-fast and Temporal use per-file distribution, while API/component uses per-test distribution. Reliability runs serial pytest within each isolated runner, using its own Compose project, network, database, Temporal, and object-store state. Do not combine matrix fan-out with a new unbounded inner worker pool or shared mutable fixture stack.

The workflow initializes only submodules a selected job needs. In the reviewed implementation, unit-fast initializes MoonSpec and Omnigent fixtures and API/component initializes Omnigent. Do not assume that only the projection job needs a submodule, or initialize every submodule for every row.

### Selective Ownership Preflight

Policy scans, deployment safety, and MoonSpec projection remain unconditional in the existing parallel preflight job. `ownership_full` selects the unchanged real verifier for tests (including fixtures, conftests, markers, plugins and duration data), collection/reporting tools, dependencies, workflow/selector changes, unknown or malformed inputs, and full-main/scheduled/manual events. Known production-only and audited prose changes do not rerun collection. The selector's tested input map is the authority; new collection inputs must extend it.

A selected verification still collects real pytest nodes, proves exactly one logical owner, and invokes the installed pytest-split plugin for all four partitions to prove completeness and disjointness. No replicated partition algorithm or cached assertion substitutes for that proof. The verifier's failure remains a preflight failure.

### Execution Budgets

The workflow is the authority for effective limits. Each row has a simple
bound sized from observed setup, testing, and bounded diagnostics/cleanup:

| Lane | Per-test timeout | Test-step bound | Job bound |
| --- | --- | --- | --- |
| Unit-fast | 60s (`--timeout 60`) | 12-minute native test step | 18-minute job |
| API/component | 120s (`--timeout 120`) | 7-minute native test step | 15-minute job |
| Temporal boundary | 120s (`--timeout 120`) | 7-minute native test step | 15-minute job |
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

Unit-fast keeps six minutes outside its 12-minute test step for setup and
reporting. Its full suite can take about nine minutes before final process
exit. Warm setup has taken about 35 seconds; the normal summary and upload
steps each have a 2-minute cap. API/component and Temporal retain 15-minute
jobs and 7-minute test steps. Reliability 20-minute jobs also cover Compose
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

Every entry in `tests/.reliability-test-durations.json` is an **observed** cost: the exact pytest node ID (path, class, and parameter components) and its JUnit `total` duration, meaning setup + call + teardown of that case, recorded by one complete reliability matrix run. Cheap and expensive cases of one file or parameterization keep their own measurements; there are no file averages or seeded estimates. A collected node without an entry (new or renamed test) is **estimated** by pytest-split as the average of the observed entries for the collected nodes and is still selected exactly once. Entries for removed tests are ignored by the plugin until the next import drops them. Genuine sub-millisecond cases may be recorded as `0.0`; booleans, negative, and non-finite values make the file unusable, which falls back to the no-history partition on every shard.

Each reliability row loads `tools/ci/write_backend_matrix_summary.py` as a pytest plugin (`-p tools.ci.write_backend_matrix_summary`), which records the exact node ID and duration semantics in its JUnit report. The same script writes the row's separate `pytest-backend-reliability-shard-N-durations.json` artifact with the run, attempt, and revision. The committed hints stay unchanged throughout a matrix run.

Refresh hints when a measured imbalance or changed corpus warrants it, from one successful run whose four reliability rows all passed:

```bash
gh run download <run-id> -p 'pytest-reliability-shard-*-attempt-<n>' -D /tmp/reliability-durations
python3 tools/ci/refresh_reliability_durations.py import /tmp/reliability-durations
```

The import accepts exactly one snapshot per shard from one run, attempt, and revision. Every shard must report success, every case must carry an exact node ID with a valid duration and the documented semantics, and no node may appear twice. Otherwise it prints the reasons and leaves the committed file byte-for-byte unchanged. An accepted import replaces the file atomically with only the observed entries and prints the source run/attempt/revision and per-shard observed cost. Record those in the PR evidence. Per-run timing artifacts are evidence, not a second correctness database or a requirement for automated timing commits. Do not rebuild sharding that is already present.

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

The integration matrix selects four independent lanes: hermetic, fresh-journey, upgrade-journey, and controller-journey (including host journal transitions). Each row owns its checkout, Compose project, artifacts and teardown. Native matrix fail-fast and the wrapper's `--maxfail=1` apply outside schedules. Each run step has a finite native deadline with room for diagnostics and teardown. Their Compose commands each get `MOONMIND_TEST_CLEANUP_TIMEOUT` seconds (default 120) plus a 10-second kill grace. The hermetic job reserves 12 minutes for bounded checkout, submodule, Buildx and image setup, 23 for execution and cleanup, four for evidence collection/upload and one for runner overhead within its 40-minute deadline; journey jobs retain 30 minutes. After teardown, each row collects only its own redacted outputs: the hermetic JUnit report, streamed log and project diagnostics on every run, or a failed journey's log tree, plus the journal transition test's phase, ps and log evidence on controller-journey. Runner loss, cancellation or overhead beyond the reserve can still prevent collection and upload.

The executable dependency map narrows only audited lane-local inputs: ordinary integration tests/fixtures select hermetic; the journal transition test selects controller-journey; unclassified host tests/fixtures retain hermetic plus controller-journey; shared journey scripts select all three journeys. Mixed changes union their lanes. The shared changed-file helper includes rename sources and destinations and deleted paths, so removal does not erase an owner.

Unknown/full changes, dependencies, Docker/Compose, shared boot/runtime/auth/config and migration boundaries conservatively retain all four. Generated OpenAPI, its generators, shared client transport and native-chat/Omnigent contracts retain cross-layer coverage. Frontend paths are not blanket exclusions. Reliability journeys keep their separate owner.

- Single-user impact: settings/secrets/preset routers and services, frontend transport, worker binding, machine-authority helpers, the credential-conversion services (`api_service/services/profile_secret_migration.py`, `api_service/services/single_user_conversion.py`), and the single-user test packages (`moonmind/single_user/`, `tests/unit/single_user/`).
- The `integration-ci` job runs the existing default Compose product journeys: fresh installation, eligible in-place upgrade, and Settings Operations controller. Each uses the candidate image and real API/dashboard without a seeded person or live provider credential. The upgrade starts from an account-era release using its own Compose file and requires the guarded conversion to classify one eligible operator. Controller coverage submits, observes errors/logs, reconnects after API replacement, and retries against the real controller without a Docker daemon. All failed or unobserved outcomes fail the row.
- Fresh and upgraded candidates also admit immediate scratch work with deployment-default selections. A test-only worker controls external provider catalog observation and execution: an exact-host probe advertises one fixed supported model, while normal host attestation and model-membership checks remain intact. The worker opens the real admitted Omnigent session and runs deterministic report computation in its actual host. Production owners persist compute, archive the workspace and finalize/clean the host. The operator API downloads and verifies the generated report, then the helper removes only that verified saved workspace and downloads again. This proves composed application integration, not native model-turn execution or external free-model availability. Lost-acknowledgment redelivery must retain one execution and consistent compute evidence.
- Each deployment uses a disposable worktree, project-specific runtime volumes/networks and scoped teardown. `tools/single_user_journey_provider.py` selects fresh/upgrade CI; the cutover helper selects upgrade only; shared runner, API checker and browser changes select all three journey rows.
- Single-user integration suites select `integration_ci=true` through the `tests/integration/` prefix (reliability-owned `tests/integration/reliability/` stays excluded). Required-check aggregation needs no workflow change: the existing `integration-ci` job in `.github/workflows/pytest-unit-tests.yml` already runs whenever `integration_ci=true`, and `ci-required` already aggregates its result.

The candidate journey observes asynchronous bootstrap separately from HTTP health. Before constructing its deferred task, it reads the existing Agent Profile inventory until one active deployment default has a ready active version, bounded by the journey's 300-second timeout. It creates no profile and applies no selection override. The subsequent default admission, idempotent redelivery, workflow observations, and dashboard cancellation must still succeed. The pre-upgrade account-era release uses its existing supported request path. After candidate workers are ready, the isolated upgrade harness runs the existing controller routing convergence under its real stack lock and operation record. It observes actual worker images, preserves pinned or unknown executions, and requires a completed receipt before new work. This covers Compose upgrade plus production routing convergence, not a full updater/API operation. On a failed cutover helper, the harness retains the original error and bounded, pre-redacted one-off logs and state in `routing-cutover-diagnostics.json`, before ownership retries obscure the cause. Reads share a 30-second budget. Only that disposable helper receives a single nonterminating stack dump after 120 seconds; diagnostics never authorize overlap or make an unconfirmed cutover pass.

`tools/test_integration.sh` builds its test image unless `MOONMIND_PYTHON_TEST_IMAGE` supplies an already loadable image. It writes `artifacts/integration-ci/` on the host, also when it runs Compose from a temporary checkout copy, and exits with pytest's status. CI sets `MOONMIND_INTEGRATION_FAIL_FAST` and `MOONMIND_INTEGRATION_TIMEOUT`. The deadline covers Compose startup and pytest together, interrupts with SIGINT to preserve the report, then exits 124 (137 after the 30-second kill grace). CI's existing image layer cache and per-file xdist execution are reused. The journey and transport rows build the candidate deployable image with the same inputs as `omnigent-exact-artifact` and read that job's layer cache scope without writing it. `MOONMIND_INTEGRATION_WORKERS` controls the supported worker override. This host-side path is not a reason to expose Docker to a managed agent.

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
Real subprocess cases retain an independently owned control server during a
fatal worker timeout and debugger suppression. The image-build cache owner
materializes both SDK-selected binaries before hermetic runtime execution;
cached-startup coverage disables SDK download egress. Neither mechanism
fixes the test that caused the original worker failure.

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

`preflight-policy` remains the existing owner of repository policy checks and the other always-run static invariants (the MoonSpec projection drift gate and the shard-ownership verifier), not a reason to duplicate those checks in test rows or add tests for documentation phrasing. `test-frontend` and `check-generated-contracts` aggregate their selected jobs. The ownership verifier protects complete/disjoint selection.

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

### Deterministic Conformance Deduplication

The deterministic runner has two Python layers and one frontend target. `test_embedded_recovery.py` runs only in the second layer, which owns its recovery evidence on success or failure. Both Python layers use the same interpreter, environment, working directory and execution flags. Failed layers print redacted diagnostics in the job log and retain their full artifact logs, so identifying a failure does not require downloading artifacts. Secret-scan failures still fail the run.

No wider cross-job result reuse is implemented. Such reuse needs a complete inventory and successful owner evidence for the same head/tree/run/attempt/mode, including all required nodes. Missing, corrupt, truncated, cancelled or skipped-required results must fail reuse. Report redaction and distinct live/exact-artifact checks must remain; a similarly named test or passing job is not equivalence evidence.

### Measurement Status And Deferred Startup Work

Historical observations supplied for this optimization: main run [37839692923](https://github.com/MoonLadderStudios/MoonMind/actions/runs/37839692923) took 20m19, with 26 jobs and 88m25 summed runner time; its final four-second gate queued for 8m17. The auth-only [37790216580](https://github.com/MoonLadderStudios/MoonMind/actions/runs/37790216580) took 13m45, including six no-op backend rows. These are baseline observations, not a matched before/after benchmark. Dynamic selection deterministically removes those six allocations on a unit-only diff; no wall-clock saving is claimed before equivalent passing CI evidence exists. Ownership collection was observed at 104–136 seconds; avoided collection is not necessarily equal critical-path savings.

Historical/candidate upgrade startup observations were 272/319 seconds, with a 92-second image build. The implementation environment lacked Docker/Compose and a daemon; no startup/image benchmark was performed. Startup, readiness cadence, image distribution and upgrade semantics are unchanged. Existing independent builds already read the deployable-image layer cache; distributing one immutable build could save compute but add build/export/upload/download serialization. A worker's three-minute health start period is failure grace, not a mandatory readiness sleep.

A future matched benchmark should alternate baseline and candidate on fresh hosted runners and clean checkouts, distinguish cold/warm caches, and record build arguments, immutable image identity, acquisition/export/distribution, Compose startup, semantic readiness, journey duration and total runner duration. Record individual samples plus median/range. Do not count shifted pull time as a gain. Use the unchanged production target and verify running containers' candidate image IDs; a locally loaded image ID is not necessarily a registry digest reference.

Fresh runners matter: project names alone do not isolate explicitly named workspace/cache volumes, global network defaults, or checkout-mounted state. Each independent sample must have fresh state. Upgrade samples must preserve the historical release's own Compose deployment and saved data through guarded conversion to the exact candidate. Preserve current readiness checks, historical bounded retries, single candidate attempt, rollback/journal checks, cancellation, original errors, bounded redacted logs and cleanup. Successful partial runs and unavailable/cancelled runs are not comparable complete samples.

Required check names and aggregate runners are retained. Without a current protection/ruleset read proving compatibility, queue observations alone do not justify removing required-result linkages or changing repository protection.
