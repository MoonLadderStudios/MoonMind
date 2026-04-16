# Quickstart: Codex Managed Session Volume Targeting

## Prerequisites

- Work from repository root.
- Use managed-agent local test mode: `MOONMIND_FORCE_LOCAL_TESTS=1`.
- Docker is required only for compose-backed integration verification.

## Focused Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py
```

Expected result: focused tests for `STORY-002` fail before implementation changes and pass after the story is complete.

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: full required unit suite passes before final MoonSpec verification.

## Integration Verification

```bash
./tools/test_integration.sh
```

Coverage target: `tests/integration/services/temporal/test_codex_session_task_creation.py` must be included in or added to the hermetic integration suite.

Expected result: hermetic integration coverage passes in a Docker-enabled environment. If `/var/run/docker.sock` is unavailable, record the blocker in verification output.

## End-To-End Story Check

1. Confirm `spec.md` preserves `MM-318` and the original preset brief.
2. Confirm `plan.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md` exist.
3. Confirm unit and integration test strategies remain separate.
4. After implementation, run `/moonspec-verify` equivalent against `specs/180-codex-volume-targeting/spec.md`.
