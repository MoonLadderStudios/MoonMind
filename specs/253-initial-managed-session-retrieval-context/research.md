# Research: Initial Managed-Session Retrieval Context

## FR-001 / DESIGN-REQ-001 - Initial retrieval resolves before runtime task work

Decision: implemented_verified; preserve existing startup ordering and rely on final verification unless later boundary work changes the sequence.
Evidence: `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` calls `ContextInjectionService.inject_context()` inside `prepare_workspace()` before the runtime command is built; `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` and `tests/unit/services/temporal/runtime/test_launcher.py` verify injection occurs during workspace preparation and before subprocess launch.
Rationale: The code and current tests jointly show that initial retrieval/injection is part of workspace preparation rather than an after-the-fact runtime action.
Alternatives considered: Treat as implemented_unverified and add another ordering test immediately. Rejected for planning because the current unit and launcher coverage already exercises the sequence directly.
Test implications: unit only beyond final verify.

## FR-002 / DESIGN-REQ-005 - Lean retrieval path with deterministic ContextPack assembly

Decision: implemented_unverified; verify the managed-session path end to end before assuming the current retrieval service fully satisfies the story.
Evidence: `moonmind/rag/service.py` embeds the query, searches Qdrant or the retrieval gateway, and builds a `ContextPack` without routing through a separate generative chat/completions step; `moonmind/rag/context_pack.py` provides deterministic packaging; `tests/unit/rag/test_service.py` and `tests/unit/rag/test_context_pack.py` cover budget normalization and pack serialization behavior.
Rationale: The implementation strongly matches the design, but the current evidence is more service-level than managed-session-boundary level. A verification-first approach is safer than declaring it fully verified.
Alternatives considered: Mark implemented_verified now. Rejected because the current tests do not explicitly prove the managed-session runtime path never inserts an unintended extra retrieval stage.
Test implications: unit plus integration-style boundary verification.

## FR-003 / DESIGN-REQ-002 / DESIGN-REQ-008 / DESIGN-REQ-011 - Durable publication and compact startup context

Decision: partial; artifact publication exists, but durable artifact/ref-backed startup truth and compact-payload proof need stronger boundary evidence.
Evidence: `moonmind/rag/context_injection.py` persists retrieval output under `workspace/artifacts/context/` and returns `PromptContextResolution.artifact_path`; tests in `tests/unit/rag/test_context_injection.py` confirm artifact creation and injected instruction framing.
Rationale: The story requires more than file creation. We still need proof that durable artifacts or refs are the authoritative startup handoff and that large retrieved bodies are not duplicated into durable workflow payloads at runtime boundaries.
Alternatives considered: Treat the existing artifact file as fully sufficient. Rejected because the design language emphasizes durable artifact/ref truth and compact durable state, which is not yet proven by the current test surface.
Test implications: unit coverage for artifact metadata/persistence plus integration or workflow-boundary verification for compact durable-state behavior.

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
Test implications: keep the new unit and focused integration coverage for shared runtime startup behavior; continue treating durable artifact/ref authority and transport neutrality as separate verification work.

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
