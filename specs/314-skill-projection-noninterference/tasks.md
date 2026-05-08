# Tasks: Skill Projection Noninterference

**Input**: Design documents from `/work/agent_jobs/mm:52200283-dcc4-4a53-afbe-281fafee1c76/repo/specs/314-skill-projection-noninterference/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/skill-projection-contract.md, quickstart.md

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason where behavior is missing or partial, then implement the production code until they pass. For `implemented_unverified` rows, add verification tests first and run the conditional fallback implementation tasks only if those tests fail.

**Organization**: Tasks cover exactly one story: `Keep Runtime Skill Projection From Masking Repo Skills`.

**Source Traceability**: Preserves Jira issue `MM-608`, the original Jira preset brief in `spec.md`, FR-001 through FR-015, SCN-001 through SCN-006, Edge-001 through Edge-007, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-009.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/services/test_skill_materialization.py tests/unit/services/test_skill_resolution.py tests/unit/workflows/test_workspace_links.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/agents/test_moonspec_verify_skill.py tests/unit/agents/codex_worker/test_worker.py tests/unit/api/routers/test_executions.py`
- Integration tests: `./tools/test_integration.sh` for any added `integration_ci` boundary; otherwise use the focused managed-runtime activity boundary through `./tools/test_unit.sh tests/unit/workflows/temporal/test_agent_runtime_activities.py`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel because it touches different files and does not depend on incomplete tasks
- Every task includes exact file paths and requirement/source IDs when applicable
- This task list contains one story only and no broad-design split work

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm active artifacts and test targets before story work

- [X] T001 Confirm active feature artifacts exist and are current in specs/314-skill-projection-noninterference/spec.md, specs/314-skill-projection-noninterference/plan.md, specs/314-skill-projection-noninterference/research.md, specs/314-skill-projection-noninterference/data-model.md, specs/314-skill-projection-noninterference/quickstart.md, and specs/314-skill-projection-noninterference/contracts/skill-projection-contract.md for MM-608 traceability FR-015 SC-007
- [X] T002 Confirm no implementation task starts before tests by reviewing this tasks.md and preserving the red-first order in specs/314-skill-projection-noninterference/tasks.md
- [X] T003 [P] Confirm focused unit targets are runnable through ./tools/test_unit.sh for tests/unit/services/test_skill_materialization.py, tests/unit/services/test_skill_resolution.py, tests/unit/workflows/test_workspace_links.py, tests/unit/workflows/temporal/test_agent_runtime_activities.py, tests/unit/agents/test_moonspec_verify_skill.py, tests/unit/agents/codex_worker/test_worker.py, and tests/unit/api/routers/test_executions.py
- [X] T004 [P] Confirm whether the managed-runtime boundary can remain a unit-level activity test or needs a hermetic integration_ci test in tests/integration/test_skill_projection_noninterference.py for FR-014 DESIGN-REQ-009

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish fixtures and traceability inventory that block story test authoring

**CRITICAL**: No production implementation work can begin until this phase is complete.

- [X] T005 Build a requirement-to-test traceability checklist in specs/314-skill-projection-noninterference/tasks.md covering FR-001 through FR-015, SCN-001 through SCN-006, Edge-001 through Edge-007, SC-001 through SC-007, and DESIGN-REQ-001 through DESIGN-REQ-009
- [X] T006 [P] Add or update shared resolved skill snapshot and artifact-service fixtures in tests/unit/services/test_skill_materialization.py for active snapshot, repo-authored `.agents/skills`, known MoonMind symlink, stale MoonMind symlink, unknown symlink, file collision, and optional `.gemini/skills` cases covering FR-001 FR-006 FR-007 DESIGN-REQ-006
- [X] T007 [P] Add or update managed job workspace fixtures in tests/unit/workflows/temporal/test_agent_runtime_activities.py for `/work/agent_jobs/<job_id>/repo`, run-scoped `runtime/skills_active/<snapshot_id>`, and checked-in `.agents/skills` source files covering FR-003 FR-004 FR-014 DESIGN-REQ-001 DESIGN-REQ-003
- [X] T008 [P] Add or update loader projection fixtures in tests/unit/services/test_skill_resolution.py for current-working-directory projection, repo projection with manifest, runtime-root projection without manifest, and hidden local overlay covering FR-009 FR-010 DESIGN-REQ-008
- [X] T009 [P] Add or update publish-filter fixtures in tests/unit/workflows/temporal/test_agent_runtime_activities.py for generated symlink projection, generated root `skills_active`, and real repo-authored `.agents/skills` directory covering FR-012 DESIGN-REQ-009

**Checkpoint**: Fixture foundations and traceability inventory are ready; story test authoring can begin.

---

## Phase 3: Story - Keep Runtime Skill Projection From Masking Repo Skills

**Summary**: As a MoonMind operator running managed agent or MoonSpec workflows against repositories with checked-in skill sources, I need active runtime skill projection isolated from repo-authored skill files so verification, Git status, and publish flows stay clean while agents can still read their selected active skills.

**Independent Test**: Prepare a managed selected-skill turn in a checkout with tracked `.agents/skills`, confirm the active skill bundle remains readable at the reported `visiblePath`, confirm repo-authored skill files remain readable and unchanged, and confirm verification/publish preflight does not treat real repo skill sources as generated projection state.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, FR-012, FR-013, FR-014, FR-015, SCN-001, SCN-002, SCN-003, SCN-004, SCN-005, SCN-006, Edge-001, Edge-002, Edge-003, Edge-004, Edge-005, Edge-006, Edge-007, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, SC-007, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-004, DESIGN-REQ-005, DESIGN-REQ-006, DESIGN-REQ-007, DESIGN-REQ-008, DESIGN-REQ-009

**Test Plan**:

- Unit: materializer metadata/path decisions, workspace alias ownership, loader projection guards, activation summary text, publish filter ownership, verifier preflight text, execution projection serialization
- Integration: managed selected-skill runtime boundary with repo-authored `.agents/skills` preserved and active snapshot exposed by `visiblePath`; use `integration_ci` only if unit-level activity boundary cannot prove the original failure mode

### Unit Tests (write first) ⚠️

> Write or update these tests FIRST. For missing or partial behavior, run them and confirm they FAIL for the expected reason before implementation. For implemented_unverified behavior, run them as verification tests and skip the corresponding fallback implementation task if they pass.

- [X] T010 [P] Add verification unit tests for repo-authored `.agents/skills` preservation, run-scoped backing path, alias unavailable metadata, and no checkout `skills_active` pollution in tests/unit/services/test_skill_materialization.py covering FR-001 FR-002 FR-003 FR-005 Edge-005 Edge-006 SC-001 SC-002 DESIGN-REQ-001 DESIGN-REQ-002 DESIGN-REQ-003
- [X] T011 [P] Add failing/verification unit tests for `.agents/skills` alias created, reused, stale MoonMind-owned replacement, repo-authored directory skip, file collision failure, and unknown symlink failure in tests/unit/workflows/test_workspace_links.py covering FR-006 FR-007 Edge-001 Edge-002 Edge-003 SC-003 DESIGN-REQ-006
- [X] T012 [P] Add verification unit tests for built-in CWD projection isolation, repo active projection rejection, local overlay hidden-by-projection rejection, and explicit diagnostics in tests/unit/services/test_skill_resolution.py covering FR-009 FR-010 Edge-004 SC-004 DESIGN-REQ-008
- [X] T013 [P] Add verification unit tests for managed activation summary `visiblePath`, selected skill `SKILL.md` path, repo-authored `.agents/skills` warning, and manifest snapshot validation in tests/unit/workflows/temporal/test_agent_runtime_activities.py covering FR-004 FR-014 SCN-002 DESIGN-REQ-004 DESIGN-REQ-007
- [X] T014 [P] Add failing/verification unit tests for full materialization metadata and structured alias diagnostic evidence in tests/unit/services/test_skill_materialization.py and tests/unit/workflows/temporal/test_agent_runtime_activities.py covering FR-005 FR-013 DESIGN-REQ-004 DESIGN-REQ-006
- [X] T015 [P] Add failing/verification unit tests proving dormant preserve-and-link helpers cannot move repo-authored `.agents/skills` during normal materialization in tests/unit/services/test_skill_materialization.py covering FR-008 SCN-001 DESIGN-REQ-005
- [X] T016 [P] Add verification unit tests for MoonSpec verification projection preflight text in tests/unit/agents/test_moonspec_verify_skill.py covering FR-011 SCN-005 SC-005 DESIGN-REQ-009
- [X] T017 [P] Add failing/verification unit tests proving publish filtering excludes MoonMind-owned projection symlinks but preserves real repo-authored `.agents/skills` directories in tests/unit/workflows/temporal/test_agent_runtime_activities.py covering FR-012 Edge-007 SCN-006 SC-006 DESIGN-REQ-009
- [X] T018 [P] Add or update execution serialization unit tests for skill runtime `visiblePath`, `backingPath`, manifest refs, and absence of full skill bodies in tests/unit/api/routers/test_executions.py covering FR-005 FR-013 DESIGN-REQ-004
- [X] T019 Run `./tools/test_unit.sh tests/unit/services/test_skill_materialization.py tests/unit/services/test_skill_resolution.py tests/unit/workflows/test_workspace_links.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/agents/test_moonspec_verify_skill.py tests/unit/api/routers/test_executions.py` and record which verification tests pass and which fail for the expected missing/partial behavior before implementation

### Integration Tests (write first) ⚠️

- [X] T020 [P] Add a managed-runtime boundary test in tests/unit/workflows/temporal/test_agent_runtime_activities.py that prepares a selected-skill turn inside `/work/agent_jobs/<job_id>/repo`, preserves checked-in `.agents/skills`, validates active `visiblePath`, and confirms no root checkout `skills_active` exists covering FR-001 FR-002 FR-003 FR-004 FR-014 SCN-001 SCN-002 DESIGN-REQ-001 DESIGN-REQ-002 DESIGN-REQ-003 DESIGN-REQ-007
- [X] T021 [P] If T020 cannot cover the original cross-boundary verification failure, add hermetic `integration_ci` coverage in tests/integration/test_skill_projection_noninterference.py for the same managed selected-skill runtime scenario covering FR-014 SC-001 SC-002 DESIGN-REQ-009
- [X] T022 [P] Add or update publish/verification boundary coverage in tests/unit/workflows/temporal/test_agent_runtime_activities.py or tests/integration/test_skill_projection_noninterference.py to prove generated projection state is filtered while repo-authored `.agents/skills` remains visible covering FR-011 FR-012 SCN-005 SCN-006 SC-005 SC-006 DESIGN-REQ-009
- [X] T023 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/workflows/test_workspace_links.py` and, if T021 was added, run `./tools/test_integration.sh` to confirm new integration/boundary tests fail for expected missing/partial behavior before implementation

### Implementation

- [X] T024 Conditional fallback: if T010 or T020 fails for preservation/backing path behavior, update moonmind/services/skill_materialization.py and moonmind/workflows/temporal/activity_runtime.py so repo-authored `.agents/skills` remains untouched and active skills materialize under run-scoped `runtime/skills_active/<snapshot_id>` covering FR-001 FR-002 FR-003 DESIGN-REQ-001 DESIGN-REQ-002 DESIGN-REQ-003
- [X] T025 Conditional fallback: if T013 fails for activation instructions, update moonmind/workflows/temporal/activity_runtime.py so managed turn text uses materialization `visiblePath`, points to `<visiblePath>/<selected-skill>/SKILL.md`, and warns when repo `.agents/skills` is source content covering FR-004 SCN-002 DESIGN-REQ-004 DESIGN-REQ-007
- [X] T026 Complete materialization metadata and diagnostic payload behavior in moonmind/services/skill_materialization.py and moonmind/workflows/temporal/activity_runtime.py for `activeSkills`, `backingPath`, `visiblePath`, `canonicalAliasPath`, `canonicalAliasAvailable`, `canonicalAliasSkippedReason`, `repoSkillSourcePreserved`, compatibility status, and operator diagnostics covering FR-005 FR-013 DESIGN-REQ-004 DESIGN-REQ-006
- [X] T027 Conditional fallback: if T011 fails for alias ownership outcomes, update moonmind/workflows/skills/workspace_links.py to create, reuse, replace only proven MoonMind-owned stale symlinks, skip optional repo-authored aliases, and block files or unknown symlinks before launch covering FR-006 FR-007 SC-003 DESIGN-REQ-006
- [X] T028 Resolve preserve-and-link fallback semantics in moonmind/services/skill_materialization.py by removing unused normal-path move/restore helpers or making any fallback explicit and lease-backed so normal managed runs cannot move repo-authored `.agents/skills` covering FR-008 DESIGN-REQ-005
- [X] T029 Conditional fallback: if T012 fails for loader guards, update moonmind/services/skill_resolution.py so BuiltInSkillLoader ignores current workspace projections and RepoSkillLoader/LocalSkillLoader fail with explicit contamination diagnostics for active projections covering FR-009 FR-010 DESIGN-REQ-008
- [X] T030 Complete MoonSpec verification preflight ownership in the appropriate runtime or verifier boundary, updating tests/unit/agents/test_moonspec_verify_skill.py and owning runtime code only when needed so contamination produces `ENVIRONMENT_CONTAMINATED_BY_SKILL_PROJECTION` instead of an indeterminate feature verdict covering FR-011 SC-005 DESIGN-REQ-009
- [X] T031 Conditional fallback: if T017 or T022 fails for publish filtering, update moonmind/workflows/temporal/activity_runtime.py so `_should_exclude_publish_path()` excludes only MoonMind-owned projection state and preserves real repo-authored `.agents/skills` covering FR-012 SC-006 DESIGN-REQ-009
- [X] T032 Conditional fallback: if non-Codex runtime command tests expose active skill path mismatch, update moonmind/agents/codex_worker/worker.py so runtime instructions and include directories do not assume repo `.agents/skills` over the activation-summary active path covering FR-004 FR-014 DESIGN-REQ-007
- [X] T033 Update AGENTS.md and GEMINI.md only if implementation changes alter active technologies or test commands, preserving the MM-608 active technology entries generated during planning covering FR-015 SC-007

### Story Validation

- [X] T034 Run `./tools/test_unit.sh tests/unit/services/test_skill_materialization.py tests/unit/services/test_skill_resolution.py tests/unit/workflows/test_workspace_links.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/agents/test_moonspec_verify_skill.py tests/unit/agents/codex_worker/test_worker.py tests/unit/api/routers/test_executions.py` and fix failures until the one-story focused suite passes covering FR-001 through FR-014
- [X] T035 If tests/integration/test_skill_projection_noninterference.py was added, run `./tools/test_integration.sh` and fix failures until the hermetic integration evidence passes covering FR-014 SC-001 SC-002 SC-005 SC-006 DESIGN-REQ-009
- [X] T036 Review specs/314-skill-projection-noninterference/spec.md, specs/314-skill-projection-noninterference/plan.md, specs/314-skill-projection-noninterference/research.md, specs/314-skill-projection-noninterference/data-model.md, specs/314-skill-projection-noninterference/contracts/skill-projection-contract.md, and specs/314-skill-projection-noninterference/tasks.md to confirm MM-608 and the original preset brief remain traceable covering FR-015 SC-007

**Checkpoint**: The story is covered by unit tests, integration or boundary evidence, implementation tasks, and independent validation.

---

## Phase 4: Polish & Final Verification

**Purpose**: Strengthen the completed story without adding hidden scope

- [X] T037 [P] Run `git diff --check` and fix whitespace or formatting issues in changed files covering final quality gates
- [X] T038 [P] Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` and record full unit-suite evidence covering final validation
- [X] T039 [P] Update specs/314-skill-projection-noninterference/quickstart.md only if implementation changes alter validation commands or integration strategy covering SC-007
- [ ] T040 Run `/moonspec-verify` after implementation and tests pass, and ensure the verification report compares the final implementation against the preserved MM-608 Jira preset brief covering FR-015 SC-007

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies; can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion and blocks story work.
- **Story (Phase 3)**: Depends on Foundational completion.
- **Polish & Final Verification (Phase 4)**: Depends on story tests and implementation validation passing.

### Within The Story

- Unit test tasks T010-T018 must be written before implementation tasks T024-T033.
- Integration/boundary test tasks T020-T022 must be written before implementation tasks T024-T033.
- Red-first confirmation tasks T019 and T023 must run before fallback implementation tasks for missing or partial behavior.
- Conditional fallback implementation tasks T024, T025, T027, T029, T031, and T032 are skipped when their verification tests pass.
- Metadata and diagnostic implementation T026 and preserve-and-link cleanup T028 are required for partial rows unless tests prove existing behavior fully satisfies FR-005, FR-008, and FR-013.
- Story validation T034-T036 must pass before polish tasks T037-T040.

### Parallel Opportunities

- T003 and T004 can run in parallel.
- T006 through T009 can run in parallel after T005.
- T010 through T018 can run in parallel when they touch their listed files independently.
- T020 through T022 can run in parallel if T021 uses a separate integration file.
- Conditional fallback tasks touching different files can run in parallel after red-first confirmation, but tasks touching moonmind/services/skill_materialization.py must be coordinated.
- T037 through T039 can run in parallel after story validation passes.

---

## Parallel Example: Story Phase

```bash
Task: "T011 Add alias ownership outcome tests in tests/unit/workflows/test_workspace_links.py"
Task: "T012 Add loader projection guard tests in tests/unit/services/test_skill_resolution.py"
Task: "T017 Add publish filter ownership tests in tests/unit/workflows/temporal/test_agent_runtime_activities.py"
```

---

## Implementation Strategy

1. Preserve the one-story scope and MM-608 traceability from `spec.md`.
2. Complete setup and fixtures before touching production code.
3. Write or update unit tests and integration/boundary tests first.
4. Run red-first confirmation for missing and partial rows; for implemented_unverified rows, record pass/fail verification evidence.
5. Implement only the fallback or partial work exposed by failing tests.
6. Re-run focused tests, then full unit verification.
7. Run `/moonspec-verify` only after implementation and tests pass.

---

## Coverage Matrix

| Source ID | Covered By Tasks |
| --- | --- |
| FR-001 | T010, T020, T024, T034 |
| FR-002 | T010, T020, T024, T034 |
| FR-003 | T010, T020, T024, T034 |
| FR-004 | T013, T020, T025, T032, T034 |
| FR-005 | T014, T018, T026, T034 |
| FR-006 | T011, T027, T034 |
| FR-007 | T011, T027, T034 |
| FR-008 | T015, T028, T034 |
| FR-009 | T012, T029, T034 |
| FR-010 | T012, T029, T034 |
| FR-011 | T016, T022, T030, T035 |
| FR-012 | T017, T022, T031, T035 |
| FR-013 | T014, T018, T026, T034 |
| FR-014 | T020, T021, T022, T034, T035 |
| FR-015 | T001, T033, T036, T040 |
| SCN-001 through SCN-006 | T020, T022, T034, T035 |
| Edge-001 through Edge-007 | T010, T011, T012, T017, T020, T022, T034, T035 |
| SC-001 through SC-007 | T034, T035, T036, T038, T040 |
| DESIGN-REQ-001 through DESIGN-REQ-009 | T010 through T032, T034, T035, T040 |
