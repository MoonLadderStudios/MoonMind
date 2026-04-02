# Implementation Plan: Canonical Return — Phase 3 (Managed Runtime Activities)

**Branch**: `123-canonical-return-phase3` | **Date**: 2026-04-02 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/123-canonical-return-phase3/spec.md`

## Summary

Phase 3 canonicalizes the managed runtime activity family (`agent_runtime.status`, `agent_runtime.fetch_result`, `agent_runtime.cancel`, `agent_runtime.publish_artifacts`) so they return typed Pydantic contracts (`AgentRunStatus`, `AgentRunResult`) directly, mirroring the pattern established in Phase 2 for external providers (Jules, Codex Cloud, OpenClaw). Implementation follows TDD: failing tests are written first, then the production code is updated to make them pass.

## Technical Context

**Language/Version**: Python 3.11+
**Primary Dependencies**: Pydantic v2, temporalio, pytest, pytest-asyncio
**Storage**: `ManagedRunStore` (in-process filesystem store)
**Testing**: pytest + pytest-asyncio via `./tools/test_unit.sh`
**Target Platform**: Linux Docker worker container (also tested on macOS)
**Project Type**: Single Python project
**Performance Goals**: Activity execution time unchanged (normalization is zero-cost dict→model conversion)
**Constraints**: In-flight replay safety — `agent_runtime_fetch_result` existing tests must pass; previously persisted dict payloads deserialized from Temporal history still work
**Scale/Scope**: 4 activity handlers in `TemporalAgentRuntimeActivities`, ~5 existing tests updated, ~15 new tests added

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Orchestrate, Don't Recreate | ✅ PASS | Adapters own normalization; workflow code becomes a consumer |
| II. One-Click Deployment | ✅ PASS | No config or compose changes |
| III. Avoid Vendor Lock-In | ✅ PASS | Canonical contracts are provider-agnostic |
| IV. Own Your Data | ✅ PASS | Output refs stay as artifact refs; no inline content |
| V. Skills Are First-Class | ✅ PASS | No skill changes |
| VI. Bittersweet Lesson | ✅ PASS | Thin adapters, thick contracts; design-for-deletion |
| VII. Runtime Configurability | ✅ PASS | No config surface changes |
| VIII. Modular Architecture | ✅ PASS | All changes isolated to activity_runtime.py |
| IX. Resilient by Default | ✅ PASS | Fail-fast on malformed inputs; retry safety preserved |
| X. Continuous Improvement | ✅ PASS | Test coverage increases |
| XI. Spec-Driven Development | ✅ PASS | This plan |
| XII. Canonical Docs | ✅ PASS | Implementation notes in docs/tmp |
| XIII. Pre-Release Velocity | ✅ PASS | Old dict return types removed immediately, no compat shims |

## Project Structure

### Documentation (this feature)

```text
specs/123-canonical-return-phase3/
├── plan.md              # This file
├── research.md          # Phase 0 findings
├── quickstart.md        # Validation quickstart
└── tasks.md             # Execution plan (speckit-tasks output)
```

### Source Code (repository root)

```text
moonmind/workflows/temporal/
├── activity_runtime.py                  # MODIFY: change return types for 4 methods
│
tests/unit/workflows/temporal/
├── test_agent_runtime_fetch_result.py   # MODIFY: assert typed return (existing tests)
├── test_agent_runtime_activities.py     # NEW: TDD tests for status, cancel, publish_artifacts
```

**Structure Decision**: Single project, all changes in existing files. No new modules required — unlike Phase 2, managed runtime activities live in `TemporalAgentRuntimeActivities` within `activity_runtime.py` not standalone modules. Phase 3 spec explicitly permits leaving launch in the class in-place and only upgrading return types.

## Phase 0 Research

### Inventory: Current return types

| Activity | Current Return | Target Return | Change Required |
|----------|---------------|---------------|-----------------|
| `agent_runtime.status` | `dict[str, Any]` | `AgentRunStatus` | YES |
| `agent_runtime.fetch_result` | `dict[str, Any]` | `AgentRunResult` | YES |
| `agent_runtime.cancel` | `None` | `AgentRunStatus` | YES |
| `agent_runtime.publish_artifacts` | `Any` | `AgentRunResult` | YES |
| `agent_runtime.launch` | `dict[str, Any]` | `dict[str, Any]` (keep; returns record) | NO |

### Replay safety analysis

- `agent_runtime.status` and `agent_runtime.fetch_result` return payloads are deserialized in `agent_run.py` via `_coerce_managed_status_payload` and `_coerce_managed_fetch_result`. Phase 4 (future) will delete these coercers. For Phase 3, the activities return typed models; the workflow coercers continue to handle both old dict payloads (for in-flight replays) and the new typed payloads (which Temporal serializes as dicts automatically). No `workflow.patched` is required for Phase 3 alone — Phase 4 is where the coercers are removed, and that is where Temporal versioning will be evaluated.
- `agent_runtime.cancel` returning `None` vs `AgentRunStatus`: the existing workflow code does not consume the cancel return value materially; this is safe to change without versioning.

### Gemini enrichment path

`agent_runtime_fetch_result` currently builds a `result_dict` from the typed model then returns the dict. The Gemini enrichment `_maybe_enrich_gemini_failure_result` already works on `AgentRunResult` directly. The publish-info and PR URL metadata is merged via `result_dict["metadata"]` then returned as a dict. Post-change: we enrich at the typed level and return the typed model directly (metadata is a field on `AgentRunResult`).

### `publish_artifacts` path

Currently accepts `Any` and normalizes to a dict. Post-change: it accepts `AgentRunResult` and returns `AgentRunResult`. The artifact-write patterns are unchanged; only the enrichment (adding `diagnostics_ref`) uses `model_copy`.

## Complexity Tracking

No constitution violations.

## Verification Plan

### Automated Tests

1. Write failing tests first (TDD) in `tests/unit/workflows/temporal/test_agent_runtime_activities.py`
2. Run `./tools/test_unit.sh` — confirm failures on the target methods
3. Update production code in `activity_runtime.py`
4. Run `./tools/test_unit.sh` — confirm all tests pass including existing `test_agent_runtime_fetch_result.py`

### Manual Verification

- Code review: confirm no `dict[str, Any]` return type remains on the 4 target methods
- Grep: confirm no callers in workflow code rely on direct dict key access of the return values (Phase 4 concern, but cross-check)
