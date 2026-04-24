# Research: Managed-Session Retrieval Durability Boundaries

## FR-001 / DESIGN-REQ-005 / DESIGN-REQ-012 - Durable retrieval truth must outlive session cache

Decision: partial; preserve the existing artifact-backed retrieval publication path, but add boundary proof that durable retrieval state rather than session-local memory is the authoritative recovery source.
Evidence: `moonmind/rag/context_injection.py` persists `ContextPack` JSON under `artifacts/context/` and records compact `metadata.moonmind.retrievedContextArtifactPath`; `docs/Rag/WorkflowRag.md` §6.2 and `docs/ManagedAgents/SharedManagedAgentAbstractions.md` define session state as a continuity cache, not durable truth.
Rationale: The code already publishes durable retrieval evidence, but the repo does not yet prove at a managed-session boundary that recovery after reset relies on those durable surfaces instead of session-local cache state.
Alternatives considered: Treat the current startup artifact publication as fully sufficient without extra tests. Rejected because MM-507 is specifically about reset-era durability semantics, not only startup publication.
Test implications: add unit and integration verification that durable artifact/ref state remains the authoritative recovery path after continuity changes.

## FR-002 / DESIGN-REQ-011 - Large retrieved bodies remain behind artifact/ref publication surfaces

Decision: implemented_unverified; verify the existing compact-metadata behavior first, then change code only if reset-era tests show payload discipline regresses across continuity boundaries.
Evidence: `moonmind/rag/context_injection.py` stores the full `ContextPack` JSON under `artifacts/context/` and records only relative artifact path, transport, and item count in request metadata; `tests/unit/rag/test_context_injection.py` and `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` already assert compact artifact refs at startup.
Rationale: Startup proof exists, but MM-507 needs explicit confirmation that the same compactness discipline still holds when a session is reset or reattached.
Alternatives considered: Redesign metadata publication immediately. Rejected because the current structure already looks correct and should be verified before it is changed.
Test implications: add reset-aware unit or integration assertions that durable workflow/runtime metadata stays compact while large retrieved bodies remain in artifacts.

## FR-003 / DESIGN-REQ-013 - Reset and session-epoch replacement preserve durable retrieval evidence

Decision: partial; extend the existing managed-session reconcile and session publication boundaries to prove retrieval evidence survives reset and epoch changes.
Evidence: `moonmind/workflows/temporal/runtime/managed_session_controller.py` reattaches or degrades active sessions during reconcile, and session records carry `sessionEpoch`; retrieval artifacts are stored in the task workspace rather than container-local paths.
Rationale: The underlying managed-session lifecycle concepts exist, but retrieval preservation across those boundaries is not currently expressed or tested.
Alternatives considered: Assume workspace-scoped artifacts automatically satisfy the requirement. Rejected because the feature requires explicit continuity semantics, not an unstated side effect of file locations.
Test implications: add unit coverage around reconcile/reset lifecycle handling and at least one integration or workflow-boundary scenario proving retrieval evidence survives reset/epoch replacement.

## FR-004 / DESIGN-REQ-017 - Next-step recovery by rerun or latest-context reattach

Decision: missing; add a deterministic recovery path that can rerun retrieval or reattach the latest durable context pack reference for the next step.
Evidence: `docs/Rag/WorkflowRag.md` §6.3 explicitly calls out rerunning retrieval or reattaching the latest context pack ref after reset, but no explicit “latest context pack ref” recovery path or test was found in current runtime boundaries.
Rationale: This is the largest uncovered contract gap for MM-507 and is the most likely place where code changes will be required after verification tests are written.
Alternatives considered: Limit recovery to implicit rerun-only behavior. Rejected for planning because the source design keeps both rerun and reattach as allowed recovery mechanisms, and the implementation should make the chosen behavior explicit.
Test implications: add verification-first tests for the selected recovery path and an implementation contingency if the current code cannot satisfy them.

## FR-005 / DESIGN-REQ-023 - Runtime-neutral durability contract across runtimes

Decision: partial; keep the durability contract shared across Codex and future runtimes and verify it at the managed-runtime boundary rather than encoding runtime-specific persistence rules.
Evidence: Codex and Claude already share `ContextInjectionService` at startup through `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` and `moonmind/workflows/temporal/runtime/strategies/claude_code.py`; `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` verifies shared startup publication but not shared reset-era semantics.
Rationale: The startup contract is already shared, so MM-507 should preserve that shape while extending continuity semantics without introducing a Codex-only recovery model.
Alternatives considered: Solve durability only for Codex. Rejected because the source design and managed-session architecture explicitly treat durability truth as a shared MoonMind concern.
Test implications: add boundary tests or contract assertions that avoid runtime-specific persistence behavior in externally visible semantics.

## FR-006 - Traceability and MM-507 preservation

Decision: implemented_verified.
Evidence: `spec.md` preserves the original MM-507 preset brief and issue key; this planning phase adds `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/managed-session-retrieval-durability-contract.md` with the same key.
Rationale: The planning artifacts themselves satisfy the traceability requirement at this phase.
Alternatives considered: None.
Test implications: traceability review only.

## Test Strategy

Decision: use verification-first planning with separate unit and integration lanes.
Evidence: the repo already has unit coverage for retrieval publication and launcher/runtime startup plus managed-session controller tests and focused integration tests for managed-session retrieval context; `AGENTS.md` requires explicit unit and integration strategies.
Rationale: Much of MM-507 is already partially implemented as infrastructure. The safest path is to add failing verification tests for reset-era continuity behavior first and implement only where those tests expose a gap.
Alternatives considered: implementation-first planning. Rejected because it risks altering already-correct artifact publication and startup code without proving the real continuity gap.
Test implications:
- Unit: extend `tests/unit/rag/test_context_injection.py`, `tests/unit/services/temporal/runtime/test_managed_session_controller.py`, `tests/unit/services/temporal/runtime/test_launcher.py`, `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py`, and `tests/unit/workflows/temporal/test_agent_runtime_activities.py`.
- Integration: extend `tests/integration/workflows/temporal/test_managed_session_retrieval_durability.py` or add a dedicated reset-continuity scenario under `tests/integration/workflows/temporal/` to prove recovery behavior and compact durable-state handling.

## Planning Tooling Constraint

Decision: generate planning artifacts manually in the active feature directory.
Evidence: the planning helper path documented by the planning skill (`scripts/bash/setup-plan.sh --json`) does not exist in this repository, so automated setup cannot run here.
Rationale: `.specify/feature.json` already points to the active feature directory, so manual artifact generation is sufficient and avoids unrelated repo changes.
Alternatives considered: stop the planning run. Rejected because the required inputs are already available and the missing helper is not a blocker.
Test implications: none beyond documenting the constraint.
