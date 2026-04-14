# Plan: Codex Managed Session Plane Phase 1

## Summary

Freeze the Codex managed session-plane MVP contract before implementing session workflows. This phase adds one canonical desired-state doc plus executable schema/state models that encode the fixed Phase 1 scope and the required clear/reset epoch transition.

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Orchestrate, Don't Recreate | PASS | Uses Codex App Server as the intended session protocol and keeps MoonMind at the orchestration boundary. |
| II. One-Click Agent Deployment | PASS | Contract stays Docker-first and does not add new deployment prerequisites. |
| III. Avoid Vendor Lock-In | PASS | Phase 1 is intentionally Codex-specific, but the contract is isolated to a bounded schema/doc slice rather than entangled core orchestration logic. |
| IV. Own Your Data | PASS | Durable-state rule makes artifacts and workflow metadata authoritative. |
| V. Skills Are First-Class and Easy to Add | PASS | Session contract references existing skill-materialization boundaries and does not mutate skill storage rules. |
| VI. Thin Scaffolding, Thick Contracts | PASS | This phase is contract-first with unit-test coverage. |
| VII. Powerful Runtime Configurability | PASS | No new hardcoded runtime execution behavior is introduced beyond the explicit Phase 1 contract. |
| VIII. Modular and Extensible Architecture | PASS | Adds a dedicated managed-session schema module and desired-state doc instead of overloading existing runtime payloads. |
| IX. Resilient by Default | PASS | Clear/reset semantics become explicit and durable-state rules stay artifact-first. |
| X. Facilitate Continuous Improvement | PASS | The contract is explicit enough to measure and evolve in later phases. |
| XI. Spec-Driven Development | PASS | This spec/plan/tasks set defines the Phase 1 slice before broader implementation. |
| XII. Canonical Documentation Separates Desired State from Migration Backlog | PASS | Desired-state session-plane contract lives in `docs/`; phase sequencing stays in this spec. |

## Change Map

| Artifact | Change | Rationale |
|---|---|---|
| `moonmind/schemas/managed_session_models.py` | Add Codex session-plane contract and state models. | FR-001 through FR-007, NF-001 |
| `moonmind/schemas/__init__.py` | Export the new session-plane schema symbols. | Keep schema imports consistent for downstream code. |
| `tests/unit/schemas/test_managed_session_models.py` | Add TDD coverage for fixed MVP defaults and clear/reset semantics. | NF-002 |
| `docs/ManagedAgents/CodexCliManagedSessions.md` | Add desired-state canonical doc for the Codex managed session plane. | FR-008 |
| `docs/Temporal/ManagedAndExternalAgentExecutionModel.md` | Add related-doc link to the new canonical session-plane contract. | Keep orchestration docs connected. |
| `docs/Temporal/ArtifactPresentationContract.md` | Add related-doc link to the new canonical session-plane contract. | Keep artifact continuity docs connected. |
| `specs/129-codex-managed-session-plane-phase1/*` | Add phase spec artifacts. | Principle XI |

## Execution Order

1. Write unit tests for the frozen Phase 1 defaults and clear/reset behavior.
2. Implement the new schema/state models.
3. Add the canonical desired-state document and cross-links.
4. Add the spec/plan/tasks artifacts for this phase.
5. Run targeted schema tests, then the repo unit-test runner.

## Risks

- **Over-modeling risk**: Adding session workflow or API contracts now would spill into later phases. Mitigation: keep this phase limited to the frozen contract and state transition semantics.
- **Doc drift risk**: A doc-only change could diverge from code. Mitigation: executable schema models and unit tests are the authoritative contract for this phase.
- **Premature generalization**: Introducing multi-runtime or Kubernetes abstractions now would violate the requested cut line. Mitigation: keep the models explicitly Codex-only and Docker-only.

## Testing Strategy

- Targeted schema tests:
  - `tests/unit/schemas/test_managed_session_models.py`
  - `tests/unit/schemas/test_agent_runtime_models.py`
- Final verification:
  - `./tools/test_unit.sh`
