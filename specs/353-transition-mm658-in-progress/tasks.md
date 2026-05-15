---
description: "Tasks for transitioning Jira issue MM-658 to status 'In Progress' through MoonMind's trusted Jira tool surface"
---

# Tasks: Transition Jira Issue MM-658 to "In Progress"

**Input**: Design documents from `specs/353-transition-mm658-in-progress/`
**Prerequisites**: `plan.md` (required), `spec.md` (required for the story), `research.md`, `data-model.md`, `contracts/transition-mm658.md`, `checklists/requirements.md`

**Tests**: Unit tests and integration tests are REQUIRED. Write tests first, confirm they fail for the intended reason, then implement only enough run-time orchestration to make them pass. Per `plan.md` "Structure Decision: Reuse", no new persistent orchestration module is added by default — the agent invokes the existing trusted Jira tools inline during the run. A minimal helper module `moonmind/workflows/temporal/transition_mm658.py` is reserved as a conditional fallback when verification of an `implemented_unverified` row fails.

**Source Traceability**: This task list preserves the literal `**Input**` from `spec.md` (`"Change Jira issue MM-658 to status 'In Progress'."`) and the source design mappings `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-003`, every functional requirement `FR-001`..`FR-010`, every acceptance scenario `SCN-001`..`SCN-004`, and every measurable outcome `SC-001`..`SC-005`.

**Test Commands**:

- Unit tests: `./tools/test_unit.sh`
- Integration tests: `./tools/test_integration.sh`
- Provider verification (live Jira; manual / nightly only): `./tools/test_jules_provider.sh`
- Final verification: `/speckit.verify`

## Format: `[ID] [P?] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- Include exact file paths in descriptions
- Include requirement, scenario, or source IDs when the task implements or validates behavior

## Source Traceability Summary

| ID | Status (from plan.md) | Coverage strategy |
|---|---|---|
| FR-001 (literal `MM-658` target) | partial | Run-time discipline + unit assertion that every Jira tool call uses `MM-658`. Code-and-test work. |
| FR-002 (discover transitions before mutation) | implemented_unverified | Verification test on `jira.get_transitions` (existing trusted tool) + run-report capture. Verification-only; conditional fallback if verification fails. |
| FR-003 (case-insensitive trimmed match for `In Progress`, unambiguous) | partial | Unit tests on inline matching logic (zero / exactly one / more-than-one match) + integration test asserting selected transition. Code-and-test work. |
| FR-004 (only via trusted Jira tools, no raw HTTP) | implemented_verified | Existing evidence; preserved through final validation only. |
| FR-005 (re-read and report verified final status) | implemented_unverified | Verification test on `jira.get_issue` post-call + run-report assertion. Verification-only; conditional fallback if verification fails. |
| FR-006 (already `In Progress` ⇒ no-op) | partial | Unit + integration test asserting zero `transition_issue` invocation when prior status equals `In Progress`. Code-and-test work. |
| FR-007 (named errors: no-match / ambiguous / not-found / missing-fields / tool-unavailable / auth-or-permission / transient / validation / final-status-mismatch) | partial | Unit + integration tests for each named error path; assert no mutation. Code-and-test work. |
| FR-008 (no other field updates, no other issues) | implemented_verified | Existing evidence; preserved through final validation; integration test asserts `fields={}` and `update={}`. |
| FR-009 (no secrets in any output) | implemented_verified | Existing redactor pipeline; preserved through final validation; run-report scan in quickstart. |
| FR-010 (final report: issue key, prior status, action, verified final status) | implemented_unverified | Verification test on the run-report shape (per `data-model.md`) + conditional fallback emission. |
| SCN-001 (transition path) | implemented_unverified | Integration test exercising calls 1→4 from `contracts/transition-mm658.md`. |
| SCN-002 (already-in-progress no-op) | partial | Integration test exercising call 1 only. Code-and-test work. |
| SCN-003 (no-matching-transition error) | partial | Integration test exercising calls 1→2; assert `stopped:no_matching_transition`. Code-and-test work. |
| SCN-004 (ambiguous-transition error) | partial | Integration test exercising calls 1→2; assert `stopped:ambiguous_transition`. Code-and-test work. |
| DESIGN-REQ-001 (only `MM-658`) | partial | Single-issue invariant assertion across every tool call. Code-and-test work. |
| DESIGN-REQ-002 (target = `In Progress`) | partial | Same coverage as FR-003. |
| DESIGN-REQ-003 (status change only) | implemented_verified | Same coverage as FR-008; preserved through final validation. |
| SC-001 (post-run Jira shows `In Progress`) | implemented_unverified | Quickstart Step 4 (live Jira UI/tool fetch). |
| SC-002 (one of three outcomes; zero partial mutations) | implemented_unverified | Run-report assertion on enumerated outcomes. |
| SC-003 (zero secret exposure) | implemented_verified | Run-report scan + redaction unit evidence. |
| SC-004 (no other Jira issue modified) | implemented_verified | Quickstart Step 5 (neighbor-issue spot check). |
| SC-005 (single observed cycle) | implemented_unverified | Run-report timing / fail-fast assertion. |

Status counts: `partial` = 9, `implemented_unverified` = 7, `implemented_verified` = 6. Code-and-test work covers `partial` rows; verification-only and conditional-fallback work covers `implemented_unverified` rows; `implemented_verified` rows are preserved through final validation only.

## Story Phase Header

- **Story Summary**: As a delivery operator, drive Jira issue `MM-658` to workflow status `In Progress` through MoonMind's trusted Jira tool surface, exactly once, and emit a structured run report capturing the issue key, prior status, action taken, and verified final status.
- **Independent Test**: After the agent run, fetch `MM-658` from the Jira tracker (UI or trusted tool) and confirm its workflow status reads `In Progress` (or that the run reported `noop_already_in_progress` against an already-`In Progress` issue, or a deterministic `stopped:*` outcome with `MM-658` unchanged).
- **Story Traceability IDs**: `FR-001`, `FR-002`, `FR-003`, `FR-004`, `FR-005`, `FR-006`, `FR-007`, `FR-008`, `FR-009`, `FR-010`, `SCN-001`, `SCN-002`, `SCN-003`, `SCN-004`, `SC-001`, `SC-002`, `SC-003`, `SC-004`, `SC-005`, `DESIGN-REQ-001`, `DESIGN-REQ-002`, `DESIGN-REQ-003`.
- **Unit Test Plan**:
  - Inline match selection (`to.name` case-insensitive trimmed equality with `"In Progress"`): zero match, exactly one match, more-than-one match, whitespace/case variants.
  - Required-field detection on the matched transition's `fields` map.
  - Pre-fetch no-op detection when the issue's `status.name` already equals `"In Progress"` (case-insensitive trimmed).
  - Run-report builder shape per `data-model.md` (action/outcome consistency, redacted error reasons, single-issue invariant `issueKey="MM-658"`).
  - Credential redaction across error paths through `redact_sensitive_text` / `SecretRedactor`.
- **Integration Test Plan**:
  - SCN-001 happy path: stub trusted Jira tool surface so calls 1→4 execute; assert exactly one `jira.transition_issue` call with `issue_key="MM-658"`, `fields={}`, `update={}`, and final report `outcome="transitioned"`, `verifiedFinalStatus="In Progress"`.
  - SCN-002 no-op: stub call 1 to return `status.name="In Progress"`; assert zero `jira.get_transitions` and `jira.transition_issue` calls and `outcome="noop_already_in_progress"`.
  - SCN-003 no-match: stub call 2 to return transitions where no `to.name` matches `"In Progress"`; assert `outcome="stopped:no_matching_transition"`, `availableTransitions` populated, no mutation.
  - SCN-004 ambiguous: stub call 2 to return more than one transition with `to.name=="In Progress"`; assert `outcome="stopped:ambiguous_transition"`, `availableTransitions` lists candidates, no mutation.
  - Edge tool unavailable: simulate trusted Jira binding disabled; assert `outcome="stopped:tool_unavailable"`, zero tool calls, sanitized error.
  - Edge issue not found: stub call 1 to raise the existing `JiraToolError` for not-found; assert `outcome="stopped:issue_not_found"`, no mutation.
  - Edge auth/permission: stub call 1 to raise an auth/permission error; assert `outcome="stopped:auth_or_permission"` and no secret strings in the report.
  - Edge transient: stub call 2 or 3 to raise a 5xx/rate-limit error; assert `outcome="stopped:transient_failure"`, no ad-hoc retry, no claim of success.
  - Edge missing required fields: stub call 2 to return one matching transition whose `fields` includes a `required: true` entry; assert `outcome="stopped:missing_required_fields"`, `missingFields` lists the field IDs (no values), no `jira.transition_issue` call.
  - Edge final-status mismatch: stub calls 1→4 so call 4 returns a status other than `In Progress`; assert `outcome="stopped:final_status_mismatch"`, `verifiedFinalStatus=<observed>`.
  - Single-issue invariant: across every integration scenario, assert no Jira tool call is issued with an `issue_key` other than `"MM-658"` and `jira.edit_issue` / `jira.add_comment` / `jira.create_subtask` are never invoked.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Confirm the existing trusted Jira tool surface and test runners are wired up. No new project scaffolding is needed because the story binds to existing code.

- [ ] T001 Confirm the trusted Jira tool surface is registered in moonmind/mcp/jira_tool_registry.py (FR-002, FR-004, FR-005)
- [ ] T002 [P] Confirm `./tools/test_unit.sh` runs the existing Jira unit suite at tests/unit/integrations/test_jira_tool_service.py (FR-002, FR-004, FR-009)
- [ ] T003 [P] Confirm `./tools/test_integration.sh` runs hermetic CI integration tests under tests/integration/ and that no new compose service is required for this story (FR-007, SCN-001..SCN-004)
- [ ] T004 [P] Reserve the test module path tests/unit/workflows/temporal/test_transition_mm658.py for the story's unit tests (FR-001, FR-003, FR-006, FR-007, FR-010)
- [ ] T005 [P] Reserve the test module path tests/integration/workflows/temporal/test_transition_mm658_integration.py for the story's integration tests (SCN-001, SCN-002, SCN-003, SCN-004)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Story-blocking prerequisites only. The story binds to existing trusted Jira tool surfaces, request models, and redaction helpers; no new persistence, schema, routing, or auth scaffolding is required. The single mandatory foundation is the run-report contract.

- [ ] T006 Lock the run-report contract from specs/353-transition-mm658-in-progress/data-model.md and specs/353-transition-mm658-in-progress/contracts/transition-mm658.md as the canonical Outcome shape; record the enumerated outcome IDs (transitioned, noop_already_in_progress, stopped:no_matching_transition, stopped:ambiguous_transition, stopped:issue_not_found, stopped:missing_required_fields, stopped:auth_or_permission, stopped:validation_failure, stopped:tool_unavailable, stopped:transient_failure, stopped:final_status_mismatch) in the test fixtures (FR-010, SC-002)
- [ ] T007 Confirm the redaction pipeline `redact_sensitive_text` / `SecretRedactor` is available at moonmind/integrations/jira/tool.py and reused at moonmind/workflows/temporal/post_merge_jira_completion.py for error-text sanitation (FR-009, SC-003)

**Checkpoint**: Run-report contract locked and redaction pipeline confirmed — story test and implementation work can begin.

---

## Phase 3: Story - Move MM-658 into "In Progress"

**Summary**: As a delivery operator, drive Jira issue `MM-658` to workflow status `In Progress` through MoonMind's trusted Jira tool surface, exactly once, and emit a structured run report.

**Independent Test**: Run the workflow against `MM-658` and re-fetch the issue from Jira to confirm its current status reads `In Progress` (or that the run reported `noop_already_in_progress` for an already-`In Progress` issue, or a deterministic `stopped:*` outcome with `MM-658` unchanged). See specs/353-transition-mm658-in-progress/quickstart.md.

**Traceability**: FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, FR-010, SCN-001, SCN-002, SCN-003, SCN-004, SC-001, SC-002, SC-003, SC-004, SC-005, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003.

### Unit Tests (write first) ⚠️

> **NOTE: Write these tests FIRST. Run them, confirm they FAIL for the expected reason, then implement only the inline run-time orchestration / minimal helper required to make them pass.**

- [ ] T008 [P] Add failing unit test for inline `to.name` match selection (zero / exactly-one / >1 / whitespace+case variants of `"In Progress"`) covering FR-003, DESIGN-REQ-002, SCN-003, SCN-004 in tests/unit/workflows/temporal/test_transition_mm658.py
- [ ] T009 [P] Add failing unit test for required-field detection on the matched transition's `fields` map (`required: true`) covering FR-007 in tests/unit/workflows/temporal/test_transition_mm658.py
- [ ] T010 [P] Add failing unit test for pre-fetch no-op detection when issue `status.name` already equals `In Progress` (case-insensitive trimmed) covering FR-006, SCN-002 in tests/unit/workflows/temporal/test_transition_mm658.py
- [ ] T011 [P] Add failing unit test for run-report builder shape (action/outcome consistency, `issueKey="MM-658"`, redacted ≤500-char `errorReason`) per specs/353-transition-mm658-in-progress/data-model.md covering FR-010, SC-002 in tests/unit/workflows/temporal/test_transition_mm658.py
- [ ] T012 [P] Add failing unit test for the single-issue invariant (every emitted Jira tool call carries `issue_key="MM-658"`; never `jira.edit_issue`/`jira.add_comment`/`jira.create_subtask`) covering FR-001, FR-008, DESIGN-REQ-001, SC-004 in tests/unit/workflows/temporal/test_transition_mm658.py
- [ ] T013 [P] Add failing unit verification test that `redact_sensitive_text` / `SecretRedactor` strips known secret-pattern strings (`ghp_`, `github_pat_`, `AIza`, `ATATT`, `AKIA`, `Bearer `, `token=`, `password=`, `BEGIN PRIVATE KEY`) from `errorReason` covering FR-009, SC-003 in tests/unit/workflows/temporal/test_transition_mm658.py

### Integration Tests (write first) ⚠️

- [ ] T014 [P] Add failing integration test for SCN-001 transition path (calls 1→4 from contracts/transition-mm658.md; assert exactly one `jira.transition_issue` call with `issue_key="MM-658"`, `fields={}`, `update={}`; assert `outcome="transitioned"` and `verifiedFinalStatus="In Progress"`) covering FR-002, FR-003, FR-005, FR-008, FR-010, SCN-001, SC-001, SC-002 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T015 [P] Add failing integration test for SCN-002 no-op (stub call 1 to return `status.name="In Progress"`; assert zero `jira.get_transitions` and zero `jira.transition_issue` calls; assert `outcome="noop_already_in_progress"`) covering FR-006, SCN-002 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T016 [P] Add failing integration test for SCN-003 no-matching-transition (stub call 2 with no matching `to.name`; assert `outcome="stopped:no_matching_transition"`, `availableTransitions` populated, zero mutation) covering FR-007, SCN-003, SC-002 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T017 [P] Add failing integration test for SCN-004 ambiguous-transition (stub call 2 with two `to.name=="In Progress"` candidates; assert `outcome="stopped:ambiguous_transition"`, `availableTransitions` lists candidates, zero mutation) covering FR-007, SCN-004, SC-002 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T018 [P] Add failing integration test for `stopped:tool_unavailable` (simulate trusted Jira binding disabled; assert zero Jira tool calls and a sanitized `errorReason`) covering FR-004, FR-007, FR-009, SC-002, SC-003 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T019 [P] Add failing integration test for `stopped:issue_not_found` (call 1 raises not-found `JiraToolError`; assert no mutation and sanitized error) covering FR-007, SC-002 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T020 [P] Add failing integration test for `stopped:auth_or_permission` (call 1 raises auth/permission error; assert no secret-pattern strings appear in the run report) covering FR-007, FR-009, SC-003 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T021 [P] Add failing integration test for `stopped:transient_failure` (call 2 or 3 raises 5xx/rate-limit `JiraToolError`; assert no ad-hoc retry, no claim of success, `priorStatus` preserved) covering FR-004, FR-007, SC-002 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T022 [P] Add failing integration test for `stopped:missing_required_fields` (matched transition declares `required: true` field; assert `missingFields` lists field IDs only and zero `jira.transition_issue` call) covering FR-007, FR-008, FR-009, SC-002 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T023 [P] Add failing integration test for `stopped:final_status_mismatch` (call 4 returns a status other than `In Progress`; assert `verifiedFinalStatus=<observed>` and `outcome="stopped:final_status_mismatch"`) covering FR-005, FR-007, FR-010, SC-002 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T023a [P] Add failing integration test for `stopped:validation_failure` (call 2 or 3 raises a non-auth 4xx `JiraToolError` such as 400 or 422 that is not a missing-required-field surface; assert `outcome="stopped:validation_failure"`, no `jira.transition_issue` retry, and a sanitized `errorReason`) covering FR-007, FR-009, SC-002, SC-003 in tests/integration/workflows/temporal/test_transition_mm658_integration.py
- [ ] T024 [P] Add failing integration assertion that across every scenario, no Jira tool call uses an `issue_key` other than `"MM-658"` and `jira.edit_issue` / `jira.add_comment` / `jira.create_subtask` are never invoked covering FR-001, FR-008, DESIGN-REQ-001, DESIGN-REQ-003, SC-004 in tests/integration/workflows/temporal/test_transition_mm658_integration.py

### Red-First Confirmation

- [ ] T025 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_transition_mm658.py` and confirm T008–T013 fail for the expected reason (missing inline orchestration / missing run-report builder), capturing the failure output for the run record (FR-001, FR-003, FR-006, FR-007, FR-009, FR-010, SC-002, SC-003)
- [ ] T026 Run `./tools/test_integration.sh -- tests/integration/workflows/temporal/test_transition_mm658_integration.py` and confirm T014–T023a and T024 fail for the expected reason (no orchestration emits the run-report), capturing the failure output for the run record (SCN-001, SCN-002, SCN-003, SCN-004, FR-005, FR-007, FR-008, SC-001, SC-002, SC-004)

### Verification of Already-Implemented Behavior (before fallback work)

- [ ] T027 Verification test: assert that the registered trusted tool `jira.get_transitions` returns `transitions[*].fields` metadata when invoked with `expand_fields=true` against a stubbed Jira tenant, covering FR-002 in tests/unit/workflows/temporal/test_transition_mm658.py
- [ ] T028 Verification test: assert that the registered trusted tool `jira.get_issue` returns the current `status.name` when invoked with `issue_key="MM-658"` against a stubbed Jira tenant, covering FR-005 in tests/unit/workflows/temporal/test_transition_mm658.py
- [ ] T029 Verification test: assert that the inline run-report builder produces the canonical Outcome shape from specs/353-transition-mm658-in-progress/data-model.md (action/outcome consistency, redacted `errorReason`), covering FR-010 in tests/unit/workflows/temporal/test_transition_mm658.py
- [ ] T030 Verification test: assert that the integration scenario for SCN-001 from specs/353-transition-mm658-in-progress/quickstart.md emits exactly one `jira.transition_issue` call with `issue_key="MM-658"`, `fields={}`, `update={}` and reports `outcome="transitioned"` with `verifiedFinalStatus="In Progress"`, covering SCN-001, FR-002, FR-005, FR-010, SC-001, SC-005 in tests/integration/workflows/temporal/test_transition_mm658_integration.py

### Implementation (inline run-time orchestration; conditional fallbacks marked)

> Per `plan.md` "Structure Decision: Reuse", the default is inline run-time orchestration with no new persistent module. T031–T034 are conditional fallbacks that materialize a minimal helper at `moonmind/workflows/temporal/transition_mm658.py` only when the paired verification task (T027–T030) fails.

- [ ] T031 (Conditional fallback for FR-002 — execute only if T027 fails) Adjust the `jira.get_transitions` call site or registration in moonmind/integrations/jira/tool.py and moonmind/mcp/jira_tool_registry.py so `expand_fields=true` returns `transitions[*].fields` metadata; otherwise no implementation work for FR-002
- [ ] T032 (Conditional fallback for FR-005 — execute only if T028 fails) Adjust the `jira.get_issue` call site in moonmind/integrations/jira/tool.py to surface `status.name` for `issue_key="MM-658"`; otherwise no implementation work for FR-005
- [ ] T033 (Conditional fallback for FR-010 — execute only if T029 fails) Add a minimal inline run-report builder helper at moonmind/workflows/temporal/transition_mm658.py that produces the canonical Outcome shape from specs/353-transition-mm658-in-progress/data-model.md (action, outcome, transition.id/name/toStatusName, verifiedFinalStatus, availableTransitions, missingFields, errorClass, redacted errorReason); otherwise no new helper is required
- [ ] T034 (Conditional fallback for SCN-001 — execute only if T030 fails) Add a minimal inline driver for SCN-001 at moonmind/workflows/temporal/transition_mm658.py that performs calls 1→4 from specs/353-transition-mm658-in-progress/contracts/transition-mm658.md and emits the run report; otherwise the existing trusted-tool surface plus inline agent execution is sufficient
- [ ] T035 Implement (or perform during the agent run) the case-insensitive trimmed `to.name == "In Progress"` matching with zero/one/>1 branching per specs/353-transition-mm658-in-progress/contracts/transition-mm658.md; emit `stopped:no_matching_transition` or `stopped:ambiguous_transition` with `availableTransitions` populated when applicable (FR-003, FR-007, SCN-003, SCN-004, DESIGN-REQ-002)
- [ ] T036 Implement (or perform during the agent run) the pre-fetch no-op short-circuit: when call 1 returns `status.name` equal to `In Progress` (case-insensitive trimmed), skip calls 2 and 3 and emit `outcome="noop_already_in_progress"`, `verifiedFinalStatus=priorStatus` (FR-006, SCN-002)
- [ ] T037 Implement (or perform during the agent run) the required-field guard on the matched transition's `fields` map: when at least one entry has `required: true`, emit `outcome="stopped:missing_required_fields"` with `missingFields` listing field IDs only, and skip `jira.transition_issue` (FR-007, FR-008, FR-009)
- [ ] T038 Implement (or perform during the agent run) the named-error mapping for `stopped:issue_not_found`, `stopped:auth_or_permission`, `stopped:validation_failure`, `stopped:tool_unavailable`, `stopped:transient_failure`, and `stopped:final_status_mismatch` per specs/353-transition-mm658-in-progress/research.md "Error Surface and Named Outcomes"; route every error string through `redact_sensitive_text` / `SecretRedactor`; never claim success on transient failures (FR-007, FR-009, SC-002, SC-003)
- [ ] T039 Implement (or perform during the agent run) the post-transition verification fetch (call 4 from specs/353-transition-mm658-in-progress/contracts/transition-mm658.md) and set `verifiedFinalStatus`; if the observed status is not `In Progress`, emit `outcome="stopped:final_status_mismatch"` (FR-005, FR-010, SCN-001)
- [ ] T040 Enforce the single-issue invariant during the run: every Jira tool call uses `issue_key="MM-658"`; `jira.edit_issue`, `jira.add_comment`, and `jira.create_subtask` are never invoked; `transition_issue` is invoked at most once with `fields={}` and `update={}` (FR-001, FR-004, FR-008, DESIGN-REQ-001, DESIGN-REQ-003, SC-004)
- [ ] T041 Wire the inline orchestration through MoonMind's trusted Jira tool surface only — never construct `httpx` calls, never read raw Jira credentials, never bypass `_ensure_enabled`/`_ensure_action_allowed`/`_ensure_project_allowed` gates exposed by moonmind/integrations/jira/tool.py (FR-004, FR-009)

### Story Validation

- [ ] T042 Run `./tools/test_unit.sh tests/unit/workflows/temporal/test_transition_mm658.py` and confirm all unit tests pass (FR-001, FR-003, FR-006, FR-007, FR-009, FR-010, SC-002, SC-003)
- [ ] T043 Run `./tools/test_integration.sh -- tests/integration/workflows/temporal/test_transition_mm658_integration.py` and confirm all integration tests pass (SCN-001, SCN-002, SCN-003, SCN-004, FR-005, FR-007, FR-008, SC-001, SC-002, SC-004)
- [ ] T044 Execute the agent run end-to-end against `MM-658` and emit the structured run report; confirm exactly one of three outcomes (`transitioned`, `noop_already_in_progress`, or `stopped:*`); attach the report as the run artifact (SC-002, SC-005, FR-010)
- [ ] T045 Independent test from specs/353-transition-mm658-in-progress/quickstart.md Step 4: re-fetch `MM-658` from Jira (UI or trusted tool) and confirm the live workflow status equals `In Progress` for `transitioned`/`noop_already_in_progress`, or equals the recorded `priorStatus` for any `stopped:*` outcome (SC-001, SC-002, SC-004)
- [ ] T046 Independent test from specs/353-transition-mm658-in-progress/quickstart.md Step 5: spot-check a neighboring Jira issue (`MM-657` or `MM-659`) to confirm it is unmodified by the run (FR-008, SC-004)
- [ ] T047 Run-report secret scan: grep the run report for `ghp_`, `github_pat_`, `AIza`, `ATATT`, `AKIA`, `Bearer `, `token=`, `password=`, `BEGIN PRIVATE KEY` and confirm zero matches (FR-009, SC-003)

**Checkpoint**: The story is fully covered by unit and integration tests, the agent run produced one of the enumerated outcomes, the live Jira state matches the run report, no neighboring issue was modified, and no secret strings appear in any artifact.

---

## Phase 4: Polish & Cross-Cutting Concerns

**Purpose**: Strengthen the completed story without expanding scope.

- [ ] T048 [P] Append the run-report outcome to the run record summary at specs/353-transition-mm658-in-progress/quickstart.md "Outcome Cheat Sheet" only if the observed outcome is not already documented; otherwise no change (SC-002, FR-010)
- [ ] T049 [P] Extend unit edge-case coverage for unicode/whitespace variants of `"In Progress"` (`" In Progress "`, `"in progress"`, `"IN PROGRESS"`) in tests/unit/workflows/temporal/test_transition_mm658.py (FR-003)
- [ ] T050 [P] Extend integration edge-case coverage for the `stopped:transient_failure` path with both 429 and 503 simulations in tests/integration/workflows/temporal/test_transition_mm658_integration.py (FR-007, SC-002)
- [ ] T051 Run specs/353-transition-mm658-in-progress/quickstart.md Steps 1–5 end-to-end and capture the resulting run-report artifact (SC-001, SC-002, SC-003, SC-004, SC-005)
- [ ] T052 Run `./tools/test_unit.sh` and `./tools/test_integration.sh` for the full hermetic suite and confirm no regression in pre-existing tests (FR-002, FR-004, FR-005, FR-008, FR-009)
- [ ] T053 Run `/speckit.verify` to validate the final implementation against the original feature request `"Change Jira issue MM-658 to status 'In Progress'."` (FR-001, FR-010, SC-001, SC-002, DESIGN-REQ-001, DESIGN-REQ-002, DESIGN-REQ-003)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: T001–T005 — no story dependencies.
- **Foundational (Phase 2)**: T006–T007 — depends on Setup.
- **Story (Phase 3)**: depends on Foundational. Within the story, the order is unit tests → integration tests → red-first confirmation → verification tests for `implemented_unverified` rows → conditional fallback implementation tasks → unconditional inline-orchestration implementation → story validation.
- **Polish (Phase 4)**: depends on the story being functionally complete and tests passing.

### Within The Story

- T008–T013 (unit tests) MUST be authored before any implementation task.
- T014–T024 (integration tests, including T023a) MUST be authored before any implementation task.
- T025–T026 (red-first confirmation) MUST run and confirm failure before T035–T041 begin.
- T027–T030 (verification tests) MUST run before T031–T034 (conditional fallbacks); T031–T034 are skipped when their paired verification passes.
- T035–T041 (orchestration implementation) MUST run after red-first confirmation and conditional fallbacks have settled.
- T042–T047 (story validation) MUST run last in the story phase.
- T053 `/speckit.verify` MUST run after all earlier phases.

### Parallel Opportunities

- T002–T005 in Phase 1 are `[P]` and can run together (different concerns / different reservation actions).
- T008–T013 are `[P]` because they describe distinct, non-overlapping test functions in tests/unit/workflows/temporal/test_transition_mm658.py; serialize if your repo's test-edit policy requires single-author edits to a file.
- T014–T024 (including T023a) are `[P]` for the same reason as T008–T013, in tests/integration/workflows/temporal/test_transition_mm658_integration.py.
- T048–T050 in Phase 4 are `[P]` (different files / different scopes).

---

## Parallel Example: Story Phase

```bash
# Author unit tests in parallel (distinct test functions in the same file):
Task: "T008 Add failing unit test for inline `to.name` match selection in tests/unit/workflows/temporal/test_transition_mm658.py"
Task: "T010 Add failing unit test for pre-fetch no-op detection in tests/unit/workflows/temporal/test_transition_mm658.py"
Task: "T012 Add failing unit test for the single-issue invariant in tests/unit/workflows/temporal/test_transition_mm658.py"

# Author integration tests in parallel (distinct test functions in the same file):
Task: "T014 Add failing integration test for SCN-001 transition path in tests/integration/workflows/temporal/test_transition_mm658_integration.py"
Task: "T015 Add failing integration test for SCN-002 no-op in tests/integration/workflows/temporal/test_transition_mm658_integration.py"
Task: "T016 Add failing integration test for SCN-003 no-matching-transition in tests/integration/workflows/temporal/test_transition_mm658_integration.py"
```

---

## Implementation Strategy

### Test-Driven Story Delivery

1. Complete Phase 1: Setup (T001–T005).
2. Complete Phase 2: Foundational (T006–T007). **CRITICAL** — story work blocks until the run-report contract is locked.
3. Build the story traceability inventory from `spec.md` (already encoded in the **Source Traceability Summary** above).
4. Author unit tests (T008–T013) and integration tests (T014–T024); confirm they fail (T025–T026).
5. Run verification tests (T027–T030) for `implemented_unverified` rows. For each verification that passes, skip the paired conditional fallback (T031–T034). For each verification that fails, execute the paired fallback.
6. Run inline-orchestration implementation tasks (T035–T041) until all unit and integration tests pass.
7. Validate the story independently with unit, integration, run-report, live Jira fetch, neighbor-issue spot check, and run-report secret scan (T042–T047).
8. Complete Phase 4 polish (T048–T052) and run final `/speckit.verify` (T053).

### Status-Driven Task Selection

- `partial` rows (FR-001, FR-003, FR-006, FR-007, SCN-002, SCN-003, SCN-004, DESIGN-REQ-001, DESIGN-REQ-002): code-and-test work — full unit + integration + red-first + implementation coverage.
- `implemented_unverified` rows (FR-002, FR-005, FR-010, SCN-001, SC-001, SC-002, SC-005): verification-first; conditional fallback only when verification fails.
- `implemented_verified` rows (FR-004, FR-008, FR-009, DESIGN-REQ-003, SC-003, SC-004): preserve existing evidence; no new implementation work; retain final validation in story validation and `/speckit.verify`.

---

## Notes

- [P] tasks = different files OR distinct, non-overlapping test functions in the same file.
- The task list covers exactly one story (Move MM-658 into "In Progress").
- The story is independently completable and testable per specs/353-transition-mm658-in-progress/quickstart.md.
- Each in-scope FR, scenario, success criterion, and source design mapping has at least one task in this list.
- Verify unit and integration tests fail before running implementation orchestration.
- Run `/speckit.verify` after implementation to check the final result against the original request.
- Avoid: vague tasks, optional testing, multi-story phases, or hidden scope beyond `"Change Jira issue MM-658 to status 'In Progress'."`.
