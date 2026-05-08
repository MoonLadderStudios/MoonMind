# Tasks: Refresh Managed Runtimes After Derived Skill Activation

**Input**: Design documents from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/skill-activation-refresh-contract.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped around exactly one user story: Activation-Ready Derived Skill Snapshot Refresh.

**Source Traceability**: MM-615 and the canonical Jira preset brief are preserved in `spec.md`; tasks cover FR-001 through FR-013, acceptance scenarios 1-6, edge cases, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-008.

**Requirement Status Summary**:

- Code and tests required for missing/partial items: FR-002, FR-003, FR-005, FR-007, FR-010, FR-012, SC-002, SC-003, SC-005, DESIGN-REQ-001, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-007, DESIGN-REQ-008
- Verification tests plus conditional fallback implementation for implemented_unverified items: FR-001, FR-004, FR-008, FR-009, FR-011, FR-013, SC-001, SC-004, SC-006, SC-007, DESIGN-REQ-002, DESIGN-REQ-005, DESIGN-REQ-006
- Already verified by existing evidence: FR-006

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Focused unit iteration: `pytest tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py -q`
- Focused integration iteration: `pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -m integration_ci -q`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing test and artifact structure for the one-story implementation.

- [ ] T001 Confirm the MM-615 source, plan status table, and contract are current in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/spec.md`, `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/plan.md`, and `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/contracts/skill-activation-refresh-contract.md` for FR-013 and SC-007
- [ ] T002 Confirm existing unit and integration test files are the implementation targets in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/services/test_skill_materialization.py`, `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py`, and `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/integration/temporal/test_skills_on_demand_request_activation.py` for FR-001 through FR-012

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the shared contract and fixtures needed before story tests and implementation.

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [ ] T003 [P] Add shared unit fixture helpers for digest-verifiable Skill bundles and materialization failure simulation in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/services/test_skill_materialization.py` for FR-002, FR-003, SC-002, and DESIGN-REQ-003
- [ ] T004 [P] Add shared activity fixture helpers for real or faithful derived snapshot materialization in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/integration/temporal/test_skills_on_demand_request_activation.py` for FR-001, FR-004, SC-001, and DESIGN-REQ-003
- [ ] T005 [P] Add shared result-serialization assertions in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` for FR-008, SC-004, DESIGN-REQ-002, and DESIGN-REQ-005

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Activation-Ready Derived Skill Snapshot Refresh

**Summary**: As a managed runtime, I want MoonMind to materialize an approved derived Skill snapshot and provide a compact activation update only after the new bundle is ready so that I never observe a partially active Skill set.

**Independent Test**: Exercise the managed-runtime Skill activation refresh path with approved derived snapshots, materialization failures, runtime refresh failures, and external-agent contexts; verify that activation is announced only after a complete ready snapshot exists, failures preserve the current snapshot, activation output stays compact, and external agents receive no v1 Skills On Demand exposure.

**Traceability**: FR-001 through FR-013; acceptance scenarios 1-6; edge cases for partial reads, checksum failure, large bundles, refresh retry, and repo/local overlays; SC-001 through SC-007; DESIGN-REQ-001 through DESIGN-REQ-008.

**Test Plan**:

- Unit: materializer checksum and staging rules, compact result serialization, activation timing metadata, failure code classification, external-agent exclusion, projection ownership guardrails.
- Integration: `agent_skill.request_on_demand` success and failure paths, real materialization-before-activation evidence, runtime refresh failure diagnostics, deferred activation guidance, adapter-visible projection safety.

### Unit Tests (write first) ⚠️

> Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [ ] T006 [P] Add failing unit tests for manifest and content checksum verification before activation in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/services/test_skill_materialization.py` for FR-002, SC-001, SC-002, DESIGN-REQ-001, and DESIGN-REQ-003
- [ ] T007 [P] Add failing unit tests proving partial writes and retry/same-snapshot writes do not expose partial `.agents/skills` projection in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/services/test_skill_materialization.py` for FR-003, SC-002, DESIGN-REQ-001, and DESIGN-REQ-008
- [ ] T008 [P] Add failing unit tests for compact activation timing metadata and no Skill body or unrestricted ref leakage in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` for FR-004, FR-005, FR-008, SC-003, SC-004, DESIGN-REQ-002, and DESIGN-REQ-005
- [ ] T009 [P] Add failing unit tests distinguishing `materialization_failed` from `runtime_refresh_failed` result diagnostics in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` for FR-007, FR-012, SC-005, and DESIGN-REQ-007
- [ ] T010 [P] Add failing unit test proving external-agent Skills On Demand activation is unavailable in v1 in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/workflows/adapters/test_base_external_agent_adapter.py` for FR-009, SC-006, and DESIGN-REQ-006
- [ ] T011 [P] Add failing unit tests proving runtime adapters cannot broaden active Skill sets or publish repo-authored `.agents/skills` projection changes in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/services/test_skill_materialization.py` for FR-010, FR-011, DESIGN-REQ-001, and DESIGN-REQ-008

### Integration Tests (write first) ⚠️

- [ ] T012 [P] Add failing integration test that `agent_skill.request_on_demand` returns `activated` only after real materialization and verification succeed in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/integration/temporal/test_skills_on_demand_request_activation.py` for FR-001, FR-002, FR-004, SC-001, and acceptance scenario 1
- [ ] T013 [P] Add failing integration test for partial write or checksum verification failure preserving the current active snapshot in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/integration/temporal/test_skills_on_demand_request_activation.py` for FR-003, FR-006, SC-002, and acceptance scenario 4
- [ ] T014 [P] Add failing integration test for non-atomic projection support returning next-turn or controlled-steer-point activation guidance in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/integration/temporal/test_skills_on_demand_request_activation.py` for FR-005, SC-003, and acceptance scenario 3
- [ ] T015 [P] Add failing integration test for post-materialization runtime refresh failure preserving the current active snapshot and returning `runtime_refresh_failed` in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/integration/temporal/test_skills_on_demand_request_activation.py` for FR-007, FR-012, SC-005, and acceptance scenario 5
- [ ] T016 [P] Add failing integration test proving repo-authored Skill sources and local-only overlays remain separate from runtime projection state during refresh in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/integration/temporal/test_skills_on_demand_request_activation.py` for FR-010, FR-011, DESIGN-REQ-008, and the repo/local overlay edge case

### Red-First Confirmation ⚠️

- [ ] T017 Run `pytest tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py -q` from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo` and confirm T006-T011 fail for the expected missing MM-615 behavior
- [ ] T018 Run `pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -m integration_ci -q` from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo` and confirm T012-T016 fail for the expected missing MM-615 behavior

### Conditional Fallback Implementation for Implemented-Unverified Rows

- [ ] T019 If T012 shows activation can return before real materialization verification, update `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/workflows/agent_skills/agent_skills_activities.py` for FR-001, FR-004, SC-001, and DESIGN-REQ-003
- [ ] T020 If T008 shows compact activation output leaks bodies or unrestricted refs, update `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/services/skills_on_demand.py` and `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/schemas/agent_skill_models.py` for FR-008, SC-004, DESIGN-REQ-002, and DESIGN-REQ-005
- [ ] T021 If T010 shows external-agent activation exposure exists, update `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/workflows/adapters/base_external_agent_adapter.py` for FR-009, SC-006, and DESIGN-REQ-006
- [ ] T022 If T011 shows repo-authored projection changes can be treated as authored source changes, update `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/services/skill_materialization.py` and `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/workflows/skills/workspace_links.py` for FR-011 and DESIGN-REQ-008

### Implementation

- [ ] T023 Implement manifest and content digest verification before materialized snapshots can be activation-ready in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/services/skill_materialization.py` for FR-002, SC-001, SC-002, DESIGN-REQ-001, and DESIGN-REQ-003
- [ ] T024 Implement staged or equivalent atomic projection safety so managed runtimes cannot observe partial `.agents/skills` contents in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/services/skill_materialization.py` and `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/workflows/skills/workspace_links.py` for FR-003, SC-002, and acceptance scenario 2
- [ ] T025 Implement compact activation timing metadata for atomic, next-turn, or controlled-steer-point activation in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/schemas/agent_skill_models.py` and `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/services/skills_on_demand.py` for FR-005, SC-003, DESIGN-REQ-002, and DESIGN-REQ-004
- [ ] T026 Implement post-materialization runtime refresh failure classification and safe diagnostics in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/workflows/agent_skills/agent_skills_activities.py` and `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/services/skills_on_demand.py` for FR-007, FR-012, SC-005, and DESIGN-REQ-007
- [ ] T027 Implement adapter-boundary guardrails that prevent independent active Skill broadening and preserve repo/local sources during refresh in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/services/skill_materialization.py`, `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/workflows/skills/workspace_links.py`, and `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/workflows/agent_skills/agent_skills_activities.py` for FR-010, FR-011, DESIGN-REQ-001, and DESIGN-REQ-008
- [ ] T028 Update runtime-facing activation result contract handling in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/moonmind/services/skills_on_demand.py` so successful and failed results match `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/contracts/skill-activation-refresh-contract.md` for FR-004, FR-008, FR-012, and acceptance scenarios 1, 4, and 5

### Story Validation

- [ ] T029 Run `pytest tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/workflows/adapters/test_base_external_agent_adapter.py -q` from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo` and verify all MM-615 unit tests pass for FR-002 through FR-012
- [ ] T030 Run `pytest tests/integration/temporal/test_skills_on_demand_request_activation.py -m integration_ci -q` from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo` and verify all MM-615 integration scenarios pass for acceptance scenarios 1-6 and SC-001 through SC-006
- [ ] T031 Run `./tools/test_unit.sh` from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo` for final unit verification of MM-615 and related Skill materialization behavior
- [ ] T032 Run `./tools/test_integration.sh` from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo` for final hermetic integration verification of MM-615 activity/materialization boundaries

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and independently testable.

---

## Phase 4: Polish & Verification

**Purpose**: Strengthen the completed story without adding hidden scope.

- [ ] T033 [P] Update `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/data-model.md` if implementation changes activation metadata or failure diagnostic fields for FR-013 and SC-007
- [ ] T034 [P] Update `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/contracts/skill-activation-refresh-contract.md` if implementation changes the compact activation contract for FR-013 and SC-007
- [ ] T035 [P] Review serialized activation outputs in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` for secrets, Skill bodies, and unrestricted refs for FR-008 and DESIGN-REQ-008
- [ ] T036 Run the quickstart validation steps from `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/quickstart.md` and record any deviations in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/verification.md` for SC-001 through SC-007
- [ ] T037 Run `/moonspec-verify` for `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/spec.md` and preserve MM-615 plus the canonical Jira preset brief in `/work/agent_jobs/mm:a3430c58-ca43-4dec-9edc-8f9c304abd0a/repo/specs/318-refresh-managed-runtimes-after-derived-skill-activation/verification.md` for FR-013 and SC-007

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story test and implementation work.
- **Story (Phase 3)**: Depends on Foundational phase completion.
- **Polish (Phase 4)**: Depends on story implementation, focused tests, full unit verification, and full integration verification.

### Within The Story

- Unit tests T006-T011 must be written before implementation.
- Integration tests T012-T016 must be written before implementation.
- Red-first confirmations T017-T018 must complete before production code tasks T019-T028.
- Conditional fallback tasks T019-T022 are skipped only when verification tests pass without production changes.
- Implementation tasks T023-T028 must complete before story validation T029-T032.
- Final `/moonspec-verify` T037 runs only after implementation and tests pass.

### Parallel Opportunities

- T003-T005 can run in parallel after T001-T002.
- T006-T011 can run in parallel after Phase 2 because they touch separate or independently scoped test files.
- T012-T016 can run in parallel after Phase 2 because they add separate integration scenarios in the same file but do not depend on each other conceptually; coordinate edits to avoid conflicts.
- T019-T022 can run in parallel only if their corresponding verification tests fail and file edits are coordinated.
- T033-T035 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
# Launch independent test authoring together:
Task: "T006 Add failing unit tests for manifest and content checksum verification in tests/unit/services/test_skill_materialization.py"
Task: "T008 Add failing unit tests for compact activation timing metadata in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py"
Task: "T010 Add failing unit test for external-agent v1 exclusion in tests/unit/workflows/adapters/test_base_external_agent_adapter.py"

# Launch integration scenario authoring with coordinated same-file edits:
Task: "T012 Add successful activation-after-verification integration scenario"
Task: "T015 Add runtime_refresh_failed integration scenario"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 setup/fixtures.
2. Write unit tests T006-T011 and integration tests T012-T016 first.
3. Run T017-T018 and confirm the new tests fail for the expected MM-615 behavior.
4. Execute conditional fallback implementation T019-T022 only where implemented_unverified checks fail.
5. Implement missing and partial behavior in T023-T028.
6. Run focused validation T029-T030, then final unit and integration runners T031-T032.
7. Complete polish and final `/moonspec-verify` T033-T037.

### Coverage Notes

- FR-006 is already implemented_verified in `plan.md`; it is covered by regression validation T013, T030, T031, T032, and T037 without new implementation work.
- Implemented_unverified rows receive verification tests and conditional fallback tasks.
- Missing and partial rows receive red-first tests and implementation tasks.
- MM-615 traceability is preserved through T001, T033, T034, T036, and T037.

---

## Notes

- This task list covers one story only.
- Do not create `plan.md`, `spec.md`, implementation code, commits, PRs, Jira transitions, or verification output during task generation.
- Use `.agents/skills` only as MoonMind-owned runtime projection state during implementation; do not mutate repo-authored `.agents/skills` sources as runtime setup.
