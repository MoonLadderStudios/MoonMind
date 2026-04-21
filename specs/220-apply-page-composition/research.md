# Research: Mission Control Page-Specific Task Workflow Composition

## FR-001 / FR-002 / DESIGN-REQ-014 / DESIGN-REQ-019

Decision: Treat task-list composition as implemented and verified by prior MM-426 work, with regression coverage required in this story.  
Evidence: `frontend/src/entrypoints/tasks-list.tsx` exposes `.task-list-control-deck.panel--controls` and `.task-list-data-slab.panel--data`; `frontend/src/entrypoints/tasks-list.test.tsx` checks composition, chips, pagination, mobile cards, and sticky posture.  
Rationale: The current code directly matches section 11.1 and the MM-428 acceptance criteria for `/tasks/list`.  
Alternatives considered: Reworking task-list layout again; rejected because it would expand scope and risk regressions.  
Test implications: Run targeted task-list UI tests as regression evidence.

## FR-003 / FR-004 / FR-005 / DESIGN-REQ-020

Decision: Treat create-page structure as mostly implemented but add explicit MM-428 tests for guided step-card flow, one floating launch rail, launch CTA, and matte textareas.  
Evidence: `frontend/src/entrypoints/task-create.tsx` has `data-canonical-create-section="Steps"`, `.card.queue-steps-section`, `.queue-step-section`, `data-canonical-create-section="Submit"`, and `.queue-floating-bar.queue-floating-bar--liquid-glass`; existing `task-create.test.tsx` verifies submit controls stay in the floating bar.  
Rationale: The UI likely satisfies the behavior, but the current tests do not name all MM-428 requirements in one route-specific composition test.  
Alternatives considered: Changing create-page markup immediately; rejected until tests expose an actual gap.  
Test implications: Add focused create-page test first; implement only if it fails for a real requirement gap.

## FR-006 / FR-007 / FR-008 / DESIGN-REQ-017 / DESIGN-REQ-021

Decision: Add explicit task-detail/evidence composition markers and styling, then verify them with focused tests.  
Evidence: `frontend/src/entrypoints/task-detail.tsx` has hero, summary, facts, steps, actions, artifacts, timeline, and live logs sections, but many sections use generic `section className="stack"` and dense table wrappers without route-specific composition markers.  
Rationale: The route is functionally separated, but MM-428 requires clear composition semantics and no competing glass effects for evidence-heavy regions.  
Alternatives considered: Leaving generic sections and relying on text headings; rejected because tests would be brittle and the design contract would remain implicit.  
Test implications: Add tests that assert summary, facts, steps, actions, artifacts/timeline/logs use explicit task-detail composition classes and matte evidence classes.

## FR-009

Decision: Preserve existing behavior by running focused task-list/create/detail tests after changes.  
Evidence: Existing Vitest files cover request/query shape, task submission payloads, task detail actions, live logs, and artifact behavior.  
Rationale: The story is visual/compositional and must not change payload or API behavior.  
Alternatives considered: New backend integration tests; rejected because backend contracts are unchanged.  
Test implications: Run targeted UI test suite and final unit wrapper when feasible.

## FR-010 / FR-011

Decision: Preserve MM-428 traceability in all MoonSpec artifacts and verification; add tests that cover all three page families.  
Evidence: `docs/tmp/jira-orchestration-inputs/MM-428-moonspec-orchestration-input.md` and `spec.md` preserve the trusted Jira brief.  
Rationale: Jira Orchestrate requires the issue key to flow into downstream artifacts and PR metadata.  
Alternatives considered: Relying only on final PR text; rejected because MoonSpec verification needs artifact-level evidence.  
Test implications: Final verification checks artifacts and source IDs.
