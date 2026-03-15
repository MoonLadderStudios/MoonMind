# Specification Analysis Report: Auth-Profile and Rate-Limit Controls (081)

**Date**: 2026-03-15
**Artifacts**: spec.md, plan.md, tasks.md
**Constitution check**: .specify/memory/constitution.md

## Findings Table

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|----|----------|----------|-------------|---------|----------------|
| U1 | Underspecification | MEDIUM | plan.md §3 — ManagedAgentAdapter | Plan references `ManagedRuntimeLauncher` (stub) but no task creates it. Phase 4 launcher is deferred to "Phase 4 of spec 073". | Add a clarifying note in plan.md that `ManagedRuntimeLauncher` is a stub/interface for Phase 5 only; adapter calls stub, not full launcher. No task change needed for Phase 5 scope. |
| U2 | Underspecification | MEDIUM | tasks.md T011 | T011 says "add `slot_assigned` signal to `agent_run.py`" but `agent_run.py` likely does not exist yet (run.py is the existing MoonMind.Run workflow). The spec doesn't mention `MoonMind.AgentRun` workflow separately. | Clarify in T011: if `agent_run.py` doesn't exist, create a minimal `AgentRunSlotContext` helper class or handle the signal in the adapter's own signal-wait loop instead. |
| C1 | Coverage Gap | LOW | spec.md FR-012 | FR-012 requires tests for all 4 behaviors (profile selection, env shaping for both modes, concurrency, 429 cooldown). tasks.md distributes tests across T014 and T018. | Coverage is intact across tasks; no change needed. Confirmed. |
| I1 | Inconsistency | LOW | spec.md §Key Entities vs. plan.md §3 env shaping | spec.md names `EnvironmentSpec` as a "Key Entity" but plan.md describes it as "inline or extracted"; may confuse implementers. | Standardize: plan.md should name `EnvironmentSpec` explicitly as the return type of `_shape_environment()`. Low impact — add a comment in the adapter file at implementation time. |
| A1 | Ambiguity | MEDIUM | spec.md DOC-REQ-009 + FR-009 | Both say "runtime-specific" env shaping but don't enumerate which env var names are cleared for each runtime family (gemini_cli, claude_code, codex_cli). | Accept as-is for Phase 5: exact var names are encapsulated in the adapter implementation. Add a `_OAUTH_CLEARED_VARS` dict per runtime_id in the adapter. Document as a constant, not a spec change. |
| A2 | Ambiguity | LOW | tasks.md T013 | T013 says "ensure AgentRunHandle contains only profile_id in metadata, never raw credential values" — "ensure" is vague. | Restate as: "Add `_validate_handle_metadata(handle)` assertion in unit test to confirm no sensitive keys in `handle.metadata`". Captured in T014 bullet. |

## Coverage Summary Table

| Requirement Key | Has Task? | Task IDs | Notes |
|-----------------|-----------|----------|-------|
| DOC-REQ-001 (ManagedAgentAuthProfile struct) | ✅ | T001, T002 | Struct already exists; verification tasks |
| DOC-REQ-002 (execution_profile_ref resolution) | ✅ | T006, T007, T014 | Full coverage |
| DOC-REQ-003 (per-profile concurrency) | ✅ | T010, T011, T012, T014 | Full coverage |
| DOC-REQ-004 (concurrency per-profile not per-family) | ✅ | T010, T014 | Full coverage |
| DOC-REQ-005 (429 cooldown) | ✅ | T015, T016, T017, T018 | Full coverage |
| DOC-REQ-006 (no credentials in payloads) | ✅ | T013, T019, T014, T020, T021 | Full coverage |
| DOC-REQ-007 (OAuth: clear API-key vars) | ✅ | T008, T014 | Full coverage |
| DOC-REQ-008 (OAuth credential volume persistence) | ✅ | T008, T014 | Full coverage |
| DOC-REQ-009 (env shaping both modes) | ✅ | T008, T009, T014 | Full coverage |
| DOC-REQ-010 (auth_profile.list activity + volume mounts) | ✅ | T003, T004, T005 | Full coverage |
| FR-011 (fail-fast on unknown/disabled profile) | ✅ | T007, T014 | Full coverage |
| FR-012 (validation tests for all 4 behaviors) | ✅ | T014, T018, T020, T021 | Full coverage |

## Constitution Alignment Issues

None. All 10 constitution principles verified against plan.md constitution check table — all PASS.

## Unmapped Tasks

None. All 25 tasks map to at least one functional requirement or DOC-REQ.

## Metrics

| Metric | Value |
|--------|-------|
| Total Functional Requirements | 12 (FR-001 to FR-012) |
| Total DOC-REQs | 10 (DOC-REQ-001 to DOC-REQ-010) |
| Total Tasks | 25 |
| Coverage % (requirements with ≥1 task) | 100% |
| Constitution violation count | 0 |
| CRITICAL findings | 0 |
| HIGH findings | 0 |
| MEDIUM findings | 3 |
| LOW findings | 2 |

## Next Actions

**No CRITICAL or HIGH issues found. Safe to proceed to speckit-implement.**

Recommended before implementation:
1. **(U2)** Clarify T011 at implementation time — if `agent_run.py` doesn't exist, implement `slot_assigned` signal wait loop inside `ManagedAgentAdapter` directly, using a `asyncio.Event` or `workflow.wait_condition` mock in tests.
2. **(A1)** Define `_OAUTH_CLEARED_VARS: dict[str, list[str]]` constant in `managed_agent_adapter.py` mapping `runtime_id → list of env var names to clear` — not a spec change, an implementation detail.
3. **(U1)** Treat `ManagedRuntimeLauncher` as a stub interface in Phase 5 — `ManagedAgentAdapter.start()` returns a synthetic `AgentRunHandle` for now without launching a real subprocess (full launch is Phase 4 of spec 073).
