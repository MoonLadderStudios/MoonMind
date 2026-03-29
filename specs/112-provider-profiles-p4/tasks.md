# Implementation Tasks: Provider Profiles Phase 4

## Dependencies & Completion Order

- **Phase 1: Setup**: Needs to happen first.
- **Phase 2: Foundational Tasks**: Blocking for User Stories. Includes `ProviderProfileMaterializer` and `SecretResolverBoundary`.
- **Phase 3: Secure Provider Authentication [US1]**: Depends on Foundational Tasks.
- **Phase 4: Profile-Driven Agent Shaping [US2]**: Depends on Foundational Tasks and US1.

## Phase 1: Setup

These tasks prepare the environment for new implementation.

- [ ] T001 Define `SecretResolverBoundary` interface in `src/moonmind/agent_runtime/adapter/secret_boundary.py`

## Phase 2: Foundational Tasks (Blocking)

These tasks must be completed before any User Story tasks can begin.

- [ ] T002 Implement `ProviderProfileMaterializer` class in `src/moonmind/agent_runtime/adapter/materializer.py` (DOC-REQ-001)
- [ ] T003 [P] Add unit tests for `ProviderProfileMaterializer` 9-step execution order in `tests/unit/moonmind/agent_runtime/adapter/test_materializer.py` (DOC-REQ-001)

## Phase 3: Secure Provider Authentication [US1]

**Goal:** Operators need to configure agents using encrypted credentials without exposing them in logs or workflow history.
**Independent Test:** Execute a run using a Profile with `secret_refs`, verifying the process environment contains the decrypted value while workflow payloads show only references.

- [ ] T004 [US1] Implement `secret_refs` decryption lookup against `ManagedSecret` in `SecretResolverBoundary` (DOC-REQ-002)
- [ ] T005 [P] [US1] Write test proving `secret_refs` are redacted from outputs in `tests/unit/moonmind/agent_runtime/adapter/test_secret_redaction.py` (DOC-REQ-002 validation)
- [ ] T006 [US1] Refactor `ManagedAgentAdapter.start()` to route through `ProviderProfileMaterializer` instead of `auth_mode` branches in `src/moonmind/agent_runtime/adapter/managed_adapter.py` (DOC-REQ-003)

## Phase 4: Profile-Driven Agent Shaping [US2]

**Goal:** Operators must rely on the materialization pipeline applying template files, configuration paths, and clearing specific environment variables before agent start.
**Independent Test:** Run an agent with `clear_env_keys` and `file_templates`, ensuring variables are clear and the temporary config is generated physically.

- [ ] T007 [US2] Update Gemini, Claude Code, and Codex CLI strategy generators to consume `command_behavior`, `default_model` and `model_overrides` from the new `ProviderProfileMaterializer` output in `src/moonmind/agent_runtime/strategies/*.py` (DOC-REQ-004)
- [ ] T008 [P] [US2] Implement temporary file cleanup in worker process shutdown hooks for `file_templates` in `src/moonmind/agent_runtime/adapter/managed_adapter.py` (DOC-REQ-004)
- [ ] T009 [US2] Add integration test for end-to-end `file_templates` generation and cleanup in `tests/integration/agent_runtime/test_managed_adapter_materializer.py` (DOC-REQ-004 validation)
