# Tasks: Managed GitHub Secret Materialization

**Input**: Design documents from `specs/201-managed-github-secret-materialization/`  
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and integration-style activity boundary tests are required. Write tests first, confirm they fail for the intended reason, then implement production code.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/schemas/test_managed_session_models.py tests/unit/workflows/temporal/test_agent_runtime_activities.py tests/unit/services/temporal/runtime/test_managed_session_controller.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/speckit.verify`

## Phase 1: Setup

- [X] T001 Confirm MM-320 canonical input exists at `docs/tmp/jira-orchestration-inputs/MM-320-moonspec-orchestration-input.md` and active spec artifacts are under `specs/201-managed-github-secret-materialization` (SC-006).
- [X] T002 Classify input as single-story runtime feature request and preserve the Jira key in `spec.md` (SC-006).

## Phase 2: Story - Managed GitHub Secret Materialization

**Summary**: Managed Codex private GitHub repo launches use reference-backed, launch-scoped credential materialization instead of durable raw `GITHUB_TOKEN` payloads.

**Independent Test**: Simulate launch requests through schema, activity, and controller boundaries and verify descriptor serialization, host git materialization, container omission, redaction, and MM-320 traceability.

**Traceability**: FR-001 through FR-014, SC-001 through SC-006, DESIGN-REQ-001 through DESIGN-REQ-006.

### Unit Tests (write first)

- [X] T003 Add failing schema tests for `githubCredential` descriptor validation and no raw token serialization in `tests/unit/schemas/test_managed_session_models.py` (FR-001, FR-002, FR-003, SC-001).
- [X] T004 Add failing activity boundary tests proving `agent_runtime.launch_session` carries a descriptor and not raw `GITHUB_TOKEN` in `tests/unit/workflows/temporal/test_agent_runtime_activities.py` (FR-004, FR-005, FR-014, SC-005).
- [X] T005 Add failing controller tests proving host git gets scoped credentials while docker/container payloads omit raw tokens in `tests/unit/services/temporal/runtime/test_managed_session_controller.py` (FR-006, FR-007, FR-008, SC-002, SC-003).
- [X] T006 Add failing redaction/error tests for missing or unresolvable required GitHub descriptors in `tests/unit/services/temporal/runtime/test_managed_session_controller.py` or `tests/unit/workflows/temporal/test_agent_runtime_activities.py` (FR-009, FR-010, SC-004).
- [X] T007 Run focused unit test command and confirm T003-T006 fail for expected missing descriptor/materialization behavior.

### Implementation

- [X] T008 Add typed GitHub credential descriptor models to `moonmind/schemas/managed_session_models.py` (FR-001, FR-002, FR-003).
- [X] T009 Refactor GitHub token resolution helpers in `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py` to resolve descriptors late and preserve local-first `GITHUB_TOKEN`/`GITHUB_PAT` fallback (FR-004, FR-011).
- [X] T010 Update `moonmind/workflows/temporal/activity_runtime.py` so `agent_runtime.launch_session` shapes non-sensitive descriptors instead of injecting raw GitHub tokens into request environment (FR-005, FR-014).
- [X] T011 Update `moonmind/workflows/temporal/runtime/managed_session_controller.py` so host git subprocesses materialize credentials from descriptors and container environment/payloads omit raw tokens (FR-006, FR-007, FR-008, FR-012).
- [X] T012 Add redaction-safe diagnostics/metadata for GitHub credential materialization without exposing values (FR-010, FR-013).
- [X] T013 Run focused unit test command and fix failures.

## Phase 3: Verification

- [X] T014 Run `rg -n "MM-320|githubCredential|GITHUB_TOKEN|GITHUB_PAT|SecretRef" specs/201-managed-github-secret-materialization docs/tmp/jira-orchestration-inputs/MM-320-moonspec-orchestration-input.md` (SC-006).
- [X] T015 Run full `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` or document exact blocker.
- [X] T016 Run `/speckit.verify` and record final verification evidence in `verification.md`.
