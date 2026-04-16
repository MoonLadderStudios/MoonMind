# Quickstart: Per-Run Codex Home Seeding

## Prerequisites

- Work from repository root.
- Use managed-agent local test mode: `MOONMIND_FORCE_LOCAL_TESTS=1`.
- Docker is required only for compose-backed integration verification.

## Focused Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py
```

Expected result: focused tests for `STORY-003` fail before implementation changes and pass after the story is complete.

## Full Unit Verification

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: full required unit suite passes before final MoonSpec verification.

## Integration Verification

```bash
./tools/test_integration.sh
```

Coverage target: `tests/integration/services/temporal/test_codex_session_runtime.py` must be included in or added to the hermetic integration suite.

Expected result: hermetic integration coverage passes in a Docker-enabled environment. If `/var/run/docker.sock` is unavailable, record the blocker in verification output.

## End-To-End Story Check

1. Confirm `spec.md` preserves `MM-357` and the original preset brief.
2. Confirm `plan.md`, `research.md`, `data-model.md`, `contracts/`, and `quickstart.md` exist.
3. Confirm unit and integration test strategies remain separate.
4. After implementation, run `/moonspec-verify` equivalent against `specs/181-codex-home-seeding/spec.md`.
