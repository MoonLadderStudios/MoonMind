# Tasks: Managed Runtime Skill Projection

**Input**: Design documents from `/specs/208-managed-runtime-skill-projection/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/runtime-skill-projection.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around the single MM-407 story so the work stays focused, traceable, and independently testable.

**Source Traceability**: FR-001 through FR-012, acceptance scenarios 1-5, edge cases, SC-001 through SC-006, DESIGN-REQ-005, DESIGN-REQ-011 through DESIGN-REQ-017, DESIGN-REQ-021.

**Test Commands**:

- Unit tests: `python -m pytest tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py tests/unit/workflows/test_workspace_links.py -q`
- Integration tests: `./tools/test_integration.sh` when Docker is available; otherwise record the Docker/socket blocker because this story is covered by service and activity-boundary unit tests.
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active MoonSpec artifacts and test surfaces.

- [X] T001 Confirm MM-407 is classified as a single-story runtime feature and active artifacts live under `specs/208-managed-runtime-skill-projection/` (FR-012, SC-006)
- [X] T002 Confirm existing runtime skill materialization surfaces in `moonmind/services/skill_materialization.py`, `moonmind/workflows/agent_skills/agent_skills_activities.py`, and `moonmind/workflows/skills/workspace_links.py` (FR-001 through FR-011)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: No new infrastructure is required; existing service and shared link helper are the foundation.

- [X] T003 Confirm `workspace_links.py` already owns shared `.agents/skills` and `.gemini/skills` link validation in `tests/unit/workflows/test_workspace_links.py` (FR-007, DESIGN-REQ-014)
- [X] T004 Confirm no database migrations or new persistent storage are needed in `specs/208-managed-runtime-skill-projection/data-model.md` (FR-010, DESIGN-REQ-015)

**Checkpoint**: Foundation ready - story test and implementation work can begin.

---

## Phase 3: Story - Project Active Skill Snapshot Into Managed Runtime

**Summary**: As a managed runtime adapter, I want a pinned skill snapshot projected into the canonical `.agents/skills` path so the launched agent sees exactly the selected skill set without MoonMind rewriting checked-in skill sources.

**Independent Test**: Materialize one-skill and multi-skill snapshots into temporary managed runtime workspaces, then validate `.agents/skills`, `_manifest.json`, selected skill files, absent unselected skills, source-folder immutability, activity output metadata, and fail-fast incompatible path handling.

**Traceability**: FR-001 through FR-012; acceptance scenarios 1-5; SC-001 through SC-006; DESIGN-REQ-005, DESIGN-REQ-011 through DESIGN-REQ-017, DESIGN-REQ-021.

**Test Plan**:

- Unit: service materialization path, manifest content, selected-only projection, incompatible path diagnostics, prompt summary compactness.
- Integration: Temporal activity boundary through `AgentSkillsActivities.materialize` using supplied `ResolvedSkillSet`; hermetic integration suite only if Docker is available and worker wiring changes.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.**

- [X] T005 [P] Add failing unit tests proving one-skill and multi-skill snapshots expose `.agents/skills/_manifest.json`, selected skill directories, and no unselected repo skills in `tests/unit/services/test_skill_materialization.py` (FR-001, FR-002, FR-005, FR-006, SC-001, SC-002, DESIGN-REQ-005, DESIGN-REQ-012)
- [X] T006 [P] Add failing unit test proving existing checked-in `.agents/skills` source directory is not rewritten and incompatible path diagnostics include path, object kind, attempted action, and remediation in `tests/unit/services/test_skill_materialization.py` (FR-003, FR-008, SC-003, SC-004, DESIGN-REQ-013)
- [X] T007 [P] Add failing unit test proving hybrid materialization returns compact prompt metadata and does not inline full `SKILL.md` bodies in `tests/unit/services/test_skill_materialization.py` (FR-009, FR-010, DESIGN-REQ-011, DESIGN-REQ-015)
- [X] T008 Run `python -m pytest tests/unit/services/test_skill_materialization.py -q` to confirm T005-T007 fail for the expected pre-implementation reasons.

### Integration / Boundary Tests (write first) ⚠️

- [X] T009 [P] Add failing activity-boundary unit test proving `AgentSkillsActivities.materialize` consumes the supplied `ResolvedSkillSet` and returns canonical `.agents/skills` metadata in `tests/unit/workflows/agent_skills/test_agent_skills_activities.py` (FR-004, FR-011, SC-005, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-021)
- [X] T010 Run `python -m pytest tests/unit/workflows/agent_skills/test_agent_skills_activities.py -q` to confirm T009 fails for the expected pre-implementation reason.

### Implementation

- [X] T011 Update `moonmind/services/skill_materialization.py` so workspace-mounted and hybrid modes materialize a run-scoped backing store and project it through canonical `.agents/skills` using the shared link helper (FR-001, FR-002, FR-004, FR-007, DESIGN-REQ-005, DESIGN-REQ-014, DESIGN-REQ-016)
- [X] T012 Update `moonmind/services/skill_materialization.py` to write `_manifest.json` with snapshot identity, runtime id, materialization mode, visible path, backing path, resolved time, and selected skill metadata (FR-005, FR-006, DESIGN-REQ-012)
- [X] T013 Update `moonmind/services/skill_materialization.py` failure handling to include path, object kind, attempted action, and remediation for incompatible `.agents` or `.agents/skills` paths (FR-003, FR-008, SC-004, DESIGN-REQ-013)
- [X] T014 Update `moonmind/services/skill_materialization.py` output metadata and workspace paths to report `.agents/skills`, backing store, manifest path, and active skill names without embedding full skill bodies (FR-009, FR-010, FR-011, DESIGN-REQ-011, DESIGN-REQ-015, DESIGN-REQ-021)
- [X] T015 Update `moonmind/workflows/agent_skills/agent_skills_activities.py` only if needed so activity materialization returns the service metadata unchanged and remains resolver-free (FR-004, FR-011, DESIGN-REQ-017, DESIGN-REQ-021)
- [X] T016 Run `python -m pytest tests/unit/services/test_skill_materialization.py tests/unit/workflows/agent_skills/test_agent_skills_activities.py tests/unit/workflows/test_workspace_links.py -q` and fix failures until the story passes.

**Checkpoint**: The story is fully functional, covered by service and activity-boundary tests, and independently testable.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without changing its core scope.

- [X] T017 Run `git diff --check` for changed files and fix formatting issues.
- [X] T018 Run `./tools/test_unit.sh` for final unit verification or record the exact blocker if the managed environment cannot complete it.
- [X] T019 Run `./tools/test_integration.sh` if Docker is available; otherwise record the Docker/socket blocker because no compose-backed code path changed.
- [X] T020 Run `/speckit.verify` by producing `specs/208-managed-runtime-skill-projection/verification.md` against the original MM-407 request, spec, plan, tasks, and test evidence.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: Complete.
- **Foundational (Phase 2)**: Complete; no migrations or new infrastructure.
- **Story (Phase 3)**: Tests first, red-first confirmation, then implementation.
- **Polish (Phase 4)**: Runs after story tests pass.

### Within The Story

- T005-T007 must be written before T008.
- T009 must be written before T010.
- T011-T015 start only after red-first confirmation in T008 and T010.
- T016 validates implementation.
- T020 is final verification after tests and quickstart evidence.

### Parallel Opportunities

- T005, T006, and T007 touch the same test file and should be sequenced by one editor, but their test concerns are independent.
- T009 can be authored in parallel with service test additions because it touches a separate test file.
- T017 and documentation review can run after T016 while final suite commands are prepared.

## Implementation Strategy

1. Preserve the MM-407 source brief and existing spec artifacts.
2. Add failing service and activity-boundary tests for canonical `.agents/skills` projection.
3. Align the service materializer with the shared workspace-link helper and `_manifest.json` contract.
4. Keep existing Codex worker compatibility-link behavior intact.
5. Run focused tests, final unit verification, and final MoonSpec verification.
