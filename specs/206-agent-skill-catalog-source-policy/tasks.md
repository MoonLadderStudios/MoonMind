# Tasks: Agent Skill Catalog and Source Policy

**Input**: Design documents from `specs/206-agent-skill-catalog-source-policy/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/agent-skill-source-policy.md, quickstart.md

**Tests**: Unit tests and boundary/integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-405 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: Coverage includes FR-001 through FR-012, acceptance scenarios 1-5, edge cases, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-004 from `spec.md`.

**Requirement Status Summary**: FR-008, FR-009, FR-010, DESIGN-REQ-004, and SC-004 require code-and-test work. FR-004, DESIGN-REQ-002, and SC-002 require verification tests with conditional fallback only if existing behavior fails. FR-001, FR-002, FR-003, FR-005, FR-006, FR-007, FR-011, FR-012, DESIGN-REQ-001, DESIGN-REQ-003, SC-001, SC-003, SC-005, SC-006, and SC-007 are already implemented or verified and remain covered by final validation.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py tests/unit/api/test_agent_skills_service.py tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py`
- Integration tests: `./tools/test_integration.sh` when Docker is available
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm existing feature structure and test surfaces before touching behavior.

- [X] T001 Confirm feature artifacts are present in `specs/206-agent-skill-catalog-source-policy/spec.md`, `specs/206-agent-skill-catalog-source-policy/plan.md`, `specs/206-agent-skill-catalog-source-policy/research.md`, `specs/206-agent-skill-catalog-source-policy/data-model.md`, `specs/206-agent-skill-catalog-source-policy/contracts/agent-skill-source-policy.md`, and `specs/206-agent-skill-catalog-source-policy/quickstart.md` for MM-405 traceability.
- [X] T002 Inspect existing agent-skill resolver, service, materializer, and activity test files in `moonmind/services/skill_resolution.py`, `api_service/services/agent_skills_service.py`, `moonmind/services/skill_materialization.py`, `moonmind/workflows/agent_skills/agent_skills_activities.py`, `tests/unit/services/test_skill_resolution.py`, `tests/unit/api/test_agent_skills_service.py`, `tests/unit/services/test_skill_materialization.py`, and `tests/unit/workflows/agent_skills/test_agent_skills_activities.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the exact existing behavior and policy gap before story tests and implementation.

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [X] T003 Map current resolver policy inputs in `moonmind/services/skill_resolution.py` against `specs/206-agent-skill-catalog-source-policy/contracts/agent-skill-source-policy.md` for FR-008, FR-009, FR-010, and DESIGN-REQ-004.
- [X] T004 Confirm existing immutable version and materialization evidence in `tests/unit/api/test_agent_skills_service.py` and `tests/unit/services/test_skill_materialization.py` for FR-004, FR-011, DESIGN-REQ-002, and SC-006.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Agent Skill Catalog and Source Policy

**Summary**: As a MoonMind operator, I want agent skills modeled as deployment-scoped, versioned instruction data with explicit source precedence and policy gates so managed runs do not confuse instruction bundles with executable tools or silently trust repo-local content.

**Independent Test**: Resolve agent-skill selections with mixed built-in, deployment, repo, and local candidates under allowed and denied policies, then verify the output contains only policy-allowed entries with provenance and immutable snapshot behavior.

**Traceability**: FR-001 through FR-012, acceptance scenarios 1-5, edge cases, SC-001 through SC-007, DESIGN-REQ-001 through DESIGN-REQ-004, MM-405.

**Test Plan**:

- Unit: resolver source policy gates, source precedence, immutable version preservation, materialization snapshot path.
- Boundary/Integration: activity-level resolution preserves policy decisions and produces compact resolved snapshots; final hermetic integration suite runs when Docker is available.

### Unit Tests (write first)

> NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [X] T005 [P] Add failing unit test proving repo skill candidates are excluded when repo sources are policy-denied in `tests/unit/services/test_skill_resolution.py` covering FR-008, FR-009, FR-010, SC-004, and DESIGN-REQ-004.
- [X] T006 [P] Add failing unit test proving repo skill candidates participate in precedence when repo sources are policy-allowed in `tests/unit/services/test_skill_resolution.py` covering FR-005, FR-006, FR-007, SC-003, SC-005, and DESIGN-REQ-003.
- [X] T007 [P] Add failing unit test proving resolver policy summary reports repo and local source policy decisions in `tests/unit/services/test_skill_resolution.py` covering FR-007, FR-008, FR-009, and DESIGN-REQ-004.
- [X] T008 [P] Add verification unit test proving creating a later deployment skill version preserves the earlier version row and artifact metadata in `tests/unit/api/test_agent_skills_service.py` covering FR-004, SC-002, and DESIGN-REQ-002.
- [X] T009 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py tests/unit/api/test_agent_skills_service.py` and confirm T005-T007 fail for missing repo policy behavior while T008 either passes or identifies a real immutability gap.

### Boundary and Integration Tests (write first)

- [X] T010 [P] Add failing activity-boundary test proving `agent_skill.resolve` carries repo/local policy into `ResolvedSkillSet.policy_summary` in `tests/unit/workflows/agent_skills/test_agent_skills_activities.py` covering acceptance scenarios 3-5, FR-008, FR-009, FR-010, and DESIGN-REQ-004.
- [X] T011 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/agent_skills/test_agent_skills_activities.py` and confirm T010 fails for the expected missing policy propagation reason before production implementation.

### Implementation

- [X] T012 Add explicit repo-source policy input to `SkillResolutionContext` in `moonmind/services/skill_resolution.py` covering FR-008, FR-009, FR-010, and DESIGN-REQ-004.
- [X] T013 Update `RepoSkillLoader` in `moonmind/services/skill_resolution.py` so repo candidates are not loaded when repo sources are denied, while preserving existing allowed-source precedence for FR-005, FR-006, FR-008, FR-009, and SC-004.
- [X] T014 Update resolver `policy_summary` in `moonmind/services/skill_resolution.py` to record repo and local source policy decisions for FR-007, FR-008, FR-009, and SC-005.
- [X] T015 Update `AgentSkillsActivities.resolve_skills` in `moonmind/workflows/agent_skills/agent_skills_activities.py` to pass explicit repo/local policy into `SkillResolutionContext` without embedding large skill content in workflow history, covering FR-008, FR-010, FR-011, and DESIGN-REQ-004.
- [X] T016 Conditional fallback: if T008 exposes an immutability gap, update `api_service/services/agent_skills_service.py` so creating a new version preserves previous AgentSkillVersion rows and artifact metadata for FR-004 and DESIGN-REQ-002.
- [X] T017 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py tests/unit/api/test_agent_skills_service.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py` and fix only MM-405-scoped failures.

### Story Validation

- [X] T018 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/test_skill_resolution.py tests/unit/api/test_agent_skills_service.py tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py` to validate FR-001 through FR-012, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-004.
- [X] T019 Run `rg -n "MM-405|DESIGN-REQ-001|DESIGN-REQ-002|DESIGN-REQ-003|DESIGN-REQ-004" specs/206-agent-skill-catalog-source-policy docs/tmp/jira-orchestration-inputs/MM-405-moonspec-orchestration-input.md` to validate source traceability for FR-012 and SC-007.

**Checkpoint**: The story is fully functional, covered by focused tests and traceability checks, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T020 Review `docs/Tasks/AgentSkillSystem.md` and `docs/Tasks/SkillAndPlanContracts.md` for consistency with implemented MM-405 behavior; update only if the runtime behavior reveals a mismatch, covering FR-001, FR-002, DESIGN-REQ-001, and DESIGN-REQ-004.
- [X] T021 Run final unit suite `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and fix only MM-405-scoped failures.
- [X] T022 Run required hermetic integration suite `./tools/test_integration.sh` when Docker is available; if unavailable, record the exact Docker availability blocker in `specs/206-agent-skill-catalog-source-policy/verification.md`.
- [X] T023 Run `/speckit.verify` against `specs/206-agent-skill-catalog-source-policy/spec.md` after implementation and tests pass, then record the final verdict and evidence in `specs/206-agent-skill-catalog-source-policy/verification.md`.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing.

### Within The Story

- T005-T008 must be written before T009.
- T009 and T011 red-first confirmations must complete before T012-T016.
- T012-T015 implement the missing repo policy behavior and activity-boundary propagation.
- T016 runs only if T008 exposes a real immutability gap.
- T017-T019 validate the story before Phase 4.

### Parallel Opportunities

- T005, T006, T007, and T008 can be authored in parallel if contributors coordinate changes to `tests/unit/services/test_skill_resolution.py`.
- T010 can be authored in parallel with T005-T008 because it touches `tests/unit/workflows/agent_skills/test_agent_skills_activities.py`.
- T020 can run after story validation and does not block T021 unless it changes documentation.

---

## Parallel Example: Story Phase

```bash
# Launch independent test authoring together:
Task: "Add repo policy resolver tests in tests/unit/services/test_skill_resolution.py"
Task: "Add activity-boundary policy test in tests/unit/workflows/agent_skills/test_agent_skills_activities.py"
Task: "Add immutable version preservation test in tests/unit/api/test_agent_skills_service.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 inspection.
2. Write resolver and service tests first.
3. Confirm repo policy tests fail for the expected missing behavior.
4. Add explicit repo-source policy to resolver context and repo loader behavior.
5. Propagate policy through the activity boundary.
6. Re-run focused tests until they pass.
7. Run final unit, integration when available, traceability, and `/speckit.verify`.

### Requirement Status Handling

- Code-and-test work: FR-008, FR-009, FR-010, DESIGN-REQ-004, SC-004.
- Verification-only with conditional fallback: FR-004, DESIGN-REQ-002, SC-002.
- Already verified, preserved by final validation: FR-001, FR-002, FR-003, FR-005, FR-006, FR-007, FR-011, FR-012, DESIGN-REQ-001, DESIGN-REQ-003, SC-001, SC-003, SC-005, SC-006, SC-007.

---

## Notes

- This task list covers exactly one story: MM-405 Agent Skill Catalog and Source Policy.
- Preserve MM-405 in implementation notes, verification output, commit text, and pull request metadata.
- Do not add compatibility aliases or hidden fallbacks for source policy decisions.
- Do not mutate checked-in `.agents/skills` folders during runtime materialization.
