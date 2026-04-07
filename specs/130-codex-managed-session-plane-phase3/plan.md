# Plan: Codex Managed Session Plane Phase 3

## Summary

Add the typed session-oriented `agent_runtime.*` activity surface for the Codex managed session plane. This phase stops at the contract boundary: schemas, catalog entries, and Temporal activity methods delegate through an explicit remote session controller without implementing the Docker session launcher itself.

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Orchestrate, Don't Recreate | PASS | Session activities call an explicit remote-session control boundary instead of embedding a local Codex loop in the worker. |
| II. One-Click Agent Deployment | PASS | No new deployment prerequisite is introduced in this phase. |
| III. Avoid Vendor Lock-In | PASS | The slice is intentionally Codex-specific, but isolated to the managed-session contract boundary. |
| IV. Own Your Data | PASS | The new summary/artifact publication contracts keep continuity artifact-first. |
| V. Skills Are First-Class and Easy to Add | PASS | No skill-resolution behavior changes. |
| VI. Thin Scaffolding, Thick Contracts | PASS | This phase is contract-first with boundary tests. |
| VII. Powerful Runtime Configurability | PASS | The runtime control boundary is injectable and does not hardcode a local execution path. |
| VIII. Modular and Extensible Architecture | PASS | Activity methods depend on a session-controller interface, not a concrete launcher. |
| IX. Resilient by Default | PASS | Missing session-controller configuration fails fast; workflow-boundary coverage is added for new activity signatures. |
| X. Facilitate Continuous Improvement | PASS | Typed contracts make later phase instrumentation easier to evolve. |
| XI. Spec-Driven Development | PASS | This spec/plan/tasks set tracks the Phase 3 slice. |
| XII. Canonical Documentation Separates Desired State from Migration Backlog | PASS | Canonical docs are updated minimally to reflect the implemented activity surface. |

## Change Map

| Artifact | Change | Rationale |
|---|---|---|
| `moonmind/schemas/managed_session_models.py` | Add typed request/response models for session launch, control, summary, and artifact publication. | FR-001, FR-002, NF-001 |
| `moonmind/schemas/__init__.py` | Export the new managed-session contract models. | Keep schema imports consistent. |
| `moonmind/workflows/temporal/activity_catalog.py` | Register session activity types on `mm.activity.agent_runtime`. | FR-004 |
| `moonmind/workflows/temporal/activity_runtime.py` | Add session activity methods that validate typed payloads and delegate via a session-controller boundary. | FR-003 |
| `tests/unit/schemas/test_managed_session_models.py` | Add TDD coverage for remote-container session request/response contracts. | NF-001 |
| `tests/unit/workflows/temporal/test_agent_runtime_activities.py` | Add TDD and Temporal-boundary coverage for session activities. | NF-002 |
| `tests/unit/workflows/temporal/test_activity_runtime.py` | Extend binding coverage for the `agent_runtime` fleet. | FR-004 |
| `docs/Temporal/ActivityCatalogAndWorkerTopology.md` | Reflect the new managed-session activity surface. | Traceability |

## Execution Order

1. Add failing schema and activity tests for the session-oriented activity surface.
2. Implement the typed managed-session contract models and exports.
3. Register the new `agent_runtime.*` session activities in the Temporal activity catalog and binding metadata.
4. Implement `TemporalAgentRuntimeActivities` session methods behind an explicit injected session-controller boundary.
5. Run focused tests, then `./tools/test_unit.sh`.

## Risks

- **Premature runtime implementation**: Phase 3 should not quietly implement the Docker launcher. Mitigation: session activities fail fast without an injected remote session controller.
- **Worker-local fallback risk**: The existing managed runtime path could accidentally be reused. Mitigation: session request models freeze `controlMode=remote_container`, and activity methods never route through `ManagedRuntimeLauncher`.
- **Boundary drift risk**: New activity signatures could be correct in unit tests but incompatible with Temporal serialization. Mitigation: add Temporal workflow-boundary tests.

## Testing Strategy

- Focused tests:
  - `tests/unit/schemas/test_managed_session_models.py`
  - `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
  - `tests/unit/workflows/temporal/test_activity_runtime.py`
- Final verification:
  - `./tools/test_unit.sh`
