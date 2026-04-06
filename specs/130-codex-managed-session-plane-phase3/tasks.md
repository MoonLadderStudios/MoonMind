# Tasks: Codex Managed Session Plane Phase 3

## T001 — Add TDD coverage for the session activity contract [P]
- [X] T001a [P] Add schema tests for remote-container launch/control request defaults and validation.
- [X] T001b [P] Add activity tests proving session methods fail fast without a session controller and return typed models when delegated.
- [X] T001c [P] Add Temporal workflow-boundary tests for representative session activity request/response serialization.
- [X] T001d [P] Extend activity-binding tests to assert the new `agent_runtime.*` session activity registrations.

**Independent Test**: `./tools/test_unit.sh tests/unit/schemas/test_managed_session_models.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/temporal/test_activity_runtime.py`

## T002 — Implement the typed managed-session contract surface [P]
- [X] T002a [P] Add launch/control/summary/publication request and response models to `moonmind/schemas/managed_session_models.py`.
- [X] T002b [P] Export the new managed-session schema symbols from `moonmind/schemas/__init__.py`.
- [X] T002c [P] Register the new session activity types in `moonmind/workflows/temporal/activity_catalog.py` and binding metadata in `moonmind/workflows/temporal/activity_runtime.py`.
- [X] T002d [P] Add session activity methods to `TemporalAgentRuntimeActivities` that validate typed payloads and delegate through an injected remote session controller.

**Independent Test**: The focused session activity suites pass and no session activity reuses the worker-local managed-runtime launcher path.

## T003 — Verify and document the new activity surface [P]
- [X] T003a [P] Update `docs/Temporal/ActivityCatalogAndWorkerTopology.md` to list the new session-oriented managed-runtime activities.
- [X] T003b [P] Run `./tools/test_unit.sh`.

**Independent Test**: `./tools/test_unit.sh` passes.
