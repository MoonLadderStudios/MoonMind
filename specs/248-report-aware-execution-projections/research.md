# Research: Report-Aware Execution Projections

## Story Classification

Decision: Treat MM-496 as a single-story runtime feature request.
Evidence: `docs/tmp/jira-orchestration-inputs/MM-496-moonspec-orchestration-input.md`; `specs/248-report-aware-execution-projections/spec.md`.
Rationale: The brief defines one independently testable runtime outcome: expose bounded report-aware summary data on execution detail without introducing a second report storage model.
Alternatives considered: Broad design breakdown was rejected because the Jira brief already selects one story and does not require processing multiple specs.
Test implications: Unit and execution-API contract tests are both required.

## Execution Detail Summary Fields First, Endpoint Deferred

Decision: Implement report-aware summary fields on `/api/executions/{workflowId}` now and explicitly defer the dedicated report endpoint.
Evidence: `docs/Artifacts/ReportArtifacts.md` §18.1 recommends execution detail convenience fields first, while §18.2 labels the dedicated report endpoint as optional future work.
Rationale: The issue brief asks for execution detail exposure and only treats the endpoint as optional. Choosing the summary-field slice keeps the first implementation bounded and avoids widening the story into a new route contract.
Alternatives considered: Add the dedicated `/report` endpoint immediately. Rejected because it is optional in the source design and would expand the implementation surface beyond the minimal first slice.
Test implications: Contract tests should cover execution detail serialization; no new route-level contract is required in this story.

## Reuse Existing Projection Helper

Decision: Reuse `build_report_projection_summary` from `moonmind/workflows/temporal/report_artifacts.py` rather than inventing a second projection builder inside the API layer.
Evidence: The helper already validates bounded projection keys, compact refs, `has_report`, latest report refs, `report_type`, `report_status`, and bounded `finding_counts` / `severity_counts`.
Rationale: Reusing the helper keeps the API aligned with the canonical report contract and reduces drift between workflow/runtime bundle semantics and execution detail presentation.
Alternatives considered: Reimplement projection shaping in `api_service/api/routers/executions.py`. Rejected because it would duplicate validation and risk diverging semantics.
Test implications: Existing helper tests remain valuable; execution router tests should prove the helper output is surfaced correctly through execution detail.

## ExecutionModel Requires Extension

Decision: Extend the execution detail response contract to carry a bounded report projection object.
Evidence: `ExecutionModel` in `moonmind/schemas/temporal_models.py` currently does not expose report-aware fields, and `api_service/api/routers/executions.py` does not materialize them.
Rationale: The projection must be part of the canonical execution-detail response to satisfy the story for API consumers.
Alternatives considered: Keep the projection only in internal helpers until a future endpoint exists. Rejected because the MM-496 story is specifically about execution detail exposure.
Test implications: Router unit tests and execution API contract tests need explicit coverage for the new response field.

## No New Storage Or Authorization Model

Decision: Keep report-aware execution detail as a convenience read model over existing artifacts only.
Evidence: `docs/Artifacts/ReportArtifacts.md` §18.2 and §21 require projection output to remain a read model over standard artifacts, and existing artifact APIs already own authorization and preview/default-read behavior.
Rationale: Execution detail should surface refs and bounded counts only, leaving artifact content access and restrictions to the existing artifact endpoints and policies.
Alternatives considered: Store a separate execution-level report projection row or inline artifact metadata beyond bounded counts. Rejected because both would violate the artifact-first design.
Test implications: Contract tests should verify only refs and bounded count fields are returned; execution detail must not surface raw artifact payloads.

## Blocker Note

Decision: Preserve MM-497 as an implementation sequencing blocker in feature-local artifacts, but do not let it prevent spec/plan/tasks generation for MM-496.
Evidence: Trusted Jira link metadata in `docs/tmp/jira-orchestration-inputs/MM-496-moonspec-orchestration-input.md` records MM-496 as blocked by MM-497.
Rationale: The user requested MoonSpec artifact generation from the Jira preset brief. Planning can proceed while still recording that implementation sequencing remains externally blocked.
Alternatives considered: Stop before creating planning artifacts. Rejected because the brief itself remains the canonical source for this MoonSpec workflow.
Test implications: Traceability should preserve the blocker note; implementation execution can be deferred until the dependency is resolved.
