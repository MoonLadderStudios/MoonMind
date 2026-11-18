# Pre-Commit Workflow

## Overview

MoonMind uses targeted local tests and inexpensive static checks for development feedback, and prefers GitHub Actions CI for broader verification. `pre-commit` provides the fast local formatting and lint guardrail. Broad local suites are not a prerequisite for opening or updating a PR, and should not routinely duplicate suites that CI will run.

The current hook set is defined in `.pre-commit-config.yaml` and runs:

- `black`
- `isort --profile=black`
- `ruff --fix`

Frontend build output and generated API types are validated separately. `pre-commit` does not run the asset pipeline because the repository wrappers invoke `pre-commit run --all-files`, and forcing full Vite/OpenAPI regeneration on every wrapper-driven test pass would add unnecessary latency. Prefer the selected CI frontend jobs for broad validation. `npm run frontend:ci` remains available for deliberate local reproduction, not as a default pre-PR requirement. Use `npm run generate` when checked-in generated frontend API types need updating. The selected CI generated-contract check verifies they are in sync, and `npm run contracts:check` is available for focused diagnosis. The canonical path detector used by CI lives at `tools/check_openapi_affecting_changes.sh`.

## Current Project Behavior

### PowerShell Test Wrappers

The Windows-oriented PowerShell wrappers still run `pre-commit` before they start their test flow:

- `tools/test-unit.ps1`
- `tools/test-integration.ps1`

Each script starts with:

```powershell
pre-commit run --all-files
```

If that command fails, the script exits before building containers or running tests.

### Canonical Unit Test Path

The repository unit-test wrapper for local development and reproduction is:

```bash
./tools/test_unit.sh
```

Use explicit Python paths or node IDs with `--python-only`, or a frontend path with `--dashboard-only --ui-args`, for targeted iteration. A no-argument invocation runs the broad default suites and is not the recommended pre-PR step. CI owns broad verification through its selected jobs and commands. The wrapper does **not** invoke `pre-commit` itself, so run relevant hooks separately outside the PowerShell wrappers.

In a human WSL session, the wrapper may delegate to `./tools/test_unit_docker.sh` when the local Python or Node toolchain is unavailable, unless `MOONMIND_FORCE_LOCAL_TESTS=1` is set. Container sessions are not redirected. See the WSL troubleshooting note before using the fallback for a targeted run.

### CI Behavior

The GitHub unit-test workflow uses impact-aware backend suite selection for pull requests. A `select-test-suites` job runs `tools/select_test_suites.py` against the changed files and emits suite decisions for:

- `unit_fast`
- `unit_slow`
- `api_component`
- `temporal_boundary`
- `integration_ci`
- `reliability_journey`
- `full_backend`

Branch protection should require the always-running `ci-required` summary job as the single required context for the backend, frontend, and generated-contract jobs, plus the standalone `migration-gate` check. Do not list those jobs as separate required contexts; each reports directly into `ci-required`, so no result-only aggregator runner sits between them. `ci-required` is a pure result aggregator with no checkout, submodule, setup, or repository command; it validates the complete selector/matrix contract, evaluates every dependency and reports each failed, cancelled, timed-out, or unexpectedly skipped selected job before exiting. Existing required check names and result linkages are unchanged. `migration-gate` independently blocks migration-graph and clean-database upgrade failures.

Static repository policy checks run in a parallel `preflight-policy` job that starts immediately alongside `select-test-suites`, rather than on the serial tail of `ci-required`. It owns docs and workflow terminology guardrails, removed-capability semantics, the status-domain audit, the GitHub workflow display-name guard, the exclusive shard-ownership verifier, and the vendored MoonSpec projection check, so those checks are not duplicated in `unit-fast`, whose scanner tests use fixtures instead of rescanning the repository, or spread across separate runners. Backend jobs use shallow checkouts and initialize the submodules required by their selected suite. `select-test-suites` computes the exact changed-file list from the event's base and head commits once with `tools/ci/compute_changed_files.sh` and feeds both suite selection and generated-contract detection from it.

Routine backend pull requests select the cheap unit regression suite. The primary backend matrix instantiates only selected unit, API/component, Temporal-boundary, and reliability-shard rows; reliability selection includes all four partitions. API/router/auth/db/service changes also run the component suite. Temporal workflow, runtime, activity-boundary, signal/update, replay, or Temporal schema changes run the Temporal boundary suite. Changes under workflow adapters, Temporal workflows, checked-in `.agents/skills` bundles, Docker runtime paths, checkpoint schemas, and reliability replay fixtures run the hermetic reliability journey suite. Docker, integration, database, compose, migration, dependency, or test-runner changes run hermetic `integration_ci` as needed. Selection starts two isolated, duration-balanced hermetic Compose shards independently of the fresh, upgrade, and controller/journal matrix. A required aggregate verifies their complete, nonoverlapping corpus and combined security-quality evidence; shard 1 alone owns proxy, scoped-egress, and storage restart qualification. Installed consumers share one immutable application image; recovery-image preparation starts alongside source checks. Shared boundaries and unknown inputs retain all four parallel rows. See [Backend Test Selection](BackendTestSelection.md) for the conservative dependency map, failure contract and measurement limits.

The Temporal boundary and reliability jobs prepare both SDK test-server binaries with the shared cache helper before parallel pytest workers start, so bounded retries handle download rate limits and incomplete archives outside individual test timeouts.

The selector fails open. Empty changed-file input, unknown paths, CI workflow changes, dependency file changes, test-runner changes, pytest configuration changes, selector changes, pushes to `main`, scheduled runs, and manual dispatches all select full verification. The release workflow owns main-push source checks alongside image builds, starts installed-image qualification when assembly finishes and host recovery once recovery preparation also finishes, and requires source and image evidence before publication. That path selects every exclusive backend shard, including `unit_slow`; it does not replace `unit-fast` with the broad unit wrapper.

A release may instead reuse the source receipt of a pull request or merge queue run that tested the identical tree, because the source phase then repeats work already proven for those bytes. `tools/ci/source_verification.py` takes producer identity from the Actions API, not the artifact: a same-repository head, a successful latest attempt started within 24 hours, both the tested commit and the workflow-definition commit have the release tree, and the receipt matches GitHub's artifact digest. The pull request must have selected the full backend, full frontend and generated-contract checks; partial path selection never stands in. The release records the decision and producer receipt separately from its own run. Any rejection, including a direct push, a changed merge result or a failed lookup, runs the source phase. Image, architecture, journey, recovery and migration qualification always run, because reused source evidence does not qualify a newly built image.

Application publishing assembles one candidate manifest, then calls the existing test and migration workflows before advancing `latest`. The full CI owner keeps source and installed-image results under its existing required aggregator. Its optional immutable image reaches fresh, upgrade, controller/journal, exact-artifact, and credential-free recovery journeys; ordinary PR CI still builds locally. Recovery preparation stops its job-owned registry, whose storage is the uploaded transport, and releases its private Buildx cache. Release qualification uses run-specific cancellation groups so an older rerun cannot cancel the current candidate. Qualification-file PRs also exercise the supplied-image path using one OCI archive restored identically into job-owned loopback registries, without publishing a release. Runtime qualification covers amd64; arm64 has build coverage. Missing or unsuccessful required results leave `latest` unchanged. This CI provenance check does not restrict installed-runtime admission or an operator's image choice.

Fresh-install journeys wait for default Agent Profile validation and Provider Profile bootstrap separately. A bounded retry of the same idempotent submission handles only the default-provider readiness refusal; other admission errors and workflow failures still fail the journey.

The real ownership verifier runs when collection inputs change (tests/fixtures/conftests/markers/plugins, dependencies, workflow/selector and duration inputs), on unknown changes, and for full-main/scheduled/manual events. Policy scans stay unconditional. It rejects missing ownership for provider-free unit, API component and integration nodes, even when an integration test lacks an execution marker. External provider, GPU and installed-deployment fixtures declare their resource requirements. The installed split plugin proves both hermetic and all four reliability partitions complete and disjoint. Hermetic duration hints in `tests/.hermetic-test-durations.json` are advisory observed setup/call/teardown times; stale entries cannot remove tests, and missing or invalid hints fall back to deterministic splitting. `MOONMIND_HERMETIC_SHARD=1` or `2` reproduces one isolated physical owner; omit it for the full local suite. Unit precedence is `slow > temporal_boundary > component > unit_fast`.

The invariant fast-unit command is:

```bash
python -m pytest tests/unit \
  --ignore=tests/unit/workflows/temporal \
  --ignore=tests/unit/api \
  --ignore=tests/unit/api_service \
  -m "unit_fast and not provider_verification and not requires_credentials" \
  -q -n auto --dist loadfile --durations=25
```

`./tools/test_unit.sh` reports the slowest Python tests with `--durations`; set `MOONMIND_PYTEST_DURATIONS` to tune the count. In CI it also writes JUnit XML unless `MOONMIND_PYTEST_JUNITXML` points at a different output path.

The workflow selects `frontend-static` and the browser matrix (Chromium and WebKit, plus Firefox for browser-sensitive changes) independently by changed-file impact. They run in parallel and report into `ci-required`, which accepts a skip only when the selector did not select the job. The generated-contract check still runs only when `tools/check_openapi_affecting_changes.sh`, run inside `select-test-suites`, reports an OpenAPI-affecting path or the change set is unknown, and `ci-required` aggregates it the same way.

See [Backend Test Selection Strategy](BackendTestSelection.md) for the detailed selector contract, category definitions, full-backend fail-open rules, and maintenance guidance.

CI does **not** currently run a dedicated `pre-commit` step, so local `pre-commit` runs are still the main way to catch formatting and auto-fixable lint issues before pushing.

The test suite and migration gate run when PRs targeting any branch are opened, updated, or reopened, including stacked remediation PRs targeting another job branch. Changing the base branch alone does not trigger them; reopen the PR or push a new commit to verify that candidate. The test suite also supports merge groups, schedules, and manual dispatch; the release workflow owns checks for pushes to `main`, reusing complete pre-merge source evidence as described above. Pushing a feature branch without an existing PR does not trigger CI. When publication is authorized, open or update the PR, or use an authorized manual dispatch, and confirm the expected run started for the intended revision. Do not assume a successful push is test evidence.

### Matched worker resource measurements

The test-suite workflow's optional `worker_comparison` dispatch input runs the
same fresh-install journey twice on one runner and application image: once with
separate workers and once with the optional combined process. Both disposable
projects enable asyncio debug. The comparison preserves ordinary production
defaults and does not touch an installed deployment.

The integration artifact retains sampled startup, cold-idle, workload and
warmed-idle memory, container counts, host capacity, worker image identities and
resource limits. Log summaries separately count asyncio slow callbacks, event-loop
lag warnings and Temporal workflow-activation deadlocks without retaining task
contents. The journey separates the restart-through-readiness window from
post-restart workload; readiness does not establish that lazy initialization is
finished. Each phase retains at most 100 numeric callback emission timestamps
and durations, with an omitted-event count. A boundary-crossing flag estimates
whether emission time minus duration precedes the phase start; log emission is
not an exact callback-end timestamp. Missing or truncated diagnostics remain
visible. A comparison requires matched conditions and observed cold-idle,
post-restart workload and warmed-idle resources. Post-restart work includes the
generated workflow and production activity probes. Pre-restart work and restart
are diagnostic windows that may finish between samples; the comparison explicitly
lists every phase without observed resources and preserves unavailable counts.
Samples crossing phase boundaries or missing container inventory remain excluded.
These short controlled journeys do not establish one-to-two-day normal-use safety;
record that gap separately from measured memory savings or passing functional checks.

### Code Scanning

GitHub runs two separate CodeQL products as dynamic workflows, with no file under `.github/workflows`. Neither is a required check, and they are not duplicate security analyses.

- **CodeQL** is security code scanning in repository default setup: default query suite, remote threat model, and the `actions`, `javascript-typescript` and `python` languages, on pull requests to `main`, pushes to `main` and a weekly schedule. Default setup cannot cancel a superseded run. Pull-request analyses are incremental (overlay database) and diff-informed, so they usually finish in about three minutes and are rarely still running when the next push arrives; a full `main` analysis costs more than twice as much runner time. Per-PR or per-ref cancellation through advanced setup was measured to save only a few percent of this product's runner time, so default setup stays. Switching needs a separately authorized security-setting change: GitHub rejects CodeQL uploads from workflows while default setup is enabled, so an advanced workflow merged first would run duplicate scans that fail to upload. A switch should keep the `CodeQL` workflow name, which pr-resolver uses to classify security checks, and the existing `/language:<lang>` categories, and confirm each language's analysis and the open alerts on `main` before default setup is retired.
- **CodeQL - Code Quality** has its own settings and runs the `code-quality` query suite for `javascript-typescript` and `python` on the same triggers. Its only controls, in repository settings or `PATCH /repos/{owner}/{repo}/code-quality/setup`, are enablement, languages, runner type or label, and AI findings. It has no concurrency or cancellation control, and switching security scanning to advanced setup neither replaces nor cancels it. Its pull-request analyses extract the whole repository without an overlay database, so each costs about as much as a `main` analysis. Python takes most of its runner time, and reducing that means dropping a language or disabling the product.

Both products analyze test code. Tests, fixtures and helpers can contain security-relevant code, so do not exclude `tests/` wholesale; a narrower generated, vendored or test-only exclusion (`paths-ignore` in a CodeQL configuration, which changes what is analyzed) needs measured coverage and timing evidence. Workflow trigger path filters only decide whether a scan runs; almost every `main` commit touches analyzed files, so they would save little.

To re-measure, list each workflow's runs with `gh api repos/{owner}/{repo}/actions/workflows/{id}/runs` and their jobs with `.../actions/runs/{id}/jobs`. Every run's event is `dynamic`; the display title (`PR #N`, `Push on main`, `Scheduled`) identifies the trigger.

## Setup

Install `pre-commit` into the Python environment you use for MoonMind development:

```bash
pip install pre-commit
```

Optionally install the Git hook so checks run automatically on `git commit`:

```bash
pre-commit install
```

## Recommended Development Workflow

1. Run inexpensive formatting and static checks relevant to the changed files, such as `pre-commit run --files <changed Python files>`. Review and stage any hook rewrites. Repository-wide hooks remain available when useful, but do not turn this into a broad test prerequisite.
2. Use the smallest representative tests for the red-green-refactor loop. An observed, representative CI failure can supply the red phase. Target explicit test paths or node IDs, not an entire CI category merely because the changed area selects it. Targeted integration or browser tests are appropriate when they exercise the changed boundary. Follow the managed-runner requirements in [AGENTS.md](../../AGENTS.md).

   ```bash
   # Targeted Python tests outside a MoonMind-managed workflow
   ./tools/test_unit.sh --python-only <pytest paths or node ids>

   # Targeted Python tests inside a MoonMind-managed workflow
   moonmind container python-tests <pytest paths or node ids>

   # Targeted frontend tests without also running the Python suite
   ./tools/test_unit.sh --dashboard-only --ui-args <test path>
   ```

3. Update generated frontend API types with `npm run generate` when the change requires it, and review the generated diff. Use relevant inexpensive frontend checks during iteration. Prefer CI for the broader frontend validation, browser matrix, and generated-contract verification instead of requiring `npm run frontend:ci` locally.
4. Let CI run the broader affected suites. Fail-open selection, broad or risky changes, and unclear coverage expand CI coverage rather than automatically requiring a full local run. This applies even when local or managed compute is available. Verify that selected suites cover the changed boundaries and user journeys. Arrange authorized verification for any relevant environment CI does not exercise rather than treating an unrelated green check as coverage.
5. When publication is authorized, push a coherent change, open or update the PR, and confirm its checks actually started for the current revision. Use the required `ci-required` and `migration-gate` results and inspect their underlying jobs as needed. PR creation is allowed while CI is pending. Do not claim passing verification or merge readiness from an earlier revision, an unrelated run, or merely queued checks.
6. For failures, inspect CI logs and iterate using the narrowest useful reproduction. Re-run broader verification in CI without routinely duplicating it locally. Report local commands and outcomes separately from CI run links, tested revisions, and pending, failed, or unavailable checks. When CI or publication is unavailable, continue safe authorized work, use representative local or managed checks where practical, and report the remaining verification gap. Never push solely to test against the user's publication policy.

### Step Execution Conformance Evidence

For a PR that changes Step Execution behavior, preserve the Step Execution conformance gate before claiming merge readiness. The PR checklist must say either:

- The conformance suite was run in CI or through a representative authorized runner, with the tested revision and run or command evidence:

  ```bash
  python -m moonmind.workflows.temporal.step_execution_conformance
  pytest tests/unit/workflows/temporal/test_step_executions.py tests/unit/workflows/temporal/test_step_checkpoints.py tests/integration/workflows/temporal/test_step_execution_manifest_evidence.py -q
  ```

- No fixture update was needed, with an explicit note explaining why the PR does not change Step Execution behavior or fixture coverage.

Do not repeat qualifying CI conformance coverage locally just to satisfy a pre-PR checklist. Confirm that the cited run actually exercised the required conformance coverage, not merely that some checks passed.

### Optional Broader Local Reproduction

Broader local execution remains useful for diagnosis that cannot be reduced to a targeted test, environment-specific behavior outside CI, or a practical fallback when CI is unavailable or publication is prohibited. It is an exception based on verification needs, not a mandatory local-first fallback chain. Keep the scope proportionate and preserve required CI checks.

The following suite-scoped commands are diagnostic references for an appropriately provisioned host environment, not the default development sequence or a replacement for CI. The workflow remains authoritative for its full setup, selection, and commands. Managed agents continue to use the authorized container-job path for Python execution rather than host-only Docker wrappers.

```bash
python -m pytest tests/unit \
  --ignore=tests/unit/workflows/temporal \
  --ignore=tests/unit/api \
  --ignore=tests/unit/api_service \
  -m "unit_fast and not provider_verification and not requires_credentials" \
  -q -n auto --dist loadfile --durations=25

python -m pytest tests/unit/api tests/unit/api_service tests/component/api \
  -m "component and not temporal_boundary and not slow and not provider_verification and not requires_credentials" \
  -q -n auto --dist load --durations=25

python -m pytest tests/unit/workflows/temporal \
  -m "temporal_boundary and not slow and not provider_verification and not requires_credentials" \
  -q -n auto --dist loadfile --durations=25

python -m pytest $(cat tools/ci/slow-test-paths.txt) \
  -m "slow and not provider_verification and not requires_credentials and not integration" \
  -q --durations=50

python tools/verify_test_shard_ownership.py
```

A bare `./tools/test_unit.sh` invocation is for a deliberate broad local unit run. Use `./tools/test_integration.sh` for a deliberate host-side hermetic integration reproduction. Changes to Docker, compose, migrations, integration tests, or runtime infrastructure normally receive their broader verification from the selected CI integration jobs, not a mandatory local integration suite.

The Windows wrappers `tools/test-unit.ps1` and `tools/test-integration.ps1` already run repository-wide `pre-commit` hooks. That implementation detail does not make their broader test flows a required pre-PR step.

## Manual Commands

```bash
# Run all configured hooks against the repo
pre-commit run --all-files

# Run hooks only for selected files
pre-commit run --files path/to/file.py
```

## Troubleshooting

### `pre-commit: command not found`

Install the tool into the active Python environment:

```bash
pip install pre-commit
```

### Hooks Rewrote Files

That usually means `black`, `isort`, or `ruff --fix` corrected formatting or lint issues. Re-review the changes, stage them, and rerun your test command.

### Unit Tests Behave Differently In WSL

The wrapper can select its Docker-backed fallback when a human WSL session lacks the local Python or Node toolchain. That fallback currently does not forward the requested test arguments, so it must not be used as a targeted reproduction. Use a configured local environment or the authorized managed runner instead. `MOONMIND_FORCE_LOCAL_TESTS=1` bypasses the fallback for a deliberate local run only when its required toolchain is available. Prefer CI for broader verification rather than accepting an unintended full local suite.

## Related Files

- `.pre-commit-config.yaml`
- `docs/Development/BackendTestSelection.md`
- `package.json`
- `tools/test_unit.sh`
- `tools/test_unit_docker.sh`
- `tools/select_test_suites.py`
- `tools/generate_openapi_types.py`
- `tools/test-unit.ps1`
- `tools/test-integration.ps1`
