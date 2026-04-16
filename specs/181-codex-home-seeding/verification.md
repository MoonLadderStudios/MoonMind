# MoonSpec Verification Report

**Feature**: Per-Run Codex Home Seeding  
**Spec**: `specs/181-codex-home-seeding/spec.md`
**Original Request Source**: `spec.md` Input, canonical Jira issue `MM-357` preset brief  
**Verdict**: FULLY_IMPLEMENTED  
**Confidence**: MEDIUM

## Test Results

| Suite | Command | Result | Notes |
|-------|---------|--------|-------|
| Focused unit | `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` | PASS | 82 Python tests and 224 frontend tests passed. |
| Required integration runner | `./tools/test_integration.sh` | NOT RUN | Blocked by missing Docker socket: `/var/run/docker.sock`. |
| Local integration file | `python -m pytest tests/integration/services/temporal/test_codex_session_runtime.py -q --tb=short` | PASS | 2 hermetic integration tests passed locally, including auth seeding plus per-run `CODEX_HOME`. |
| Full unit | `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` | PASS | 3432 Python tests, 16 subtests, and 224 frontend tests passed. |

## Requirement Coverage

| Requirement | Evidence | Status | Notes |
|-------------|----------|--------|-------|
| FR-001 | `CodexManagedSessionRuntime._ensure_directories()` creates `codex_home_path`; `test_runtime_launch_session_exports_codex_home` validates launch uses the per-run home. | VERIFIED | Per-run home is created before launch and used for app-server startup. |
| FR-002 | `_seed_codex_home_from_auth_volume()` validates `MANAGED_AUTH_VOLUME_PATH`, copies eligible entries, excludes generated entries and symlinks; unit tests cover eligible files, directories, missing path, file path, `sessions`, `logs_*`, and symlinks. | VERIFIED | No raw credential values are serialized by the seeding code. |
| FR-003 | `_app_server_client()` passes `env={"CODEX_HOME": str(self._codex_home_path)}` and launch calls seeding before app-server initialize. | VERIFIED | Unit and integration tests record the fake app-server environment. |
| FR-004 | Runtime homes/auth volumes are used as internal runtime state; progress evidence uses Codex artifact activity and tests assert artifact-based progress probing. | VERIFIED | No operator-facing summary or artifact payload added raw runtime home or auth-volume listings in this story. |
| FR-005 | `spec.md`, `plan.md`, `tasks.md`, `research.md`, `data-model.md`, `quickstart.md`, and contract artifacts preserve `MM-357`. | VERIFIED | Repository search confirmed no stale prior-issue traceability remains in the active spec artifacts. |

## Acceptance Scenario Coverage

| Scenario | Evidence | Status | Notes |
|----------|----------|--------|-------|
| Valid auth source copies eligible entries before app-server start | `_seed_codex_home_from_auth_volume()` plus unit/integration tests. | VERIFIED | Covered for files and directories. |
| Missing or invalid auth source fails with actionable error | Unit tests for missing path, file path, and auth path equal to Codex home. | VERIFIED | Error messages name `MANAGED_AUTH_VOLUME_PATH`. |
| Excluded entries are not copied | Unit/integration tests for `logs_*`, `sessions`, `config.toml`, and symlink exclusion. | VERIFIED | Generated/runtime entries remain excluded from per-run seed. |
| App server uses per-run `CODEX_HOME` | `_app_server_client()` and fake app-server environment recording tests. | VERIFIED | Covered by unit and integration tests. |
| Operator evidence is artifact-backed | Existing strategy progress tests use Codex artifacts as progress signal, and this story does not expose runtime homes/auth volumes in presentation outputs. | VERIFIED | UI/live-log surface work remains out of scope per spec. |

## Constitution And Source Design Coverage

| Item | Evidence | Status | Notes |
|------|----------|--------|-------|
| DESIGN-REQ-005 | Per-task workspace paths and `.moonmind/codex-home` are created and validated. | VERIFIED | Covered by runtime code and tests. |
| DESIGN-REQ-007 | Eligible auth entries seed one way from durable auth volume before app-server startup. | VERIFIED | Covered by runtime code and tests. |
| DESIGN-REQ-008 | Runtime starts Codex App Server with per-run `CODEX_HOME`. | VERIFIED | Covered by app-server client environment tests. |
| DESIGN-REQ-010 | Raw credential contents are not written into workflow history, logs, artifacts, or UI responses by this story. | VERIFIED | Seeding copies local files only and tests do not expose content in outputs. |
| DESIGN-REQ-019 | Execution evidence remains logs/artifacts/summaries rather than runtime homes/auth volumes. | VERIFIED | Existing strategy tests cover artifact-based progress; no presentation API exposes auth-volume contents. |
| DESIGN-REQ-020 | Work stays within Codex session runtime and strategy boundaries. | VERIFIED | No provider-profile, OAuth enrollment, or UI scope added. |

## Original Request Alignment

- PASS. The active feature artifacts use Jira issue `MM-357` as the canonical orchestration input.
- PASS. The implementation starts Codex App Server from a per-run `CODEX_HOME` seeded one way from a durable auth source.
- PASS. The auth volume is not treated as live runtime state.
- PASS. Out-of-scope areas from the Jira brief were not implemented.

## Gaps

- The required compose-backed integration runner could not execute in this managed container because Docker is unavailable at `/var/run/docker.sock`.

## Remaining Work

- None for code or tests in this repository. Re-run `./tools/test_integration.sh` in a Docker-enabled environment for compose-backed confirmation.

## Decision

- The story is fully implemented based on source inspection, unit validation, local hermetic integration validation, and preserved `MM-357` traceability. The only residual risk is environment-specific compose verification.
