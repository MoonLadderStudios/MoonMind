# Tasks: Runtime Command Rendering After Context Preparation

**Input**: Design documents from `/work/agent_jobs/mm:03ecc585-89e8-4589-8bdf-05bc41f57e4a/repo/specs/356-runtime-command-rendering/`
**Prerequisites**: `plan.md`, `spec.md`, `research.md`, `data-model.md`, `contracts/runtime-command-rendering.md`, `quickstart.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement the production code until they pass.

**Organization**: Tasks are grouped by phase around one user story: preserve slash-command recognition at runtime launch after MoonMind prepares context.

**Source Traceability**: This task plan preserves `MM-686` and the original Jira preset brief from `spec.md`. It covers FR-001 through FR-016, SCN-001 through SCN-006, SC-001 through SC-007, DESIGN-REQ-006, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, and DESIGN-REQ-019. Requirement statuses from `plan.md`: 19 missing, 11 partial, 6 implemented_unverified, 1 implemented_verified.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/workflows/tasks/test_task_contract.py tests/unit/schemas/test_agent_runtime_models.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py`
- Integration tests: `./tools/test_integration.sh`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Path Conventions

- Runtime strategies: `moonmind/workflows/temporal/runtime/strategies/`
- Runtime launcher: `moonmind/workflows/temporal/runtime/launcher.py`
- Runtime request schema: `moonmind/schemas/agent_runtime_models.py`
- Task contract metadata: `moonmind/workflows/tasks/task_contract.py`
- Workflow request construction: `moonmind/workflows/temporal/workflows/run.py`
- Unit tests: `tests/unit/`
- Integration tests: `tests/integration/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the one-story planning artifacts and test surfaces before writing red-first tests.

- [ ] T001 Confirm `specs/356-runtime-command-rendering/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-command-rendering.md`, and `quickstart.md` are present and preserve `MM-686` for FR-016/SC-007.
- [ ] T002 Inspect existing runtime command normalization tests in `tests/unit/workflows/tasks/test_task_contract.py` and existing runtime strategy tests in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` to align new red-first tests with FR-008, FR-014, DESIGN-REQ-016.
- [ ] T003 Inspect existing launcher context-order tests in `tests/unit/services/temporal/runtime/test_launcher.py` and `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` to reuse fixture patterns for FR-001, FR-005, FR-015, DESIGN-REQ-011.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Establish the test harness and traceability inventory that story tasks depend on.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [ ] T004 [P] Add or extend runtime strategy test helpers for render inputs and fake profiles in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` covering FR-002, DESIGN-REQ-012.
- [ ] T005 [P] Add or extend launcher subprocess-capture helpers for rendered prompt assertions in `tests/unit/services/temporal/runtime/test_launcher.py` covering SCN-001, SCN-006.
- [ ] T006 [P] Add or extend integration launcher fixture helpers in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` covering retrieval context and prompt capture for SCN-001, SCN-002.
- [ ] T007 Build the story traceability inventory in `specs/356-runtime-command-rendering/tasks.md` and keep all FR, SCN, SC, and DESIGN-REQ rows mapped to later tasks for FR-016/SC-007.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Preserve Slash Command Recognition At Runtime Launch

**Summary**: As a managed runtime operator, I want slash commands to be rendered only after MoonMind has prepared retrieval context, skill summaries, and runtime notes so that supported runtimes still recognize the command as a command while preserving prepared context.

**Independent Test**: Submit command-leading tasks for slash-capable runtimes with retrieval context, skill activation summaries, and managed runtime notes present, then verify that the final runtime-visible input starts with the command when required and that unsupported, escaped, unknown, materialized, and failure cases produce the documented outcomes before launch.

**Traceability**: FR-001 through FR-016; SCN-001 through SCN-006; SC-001 through SC-007; DESIGN-REQ-006, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019.

**Test Plan**:

- Unit: runtime render outcome models, Codex/Claude strategy rendering, unknown command guardrails, escaped literal handling, failure/fallback classification, no-secret diagnostics, task/request metadata propagation.
- Integration: launcher order with retrieval context, skill activation summaries, managed runtime notes, Codex/Claude prompt capture, and pre-launch hard render failure behavior.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only enough code to make them pass.

- [ ] T008 [P] Add failing unit tests for Codex CLI prompt-prefix rendering, prepared-context ordering, and `/review` first-position recognition in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` covering FR-001, FR-003, FR-007, SCN-001, SC-001, DESIGN-REQ-011, DESIGN-REQ-013.
- [ ] T009 [P] Add failing unit tests for Claude Code prompt-prefix rendering, prepared-context ordering, and no Create-page provider markup dependency in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` covering FR-003, FR-007, SCN-002, SC-001, DESIGN-REQ-013.
- [ ] T010 [P] Add failing unit tests for render outcome statuses and modes in `tests/unit/schemas/test_agent_runtime_models.py` covering FR-002, FR-004, DESIGN-REQ-006, DESIGN-REQ-012.
- [ ] T011 [P] Add failing unit tests for unknown opaque slash commands and explicit materialized-command guardrails in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` covering FR-008, FR-009, SCN-003, SCN-004, SC-003, SC-006, DESIGN-REQ-016.
- [ ] T012 [P] Add failing unit tests for escaped literal slash rendering and non-command wrapper behavior in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` covering FR-010, SCN-005, SC-004, DESIGN-REQ-017.
- [ ] T013 [P] Add failing unit tests for renderer failure, fallback event handling, and redacted diagnostics in `tests/unit/services/temporal/runtime/test_launcher.py` covering FR-006, FR-011, FR-013, SCN-006, SC-005, DESIGN-REQ-018, DESIGN-REQ-019.
- [ ] T014 [P] Add failing unit tests for propagating backend-normalized runtime command metadata into `AgentExecutionRequest` construction in `tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py` covering FR-001, FR-008, FR-016.
- [ ] T015 [P] Add regression tests preserving existing backend normalization for unknown, escaped, malformed, and hinted commands in `tests/unit/workflows/tasks/test_task_contract.py` covering FR-008, FR-010, FR-014, DESIGN-REQ-016, DESIGN-REQ-017.
- [ ] T016 Run `./tools/test_unit.sh tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/schemas/test_agent_runtime_models.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py tests/unit/workflows/tasks/test_task_contract.py` and confirm T008-T015 fail for missing runtime render behavior before production changes.

### Integration Tests (write first) ⚠️

- [ ] T017 [P] Add failing integration test for Codex CLI launch with `/review`, retrieval context, skill activation summary, and managed runtime notes in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` covering FR-001, FR-003, FR-005, FR-015, SCN-001, SC-001, SC-002, DESIGN-REQ-011.
- [ ] T018 [P] Add failing integration test for Claude Code launch with `/review` and retrieval context preserving slash-command first position in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` covering FR-003, FR-007, SCN-002, SC-001, DESIGN-REQ-013.
- [ ] T019 [P] Add failing integration test for unknown valid slash command pass-through and no materialization in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` covering FR-008, FR-009, SCN-003, SCN-004, SC-003, SC-006, DESIGN-REQ-016.
- [ ] T020 [P] Add failing integration test that hard runtime render failure prevents subprocess launch in `tests/integration/workflows/temporal/test_managed_session_retrieval_context.py` covering FR-006, FR-011, SCN-006, SC-005, DESIGN-REQ-019.
- [ ] T021 Run `./tools/test_integration.sh` or targeted `pytest tests/integration/workflows/temporal/test_managed_session_retrieval_context.py -q --tb=short` and confirm T017-T020 fail for missing runtime render behavior before production changes.

### Implementation

- [ ] T022 Implement runtime command invocation and render outcome models in `moonmind/schemas/agent_runtime_models.py` covering FR-002, FR-004, FR-006, FR-011, DESIGN-REQ-006, DESIGN-REQ-012.
- [ ] T023 Implement propagation of objective runtime command metadata from task inputs or authoritative snapshots into `AgentExecutionRequest` in `moonmind/workflows/temporal/workflows/run.py` covering FR-001, FR-008, FR-016.
- [ ] T024 Preserve and validate runtime command metadata compatibility in `moonmind/workflows/tasks/task_contract.py` covering FR-008, FR-010, FR-014, DESIGN-REQ-016, DESIGN-REQ-017.
- [ ] T025 Extend `ManagedRuntimeStrategy` with runtime command rendering hooks and default plain-prompt behavior in `moonmind/workflows/temporal/runtime/strategies/base.py` covering FR-002, FR-004, DESIGN-REQ-006, DESIGN-REQ-012.
- [ ] T026 Implement Codex CLI prompt-prefix, opaque pass-through, escaped literal, and unknown-materialization guard behavior in `moonmind/workflows/temporal/runtime/strategies/codex_cli.py` covering FR-003, FR-007, FR-008, FR-009, FR-010, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-017.
- [ ] T027 Implement Claude Code prompt-prefix, opaque pass-through, escaped literal, and unknown-materialization guard behavior in `moonmind/workflows/temporal/runtime/strategies/claude_code.py` covering FR-003, FR-007, FR-008, FR-009, FR-010, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-017.
- [ ] T028 Wire final runtime command rendering after workspace preparation and skill projection but before `build_command()` in `moonmind/workflows/temporal/runtime/launcher.py` covering FR-001, FR-003, FR-005, FR-015, SCN-001, SCN-002, DESIGN-REQ-011.
- [ ] T029 Implement pre-launch render failure classification and policy-approved fallback annotation in `moonmind/workflows/temporal/runtime/launcher.py` covering FR-006, FR-011, SCN-006, SC-005, DESIGN-REQ-019.
- [ ] T030 Implement render diagnostic redaction and untrusted command text handling in `moonmind/workflows/temporal/runtime/launcher.py` and `moonmind/workflows/temporal/runtime/strategies/base.py` covering FR-012, FR-013, DESIGN-REQ-018.
- [ ] T031 Conditional fallback: if T014 verification shows runtime command metadata is not available from task inputs, add compact runtime command metadata transport in `moonmind/schemas/agent_runtime_models.py` and `moonmind/workflows/temporal/workflows/run.py` covering FR-001, FR-008, FR-016.
- [ ] T032 Conditional fallback: if T013 or T020 expose an incompatible existing failure taxonomy, add a supported `runtime_command_render_failed` classification path in `moonmind/schemas/agent_runtime_models.py` and `moonmind/workflows/temporal/runtime/launcher.py` covering FR-006, FR-011, SC-005.
- [ ] T033 Conditional fallback: if T015 existing backend normalization regression tests fail, repair normalization in `moonmind/workflows/tasks/task_contract.py` without changing authored instruction semantics covering FR-008, FR-010, FR-014.
- [ ] T034 Run `./tools/test_unit.sh tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py tests/unit/services/temporal/runtime/test_launcher.py tests/unit/schemas/test_agent_runtime_models.py tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py tests/unit/workflows/tasks/test_task_contract.py` and fix implementation until unit coverage passes.
- [ ] T035 Run `./tools/test_integration.sh` and fix implementation until integration coverage passes for SCN-001 through SCN-006 and DESIGN-REQ-011 through DESIGN-REQ-019.
- [ ] T036 Validate the single story end to end using `specs/356-runtime-command-rendering/quickstart.md` and record any command/output evidence in implementation notes for SC-001 through SC-007.

**Checkpoint**: The story is fully functional, covered by unit and integration tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed runtime rendering story without adding hidden scope.

- [ ] T037 [P] Review `specs/356-runtime-command-rendering/contracts/runtime-command-rendering.md` against final implementation and update only if the implemented contract intentionally differs, preserving MM-686 and FR-016.
- [ ] T038 [P] Review `specs/356-runtime-command-rendering/data-model.md` against final models and update entity fields or state transitions only if implementation changed them, preserving DESIGN-REQ mappings.
- [ ] T039 [P] Add or adjust focused edge-case coverage in `tests/unit/workflows/temporal/runtime/strategies/test_remaining_strategies.py` for empty prepared context, large prepared context, and unsupported runtime policy covering Edge Cases, SC-002, DESIGN-REQ-019.
- [ ] T040 [P] Add or adjust security hardening assertions in `tests/unit/services/temporal/runtime/test_launcher.py` for secret redaction in render diagnostics covering FR-013, DESIGN-REQ-018.
- [ ] T041 Run `./tools/test_unit.sh` for the full unit suite and resolve failures without widening MM-686 scope.
- [ ] T042 Run `./tools/test_integration.sh` for the required hermetic integration suite and resolve failures without adding provider-verification dependencies.
- [ ] T043 Confirm `MM-686` and the original Jira preset brief remain preserved in `specs/356-runtime-command-rendering/spec.md`, `plan.md`, `research.md`, `data-model.md`, `contracts/runtime-command-rendering.md`, `quickstart.md`, and this `tasks.md` for FR-016/SC-007.
- [ ] T044 Run `/moonspec-verify` for `specs/356-runtime-command-rendering` after implementation and tests pass, preserving MM-686, original preset brief, test evidence, and source-design coverage.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS story work.
- **Story (Phase 3)**: Depends on Foundational phase completion.
- **Polish (Phase 4)**: Depends on the story being functionally complete and tests passing.

### Within The Story

- T008-T015 unit tests must be written before T016 red-first confirmation.
- T017-T020 integration tests must be written before T021 red-first confirmation.
- T016 and T021 must confirm expected failures before implementation tasks T022-T033.
- T022-T024 establish schema/metadata propagation before strategy and launcher wiring in T025-T030.
- T025 must complete before runtime-specific strategy tasks T026-T027.
- T026-T027 and T028-T030 must complete before validation tasks T034-T036.
- Conditional fallback tasks T031-T033 run only when their corresponding verification tests fail.
- T041-T044 run only after story validation passes.

### Parallel Opportunities

- T004-T006 can run in parallel because they touch different test fixtures.
- T008-T015 can be drafted in parallel where they touch different test files; T008, T009, T011, and T012 share one file and require coordination.
- T017-T020 share an integration file and should be coordinated rather than edited blindly in parallel.
- T026 and T027 can run in parallel after T025 because they touch different runtime strategy files.
- T037-T040 can run in parallel after implementation because they touch different docs/test files.

---

## Parallel Example: Story Phase

```bash
# After T004-T007 complete, these can be assigned separately:
Task: "Add failing schema/render outcome tests in tests/unit/schemas/test_agent_runtime_models.py"
Task: "Add failing launcher failure/redaction tests in tests/unit/services/temporal/runtime/test_launcher.py"
Task: "Add failing request metadata propagation tests in tests/unit/workflows/temporal/workflows/test_run_agent_dispatch.py"

# After T025 completes, runtime strategy implementation can split:
Task: "Implement Codex CLI runtime command rendering in moonmind/workflows/temporal/runtime/strategies/codex_cli.py"
Task: "Implement Claude Code runtime command rendering in moonmind/workflows/temporal/runtime/strategies/claude_code.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1 and Phase 2 to confirm traceability and fixtures.
2. Write unit tests T008-T015 and confirm they fail with T016.
3. Write integration tests T017-T020 and confirm they fail with T021.
4. Implement schema, metadata propagation, strategy rendering, launcher wiring, failure/fallback behavior, and security handling in T022-T030.
5. Run conditional fallback implementation tasks T031-T033 only if verification tests reveal those gaps.
6. Run targeted unit and integration validation T034-T036.
7. Complete polish, full unit/integration validation, traceability review, and `/moonspec-verify` in T037-T044.

### Requirement Status Handling

- Code-and-test work: missing and partial rows from `plan.md`, including FR-001 through FR-013, FR-015, SCN-001 through SCN-006, SC-001 through SC-006, and DESIGN-REQ-006, DESIGN-REQ-011, DESIGN-REQ-012, DESIGN-REQ-013, DESIGN-REQ-016, DESIGN-REQ-017, DESIGN-REQ-018, DESIGN-REQ-019.
- Verification-only with preservation: implemented_verified row FR-014 remains covered by existing task-contract evidence plus regression and final verification.
- Conditional fallback work: implemented_unverified rows FR-009, FR-013, FR-016, SCN-004, SC-006, and SC-007 include explicit fallback or preservation tasks.
- Traceability: every final artifact and verification output must preserve MM-686 and the original Jira preset brief.

---

## Notes

- This task list covers exactly one story: preserve slash-command recognition at runtime launch after context preparation.
- Unit and integration tests are mandatory and precede implementation.
- No provider verification tests are required; all planned integration coverage is hermetic.
- Do not add Create page preview scope; MM-686 consumes existing preview/normalization behavior and focuses on managed runtime rendering.
- Do not implement `tasks.md` work during task generation; this file is the handoff to `/speckit.implement`.
