# Tasks: Launch Claude Secret Ref

**Input**: Design documents from `/specs/238-launch-claude-secret-ref/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

**Tests**: Unit tests and boundary-style integration tests are REQUIRED. Write tests first, confirm whether they fail for the intended reason or pass because implementation already exists, then apply only the production changes needed to satisfy MM-448.

**Organization**: Tasks are grouped around the single MM-448 managed-runtime launch story.

**Source Traceability**: MM-448; DESIGN-REQ-006, DESIGN-REQ-013, DESIGN-REQ-014; FR-001 through FR-011; SC-001 through SC-006.

**Test Commands**:

- Unit tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_materializer.py tests/unit/services/temporal/runtime/test_launcher.py`
- Integration tests: `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_launcher.py`
- Final verification: `/moonspec-verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Path Conventions

- **Runtime launcher**: `moonmind/workflows/temporal/runtime/`
- **Runtime materializer**: `moonmind/workflows/adapters/`
- **Unit tests**: `tests/unit/workflows/adapters/`
- **Launcher boundary tests**: `tests/unit/services/temporal/runtime/`

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the active MM-448 feature directory and existing test harnesses.

- [X] T001 Confirm `specs/238-launch-claude-secret-ref/spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md`, and `contracts/claude-secret-ref-launch.md` exist for MM-448 traceability.
- [X] T002 Confirm focused test files exist at `tests/unit/workflows/adapters/test_materializer.py` and `tests/unit/services/temporal/runtime/test_launcher.py`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Inspect the existing launch and secret-resolution boundaries before story work.

**CRITICAL**: No story implementation work can begin until this phase is complete.

- [X] T003 Inspect `ProviderProfileMaterializer.materialize` in `moonmind/workflows/adapters/materializer.py` for clear-env, secret-ref resolution, and `env_template.from_secret_ref` behavior covering FR-002, FR-004, FR-005, DESIGN-REQ-006.
- [X] T004 Inspect managed runtime launch materialization in `moonmind/workflows/temporal/runtime/launcher.py` for profile-driven secret resolution and process environment handoff covering FR-001, FR-003, FR-006, DESIGN-REQ-013.
- [X] T005 Inspect `resolve_managed_api_key_reference` in `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py` for `db://` and missing-secret failure behavior covering FR-003, FR-009.

**Checkpoint**: Foundation ready - story test and implementation work can now begin.

---

## Phase 3: Story - Launch Claude From Secret Ref Profile

**Summary**: As MoonMind launching a Claude Code managed runtime, I want the existing provider-profile materialization path to use the `claude_anthropic` secret-reference profile so that Claude starts with the right Anthropic credential and no conflicting environment values.

**Independent Test**: Configure a launch profile for `claude_anthropic` with a valid `anthropic_api_key` managed secret reference, conflicting Anthropic/OpenAI environment values, and `api_key_env` materialization, then invoke managed-runtime launch materialization and verify the resulting Claude Code environment contains only the resolved `ANTHROPIC_API_KEY`, omits configured conflicts, carries no raw secret in durable payloads or diagnostics, and fails with a secret-free actionable message when the binding is missing or unreadable.

**Traceability**: MM-448, FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, FR-011, SC-001, SC-002, SC-003, SC-004, SC-005, SC-006, DESIGN-REQ-006, DESIGN-REQ-013, DESIGN-REQ-014

**Test Plan**:

- Unit: materializer alias-based secret rendering, clear-env behavior, missing alias failure, and secret-free error behavior.
- Integration: launcher boundary test that captures the child process environment for a `claude_anthropic` profile and verifies no process starts when secret resolution fails.

### Unit Tests (write first)

- [X] T006 [P] Add materializer unit test for `claude_anthropic` alias-based `anthropic_api_key` to `ANTHROPIC_API_KEY` rendering in `tests/unit/workflows/adapters/test_materializer.py` for FR-002, FR-004, SC-001, DESIGN-REQ-006.
- [X] T007 [P] Add materializer unit test assertions that `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, and `OPENAI_API_KEY` are cleared before final `ANTHROPIC_API_KEY` injection in `tests/unit/workflows/adapters/test_materializer.py` for FR-005, SC-002.
- [X] T008 [P] Add missing `anthropic_api_key` binding failure test in `tests/unit/workflows/adapters/test_materializer.py` for FR-009, SC-004.
- [X] T009 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_materializer.py` and record whether T006-T008 fail for missing behavior or pass because implementation already exists.

### Integration Tests (write first)

- [X] T010 [P] Add launcher boundary test for successful `claude_anthropic` `db://` secret-ref launch in `tests/unit/services/temporal/runtime/test_launcher.py` covering FR-001, FR-003, FR-004, FR-005, FR-006, FR-007, FR-010, SCN-001, SCN-002, SC-001, SC-002, DESIGN-REQ-013.
- [X] T011 [P] Add launcher boundary failure test proving missing `anthropic_api_key` resolution fails before `asyncio.create_subprocess_exec` and keeps raw secret material out of the raised error in `tests/unit/services/temporal/runtime/test_launcher.py` for FR-008, FR-009, SCN-003, SCN-004, SC-003, SC-004.
- [X] T012 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/services/temporal/runtime/test_launcher.py` and record whether T010-T011 fail for missing behavior or pass because implementation already exists.

### Implementation

- [X] T013 If T006-T008 expose a materializer gap, update `moonmind/workflows/adapters/materializer.py` to render `env_template.from_secret_ref` aliases, clear configured conflicts before injection, and raise actionable secret-free missing-alias errors for FR-002, FR-004, FR-005, FR-009.
- [X] T014 If T010-T011 expose a launcher gap, update `moonmind/workflows/temporal/runtime/launcher.py` to resolve `profile.secret_refs` through `resolve_managed_api_key_reference`, fail before process start on unresolved bindings, and preserve profile-driven launch semantics for FR-001, FR-003, FR-006, FR-007, FR-009, FR-010, SCN-005. No launcher code change was required after focused verification.
- [X] T015 If T010-T011 expose a resolver-message gap, update `moonmind/workflows/temporal/runtime/managed_api_key_resolve.py` to keep missing or unreadable secret-ref errors actionable and secret-free for FR-008, FR-009. No resolver code change was required after focused verification.
- [X] T016 Run focused materializer and launcher commands, fix failures, and verify MM-448 story behavior passes end-to-end.

**Checkpoint**: The story is functional, covered by materializer and launcher boundary tests, and testable independently.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without adding hidden scope.

- [X] T017 [P] Confirm MM-448 and DESIGN-REQ-006, DESIGN-REQ-013, DESIGN-REQ-014 remain present in `specs/238-launch-claude-secret-ref/spec.md`, `plan.md`, and `tasks.md` for FR-011, SC-006.
- [X] T018 [P] Confirm no raw Anthropic test secret appears in checked-in MoonSpec artifacts or failure assertions for FR-008, SC-003.
- [X] T019 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh tests/unit/workflows/adapters/test_materializer.py tests/unit/services/temporal/runtime/test_launcher.py`.
- [X] T020 Run `MOONMIND_FORCE_LOCAL_TESTS=1 ./tools/test_unit.sh` for final unit verification, or record exact blocker.
- [X] T021 Run `/moonspec-verify` to validate final implementation against MM-448, FR-001 through FR-011, SC-001 through SC-006, and DESIGN-REQ-006/013/014.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately.
- **Foundational (Phase 2)**: Depends on setup completion and blocks story work.
- **Story (Phase 3)**: Depends on foundational inspection.
- **Polish (Phase 4)**: Depends on focused story tests and implementation evidence.

### Within The Story

- Unit tests T006-T008 must be written before materializer implementation contingency T013.
- Launcher tests T010-T011 must be written before launcher/resolver contingencies T014-T015.
- T016 runs after test authoring and any required implementation contingency.
- T021 final verification runs after focused and final unit test evidence.

### Parallel Opportunities

- T006-T008 can be authored together in `test_materializer.py` after T003.
- T010-T011 can be authored together in `test_launcher.py` after T004-T005.
- T017-T018 can run in parallel after story validation.

---

## Parallel Example: Story Phase

```bash
Task: "Add materializer alias/clear-env tests in tests/unit/workflows/adapters/test_materializer.py"
Task: "Add launcher success/failure boundary tests in tests/unit/services/temporal/runtime/test_launcher.py"
```

---

## Implementation Strategy

### Resume-Aware Test-First Delivery

1. Preserve MM-448 as the canonical source through the spec, plan, tasks, and verification artifacts.
2. Write focused materializer and launcher tests for the exact `claude_anthropic` profile shape.
3. Run focused tests and classify results:
   - if they fail, implement only the missing materializer/launcher/resolver behavior;
   - if they pass, mark implementation contingency tasks as satisfied by existing code.
4. Rerun focused tests, then run final unit verification.
5. Run `/moonspec-verify` and use the verdict as final authority.

---

## Notes

- This task list covers one story only: MM-448 runtime launch from a secret-reference Claude Anthropic profile.
- Do not broaden scope to MM-447 manual-auth backend commit behavior or MM-446 frontend drawer behavior.
- Do not invoke real Claude Code or require real Anthropic credentials for verification.
