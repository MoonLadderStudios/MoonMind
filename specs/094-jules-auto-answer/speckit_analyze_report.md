# Specification Analysis Report: Jules Question Auto-Answer

**Feature**: `094-jules-auto-answer`
**Analyzed**: 2026-03-21
**Artifacts**: spec.md, plan.md, tasks.md, data-model.md, contracts/requirements-traceability.md

## Findings

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|---|---|---|---|---|---|
| C1 | Coverage | MEDIUM | spec.md FR-008, tasks.md | FR-008 (auto-answer in `_run_integration_stage`) has task T017/T023 but no dedicated validation test task for the integration polling path alone | Add a test task targeting the `run.py` auto-answer path specifically, or combine with US1 flow test |
| I1 | Inconsistency | LOW | tasks.md T024, agent_run.py | T024 reads `JULES_MAX_AUTO_ANSWERS` via activity but the workflow in agent_run.py is deterministic — env vars must be read in activities, not workflow code. Correctly designed. | No action; correctly uses activity for env read |
| A1 | Ambiguity | LOW | spec.md Assumptions | Assumption: "Activities API returns activities in reverse chronological order" — API docs don't explicitly guarantee ordering | Accept assumption; add defensive sort by `createTime` in implementation |
| U1 | Underspecification | LOW | spec.md FR-005, plan.md | `integration.jules.answer_question` activity is described as orchestrating list+LLM+send but this bundles three concerns into one activity. May want separate activities for better testability | Accept for MVP; can refactor to separate activities later |

## Coverage Summary

| Requirement | Has Task? | Task IDs | Notes |
|---|---|---|---|
| FR-001 | ✅ | T002, T003 | Status map change |
| FR-002 | ✅ | T002, T004 | New literal added |
| FR-003 | ✅ | T011 | Transport method |
| FR-004 | ✅ | T012, T014, T015 | List activities activity |
| FR-005 | ✅ | T013, T014, T015 | Answer question activity |
| FR-006 | ✅ | T005, T006, T007, T033 | Schema models |
| FR-007 | ✅ | T016 | AgentRun polling |
| FR-008 | ✅ | T017 | Run integration polling |
| FR-009 | ✅ | T022, T023, T024 | Max cycles |
| FR-010 | ✅ | T029, T030 | Deduplication |
| FR-011 | ✅ | T026, T027, T028 | Opt-out |
| FR-012 | ✅ | T024, T026, T031 | Config env vars |
| FR-013 | ✅ | T016, T017 | sendMessage reuse |

## DOC-REQ Coverage

| DOC-REQ | Has Implementation Task? | Has Validation Task? | Status |
|---|---|---|---|
| DOC-REQ-001 | ✅ T002–T004 | ✅ T009 | Covered |
| DOC-REQ-002 | ✅ T011, T012 | ✅ T018, T019 | Covered |
| DOC-REQ-003 | ✅ T013 | ✅ T020 | Covered |
| DOC-REQ-004 | ✅ T016, T017 | ✅ T021 | Covered |
| DOC-REQ-005 | ✅ T016, T017 | ✅ T021 | Covered |
| DOC-REQ-006 | ✅ T022, T023 | ✅ T025 | Covered |
| DOC-REQ-007 | ✅ T029 | ✅ T030 | Covered |
| DOC-REQ-008 | ✅ T026, T027 | ✅ T028 | Covered |
| DOC-REQ-009 | ✅ T024, T026, T031 | ✅ T025, T028 | Covered |
| DOC-REQ-010 | ✅ T005, T006, T007 | ✅ T010 | Covered |
| DOC-REQ-011 | ✅ T012, T014, T015 | ✅ T019 | Covered |
| DOC-REQ-012 | ✅ T013, T014, T015 | ✅ T020 | Covered |

## Constitution Alignment Issues

None. All principles satisfied:
- Orchestrate, don't recreate: ✅ Reuses existing transport and activities
- No vendor lock-in: ✅ Provider-neutral workflow logic
- Modular architecture: ✅ New activities are thin wrappers
- Spec-driven: ✅ Full DOC-REQ traceability

## Unmapped Tasks

None. All tasks map to at least one requirement.

## Metrics

- Total Requirements: 13
- Total Tasks: 35
- Coverage: 100% (13/13 requirements with ≥1 task)
- DOC-REQ Coverage: 100% (12/12 with implementation + validation tasks)
- Ambiguity Count: 1 (LOW)
- Duplication Count: 0
- Critical Issues: 0

## Next Actions

- **No CRITICAL issues** — safe to proceed to speckit-implement.
- LOW/MEDIUM findings are acceptable for MVP implementation.
- Consider adding defensive `createTime` sort in `list_activities` implementation (A1).
