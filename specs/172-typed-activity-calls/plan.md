# Implementation Plan: Typed Temporal Activity Calls

**Branch**: `172-typed-activity-calls` | **Date**: 2026-04-15 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `/specs/172-typed-activity-calls/spec.md`

## Summary

Enforce typed Temporal payload conversion and typed activity calls for the high-risk managed/external agent runtime activity boundary. The implementation adds one shared MoonMind Temporal data converter contract, extends activity request models for managed runtime and external run identifiers, updates the typed execution facade, and migrates AgentRun workflow call sites to construct typed models before invoking activities. Tests cover the converter contract, request validation, workflow call-site payload types, and real Temporal boundary serialization.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: Temporal Python SDK, `temporalio.contrib.pydantic.pydantic_data_converter`, Pydantic v2, pytest, Temporal test worker utilities
**Storage**: Temporal workflow histories carry compact typed payloads; no new persistence tables
**Unit Testing**: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh`; targeted `pytest` for `tests/unit/workflows/temporal/...` during iteration
**Integration Testing**: Targeted Temporal test-worker unit coverage is required for this story because it proves typed request serialization through a real Temporal worker without Docker. Run `./tools/test_integration.sh` for the hermetic `integration_ci` suite when Docker is available; record Docker socket unavailability as the exact blocker when it is not.
**Target Platform**: MoonMind Temporal client, worker fleet, and AgentRun workflow/activity runtime
**Project Type**: Backend Python service and Temporal workflow runtime in a single repository
**Performance Goals**: No additional network calls or polling; validation is constant-time for small activity payloads
**Constraints**: Preserve stable activity type strings; keep nondeterministic provider/file/network work in activities; retain only boundary-level legacy dict handling needed for in-flight payload safety; do not expose provider-shaped data to workflow logic
**Scale/Scope**: Representative migrated boundary includes shared data converter, typed execution facade, AgentRun external status/fetch/cancel calls, managed status/fetch/cancel calls, publish result handoff, and boundary validation tests

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

- **I. Orchestrate, Don't Recreate**: PASS. The change strengthens Temporal orchestration contracts without creating a new agent runtime.
- **II. One-Click Agent Deployment**: PASS. No new service, secret, or cloud dependency is introduced.
- **III. Avoid Vendor Lock-In**: PASS. Provider data is normalized behind adapter/activity boundaries into MoonMind contracts.
- **IV. Own Your Data**: PASS. Workflow history stores MoonMind-owned typed payloads rather than provider-specific bodies.
- **V. Skills Are First-Class and Easy to Add**: PASS. Skill behavior is not changed.
- **VI. The Bittersweet Lesson**: PASS. The change is a thin contract layer around existing runtime behavior.
- **VII. Powerful Runtime Configurability**: PASS. No configuration semantics are hardcoded or altered.
- **VIII. Modular and Extensible Architecture**: PASS. Converter, schemas, typed facade, workflow call sites, and tests stay in existing module boundaries.
- **IX. Resilient by Default**: PASS. Boundary tests and validated compatibility aliases protect in-flight invocation shapes.
- **X. Facilitate Continuous Improvement**: PASS. Stronger contracts make failures explicit and easier to diagnose.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS. This plan derives from the single-story spec.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS. Runtime work and tracking remain in feature artifacts and docs/tmp references.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS. No new internal compatibility alias is added beyond boundary validation for existing payload shapes.

## Project Structure

### Documentation (this feature)

```text
specs/172-typed-activity-calls/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── checklists/
│   └── requirements.md
├── contracts/
│   └── temporal-activity-boundary.md
└── tasks.md
```

### Source Code (repository root)

```text
moonmind/
├── schemas/
│   └── temporal_activity_models.py
└── workflows/
    └── temporal/
        ├── client.py
        ├── data_converter.py
        ├── typed_execution.py
        ├── activity_runtime.py
        └── workflows/
            └── agent_run.py

tests/
└── unit/
    └── workflows/
        └── temporal/
            ├── test_temporal_client.py
            ├── test_typed_activity_boundaries.py
            ├── test_agent_runtime_activities.py
            └── workflows/
                └── test_agent_run_jules_execution.py
```

**Structure Decision**: Use existing schema, Temporal client, typed execution, activity runtime, and AgentRun workflow modules. This keeps the feature at the Temporal boundary where the source design requires it.

## Phase 0: Research Summary

Research is captured in [research.md](./research.md). Key decisions:

1. Create a shared data converter module and import it from client/worker-facing code.
2. Add Pydantic request models for managed runtime and external run activity inputs using `extra="forbid"`.
3. Preserve legacy dict aliases only at public activity entry validation.
4. Migrate AgentRun call sites to typed request models and typed execution without changing activity type strings.
5. Prove typed serialization with unit and Temporal test-worker coverage, and run the compose-backed hermetic integration suite when Docker is available.

## Phase 1: Design Outputs

- [data-model.md](./data-model.md): Defines shared converter, request models, typed facade, canonical runtime responses, and boundary shim behavior.
- [contracts/temporal-activity-boundary.md](./contracts/temporal-activity-boundary.md): Captures activity type strings, request/response contracts, legacy aliases, and workflow-facing guarantees.
- [quickstart.md](./quickstart.md): Lists targeted and full validation commands.

## Implementation Strategy

1. Add a shared MoonMind Temporal data converter contract and replace direct converter imports in client construction.
2. Add strict typed activity request models for external run identifiers and managed runtime status/fetch/cancel operations.
3. Extend `execute_typed_activity` overloads for migrated activity names and route AgentRun activity execution through that facade.
4. Update AgentRun workflow call sites to pass typed request models for migrated external and managed runtime calls.
5. Update activity runtime entry points to validate any retained dict payloads into the typed models before business logic.
6. Add tests for converter identity, strict model validation, typed call-site payloads, and Temporal boundary round-trips. Treat `./tools/test_integration.sh` as the integration strategy for environments with Docker access.

## Post-Design Constitution Re-Check

- **I. Orchestrate, Don't Recreate**: PASS.
- **II. One-Click Agent Deployment**: PASS.
- **III. Avoid Vendor Lock-In**: PASS.
- **IV. Own Your Data**: PASS.
- **V. Skills Are First-Class and Easy to Add**: PASS.
- **VI. The Bittersweet Lesson**: PASS.
- **VII. Powerful Runtime Configurability**: PASS.
- **VIII. Modular and Extensible Architecture**: PASS.
- **IX. Resilient by Default**: PASS.
- **X. Facilitate Continuous Improvement**: PASS.
- **XI. Spec-Driven Development Is the Source of Truth**: PASS.
- **XII. Canonical Documentation Separates Desired State from Migration Backlog**: PASS.
- **XIII. Pre-Release Velocity: Delete, Don't Deprecate**: PASS.

## Complexity Tracking

No constitution violations or complexity exceptions.
