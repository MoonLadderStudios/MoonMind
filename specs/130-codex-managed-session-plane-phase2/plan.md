# Plan: Codex Managed Session Plane Phase 2

## Summary

Introduce the durable task-scoped workflow owner for Codex managed sessions. This phase adds the `MoonMind.AgentSession` workflow, the bounded session-binding models it uses, and the `MoonMind.Run` wiring that starts one session workflow per task for managed Codex steps and tears it down during finalization.

## Constitution Check

| Principle | Status | Notes |
|---|---|---|
| I. Orchestrate, Don't Recreate | PASS | The session owner is a Temporal workflow boundary, not an in-process Codex loop. |
| II. One-Click Agent Deployment | PASS | No new deployment prerequisite is introduced in this workflow-only phase. |
| III. Avoid Vendor Lock-In | PASS | The change is isolated to Codex-specific session models/workflow rather than entangling the general agent path. |
| IV. Own Your Data | PASS | Session identity becomes workflow-owned metadata instead of ephemeral step-local state. |
| V. Skills Are First-Class and Easy to Add | PASS | Skill resolution remains an input ref on `MoonMind.AgentRun`; session ownership does not change skill semantics. |
| VI. Thin Scaffolding, Thick Contracts | PASS | Bounded session models and workflow tests anchor the phase before launcher/activity work exists. |
| VII. Powerful Runtime Configurability | PASS | Runtime/profile choice continues to flow through existing request parameters. |
| VIII. Modular and Extensible Architecture | PASS | Adds a dedicated workflow/module boundary for session ownership. |
| IX. Resilient by Default | PASS | Session continuity becomes replay-safe and task-scoped under Temporal control. |
| X. Facilitate Continuous Improvement | PASS | The workflow can later expose richer control/summary surfaces without redesigning root task orchestration. |
| XI. Spec-Driven Development | PASS | This phase is captured in a dedicated spec/plan/tasks slice. |
| XII. Canonical Documentation Separates Desired State from Migration Backlog | PASS | Detailed implementation sequencing stays in spec artifacts. |

## Change Map

| Artifact | Change | Rationale |
|---|---|---|
| `moonmind/schemas/managed_session_models.py` | Add Phase 2 session binding/input/control/status models. | FR-001, FR-002, NF-001 |
| `moonmind/schemas/agent_runtime_models.py` | Allow managed Codex steps to carry a bounded `managedSession` binding. | FR-004, NF-003 |
| `moonmind/workflows/temporal/workflows/agent_session.py` | Add the new `MoonMind.AgentSession` workflow. | FR-001, FR-002 |
| `moonmind/workflows/temporal/workflows/run.py` | Start/reuse/terminate the task-scoped Codex session workflow and bind requests. | FR-003, FR-004, FR-005 |
| `moonmind/workflows/temporal/workflows/agent_run.py` | Preserve managed-session identity on managed Codex step results. | FR-004 |
| `moonmind/workflows/temporal/workers.py`, `moonmind/workflows/temporal/worker_runtime.py`, `moonmind/workflows/temporal/worker_entrypoint.py` | Register the new workflow type. | FR-006 |
| `tests/unit/workflows/temporal/workflows/test_agent_session.py` | Add TDD coverage for the session-owner workflow. | NF-002 |
| `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py` | Add TDD coverage for root-workflow session binding and teardown. | NF-002 |
| `tests/unit/schemas/test_agent_runtime_models.py` | Pin the new request contract behavior. | NF-002 |

## Execution Order

1. Add failing schema/workflow tests for the new binding and session-owner behavior.
2. Implement the new managed-session models and `MoonMind.AgentSession` workflow.
3. Wire `MoonMind.Run` to start/reuse/terminate one task-scoped Codex session and pass the binding into `MoonMind.AgentRun`.
4. Register the workflow and update worker-topology expectations.
5. Run targeted tests, then `./tools/test_unit.sh`.

## Risks

- **Premature runtime coupling**: This phase must not sneak in launcher/activity behavior from later phases. Mitigation: keep session workflow state-only and bounded.
- **Execution-path regression**: Non-Codex or external agent steps must keep their current path. Mitigation: gate the session binding strictly to managed Codex requests and add a negative-path unit test.
- **Workflow registration drift**: Adding the workflow in one runtime bootstrap path but not another would create environment-specific failures. Mitigation: update both worker registration sites and the topology test.

## Testing Strategy

- Targeted tests:
  - `tests/unit/schemas/test_agent_runtime_models.py`
  - `tests/unit/workflows/temporal/workflows/test_agent_session.py`
  - `tests/unit/workflows/temporal/workflows/test_run_codex_sessions.py`
  - `tests/unit/workflows/temporal/test_temporal_workers.py`
- Final verification:
  - `./tools/test_unit.sh`
