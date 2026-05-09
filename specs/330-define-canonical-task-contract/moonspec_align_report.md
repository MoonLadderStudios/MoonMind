## MoonSpec Alignment

**Feature**: 330-define-canonical-task-contract (MM-638)
**Date**: 2026-05-09

Updated:
- `specs/330-define-canonical-task-contract/tasks.md`: Corrected total task count (21→29), corrected parallel task range in Summary (T009-T011→T011-T015), replaced stale dependency graph with accurate IDs matching phase numbering (T006=TaskGitSelection, T007-T009=ExecutionSpec/validator, T010=__all__, T011-T015=unit tests, T017-T020=integration tests, T022-T028=story validation, T029=verify), expanded T014 description to explicitly require `edited_full_retry + resume → error` test case in addition to `exact_full_rerun + resume → error`
- `specs/330-define-canonical-task-contract/quickstart.md`: Fixed SC-006 Python assertion from weak `OR` form (`targetBranch is None OR branch == ...`) to correct two-line form asserting `branch == ...` AND `targetBranch not in git`
- `specs/330-define-canonical-task-contract/research.md`: Resolved contradiction between research statement ("no new integration test required") and tasks T017-T020; clarified that the existing API-level test does not cover the new recovery/resume payload scenarios and that T017-T020 are hermetic `integration_ci` tests that invoke `build_canonical_task_view` directly for targeted FR-012 coverage

Key decisions:
- **Dependency graph task IDs**: The graph used prospective numbering (T006=ExecutionSpec fields) that shifted when Phase 4 (TaskGitSelection) was inserted before Phase 5. Chose to update the graph to match the phases rather than renumber phases, because the phases were already committed and renumbering would invalidate any in-progress tracking.
- **Integration test contradiction**: chose to update research.md with a narrower, accurate claim (existing test covers API path but not recovery/resume scenarios) rather than removing planned tasks, because FR-012 requires explicit API-boundary coverage for the new payload types and the existing test predates MM-638.
- **SC-006 assertion**: the `OR` form was not wrong for contract validation purposes but was a weaker check that would not detect `branch` being absent when `targetBranch` is also absent. The two-assertion form catches both properties independently and matches the implementation behavior verified by `test_fr011_target_branch_normalized_to_branch_in_canonical_output`.

Remaining risks:
- T017-T020 integration tests are not yet written; they remain as implementation tasks. The unit tests (T011-T015) cover the same normalization surface and all 53 pass today, so the story is unblocked for verification at the unit level.
- `edited_full_retry + resume → error` is enforced by the existing `_validate_recovery_resume_consistency` validator but lacks a dedicated labeled test; the updated T014 description explicitly requires it.

Validation:
- `MOONMIND_FORCE_LOCAL_TESTS=1 python -m pytest tests/unit/workflows/tasks/test_task_contract.py -q`: 53 passed in 0.08s — no regressions introduced by spec artifact edits
