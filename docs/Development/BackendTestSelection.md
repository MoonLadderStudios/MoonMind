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
full_backend=true|false
frontend_static=true|false
frontend_browser_chromium=true|false
frontend_browser_firefox=true|false
full_frontend=true|false
```

The selector is conservative. If it cannot classify the change confidently, it selects full backend verification.

### Shared Changed-File Helper

`tools/ci/compute_changed_files.sh` is the single event-aware classifier that computes the exact changed-file list from a shallow checkout. It fetches only the exact base and head commits (`ensure_commit_available`) and emits the two-dot tree diff. The selector, deployment-safety validation, and the generated-contract detector all consume it instead of maintaining subtly different event logic. It classifies pull requests, pushes with a real non-zero base SHA, and merge groups as known change sets; manual dispatches, scheduled runs, first pushes, and missing or unavailable commits resolve to an unknown change set so every consumer stays fail-open.

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
unit-fast + unit-slow + api-component + temporal-boundary + reliability-journey + integration-ci
```

The `unit-fast` command is invariant: full runs do not switch it to the broad
unit wrapper. Ownership precedence is `slow > temporal_boundary > component >
unit_fast`. `tools/verify_test_shard_ownership.py` collects the provider-free
CI corpus and fails on missing, duplicate, or conflicting ownership.

## Required Check Model

Conditional GitHub Actions jobs are not suitable as individual branch-protection requirements because skipped jobs can leave required checks unresolved. MoonMind uses one always-running required summary job instead:

- `select-test-suites` computes backend suite outputs from a shallow, submodule-free checkout.
- `preflight-policy` runs the static repository policy checks in parallel with test selection.
- `moonspec-projection` verifies the vendored MoonSpec projection.
- `unit-fast`, `unit-slow`, `api-component`, `temporal-boundary`, `integration-ci`, and `reliability-journey-checkpoint-resume` run only when selected.
- `ci-required` always runs and fails if any always-required or selected backend job did not complete successfully.

`ci-required` is a pure result aggregator: it performs no repository operations (no checkout, no submodules, no Python/Node setup, no repository command) and has a short timeout. It evaluates every dependency and emits one annotation per failed, cancelled, timed-out, or unexpectedly skipped selected job before exiting, rather than stopping at the first failure. This keeps repository, submodule, and policy work off the serial tail of required CI.

`preflight-policy` owns the static repository guardrails — docs terminology, workflow terminology, removed-capability semantics, status-token domains, the status-token audit, the GitHub workflow display-name guard, and AgentSession deployment-safety validation. These checks start immediately alongside `select-test-suites` and are no longer duplicated in `unit-fast` or `ci-required`. In CI, deployment-safety validation consumes the exact event-derived changed-file list (`--changed-files-file`) computed by `tools/ci/compute_changed_files.sh`; local development still uses `--base-ref`.

Backend jobs use shallow, submodule-free checkouts. Only `moonspec-projection` initializes a submodule, and it initializes just `moonspec` via `git submodule update --init --depth 1 -- moonspec`. Open WebUI and Omnigent are never initialized in required backend CI.

Branch protection must require `ci-required` for backend selection, plus any separately required frontend, generated-contract, CodeQL, or repository policy checks. It must also require the standalone `migration-gate` check so migration-graph and clean-database upgrade failures block merges independently of impact selection.

Required checks must run against the current merge candidate. Prefer GitHub Merge Queue, which exercises the checked-in `merge_group` triggers before each queued merge. If Merge Queue is unavailable, require branches to be up to date with `main` before merging. A successful check from an older base revision is not authoritative: two concurrent pull requests can each have a valid migration graph while their combined result creates multiple Alembic heads.

## Main, Manual, And Scheduled Runs

Pull request CI is impact-aware. Safety paths are intentionally broader:

- Pushes to `main` run full backend verification.
- Manual dispatches run full backend verification.
- Scheduled runs run full backend verification.
- The `CI / Test Suite` workflow owns the hermetic `integration-ci` job for all
  three safety paths, so scheduled and manual runs do not need a second
  standalone integration workflow.
- Provider verification remains separate and should run only where required provider credentials are intentionally available.

## Local Commands

Run selector tests after changing path rules:

```bash
pytest tests/unit/tools/test_select_test_suites.py -q
```

Run the fast unit PR safety net:

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
  -q -n auto --dist loadfile --durations=25
```

Run Temporal boundary coverage:

```bash
pytest tests/unit/workflows/temporal \
  -m "temporal_boundary and not slow and not provider_verification and not requires_credentials" \
  -q --durations=25
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

Run the hermetic reliability journeys:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest tests/integration/reliability \
  -m reliability_journey -q --durations=25
```

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
journey. The required CI reliability job has a 30-minute budget.

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
