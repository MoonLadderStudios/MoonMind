# MoonSpec Verification Report

**Feature**: Launch Codex Auth Materialization  
**Spec**: `/work/agent_jobs/mm:86b98b8a-fbfe-413d-8f3b-80fad3329536/repo/specs/175-launch-codex-auth-materialization/spec.md`  
**Original Request Source**: `spec.md` Input for MM-334, derived from the MM-318 OAuthTerminal design breakdown  
**Verdict**: ADDITIONAL_WORK_NEEDED  
**Confidence**: HIGH for implementation and unit evidence; MEDIUM overall because Docker-backed integration could not run in this container.

## Test Results

| Suite | Command | Result | Notes |
|-------|---------|--------|-------|
| Focused unit | `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_codex_session_runtime.py` | PASS | 122 Python tests passed; the runner also executed 221 frontend tests successfully. |
| Full unit | `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` | PASS | 3198 Python tests passed, 1 xpassed, 16 subtests passed; 221 frontend tests passed. |
| Integration | `./tools/test_integration.sh` | NOT RUN | `/var/run/docker.sock` is missing in this managed container; Docker CLI cannot connect to the daemon. |

## Requirement Coverage

| Requirement | Evidence | Status | Notes |
|-------------|----------|--------|-------|
| FR-001 | `codex_session_adapter.py:875`, `codex_session_adapter.py:881`, `codex_session_adapter.py:884`; `managed_session_controller.py:1360` | VERIFIED | Launch request carries workspace, session, artifact, and per-run Codex home paths. |
| FR-002 | `codex_session_adapter.py:894`; `test_codex_session_adapter.py:528`; `test_codex_session_adapter.py:535`; `test_codex_session_adapter.py:547` | VERIFIED | OAuth-backed profile metadata and explicit auth target are passed as compact metadata. |
| FR-003 | `managed_session_controller.py:260`; `managed_session_controller.py:272`; `managed_session_controller.py:1376`; `managed_session_controller.py:1383`; `test_managed_session_controller.py:3293`; `test_managed_session_controller.py:3347` | VERIFIED | Controller mounts the auth volume only at `MANAGED_AUTH_VOLUME_PATH` and rejects equality with `codexHomePath`. |
| FR-004 | `codex_session_runtime.py:381`; `codex_session_runtime.py:385`; `codex_session_runtime.py:393`; `codex_session_runtime.py:397`; `test_codex_session_runtime.py:2192` | VERIFIED | Runtime independently validates auth source path and rejects equality before seeding. |
| FR-005 | `codex_session_runtime.py:402`; `codex_session_runtime.py:404`; `codex_session_runtime.py:410`; `codex_session_runtime.py:413`; `test_codex_session_runtime.py:2156` | VERIFIED | Eligible auth entries seed one way; excluded logs are not copied and existing materialized config is preserved. |
| FR-006 | `codex_session_runtime.py:430`; `codex_session_runtime.py:1497`; `test_codex_session_runtime.py:2131` | VERIFIED | App Server starts with `CODEX_HOME` set to the per-run Codex home. |
| FR-007 | `test_codex_session_adapter.py:528`; `test_managed_session_controller.py:3293`; `test_codex_session_runtime.py:2131`; `test_codex_session_runtime.py:2156`; `test_codex_session_runtime.py:2192` | PARTIAL | Profile-to-launch and runtime materialization boundaries have unit evidence; Docker-backed integration evidence remains unavailable locally. |

## Acceptance Scenario Coverage

| Scenario | Evidence | Status | Notes |
|----------|----------|--------|-------|
| Selected OAuth-backed profile launches with workspace-backed per-run Codex home | `codex_session_adapter.py:875`; `test_codex_session_adapter.py:541` | VERIFIED | The launch payload uses the task run under `.moonmind/codex-home`. |
| Durable auth volume mounts only at explicit auth target | `managed_session_controller.py:1379`; `test_managed_session_controller.py:3348` | VERIFIED | Unit test asserts no mount is created at `codexHomePath`. |
| Runtime validates and seeds before App Server startup | `codex_session_runtime.py:381`; `codex_session_runtime.py:1497`; `test_codex_session_runtime.py:2156` | VERIFIED | Seeding occurs before client initialization. |
| Invalid equal auth target and Codex home fails fast | `managed_session_controller.py:272`; `codex_session_runtime.py:385`; `test_codex_session_runtime.py:2192` | VERIFIED | Both launcher and runtime boundaries reject the unsafe path shape. |

## Constitution And Source Design Coverage

| Item | Evidence | Status | Notes |
|------|----------|--------|-------|
| DESIGN-REQ-005 | `codex_session_adapter.py:881`; `managed_session_controller.py:1360` | VERIFIED | Managed Codex sessions use the shared workspace layout and per-run home. |
| DESIGN-REQ-006 | `managed_session_controller.py:260`; `codex_session_runtime.py:385` | VERIFIED | Auth volume target is explicit and separate. |
| DESIGN-REQ-007 | `codex_session_runtime.py:402`; `test_codex_session_runtime.py:2156` | VERIFIED | Credential copy is one-way and filtered. |
| DESIGN-REQ-015 | `managed_session_controller.py:1360`; `managed_session_controller.py:1366`; `managed_session_controller.py:1370` | VERIFIED | Reserved session environment values are launcher-owned. |
| DESIGN-REQ-016 | `codex_session_runtime.py:372`; `codex_session_runtime.py:381`; `codex_session_runtime.py:430` | VERIFIED | Runtime creates the per-run home, seeds auth entries, and uses it as `CODEX_HOME`. |
| DESIGN-REQ-017 | Focused unit test command; full unit command | PARTIAL | Unit evidence is complete, but compose-backed integration remains blocked by missing Docker socket. |

## Original Request Alignment

- The implementation aligns with the MM-318 OAuthTerminal runtime source requirement for task-scoped Codex auth materialization.
- The durable auth volume remains a source-only credential store; the live Codex App Server home is per-run and workspace-backed.
- The verification cannot claim full completion until the Docker-backed integration command runs in an environment with Docker access.

## Gaps

- Required integration verification could not run because `/var/run/docker.sock` is unavailable in this managed container.

## Remaining Work

- Run `./tools/test_integration.sh` in a Docker-enabled environment and record the result.

## Decision

- Keep the feature at `ADDITIONAL_WORK_NEEDED` until Docker-backed integration verification passes. No implementation gap was found in the inspected code or unit evidence.
