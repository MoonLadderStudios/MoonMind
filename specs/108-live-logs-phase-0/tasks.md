# Tasks: Live Logs Phase 0

## Phase 1: Setup
- [x] T001 Establish phase 0 codebase target directories based on architecture plan.

## Phase 2: Foundational
- [x] T002 Aggregate base context surrounding `tmate` and legacy log streams into `specs/108-live-logs-phase-0/research.md`.

## Phase 3: Codebase Assessment and Auditing
- [x] T003 [P] [US1] Inventory legacy observer references (`tmate/web_ro`) for DOC-REQ-002 in `specs/108-live-logs-phase-0/research.md`.
- [x] T004 [P] [US1] Validate inventory check DOC-REQ-002 by confirming script evaluation.
- [x] T005 [P] [US1] Inventory UI surfaces for DOC-REQ-003 targeting "Live Output" semantics in `specs/108-live-logs-phase-0/research.md`.
- [x] T006 [P] [US1] Validate UI surface inventory check DOC-REQ-003 via `yarn typecheck` analog.
- [x] T007 [P] [US1] Identify DB models storing metadata for DOC-REQ-004 in `api_service/db/models.py`.
- [x] T008 [P] [US1] Validate model inventory DOC-REQ-004 manually.
- [x] T009 [P] [US1] Map artifact-writing paths for logs/stdout/stderr for DOC-REQ-005 in `specs/108-live-logs-phase-0/research.md`.
- [x] T010 [US1] Validate log paths DOC-REQ-005 manually.

## Phase 4: Architectural Definition and Strategy
- [x] T011 [US2] Define backend observability service boundary path for DOC-REQ-006 inside `specs/108-live-logs-phase-0/research.md`.
- [x] T012 [US2] Validate observability boundary via unit test: `run test:unit`.
- [x] T013 [P] [US2] Implement `logStreamingEnabled` feature flag in `moonmind/config/settings.py` for DOC-REQ-007.
- [x] T014 [US2] Validate feature flag definition by running pytest: `./tools/test_unit.sh`.
- [x] T015 [US2] Design migration boundary decoupling legacy sessions for DOC-REQ-008 inside `specs/108-live-logs-phase-0/research.md`.
- [x] T016 [US2] Validate migration boundary fallback logic structurally via review.

## Phase 5: Documentation Parity and Blueprint
- [x] T017 [US3] Enforce `docs/ManagedAgents/LiveLogs.md` as canonical source matching DOC-REQ-001 by updating `docs/README.md`.
- [x] T018 [US3] Validate canonical reference logic DOC-REQ-001 via `git diff`.
- [x] T019 [US3] Scrub outdated `tmate/web_ro` dependencies from documentation for DOC-REQ-009 inside `docs/`.
- [x] T020 [US3] Validate documentation cleanup DOC-REQ-009 via `grep`.
- [x] T021 [US3] Emit implementation phase planning issues for DOC-REQ-010 updating `docs/ManagedAgents/LiveLogs.md`.
- [x] T022 [US3] Validate future plan issues for DOC-REQ-010 via checking `.specify/scripts/bash/validate-implementation-scope.sh`.
