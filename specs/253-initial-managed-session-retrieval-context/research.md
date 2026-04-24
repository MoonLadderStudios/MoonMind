# Research: Initial Managed-Session Retrieval Context

## FR-001 / DESIGN-REQ-001 - Initial retrieval resolves before runtime task work

Decision: implemented_verified; preserve existing startup ordering and rely on final verification unless later boundary work changes the sequence.
Evidence: `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` calls `ContextInjectionService.inject_context()` inside `prepare_workspace()` before the runtime command is built; `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` and `tests/unit/services/temporal/runtime/test_launcher.py` verify injection occurs during workspace preparation and before subprocess launch.
Rationale: The code and current tests jointly show that initial retrieval/injection is part of workspace preparation rather than an after-the-fact runtime action.
Alternatives considered: Treat as implemented_unverified and add another ordering test immediately. Rejected for planning because the current unit and launcher coverage already exercises the sequence directly.
Test implications: unit only beyond final verify.

## FR-002 / DESIGN-REQ-005 - Lean retrieval path with deterministic ContextPack assembly

Decision: implemented_verified.
Evidence: `moonmind/rag/service.py` embeds the query, searches Qdrant or the retrieval gateway, and builds a `ContextPack` without routing through a separate generative chat/completions step; `moonmind/rag/context_pack.py` provides deterministic packaging; `tests/unit/rag/test_service.py` now verifies direct retrieval uses embedding plus Qdrant search and gateway retrieval skips embedding while preserving the same contract shape; `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` verifies the runtime boundary consumes the resulting contract for both direct and gateway transports.
Rationale: The added unit and launcher-boundary integration coverage now prove both the lean retrieval path and the runtime-boundary contract without introducing an extra generative retrieval hop.
Alternatives considered: Introduce a second retrieval-stage wrapper for runtime adapters. Rejected because the existing service and integration evidence already prove the current path is sufficient.
Test implications: maintain the direct/gateway service tests and the launcher-boundary integration test.

## FR-003 / DESIGN-REQ-002 / DESIGN-REQ-008 / DESIGN-REQ-011 - Durable publication and compact startup context

Decision: implemented_verified.
Evidence: `moonmind/rag/context_injection.py` persists retrieval output under `workspace/artifacts/context/`, records compact `metadata.moonmind` fields with the artifact path, transport, and item count, and injects a compact artifact reference into the runtime instruction; `tests/unit/rag/test_context_injection.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` confirm the artifact reference remains relative, compact, and launcher-visible.
Rationale: The compact artifact-reference metadata and injected artifact notice make the durable artifact the explicit startup reference while keeping the runtime-boundary payload compact.
Alternatives considered: Add a new durable storage system for startup context. Rejected because the existing workspace artifact plus compact artifact-reference metadata already satisfies the story without new persistence.
Test implications: keep the compact artifact-reference unit coverage and the launcher-boundary integration coverage.

## FR-004 / DESIGN-REQ-006 - Adapter input surface and untrusted retrieved text framing

Decision: implemented_verified; preserve current behavior and only revisit if later planning uncovers a runtime path that bypasses it.
Evidence: `moonmind/rag/context_injection.py` composes the instruction with `BEGIN_RETRIEVED_CONTEXT` / `END_RETRIEVED_CONTEXT` framing and explicit untrusted-text safety guidance; `moonmind/workflows/temporal/activity_runtime.py` and `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` use the injected instruction path; `tests/unit/rag/test_context_injection.py` and `tests/unit/workflows/temporal/test_agent_runtime_activities.py` verify the framing and prepared instruction boundary.
Rationale: The current code and tests cover both the composition logic and the runtime-boundary instruction preparation path.
Alternatives considered: Add more direct tests now. Rejected because the current coverage is already boundary-aware and the remaining risk sits elsewhere.
Test implications: unit only beyond final verify.

## FR-005 / DESIGN-REQ-017 / DESIGN-REQ-025 - Reusable runtime contract and MoonMind-owned policy

Decision: implemented_verified for shared Codex/Claude startup reuse; durable artifact/ref authority remains a separate partial concern under FR-003.
Evidence: `moonmind/rag/context_injection.py` and `moonmind/rag/context_pack.py` remain shared primitives; `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` and `moonmind/workflows/temporal/runtime/strategies/claude_code.py` now both call `ContextInjectionService.inject_context()` during workspace preparation; `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py`, `tests/unit/services/temporal/runtime/test_launcher.py`, and `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` verify the Claude path uses the same shared startup contract; `api_service/api/routers/retrieval_gateway.py` preserves MoonMind-owned retrieval transport.
Rationale: The previously under-verified runtime reuse gap was real. The narrow fix was to move Claude workspace preparation onto the same `ContextInjectionService` contract already used by Codex, then verify that behavior at strategy, launcher, and focused integration boundaries.
Alternatives considered: Limit the story to Codex only. Rejected because `spec.md` intentionally preserves the shared-contract requirement from the source design. Leave Claude on bespoke `CLAUDE.md` handling. Rejected because that would keep DESIGN-REQ-017 only partially implemented.
Test implications: keep the shared runtime startup tests; direct/gateway neutrality is now verified rather than deferred.

## FR-006 - Traceability and MM-505 preservation

Decision: implemented_verified.
Evidence: `spec.md` preserves the original brief and MM-505; `plan.md`, `research.md`, `data-model.md`, `contracts/managed-session-retrieval-context-contract.md`, and `quickstart.md` all retain the issue key.
Rationale: The planning artifacts themselves satisfy the traceability requirement at this stage.
Alternatives considered: None.
Test implications: traceability review only.

## Test Strategy

Decision: use verification-first TDD with explicit unit and integration lanes.
Evidence: existing unit coverage already exercises retrieval services, context injection, Codex runtime strategy preparation, launcher sequencing, and Temporal activity-boundary instruction preparation; repo policy in `AGENTS.md` requires explicit unit and integration test strategies and emphasizes workflow-boundary coverage for compatibility-sensitive runtime changes.
Rationale: Most MM-505 behavior appears present. The safest next step is to write verification tests first for the partially covered boundaries, then implement only where those tests expose a real gap.
Alternatives considered: immediate implementation-first planning. Rejected because the current repository already contains much of the required behavior.
Test implications: 
- Unit: extend `tests/unit/rag/test_context_injection.py`, `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py`, `tests/unit/workflows/temporal/test_agent_runtime_activities.py`, and `tests/unit/services/temporal/runtime/test_launcher.py` as needed.
- Integration: add a focused workflow-boundary scenario under `tests/integration/workflows/temporal/` if compact durable-state proof or reusable runtime behavior cannot be established adequately through unit tests alone.

## Planning Tooling Constraint

Decision: generate planning artifacts manually in the active feature directory.
Evidence: `.specify/scripts/bash/setup-plan.sh --json` fails on the orchestrated branch name `run-jira-orchestrate-for-mm-505-resolve-1941b9e0` because it requires a numeric feature-branch prefix.
Rationale: The skill’s setup helper is branch-name-gated in this environment, but the active feature directory is already known from `.specify/feature.json`. Manual artifact creation preserves the planning workflow without changing branch state.
Alternatives considered: renaming the branch or stopping the run. Rejected because both are unnecessary and disruptive in this managed session.
Test implications: none beyond documenting the constraint.
