# Tasks: Provider Profiles Migration

**Input**: Design documents from `/specs/105-provider-profiles/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization and basic structure

- [x] T001 Verify local database dependencies and developer environment readiness for schema migration.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [x] T002 Rename Database Table: Create Alembic migration renaming `managed_agent_auth_profiles` to `managed_agent_provider_profiles` bringing its schema in line with `data-model.md` and DOC-REQ-002 requirements `api_service/migrations/versions/`.
- [x] T003 Rename Workflow: Rename `AuthProfileManager` to `ProviderProfileManager` across workflow definitions in `moonmind/workflows/temporal/runtime/manager.py` (DOC-REQ-002).
- [x] T004 Update SQL Schema Models: Rename `ManagedAgentAuthProfile` to `ManagedAgentProviderProfile` in `api_service/core/models/agents.py` (DOC-REQ-002).
- [x] T005 Update Pydantic schemas: Add `profile_selector` block to `AgentExecutionRequest` in `moonmind/schema/agent.py` to support `provider_id` routing (DOC-REQ-003).

**Checkpoint**: Foundation ready - DB and core models are updated.

---

## Phase 3: User Story 1 - Provider-Aware Agent Dispatch (Priority: P1) 🎯 MVP

**Goal**: As a workflow, I can specify that I want a `claude_code` agent using `provider_id: minimax` instead of just a generic `claude_code` slot.

**Independent Test**: Can be validated by passing a strict profile selector for an alternate provider and verifying it dispatches to that provider instead of the default.

### Implementation for User Story 1

- [x] T006 [P] [US1] Update `AgentRun` workflow to supply `profile_selector` to the Manager when a run specifies a provider (DOC-REQ-001, DOC-REQ-003).
- [x] T007 [US1] Update Provider Profile Manager to filter available slots based on `profile_selector` (match `runtime_id`, `provider_id`, and `tags`) sorting by `priority` (DOC-REQ-003).
- [x] T008 [P] [US1] Validation: Add unit test verifying `ProviderProfileManager._find_available_profile` correctly sorts by priority and matches `provider_id` in `tests/unit/workflows/temporal/test_profile_manager.py` (DOC-REQ-003).

---

## Phase 4: User Story 2 - Environment Variables Layering (Priority: P1)

**Goal**: Construct the provider environment correctly via overrides and explicitly clear keys like `OPENAI_API_KEY` to prevent unintentional provider fallback.

**Independent Test**: Can be independently verified by inspecting the final env dict.

### Implementation for User Story 2

- [x] T009 [P] [US2] Update `ManagedRuntimeLauncher.prepare_env()` to copy `os.environ` and clear keys specified in `clear_env_keys` before applying `env_template` overrides in `moonmind/workflows/temporal/runtime/launcher.py` (DOC-REQ-004).
- [x] T010 [P] [US2] Seed the database with the baseline `claude_anthropic` and `claude_minimax` profiles containing appropriate `clear_env_keys` configurations in `api_service/data/seed.py`.
- [x] T011 [P] [US2] Validation: Add unit test verifying layered environment construction (including clearing variables) in `tests/unit/services/temporal/runtime/test_launcher.py` (DOC-REQ-004).

---

## Phase 5: User Story 3 - Launch-Time Secret Resolution (Priority: P2)

**Goal**: Configure provider keys using `secret_ref` strings to prevent raw secrets from entering Temporal histories or DB tables.

**Independent Test**: Reviewing Temporal histories for `AgentRun` execution payloads.

### Implementation for User Story 3

- [ ] T012 [US3] Plumb `secret_refs` dictionary parsing into the `ManagedRuntimeLauncher` so it fetches actual secret strings from the Secrets service purely at run time prior to yielding the environment (DOC-REQ-005).
- [ ] T013 [P] [US3] Validation: Add boundary test verifying `AgentExecutionRequest` payloads serialize `secret_ref` pointers but never raw secrets in `tests/unit/test_payloads.py` (DOC-REQ-005).

---

## Final Phase: Polish & Cross-Cutting Concerns

**Purpose**: Improvements that affect multiple user stories

- [ ] T014 Run validation task against DB ensuring `AuthProfile` terminology is dropped and existing tests still pass.
- [ ] T015 Verify UI components correctly call the newly renamed router endpoints if required.
