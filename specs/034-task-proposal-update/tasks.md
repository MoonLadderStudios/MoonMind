# Tasks: Task Proposal Targeting Policy

**Input**: Design documents from `/specs/034-task-proposal-update/`
**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Validation tasks are explicitly listed per user story to keep DOC-REQ coverage measurable.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Ensure documentation/config templates advertise the new knobs so later code changes remain traceable.

- [X] T001 Update `api_service/config.template.toml` with `MOONMIND_PROPOSAL_TARGETS` and `MOONMIND_CI_REPOSITORY` samples so deployers configure policy defaults early (DOC-REQ-003, DOC-REQ-004).
- [X] T002 Add a short "Policy Overview" subsection to `docs/TaskQueueSystem.md` referencing `proposalPolicy.targets` and MoonMind CI routing to align operator docs with DOC-REQ-003/004 expectations.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Introduce shared schema/config hooks before user-story-specific logic lands.

- [X] T003 Implement policy settings (`proposal_targets_default`, `moonmind_ci_repository`) in `moonmind/config/settings.py` plus wire them into `SpecWorkflowSettings`/`AppSettings`, including documented default slot counts (`project=3`, `moonmind=2`) and severity vocabulary (`low|medium|high|critical` with a default MoonMind floor of `high`) (DOC-REQ-003, DOC-REQ-004, DOC-REQ-013).
- [X] T004 Extend `moonmind/workflows/agent_queue/task_contract.py` to accept/validate `task.proposalPolicy` (targets, maxItems, minSeverityForMoonMind), persist it into canonical payloads, and compute an `EffectiveProposalPolicy` that falls back to defaults/logs when overrides are absent (DOC-REQ-005, DOC-REQ-009, DOC-REQ-010, DOC-REQ-013).
- [X] T005 Update shared schemas (`moonmind/schemas/agent_queue_models.py` for `CreateJobRequest`, `moonmind/schemas/task_proposal_models.py` for optional `reviewPriority` + `priority_override_reason`) so API inputs/outputs reflect the new contract (DOC-REQ-005, DOC-REQ-007, DOC-REQ-010).

**Checkpoint**: Policy data can now flow end-to-end through normalized payloads.

---

## Phase 3: User Story 1 - Workers select proposal targets dynamically (Priority: P1) 🎯 MVP

**Goal**: Workers honor policy defaults/overrides and emit project + MoonMind proposals with severity-aware gating.

**Independent Test**: Run `pytest tests/unit/agents/codex_worker/test_worker.py` to confirm `proposalPolicy` merging, severity gating, and `maxItems` enforcement without touching API/service pieces.

### Implementation for User Story 1

- [X] T006 [US1] Refactor `moonmind/agents/codex_worker/worker.py` to parse `proposalPolicy` from canonical payloads, merge with env defaults, and branch proposal generation per target (DOC-REQ-003, DOC-REQ-005, DOC-REQ-009).
- [X] T007 [US1] Inject MoonMind CI repository rewriting + signal-tag normalization when `targets` include `moonmind`, ensuring `taskCreateRequest.payload.repository` equals `MOONMIND_CI_REPOSITORY` and `[run_quality]` titles/tags (with sorted signal-tag slug appended to the normalized title) are set before submission (DOC-REQ-004, DOC-REQ-006, DOC-REQ-013).
- [X] T008 [US1] Attach origin metadata (`triggerRepo`, `triggerJobId`, optional `triggerStepId`, `signal` payload) using `PreparedTaskWorkspace` context prior to posting proposals (DOC-REQ-008).
- [X] T009 [US1] Add guardrails in `moonmind/workflows/task_proposals/service.py` and `moonmind/workflows/task_proposals/repositories.py` so dedup + human-review semantics remain untouched when policy rewrites occur, including the new normalized-title definition (`[run_quality] … (tags: <sorted slug>)`) for MoonMind proposals (DOC-REQ-001, DOC-REQ-002, DOC-REQ-013).

### Validation for User Story 1

- [X] T010 [US1] Expand `tests/unit/agents/codex_worker/test_worker.py` with fixtures covering project-only, moonmind-only, dual-target, severity-below-threshold, and env-default-only scenarios (no `proposalPolicy`, env toggles between `project`/`moonmind`/`both`) (DOC-REQ-003, DOC-REQ-004, DOC-REQ-005, DOC-REQ-009, DOC-REQ-013).
- [X] T011 [US1] Add regression assertions to `tests/unit/workflows/task_proposals/test_service.py` (or new worker-focused tests) ensuring dedup keys + human-review gating still match legacy behavior after worker rewrites, including multi-signal MoonMind proposals that only differ by tag slug (DOC-REQ-001, DOC-REQ-002, DOC-REQ-013).

**Checkpoint**: Worker can emit compliant proposals + skip noise without API changes.

---

## Phase 4: User Story 2 - Reviewers triage MoonMind CI improvements (Priority: P2)

**Goal**: API/service/dashboard enforce normalized metadata so CI reviewers can filter and prioritize proposals quickly.

**Independent Test**: Run FastAPI + JS unit smoke tests (pytest modules + manual dashboard check) to ensure MoonMind submissions fail without metadata and dashboard filters surface new attributes.

### Implementation for User Story 2

- [X] T012 [US2] Update `moonmind/workflows/task_proposals/service.py` to enforce `[run_quality]` category/titles, allow only approved tags, require origin metadata fields, derive `reviewPriority` from signal severity, and persist a `priority_override_reason` whenever escalation occurs (DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-012, DOC-REQ-013).
- [X] T013 [US2] Enhance `api_service/api/routers/task_proposals.py` so `POST /api/proposals` accepts optional `reviewPriority`, surfaces validation errors for missing MoonMind metadata, passes normalized payloads downstream, and returns the derived priority + override reason in responses for UI consumption (DOC-REQ-006, DOC-REQ-008, DOC-REQ-007, DOC-REQ-011).
- [X] T014 [US2] Upgrade `api_service/static/task_dashboard/dashboard.js` with repository/category/tag filter controls plus UI display of origin metadata, derived priority badges, and override-reason tooltips (DOC-REQ-011, DOC-REQ-013).

### Validation for User Story 2

- [X] T015 [US2] Extend `tests/unit/workflows/task_proposals/test_service.py` to cover metadata enforcement, priority derivation (loop detection, retry exhaustion, missing refs), and persistence of the `priority_override_reason` field (DOC-REQ-006, DOC-REQ-007, DOC-REQ-008, DOC-REQ-013).
- [X] T016 [US2] Expand `tests/unit/api/routers/test_task_proposals.py` with worker-token + user-auth payloads to verify API validation errors fire when MoonMind metadata/tags are missing (DOC-REQ-006, DOC-REQ-008).
- [ ] T017 [US2] Manually verify the dashboard by rebuilding assets (`npm run dashboard:css`) and checking repository/category/tag filters, origin metadata panes, derived priority badges, and override tooltip text in `api_service/static/task_dashboard/dashboard.js` (DOC-REQ-011, DOC-REQ-013).

**Checkpoint**: Reviewers can discover + triage MoonMind CI proposals with normalized metadata.

---

## Phase 5: User Story 3 - Platform engineers audit schema/config updates (Priority: P3)

**Goal**: Keep schemas, docs, and quickstart guidance aligned so deployments remain reproducible.

**Independent Test**: Run `./tools/test_unit.sh` plus linting to ensure config/schema changes pass across modules.

### Implementation for User Story 3

- [X] T018 [US3] Wire new policy settings into `SpecWorkflowSettings`/`AppSettings` bridging logic so `CodexWorkerConfig.from_env` inherits overrides (DOC-REQ-010).
- [X] T019 [US3] Update `docs/TaskProposalQueue.md` quickstart section with explicit instructions for configuring `proposalPolicy` overrides and MoonMind CI signals (DOC-REQ-010, DOC-REQ-012).
- [X] T020 [US3] Refresh `specs/034-task-proposal-update/quickstart.md` (and link from README if needed) to document validation steps (DOC-REQ-010, DOC-REQ-012).

### Validation for User Story 3

- [X] T021 [US3] Add config parsing tests in `tests/unit/config/test_settings.py` covering env overrides + worker passthroughs for the new policy settings, including scenarios where env values are missing/invalid and defaults (targets + slots + severity floor) apply (DOC-REQ-010, DOC-REQ-013).
- [ ] T022 [US3] Execute `./tools/test_unit.sh` after wiring docs/config to ensure system-wide regression coverage stays green (DOC-REQ-012).

**Checkpoint**: Operators have end-to-end guidance + automated coverage for schema/config updates.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Final cleanup and verification once all user stories land.

- [X] T023 [P] Document release notes in `docs/TaskProposalQueue.md` summarizing policy roll-out impacts across DOC-REQ-001 → DOC-REQ-013.
- [ ] T024 Conduct final manual smoke test by promoting a MoonMind CI proposal through `/api/proposals/{id}/promote` using the dashboard, ensuring dedup + notifications behave as before (DOC-REQ-001, DOC-REQ-002, DOC-REQ-011).

---

## Dependencies & Execution Order

- **Setup (Phase 1)** must finish before code changes so operators know which env vars/config templates to apply.
- **Foundational (Phase 2)** unlocks access to policy data everywhere; no user story can start until schema/config hooks exist.
- **User Story 1** depends on Foundational tasks and delivers the MVP (worker-side behavior). Stories 2 and 3 may begin once Phase 2 is done but should not merge before US1 to keep rollout incremental.
- **User Story 2** builds on US1 outputs (MoonMind proposals) but technically only requires Phase 2; still, implementing US1 first minimizes API enforcement churn.
- **User Story 3** can run parallel to US2 after Phase 2 and should complete before final polish so documentation references real behavior.
- **Polish** runs last to publish cross-cutting notes + smoke tests.

## Parallel Opportunities

- Setup + Foundational tasks labeled without dependencies can be split among different contributors (e.g., one developer handles config template updates while another wires Pydantic schemas).
- After Phase 2, one engineer can focus on worker logic (US1) while another tackles API/dashboard changes (US2); a third can start documentation/tests for platform guidance (US3).
- Validation tasks (pytest suites, npm dashboard build) can run in parallel once their respective code changes exist because they touch separate tools.

## Implementation Strategy

1. Finish Phases 1–2 to introduce config + schema hooks.
2. Deliver User Story 1 as MVP so policy-driven workers exist even if reviewers temporarily fall back to legacy UI filtering.
3. Layer User Story 2 to tighten API enforcement + surface UI filters, unlocking doc acceptance criteria.
4. Wrap with User Story 3 to ensure deployer experience/test harnesses capture the new knobs, then run final polish + smoke tests before handing off.
