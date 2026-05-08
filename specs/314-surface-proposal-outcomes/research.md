# Research: Surface Proposal Outcomes

## FR-001 / Proposal Requested Summary

Decision: partial; preserve existing requested flag and add tests proving it appears in all required summary outputs.
Evidence: `moonmind/workflows/temporal/workflows/run.py` writes `proposals.requested`; `moonmind/agents/codex_worker/worker.py` writes `proposals.requested` in `reports/run_summary.json`.
Rationale: The core flag exists, but MM-600 requires the whole summary contract, so this stays partial until verified with delivered/failure/link fields.
Alternatives considered: Treat as implemented; rejected because the success criteria require the full proposal outcome summary set.
Test implications: unit + integration.

## FR-002 / Generated Submitted Delivered Counts

Decision: partial; generated/submitted counts exist, delivered count must be added to proposal submission results and finish summaries.
Evidence: `run.py` stores `_proposals_generated` and `_proposals_submitted`; `worker.py` `ProposalSubmissionReport` has `generated_count` and `submitted_count` only.
Rationale: Delivered count is a named acceptance criterion and is not represented in inspected summary builders.
Alternatives considered: Derive delivered count in UI from proposal records only; rejected because finish summaries and exported summaries also require it.
Test implications: unit + integration.

## FR-003 / Redacted Provider Failures And Validation Errors

Decision: partial; existing generic error arrays must become structured enough to distinguish redacted validation errors from provider-specific delivery failures.
Evidence: `run.py` appends generation/submission errors; `worker.py` redacts finish summary payloads; proposal service and delivery tests use `SecretRedactor`.
Rationale: Operators need partial-success diagnostics without secrets, and generic strings are insufficient for provider failure visibility.
Alternatives considered: Leave provider diagnostics only in logs; rejected because the spec requires summaries, delivery records, and operator diagnostics.
Test implications: unit + integration.

## FR-004 / External Issue Links

Decision: partial; proposal records expose external URLs but run summaries and execution detail must link delivered proposals.
Evidence: `TaskProposalModel` serializes `externalUrl`; `task_proposals.py` returns review delivery external URLs; no task-detail references to proposal delivery URLs were found.
Rationale: Delivered links are already persisted but not surfaced in the run-centric places required by MM-600.
Alternatives considered: Use `/api/proposals` as the only link surface; rejected because execution detail and finish summaries are required.
Test implications: unit + integration + frontend unit.

## FR-005 / Dedup Updates

Decision: partial; dedup calculation and duplicate metadata exist, but summary/detail outcome visibility is missing.
Evidence: `TaskProposalRepository.find_open_duplicate()` and service dedup tests exist; review delivery serialization can include `created` and `duplicateSource`.
Rationale: Operators must see whether a candidate created a new issue or updated/attached to an existing issue.
Alternatives considered: Hide dedup status behind provider metadata; rejected because the spec calls for dedup updates in summaries and Mission Control.
Test implications: unit + integration + frontend unit.

## FR-006 / Proposal State Exposure

Decision: implemented_unverified; workflow state-setting code exists but should gain a direct boundary test for active proposal-stage visibility.
Evidence: `MoonMindRunWorkflow._run_proposals_stage()` calls `_set_state(STATE_PROPOSALS, summary="Generating task proposals.")`; executions router includes `proposals` in status vocabulary.
Rationale: The behavior appears implemented, but current inspected tests focus on activity invocation and dashboard mapping rather than state exposure while in progress.
Alternatives considered: Mark implemented_verified; rejected because no direct state exposure assertion was found.
Test implications: unit + integration.

## FR-007 / Dashboard Running Compatibility

Decision: implemented_verified; preserve existing mapping.
Evidence: `tests/unit/api/routers/test_task_dashboard_view_model.py` asserts `proposals` maps to `running`; task-list tests include `proposals` status filtering.
Rationale: Current tests directly cover the compatibility mapping.
Alternatives considered: Add more implementation; rejected because no code change is currently indicated beyond final verification.
Test implications: none beyond final verify unless adjacent state payloads change.

## FR-008 / Delivery Detail Visibility

Decision: partial; API serialization exists for proposal records, but execution detail and Mission Control need run-scoped outcome visibility.
Evidence: `TaskProposalModel` and `_serialize_review_delivery()` expose provider, status, external key/url, task snapshot, and created/duplicate fields; `task-detail.tsx` has no proposal outcome rendering references beyond URL parsing.
Rationale: The required operator surface is run detail/Mission Control, not only the standalone proposal list.
Alternatives considered: Link out to the proposal page; rejected because normal review must remain external-tracker-native.
Test implications: unit + frontend unit + integration.

## FR-009 / Compact Task Summary

Decision: partial; task preview has many required fields but is incomplete and not rendered in required surfaces.
Evidence: `_build_task_preview()` serializes repository, runtime, skill, publish mode, instructions, preset provenance, authored preset count, and step source metadata.
Rationale: Priority and attempt policy are not visible, and the preview is not attached to run detail/Mission Control proposal outcomes.
Alternatives considered: Show full task payload; rejected because large task snapshots must stay out of workflow/UI summary payloads.
Test implications: unit + frontend unit.

## FR-010 / Malformed Candidate Handling

Decision: implemented_unverified; existing submission code skips malformed proposals but must prove visible redacted outcome data and no promotion.
Evidence: `worker.py` and `TaskProposalService` paths skip missing title/task payload candidates and record errors; provider decision tests prove edited issue text does not replace stored snapshots.
Rationale: The safety behavior exists in service layers, but MM-600 requires operator-visible redacted errors and no silent semantic drops.
Alternatives considered: Treat as implemented; rejected because outcome-surface proof is missing.
Test implications: unit + integration.

## FR-011 / Retry-Safe Delivery Failures

Decision: partial; provider delivery and decision paths have idempotency foundations, but delivery-failure visibility across summaries/diagnostics is incomplete.
Evidence: delivery service tests cover external delivery result persistence and duplicate provider event handling; summary builders do not include delivery failure categories.
Rationale: External delivery failures must be visible and retry-safe across multiple surfaces.
Alternatives considered: Rely on provider logs only; rejected for operator diagnostics.
Test implications: unit + integration.

## FR-012 / External Tracker Primary Review

Decision: partial; the architecture is external-tracker-native, but the current UI exposes a queue-like `ProposalsPage`.
Evidence: `frontend/src/entrypoints/proposals.tsx` labels the page "Review and manage task proposals in the queue"; source docs require no standalone proposal page as the normal review path.
Rationale: The implementation must ensure GitHub/Jira is the normal path and any MoonMind surface is status/admin/recovery only.
Alternatives considered: Delete all proposal UI; rejected because status/admin/recovery views remain valid source-design surfaces.
Test implications: frontend unit.

## FR-013 / Promotion Result Links

Decision: partial; provider decision responses can store promoted execution IDs, but run detail/Mission Control outcome links are missing.
Evidence: `TaskProposalProviderDecisionResponse` includes `promotedExecutionId`; tests cover provider decision promotion through canonical execution path and delivery recovery exposes provider decisions.
Rationale: Promotion outcome is known but not yet surfaced where MM-600 requires it.
Alternatives considered: Keep promotion links only in provider metadata; rejected because operators need run detail/Mission Control visibility.
Test implications: unit + integration + frontend unit.

## FR-014 / MM-600 Traceability

Decision: implemented_unverified; preserve through all downstream artifacts.
Evidence: `spec.md` preserves the canonical Jira preset brief and this plan preserves MM-600.
Rationale: Verification must compare against the preserved Jira source.
Alternatives considered: none.
Test implications: final verify.

## Test Strategy

Decision: use test-first backend unit tests, frontend unit tests, and hermetic integration coverage.
Evidence: Repo instructions require `./tools/test_unit.sh` for final unit verification and `./tools/test_integration.sh` for `integration_ci`. Existing focused tests live in `tests/unit/workflows/temporal/workflows/test_run_proposals.py`, `tests/unit/workflows/task_proposals/test_service.py`, `tests/unit/api/routers/test_task_proposals.py`, and frontend Mission Control/task detail tests.
Rationale: The story crosses workflow summary, API serialization, and UI rendering boundaries, so isolated tests alone are insufficient.
Alternatives considered: UI-only validation; rejected because summary/report contracts and workflow state are backend-owned.
Test implications: unit + integration + frontend unit.
