# Backend Test Selection Strategy

## Purpose

MoonMind uses impact-aware backend test selection to keep pull request feedback fast without weakening coverage for risky changes. The strategy is:

```text
Run the cheap, broad safety net on backend pull requests.
Run expensive specialized suites only when changed files can affect them.
Run full backend verification for risky or uncertain cases.
Fail open when classification is incomplete or ambiguous.
```

This document describes the intended steady-state behavior. It is not a rollout checklist.

## Test Categories

Backend tests are classified by the runtime resources they start, not by the implementation code they exercise.

| Category | Marker | Resource boundary | PR behavior |
| --- | --- | --- | --- |
| Fast unit | `unit_fast` | Pure Python logic, schemas, validation, and services with mocks. No Docker, network, external process, or Temporal test server. | Required for backend-impacting pull requests. |
| Slow unit | `slow` | Remaining slow tests under `tests/unit`, with precedence over component and Temporal ownership. | Runs on main pushes, schedules, manual/full runs, fail-open runs, and direct changes to known slow tests. |
| Component | `component` | FastAPI `TestClient`, dependency overrides, and in-process router/service wiring. | Required for API, auth, database, service, and generated OpenAPI type changes. |
| Temporal boundary | `temporal_boundary` | Temporal `WorkflowEnvironment`, `Worker`, `Replayer`, workflow signal/update/query/replay, activity-boundary, and serialized payload behavior. | Required for Temporal workflow, runtime, worker, or Temporal schema-sensitive changes. |
| Reliability journey | `reliability_journey` | Hermetic production composition across the Temporal test server, real workflows, managed-session/runtime adapters, scripted provider or subprocess behavior, terminal artifacts, and checkpoint/finalization routing. No external network or credentials. | Required for orchestration seams, managed runtime packaging, skill contracts, checkpoints, and replay fixtures. |
| Slow | `slow` | Valuable tests that are too expensive or too environment-sensitive for the default PR fast path. | Excluded from the default PR fast path; run manually, nightly, or by explicit target. |
| Hermetic integration CI | `integration` plus `integration_ci` | Docker Compose-backed tests using local dependencies only. No external credentials. | Required for Docker, compose, database, migration, integration-test, and runtime infrastructure changes. |
| Provider verification | `provider_verification` plus provider-specific markers | Live external-provider tests requiring real credentials. | Outside required PR CI; run manually or in credentialed scheduled environments. |

The pytest marker registry lives in `pyproject.toml`. Runtime classification for existing tests is centralized in `tests/conftest.py`; explicit markers are still preferred when a test has a clear resource boundary.

## Selector Contract

`tools/select_test_suites.py` reads changed file paths from stdin and emits GitHub Actions-compatible outputs:

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

The selector is conservative. If it cannot classify the change confidently, it selects full backend verification.

### Shared Changed-File Helper

`tools/ci/compute_changed_files.sh` is the single event-aware classifier that computes the exact changed-file list from a shallow checkout. It fetches only the exact base and head commits (`ensure_commit_available`) and emits the two-dot tree diff. The selector, deployment validation, and the generated-contract detector all consume it instead of maintaining subtly different event logic. It classifies pull requests, pushes with a real non-zero base SHA, and merge groups as known change sets; manual dispatches, scheduled runs, first pushes, and missing or unavailable commits resolve to an unknown change set so every consumer stays fail-open.

### Backend Detection

A pull request is backend-impacting when it touches backend source, backend tests, backend tooling, migrations, or workflow-sensitive generated contracts. Backend-impacting pull requests select `unit_fast=true` unless the selector forces the full backend path, which also includes fast unit coverage.

Canonical guidance (`AGENTS.md`, `README.md`, and files under `docs/`) and
frontend-only changes do not select backend suites unless they touch a
backend-sensitive generated contract or another fail-open path.

Frontend selection is independent. Generated OpenAPI client changes select static validation only; ordinary UI source selects static validation plus Chromium; browser tests, styles, browser configuration, and npm dependency changes also select Firefox. Pushes to `main`, schedules, manual runs, unavailable change sets, and unknown paths select the full backend and frontend paths. The stable `test-frontend` job always runs as a result aggregator even when both frontend runner jobs are intentionally skipped.

### Component Selection

The selector enables `api_component=true` for changes under or matching:

- `api_service/api/`
- `api_service/auth*`
- `api_service/auth_providers.py`
- `api_service/db/`
- `api_service/services/`
- `tests/unit/api/`
- `tests/unit/api_service/`
- `tests/component/api/`
- `tools/export_openapi.py`
- `tools/generate_openapi_types.py`
- `frontend/src/generated/openapi.ts`

Component tests are intended to catch in-process API, router, auth, database, and service wiring regressions without starting Docker or a live Temporal server.

### Temporal Boundary Selection

The selector enables `temporal_boundary=true` for changes under or matching:

- `moonmind/workflows/temporal/`
- `moonmind/schemas/managed_session_models.py`
- `moonmind/schemas/*workflow*`
- `moonmind/schemas/*temporal*`
- `api_service/worker*`
- `tests/unit/workflows/temporal/`
- `tests/integration/workflows/temporal/`

Temporal boundary tests are mandatory for changes to workflow code, activity invocation shapes, signal/update/query names, replay-visible behavior, status normalization, serialized payloads, managed-session schemas, or adapter-to-workflow contracts.

Changes under `moonmind/workflows/adapters/` select both Temporal boundary and reliability journey coverage because adapter results and metadata are workflow-visible contracts.

### Reliability Journey Selection

The selector enables `reliability_journey=true` for changes to:

- `moonmind/workflows/adapters/` and `moonmind/workflows/temporal/`, including checkpoint policy, activity catalog, and worker routing
- `moonmind/schemas/agent_runtime_models.py`, `moonmind/schemas/managed_session_models.py`, `moonmind/schemas/temporal_models.py`, and checkpoint schemas
- `.agents/skills/` and their orchestration tools
- `tests/integration/reliability/` replay fixtures and scripted runtime helpers
- managed-agent runtime Dockerfiles, image build/install files under `api_service/docker/`, and runtime images under `docker/`
- CI workflows, selector/test runners, dependency locks, and global pytest configuration through the full-backend fail-open path

This is a separate resource boundary from ordinary unit and Compose integration coverage. It runs a small deterministic journey corpus through real production orchestration layers while replacing external providers and networks with scripted local counterparts.

### Backend Matrix Ownership And Failure Propagation (MoonLadderStudios/MoonMind#4377)

The eligible primary backend executions — `unit-fast`, `api-component`,
`temporal-boundary`, and four deterministic reliability shards
(`reliability-shard-1` through `reliability-shard-4`) — share one native
GitHub Actions matrix job named `backend-matrix` in
`.github/workflows/pytest-unit-tests.yml`. This shared matrix boundary is
what allows a failure in a Temporal/API/unit entry to cancel running or
queued reliability siblings through native `strategy.fail-fast` behavior.
No custom API cancellation loop, privileged Actions write token, external
cancellation bot, or polling workflow is used.

Decision evidence: run #14404 showed `temporal-boundary` failing near the
three-minute mark while the then-independent reliability job continued to
minute 21 — roughly 18 minutes of executor time spent after the first
definitive required-backend failure (excluding queued time and
user-canceled obsolete runs). That waste justified consolidation; the
matrix keeps successful-run critical-path time flat by isolating setup
and mutable resources per row (see below) rather than adding a serialized
environment-build prerequisite.

Matrix rows are derived from the existing `tools/select_test_suites.py`
outputs with a small explicit mapping: `unit_fast` selects the `unit-fast`
row, `api_component` selects `api-component`, `temporal_boundary` selects
`temporal-boundary`, and `reliability_journey` selects all four reliability
shards. There is no second change-impact classifier, universal runner
framework, or shell snippet derived from untrusted issue/branch text —
only fixed trusted pytest commands with ordinary quoted parameters.

- `strategy.fail-fast` is `${{ github.event_name != 'schedule' }}`: enabled
  for pull-request and merge-group validation (plus push/manual runs that
  share the same validation path) so a failed Temporal/API/unit entry
  cancels pending/running reliability siblings; disabled (`false`) for
  scheduled diagnostics so nightly runs keep collecting sibling outcomes.
- Fast rows preserve existing xdist behavior (`-n auto`, `--dist load` for
  the API shard and `--dist loadfile` for unit-fast/temporal) and never
  build images or start Compose services. Reliability rows run serial
  pytest (no `-n`) inside their own isolated Compose
  PostgreSQL/Temporal/MinIO under per-shard Docker project names
  (`moonmind-reliability-reliability-shard-N`). The setup step exports
  `MOONMIND_TEST_DOCKER_NETWORK=moonmind-reliability-<suite>_default` so
  fixture tests attach to their row's isolated Compose network instead of
  the retired single-job `moonmind-reliability-qualification_default`.
- Per-test (`--timeout 600` on fast rows, `--timeout 420` on reliability
  rows), test-step (reliability pytest wrapped in `timeout 660s` for an
  11-minute ceiling above the 510-525s LPT partition with an explicit
  budget-exceeded annotation), job
  (`timeout-minutes: 20`), and cleanup (`always()` compose `down -v`,
  wrapped in `timeout 100s`) bounds are preserved on every row. Reliability
  collection steps are additionally wrapped in `timeout 100s`/`timeout 60s`
  so one slow diagnostic command cannot stall the row; each command
  records its own failure to `collection-status.txt` without stopping the
  remaining bounded collection or cleanup, and the original test failure
  is never replaced.
- Each row streams combined stdout/stderr through
  `2>&1 | tee artifacts/pytest-backend-<suite>.log` with
  `PYTHONUNBUFFERED=1` and `set -euo pipefail` (plus `PIPESTATUS`
  capture), so live Actions logs stay complete while a local text log is
  retained without buffering the whole output in memory. Each row keeps its
  JUnit report plus derived `artifacts/pytest-backend-<suite>-slowest.txt`
  and `artifacts/pytest-backend-<suite>-durations.json` (written by the
  small `tools/ci/write_backend_matrix_summary.py` hook from standard
  pytest/JUnit output). Reliability rows run
  `-vv --tb=short --durations=25` so the active node ID is visible before a
  stall; other lanes keep lower-noise console verbosity with duration
  output. A missing final JUnit file is reported as unavailable/interrupted
  in the per-job `$GITHUB_STEP_SUMMARY`, never as zero tests or a pass.
- Each row uploads a stable suite/shard/run-attempt artifact
  (`pytest-<suite>-attempt-<attempt>`) containing only the known diagnostic
  files (JUnit XML, text log, slowest report, duration-hints snapshot) with
  `retention-days: 7` and `if-no-files-found: warn`, on success, failure,
  and (best-effort) normal cancellation via `always()` plus the native
  selection guard. Reliability Compose logs and scoped manifests upload
  separately with the same retention. No hidden environment files, tokens,
  unrestricted workspaces, or whole source trees are staged.
- Each row appends a per-job `$GITHUB_STEP_SUMMARY` (via the same hook)
  with suite/shard identity, tested revision, run/attempt, JUnit counts
  (never progress-% parsing), outcome (`passed`, `failed`, `canceled`,
  `intentionally unselected`, or `unavailable`), measured test-step wall
  time plus JUnit suite time, top slowest cases, and evidence paths. The
  hook always exits 0 so a parsing problem never hides an unsuccessful job
  or alters selection.
- Duration hints for #4366 maintenance are the per-shard
  `pytest-backend-<suite>-durations.json` snapshots, uploaded as separate
  artifacts. The committed partition input stays immutable during a matrix
  run: rows never overwrite a shared baseline, and a partial failed-shard
  result never replaces a complete baseline.
- Scope: unrelated `unit-slow`, `integration-ci`, exact-artifact,
  frontend, generated-contract, and `migration-gate` jobs remain
  independently enforced. `ci-required` consumes only the `backend-matrix`
  aggregate `result` (never last-writer matrix outputs); a failed,
  timed-out, or canceled selected row fails the aggregate, and an empty
  backend selection skips the matrix intentionally without instantiating
  tests or hiding selector errors.

Reliability sharding is deterministic and duration-balanced
(MoonLadderStudios/MoonMind#4367): files matching
`tests/integration/reliability/test_*.py` are assigned with a greedy
longest-processing-time partition over the timing hints in
`tools/ci/reliability_shard_timings.json`. The CI workflow implements this
with `python3 tools/ci/partition_reliability_shards.py --shard <N>`;
`tools/verify_test_shard_ownership.py` enforces the same partition through
the shared `tools/ci/reliability_shard_partition.py` module, so local
ownership checks and CI execute each file in the same shard. Timing hints
are an optimization hint only: new, renamed, or stale entries fall back to
the default weight and every file is still selected exactly once. Refresh
the hints from recent per-shard `pytest-backend-<suite>-durations.json`
snapshots; never add exact test-count, timing-file freshness, test-filename,
or preferred-wording gates.

Diagnostic limitation: a canceled sibling may exit before writing its
junit report or Compose logs. Cancellation uploads are best-effort
(`||` fallbacks, `if-no-files-found: warn/error` per artifact) and the
original failure remains visible in the failed entry plus the
`ci-required` aggregate — `ci-required` can never turn green because other
entries were canceled or skipped. Runner disappearance and a hard job kill
may prevent final uploads entirely; live Actions output (streamed via
`tee`) remains the primary record in that case rather than a guaranteed
final artifact.

### Reproducible Before/After Comparison (MoonLadderStudios/MoonMind#4370)

No automatic scheduled monitoring workflow is added. To compare a change
against the pre-evidence baseline (failure-only diagnostics, no
success-path text log/JUnit upload, `-q` reliability verbosity):

1. Fix the selected universe: run with the same selector outputs (same
   `unit_fast`/`api_component`/`temporal_boundary`/`reliability_journey`
   selection, same reliability file set from
   `python3 tools/ci/partition_reliability_shards.py --shard <N>`).
2. Fix the revision/configuration: compare runs on the same commit (or
   adjacent commits with no test/workflow changes), same workflow file,
   same per-row `--timeout` / `timeout 660s` / `timeout-minutes: 20`
   bounds, same runner class (`ubuntu-latest`).
3. Repeat each side at least twice to separate ordinary timing noise from a
   real shift; do not add a performance gate on the result.
4. Separate cold and warm setup: record dependency-install/Compose-pull
   time apart from pytest execution (cold = cache miss / fresh Compose
   pull, warm = cache hit). Record queue time separately from execution
   time using the run's `created_at`/`started_at` timestamps.
5. Compare longest shard versus summed runner time: the critical path is
   the slowest `backend-matrix` row (JUnit suite time plus its step summary
   wall time); the cost is the sum over rows. The #4366 duration-hints
   snapshots (`pytest-backend-<suite>-durations.json`) and the per-row
   slowest reports supply both without rerunning the suite.
6. A subsequent successful-but-slow run is diagnosed from its retained
   `pytest-backend-<suite>.log`, JUnit XML, slowest report, and step
   summary alone.

### Hermetic Integration CI Selection

The selector enables `integration_ci=true` for changes under or matching:

- `docker-compose.test.yaml`
- `api_service/Dockerfile`
- `.env-template`
- `tests/integration/`
- `tools/test_integration.sh`
- `api_service/db/`
- `api_service/migrations/`
- `migrations/`
- `alembic/`
- `pyproject.toml`
- `uv.lock`

This suite validates compose-backed local infrastructure seams and must remain free of external-provider credentials.
Tests under `tests/integration/reliability/` are explicitly excluded because
the reliability journey shard owns them.

`tools/test_integration.sh` builds the compose `pytest` image unless
`MOONMIND_PYTHON_TEST_IMAGE` names a caller-supplied image, in which case the
image must already be loadable locally. CI prebuilds that image with a GitHub
Actions layer cache so dependency layers are not rebuilt on every run. The suite
runs under xdist with per-file distribution; `MOONMIND_INTEGRATION_WORKERS`
overrides the default worker count.

### Omnigent Conformance Selection

The selector enables `omnigent_conformance=true` for Omnigent-owned paths (the
same inventory that elevates the complete Omnigent contract gate), for the
runner's own evidence inputs listed in `OMNIGENT_CONFORMANCE_INPUT_EXACT` (its
pytest layers, frontend test, profile fixture, and report builder), and for every
full-verification event. `ci-required` aggregates the selected job's result. The deterministic conformance runner republishes the
Omnigent evidence bundle from layers the exclusive shards already execute, so an
unrelated change does not pay for it.

## Full Backend Path

The selector enables `full_backend=true` and selects all backend suites when any of the following is true:

- Changed files cannot be determined.
- Changed-file input is empty.
- A changed path is unknown to the selector.
- The event is a push to `main`.
- The event is `workflow_dispatch`.
- The event is `schedule`.
- CI workflow files changed under `.github/workflows/`.
- Dependency files changed, including `pyproject.toml`, `uv.lock`, or `poetry.lock`.
- Test runner or selector files changed, including `tools/test_unit.sh`, `tools/test_unit_docker.sh`, `tools/test_integration.sh`, or `tools/select_test_suites.py`.
- Global pytest configuration changed, including `tests/conftest.py` or `tests/unit/conftest.py`.

The full backend path selects the same exclusive shards used by targeted runs:

```bash
unit-fast + unit-slow + api-component + temporal-boundary + reliability-journey (4 shards) + integration-ci
```

In CI the `reliability-journey` selection fans out to all four
`backend-matrix` reliability shards; locally the single corpus command
above covers the same files.

The `unit-fast` command is invariant: full runs do not switch it to the broad
unit wrapper. Ownership precedence is `slow > temporal_boundary > component >
unit_fast`. `tools/verify_test_shard_ownership.py` collects the provider-free
CI corpus and fails on missing, duplicate, or conflicting ownership.

## Required Check Model

Conditional GitHub Actions jobs are not suitable as individual branch-protection requirements because skipped jobs can leave required checks unresolved. MoonMind uses one always-running required summary job instead:

- `select-test-suites` computes backend suite outputs from a shallow, submodule-free checkout.
- `preflight-policy` runs the static repository policy checks in parallel with test selection.
- `moonspec-projection` verifies the vendored MoonSpec projection.
- `backend-matrix` (unit-fast, api-component, temporal-boundary, four reliability shards), `unit-slow`, `integration-ci`, `omnigent-exact-artifact`, and `omnigent-deterministic-conformance` run only when selected.
- `test-frontend` and `check-generated-contracts` always run as result aggregators for the selected frontend and generated-contract jobs.
- `verify-test-shard-ownership` always runs because exclusive shard ownership is a static repository invariant.
- `ci-required` always runs and fails if any always-required or selected backend job, the `test-frontend` aggregator, the `check-generated-contracts` aggregator, or the shard-ownership verifier did not complete successfully.

`ci-required` is a pure result aggregator: it performs no repository operations (no checkout, no submodules, no Python/Node setup, no repository command) and has a short timeout. It evaluates every dependency and emits one annotation per failed, cancelled, timed-out, or unexpectedly skipped selected job before exiting, rather than stopping at the first failure. For the consolidated backend suites it consumes only the `backend-matrix` aggregate result (selected when any of `unit_fast`, `api_component`, `temporal_boundary`, or `reliability_journey` is true, otherwise expecting `skipped`); per-row completion is never inferred from last-writer matrix outputs. This keeps repository, submodule, and policy work off the serial tail of required CI.

`preflight-policy` owns the static repository guardrails — docs terminology, workflow terminology, removed-capability semantics, status-token domains, the status-token audit, the GitHub workflow display-name guard, and AgentSession deployment validation. These checks start immediately alongside `select-test-suites` and are no longer duplicated in `unit-fast` or `ci-required`. In CI, deployment validation consumes the exact event-derived changed-file list (`--changed-files-file`) computed by `tools/ci/compute_changed_files.sh`; local development still uses `--base-ref`.

Backend jobs use shallow, submodule-free checkouts. Only `moonspec-projection` initializes a submodule, and it initializes just `moonspec` via `git submodule update --init --depth 1 -- moonspec`. Open WebUI and Omnigent are never initialized in required backend CI.

Branch protection must require `ci-required` as the single required context for backend, frontend, and generated-contract gates. `test-frontend` and `check-generated-contracts` report into `ci-required`, so they must not be listed as separate required contexts; a separately required aggregator can leave merges blocked after a check rename or removal. CodeQL and other repository policy checks that run outside this workflow remain separately required. Branch protection must also require the standalone `migration-gate` check so migration-graph and clean-database upgrade failures block merges independently of impact selection.

Required checks must run against the current merge candidate. Prefer GitHub Merge Queue, which exercises the checked-in `merge_group` triggers before each queued merge. If Merge Queue is unavailable, require branches to be up to date with `main` before merging. A successful check from an older base revision is not authoritative: two concurrent pull requests can each have a valid migration graph while their combined result creates multiple Alembic heads.

## Main, Manual, And Scheduled Runs

Pull request CI is impact-aware. Full-verification paths are intentionally broader:

- Pushes to `main` run full backend verification.
- Manual dispatches run full backend verification.
- Scheduled runs run full backend verification.
- The `CI / Test Suite` workflow owns the hermetic `integration-ci` job for all
  three full-verification paths, so scheduled and manual runs do not need a second
  standalone integration workflow.
- Provider verification remains separate and should run only where required provider credentials are intentionally available.

## Local Commands

Run selector tests after changing path rules:

```bash
pytest tests/unit/tools/test_select_test_suites.py -q
```

Run the fast unit PR regression suite:

```bash
pytest tests/unit \
  --ignore=tests/unit/workflows/temporal \
  --ignore=tests/unit/api \
  --ignore=tests/unit/api_service \
  -m "unit_fast and not provider_verification and not requires_credentials" \
  -q -n auto --dist loadfile --durations=25
```

Run component coverage:

```bash
pytest tests/unit/api tests/unit/api_service tests/component/api \
  -m "component and not temporal_boundary and not slow and not provider_verification and not requires_credentials" \
  -q -n auto --dist load --durations=25
```

Component tests distribute per test rather than per file because a few router
modules hold several hundred tests each. `tests/unit/api/conftest.py` fails
Compose-only hostname lookups immediately (the settings-backed S3 artifact store
and the API Postgres engine) so un-overridden request dependencies do not spend
seconds per request in DNS and client retry backoff.

Run Temporal boundary coverage:

```bash
pytest tests/unit/workflows/temporal \
  -m "temporal_boundary and not slow and not provider_verification and not requires_credentials" \
  -q -n auto --dist loadfile --durations=25
```

Run slow unit coverage without xdist:

```bash
pytest tests/unit \
  -m "slow and not provider_verification and not requires_credentials and not integration" \
  -q --durations=50
```

Run hermetic integration CI:

```bash
./tools/test_integration.sh
```

Run the hermetic reliability journeys (all shards):

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest tests/integration/reliability \
  -m reliability_journey -q --durations=25
```

Run one duration-balanced reliability shard locally (mirrors the CI
matrix partition; list a shard's files first, then run them):

```bash
python3 tools/ci/partition_reliability_shards.py --shard 0
mapfile -t shard_files < <(python3 tools/ci/partition_reliability_shards.py --shard 0)
MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest "${shard_files[@]}" \
  -m reliability_journey -q --durations=25
```

Shard indexes `0`–`3` map to `reliability-shard-1`–`reliability-shard-4`.
`tools/verify_test_shard_ownership.py` assigns each file to the same shard
via `reliability_shard_for_path()`.

Run the checkpoint archive cold-resume replay directly:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest \
  tests/integration/reliability/test_escaped_failure_journeys.py \
  -k source_destroying_cold_resume -q
```

Verify checkpoint/runtime selector coverage with:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh \
  tests/unit/tools/test_select_test_suites.py --python-only
```

The archive replay deliberately destroys the source workspace before using
durable artifact evidence to restore a distinct destination and retries the
restore idempotently. It exercises production capture/restore engines and the
artifact boundary, but does not substitute for the Temporal-to-managed-AgentRun
journey. The required CI reliability shards each carry an 11-minute test-step
budget (above the 510-525s LPT partition) inside a 20-minute job ceiling.

Verify that every eligible provider-free node has exactly one owner:

```bash
python tools/verify_test_shard_ownership.py
```

## Maintaining The Selector

When adding a new backend subsystem, test category, or high-risk path:

1. Add the path rule to `tools/select_test_suites.py`.
2. Add selector unit coverage in `tests/unit/tools/test_select_test_suites.py`.
3. Update this document when the intended strategy changes.
4. Keep test classification resource-based.
5. Prefer over-selection to under-selection.

Selector changes must force full backend verification, because a broken selector can silently skip the wrong suites.
