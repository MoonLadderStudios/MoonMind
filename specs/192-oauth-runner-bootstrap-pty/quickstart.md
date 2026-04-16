# Quickstart: OAuth Runner Bootstrap PTY

## Prerequisites

- Work from repository root.
- Use managed-agent local test mode for unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1`.
- Docker is required only for compose-backed hermetic integration verification.
- The active feature directory is `specs/192-oauth-runner-bootstrap-pty`.

## Focused Unit Verification

Run the red-first unit targets for the MM-361 runner lifecycle:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py
```

Expected pre-implementation result: newly added MM-361 tests fail because the runner still uses placeholder sleep behavior or does not route provider bootstrap command ownership through the terminal bridge.

Expected post-implementation result: focused tests pass and prove provider bootstrap command validation, activity-to-runtime runner startup, terminal bridge command ownership, redacted startup failures, generic exec rejection, and idempotent cleanup.

## Workflow Boundary Verification

Run focused Temporal OAuth session tests when iterating on workflow/activity boundaries:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_session_activities.py
```

Expected result: workflow-bound activity payload expectations remain compatible while runner startup and cleanup behavior reflect the MM-361 contract.

## Full Unit Verification

Before final verification, run:

```bash
MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh
```

Expected result: full required unit suite passes.

## Integration Verification

When Docker is available, run:

```bash
./tools/test_integration.sh
```

Coverage target: `tests/integration/temporal/test_oauth_session.py` must include or retain hermetic coverage for OAuth session success, failure, cancellation, expiry, API-finalize, and runner stop paths.

Expected result: hermetic integration coverage passes in a Docker-enabled environment. If `/var/run/docker.sock` is unavailable in a managed-agent container, record the exact blocker in verification output.

## End-To-End Story Check

1. Confirm `spec.md` preserves `MM-361` and the original Jira preset brief.
2. Confirm `plan.md`, `research.md`, `data-model.md`, `contracts/oauth-runner-bootstrap-pty.md`, and `quickstart.md` exist.
3. Confirm unit and integration test strategies remain separate.
4. Confirm runner startup no longer uses placeholder sleep behavior for provider bootstrap terminal ownership.
5. After implementation, run `/moonspec-verify` equivalent against `specs/192-oauth-runner-bootstrap-pty/spec.md`.

## Validation Results - 2026-04-16

- Red-first focused unit evidence: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` failed before production changes for the expected MM-361 reasons: missing provider bootstrap command resolver, activity not passing `bootstrap_command`, terminal bridge not accepting `bootstrap_command`, and placeholder runner startup behavior.
- Focused unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/api_service/api/test_oauth_terminal_websocket.py tests/unit/auth/test_oauth_provider_registry.py tests/unit/auth/test_oauth_session_activities.py tests/unit/services/temporal/runtime/test_terminal_bridge.py` passed with 46 Python tests and 225 dashboard tests.
- Focused Temporal integration verification: `pytest tests/integration/temporal/test_oauth_session.py -q --tb=short` passed with 9 tests.
- Compose-backed integration verification: `./tools/test_integration.sh` did not run because `/var/run/docker.sock` is unavailable in this managed container (`dial unix /var/run/docker.sock: connect: no such file or directory`).
- Full unit verification: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` passed with 3448 Python tests, 1 xpass, 16 subtests, and 225 dashboard tests.
