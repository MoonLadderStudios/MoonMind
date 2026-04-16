# Research: Codex Managed Session Volume Targeting

## Implementation Boundary

Decision: Implement managed-session launch mount validation and reserved environment propagation in existing MoonMind modules: moonmind/workflows/adapters/codex_session_adapter.py, moonmind/workflows/temporal/runtime/managed_session_controller.py, moonmind/schemas/managed_session_models.py.
Rationale: The source design requires thin orchestration boundaries and explicit ownership rather than new parallel runtime systems.
Alternatives considered: Creating new subsystems or compatibility wrappers was rejected because MoonMind is pre-release and internal contracts should fail fast rather than gain compatibility aliases.

## Unit Test Strategy

Decision: Use focused pytest coverage through `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_codex_session_adapter.py tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/schemas/test_managed_session_models.py`.
Rationale: Unit tests can prove validation, serialization, redaction, policy, and state behavior without requiring Docker credentials or external providers.
Alternatives considered: Provider verification tests were rejected for this planning phase because MM-356 requires deterministic local evidence first.

## Integration Test Strategy

Decision: Use compose-backed hermetic integration coverage through `./tools/test_integration.sh` when Docker is available; required coverage target: `tests/integration/services/temporal/test_codex_session_task_creation.py`.
Rationale: Stories touching API, Temporal, managed runtime, Docker, or browser transport need boundary evidence beyond isolated unit tests.
Alternatives considered: Skipping integration was rejected; only local execution may be blocked when `/var/run/docker.sock` is unavailable.

## Source Design Coverage

Decision: Preserve in-scope source coverage IDs `DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-017, DESIGN-REQ-020` and keep all other OAuthTerminal stories out of scope for this spec.
Rationale: Each generated spec must remain a single independently testable story while still preserving traceability to the broad OAuthTerminal design.
Alternatives considered: Combining stories was rejected because it would violate the one-story MoonSpec gate.
