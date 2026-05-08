# Tasks: Policy-Aware Skill Query

**Input**: `specs/316-policy-aware-skill-query/spec.md`, `specs/316-policy-aware-skill-query/plan.md`, `specs/316-policy-aware-skill-query/research.md`, `specs/316-policy-aware-skill-query/data-model.md`, `specs/316-policy-aware-skill-query/contracts/skills-on-demand-query-contract.md`
**Prerequisites**: Existing Skills On Demand settings, service, schema, resolver, and activity registrations.
**Unit Test Command**: `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/services/test_skill_resolution.py`
**Integration Test Command**: `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py::test_enabled_activity_query_returns_typed_result`
**Final Verification**: `/speckit.verify`

## Source Traceability Summary

- Jira issue: MM-613
- Source design: `docs/Steps/SkillsOnDemand.md`
- Coverage IDs: DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-013, DESIGN-REQ-014
- Upstream plan status: all FR, SCN, and in-scope DESIGN-REQ rows are `implemented_verified` in `plan.md`.
- Completed code-and-test rows: FR-001 through FR-010, SCN-001 through SCN-005, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-013, DESIGN-REQ-014
- Completed verification/traceability rows: FR-011, SC-006

## Phase 1: Setup

- [X] T001 Confirm active feature artifacts exist for MM-613 in specs/316-policy-aware-skill-query/spec.md, specs/316-policy-aware-skill-query/plan.md, specs/316-policy-aware-skill-query/research.md, specs/316-policy-aware-skill-query/data-model.md, specs/316-policy-aware-skill-query/quickstart.md, and specs/316-policy-aware-skill-query/contracts/skills-on-demand-query-contract.md
- [X] T002 Confirm existing test harness paths for Skills On Demand unit and activity-boundary coverage in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py

## Phase 2: Foundational

- [X] T003 Review existing query/request schema, service, and activity boundaries in moonmind/schemas/agent_skill_models.py, moonmind/services/skills_on_demand.py, and moonmind/workflows/agent_skills/agent_skills_activities.py for MM-613 scope
- [X] T004 Add or update shared fake Skill catalog fixtures for query tests in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py (FR-006, DESIGN-REQ-002, DESIGN-REQ-014)

## Phase 3: Story - Policy-Aware Skill Metadata Discovery

**Summary**: Managed runtimes can query for bounded, policy-aware Skill metadata without receiving Skill bodies or changing active snapshots.
**Independent Test**: Enable Skills On Demand query mode, issue valid and invalid query requests through service and activity boundaries, and verify metadata-only results, eligibility diagnostics, active-snapshot membership, bounded results, and no materialization.
**Traceability IDs**: FR-001 through FR-011, SCN-001 through SCN-005, SC-001 through SC-006, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-010, DESIGN-REQ-013, DESIGN-REQ-014

### Unit Test Plan

- Validate typed query/result contracts and input failures.
- Validate enabled query metadata projection, source/runtime eligibility, current snapshot membership, and bounded result count.
- Validate body/content-ref/source-path omissions.
- Preserve disabled query behavior.

### Integration Test Plan

- Use Temporal `ActivityEnvironment` to exercise `agent_skill.query_on_demand` with settings enabled and assert the service contract is preserved at the activity boundary with no materialization.

- [X] T005 Add failing unit tests for enabled query returning `ok` with metadata-only results in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py (FR-001, FR-003, FR-004, SCN-001, SCN-004, DESIGN-REQ-002, DESIGN-REQ-010, DESIGN-REQ-014)
- [X] T006 Add failing unit tests for blank query, invalid request shape, and max result bounds in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py (FR-002, FR-009, SCN-002, DESIGN-REQ-010)
- [X] T007 Add failing unit tests for ineligible source/runtime matches and safe `eligibility_summary` behavior in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py (FR-006, FR-007, SCN-003, DESIGN-REQ-013, DESIGN-REQ-014)
- [X] T008 Add failing unit tests for `in_current_snapshot` membership from active snapshot context in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py (FR-005, SCN-005, DESIGN-REQ-010)
- [X] T009 Add failing unit tests proving enabled query does not materialize or mutate snapshots in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py (FR-008, DESIGN-REQ-003)
- [X] T010 Add failing activity-boundary test for enabled `agent_skill.query_on_demand` metadata results without materialization in tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py (FR-001, FR-003, FR-008, DESIGN-REQ-013)
- [X] T011 Run red-first focused tests with `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` and record expected failures in specs/316-policy-aware-skill-query/tasks.md (FR-001 through FR-010) - initial result failed during collection because `SkillCatalogSearchResult` was missing, confirming the new contract gap.
- [X] T012 Update query status, denial codes, `SkillCatalogSearchResult`, `SkillsOnDemandQueryRequest`, and `SkillsOnDemandQueryResult` contracts in moonmind/schemas/agent_skill_models.py (FR-002, FR-003, FR-009, DESIGN-REQ-010)
- [X] T013 Implement enabled metadata query search, validation, safe projection, eligibility summaries, active snapshot membership, bounded results, and compact metadata in moonmind/services/skills_on_demand.py (FR-001, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, DESIGN-REQ-002, DESIGN-REQ-003, DESIGN-REQ-013, DESIGN-REQ-014)
- [X] T014 Wire `AgentSkillsActivities.query_on_demand` to pass resolver-backed catalog context without enabling materialization in moonmind/workflows/agent_skills/agent_skills_activities.py (FR-001, FR-006, FR-008, DESIGN-REQ-013)
- [X] T015 Update or add resolver helper behavior only if needed for policy-aware candidate search in moonmind/services/skill_resolution.py (FR-006, DESIGN-REQ-002)
- [X] T016 Run focused green tests with `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py` (FR-001 through FR-010)
- [X] T017 Run story validation command `./tools/test_unit.sh tests/unit/workflows/agent_skills/test_skills_on_demand_controls.py tests/unit/services/test_skill_resolution.py` and confirm MM-613 behavior remains bounded and metadata-only (SC-001 through SC-005)

## Final Phase: Polish And Verification

- [X] T018 Review query result serialization for unsafe fields using tests and code inspection in moonmind/schemas/agent_skill_models.py and moonmind/services/skills_on_demand.py (FR-004, SC-001, DESIGN-REQ-014)
- [X] T019 Run full unit verification with `./tools/test_unit.sh` and record result in specs/316-policy-aware-skill-query/verification.md (FR-001 through FR-011)
- [X] T020 Run `/speckit.verify` equivalent and write specs/316-policy-aware-skill-query/verification.md with MM-613, acceptance scenario coverage, DESIGN-REQ coverage, test evidence, and final verdict (FR-011, SC-006)

## Dependencies And Execution Order

1. T001-T004 establish artifact and fixture foundations.
2. T005-T010 add red-first unit and activity-boundary tests.
3. T011 confirms the tests fail for the missing enabled query behavior.
4. T012-T015 implement the minimal schema, service, activity, and resolver changes required by the failing tests.
5. T016-T018 validate focused story behavior and metadata safety.
6. T019-T020 complete final unit and MoonSpec verification.

## Parallel Opportunities

- T005 through T010 touch the same test file and should be ordered carefully rather than parallelized.
- T012 and T013 can be drafted together only after red-first tests are established, but final schema changes must land before service changes are validated.
- T018 can run after T016 while broader unit verification T019 is prepared.

## Implementation Strategy

The completed task sequence followed TDD: red-first tests replaced enabled-not-implemented placeholder expectations, then typed metadata result contracts, service-level validation/projection, resolver-backed catalog search, and activity wiring were implemented. Disabled behavior remained unchanged, query activity wiring stayed thin, and materialization is explicitly guarded by tests. `MM-613` and source coverage IDs are preserved in verification evidence.
