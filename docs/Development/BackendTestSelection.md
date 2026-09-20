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
reliability_journey=true|false
exact_artifact=true|false
omnigent_conformance=true|false
full_backend=true|false
frontend_static=true|false
frontend_browser_chromium=true|false
frontend_browser_firefox=true|false
full_frontend=true|false
```

### Shared Changed-File Helper

`tools/ci/compute_changed_files.sh` owns event-aware base/head resolution for selection, deployment validation, and generated-contract detection. It fetches exact commits as needed from a shallow checkout and emits a two-dot tree diff. Known PR, push, and merge-group changes use their actual base/head. Unavailable history, first pushes, schedules, and manual runs take the conservative unknown-change path. Do not add a second event classifier.

### Backend Detection

Backend source, tests, tooling, migrations, and sensitive generated contracts select the fast-unit safety net. Canonical prose such as `AGENTS.md`, `README.md`, and `docs/` does not itself select backend execution unless another sensitive or unknown path requires it.

Frontend selection is independent. Generated API-client changes require static validation. UI source selects static checks and Chromium. Browser tests, styles, configuration, and dependency changes can also require Firefox. Full/unknown paths select full frontend coverage. `test-frontend` aggregates selected frontend jobs even when intentional nonselection leaves both browser jobs skipped.

### Component Selection

API routers, authentication, database/service wiring, component/API tests, and generated API contracts select component coverage. Exact path predicates and their regression tests live in the selector and `tests/unit/tools/test_select_test_suites.py`, rather than a second path registry in this document.

### Temporal Boundary Selection

Workflow/runtime/worker code, Temporal-facing schemas, relevant tests, and changes to replay-visible invocation or payload behavior require Temporal-boundary coverage. Adapter changes select both Temporal and reliability coverage because adapter results cross durable workflow boundaries.

### Reliability Journey Selection

Reliability coverage is selected for orchestration and runtime seams, checkpoints, skill contracts and their materialization, replay fixtures, and managed runtime packaging. Shared CI, dependency, selector, or global test-configuration changes take the full-backend path.

These journeys test retained user outcomes through real production composition. Scripted external providers remove network/credential dependence, not the boundary under test.

### Backend Matrix Ownership And Failure Propagation

The current `backend-matrix` contains unit-fast, api-component, temporal-boundary, and four reliability rows. The matrix job is skipped when no primary backend suite is selected. Otherwise its fixed rows use the selector outputs to guard suite-specific setup and test steps. An unselected row doing no pytest work is intentional nonselection, not proof that its tests passed.

Native `strategy.fail-fast` is enabled outside scheduled diagnostics. It can cancel siblings inside this matrix after a failure. It does not cancel unrelated frontend, integration, image, or migration jobs. Scheduled diagnostics keep collecting sibling outcomes within their execution bounds. Superseded-run cancellation remains a separate existing Actions concurrency behavior.

Fast rows retain their suite-specific xdist execution. Unit-fast and Temporal use per-file distribution, while API/component uses per-test distribution. Reliability runs serial pytest within each isolated runner, using its own Compose project, network, database, Temporal, and object-store state. Do not combine matrix fan-out with a new unbounded inner worker pool or shared mutable fixture stack.

The workflow initializes only submodules a selected job needs. In the reviewed implementation, unit-fast initializes MoonSpec and Omnigent fixtures and API/component initializes Omnigent. Do not assume that only the projection job needs a submodule, or initialize every submodule for every row.

### Execution Budgets

The workflow is the authority for effective limits. At the September 20, 2026 review baseline `557d4781f58577c12a2784929953286abff3b925`, primary execution has these bounds:

| Lane | Per-test timeout | Test-step bound | Job bound |
| --- | --- | --- | --- |
| Unit-fast, API/component, Temporal | 600 seconds | No separate native test-step timeout | 30 minutes |
| Ordinary reliability shard | 150 seconds | 600-second shell deadline inside a 12-minute Actions step | 30 minutes |
| Scheduled reliability shard | 300 seconds | 660-second shell deadline inside a 12-minute Actions step | 30 minutes |

This is a code snapshot, not approval of those oversized fast-lane/job limits. The revised #4369 owns the remaining reductions. Its proposed shorter limits must fit measured healthy setup, tests, and bounded cleanup. Do not turn proposed values into claims that they already run, or apply a shorter deadline than the selected healthy corpus can satisfy.

Use the installed pytest timeout mechanism for stuck tests and native Actions step/job bounds for the outer process. Keep an existing shell deadline only where it provides a distinct useful bound. A cooperative session timeout and a diagnostic stack dump do not replace a hard process bound. No additional timeout framework, test retry loop, watchdog, or cancellation service is needed.

Pytest fail-fast stops one invocation. Native matrix fail-fast stops its siblings. The reviewed pytest invocations do not yet set `--maxfail`; finishing that behavior belongs to #4369 rather than another matrix consolidation.

### Reliability Sharding

The collected reliability universe is split into four groups through pytest-split:

```text
--splits 4 --group N --splitting-algorithm least_duration
--durations-path tests/.reliability-test-durations.json
```

`N` is 1 through 4. All shards use the same checkout, collection inputs, and advisory hints. `tools/ci/refresh_reliability_durations.py --validate-only` checks hint usability. Missing or unusable history warns and falls back consistently; it must not exclude new tests or make an otherwise correct test fail. The ownership verifier checks the actual plugin collections for complete, disjoint coverage.

Refresh hints when a measured imbalance or changed corpus warrants it. Per-run timing artifacts are evidence, not a second correctness database or a requirement for automated timing commits. Do not rebuild sharding that is already present.

### Reliability Docker Fixture Layers

The Compose dependency file uses registry images. That does **not** mean the test corpus builds no local images: `test_automatic_release_availability.py` at the reviewed baseline generates a Python/Temporal Dockerfile and builds case-specific images. Conversely, the presence of those builds does not prove that dependency layers are repeatedly rebuilt. Native Docker caching may already reuse them.

First check whether the fixture survives the authorized deployment simplification. Do not build a cache for retired release machinery or restore a deleted fixture to satisfy an old optimization issue. For surviving expensive builds, inspect actual cold/warm build output and separate build cost from routing waits and startup. Prefer native layer caching; add only a small immutable dependency fixture if a material remaining cost is demonstrated.

Mutable release records, images under test, containers, volumes, queues, and workspaces remain case/shard-owned. A cached base must not make an intentionally removed case image appear restored. Cold execution must remain correct, and cleanup must not remove another case's resources or use global Docker prune. The existing integration/exact-artifact build caches remain separate from this measurement question.

### Logs And Diagnostic Evidence

The existing workflow streams combined pytest output through `tee`, captures the pytest exit code, and uses `tools/ci/write_backend_matrix_summary.py` with logs, actual JUnit reports, and timing artifacts. Extend that path rather than introducing another reporter. Continue relevant work in the existing #4371 implementation PR.

Keep normal success evidence small: tested revision, suite/shard and attempt identity, real output, any JUnit report, and useful slow-case timings. Existing artifacts use finite retention and distinct shard/attempt names. Richer service diagnostics belong on the failure path where useful, from known test-owned locations with redaction. Do not collect whole environments, source trees, tokens, or unrelated host files.

Missing or partial JUnit is unavailable/incomplete evidence, never zero tests or a pass. Preserve the original failure through logging, report generation, and cleanup. Bound secondary operations separately so a hung log command cannot consume the rest of the job. Matrix cancellation is best effort for artifact collection, and a hard job kill or runner loss can prevent final uploads entirely.

Validate interruption with a real disposable subprocess through the production reporting path. Fabricating post-kill artifacts proves a parser can read them, not that a killed test leaves them. No new universal fault-injection framework or documentation-wording tests are required.

### Reproducible Before/After Comparison

Use existing CI evidence before commissioning another run. Compare equivalent selected coverage and runner resources, distinguish cold/warm setup and queue delay, and report both longest-row latency and summed runner execution. A failed/canceled partial corpus is not equivalent to a complete passing run.

If the change removes an authorized obsolete capability, identify the retired work and the retained replacement outcomes. Do not advertise that as equivalent coverage of the deleted mechanism. Do not require a new monitoring service, exact timing baseline, or performance gate to make a small improvement. An evidence-backed no-change disposition is valid when an optimization would add more complexity than benefit.

### Hermetic Integration CI Selection

Compose, Docker/runtime infrastructure, database and migration changes, integration tests, and their runner/dependencies select the existing credential-free integration path. Reliability journeys keep their separate owner.

`tools/test_integration.sh` builds its test image unless `MOONMIND_PYTHON_TEST_IMAGE` supplies an already loadable image. CI's existing image layer cache and per-file xdist execution are reused. `MOONMIND_INTEGRATION_WORKERS` controls the supported worker override. This host-side path is not a reason to expose Docker to a managed agent.

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

`preflight-policy` remains the existing owner of repository policy checks, not a reason to duplicate those checks in test rows or add tests for documentation phrasing. `test-frontend` and `check-generated-contracts` aggregate their selected jobs. The ownership verifier protects complete/disjoint selection.

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

`python3 tools/ci/refresh_reliability_durations.py` refreshes the advisory collection-based hints when needed. `python tools/verify_test_shard_ownership.py` checks the eligible provider-free universe in its supported environment. These are not broad local prerequisites to every PR.

The source-destroying checkpoint-resume journey still exercises durable capture/restore and idempotent recovery. It does not by itself prove the entire Temporal-to-managed-runtime journey. Use the appropriate owning integration boundary rather than treating one helper test as complete product verification.

## Maintaining The Selector

Keep path rules and their focused behavior tests together. Use conservative selection when uncertain. Selector changes require full CI because incorrect classification can silently omit tests.

During an authorized architecture removal, update obsolete test ownership, fixture setup, and duration hints with the retired code. Preserve real coverage of supported default journeys, data integrity, and the active-work transition. Do not preserve every historical class or versioning parameter solely because an older issue listed it. Explain moved/retired coverage briefly in the PR instead of building a permanent registry or approval mechanism.
