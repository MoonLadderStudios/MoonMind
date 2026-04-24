# Research: Report Semantics Rollout

## Story Classification

Decision: Treat MM-497 as a single-story runtime feature request and a verification-first planning story.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-497-moonspec-orchestration-input.md`; `specs/249-report-semantics-rollout/spec.md`.
Rationale: The brief defines one independently testable outcome: preserve generic-output compatibility while newer report workflows adopt explicit report semantics with bounded migration and representative mappings.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one story and does not require processing multiple specs.
Test implications: Focused unit, contract, and UI verification are required, with hermetic integration escalation only if verification reveals runtime drift.

## FR-001 / DESIGN-REQ-021 Generic Output Compatibility

Decision: implemented_verified.
Evidence: `docs/Artifacts/ReportArtifacts.md` explicitly states existing generic outputs can continue using `output.primary`; `tests/unit/workflows/temporal/test_report_workflow_rollout.py` contains regression coverage that rejects `output.primary` as the canonical report for report-producing workflows.
Rationale: The repo already preserves the generic-output fallback rather than forcing a flag-day report migration.
Alternatives considered: Mark this partial until another integration test is added. Rejected because the current source design plus focused rollout validation already cover the intended boundary.
Test implications: Focused unit rerun only unless later fixes touch persistence or serialization.

## FR-002 Explicit `report.*` Semantics For Report Workflows

Decision: implemented_verified.
Evidence: `moonmind/workflows/temporal/report_artifacts.py` defines canonical `report.primary`, `report.summary`, `report.structured`, and `report.evidence` sets for report-producing workflows; `tests/unit/workflows/temporal/test_report_workflow_rollout.py` validates explicit report-link requirements.
Rationale: New report workflows already prefer explicit report semantics rather than reusing generic output behavior.
Alternatives considered: Re-implement the classification logic in another runtime layer. Rejected because the current helper already owns the rollout contract.
Test implications: Focused unit rerun only.

## FR-003 Incremental Rollout Without Flag-Day Migration

Decision: implemented_unverified.
Evidence: `docs/Artifacts/ReportArtifacts.md` section 19 defines the staged rollout; `api_service/api/routers/executions.py`, `tests/unit/api/routers/test_executions.py`, `tests/contract/test_temporal_execution_api.py`, and `frontend/src/entrypoints/task-detail.tsx` / `task-detail.test.tsx` show that explicit report projections and UI surfacing already coexist with normal artifact behavior.
Rationale: The current repo appears to satisfy the staged rollout path, but MM-497 still needs feature-local verification evidence tying those pieces together.
Alternatives considered: Mark as fully verified based only on related feature work. Rejected because MM-497 needs its own planning and later verification trail.
Test implications: Focused unit + contract + UI verification, with implementation contingency only if those tests reveal drift.

## FR-004 / DESIGN-REQ-023 Explicit Non-Goals Remain Out Of Scope

Decision: implemented_verified.
Evidence: `docs/Artifacts/ReportArtifacts.md` sections 2, 5, and 20 explicitly keep PDF rendering, provider-specific prompts, full-text indexing, legal review, separate report storage, mutable report updates, and provider-native payload parsing out of scope for the rollout.
Rationale: The source design already bounds the story against those capabilities, and no repo evidence suggests the rollout depends on them.
Alternatives considered: Treat out-of-scope items as unresolved until tasks exist. Rejected because the source doc already defines these boundaries clearly.
Test implications: No special runtime test beyond downstream traceability review.

## FR-005 / DESIGN-REQ-024 Representative Workflow Mappings

Decision: implemented_verified.
Evidence: `moonmind/workflows/temporal/report_artifacts.py` defines representative mappings for `unit_test_report`, `coverage_report`, `security_pentest_report`, and `benchmark_report`; `tests/unit/workflows/temporal/test_report_workflow_rollout.py` verifies the expected link-type sets and report-type coverage.
Rationale: The repo already preserves the representative producer mappings required by the brief.
Alternatives considered: Add new workflow families as part of MM-497. Rejected because the story requires preserved representative mappings, not additional producers.
Test implications: Focused unit rerun only.

## FR-006 Deferred Product Questions

Decision: partial.
Evidence: `docs/Artifacts/ReportArtifacts.md` section 20 preserves open questions around `report_type`, auto-pinning, projection timing, export semantics, evidence grouping, and multi-step report projections; MM-497-specific downstream artifacts were missing before this planning stage.
Rationale: The source design already preserves the deferred decisions, but the MM-497 feature directory still needs to carry them through planning, tasks, and final verification.
Alternatives considered: Treat the source doc alone as sufficient. Rejected because the Jira brief requires story-local preservation for later verification.
Test implications: Traceability review in planning, tasks, and final verification.

## FR-007 Traceability

Decision: partial.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-497-moonspec-orchestration-input.md` and `specs/249-report-semantics-rollout/spec.md` preserve MM-497, but `plan.md`, `tasks.md`, and `verification.md` were missing at planning start.
Rationale: The story is verification-first, but downstream artifacts still need to preserve the Jira issue key and original preset brief.
Alternatives considered: Treat spec-only traceability as enough. Rejected because final verification must compare against the Jira source brief.
Test implications: Traceability review is mandatory in tasks and final verification.

## Repo Gap Analysis Outcome

Decision: No production-code change is clearly required at planning time; MM-497 should proceed as a verification-first story with explicit traceability work.
Evidence: Existing rollout validation in `tests/unit/workflows/temporal/test_report_workflow_rollout.py`, explicit report publication/runtime helpers in `moonmind/workflows/temporal/report_artifacts.py` and `moonmind/workflows/temporal/artifacts.py`, execution-detail report projection coverage in `tests/unit/api/routers/test_executions.py` and `tests/contract/test_temporal_execution_api.py`, and Mission Control report surfacing in `frontend/src/entrypoints/task-detail.tsx` and `task-detail.test.tsx`.
Rationale: The repo already appears to satisfy the runtime behavior described by MM-497, so planning should prioritize verification, traceability, and a minimal implementation contingency rather than inventing code churn.
Alternatives considered: Force new implementation work simply because a new feature directory exists. Rejected because that would weaken the evidence-first planning discipline.
Test implications: Tasks should begin with focused verification and only move into implementation if that verification exposes a gap.
