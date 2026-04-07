# Tasks: Codex Managed Session Plane Phase 4

## T001 — Add TDD coverage for the transitional launcher [P]
- [X] T001a [P] Add unit tests for the container-side Codex app-server bridge request/response flow and logical thread mapping in `tests/unit/services/temporal/runtime/test_codex_session_runtime.py`.
- [X] T001b [P] Add unit tests for the Docker-backed managed-session controller launch/status/send-turn/clear/terminate flow in `tests/unit/services/temporal/runtime/test_managed_session_controller.py`.
- [X] T001c [P] Extend worker bootstrap tests in `tests/unit/workflows/temporal/test_temporal_worker_runtime.py` to assert the concrete session controller is built and injected into `TemporalAgentRuntimeActivities`.

**Independent Test**: `./tools/test_unit.sh tests/unit/services/temporal/runtime/test_managed_session_controller.py tests/unit/services/temporal/runtime/test_codex_session_runtime.py tests/unit/workflows/temporal/test_temporal_worker_runtime.py`

## T002 — Implement the container-side Codex session runtime [P]
- [X] T002a [P] Add a container-session runtime module under `moonmind/workflows/temporal/runtime/` that validates the mount contract, persists runtime session state, and translates control requests into `codex app-server` stdio calls.
- [X] T002b [P] Add a stable container startup/control entrypoint that the transitional session container can execute from the existing MoonMind image.
- [X] T002c [P] Keep MoonMind logical thread ids stable by persisting logical-to-vendor thread mapping in the mounted session workspace.

**Independent Test**: The container-side runtime tests pass without any worker-local Codex execution path.

## T003 — Implement the Docker-backed managed-session controller [P]
- [X] T003a [P] Add a concrete Docker-backed managed-session controller that launches a separate container from the request image, polls readiness, and executes session control actions against the container boundary.
- [X] T003b [P] Return typed Phase 3 managed-session models from launch/status/send-turn/clear/terminate operations and fail fast on Docker/control errors.
- [X] T003c [P] Ensure the controller never routes through `ManagedRuntimeLauncher.launch()`.

**Independent Test**: The controller tests prove launch/status/send-turn/clear/terminate all use the container boundary and preserve the typed contract surface.

## T004 — Wire the controller into the agent-runtime worker [P]
- [X] T004a [P] Update `moonmind/workflows/temporal/worker_runtime.py` so `_build_agent_runtime_deps()` constructs the concrete session controller.
- [X] T004b [P] Inject the concrete session controller into `TemporalAgentRuntimeActivities`.
- [X] T004c [P] Run `./tools/test_unit.sh`.

**Independent Test**: Worker bootstrap tests and the full unit suite pass with the concrete controller injected.
