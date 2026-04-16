# MoonSpec Verification Report

**Feature**: Codex Managed Session Volume Targeting  
**Spec**: `/work/agent_jobs/mm:2c55783d-90ec-4a8c-baff-632d30c9c237/repo/specs/180-codex-volume-targeting/spec.md`  
**Original Request Source**: `spec.md` `Input` preserving Jira issue `MM-356`  
**Verdict**: ADDITIONAL_WORK_NEEDED  
**Confidence**: MEDIUM

## Test Results

| Suite | Command | Result | Notes |
|-------|---------|--------|-------|
| Focused unit | `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py` | PASS | 115 Python tests and 224 frontend tests passed. |
| Integration boundary | `python -m pytest tests/integration/services/temporal/test_codex_session_task_creation.py::test_codex_session_launch_command_uses_workspace_and_explicit_auth_target -q --tb=short` | PASS | Hermetic fake-runner test verifies launch command workspace/auth mounts and reserved env propagation. |
| Compose integration | `./tools/test_integration.sh` | NOT RUN | Blocked: `/var/run/docker.sock` is unavailable in this managed-agent container. |
| Full unit | `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` | PASS | 3419 Python tests, 16 subtests, and 224 frontend tests passed. |

## Requirement Coverage

| Requirement | Evidence | Status | Notes |
|-------------|----------|--------|-------|
| FR-001 | `managed_session_controller.py` mounts configured workspace volume; integration boundary test asserts workspace mount and `MOONMIND_SESSION_WORKSPACE_PATH`. | VERIFIED | Workspace volume is mounted for managed Codex launch. |
| FR-002 | `managed_agent_adapter.py` maps profile `volume_mount_path` to `MANAGED_AUTH_VOLUME_PATH`; `managed_session_controller.py` only mounts `codex_auth_volume` when that env value is present. | VERIFIED | Existing unit tests cover profile-derived auth target and no auth mount when absent. |
| FR-003 | `managed_session_models.py` and `managed_session_controller.py` reject `MANAGED_AUTH_VOLUME_PATH` equal to `codexHomePath`. | VERIFIED | Added schema unit tests cover relative auth target and equality rejection. |
| FR-004 | `managed_session_controller.py` emits reserved `MOONMIND_SESSION_*` env values and rejects caller overrides. | VERIFIED | Existing controller test covers override rejection; integration boundary test covers reserved values in launch command. |
| FR-005 | `spec.md`, plan, tasks, contract, quickstart, checklist, and verification preserve `MM-356`. | VERIFIED | Feature artifacts preserve the canonical Jira issue key and preset brief. |

## Acceptance Scenario Coverage

| Scenario | Evidence | Status | Notes |
|----------|----------|--------|-------|
| Shared workspace mounted | `test_codex_session_launch_command_uses_workspace_and_explicit_auth_target` | VERIFIED | Verifies workspace volume mount in docker run command. |
| No auth target means no auth mount | Existing controller unit test `test_controller_launch_does_not_mount_auth_volume_without_explicit_target` | VERIFIED | Preserved behavior. |
| Explicit auth target mounted separately | Existing controller unit test and new integration boundary test | VERIFIED | Verifies auth volume mount at `/home/app/.codex-auth`, not `codexHomePath`. |
| Auth target equals Codex home fails before container creation | Schema validation and controller validation | VERIFIED | Added schema coverage; existing controller validation remains. |
| Reserved session env values are launcher-owned | Controller validation and integration boundary test | VERIFIED | Caller override rejected; launcher-provided env emitted. |

## Constitution And Source Design Coverage

| Item | Evidence | Status | Notes |
|------|----------|--------|-------|
| DESIGN-REQ-004 | Workspace mount in controller and integration boundary test | VERIFIED | Required managed-session workspace mount is present. |
| DESIGN-REQ-005 | Adapter builds per-task repo/session/artifacts/Codex home paths under session root | VERIFIED | Launch payload carries workspace and Codex home paths. |
| DESIGN-REQ-006 | Profile-derived explicit auth target, schema/controller equality rejection, auth mount only at target | VERIFIED | Auth volume remains separate from live Codex home. |
| DESIGN-REQ-017 | Controller launch command includes workspace mount, conditional auth mount, and reserved session env | VERIFIED | Covered by unit and integration boundary tests. |
| DESIGN-REQ-020 | Changes remain in schema, adapter, and controller boundaries | VERIFIED | No OAuth terminal, provider profile storage, or workload-container scope added. |

## Original Request Alignment

- The input is treated as a single-story runtime feature request from Jira `MM-356`.
- The existing `specs/180-codex-volume-targeting` artifacts were resumed rather than regenerated.
- The Jira preset brief is now the canonical preserved input for downstream artifacts and PR metadata.

## Gaps

- Required compose-backed integration verification could not run in this managed-agent container because `/var/run/docker.sock` is missing.

## Remaining Work

1. Run `./tools/test_integration.sh` in a Docker-enabled environment and record the result.

## MM-363 Docker Verification Attempt

- Attempted under `specs/194-oauth-terminal-docker-verification` on 2026-04-16.
- Command: `./tools/test_integration.sh`
- Result: BLOCKED before tests ran because Docker could not connect to `unix:///var/run/docker.sock`; the socket is absent in this managed-agent container.
- Decision: Preserve ADDITIONAL_WORK_NEEDED. No compose-backed closure evidence was produced.

## Decision

- Code, unit coverage, and direct integration boundary evidence satisfy the story.
- Final MoonSpec closure remains blocked on the compose-backed integration command required by the repo test taxonomy.
